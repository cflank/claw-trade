from __future__ import annotations

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder


class _FailPdfRenderer:
    def render(self, markdown: str) -> bytes:
        raise RuntimeError("pdf failed")


class _NoopChannelBridge:
    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        return {"state": "disconnected", "canSendText": False, "canSendFile": False}

    def send_text(self, *, channel_kind: str, text: str, dedupe_key: str) -> dict[str, object]:
        return {"sent": False}

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes,
    ) -> dict[str, object]:
        return {"sent": False}


def test_report_stays_saved_when_pdf_export_fails_and_completion_notification_still_works() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-ok",
        instrument_code="AAPL",
        market="US",
        title="AAPL 报告",
        markdown="# 报告\n正文",
    )
    notifications: list[tuple[str, str]] = []
    pdf_service = PdfExportService(repo, renderer=_FailPdfRenderer())
    pdf_record = pdf_service.export_saved_markdown_to_pdf("r-ok", request_id="pdf-fail")
    assert pdf_record.state == "failed"
    assert len(repo.list_saved_reports()) == 1

    summary_builder = CompletionSummaryBuilder(repo)
    service = ReportNotificationService(
        repo,
        summary_builder,
        pdf_service,
        _NoopChannelBridge(),
        in_app_notifier=lambda report_id, text: notifications.append((report_id, text)),
    )
    result = service.notify_report_completion("r-ok")
    assert result["sent"] is False
    assert any("报告已完成" in item[1] for item in notifications)
