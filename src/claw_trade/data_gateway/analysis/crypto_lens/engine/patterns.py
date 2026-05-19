from __future__ import annotations

from ..output_contract import AnalysisSection


def enrich_technical_patterns(section: AnalysisSection) -> AnalysisSection:
    notes = list(section.notes)
    rows = section.evidence.get("rows")
    if isinstance(rows, int) and rows >= 120:
        notes.append("样本长度达到多周期形态粗判阈值")
    if section.status.value == "gap":
        notes.append("形态判断受数据缺口限制")
    return AnalysisSection(
        status=section.status,
        summary=section.summary,
        evidence=section.evidence,
        gap_ids=section.gap_ids,
        notes=tuple(notes),
    )
