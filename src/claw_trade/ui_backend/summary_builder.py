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
        _ = pdf_available
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        markdown = self._repository.read_markdown(report_id)
        if not markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")

        pm_conclusion = self._repository.read_pm_final_conclusion(report_id)
        final_conclusion = _extract_final_conclusion(pm_conclusion, markdown)
        reasons = _extract_summary_items(
            markdown,
            headings=(
                "核心理由",
                "投资理由",
                "结论依据",
                "主要依据",
                "核心判断",
                "投资结论",
                "投资建议",
                "最终结论",
            ),
            labels=("核心理由", "投资理由", "结论依据", "主要依据"),
            max_items=3,
        )
        risks = _extract_summary_items(
            markdown,
            headings=("主要风险", "风险提示", "关键风险", "风险约束", "最终结论"),
            labels=("主要风险", "风险提示", "关键风险", "核心风险约束", "风险约束"),
            max_items=2,
        )
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
            pdf_available=False,
            created_at=_now_iso(),
        )
        self._cache[report_id] = summary
        return summary.to_user_dict()

    def get_cached(self, report_id: str) -> dict[str, Any] | None:
        summary = self._cache.get(report_id)
        return None if summary is None else summary.to_user_dict()


def render_completion_summary_text(summary: dict[str, Any], *, can_send_file: bool | None = None) -> str:
    reasons = summary.get("coreReasons") or []
    risks = summary.get("mainRisks") or []
    final_text = _compact_item(str(summary.get("finalConclusion") or "最终结论请查看报告正文。"), max_chars=180)
    instrument_code = str(summary.get("instrumentCode") or "").strip()
    lines = [
        "报告已完成",
        f"标的：{instrument_code}" if instrument_code else "",
        "",
        "结论",
        final_text,
        "",
        "核心理由",
        *_numbered_lines(reasons[:3], fallback="完整理由请查看报告正文。", max_chars=110),
        "",
        "主要风险",
        *_numbered_lines(risks[:2], fallback="主要风险请查看报告正文。", max_chars=110),
        "",
        "完整报告",
        "查看完整报告可获取全部分析细节。",
    ]
    if can_send_file is True:
        lines.append("需要 PDF 时，回复“发送完整报告”。")
    elif can_send_file is False:
        lines.append("当前微信只能发送文字通知，PDF 暂不能从该通道发送。")
    else:
        lines.append("完整报告可在设备界面查看。")
    return "\n".join(line for line in lines if line != "")


def _numbered_lines(items: list[Any], *, fallback: str, max_chars: int) -> list[str]:
    cleaned = [_compact_item(str(item), max_chars=max_chars) for item in items]
    cleaned = [item for item in cleaned if _is_meaningful_item(item)]
    if not cleaned:
        cleaned = [fallback]
    return [f"{index}. {item}" for index, item in enumerate(cleaned, start=1)]


def _extract_final_conclusion(pm_conclusion: str | None, markdown: str) -> str:
    if pm_conclusion and pm_conclusion.strip():
        return pm_conclusion.strip()
    section = _extract_section_text(markdown, ("最终裁决", "最终投资决策", "最终结论"))
    if section:
        first = _first_sentence(section)
        if first:
            return first
    labeled = _extract_labeled_items(
        markdown,
        ("最终裁决", "组合经理最终决定", "最终投资决策", "最终结论", "投资建议"),
        1,
    )
    if labeled:
        return labeled[0]
    section = _extract_section_text(markdown, ("投资结论", "投资建议", "组合动作"))
    if section:
        first = _first_sentence(section)
        if first:
            return first
    return "最终结论已写入完整报告，请查看正文。"


def _extract_summary_items(
    markdown: str,
    *,
    headings: tuple[str, ...],
    labels: tuple[str, ...],
    max_items: int,
) -> list[str]:
    labeled = _extract_labeled_items(markdown, labels, max_items)
    if labeled:
        return _dedupe_items(labeled, max_items)
    out: list[str] = []
    for section in _extract_section_texts(markdown, headings):
        if len(out) >= max_items:
            break
        out.extend(_extract_items_from_text(section, max_items - len(out)))
    if out:
        return _dedupe_items(out, max_items)
    if len(out) < max_items:
        out.extend(_extract_paragraph_items(markdown, max_items - len(out)))
    return _dedupe_items(out, max_items)


