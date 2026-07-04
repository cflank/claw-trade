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
    PdfArtifactRecord,
    ReportRepository,
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
    pdf_artifact_id: str | None
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

        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        markdown = self._repository.read_markdown(report_id)
        if not markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")

        source_hash_before = sha256(markdown.encode("utf-8")).hexdigest()
        if not force:
            restored = self._record_existing_pdf_artifact(report_id, source_hash_before)
            if restored is not None:
                self._request_cache[request_id] = restored
                return restored
        record_id = f"pdf_export_{uuid4().hex}"
        try:
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
            source_hash_after = sha256(self._repository.read_markdown(report_id).encode("utf-8")).hexdigest()
            if source_hash_after != source_hash_before:
                raise RuntimeError("markdown content hash changed during export")
            artifact = self._repository.write_pdf_artifact(report_id, pdf_bytes)
            try:
                self._ensure_persisted_artifact_matches(artifact, pdf_bytes)
            except Exception:
                self._repository.remove_pdf_artifact(report_id, artifact.id)
                raise
            record = PdfExportRecord(
                id=record_id,
                report_id=report_id,
                source_markdown_hash=source_hash_before,
                state="ready",
                pdf_artifact_id=artifact.id,
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
                pdf_artifact_id=None,
                user_message=_pdf_export_user_message(exc),
                failure_detail=_trim_failure_detail(str(exc)),
                updated_at=_now_iso(),
            )
        self._records[report_id] = record
        self._request_cache[request_id] = record
        return record

    def export_report_pdf(self, report_id: str, *, request_id: str, force: bool = False) -> dict[str, object]:
        internal = self.export_saved_markdown_to_pdf(report_id, request_id=request_id, force=force)
        return to_pdf_export_for_user(internal)

    def get_latest_record(self, report_id: str) -> PdfExportRecord | None:
        existing = self._records.get(report_id)
        if existing is not None:
            return existing
        report = self._repository.get_report(report_id)
        if report is None:
            return None
        return self._record_existing_pdf_artifact(report_id, report.markdown_hash)

    def _record_existing_pdf_artifact(self, report_id: str, source_markdown_hash: str) -> PdfExportRecord | None:
        artifact = self._repository.latest_pdf_artifact(report_id)
        if artifact is None:
            return None
        record = PdfExportRecord(
            id=f"pdf_export_{artifact.id}",
            report_id=report_id,
            source_markdown_hash=source_markdown_hash,
            state="ready",
            pdf_artifact_id=artifact.id,
            user_message=None,
            failure_detail=None,
            updated_at=artifact.created_at,
        )
        self._records[report_id] = record
        return record

    def _ensure_persisted_artifact_matches(self, artifact: PdfArtifactRecord, pdf_bytes: bytes) -> None:
        stored_bytes = self._repository.read_pdf_bytes(artifact.report_id, artifact.id)
        if stored_bytes != pdf_bytes:
            raise RuntimeError("persisted pdf content mismatch")
        if artifact.path is None:
            return
        if not artifact.path.exists() or not artifact.path.is_file():
            raise RuntimeError(f"persisted pdf path missing: {artifact.path}")
        if artifact.path.stat().st_size != len(pdf_bytes):
            raise RuntimeError(
                f"persisted pdf size mismatch: path={artifact.path.stat().st_size} bytes={len(pdf_bytes)}"
            )
        if not stored_bytes.startswith(b"%PDF-"):
            raise RuntimeError("persisted pdf header invalid")


def to_pdf_export_for_user(record: PdfExportRecord) -> dict[str, object]:
    payload = {
        "reportId": record.report_id,
        "state": record.state,
        "available": record.state == "ready",
        "userMessage": record.user_message,
        "updatedAt": record.updated_at,
    }
    if record.failure_detail:
        payload["failureDetail"] = record.failure_detail
    return payload


def _pdf_export_user_message(exc: Exception) -> str:
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
