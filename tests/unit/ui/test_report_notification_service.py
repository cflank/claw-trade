from __future__ import annotations

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    PdfRuntimeCapability,
)
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder


class _ChannelBridge:
    def __init__(self, *, connected: bool, can_send_text: bool, can_send_file: bool = False) -> None:
        self._connected = connected
        self._can_send_text = can_send_text
        self._can_send_file = can_send_file
        self.last_text = ""
        self.last_target = ""
        self.last_account_id: str | None = None

    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        return {
            "state": "connected" if self._connected else "disconnected",
            "canSendText": self._can_send_text,
            "canSendFile": self._can_send_file,
        }

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        self.last_text = text
        self.last_target = target or ""
        self.last_account_id = account_id
        assert channel_kind == "wechat_clawbot"
        assert dedupe_key
        return {"sent": True}

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes | None = None,
        file_path=None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        return {"sent": True, "messageId": "m1"}


class _PassRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        _ = (markdown, report_asset_dir)
        self.calls += 1
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


def _build_service(channel: _ChannelBridge, notifications: list[tuple[str, str]]) -> ReportNotificationService:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-notify",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n## 核心理由\n- 资金回流",
        pm_final_conclusion="可继续跟踪",
    )
    summary_builder = CompletionSummaryBuilder(repo)
    pdf_service = PdfExportService(repo)
    return ReportNotificationService(
        repo,
        summary_builder,
        pdf_service,
        channel,
        in_app_notifier=lambda report_id, text: notifications.append((report_id, text)),
    )


def test_notify_report_completion_pushes_summary_not_full_report() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=True)
    service = _build_service(channel, notifications)
    result = service.notify_report_completion("r-notify", target="sender-1", account_id="account-1")
    assert result["sent"] is True
    assert "报告已完成" in channel.last_text
    assert "查看完整报告" in channel.last_text
    assert "回复“发送完整报告”" in channel.last_text
    assert "# 报告" not in channel.last_text
    assert channel.last_target == "sender-1"
    assert channel.last_account_id == "account-1"


def test_notify_report_completion_does_not_generate_pdf_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-notify",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n## 核心理由\n- 资金回流",
        asset_dir=tmp_path / "reports" / "assets",
    )
    summary_builder = CompletionSummaryBuilder(repo)
    renderer = _PassRenderer()
    pdf_service = PdfExportService(
        repo,
        renderer=renderer,
        runtime_capabilities_provider=_ready_capabilities,
    )
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=True)
    service = ReportNotificationService(repo, summary_builder, pdf_service, channel)

    result = service.notify_report_completion("r-notify", target="sender-1", account_id="account-1")

    assert result["sent"] is True
    assert renderer.calls == 0
    assert not (tmp_path / "reports" / "pdf").exists()


def test_notify_report_completion_does_not_ask_for_file_when_channel_cannot_send_file() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=False)
    service = _build_service(channel, notifications)

    result = service.notify_report_completion("r-notify", target="sender-1", account_id="account-1")

    assert result["sent"] is True
    assert "当前微信只能发送文字通知" in channel.last_text
    assert "回复“发送完整报告”" not in channel.last_text
    assert "PDF 暂不能从该通道发送" in channel.last_text


def test_notify_report_completion_falls_back_to_in_app_when_channel_unavailable() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=False, can_send_text=False)
    service = _build_service(channel, notifications)
    result = service.notify_report_completion("r-notify")
    assert result["sent"] is False
    assert any("微信通知暂不可用" in item[1] for item in notifications)
