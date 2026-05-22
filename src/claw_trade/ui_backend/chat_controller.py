from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_context import (
    ChatContext,
    create_normal_chat_context,
    switch_chat_context,
    to_chat_context_for_user_payload,
)
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.error_translator import translate_internal_error_for_user
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import QueueError, ReportTaskQueue
from claw_trade.ui_contracts.enums import ChatContextKind


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    context_kind: ChatContextKind
    actor: str
    kind: str
    text: str
    created_at: str
    card_id: str | None = None
    report_id: str | None = None
    task_id: str | None = None


class ChatController:
    def __init__(
        self,
        *,
        openclaw_client: OpenClawGatewayClient,
        recognizer: IntentRecognizer,
        confirmation: ConfirmationController,
        queue: ReportTaskQueue,
        settings: ReportWorkflowSettings,
    ) -> None:
        self._openclaw = openclaw_client
        self._recognizer = recognizer
        self._confirmation = confirmation
        self._queue = queue
        self._settings = settings
        self._contexts: dict[str, ChatContext] = {}
        self._messages: dict[str, list[ChatMessage]] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._message_seq = 0

    def send_chat_message(self, *, request_id: str, context_id: str, text: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        try:
            payload = self._handle_send_message(request_id=request_id, context_id=context_id, text=text)
        except QueueError as exc:
            payload = {"error": {"code": exc.code, "message": exc.user_message}}
        except Exception as exc:
            failure = translate_internal_error_for_user(exc)
            payload = {"error": {"code": failure.code, "message": failure.user_message}}
        self._idempotency[request_id] = payload
        return payload

    def create_intent_draft(self, *, request_id: str, source_message_id: str, text: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        try:
            draft = self._recognizer.classify_user_intent(
                text=text,
                source_message_id=source_message_id,
                settings=self._settings,
            )
            if draft is None:
                raise QueueError("CONFIRMATION_REQUIRED", "invalid_input", "请先提供完整的报告/定时/提醒信息。")
            self._confirmation.register_draft(draft)
            result = {"draft": _draft_for_user(draft), "confirmationCard": self._confirmation.build_confirmation_card(draft)}
        except Exception as exc:
            if isinstance(exc, QueueError):
                result = {"error": {"code": exc.code, "message": exc.user_message}}
            else:
                failure = translate_internal_error_for_user(exc, category="invalid_input")
                result = {"error": {"code": failure.code, "message": failure.user_message}}
        self._idempotency[request_id] = result
        return result

    def confirm_intent_draft(
        self,
        *,
        request_id: str,
        draft_id: str,
        decision: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        try:
            result = self._confirmation.confirm_intent_draft(
                request_id=request_id,
                draft_id=draft_id,
                decision=decision,
                overrides=overrides,
            )
        except QueueError as exc:
            result = {"error": {"code": exc.code, "message": exc.user_message}}
        except Exception as exc:
            failure = translate_internal_error_for_user(exc)
            result = {"error": {"code": failure.code, "message": failure.user_message}}
        self._idempotency[request_id] = result
        return result

    def switch_chat_context(
        self,
        *,
        context_id: str,
        kind: ChatContextKind,
        active_task_id: str | None = None,
        active_report_id: str | None = None,
        locked_workflow_run_id: str | None = None,
    ) -> dict[str, Any]:
        context = self._get_or_create_context(context_id)
        updated = switch_chat_context(
            context,
            kind=kind,
            active_task_id=active_task_id,
            active_report_id=active_report_id,
            locked_workflow_run_id=locked_workflow_run_id,
        )
        self._contexts[context_id] = updated
        return to_chat_context_for_user_payload(updated, workflow_running=bool(locked_workflow_run_id))

    def _handle_send_message(self, *, request_id: str, context_id: str, text: str) -> dict[str, Any]:
        content = text.strip()
        if not content:
            raise QueueError("INVALID_INPUT", "invalid_input", "请输入内容。")
        context = self._get_or_create_context(context_id)
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="user",
            kind="plain",
            text=content,
        )
        if self._is_exit_to_normal_chat(content):
            context = switch_chat_context(context, kind=ChatContextKind.NORMAL_CHAT)
            self._contexts[context_id] = context
            return self._chat_result(context)
        if context.kind == ChatContextKind.TASK_FOLLOWING and context.active_task_id:
            task = self._queue.get_task_for_testing(context.active_task_id)
            if task is not None and task.status.value == "running":
                if self._recognizer.looks_like_task_mutation(content):
                    draft = self._recognizer.create_regenerate_draft_after_completion(
                        source_message_id=f"msg-{request_id}",
                        current_task=self._queue.to_report_task_for_user(task) or {},
                        extra_requirement=content,
                        settings=self._settings,
                    )
                    self._confirmation.register_draft(draft)
                    card = self._confirmation.build_confirmation_card(draft)
                    self._append_message(
                        context_id=context.id,
                        context_kind=context.kind,
                        actor="system",
                        kind="task_progress",
                        text="当前报告正在生成，无法中途修改。已为你准备完成后重做确认卡。",
                    )
                    self._append_message(
                        context_id=context.id,
                        context_kind=context.kind,
                        actor="system",
                        kind="confirmation_card",
                        text=card["title"],
                        card_id=card["id"],
                    )
                    return self._chat_result(context, confirmation_card=card)
                if self._recognizer.looks_like_progress_question(content):
                    snapshot = self._queue.get_report_queue_snapshot_for_user()
                    self._append_message(
                        context_id=context.id,
                        context_kind=context.kind,
                        actor="system",
                        kind="task_progress",
                        text="报告正在生成中，请稍候。",
                    )
                    return self._chat_result(context, queue_snapshot=snapshot)
        try:
            draft = self._recognizer.classify_user_intent(
                text=content,
                source_message_id=f"msg-{request_id}",
                settings=self._settings,
            )
        except Exception:
            draft = None
        if draft is not None:
            self._confirmation.register_draft(draft)
            card = self._confirmation.build_confirmation_card(draft)
            context = switch_chat_context(context, kind=ChatContextKind.INTENT_CONFIRMING)
            self._contexts[context_id] = context
            self._append_message(
                context_id=context.id,
                context_kind=context.kind,
                actor="system",
                kind="confirmation_card",
                text=card["title"],
                card_id=card["id"],
            )
            return self._chat_result(context, confirmation_card=card)
        reply = self._openclaw.chat_send(context_id=context.id, text=content, request_id=request_id)
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="assistant",
            kind="plain",
            text=reply.text,
        )
        return self._chat_result(context, assistant_reply=reply.text)

    def _chat_result(
        self,
        context: ChatContext,
        *,
        confirmation_card: dict[str, Any] | None = None,
        queue_snapshot: dict[str, Any] | None = None,
        assistant_reply: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "context": to_chat_context_for_user_payload(context, workflow_running=bool(context.locked_workflow_run_id)),
            "messages": [self._message_to_payload(item) for item in self._messages.get(context.id, [])],
        }
        if confirmation_card is not None:
            payload["confirmationCard"] = confirmation_card
        if queue_snapshot is not None:
            payload["queueSnapshot"] = queue_snapshot
        if assistant_reply is not None:
            payload["assistantReply"] = assistant_reply
        return payload

    def _get_or_create_context(self, context_id: str) -> ChatContext:
        context = self._contexts.get(context_id)
        if context is not None:
            return context
        context = create_normal_chat_context(context_id=context_id)
        self._contexts[context_id] = context
        self._messages.setdefault(context_id, [])
        return context

    def _append_message(
        self,
        *,
        context_id: str,
        context_kind: ChatContextKind,
        actor: str,
        kind: str,
        text: str,
        card_id: str | None = None,
        report_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self._message_seq += 1
        item = ChatMessage(
            message_id=f"m-{self._message_seq}",
            context_kind=context_kind,
            actor=actor,
            kind=kind,
            text=text,
            created_at=datetime.now(tz=UTC).isoformat(),
            card_id=card_id,
            report_id=report_id,
            task_id=task_id,
        )
        self._messages.setdefault(context_id, []).append(item)

    @staticmethod
    def _message_to_payload(message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messageId": message.message_id,
            "contextKind": message.context_kind.value,
            "actor": message.actor,
            "kind": message.kind,
            "text": message.text,
            "createdAt": message.created_at,
        }
        if message.card_id:
            payload["cardId"] = message.card_id
        if message.report_id:
            payload["reportId"] = message.report_id
        if message.task_id:
            payload["taskId"] = message.task_id
        return payload

    @staticmethod
    def _is_exit_to_normal_chat(text: str) -> bool:
        lowered = text.strip().lower()
        return lowered in {"回到普通聊天", "退出这个报告", "聊别的", "normal chat"}


def _draft_for_user(draft: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "draftId": draft.draft_id,
        "kind": draft.kind.value,
        "instrumentCode": draft.instrument_code,
        "instrumentName": draft.instrument_name,
        "market": draft.market.value,
        "notification": dict(draft.notification),
        "status": draft.status,
    }
    if draft.schedule:
        payload["schedule"] = dict(draft.schedule)
    if draft.price_condition:
        payload["priceCondition"] = dict(draft.price_condition)
    return payload
