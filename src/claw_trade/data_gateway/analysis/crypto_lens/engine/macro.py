from __future__ import annotations

from typing import Any, Mapping

from ..input_contract import CryptoLensInput
from ..output_contract import AnalysisSection, AnalysisState
from .readiness import state_from_domain_status


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_macro(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    base_state = state_from_domain_status(data.domain_status["macro"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="宏观域缺失，宏观约束判断不可用。",
            evidence={},
            gap_ids=data.gap_ids_for("macro"),
        )

    dxy = _to_float(payload.get("dxy"))
    us10y = _to_float(payload.get("us10y"))
    summary = "宏观上下文来自归一化宏观字段。"
    if dxy is not None:
        summary += f" DXY={dxy:.2f}."
    if us10y is not None:
        summary += f" 美债10Y={us10y:.2f}."

    return AnalysisSection(
        status=base_state,
        summary=summary,
        evidence={"dxy": dxy, "us10y": us10y},
        gap_ids=data.gap_ids_for("macro"),
    )
