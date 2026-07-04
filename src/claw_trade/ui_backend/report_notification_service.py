from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Protocol

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.report_cleanup import ReportFileSendTracker
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.summary_builder import (
    CompletionSummaryBuilder,
    render_completion_summary_text,
)


class ChannelUserBridge(Protocol):
    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]: ...

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]: ...

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes | None = None,
        file_path: Path | None = None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]: ...

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None: ...


class ReportNotificationService:
    def __init__(
        self,
        repository: ReportRepository,
        summary_builder: CompletionSummaryBuilder,
        pdf_export_service: PdfExportService,
        channel_bridge: ChannelUserBridge,
        *,
        in_app_notifier: Callable[[str, str], None] | None = None,
        file_send_tracker: ReportFileSendTracker | None = None,
    ) -> None:
        self._repository = repository
        self._summary_builder = summary_builder
        self._pdf_export_service = pdf_export_service
        self._channel_bridge = channel_bridge
        self._in_app_notifier = in_app_notifier or (lambda _report_id, _text: None)
        self._file_send_tracker = file_send_tracker or ReportFileSendTracker()

    def notify_report_completion(
        self,
        report_id: str,
        *,
        channel_kind: str = "wechat_clawbot",
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        pdf_state = self._pdf_export_service.get_latest_record(report_id)
        if pdf_state is None:
            pdf_state = self._pdf_export_service.export_saved_markdown_to_pdf(
                report_id,
                request_id=f"completion:{report_id}:pdf",
            )
        summary = self._summary_builder.get_cached(report_id)
        if summary is None:
            summary = self._summary_builder.build_completion_summary_from_saved_report(
                report_id,
                pdf_available=bool(pdf_state and pdf_state.state == "ready"),
            )
        status: dict[str, object] | None = None
        can_send_file: bool | None = None
        if target:
            try:
                status = self._channel_bridge.get_channel_status(probe=False)
            except Exception:
                status = {"state": "unknown", "canSendText": False, "canSendFile": False}
            if str(status.get("state")) == "connected" and bool(status.get("canSendText")):
                can_send_file = bool(status.get("canSendFile"))

        text = render_completion_summary_text(summary, can_send_file=can_send_file)
        self._in_app_notifier(report_id, text)

        if not target:
            self._in_app_notifier(report_id, "微信通知暂不可用，已在设备界面显示完成摘要。")
            return {"sent": False, "delivery": "in_app_only", "text": text}

        status = status or {"state": "unknown", "canSendText": False, "canSendFile": False}
        if str(status.get("state")) != "connected" or not bool(status.get("canSendText")):
            self._in_app_notifier(report_id, "微信通知暂不可用，已在设备界面显示完成摘要。")
            return {"sent": False, "delivery": "in_app_only", "text": text}
        try:
            sent = self._channel_bridge.send_text(
                channel_kind=channel_kind,
                text=text,
                dedupe_key=f"completion:{report_id}",
                target=target,
                account_id=account_id,
            )
            return {"sent": bool(sent.get("sent", True)), "delivery": "channel", "text": text}
        except Exception:
            self._in_app_notifier(report_id, "微信通知发送失败，已在设备界面显示完成摘要。")
            return {"sent": False, "delivery": "in_app_only", "text": text}

    def request_full_report_file(
        self,
        report_id: str,
        request_id: str,
        *,
        channel_kind: str = "wechat_clawbot",
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        with self._file_send_tracker.track(report_id):
            return self._request_full_report_file(
                report_id,
                request_id,
                channel_kind=channel_kind,
                target=target,
                account_id=account_id,
            )

    def _request_full_report_file(
        self,
        report_id: str,
        request_id: str,
        *,
        channel_kind: str,
        target: str | None,
        account_id: str | None,
    ) -> dict[str, object]:
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")

        resolved_target = str(target or "").strip()
        resolved_account_id = str(account_id or "").strip() or None
        if not resolved_target:
            report_target = _report_target_from_origin_context(
                report.origin_context_id,
                channel_kind=channel_kind,
            )
            if report_target is not None:
                resolved_target, resolved_account_id = report_target
        if not resolved_target:
            report_target = self._channel_bridge.resolve_default_report_file_target(channel_kind=channel_kind)
            if report_target is not None:
                resolved_target, resolved_account_id = report_target
        if not resolved_target:
            return {
                "sent": False,
                "code": "NOTIFICATION_UNAVAILABLE",
                "userMessage": "微信已连接，但没有可投递的微信聊天。请先在要接收报告的聊天里给 ClawBot 发一条消息，再点转发。",
            }

        latest_pdf = self._pdf_export_service.get_latest_record(report_id)
        if latest_pdf is None or latest_pdf.state != "ready":
            latest_pdf = self._pdf_export_service.export_saved_markdown_to_pdf(
                report_id,
                request_id=f"{request_id}:pdf",
            )
        if latest_pdf.state != "ready" or not latest_pdf.pdf_artifact_id:
            return {
                "sent": False,
                "code": "PDF_EXPORT_FAILED",
                "userMessage": latest_pdf.user_message
                or "PDF 暂不可用，完整报告仍可在设备界面查看。",
            }

        status = self._channel_bridge.get_channel_status(probe=True)
        if str(status.get("state")) != "connected":
            return {
                "sent": False,
                "code": "NOTIFICATION_UNAVAILABLE",
                "userMessage": "微信通知暂不可用，请在设备界面查看。",
            }
        if not bool(status.get("canSendFile")):
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整报告文件暂不可发送，请在设备界面查看。",
            }

        try:
            file_path = self._repository.pdf_artifact_path(report_id, latest_pdf.pdf_artifact_id)
            payload = (
                None
                if file_path is not None
                else self._repository.read_pdf_bytes(report_id, latest_pdf.pdf_artifact_id)
            )
        except UiProductError:
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整报告文件暂不可发送，请在设备界面查看。",
            }
        try:
            result = self._channel_bridge.send_report_file_via_channel(
                request_id=request_id,
                report_id=report_id,
                channel_kind=channel_kind,
                file_name=f"{report.instrument_code}_report.pdf",
                payload=payload,
                file_path=file_path,
                target=resolved_target,
                account_id=resolved_account_id,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "FILE_SEND_UNSUPPORTED"))
            if code not in {"NOTIFICATION_UNAVAILABLE", "FILE_SEND_UNSUPPORTED"}:
                code = "FILE_SEND_UNSUPPORTED"
            user_message = str(getattr(exc, "user_message", "") or "").strip()
            return {
                "sent": False,
                "code": code,
                "userMessage": user_message
                or (
                    "完整报告文件暂不可发送，请在设备界面查看。"
                    if code == "FILE_SEND_UNSUPPORTED"
                    else "微信通知暂不可用，请在设备界面查看。"
                ),
            }
        if not isinstance(result, Mapping):
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整报告文件暂不可发送，请在设备界面查看。",
            }
        message_id = result.get("messageId") or result.get("message_id")
        if isinstance(message_id, str):
            message_id = message_id.strip() or None
        else:
            message_id = None
        if result.get("ok") is False:
            sent = False
        elif "sent" in result:
            sent = bool(result.get("sent"))
        else:
            sent = bool(message_id)
        if not sent:
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整报告文件暂不可发送，请在设备界面查看。",
            }
        return {
            "sent": True,
            "messageId": message_id,
            "userMessage": "完整报告已发送。",
        }


def _report_target_from_origin_context(
    origin_context_id: object,
    *,
    channel_kind: str,
) -> tuple[str, str | None] | None:
    text = str(origin_context_id or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(":", 2)]
    if len(parts) != 3:
        return None
    origin_channel, account_id, sender_id = parts
    if origin_channel != channel_kind or not sender_id:
        return None
    return sender_id, account_id or None