def _extract_labeled_items(markdown: str, labels: tuple[str, ...], max_items: int) -> list[str]:
    out: list[str] = []
    for raw_line in markdown.splitlines():
        line = _clean_markdown_text(raw_line)
        if not line:
            continue
        for label in labels:
            match = re.match(rf"^{re.escape(label)}\s*[:：]\s*(.+)$", line)
            if not match:
                continue
            item = _compact_item(match.group(1).strip())
            if _is_meaningful_item(item):
                out.append(item)
            break
        if len(out) >= max_items:
            break
    return out


def _extract_items_from_text(text: str, max_items: int) -> list[str]:
    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(?:[-*+]\s+|\d+[.)、]\s+)", line):
            item = _compact_item(_clean_markdown_text(line))
            if _is_meaningful_item(item):
                out.append(item)
        if len(out) >= max_items:
            return out
    if out:
        return out
    return _extract_paragraph_items(text, max_items)


def _extract_paragraph_items(markdown: str, max_items: int) -> list[str]:
    out: list[str] = []
    paragraph: list[str] = []
    for raw_line in [*markdown.splitlines(), ""]:
        line = raw_line.strip()
        if line.startswith("#"):
            line = ""
        if not line:
            if paragraph:
                item = _compact_item(_clean_markdown_text(" ".join(paragraph)))
                if _is_meaningful_item(item):
                    out.append(item)
                paragraph = []
            if len(out) >= max_items:
                break
            continue
        paragraph.append(line)
    return out


def _extract_section_text(markdown: str, headings: tuple[str, ...]) -> str:
    sections = _extract_section_texts(markdown, headings)
    return sections[0] if sections else ""


def _extract_section_texts(markdown: str, headings: tuple[str, ...]) -> list[str]:
    lines = markdown.splitlines()
    sections: list[str] = []
    start_index: int | None = None
    start_level: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s*(.+?)\s*$", line.strip())
        if not match:
            continue
        stripped = _normalize_heading(match.group(2))
        if any(heading in stripped for heading in headings):
            start_index = index + 1
            start_level = len(match.group(1))
            content: list[str] = []
            for content_line in lines[start_index:]:
                content_match = re.match(r"^(#{1,6})\s+", content_line.strip())
                if content_match and len(content_match.group(1)) <= start_level:
                    break
                content.append(content_line)
            section = "\n".join(content).strip()
            if section:
                sections.append(section)
    return sections


def _normalize_heading(text: str) -> str:
    cleaned = _clean_markdown_text(text)
    cleaned = re.sub(r"^[^\w\u4e00-\u9fff]+", "", cleaned)
    cleaned = re.sub(r"^(?:第?[一二三四五六七八九十百千万\d]+[章节、.．:：]\s*)+", "", cleaned)
    return cleaned.strip()


def _first_sentence(text: str) -> str:
    for item in _extract_paragraph_items(text, 1):
        first = re.split(r"(?<=[。！？!?])\s*", item, maxsplit=1)[0].strip()
        return first or item
    return ""


def _clean_markdown_text(text: str) -> str:
    cleaned = re.sub(r"!\[[^\]]*]\([^)]+\)", "", text)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = cleaned.replace("*", "")
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" \t-")


def _compact_item(text: str, max_chars: int = 120) -> str:
    cleaned = _clean_markdown_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    first_sentence = re.split(r"(?<=[。！？!?])\s*", cleaned, maxsplit=1)[0].strip()
    if 12 <= len(first_sentence) <= max_chars:
        return first_sentence
    return f"{cleaned[: max_chars - 3].rstrip()}..."


def _is_meaningful_item(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 3:
        return False
    if stripped.endswith((":", "：")):
        return False
    return bool(re.search(r"[\w\u4e00-\u9fff]", stripped))


def _dedupe_items(items: list[str], max_items: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _compact_item(item)
        if not _is_meaningful_item(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= max_items:
            break
    return out
