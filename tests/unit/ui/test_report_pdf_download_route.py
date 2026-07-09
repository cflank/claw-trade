from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    PdfRuntimeCapability,
)
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.web.routes_ui import (
    ExportReportPdfRequest,
    SendReportFileRequest,
    download_report_pdf,
    export_report_pdf,
    send_report_file_via_channel,
)


def _request(services: object):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui_services=services)))


class _PassRenderer:
    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        _ = (markdown, report_asset_dir)
        return b"%PDF-1.7\n" + (b"A" * 700)


def _ready_capabilities() -> PdfRuntimeCapabilities:
    return PdfRuntimeCapabilities(
        items=(
            PdfRuntimeCapability(name="python:markdown", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="python:pdfkit", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="bin:wkhtmltopdf", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="bin:fontconfig(fc-match)", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="font:noto-cjk", category="primary", available=True, required=True),
        )
    )


def test_download_report_pdf_is_disabled_without_saved_artifact(tmp_path: Path) -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-download",
        instrument_code="BTC/USDT",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告",
        asset_dir=tmp_path / "run" / "reports" / "assets",
    )
    services = SimpleNamespace(
        repository=repo,
    )

    response = download_report_pdf(_request(services), reportId="r-download")

    assert response.status_code == 409
    assert b"FILE_SEND_UNSUPPORTED" in response.body
    assert b"PDF" in response.body
    assert not (tmp_path / "run" / "reports" / "pdf").exists()


def test_export_report_pdf_route_is_disabled_without_saved_artifact(tmp_path: Path) -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-export",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告",
        asset_dir=tmp_path / "run" / "reports" / "assets",
    )
    services = SimpleNamespace(
        repository=repo,
        pdf_export_service=PdfExportService(
            repo,
            renderer=_PassRenderer(),
            runtime_capabilities_provider=_ready_capabilities,
        ),
    )

    response = export_report_pdf(
        ExportReportPdfRequest(requestId="req-export", reportId="r-export"),
        _request(services),
    )

    assert response.status_code == 200
    assert b'"available":false' in response.body
    assert b"PDF" in response.body
    assert not (tmp_path / "run" / "reports" / "pdf").exists()


def test_send_report_file_route_does_not_send_lan_download_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CLAW_TRADE_UI_PUBLIC_BASE_URL", raising=False)
    service = _ReportNotificationProbe()
    services = SimpleNamespace(report_notification_service=service)

    response = send_report_file_via_channel(
        SendReportFileRequest(requestId="req-1", reportId="r-file", channelKind="wechat_clawbot"),
        _request(services),
    )

    assert response.status_code == 200
    assert "download_url" not in service.kwargs


def test_send_report_file_route_ignores_public_download_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLAW_TRADE_UI_PUBLIC_BASE_URL", "https://download.example.com/app/")
    service = _ReportNotificationProbe()
    services = SimpleNamespace(report_notification_service=service)

    response = send_report_file_via_channel(
        SendReportFileRequest(requestId="req-1", reportId="r-file", channelKind="wechat_clawbot"),
        _request(services),
    )

    assert response.status_code == 200
    assert "download_url" not in service.kwargs


def test_send_report_file_route_uses_origin_context_target() -> None:
    service = _ReportNotificationProbe()
    services = SimpleNamespace(report_notification_service=service)

    response = send_report_file_via_channel(
        SendReportFileRequest(
            requestId="req-origin",
            reportId="r-file",
            channelKind="wechat_clawbot",
            originContextId="wechat_clawbot:account-997:sender-997",
        ),
        _request(services),
    )

    assert response.status_code == 200
    assert service.kwargs["channel_kind"] == "wechat_clawbot"
    assert service.kwargs["target"] == "sender-997"
    assert service.kwargs["account_id"] == "account-997"


class _ReportNotificationProbe:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def request_full_report_file(self, **kwargs):  # type: ignore[no-untyped-def]
        self.kwargs = dict(kwargs)
        return {"sent": True, "userMessage": "ok"}
