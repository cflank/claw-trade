from __future__ import annotations

from pathlib import Path

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.pdf_renderer import (
    PandocFallbackRenderer,
    PdfKitWithPandocFallbackRenderer,
)
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    PdfRuntimeCapability,
)
from claw_trade.ui_backend.pdf_validation import PdfValidationResult
from claw_trade.ui_backend.report_repository import ReportRepository


class _PrimaryFailRenderer:
    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        raise RuntimeError("primary failed")


class _FakePandoc:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    def convert_text(  # type: ignore[no-untyped-def]
        self,
        source: str,
        to: str,
        format: str,
        outputfile: str,
        extra_args: list[str],
    ) -> str:
        engine = _engine_from_extra_args(extra_args)
        self.calls.append(
            {
                "engine": engine,
                "source": source,
                "to": to,
                "format": format,
                "outputfile": outputfile,
                "extra_args": tuple(extra_args),
            }
        )
        outcome = self._outcomes[engine]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, bytes):
            Path(outputfile).write_bytes(outcome)
            return ""
        if outcome == "empty":
            return ""
        raise RuntimeError(f"unsupported fake outcome: {outcome!r}")


def _engine_from_extra_args(extra_args: list[str]) -> str:
    for item in extra_args:
        if item.startswith("--pdf-engine="):
            return item.split("=", maxsplit=1)[1]
    return "default"


def _repo() -> ReportRepository:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-pandoc",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown="# TSLA 报告\n正文",
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


def test_pandoc_fallback_renderer_engine_order_and_yaml_arg() -> None:
    fake_pandoc = _FakePandoc(
        outcomes={
            "wkhtmltopdf": RuntimeError("wk failed"),
            "weasyprint": RuntimeError("weasy failed"),
            "default": b"%PDF-1.7\n" + (b"P" * 700),
        }
    )
    renderer = PandocFallbackRenderer(pypandoc_module=fake_pandoc)

    rendered = renderer.render("# 标题\n正文")

    assert rendered.startswith(b"%PDF-")
    assert [call["engine"] for call in fake_pandoc.calls] == ["wkhtmltopdf", "weasyprint", "default"]
    assert all("--from=markdown-yaml_metadata_block" in call["extra_args"] for call in fake_pandoc.calls)
    attempts = renderer.last_attempts
    assert [attempt.engine for attempt in attempts] == ["wkhtmltopdf", "weasyprint", "default"]
    assert attempts[0].exception_type == "RuntimeError"
    assert attempts[1].exception_type == "RuntimeError"
    assert attempts[2].exception_type is None
    assert attempts[2].output_exists is True
    assert attempts[2].output_size > 0
    assert all(attempt.output_path for attempt in attempts)


def test_pdf_export_failed_when_all_pandoc_engines_fail_and_no_artifact() -> None:
    fake_pandoc = _FakePandoc(
        outcomes={
            "wkhtmltopdf": RuntimeError("wk failed"),
            "weasyprint": RuntimeError("weasy failed"),
            "default": RuntimeError("default failed"),
        }
    )
    renderer = PdfKitWithPandocFallbackRenderer(
        primary_renderer=_PrimaryFailRenderer(),
        pandoc_renderer=PandocFallbackRenderer(pypandoc_module=fake_pandoc),
    )
    repo = _repo()
    service = PdfExportService(repo, renderer=renderer, runtime_capabilities_provider=_ready_capabilities)

    record = service.export_saved_markdown_to_pdf("r-pandoc", request_id="pandoc-all-fail")

    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert repo.latest_pdf_artifact("r-pandoc") is None
    attempts = renderer.last_pandoc_attempts
    assert [attempt.engine for attempt in attempts] == ["wkhtmltopdf", "weasyprint", "default"]
    assert all(attempt.exception_type == "RuntimeError" for attempt in attempts)
    assert all(attempt.output_size == 0 for attempt in attempts)
    assert all(attempt.output_path for attempt in attempts)


def test_pandoc_fallback_output_still_requires_validator(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake_pandoc = _FakePandoc(
        outcomes={
            "wkhtmltopdf": RuntimeError("wk failed"),
            "weasyprint": RuntimeError("weasy failed"),
            "default": b"%PDF-1.7\n" + (b"Q" * 700),
        }
    )
    renderer = PdfKitWithPandocFallbackRenderer(
        primary_renderer=_PrimaryFailRenderer(),
        pandoc_renderer=PandocFallbackRenderer(pypandoc_module=fake_pandoc),
    )
    repo = _repo()
    service = PdfExportService(repo, renderer=renderer, runtime_capabilities_provider=_ready_capabilities)
    calls: dict[str, int] = {"count": 0}

    def fake_validate(pdf_bytes: bytes, **_kwargs) -> PdfValidationResult:  # type: ignore[no-untyped-def]
        calls["count"] += 1
        assert pdf_bytes.startswith(b"%PDF-")
        return PdfValidationResult(valid=False, reason="forced invalid")

    monkeypatch.setattr("claw_trade.ui_backend.pdf_export_service.validate_pdf_bytes", fake_validate)

    record = service.export_saved_markdown_to_pdf("r-pandoc", request_id="pandoc-validator")

    assert calls["count"] == 1
    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert repo.latest_pdf_artifact("r-pandoc") is None
