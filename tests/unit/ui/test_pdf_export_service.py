from __future__ import annotations

from claw_trade.ui_backend.pdf_export_service import PdfExportService, to_pdf_export_for_user
from claw_trade.ui_backend.report_repository import ReportRepository


class _FailRenderer:
    def render(self, markdown: str) -> bytes:
        raise RuntimeError("render failed")


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


def test_export_saved_markdown_to_pdf_keeps_markdown_hash() -> None:
    repo = _repo()
    source_hash = repo.get_report("r-pdf").markdown_hash
    service = PdfExportService(repo)
    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-1")
    assert record.state == "ready"
    assert repo.get_report("r-pdf").markdown_hash == source_hash
    user = to_pdf_export_for_user(record)
    assert set(user.keys()) == {"reportId", "state", "available", "userMessage", "updatedAt"}


def test_pdf_export_failure_does_not_remove_saved_report() -> None:
    repo = _repo()
    service = PdfExportService(repo, renderer=_FailRenderer())
    record = service.export_saved_markdown_to_pdf("r-pdf", request_id="pdf-2")
    assert record.state == "failed"
    assert len(repo.list_saved_reports()) == 1
