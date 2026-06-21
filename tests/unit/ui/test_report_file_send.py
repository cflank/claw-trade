from __future__ import annotations

from pathlib import Path

from claw_trade.ui_backend import pdf_validation
from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    PdfRuntimeCapability,
)
from claw_trade.ui_backend.report_cleanup import ReportFileSendTracker
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder


class _FileChannelBridge:
    def __init__(
        self,
        *,
        state: str,
        can_send_file: bool,
        send_result: dict[str, object] | None = None,
        default_report_file_target: tuple[str, str | None] | None = None,
    ) -> None:
        self.state = state
        self.can_send_file = can_send_file
        self.send_result = send_result if send_result is not None else {"sent": True, "messageId": "msg-1"}
        self.default_report_file_target = default_report_file_target
        self.last_payload: bytes | None = None
        self.last_file_path: Path | None = None
        self.last_target: str | None = None
        self.last_account_id: str | None = None
        self.send_calls = 0

    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        return {"state": self.state, "canSendText": True, "canSendFile": self.can_send_file}

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None:
        _ = channel_kind
        return self.default_report_file_target

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        return {"sent": True}

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
    ) -> dict[str, object]:
        self.send_calls += 1
        self.last_payload = payload
        self.last_file_path = file_path
        self.last_target = target
        self.last_account_id = account_id
        return dict(self.send_result)


class _PassRenderer:
    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        _ = (markdown, report_asset_dir)
        return b"%PDF-1.7\n" + (b"A" * 700)


class _FailRenderer:
    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        _ = (markdown, report_asset_dir)
        raise RuntimeError("pdf failed")


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


def _install_valid_pdf_extractor(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        pdf_validation,
        "_extract_text_with_available_engine",
        lambda _payload: (1, "BTC 报告 正文", "stub"),
    )


def _make_service(
    channel: _FileChannelBridge,
    *,
    asset_dir: Path | None = None,
    renderer=None,
    origin_context_id: str | None = None,
    file_send_tracker: ReportFileSendTracker | None = None,
) -> ReportNotificationService:  # type: ignore[no-untyped-def]
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-file",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n正文",
        asset_dir=asset_dir,
        origin_context_id=origin_context_id,
    )
    summary_builder = CompletionSummaryBuilder(repo)
    pdf_service = PdfExportService(
        repo,
        renderer=renderer or _PassRenderer(),
        runtime_capabilities_provider=_ready_capabilities,
    )
    pdf_service.export_saved_markdown_to_pdf("r-file", request_id="seed-pdf")
    return ReportNotificationService(
        repo,
        summary_builder,
        pdf_service,
        channel,
        file_send_tracker=file_send_tracker,
    )


