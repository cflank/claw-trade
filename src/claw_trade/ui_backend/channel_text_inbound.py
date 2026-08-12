from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from threading import Lock, Thread
from typing import Any, Callable

from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_contracts.enums import ChatContextKind


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


@dataclass(frozen=True)
class _PendingSelectionReport:
    workflow_run_id: str
    markdown: str


_REPORT_QUESTION_PROCESSING_REPLY = "收到，正在查这份报告。"
_CONFIRMATION_PROCESSING_REPLY = "收到，正在识别标的，请稍等。"
_LOGGER = logging.getLogger(__name__)


class ChannelTextInboundController:
    def __init__(
        self,
        chat_controller: ChatController,
        *,
        request_full_report_file: Callable[[str, str, ChannelReplyTarget], dict[str, object]] | None = None,
        background_submitter: Callable[[Callable[[], None]], None] | None = None,
        send_channel_text: Callable[[str, str, ChannelReplyTarget], dict[str, object]] | None = None,
        request_selection_report_file: Callable[[str, str, str, ChannelReplyTarget], dict[str, object]] | None = None,
        ask_report_question: Callable[[str, str, str, str], dict[str, str]] | None = None,
        bind_notification_recipient: Callable[[str, str | None, str], dict[str, object]] | None = None,
        remember_current_notification_recipient: Callable[[str | None, str], object] | None = None,
        resolve_latest_delivered_report: Callable[[str | None, str], str | None] | None = None,
    ) -> None:
        self._chat_controller = chat_controller
        self._request_full_report_file = request_full_report_file
        self._background_submitter = background_submitter or _submit_background_job
        self._send_channel_text = send_channel_text
        self._request_selection_report_file = request_selection_report_file
        self._ask_report_question = ask_report_question
        self._bind_notification_recipient = bind_notification_recipient
        self._remember_current_notification_recipient = remember_current_notification_recipient
        self._resolve_latest_delivered_report = resolve_latest_delivered_report
        self._pending: dict[str, _PendingDraft] = {}
        self._selection_reports: dict[str, _PendingSelectionReport] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._latest_conversation_key: str | None = None
        self._lock = Lock()

    def handle_message(self, message: ChannelTextMessage) -> dict[str, Any]:
        if message.request_id in self._idempotency:
            return self._idempotency[message.request_id]

        text = message.text.strip()
        if not text:
            return self._remember(message.request_id, {"handled": False})

        if _looks_like_notification_binding(text) and self._bind_notification_recipient is not None:
            result = self._bind_notification_recipient(text, message.account_id, message.sender_id)
            return self._remember(
                message.request_id,
                {
                    "handled": True,
                    "replyText": str(result.get("message") or "绑定失败，请重新生成绑定码。"),
                    "state": "notification_bound" if result.get("bound") is True else "notification_binding_rejected",
                },
            )

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

        if _looks_like_select_command(text):
            return self._handle_select_command(
                message=message,
                conversation_key=conversation_key,
                text=text,
            )

        if _looks_like_help_command(text):
            return self._handle_help_command(
                message=message,
                conversation_key=conversation_key,
                text=text,
            )

        if IntentRecognizer.looks_like_supported_intent(text):
            return self._handle_confirmation_intent(
                message=message,
                conversation_key=conversation_key,
                text=text,
            )

        if not _looks_like_supported_chat_intent(text):
            return self._handle_normal_chat(message=message, conversation_key=conversation_key, text=text)

        return self._handle_supported_chat_message(message=message, conversation_key=conversation_key, text=text)

    def _handle_supported_chat_message(
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
                {"handled": True, "replyText": error, "state": "rejected"},
            )
        card = result.get("confirmationCard")
        if not isinstance(card, dict):
            return self._remember(message.request_id, _reply_from_chat_result(result))
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

    def _handle_confirmation_intent(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        text: str,
    ) -> dict[str, Any]:
        if self._send_channel_text is None:
            return self._handle_supported_chat_message(message=message, conversation_key=conversation_key, text=text)
        target = ChannelReplyTarget(
            channel_kind=message.channel_kind,
            account_id=message.account_id,
            sender_id=message.sender_id,
        )

        def _finish() -> None:
            result = self._chat_controller.send_chat_message(
                request_id=message.request_id,
                context_id=conversation_key,
                text=text,
            )
            reply_text = self._confirmation_result_reply_text(conversation_key=conversation_key, result=result)
            try:
                self._send_channel_text(reply_text, f"channel-confirmation-result:{message.request_id}", target)
            except Exception:
                return

        self._background_submitter(_finish)
        return self._remember(
            message.request_id,
            {
                "handled": True,
                "replyText": _CONFIRMATION_PROCESSING_REPLY,
                "state": "confirmation_processing",
                "deferFinalReply": True,
            },
        )

    def _handle_help_command(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        text: str,
    ) -> dict[str, Any]:
        reply_text = _wechat_help_message()
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="user",
            text=text,
        )
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="system",
            text=reply_text,
        )
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": reply_text, "state": "replied"},
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
        if "scheduledReport" in result and self._remember_current_notification_recipient is not None:
            self._remember_current_notification_recipient(message.account_id, message.sender_id)
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": _format_confirmed_reply(result), "state": "confirmed"},
        )

    def _confirmation_result_reply_text(self, *, conversation_key: str, result: dict[str, Any]) -> str:
        error = _extract_error(result)
        if error is not None:
            return error
        card = result.get("confirmationCard")
        if isinstance(card, dict):
            draft_id = str(card.get("draftId") or "").strip()
            if draft_id:
                with self._lock:
                    self._pending[conversation_key] = _PendingDraft(draft_id=draft_id)
            return _format_confirmation_reply(card)
        return _normal_chat_reply_text(result)

    def _handle_select_command(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        text: str,
    ) -> dict[str, Any]:
        self._chat_controller.begin_channel_select_command(
            context_id=conversation_key,
            text=text,
        )

        def _finish() -> None:
            result = self._chat_controller.finish_channel_select_command(
                request_id=message.request_id,
                context_id=conversation_key,
                text=text,
            )
            self._push_select_result_to_channel(
                message=message,
                conversation_key=conversation_key,
                result=result,
            )

        self._background_submitter(_finish)
        return self._remember(
            message.request_id,
            {
                "handled": True,
                "replyText": "收到，正在执行 /select 选股；完成后会显示在工作台。",
                "state": "selection_processing",
            },
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
        if report_id:
            return self._send_full_report_file(
                message=message,
                report_id=report_id,
            )

        with self._lock:
            selection_report = self._selection_reports.get(conversation_key)
        if selection_report is not None:
            return self._send_selection_report_file(
                message=message,
                selection_report=selection_report,
            )

        if self._resolve_latest_delivered_report is not None:
            report_id = str(self._resolve_latest_delivered_report(message.account_id, message.sender_id) or "").strip()
            if report_id:
                return self._send_full_report_file(
                    message=message,
                    report_id=report_id,
                )

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

    def _send_full_report_file(
        self,
        *,
        message: ChannelTextMessage,
        report_id: str,
    ) -> dict[str, Any]:
        conversation_key = self._conversation_key(message)
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
        request_full_report_file = self._request_full_report_file
        reply_text = "收到，正在发送完整报告。"
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="system",
            text=reply_text,
        )
        request_id = message.request_id

        def _finish() -> None:
            try:
                result = request_full_report_file(
                    report_id,
                    f"channel-full-report:{request_id}",
                    target,
                )
                final_text = str(result.get("userMessage") or "").strip() or (
                    "完整报告已发送。" if result.get("sent") else "完整报告文件暂不可发送，请在设备界面查看。"
                )
            except Exception as exc:
                final_text = str(getattr(exc, "user_message", "") or exc).strip()
                if not final_text:
                    final_text = "完整报告文件暂不可发送，请在设备界面查看。"
            self._chat_controller.append_channel_plain_message(
                context_id=conversation_key,
                actor="system",
                text=final_text,
            )
            if self._send_channel_text is None:
                return
            try:
                self._send_channel_text(final_text, f"channel-full-report-result:{request_id}", target)
            except Exception:
                return

        self._background_submitter(_finish)
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": reply_text, "state": "file_sending"},
        )

    def _send_selection_report_file(
        self,
        *,
        message: ChannelTextMessage,
        selection_report: _PendingSelectionReport,
    ) -> dict[str, Any]:
        conversation_key = self._conversation_key(message)
        if self._request_selection_report_file is None:
            reply_text = "完整选股报告发送暂不可用，请在设备界面查看。"
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
        request_selection_report_file = self._request_selection_report_file
        reply_text = "收到，正在发送完整选股报告。"
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="system",
            text=reply_text,
        )
        request_id = message.request_id

        def _finish() -> None:
            try:
                result = request_selection_report_file(
                    selection_report.workflow_run_id,
                    selection_report.markdown,
                    f"channel-selection-report:{request_id}",
                    target,
                )
                final_text = str(result.get("userMessage") or "").strip() or (
                    "完整选股报告已发送。"
                    if result.get("sent")
                    else "完整选股报告文件暂不可发送，请在设备界面查看。"
                )
            except Exception as exc:
                final_text = str(getattr(exc, "user_message", "") or exc).strip()
                if not final_text:
                    final_text = "完整选股报告文件暂不可发送，请在设备界面查看。"
            self._chat_controller.append_channel_plain_message(
                context_id=conversation_key,
                actor="system",
                text=final_text,
            )
            if self._send_channel_text is None:
                return
            try:
                self._send_channel_text(final_text, f"channel-selection-report-result:{request_id}", target)
            except Exception:
                return

        self._background_submitter(_finish)
        return self._remember(
            message.request_id,
            {"handled": True, "replyText": reply_text, "state": "file_sending"},
        )

    def _push_select_result_to_channel(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        result: dict[str, Any],
    ) -> None:
        selection = result.get("selection")
        reply_text = _latest_selection_reply_text(result) or _extract_error(result)
        if not reply_text:
            return
        if isinstance(selection, dict):
            workflow_run_id = str(selection.get("workflowRunId") or "").strip()
            markdown = str(selection.get("readerReportMarkdown") or "").strip()
            if workflow_run_id and markdown:
                with self._lock:
                    self._selection_reports[conversation_key] = _PendingSelectionReport(
                        workflow_run_id=workflow_run_id,
                        markdown=markdown,
                    )
                reply_text = _format_select_completion_reply(reply_text)
        if self._send_channel_text is None:
            return
        target = ChannelReplyTarget(
            channel_kind=message.channel_kind,
            account_id=message.account_id,
            sender_id=message.sender_id,
        )
        dedupe_key = f"channel-select-summary:{message.request_id}"
        delivery_known_failed = False
        try:
            for _attempt in range(2):
                send_result = self._send_channel_text(reply_text, dedupe_key, target)
                if send_result.get("sent") is True:
                    return
                delivery_known_failed = send_result.get("resultKnown") is True
                if not delivery_known_failed:
                    break
        except Exception:
            delivery_known_failed = False
            _LOGGER.exception("selection result WeChat delivery failed request_id=%s", message.request_id)
        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="system",
            text=(
                "选股结果发送到微信失败；结果已保留在工作台。"
                if delivery_known_failed
                else "选股结果是否送达微信无法确认；为避免重复发送，本次未自动重试，结果已保留在工作台。"
            ),
        )

    def _handle_normal_chat(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        text: str,
    ) -> dict[str, Any]:
        snapshot = self._chat_controller.get_chat_session(context_id=conversation_key)
        context = snapshot.get("context")
        if isinstance(context, dict) and context.get("kind") == ChatContextKind.REPORT_READING.value:
            report_id = str(context.get("activeReportId") or "").strip()
            if report_id:
                return self._handle_report_question(
                    message=message,
                    conversation_key=conversation_key,
                    report_id=report_id,
                    text=text,
                )

        if self._chat_controller.is_running_task_context(context_id=conversation_key):
            result = self._chat_controller.send_chat_message(
                request_id=message.request_id,
                context_id=conversation_key,
                text=text,
            )
            return self._remember(message.request_id, _reply_from_chat_result(result))

        return self._remember(message.request_id, {"handled": False})

    def _handle_report_question(
        self,
        *,
        message: ChannelTextMessage,
        conversation_key: str,
        report_id: str,
        text: str,
    ) -> dict[str, Any]:
        self._chat_controller.append_channel_plain_message(context_id=conversation_key, actor="user", text=text)
        if self._ask_report_question is None:
            reply_text = "报告追问暂不可用，请在页面查看。"
            self._chat_controller.append_channel_plain_message(
                context_id=conversation_key,
                actor="system",
                text=reply_text,
            )
            return self._remember(message.request_id, {"handled": True, "replyText": reply_text, "state": "failed"})

        target = ChannelReplyTarget(
            channel_kind=message.channel_kind,
            account_id=message.account_id,
            sender_id=message.sender_id,
        )

        def _finish() -> None:
            reply_text = self._report_question_reply_text(
                report_id=report_id,
                text=text,
                request_id=message.request_id,
                context_id=conversation_key,
            )
            self._chat_controller.append_channel_plain_message(
                context_id=conversation_key,
                actor="system",
                text=reply_text,
            )
            if self._send_channel_text is None:
                return
            try:
                self._send_channel_text(reply_text, f"channel-report-question:{message.request_id}", target)
            except Exception:
                return

        self._chat_controller.append_channel_plain_message(
            context_id=conversation_key,
            actor="system",
            text=_REPORT_QUESTION_PROCESSING_REPLY,
        )
        self._background_submitter(_finish)
        return self._remember(
            message.request_id,
            {
                "handled": True,
                "replyText": _REPORT_QUESTION_PROCESSING_REPLY,
                "state": "report_question_processing",
                "deferFinalReply": True,
            },
        )

    def _report_question_reply_text(self, *, report_id: str, text: str, request_id: str, context_id: str) -> str:
        if self._ask_report_question is None:
            return "报告追问暂不可用，请在页面查看。"
        try:
            result = self._ask_report_question(report_id, text, request_id, context_id)
        except Exception as exc:
            user_message = getattr(exc, "user_message", None)
            return str(user_message or "报告追问失败，请稍后再试。")
        return str(result.get("text") or "报告材料里没有找到可回答的内容。")

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

    def remember_conversation_context(self, conversation_key: str) -> None:
        cleaned = conversation_key.strip()
        if cleaned:
            self._remember_latest_conversation(cleaned)

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


