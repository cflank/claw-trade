from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from claw_trade.ui_backend.pdf_renderer import PdfKitWithPandocFallbackRenderer
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    detect_pdf_runtime_capabilities,
)
from claw_trade.ui_backend.pdf_validation import validate_pdf_bytes
from claw_trade.ui_backend.report_repository import (
    ReportRepository,
    SavedReportRecord,
    UiProductError,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PdfRenderer(Protocol):
    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> bytes: ...


@dataclass(frozen=True)
class PdfExportRecord:
    id: str
    report_id: str
    source_markdown_hash: str
    state: str
    user_message: str | None
    failure_detail: str | None
    updated_at: str


class _DefaultPdfRenderer:
    def __init__(self) -> None:
        self._renderer = PdfKitWithPandocFallbackRenderer()

    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> bytes:
        return self._renderer.render(markdown, report_asset_dir=report_asset_dir)


class PdfExportService:
    def __init__(
        self,
        repository: ReportRepository,
        renderer: PdfRenderer | None = None,
        runtime_capabilities_provider: Callable[[], PdfRuntimeCapabilities] | None = None,
    ) -> None:
        self._repository = repository
        self._renderer = renderer or _DefaultPdfRenderer()
        self._runtime_capabilities_provider = runtime_capabilities_provider or detect_pdf_runtime_capabilities
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

        report, markdown, source_hash_before = self._load_pdf_source(report_id)
        record_id = f"pdf_export_{uuid4().hex}"
        try:
            self._render_validated_pdf_bytes(report_id, report, markdown, source_hash_before)
            record = PdfExportRecord(
                id=record_id,
                report_id=report_id,
                source_markdown_hash=source_hash_before,
                state="ready",
                user_message=None,
                failure_detail=None,
                updated_at=_now_iso(),
            )
        except Exception as exc:
            record = PdfExportRecord(
                id=record_id,
                report_id=report_id,
                source_markdown_hash=source_hash_before,
                state="failed",
                user_message=pdf_export_user_message(exc),
                failure_detail=_trim_failure_detail(str(exc)),
                updated_at=_now_iso(),
            )
        self._records[report_id] = record
        self._request_cache[request_id] = record
        return record

    def render_saved_markdown_to_pdf_bytes(self, report_id: str) -> bytes:
        report, markdown, source_hash_before = self._load_pdf_source(report_id)
        return self._render_validated_pdf_bytes(report_id, report, markdown, source_hash_before)

    def export_report_pdf(self, report_id: str, *, request_id: str, force: bool = False) -> dict[str, object]:
        _ = (request_id, force)
        if self._repository.get_report(report_id) is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        return {
            "reportId": report_id,
            "state": "failed",
            "available": False,
            "userMessage": "系统不保存 PDF，请使用微信转发完整报告。",
            "updatedAt": _now_iso(),
        }

    def _load_pdf_source(self, report_id: str) -> tuple[SavedReportRecord, str, str]:
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        markdown = self._repository.read_markdown(report_id)
        if not markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")
        return report, markdown, sha256(markdown.encode("utf-8")).hexdigest()

    def _render_validated_pdf_bytes(
        self,
        report_id: str,
        report: SavedReportRecord,
        markdown: str,
        source_hash_before: str,
    ) -> bytes:
        capabilities = self._runtime_capabilities_provider()
        if not capabilities.primary_ready:
            names = ",".join(item.name for item in capabilities.missing_primary())
            raise RuntimeError(f"pdf primary runtime capability missing: {names}")
        pdf_bytes = self._renderer.render(markdown, report_asset_dir=report.asset_dir)
        validation = validate_pdf_bytes(
            pdf_bytes,
            required_keywords=(report.title, report.instrument_code, "报告"),
        )
        if not validation.valid:
            raise RuntimeError(f"pdf validation failed: {validation.reason}")
        source_after = self._repository.read_markdown(report_id)
        if not source_after:
            raise RuntimeError("markdown content unavailable during export")
        source_hash_after = sha256(source_after.encode("utf-8")).hexdigest()
        if source_hash_after != source_hash_before:
            raise RuntimeError("markdown content hash changed during export")
        return pdf_bytes

def to_pdf_export_for_user(record: PdfExportRecord) -> dict[str, object]:
    payload = {
        "reportId": record.report_id,
        "state": record.state,
        "available": False,
        "userMessage": record.user_message,
        "updatedAt": record.updated_at,
    }
    if record.failure_detail:
        payload["failureDetail"] = record.failure_detail
    return payload


def pdf_export_user_message(exc: Exception) -> str:
    detail = _trim_failure_detail(str(exc))
    if detail.startswith("pdf primary runtime capability missing:"):
        missing = detail.split(":", 1)[1].strip() or "PDF 运行依赖"
        return f"PDF 暂不可用：目标机缺少 {missing}。完整报告仍可在设备界面查看。"
    if detail.startswith("pdf validation failed:"):
        return f"PDF 暂不可用：导出校验失败（{detail.split(':', 1)[1].strip()}）。完整报告仍可在设备界面查看。"
    return "PDF 暂不可用，完整报告仍可在设备界面查看。"


def _trim_failure_detail(text: str) -> str:
    clean = " ".join(text.split())
    if len(clean) <= 240:
        return clean
    return clean[:237] + "..."
