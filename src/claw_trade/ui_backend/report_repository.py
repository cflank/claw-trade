from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote

_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class UiProductError(Exception):
    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True)
class SavedReportRecord:
    id: str
    instrument_code: str
    instrument_name: str | None
    market: str
    title: str
    generated_at: str
    summary_snippet: str
    markdown: str
    markdown_hash: str
    pm_final_conclusion: str | None
    created_at: str
    updated_at: str
    asset_dir: Path | None = None
    origin_context_id: str | None = None


class ReportRepository:
    def __init__(self, *, deletion_index_path: Path | None = None) -> None:
        self._reports: dict[str, SavedReportRecord] = {}
        self._report_order: list[str] = []
        self._deletion_index_path = deletion_index_path
        self._deleted_report_ids: set[str] = _load_deleted_report_ids(deletion_index_path)

    def save_succeeded_report(
        self,
        *,
        report_id: str,
        instrument_code: str,
        market: str,
        title: str,
        markdown: str,
        generated_at: str | None = None,
        instrument_name: str | None = None,
        summary_snippet: str | None = None,
        pm_final_conclusion: str | None = None,
        asset_dir: Path | None = None,
        origin_context_id: str | None = None,
        source_status: str = "succeeded",
    ) -> SavedReportRecord:
        if source_status != "succeeded":
            raise UiProductError("REPORT_NOT_READY", "仅成功报告可以写入历史。")
        if report_id in self._deleted_report_ids:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        text = markdown.strip()
        if not text:
            raise UiProductError("REPORT_NOT_READY", "报告正文为空，无法保存。")
        now = _now_iso()
        record = SavedReportRecord(
            id=report_id,
            instrument_code=instrument_code,
            instrument_name=instrument_name,
            market=market,
            title=title.strip() or f"{instrument_code} 报告",
            generated_at=generated_at or now,
            summary_snippet=(summary_snippet or _default_summary_snippet(text)).strip(),
            markdown=markdown,
            markdown_hash=sha256(markdown.encode("utf-8")).hexdigest(),
            pm_final_conclusion=pm_final_conclusion.strip() if pm_final_conclusion else None,
            created_at=now,
            updated_at=now,
            asset_dir=asset_dir.resolve() if asset_dir is not None else None,
            origin_context_id=_optional_report_text(origin_context_id),
        )
        self._reports[report_id] = record
        if report_id not in self._report_order:
            self._report_order.append(report_id)
        return record

    def list_saved_reports(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for report_id in reversed(self._report_order):
            report = self._reports[report_id]
            items.append(to_saved_report_for_user(report))
        return items

    def list_saved_report_records(self) -> tuple[SavedReportRecord, ...]:
        return tuple(self._reports[report_id] for report_id in self._report_order)

    def get_report(self, report_id: str) -> SavedReportRecord | None:
        return self._reports.get(report_id)

    def is_deleted_report(self, report_id: str) -> bool:
        return report_id in self._deleted_report_ids

    def deleted_report_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._deleted_report_ids))

    def delete_saved_report(self, report_id: str) -> bool:
        existed = report_id in self._reports or report_id in self._deleted_report_ids
        self._reports.pop(report_id, None)
        self._report_order = [item for item in self._report_order if item != report_id]
        self._deleted_report_ids.add(report_id)
        self._persist_deleted_report_ids()
        return existed

    def remove_report_state(self, report_id: str) -> bool:
        existed = report_id in self._reports
        self._reports.pop(report_id, None)
        self._report_order = [item for item in self._report_order if item != report_id]
        return existed

    def discard_deleted_report_id(self, report_id: str) -> bool:
        existed = report_id in self._deleted_report_ids
        self._deleted_report_ids.discard(report_id)
        if existed:
            self._persist_deleted_report_ids()
        return existed

    def read_markdown(self, report_id: str) -> str | None:
        report = self._reports.get(report_id)
        return None if report is None else report.markdown

    def read_pm_final_conclusion(self, report_id: str) -> str | None:
        report = self._reports.get(report_id)
        return None if report is None else report.pm_final_conclusion

    def resolve_report_asset(self, report_id: str, asset_path: str) -> Path | None:
        report = self.get_report(report_id)
        if report is None or report.asset_dir is None:
            return None
        cleaned = asset_path.strip().replace("\\", "/")
        if not cleaned or cleaned.startswith("/") or "://" in cleaned:
            return None
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned.startswith("assets/"):
            cleaned = cleaned[len("assets/") :]
        asset_root = report.asset_dir.resolve()
        candidate = (asset_root / cleaned).resolve()
        try:
            candidate.relative_to(asset_root)
        except ValueError:
            return None
        return candidate

    def list_markdown_image_assets(self, report_id: str) -> list[dict[str, Any]]:
        report = self.get_report(report_id)
        if report is None:
            return []
        assets: list[dict[str, Any]] = []
        for title, target in _iter_markdown_image_targets(report.markdown):
            asset_path = self.resolve_report_asset(report_id, target)
            if asset_path is None or not asset_path.exists() or not asset_path.is_file():
                continue
            assets.append(
                {
                    "title": title or asset_path.name,
                    "chart_type": "other",
                    "status": "ready",
                    "captured_at": report.updated_at,
                }
            )
        return assets

    def get_report_detail(
        self,
        report_id: str,
        *,
        completion_summary: dict[str, Any] | None = None,
        chart_evidence: dict[str, Any] | None = None,
        data_source_events: list[dict[str, Any]] | None = None,
        pdf_export: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report = self.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        _ = pdf_export
        markdown = _rewrite_markdown_asset_urls(report)
        assets = [
            {
                "kind": "markdown",
                "available": True,
                "status": "ready",
                "userMessage": None,
                "updatedAt": report.updated_at,
            },
            {
                "kind": "pdf",
                "available": False,
                "status": "not_requested",
                "userMessage": None,
                "updatedAt": None,
            },
        ]
        return {
            "report": to_saved_report_for_user(report),
            "markdown": markdown,
            "completionSummary": completion_summary or {},
            "dataSourceEvents": data_source_events or [],
            "chartEvidence": chart_evidence or {"reportId": report_id, "summary": "missing", "items": []},
            "assets": assets,
        }

    def _persist_deleted_report_ids(self) -> None:
        if self._deletion_index_path is None:
            return
        payload = {
            "schema_version": "ui_deleted_reports.v1",
            "report_ids": sorted(self._deleted_report_ids),
        }
        self._deletion_index_path.parent.mkdir(parents=True, exist_ok=True)
        self._deletion_index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def to_saved_report_for_user(report: SavedReportRecord) -> dict[str, Any]:
    return {
        "id": report.id,
        "instrumentCode": report.instrument_code,
        "instrumentName": report.instrument_name,
        "market": report.market,
        "title": report.title,
        "generatedAt": report.generated_at,
        "summarySnippet": report.summary_snippet,
        "originContextId": report.origin_context_id,
        "canForwardToChannel": _has_channel_reply_target(report.origin_context_id),
    }


def _optional_report_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _has_channel_reply_target(origin_context_id: object) -> bool:
    text = str(origin_context_id or "").strip()
    if not text:
        return False
    parts = [part.strip() for part in text.split(":", 2)]
    return len(parts) == 3 and bool(parts[0]) and bool(parts[2])


def _default_summary_snippet(markdown: str) -> str:
    for line in markdown.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("#"):
            continue
        return text[:120]
    return "完整结论请查看报告正文。"


def _rewrite_markdown_asset_urls(report: SavedReportRecord) -> str:
    if report.asset_dir is None:
        return report.markdown

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        raw_target = match.group(2).strip()
        target, suffix = _split_markdown_target(raw_target)
        if not _is_local_asset_target(target):
            return match.group(0)
        url = (
            "/api/ui/get-report-asset"
            f"?reportId={quote(report.id, safe='')}"
            f"&assetPath={quote(target, safe='')}"
        )
        return f"![{alt}]({url}{suffix})"

    return _MARKDOWN_IMAGE_RE.sub(replace, report.markdown)


def _iter_markdown_image_targets(markdown: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for alt, raw_target in _MARKDOWN_IMAGE_RE.findall(markdown):
        target, _suffix = _split_markdown_target(raw_target.strip())
        if _is_local_asset_target(target):
            targets.append((alt.strip(), target))
    return targets


def _split_markdown_target(raw_target: str) -> tuple[str, str]:
    if " " not in raw_target:
        return raw_target, ""
    target, suffix = raw_target.split(maxsplit=1)
    return target, f" {suffix}"


def _is_local_asset_target(target: str) -> bool:
    normalized = target.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.startswith("assets/")


def _load_deleted_report_ids(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    raw_ids = payload.get("report_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list):
        return set()
    return {str(item).strip() for item in raw_ids if str(item).strip()}
