from __future__ import annotations

from typing import Any, Mapping

from ..input_contract import CryptoLensInput
from ..output_contract import AnalysisSection, AnalysisState
from .readiness import state_from_domain_status


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_derivatives(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    base_state = state_from_domain_status(data.domain_status["derivatives"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="衍生品域缺失，资金费率与持仓拥挤度未覆盖。",
            evidence={},
            gap_ids=data.gap_ids_for("derivatives"),
        )

    funding = _to_float(payload.get("funding"))
    oi = _to_float(payload.get("oi"))
    long_short_ratio = _to_float(payload.get("long_short_ratio"))
    cvd = _to_float(payload.get("cvd"))
    cvd_proxy = _to_float(payload.get("cvd_proxy"))
    funding_unit = str(payload.get("funding_unit") or "") or None
    oi_unit = str(payload.get("oi_unit") or "") or None
    funding_source_field = str(payload.get("funding_source_field") or "") or None
    oi_source_field = str(payload.get("oi_source_field") or "") or None
    cvd_source_field = str(payload.get("cvd_source_field") or "") or None
    cvd_source = str(payload.get("cvd_proxy_source") or "") or None
    cvd_unit = str(payload.get("cvd_unit") or payload.get("cvd_proxy_unit") or "") or None
    notes: list[str] = []
    if funding is None:
        notes.append("funding 缺失")
    if oi is None:
        notes.append("oi 缺失")

    summary = "衍生品上下文基于 funding/OI/多空比/CVD 字段生成。"
    if funding is not None:
        summary += f" funding={funding:.6f}{f' {funding_unit}' if funding_unit else ''}."
    if oi is not None:
        summary += f" OI={oi:.2f}{f' {oi_unit}' if oi_unit else ''}."
    if long_short_ratio is not None:
        summary += f" 多空比={long_short_ratio:.3f}."
    if cvd is not None:
        summary += f" CVD={cvd:.2f}{f' {cvd_unit}' if cvd_unit else ''}."
    elif cvd_proxy is not None:
        summary += f" 主动买卖量差代理={cvd_proxy:.2f}{f' {cvd_unit}' if cvd_unit else ''}."

    status = base_state
    if notes and status == AnalysisState.READY:
        status = AnalysisState.PARTIAL

    return AnalysisSection(
        status=status,
        summary=summary,
        evidence={
            "funding": funding,
            "funding_unit": funding_unit,
            "funding_source_field": funding_source_field,
            "oi": oi,
            "oi_unit": oi_unit,
            "oi_source_field": oi_source_field,
            "long_short_ratio": long_short_ratio,
            "cvd": cvd,
            "cvd_unit": cvd_unit if cvd is not None else None,
            "cvd_source_field": cvd_source_field,
            "cvd_proxy": cvd_proxy,
            "cvd_proxy_unit": cvd_unit,
            "cvd_proxy_source": cvd_source,
        },
        gap_ids=data.gap_ids_for("derivatives"),
        notes=tuple(notes),
    )
