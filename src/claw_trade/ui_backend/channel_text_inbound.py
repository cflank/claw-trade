from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer


@dataclass(frozen=True)
class ChannelTextMessage:
    request_id: str
    channel_kind: str
    account_id: str | None
    sender_id: str
    text: str
    message_id: str | None = None
    received_at: str | None = None


@dataclass(frozen=True)
class _PendingDraft:
    draft_id: str


class ChannelTextInboundController:
    def __init__(self, chat_controller: ChatController) -> None:
        self._chat_controller = chat_controller
        self._pending: dict[str, _PendingDraft] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def handle_message(self, message: ChannelTextMessage) -> dict[str, Any]:
        if message.request_id in self._idempotency:
            return self._idempotency[message.request_id]

        text = message.text.strip()
        if not text:
            return self._remember(message.request_id, {"handled": False})

        conversation_key = self._conversation_key(message)
        decision = _parse_confirmation_decision(text)

        with self._lock:
            pending = self._pending.get(conversation_key)

        if pending is not None:
            if decision is None:
                return self._remember(
                    message.request_id,
                    {
                        "handled": True,
                        "replyText": "当前有一张待确认卡。回复“确认”开始执行，或回复“取消”放弃。",
                        "state": "awaiting_confirmation",
                    },
                )
            return self._handle_confirmation(
                message=message,
                conversation_key=conversation_key,
                draft_id=pending.draft_id,
                decision=decision,
            )

        if not IntentRecognizer.looks_like_report_intent(text):
            return self._remember(message.request_id, {"handled": False})

        result = self._chat_controller.create_intent_draft(
            request_id=message.request_id,
            source_message_id=message.message_id or message.request_id,
            text=text,
        )
        error = _extract_error(result)
        if error is not None:
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": error, "state": "rejected"},
            )
        card = result.get("confirmationCard")
        if not isinstance(card, dict):
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": "请提供完整的报告指令，例如：/report TSLA。", "state": "rejected"},
            )
        draft_id = str(card.get("draftId") or "").strip()
        if draft_id:
            with self._lock:
                self._pending[conversation_key] = _PendingDraft(draft_id=draft_id)
        return self._remember(
            message.request_id,
            {
                "handled": True,
                "replyText": _format_confirmation_reply(card),
                "state": "awaiting_confirmation",
            },
        )

    def _handle_confirmation(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        draft_id: str,
        decision: str,
    ) -> dict[str, Any]:
        result = self._chat_controller.confirm_intent_draft(
            request_id=message.request_id,
            draft_id=draft_id,
            decision=decision,
        )
        with self._lock:
            self._pending.pop(conversation_key, None)

        error = _extract_error(result)
        if error is not None:
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": error, "state": "failed"},
            )
        if decision == "cancel":
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": "已取消。", "state": "cancelled"},
            )
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": _format_confirmed_reply(result), "state": "confirmed"},
        )

    def _remember(self, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self._idempotency[request_id] = result
        return result

    @staticmethod
    def _conversation_key(message: ChannelTextMessage) -> str:
        return f"{message.channel_kind}:{message.account_id or ''}:{message.sender_id}"


def _parse_confirmation_decision(text: str) -> str | None:
    normalized = text.strip().lower()
    if normalized in {"确认", "确定", "开始", "执行", "confirm", "yes", "y", "ok"}:
        return "confirm"
    if normalized in {"取消", "放弃", "不要", "cancel", "no", "n"}:
        return "cancel"
    return None


def _extract_error(result: dict[str, Any]) -> str | None:
    error = result.get("error")
    if not isinstance(error, dict):
        return None
    message = str(error.get("message") or "").strip()
    return message or "暂时无法处理这条指令，请稍后重试。"


def _format_confirmation_reply(card: dict[str, Any]) -> str:
    title = str(card.get("title") or "请确认是否创建报告").strip()
    lines = [title]
    for item in card.get("summaryLines") or []:
        text = str(item).strip()
        if text:
            lines.append(text)
    lines.append("回复“确认”开始执行；回复“取消”放弃。")
    return "\n".join(lines)


def _format_confirmed_reply(result: dict[str, Any]) -> str:
    task = result.get("task")
    if isinstance(task, dict):
        task_id = str(task.get("taskId") or "").strip()
        status_label = str(task.get("statusLabel") or task.get("status") or "").strip()
        lines = ["已确认，报告任务已提交。"]
        if task_id:
            lines.append(f"任务：{task_id}")
        if status_label:
            lines.append(f"状态：{status_label}")
        return "\n".join(lines)
    if "scheduledReport" in result:
        return "已确认，定时报告已创建。"
    if "priceAlert" in result:
        return "已确认，价格提醒已创建。"
    return "已确认，已提交。"