def _looks_like_notification_binding(text: str) -> bool:
    return re.fullmatch(r"\s*绑定通知\s+\S+\s*", text) is not None


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


def _looks_like_select_command(text: str) -> bool:
    tokens = text.strip().split()
    if not tokens or tokens[0].lower() != "/select":
        return False
    rest = tokens[1:]
    if not rest:
        return True
    first = rest.pop(0)
    if _is_select_market_token(first):
        if rest and rest[0].lower() in {"refresh", "刷新"}:
            rest.pop(0)
    elif first.lower() in {"refresh", "刷新"}:
        pass
    elif _is_date_token(first):
        pass
    else:
        return False
    return not rest or (len(rest) == 1 and _is_date_token(rest[0]))


def _is_select_market_token(text: str) -> bool:
    lowered = text.lower()
    return lowered in {"1", "cn_a", "a", "2", "crypto", "3", "us", "hk"} or text in {"A股", "加密", "港股"}


def _is_date_token(text: str) -> bool:
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is not None


def _looks_like_help_command(text: str) -> bool:
    lowered = text.strip().lower()
    return (
        lowered in {"/help", "help", "帮助"}
        or lowered.startswith("/help ")
        or lowered.startswith("help ")
        or lowered.startswith("帮助 ")
    )


def _looks_like_supported_chat_intent(text: str) -> bool:
    lowered = text.strip().lower()
    return (
        _looks_like_help_command(text)
        or IntentRecognizer.looks_like_supported_intent(text)
        or lowered.startswith("/maint")
        or ("维护" in lowered and ("状态" in lowered or "情况" in lowered or "摘要" in lowered))
    )


