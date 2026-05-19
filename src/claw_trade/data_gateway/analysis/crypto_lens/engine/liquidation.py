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
    if isinstance(largest, Mapping):
        cluster_price = _to_float(largest.get("price"))
        cluster_size = _to_float(largest.get("size"))

    summary = "清算上下文基于清算簇聚合结果生成。"
    if cluster_price is not None:
        summary += f" 最大清算簇价格 {cluster_price:.2f}."
    if cluster_size is not None:
        summary += f" 规模 {cluster_size:.2f}."

    status = base_state
    if cluster_price is None and status == AnalysisState.READY:
        status = AnalysisState.PARTIAL

    return AnalysisSection(
        status=status,
        summary=summary,
        evidence={"largest_cluster_price": cluster_price, "largest_cluster_size": cluster_size},
        gap_ids=data.gap_ids_for("liquidation_map"),
    )
