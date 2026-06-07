from __future__ import annotations

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    RuntimeProbe,
    detect_pdf_runtime_capabilities,
)
from claw_trade.ui_backend.report_repository import ReportRepository


class _Probe(RuntimeProbe):
    def __init__(
        self,
        *,
        modules: set[str] | None = None,
        commands: dict[tuple[str, ...], tuple[bool, str]] | None = None,
    ) -> None:
        self._modules = modules or set()
        self._commands = commands or {}

    def module_available(self, module_name: str) -> bool:
        return module_name in self._modules

    def command_output(self, args: list[str]) -> tuple[bool, str]:
        return self._commands.get(tuple(args), (False, "missing"))


class _RendererSpy:
    def __init__(self) -> None:
        self.called = False

    def render(self, markdown: str) -> bytes:
        self.called = True
        return b"%PDF-1.7\nfake"


def _repo() -> ReportRepository:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-pdf-gate",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown="# 标题\n正文",
    )
    return repo


def test_detect_pdf_runtime_capabilities_classification() -> None:
    probe = _Probe(
        modules={"markdown", "pdfkit", "pypandoc"},
        commands={
            ("wkhtmltopdf", "--version"): (True, "wkhtmltopdf 0.12.6"),
            ("fc-match", "--version"): (True, "fontconfig version 2.15"),
            ("fc-match", "-f", "%{family}\n", "Noto Sans CJK SC"): (True, "Noto Sans CJK SC"),
            ("pandoc", "--version"): (True, "pandoc 3.8.2.1"),
        },
    )

    capabilities = detect_pdf_runtime_capabilities(probe)

    assert capabilities.primary_ready is True
    assert {item.name for item in capabilities.by_category("primary")} == {
        "python:markdown",
        "python:pdfkit",
        "bin:wkhtmltopdf",
        "bin:fontconfig(fc-match)",
        "font:noto-cjk",
    }
    assert {item.name for item in capabilities.by_category("compatibility")} == {"python:pypandoc", "bin:pandoc"}
    assert {item.name for item in capabilities.by_category("optional")} == {"python:weasyprint"}
    assert capabilities.by_category("optional")[0].available is False


def test_export_fails_when_primary_runtime_capability_missing() -> None:
    probe = _Probe(
        modules={"markdown", "pdfkit", "pypandoc"},
        commands={
            ("wkhtmltopdf", "--version"): (False, "wkhtmltopdf: not found"),
            ("fc-match", "--version"): (True, "fontconfig version 2.15"),
            ("fc-match", "-f", "%{family}\n", "Noto Sans CJK SC"): (True, "Noto Sans CJK SC"),
            ("pandoc", "--version"): (True, "pandoc 3.8.2.1"),
        },
    )
    renderer = _RendererSpy()
    repo = _repo()
    service = PdfExportService(
        repo,
        renderer=renderer,
        runtime_capabilities_provider=lambda: detect_pdf_runtime_capabilities(probe),
    )

    record = service.export_saved_markdown_to_pdf("r-pdf-gate", request_id="req-capability-missing")

    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert renderer.called is False
    assert repo.latest_pdf_artifact("r-pdf-gate") is None


def test_export_fails_when_noto_cjk_font_missing() -> None:
    probe = _Probe(
        modules={"markdown", "pdfkit", "pypandoc"},
        commands={
            ("wkhtmltopdf", "--version"): (True, "wkhtmltopdf 0.12.6"),
            ("fc-match", "--version"): (True, "fontconfig version 2.15"),
            ("fc-match", "-f", "%{family}\n", "Noto Sans CJK SC"): (True, "DejaVu Sans"),
            ("pandoc", "--version"): (True, "pandoc 3.8.2.1"),
        },
    )
    capabilities = detect_pdf_runtime_capabilities(probe)
    assert capabilities.primary_ready is False
    assert "font:noto-cjk" in {item.name for item in capabilities.missing_primary()}

    renderer = _RendererSpy()
    repo = _repo()
    service = PdfExportService(
        repo,
        renderer=renderer,
        runtime_capabilities_provider=lambda: capabilities,
    )

    record = service.export_saved_markdown_to_pdf("r-pdf-gate", request_id="req-font-missing")
    assert record.state == "failed"
    assert record.pdf_artifact_id is None
    assert renderer.called is False
    assert repo.latest_pdf_artifact("r-pdf-gate") is None
