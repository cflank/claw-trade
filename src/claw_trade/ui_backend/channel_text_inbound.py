from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

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
class ChannelReplyTarget:
    channel_kind: str
    account_id: str | None
    sender_id: str


@dataclass(frozen=True)
class _PendingDraft:
    draft_id: str


class ChannelTextInboundController:
    def __init__(
        self,
        chat_controller: ChatController,
        *,
        request_full_report_file: Callable[[str, str, ChannelReplyTarget], dict[str, object]] | None = None,
    ) -> None:
        self._chat_controller = chat_controller
        self._request_full_report_file = request_full_report_file
        self._pending: dict[str, _PendingDraft] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._latest_conversation_key: str | None = None
        self._lock = Lock()

    def handle_message(self, message: ChannelTextMessage) -> dict[str, Any]:
        if message.request_id in self._idempotency:
            return self._idempotency[message.request_id]

        text = message.text.strip()
        if not text:
            return self._remember(message.request_id, {"handled": False})

        conversation_key = self._conversation_key(message)
        self._remember_latest_conversation(conversation_key)
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

        if _looks_like_full_report_request(text):
            return self._handle_full_report_request(
                message=message,
                conversation_key=conversation_key,
                text=text,
            )

        if not IntentRecognizer.looks_like_report_intent(text):
            return self._handle_normal_chat(message=message, conversation_key=conversation_key, text=text)

        result = self._chat_controller.send_chat_message(
            request_id=message.request_id,
            context_id=conversation_key,
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
        result = self._chat_controller.confirm_intent_draft_from_chat(
            request_id=message.request_id,
            context_id=conversation_key,
            draft_id=draft_id,
            decision=decision,
            text=message.text,
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

    def _handle_full_report_request(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        text: str,
    ) -> dict[str, Any]:
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="user",
            text=text,
        )
        snapshot = self._chat_controller.get_chat_session(context_id=conversation_key)
        context = snapshot.get("context")
        report_id = ""
        if isinstance(context, dict):
            report_id = str(context.get("activeReportId") or "").strip()
        if not report_id:
            reply_text = "没有找到可发送的完整报告。请先等待报告完成。"
            self._chat_controller.append_channel_plain_message(
                context_id=conversation_key,
                actor="system",
                text=reply_text,
            )
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": reply_text, "state": "failed"},
            )
        if self._request_full_report_file is None:
            reply_text = "完整报告发送暂不可用，请在设备界面查看。"
            self._chat_controller.append_channel_plain_message(
                context_id=conversation_key,
                actor="system",
                text=reply_text,
            )
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": reply_text, "state": "failed"},
            )

        target = ChannelReplyTarget(
            channel_kind=message.channel_kind,
            account_id=message.account_id,
            sender_id=message.sender_id,
        )
        result = self._request_full_report_file(
            report_id,
            f"channel-full-report:{message.request_id}",
            target,
        )
        reply_text = str(result.get("userMessage") or "").strip() or (
            "完整报告已发送。" if result.get("sent") else "完整报告文件暂不可发送，请在设备界面查看。"
        )
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="system",
            text=reply_text,
        )
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": reply_text, "state": "sent" if result.get("sent") else "failed"},
        )

    def _handle_normal_chat(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        text: str,
    ) -> dict[str, Any]:
        result = self._chat_controller.send_chat_message(
            request_id=message.request_id,
            context_id=conversation_key,
            text=text,
        )
        error = _extract_error(result)
        if error is not None:
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": error, "state": "failed"},
            )
        reply_text = str(result.get("assistantReply") or "").strip()
        if not reply_text:
            return self._remember(
                message.request_id,
                {"handled": True, "replyText": "已收到，但助手暂时没有返回内容。", "state": "empty_reply"},
            )
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": reply_text, "state": "replied"},
        )

    def _remember(self, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self._idempotency[request_id] = result
        return result

    def latest_conversation_snapshot(self) -> dict[str, Any]:
        with self._lock:
            conversation_key = self._latest_conversation_key
        if not conversation_key:
            return {"channelKind": "wechat_clawbot", "messages": [], "confirmationCards": {}}
        snapshot = self._chat_controller.get_chat_session(context_id=conversation_key)
        return {"channelKind": "wechat_clawbot", **snapshot}

    def _remember_latest_conversation(self, conversation_key: str) -> None:
        with self._lock:
            self._latest_conversation_key = conversation_key

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


def _looks_like_full_report_request(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "发送完整报告",
        "发完整报告",
        "发送全文",
        "发全文",
        "完整报告",
        "全文",
        "send full report",
    }


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
