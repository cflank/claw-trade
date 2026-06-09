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
            summary="清算图域缺失，无法识别主要清算簇。",
            evidence={},
            gap_ids=data.gap_ids_for("liquidation_map"),
        )

    largest = payload.get("largest_cluster")
    cluster_price: float | None = None
    cluster_size: float | None = None
    cluster_price_unit: str | None = None
    cluster_size_unit: str | None = None
    cluster_side: object | None = None
    if isinstance(largest, Mapping):
        cluster_price = _to_float(largest.get("price"))
        cluster_size = _to_float(largest.get("size"))
        cluster_price_unit = str(largest.get("price_unit") or "") or None
        cluster_size_unit = str(largest.get("size_unit") or "") or None
        cluster_side = largest.get("side")
    long_total = _to_float(payload.get("long_liquidation_total"))
    short_total = _to_float(payload.get("short_liquidation_total"))
    total = _to_float(payload.get("liquidation_value_total"))
    invalid_heatmap_count = _to_float(payload.get("invalid_heatmap_sample_count"))

    summary = "清算上下文基于统一数据层清算数据生成。"
    if cluster_price is not None:
        summary += f" 最大清算簇价格 {cluster_price:.2f}{f' {cluster_price_unit}' if cluster_price_unit else ''}."
    if cluster_size is not None:
        summary += f" 规模 {cluster_size:.2f}{f' {cluster_size_unit}' if cluster_size_unit else ''}."
    if total is not None:
        summary += f" 聚合清算额 {total:.2f}."
    if long_total is not None or short_total is not None:
        summary += f" 多头清算 {long_total or 0.0:.2f}，空头清算 {short_total or 0.0:.2f}."
    if invalid_heatmap_count:
        summary += f" 已剔除异常热力图价格点 {invalid_heatmap_count:.0f} 个."

    status = base_state
    if cluster_price is None and total is None and status == AnalysisState.READY:
        status = AnalysisState.PARTIAL
    notes: tuple[str, ...] = ()
    if cluster_price is None and total is not None:
        if invalid_heatmap_count:
            notes = ("清算热力图返回过价格点，但价格量级与当前价格不匹配，未作为最大簇使用。",)
        else:
            notes = ("当前只有清算历史聚合，未取得清算热力图簇价格。",)

    return AnalysisSection(
        status=status,
        summary=summary,
        evidence={
            "largest_cluster_price": cluster_price,
            "largest_cluster_size": cluster_size,
            "largest_cluster_price_unit": cluster_price_unit,
            "largest_cluster_size_unit": cluster_size_unit,
            "largest_cluster_side": cluster_side,
            "long_liquidation_total": long_total,
            "short_liquidation_total": short_total,
            "liquidation_value_total": total,
            "sample_count": payload.get("sample_count"),
            "heatmap_sample_count": payload.get("heatmap_sample_count"),
            "invalid_heatmap_sample_count": payload.get("invalid_heatmap_sample_count"),
        },
        gap_ids=data.gap_ids_for("liquidation_map"),
        notes=notes,
    )
