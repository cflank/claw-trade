from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Callable
from typing import Any

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.controller import SelectCommandCode, SelectionController
from claw_trade.selection.store import SelectionRunStore
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
    selection: dict[str, Any] | None = None


class ChatController:
    def __init__(
        self,
        *,
        openclaw_client: OpenClawGatewayClient,
        recognizer: IntentRecognizer,
        confirmation: ConfirmationController,
        queue: ReportTaskQueue,
        settings: ReportWorkflowSettings,
        report_model_ready_checker: Callable[[], None] | None = None,
        selection_controller: SelectionController | None = None,
    ) -> None:
        self._openclaw = openclaw_client
        self._recognizer = recognizer
        self._confirmation = confirmation
        self._queue = queue
        self._settings = settings
        self._report_model_ready_checker = report_model_ready_checker
        self._selection_controller = selection_controller or SelectionController(store=SelectionRunStore())
        self._contexts: dict[str, ChatContext] = {}
        self._messages: dict[str, list[ChatMessage]] = {}
        self._confirmation_cards: dict[str, dict[str, dict[str, Any]]] = {}
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
            self._assert_report_model_ready_for_draft(draft)
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
        origin_context_id: str | None = None,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        try:
            result = self._confirmation.confirm_intent_draft(
                request_id=request_id,
                draft_id=draft_id,
                decision=decision,
                overrides=overrides,
                origin_context_id=origin_context_id,
            )
        except QueueError as exc:
            result = {"error": {"code": exc.code, "message": exc.user_message}}
        except Exception as exc:
            failure = translate_internal_error_for_user(exc)
            result = {"error": {"code": failure.code, "message": failure.user_message}}
        self._idempotency[request_id] = result
        return result

    def confirm_intent_draft_from_chat(
        self,
        *,
        request_id: str,
        context_id: str,
        draft_id: str,
        decision: str,
        text: str,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        context = self._get_or_create_context(context_id)
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="user",
            kind="plain",
            text=text.strip() or decision,
        )
        result = self.confirm_intent_draft(
            request_id=request_id,
            draft_id=draft_id,
            decision=decision,
            origin_context_id=context.id,
        )
        error = result.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "暂时无法处理这条指令，请稍后重试。").strip()
            self._append_message(
                context_id=context.id,
                context_kind=context.kind,
                actor="system",
                kind="plain",
                text=message,
            )
            payload = {**result, **self._chat_result(context)}
            self._idempotency[request_id] = payload
            return payload
        if decision == "cancel":
            context = switch_chat_context(context, kind=ChatContextKind.NORMAL_CHAT)
            self._contexts[context_id] = context
            self._append_message(
                context_id=context.id,
                context_kind=context.kind,
                actor="system",
                kind="plain",
                text="已取消。",
            )
        else:
            context = self._context_after_confirm(context, result)
            self._contexts[context_id] = context
            task_id = _task_id_from_result(result)
            if task_id:
                self._queue.set_task_origin_context(task_id, context.id)
            if self._has_completed_message(context.id, task_id):
                context = self._get_or_create_context(context.id)
            else:
                self._append_message(
                    context_id=context.id,
                    context_kind=context.kind,
                    actor="system",
                    kind="task_progress" if isinstance(result.get("task"), dict) else "plain",
                    text=_format_confirmed_message(result),
                    task_id=task_id,
                )
        payload = {**result, **self._chat_result(context)}
        self._idempotency[request_id] = payload
        return payload

    def get_chat_session(self, *, context_id: str) -> dict[str, Any]:
        context = self._get_or_create_context(context_id)
        return self._chat_result(context)

    def append_report_completed_message(
        self,
        *,
        context_id: str,
        report_id: str,
        text: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        context = self._get_or_create_context(context_id)
        context = switch_chat_context(context, kind=ChatContextKind.REPORT_READING, active_report_id=report_id)
        self._contexts[context_id] = context
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="system",
            kind="report_completed",
            text=text,
            report_id=report_id,
            task_id=task_id,
        )
        return self._chat_result(context)

    def append_channel_plain_message(self, *, context_id: str, actor: str, text: str) -> dict[str, Any]:
        if actor not in {"user", "system"}:
            raise ValueError("actor must be user or system")
        context = self._get_or_create_context(context_id)
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor=actor,
            kind="plain",
            text=text,
        )
        return self._chat_result(context)

    def begin_channel_select_command(self, *, context_id: str, text: str) -> dict[str, Any]:
        content = text.strip()
        if not self._is_explicit_select_command(content):
            raise QueueError("INVALID_INPUT", "invalid_input", "请输入完整的 /select 指令。")
        context = self._get_or_create_context(context_id)
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="user",
            kind="plain",
            text=content,
        )
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="system",
            kind="selection_refreshing",
            text="已收到 `/select`，正在执行选股；完成后会显示在这里。",
        )
        return self._chat_result(context)

    def finish_channel_select_command(self, *, request_id: str, context_id: str, text: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        context = self._get_or_create_context(context_id)
        content = text.strip()
        try:
            payload = self._handle_explicit_select_command(
                context=context,
                request_id=request_id,
                content=content,
            )
        except QueueError as exc:
            self._append_message(
                context_id=context.id,
                context_kind=context.kind,
                actor="system",
                kind="plain",
                text=exc.user_message,
            )
            payload = {"error": {"code": exc.code, "message": exc.user_message}, **self._chat_result(context)}
        except Exception as exc:
            failure = translate_internal_error_for_user(exc)
            self._append_message(
                context_id=context.id,
                context_kind=context.kind,
                actor="system",
                kind="plain",
                text=failure.user_message,
            )
            payload = {"error": {"code": failure.code, "message": failure.user_message}, **self._chat_result(context)}
        self._idempotency[request_id] = payload
        return payload

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
        if self._is_explicit_select_command(content):
            return self._handle_explicit_select_command(context=context, request_id=request_id, content=content)
        if self._recognizer.looks_like_report_intent(content):
            try:
                draft = self._recognizer.classify_user_intent(
                    text=content,
                    source_message_id=f"msg-{request_id}",
                    settings=self._settings,
                )
            except Exception:
                draft = None
            if draft is not None:
                self._assert_report_model_ready_for_draft(draft)
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

    def _handle_explicit_select_command(
        self,
        *,
        context: ChatContext,
        request_id: str,
        content: str,
    ) -> dict[str, Any]:
        select_result = self._selection_controller.handle_select_command(
            raw_text=content,
            request_id=request_id,
            user_id=context.id,
        )
        if select_result.code == SelectCommandCode.COMPLETED:
            message_kind = "selection_result"
        elif select_result.code == SelectCommandCode.DATA_REFRESH_REQUESTED:
            message_kind = "selection_refreshing"
        else:
            message_kind = "selection_unavailable"
        selection_payload = {
            "code": select_result.code.value,
            "workflowRunId": select_result.select_workflow_run_id,
            "evidencePath": str(select_result.evidence_path),
            "unavailableCode": select_result.unavailable_code.value if select_result.unavailable_code else None,
            "failureReason": select_result.failure_reason,
            "readerReportMarkdown": select_result.reader_report_markdown,
        }
        if select_result.data_refresh is not None:
            selection_payload["dataRefresh"] = {
                "status": select_result.data_refresh.status,
                "selectionRunId": select_result.data_refresh.selection_run_id,
                "tradeDate": select_result.data_refresh.trade_date,
                "reason": select_result.data_refresh.reason,
                "errorCode": select_result.data_refresh.error_code,
            }
        self._append_message(
            context_id=context.id,
            context_kind=context.kind,
            actor="system",
            kind=message_kind,
            text=select_result.chat_text,
            selection=selection_payload,
        )
        payload = self._chat_result(context)
        payload["selection"] = selection_payload
        return payload

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
            self._remember_confirmation_card(context.id, confirmation_card)
            payload["confirmationCard"] = confirmation_card
        cards = self._confirmation_cards.get(context.id)
        if cards:
            payload["confirmationCards"] = cards
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
        if context_id.startswith("wechat_clawbot:"):
            context = switch_chat_context(context, kind=ChatContextKind.NORMAL_CHAT, title="微信聊天")
        self._contexts[context_id] = context
        self._messages.setdefault(context_id, [])
        self._confirmation_cards.setdefault(context_id, {})
        return context

    def _remember_confirmation_card(self, context_id: str, card: dict[str, Any]) -> None:
        card_id = str(card.get("id") or "").strip()
        if not card_id:
            return
        self._confirmation_cards.setdefault(context_id, {})[card_id] = card

    def _has_completed_message(self, context_id: str, task_id: str | None) -> bool:
        for message in reversed(self._messages.get(context_id, [])):
            if message.kind != "report_completed":
                continue
            if task_id is None or message.task_id == task_id:
                return True
        return False

    @staticmethod
    def _context_after_confirm(context: ChatContext, result: dict[str, Any]) -> ChatContext:
        task = result.get("task")
        if isinstance(task, dict):
            report_id = str(task.get("reportId") or "").strip()
            if report_id:
                return switch_chat_context(context, kind=ChatContextKind.REPORT_READING, active_report_id=report_id)
            task_id = str(task.get("taskId") or "").strip() or None
            if task_id:
                return switch_chat_context(context, kind=ChatContextKind.TASK_FOLLOWING, active_task_id=task_id)
        return switch_chat_context(context, kind=ChatContextKind.NORMAL_CHAT)

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
        selection: dict[str, Any] | None = None,
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
            selection=selection,
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
        if message.selection:
            payload["selection"] = dict(message.selection)
        return payload

    @staticmethod
    def _is_exit_to_normal_chat(text: str) -> bool:
        lowered = text.strip().lower()
        return lowered in {"回到普通聊天", "退出这个报告", "聊别的", "normal chat"}

    @staticmethod
    def _is_explicit_select_command(text: str) -> bool:
        return re.match(r"^\s*/select(?:\s+\d{4}-\d{2}-\d{2})?\s*$", text, re.IGNORECASE) is not None

    def _assert_report_model_ready_for_draft(self, draft: Any) -> None:
        if self._report_model_ready_checker is None:
            return
        if getattr(draft, "kind", None) is None:
            return
        if str(getattr(draft.kind, "value", "")) != "report":
            return
        try:
            self._report_model_ready_checker()
        except Exception as exc:
            raise QueueError("REPORT_MODEL_NOT_READY", "report_model_not_ready", str(exc) or "报告模型未就绪。") from exc


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


def _format_confirmed_message(result: dict[str, Any]) -> str:
    task = result.get("task")
    if isinstance(task, dict):
        status = str(task.get("status") or "").strip()
        if status == "queued":
            return "报告已进入队列。"
        if status == "running":
            return "报告任务已启动，正在生成。"
        return "已确认，报告任务已提交。"
    if "scheduledReport" in result:
        return "已确认，定时报告已创建。"
    if "priceAlert" in result:
        return "已确认，价格提醒已创建。"
    return "已确认，已提交。"


def _task_id_from_result(result: dict[str, Any]) -> str | None:
    task = result.get("task")
    if not isinstance(task, dict):
        return None
    task_id = str(task.get("taskId") or "").strip()
    return task_id or None
