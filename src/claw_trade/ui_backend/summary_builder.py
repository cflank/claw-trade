from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ReportCompletionSummary:
    id: str
    report_id: str
    instrument_code: str
    generated_at: str
    final_conclusion: str
    core_reasons: tuple[str, ...]
    main_risks: tuple[str, ...]
    failed_configured_data_sources: tuple[dict[str, Any], ...]
    full_report_available: bool
    pdf_available: bool
    created_at: str

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reportId": self.report_id,
            "instrumentCode": self.instrument_code,
            "generatedAt": self.generated_at,
            "finalConclusion": self.final_conclusion,
            "coreReasons": list(self.core_reasons),
            "mainRisks": list(self.main_risks),
            "failedConfiguredDataSources": list(self.failed_configured_data_sources),
            "fullReportAvailable": self.full_report_available,
            "pdfAvailable": self.pdf_available,
            "createdAt": self.created_at,
        }


class CompletionSummaryBuilder:
    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository
        self._cache: dict[str, ReportCompletionSummary] = {}

    def build_completion_summary_from_saved_report(
        self,
        report_id: str,
        *,
        failed_configured_data_sources: list[dict[str, Any]] | None = None,
        pdf_available: bool = False,
    ) -> dict[str, Any]:
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        markdown = self._repository.read_markdown(report_id)
        if not markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")

        pm_conclusion = self._repository.read_pm_final_conclusion(report_id)
        final_conclusion = _extract_final_conclusion(pm_conclusion, markdown)
        reasons = _extract_bullets(markdown, ("核心理由", "投资理由", "结论依据", "主要依据"), 3)
        risks = _extract_bullets(markdown, ("主要风险", "风险提示", "关键风险"), 2)
        if not reasons:
            reasons = ["完整理由请查看报告正文。"]
        if not risks:
            risks = ["主要风险请查看报告正文。"]
        summary = ReportCompletionSummary(
            id=f"summary_{report.id}",
            report_id=report.id,
            instrument_code=report.instrument_code,
            generated_at=report.generated_at,
            final_conclusion=final_conclusion,
            core_reasons=tuple(reasons),
            main_risks=tuple(risks),
            failed_configured_data_sources=tuple(failed_configured_data_sources or ()),
            full_report_available=True,
            pdf_available=pdf_available,
            created_at=_now_iso(),
        )
        self._cache[report_id] = summary
        return summary.to_user_dict()

    def get_cached(self, report_id: str) -> dict[str, Any] | None:
        summary = self._cache.get(report_id)
        return None if summary is None else summary.to_user_dict()


def render_completion_summary_text(summary: dict[str, Any]) -> str:
    reasons = summary.get("coreReasons") or []
    risks = summary.get("mainRisks") or []
    reason_text = "；".join(str(item) for item in reasons[:3]) or "完整理由请查看报告正文。"
    risk_text = "；".join(str(item) for item in risks[:2]) or "主要风险请查看报告正文。"
    final_text = str(summary.get("finalConclusion") or "最终结论请查看报告正文。")
    return (
        f"报告已完成。\n"
        f"最终结论：{final_text}\n"
        f"核心理由：{reason_text}\n"
        f"主要风险：{risk_text}\n"
        "查看完整报告以获取全部分析细节。"
    )


def _extract_final_conclusion(pm_conclusion: str | None, markdown: str) -> str:
    if pm_conclusion and pm_conclusion.strip():
        return pm_conclusion.strip()
    section = _extract_section_text(markdown, ("最终裁决", "最终投资决策", "最终结论"))
    if section:
        first = re.split(r"[。！？\n]", section.strip(), maxsplit=1)[0].strip()
        if first:
            return first
    return "最终结论已写入完整报告，请查看正文。"


def _extract_bullets(markdown: str, headings: tuple[str, ...], max_items: int) -> list[str]:
    section = _extract_section_text(markdown, headings)
    if not section:
        return []
    out: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[-*]\s+", line):
            out.append(re.sub(r"^[-*]\s+", "", line).strip())
        elif re.match(r"^\d+[.)]\s+", line):
            out.append(re.sub(r"^\d+[.)]\s+", "", line).strip())
        if len(out) >= max_items:
            break
    return [item for item in out if item]


def _extract_section_text(markdown: str, headings: tuple[str, ...]) -> str:
    lines = markdown.splitlines()
    start_index: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip().strip("#").strip()
        if stripped in headings:
            start_index = index + 1
            break
    if start_index is None:
        return ""
    content: list[str] = []
    for line in lines[start_index:]:
        if line.strip().startswith("#"):
            break
        content.append(line)
    return "\n".join(content).strip()
