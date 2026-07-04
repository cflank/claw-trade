from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from claw_trade.ui_backend.pdf_export_service import PdfExportService, to_pdf_export_for_user
from claw_trade.ui_backend.pdf_renderer import PdfHtmlRenderer, PdfKitRenderer
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    PdfRuntimeCapability,
)
from claw_trade.ui_backend.pdf_validation import PdfValidationEvidence, PdfValidationResult
from claw_trade.ui_backend.report_repository import ReportRepository


class _FailRenderer:
    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        raise RuntimeError("render failed")


class _PassRenderer:
    def __init__(self) -> None:
        self.last_asset_dir = None

    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        self.last_asset_dir = report_asset_dir
        return b"%PDF-1.7\n" + (b"A" * 700)


def _repo() -> ReportRepository:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-pdf",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown="# 标题\n正文",
    )
    return repo


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


def test_pdfkit_renderer_calls_from_string_with_required_options(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: dict[str, object] = {}

    def fake_from_string(html: str, output_file: object, *, options: dict[str, object]) -> bytes:
        calls["html"] = html
        calls["output_file"] = output_file
        calls["options"] = dict(options)
        return b"%PDF-1.7\n" + (b"B" * 700)

    monkeypatch.setitem(sys.modules, "pdfkit", SimpleNamespace(from_string=fake_from_string))

    rendered = PdfKitRenderer().render("# 标题\n正文")
    assert rendered.startswith(b"%PDF-")
    assert calls["output_file"] is False
    assert calls["options"] == {
        "encoding": "UTF-8",
        "enable-local-file-access": None,
        "page-size": "A4",
        "margin-top": "20mm",
        "margin-right": "20mm",
        "margin-bottom": "20mm",
        "margin-left": "20mm",
    }


def test_pdf_html_renderer_does_not_double_apply_page_margin() -> None:
    html = PdfHtmlRenderer().render("# 标题\n正文").html
    body_block = html.split("body {", maxsplit=1)[1].split("}", maxsplit=1)[0]

    assert "body {" in html
    assert "margin: 0;" in body_block
    assert "margin: 20mm;" not in body_block


def test_export_saved_markdown_to_pdf_keeps_markdown_hash(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = _repo()
    source_hash = repo.get_report("r-pdf").markdown_hash
    renderer = _PassRenderer()
    service = PdfExportService(repo, renderer=renderer, runtime_capabilities_provider=_ready_capabilities)
    monkeypatch.setattr(
        "claw_trade.ui_backend.pdf_export_service.validate_pdf_bytes",
        lambda *_args, **_kwargs: PdfValidationResult(
            valid=True,
            evidence=PdfValidationEvidence(engine="stub", page_count=1, extracted_text="TSLA 报告"),
        ),
    )
    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-1")
    assert record.state == "ready"
    assert renderer.last_asset_dir is None
    assert repo.get_report("r-pdf").markdown_hash == source_hash
    user = to_pdf_export_for_user(record)
    assert set(user.keys()) == {"reportId", "state", "available", "userMessage", "updatedAt"}


def test_export_saved_markdown_to_pdf_writes_file_when_report_has_asset_dir(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = ReportRepository()
    asset_dir = tmp_path / "reports" / "assets"
    repo.save_succeeded_report(
        report_id="r-pdf",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown="# 标题\n正文",
        asset_dir=asset_dir,
    )
    renderer = _PassRenderer()
    service = PdfExportService(repo, renderer=renderer, runtime_capabilities_provider=_ready_capabilities)
    monkeypatch.setattr(
        "claw_trade.ui_backend.pdf_export_service.validate_pdf_bytes",
        lambda *_args, **_kwargs: PdfValidationResult(
            valid=True,
            evidence=PdfValidationEvidence(engine="stub", page_count=1, extracted_text="TSLA 报告"),
        ),
    )

    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-file")

    assert record.state == "ready"
    assert renderer.last_asset_dir == asset_dir.resolve()
    pdf_path = repo.pdf_artifact_path("r-pdf", record.pdf_artifact_id or "")
    assert pdf_path is not None
    assert pdf_path.parent == tmp_path / "reports" / "pdf"
    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-")
    assert pdf_path.stat().st_size == len(pdf_bytes)


def test_export_saved_markdown_to_pdf_reuses_restored_pdf(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo = ReportRepository()
    asset_dir = tmp_path / "reports" / "assets"
    repo.save_succeeded_report(
        report_id="r-restored-pdf",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown="# 标题\n正文",
        asset_dir=asset_dir,
    )
    pdf_dir = tmp_path / "reports" / "pdf"
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "pdf_restored.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n" + (b"A" * 700))
    repo.restore_pdf_artifact("r-restored-pdf", pdf_path)
    service = PdfExportService(repo, renderer=_FailRenderer(), runtime_capabilities_provider=_ready_capabilities)

    record = service.export_saved_markdown_to_pdf("r-restored-pdf", request_id="pdf-restored")

    assert record.state == "ready"
    assert record.pdf_artifact_id == "pdf_restored"
    assert repo.pdf_artifact_path("r-restored-pdf", "pdf_restored") == pdf_path.resolve()


def test_pdf_export_failure_does_not_write_artifact_when_validation_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = _repo()
    renderer = _PassRenderer()
    service = PdfExportService(repo, renderer=renderer, runtime_capabilities_provider=_ready_capabilities)
    write_calls: list[tuple[str, bytes]] = []
    original_write = repo.write_pdf_artifact

    def write_spy(report_id: str, pdf_bytes: bytes):  # type: ignore[no-untyped-def]
        write_calls.append((report_id, pdf_bytes))
        return original_write(report_id, pdf_bytes)

    monkeypatch.setattr(repo, "write_pdf_artifact", write_spy)
    monkeypatch.setattr(
        "claw_trade.ui_backend.pdf_export_service.validate_pdf_bytes",
        lambda *_args, **_kwargs: PdfValidationResult(valid=False, reason="missing keywords"),
    )

    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-2")
    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert record.failure_detail == "pdf validation failed: missing keywords"
    assert write_calls == []
    assert len(repo.list_saved_reports()) == 1
    assert repo.latest_pdf_artifact("r-pdf") is None


def test_pdf_export_failure_does_not_remove_saved_report() -> None:
    repo = _repo()
    service = PdfExportService(repo, renderer=_FailRenderer(), runtime_capabilities_provider=_ready_capabilities)
    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-2")
    assert record.state == "failed"
    assert record.failure_detail == "render failed"
    assert len(repo.list_saved_reports()) == 1


def test_export_cleans_up_artifact_when_post_write_confirmation_fails(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-pdf-cleanup",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown="# 标题\n正文",
        asset_dir=tmp_path / "reports" / "assets",
    )
    renderer = _PassRenderer()
    service = PdfExportService(repo, renderer=renderer, runtime_capabilities_provider=_ready_capabilities)
    monkeypatch.setattr(
        "claw_trade.ui_backend.pdf_export_service.validate_pdf_bytes",
        lambda *_args, **_kwargs: PdfValidationResult(
            valid=True,
            evidence=PdfValidationEvidence(engine="stub", page_count=1, extracted_text="TSLA 报告"),
        ),
    )

    written_file_paths: list = []
    original_write = repo.write_pdf_artifact

    def write_spy(report_id: str, pdf_bytes: bytes):  # type: ignore[no-untyped-def]
        artifact = original_write(report_id, pdf_bytes)
        if artifact.path is not None:
            written_file_paths.append(artifact.path)
        return artifact

    def raise_post_write_mismatch(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("post write mismatch")

    monkeypatch.setattr(repo, "write_pdf_artifact", write_spy)
    monkeypatch.setattr(service, "_ensure_persisted_artifact_matches", raise_post_write_mismatch)

    record = service.export_saved_markdown_to_pdf("r-pdf-cleanup", request_id="pdf-cleanup")

    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert repo.latest_pdf_artifact("r-pdf-cleanup") is None
    assert written_file_paths
    assert all(not path.exists() for path in written_file_paths)


def test_default_renderer_fallback_still_calls_validator(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class _FakePandoc:
        def convert_text(  # type: ignore[no-untyped-def]
            self,
            source: str,
            to: str,
            format: str,
            outputfile: str,
            extra_args: list[str],
        ) -> str:
            _ = (source, to, format)
            engine = "default"
            for item in extra_args:
                if item.startswith("--pdf-engine="):
                    engine = item.split("=", maxsplit=1)[1]
                    break
            if engine != "default":
                raise RuntimeError(f"{engine} unavailable")
            Path(outputfile).write_bytes(b"%PDF-1.7\n" + (b"F" * 700))
            return ""

    repo = _repo()
    service = PdfExportService(repo, runtime_capabilities_provider=_ready_capabilities)
    monkeypatch.setattr(
        "claw_trade.ui_backend.pdf_renderer.PdfKitRenderer.render",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pdfkit down")),
    )
    monkeypatch.setitem(sys.modules, "pypandoc", _FakePandoc())

    validate_calls: dict[str, int] = {"count": 0}

    def fake_validate(pdf_bytes: bytes, **_kwargs) -> PdfValidationResult:  # type: ignore[no-untyped-def]
        validate_calls["count"] += 1
        assert pdf_bytes.startswith(b"%PDF-")
        return PdfValidationResult(valid=False, reason="forced invalid after fallback")

    monkeypatch.setattr("claw_trade.ui_backend.pdf_export_service.validate_pdf_bytes", fake_validate)

    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-default-fallback")

    assert validate_calls["count"] == 1
    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert repo.latest_pdf_artifact("r-pdf") is None
