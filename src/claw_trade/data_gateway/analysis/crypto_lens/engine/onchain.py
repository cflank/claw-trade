from __future__ import annotations

from typing import Any, Mapping

from ..input_contract import CryptoLensInput
from ..output_contract import AnalysisSection, AnalysisState
from .readiness import state_from_domain_status


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_onchain(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    base_state = state_from_domain_status(data.domain_status["onchain"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="链上域缺失，暂无法补充链上侧解释。",
            evidence={},
            gap_ids=data.gap_ids_for("onchain"),
        )

    netflow = _to_float(payload.get("exchange_netflow"))
    netflow_unit = str(payload.get("exchange_netflow_unit") or "") or None
    netflow_source_field = str(payload.get("exchange_netflow_source_field") or "") or None
    active_addresses = _to_float(payload.get("active_addresses"))
    mvrv = _to_float(payload.get("mvrv"))
    sth_sopr = _to_float(payload.get("sth_sopr"))
    lth_sopr = _to_float(payload.get("lth_sopr"))
    nupl = _to_float(payload.get("nupl"))
    whale_count = _to_float(payload.get("whale_large_tx_count"))
    whale_count_unit = str(payload.get("whale_large_tx_count_unit") or "") or None
    stablecoin_netflow = _to_float(payload.get("stablecoin_exchange_netflow"))
    stablecoin_market_cap = _to_float(payload.get("stablecoin_market_cap"))
    summary = "链上上下文来自归一化链上字段。"
    if netflow is not None:
        summary += f" 交易所净流量={netflow:.2f}{f' {netflow_unit}' if netflow_unit else ''}."
    if active_addresses is not None:
        summary += f" 活跃地址数={active_addresses:.0f}."
    if mvrv is not None:
        summary += f" MVRV={mvrv:.3f}."
    if sth_sopr is not None:
        summary += f" STH-SOPR={sth_sopr:.3f}."
    if lth_sopr is not None:
        summary += f" LTH-SOPR={lth_sopr:.3f}."
    if nupl is not None:
        summary += f" NUPL={nupl:.3f}."
    if whale_count is not None:
        summary += f" 大额转账样本={whale_count:.0f}{f' {whale_count_unit}' if whale_count_unit else ''}."
    if stablecoin_market_cap is not None:
        summary += f" 稳定币市值={stablecoin_market_cap:.2f}."

    return AnalysisSection(
        status=base_state,
        summary=summary,
        evidence={
            "exchange_netflow": netflow,
            "exchange_netflow_unit": netflow_unit,
            "exchange_netflow_source_field": netflow_source_field,
            "active_addresses": active_addresses,
            "mvrv": mvrv,
            "sth_sopr": sth_sopr,
            "lth_sopr": lth_sopr,
            "nupl": nupl,
            "whale_large_tx_count": whale_count,
            "whale_large_tx_count_unit": whale_count_unit,
            "stablecoin_exchange_netflow": stablecoin_netflow,
            "stablecoin_market_cap": stablecoin_market_cap,
        },
        gap_ids=data.gap_ids_for("onchain"),
    )
