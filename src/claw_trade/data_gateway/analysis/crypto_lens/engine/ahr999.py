from __future__ import annotations

from typing import Any, Mapping

from ..input_contract import CryptoLensInput
from ..output_contract import AnalysisSection, AnalysisState
from .readiness import state_from_domain_status


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_ahr999(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    ticker = data.ticker.upper()
    if not ticker.startswith("BTC"):
        return AnalysisSection(
            status=AnalysisState.NOT_APPLICABLE,
            summary="AHR999 仅适用于 BTC，当前标的不适用。",
            evidence={},
            gap_ids=(),
        )

    base_state = state_from_domain_status(data.domain_status["ahr999"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="BTC 场景下 AHR999 域缺失。",
            evidence={},
            gap_ids=data.gap_ids_for("ahr999"),
        )

    value = _to_float(payload.get("value"))
    fitted_price = _to_float(payload.get("fitted_price"))
    summary = "AHR999 上下文来自归一化 AHR999 字段。"
    if value is not None:
        summary += f" 指标值={value:.4f}."
    if fitted_price is not None:
        summary += f" 拟合价格={fitted_price:.2f}."

    status = base_state
    if value is None and status == AnalysisState.READY:
        status = AnalysisState.PARTIAL

    return AnalysisSection(
        status=status,
        summary=summary,
        evidence={"value": value, "fitted_price": fitted_price},
        gap_ids=data.gap_ids_for("ahr999"),
    )
