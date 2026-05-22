from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PdfRenderer(Protocol):
    def render(self, markdown: str) -> bytes: ...


@dataclass(frozen=True)
class PdfExportRecord:
    id: str
    report_id: str
    source_markdown_hash: str
    state: str
    pdf_artifact_id: str | None
    user_message: str | None
    updated_at: str


class _DefaultPdfRenderer:
    def render(self, markdown: str) -> bytes:
        return ("PDF\n\n" + markdown).encode("utf-8")


class PdfExportService:
    def __init__(self, repository: ReportRepository, renderer: PdfRenderer | None = None) -> None:
        self._repository = repository
        self._renderer = renderer or _DefaultPdfRenderer()
        self._records: dict[str, PdfExportRecord] = {}
        self._request_cache: dict[str, PdfExportRecord] = {}

    def export_saved_markdown_to_pdf(self, report_id: str, *, request_id: str, force: bool = False) -> PdfExportRecord:
        cached = self._request_cache.get(request_id)
        if cached is not None:
            return cached
        existing = self._records.get(report_id)
        if existing and existing.state == "ready" and not force:
            self._request_cache[request_id] = existing
            return existing

        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        markdown = self._repository.read_markdown(report_id)
        if not markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")

        source_hash_before = sha256(markdown.encode("utf-8")).hexdigest()
        record_id = f"pdf_export_{uuid4().hex}"
        try:
            pdf_bytes = self._renderer.render(markdown)
            source_hash_after = sha256(self._repository.read_markdown(report_id).encode("utf-8")).hexdigest()
            if source_hash_after != source_hash_before:
                raise RuntimeError("markdown content hash changed during export")
            artifact = self._repository.write_pdf_artifact(report_id, pdf_bytes)
            record = PdfExportRecord(
                id=record_id,
                report_id=report_id,
                source_markdown_hash=source_hash_before,
                state="ready",
                pdf_artifact_id=artifact.id,
                user_message=None,
                updated_at=_now_iso(),
            )
        except Exception:
            record = PdfExportRecord(
                id=record_id,
                report_id=report_id,
                source_markdown_hash=source_hash_before,
                state="failed",
                pdf_artifact_id=None,
                user_message="PDF 暂不可用，完整报告仍可在设备界面查看。",
                updated_at=_now_iso(),
            )
        self._records[report_id] = record
        self._request_cache[request_id] = record
        return record

    def export_report_pdf(self, report_id: str, *, request_id: str, force: bool = False) -> dict[str, object]:
        internal = self.export_saved_markdown_to_pdf(report_id, request_id=request_id, force=force)
        return to_pdf_export_for_user(internal)

    def get_latest_record(self, report_id: str) -> PdfExportRecord | None:
        return self._records.get(report_id)


def to_pdf_export_for_user(record: PdfExportRecord) -> dict[str, object]:
    return {
        "reportId": record.report_id,
        "state": record.state,
        "available": record.state == "ready",
        "userMessage": record.user_message,
        "updatedAt": record.updated_at,
    }
