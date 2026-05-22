from __future__ import annotations

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder


class _FileChannelBridge:
    def __init__(self, *, state: str, can_send_file: bool, send_success: bool = True) -> None:
        self.state = state
        self.can_send_file = can_send_file
        self.send_success = send_success
        self.last_payload: bytes | None = None

    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        return {"state": self.state, "canSendText": True, "canSendFile": self.can_send_file}

    def send_text(self, *, channel_kind: str, text: str, dedupe_key: str) -> dict[str, object]:
        return {"sent": True}

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes,
    ) -> dict[str, object]:
        self.last_payload = payload
        return {"sent": self.send_success, "messageId": "msg-1" if self.send_success else None}


def _make_service(channel: _FileChannelBridge) -> ReportNotificationService:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-file",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n正文",
    )
    summary_builder = CompletionSummaryBuilder(repo)
    pdf_service = PdfExportService(repo)
    pdf_service.export_saved_markdown_to_pdf("r-file", request_id="seed-pdf")
    return ReportNotificationService(repo, summary_builder, pdf_service, channel)


def test_request_full_report_file_requires_file_capability() -> None:
    service = _make_service(_FileChannelBridge(state="connected", can_send_file=False))
    result = service.request_full_report_file("r-file", "req-file-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"


def test_request_full_report_file_sends_ready_pdf() -> None:
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(channel)
    result = service.request_full_report_file("r-file", "req-file-2")
    assert result["sent"] is True
    assert result["messageId"] == "msg-1"
    assert channel.last_payload is not None
    assert "localPath" not in result


def test_request_full_report_file_does_not_claim_success_when_channel_send_returns_false() -> None:
    channel = _FileChannelBridge(state="connected", can_send_file=True, send_success=False)
    service = _make_service(channel)
    result = service.request_full_report_file("r-file", "req-file-3")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"
