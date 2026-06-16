from __future__ import annotations

from typing import Any, Mapping

from ..input_contract import CryptoLensInput
from ..output_contract import AnalysisSection, AnalysisState
from .readiness import state_from_domain_status


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_liquidation(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    base_state = state_from_domain_status(data.domain_status["liquidation_map"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="清算图域缺失，未取得清算热力图数据。",
            evidence={},
            gap_ids=data.gap_ids_for("liquidation_map"),
        )

    invalid_heatmap_count = _to_float(payload.get("invalid_heatmap_sample_count"))
    heatmap_count = _to_float(payload.get("heatmap_sample_count"))

    summary = "清算上下文基于统一数据层清算数据生成。"
    if heatmap_count is not None:
        summary += f" 清算热力图样本 {heatmap_count:.0f} 个."
    if invalid_heatmap_count:
        summary += f" 已剔除异常热力图价格点 {invalid_heatmap_count:.0f} 个."

    status = base_state
    if heatmap_count is None and status == AnalysisState.READY:
        status = AnalysisState.PARTIAL
    notes: tuple[str, ...] = ()
    if invalid_heatmap_count:
        notes = ("清算热力图返回过价格点，但价格量级与当前价格不匹配，已从热力图样本中剔除。",)

    return AnalysisSection(
        status=status,
        summary=summary,
        evidence={
            "heatmap_points": payload.get("heatmap_points"),
            "heatmap_sample_count": payload.get("heatmap_sample_count"),
            "invalid_heatmap_sample_count": payload.get("invalid_heatmap_sample_count"),
        },
        gap_ids=data.gap_ids_for("liquidation_map"),
        notes=notes,
    )