def test_request_full_report_file_requires_file_capability(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    service = _make_service(_FileChannelBridge(state="connected", can_send_file=False))
    result = service.request_full_report_file("r-file", "req-file-1", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"


def test_request_full_report_file_sends_ready_pdf(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(channel, asset_dir=tmp_path / "reports" / "assets")
    result = service.request_full_report_file("r-file", "req-file-2", target="sender-1")
    assert result["sent"] is True
    assert result["messageId"] == "msg-1"
    assert result["userMessage"] == "完整报告已发送。"
    assert "暂不可发送" not in result["userMessage"]
    assert channel.last_payload is None
    assert channel.last_file_path is not None
    assert channel.last_file_path.exists()
    assert "localPath" not in result


def test_request_full_report_file_marks_report_in_flight_while_sending(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    tracker = ReportFileSendTracker()
    seen_active_ids: list[set[str]] = []
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(
        channel,
        asset_dir=tmp_path / "reports" / "assets",
        file_send_tracker=tracker,
    )
    original_send = channel.send_report_file_via_channel

    def _send_with_tracking(**kwargs):  # type: ignore[no-untyped-def]
        seen_active_ids.append(tracker.active_report_ids())
        return original_send(**kwargs)

    channel.send_report_file_via_channel = _send_with_tracking  # type: ignore[method-assign]

    result = service.request_full_report_file("r-file", "req-file-in-flight", target="sender-1")

    assert result["sent"] is True
    assert seen_active_ids == [{"r-file"}]
    assert tracker.active_report_ids() == set()


def test_request_full_report_file_uses_saved_wechat_origin_when_target_is_not_explicit(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(
        channel,
        asset_dir=tmp_path / "reports" / "assets",
        origin_context_id="wechat_clawbot:account-1:sender-1",
    )
    result = service.request_full_report_file("r-file", "req-file-current-wechat")
    assert result["sent"] is True
    assert channel.send_calls == 1
    assert channel.last_file_path is not None
    assert channel.last_target == "sender-1"
    assert channel.last_account_id == "account-1"


def test_request_full_report_file_uses_default_wechat_conversation_when_saved_origin_is_missing(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(
        state="connected",
        can_send_file=True,
        default_report_file_target=("sender-default", "account-default"),
    )
    service = _make_service(channel, asset_dir=tmp_path / "reports" / "assets")
    result = service.request_full_report_file("r-file", "req-file-default-wechat")
    assert result["sent"] is True
    assert channel.send_calls == 1
    assert channel.last_file_path is not None
    assert channel.last_target == "sender-default"
    assert channel.last_account_id == "account-default"


def test_request_full_report_file_without_target_or_saved_origin_returns_clear_message(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(channel, asset_dir=tmp_path / "reports" / "assets")
    result = service.request_full_report_file("r-file", "req-file-no-wechat-origin")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"
    assert result["userMessage"] == "完整报告文件暂不可发送，请在设备界面查看。"
    assert channel.send_calls == 0


def test_request_full_report_file_pdf_failed_returns_unsupported_and_never_calls_send(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(channel, renderer=_FailRenderer())
    result = service.request_full_report_file("r-file", "req-file-pdf-failed", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"
    assert channel.send_calls == 0


def test_request_full_report_file_missing_artifact_path_returns_unsupported(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(channel, asset_dir=tmp_path / "reports" / "assets")
    pdf_record = service._pdf_export_service.get_latest_record("r-file")
    assert pdf_record is not None
    artifact_path = service._repository.pdf_artifact_path("r-file", pdf_record.pdf_artifact_id or "")
    assert artifact_path is not None
    artifact_path.unlink()

    result = service.request_full_report_file("r-file", "req-file-missing-artifact", target="sender-1")

    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"
    assert channel.send_calls == 0


def test_request_full_report_file_artifact_read_failure_returns_unsupported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(state="connected", can_send_file=True)
    service = _make_service(channel)

    def _raise_read_failure(_report_id: str, _artifact_id: str) -> bytes:
        raise UiProductError("REPORT_NOT_READY", "完整报告文件暂不可发送，请在设备界面查看。")

    monkeypatch.setattr(service._repository, "read_pdf_bytes", _raise_read_failure)
    result = service.request_full_report_file("r-file", "req-file-read-failed", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"
    assert channel.send_calls == 0


def test_request_full_report_file_treats_message_id_only_response_as_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(
        state="connected",
        can_send_file=True,
        send_result={"messageId": "msg-only"},
    )
    service = _make_service(channel)
    result = service.request_full_report_file("r-file", "req-file-message-id-only", target="sender-1")
    assert result["sent"] is True
    assert result["messageId"] == "msg-only"


def test_request_full_report_file_does_not_claim_success_when_channel_send_returns_false(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(
        state="connected",
        can_send_file=True,
        send_result={"sent": False, "messageId": "msg-1"},
    )
    service = _make_service(channel)
    result = service.request_full_report_file("r-file", "req-file-sent-false", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"


def test_request_full_report_file_does_not_claim_success_when_channel_send_returns_ok_false(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(
        state="connected",
        can_send_file=True,
        send_result={"ok": False, "messageId": "msg-1"},
    )
    service = _make_service(channel)
    result = service.request_full_report_file("r-file", "req-file-ok-false", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"


def test_request_full_report_file_does_not_claim_success_when_channel_send_returns_empty_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_valid_pdf_extractor(monkeypatch)
    channel = _FileChannelBridge(
        state="connected",
        can_send_file=True,
        send_result={},
    )
    service = _make_service(channel)
    result = service.request_full_report_file("r-file", "req-file-empty-send", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"
