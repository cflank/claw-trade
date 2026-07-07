from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.web.routes_ui import (
    SendReportFileRequest,
    download_report_pdf,
    send_report_file_via_channel,
)


def _request(services: object):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui_services=services)))


def test_download_report_pdf_returns_saved_pdf(tmp_path: Path) -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-download",
        instrument_code="BTC/USDT",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告",
        asset_dir=tmp_path / "run" / "reports" / "assets",
    )
    pdf_bytes = b"%PDF-1.7\nsaved pdf"
    artifact = repo.write_pdf_artifact("r-download", pdf_bytes)
    services = SimpleNamespace(repository=repo, pdf_export_service=PdfExportService(repo))

    response = download_report_pdf(_request(services), reportId="r-download")

    assert response.media_type == "application/pdf"
    assert "BTC_USDT_report.pdf" in response.headers["content-disposition"]
    assert Path(getattr(response, "path")) == artifact.path


def test_send_report_file_route_does_not_send_lan_download_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CLAW_TRADE_UI_PUBLIC_BASE_URL", raising=False)
    service = _ReportNotificationProbe()
    services = SimpleNamespace(report_notification_service=service)

    response = send_report_file_via_channel(
        SendReportFileRequest(requestId="req-1", reportId="r-file", channelKind="wechat_clawbot"),
        _request(services),
    )

    assert response.status_code == 200
    assert service.download_url is None


def test_send_report_file_route_passes_public_download_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLAW_TRADE_UI_PUBLIC_BASE_URL", "https://download.example.com/app/")
    service = _ReportNotificationProbe()
    services = SimpleNamespace(report_notification_service=service)

    response = send_report_file_via_channel(
        SendReportFileRequest(requestId="req-1", reportId="r-file", channelKind="wechat_clawbot"),
        _request(services),
    )

    assert response.status_code == 200
    assert service.download_url == "https://download.example.com/app/api/ui/download-report-pdf?reportId=r-file"


class _ReportNotificationProbe:
    def __init__(self) -> None:
        self.download_url: str | None = None

    def request_full_report_file(self, **kwargs):  # type: ignore[no-untyped-def]
        self.download_url = kwargs["download_url"]
        return {"sent": True, "userMessage": "ok"}