def _extract_error(result: dict[str, Any]) -> str | None:
    error = result.get("error")
    if not isinstance(error, dict):
        return None
    message = str(error.get("message") or "").strip()
    return message or "暂时无法处理这条指令，请稍后重试。"


def _latest_selection_reply_text(result: dict[str, Any]) -> str | None:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") not in {
            "selection_result",
            "selection_refreshing",
            "selection_failed",
            "selection_unavailable",
        }:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            return text
    return None


def _normal_chat_reply_text(result: dict[str, Any]) -> str:
    error = _extract_error(result)
    if error is not None:
        return error
    selection = result.get("selection")
    if isinstance(selection, dict):
        return _latest_selection_reply_text(result) or "已收到 /select，但暂时没有返回选股结果。"
    reply_text = str(result.get("assistantReply") or "").strip()
    return reply_text or "已收到，但助手暂时没有返回内容。"


def _latest_message_text(result: dict[str, Any]) -> str | None:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            return text
    return None


def _reply_from_chat_result(result: dict[str, Any]) -> dict[str, Any]:
    error = _extract_error(result)
    if error is not None:
        return {"handled": True, "replyText": error, "state": "rejected"}
    reply_text = str(result.get("assistantReply") or "").strip() or (_latest_message_text(result) or "")
    if not reply_text:
        return {"handled": True, "replyText": "已收到，但助手暂时没有返回内容。", "state": "empty_reply"}
    return {"handled": True, "replyText": reply_text, "state": "replied"}


def _format_select_completion_reply(text: str) -> str:
    return f"{text}\n\n如需完整选股报告 PDF，回复“发送完整报告”。"


def _wechat_help_message() -> str:
    return "\n".join(
        [
            "命令帮助",
            "/report TSLA：美股报告",
            "/report 600519.SH：A股报告",
            "/report BTC/USDT：加密报告",
            "/select：A股选股结果",
            "/select 2：加密选股结果",
            "/select 刷新：刷新A股数据",
            "/select 2 刷新：刷新加密数据",
            "/alert BTC 高于 70000：价格提醒",
            "/sched TSLA 每天 08:00：定时报告",
            "/maint：维护状态",
            "发送完整报告：发PDF",
            "回到普通聊天：退出报告",
        ]
    )


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


def _submit_background_job(job: Callable[[], None]) -> None:
    Thread(target=job, daemon=True).start()
