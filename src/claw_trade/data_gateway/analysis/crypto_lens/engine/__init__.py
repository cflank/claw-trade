from __future__ import annotations

from ..input_contract import CryptoLensInput
from ..output_contract import (
    ENGINE_NAME,
    ENGINE_VERSION,
    AnalysisReadiness,
    AnalysisSection,
    AnalysisState,
    CryptoLensAnalysisResult,
    ReadinessStatus,
)
from claw_trade.data_gateway.models import GapSeverity
from .ahr999 import analyze_ahr999
from .derivatives import analyze_derivatives
from .liquidation import analyze_liquidation
from .macro import analyze_macro
from .onchain import analyze_onchain
from .patterns import enrich_technical_patterns
from .technical import analyze_market_structure, analyze_technical_patterns

_CORE_DOMAIN_KEYS = {"market", "ohlcv"}
_CORE_DATA_TYPES = {"quote_snapshot", "daily_bar", "intraday_bar"}


def analyze_crypto_lens(data: CryptoLensInput, *, engine_source_ref: str = "source_hash:uncomputed") -> CryptoLensAnalysisResult:
    market_structure = analyze_market_structure(data.domains.market, data)
    technical_patterns = enrich_technical_patterns(analyze_technical_patterns(data.domains.ohlcv, data))
    derivatives_context = analyze_derivatives(data.domains.derivatives, data)
    liquidation_context = analyze_liquidation(data.domains.liquidation_map, data)
    onchain_context = analyze_onchain(data.domains.onchain, data)
    macro_context = analyze_macro(data.domains.macro, data)
    ahr999_context = analyze_ahr999(data.domains.ahr999, data)
    return CryptoLensAnalysisResult(
        analysis_id=f"crypto_lens:{data.run_id}:{data.call_id}",
        run_id=data.run_id,
        call_id=data.call_id,
        ticker=data.ticker,
        as_of=data.as_of,
        engine_name=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        engine_source_ref=engine_source_ref,
        input_normalized_refs=data.normalized_refs,
        status=_result_status(
            {
                "market_structure": market_structure,
                "technical_patterns": technical_patterns,
                "derivatives_context": derivatives_context,
                "liquidation_context": liquidation_context,
                "onchain_context": onchain_context,
                "macro_context": macro_context,
                "ahr999_context": ahr999_context,
            }
        ),
        readiness=_readiness(data),
        indicator_coverage=_indicator_coverage(
            {
                "market_structure": market_structure,
                "technical_patterns": technical_patterns,
                "derivatives_context": derivatives_context,
                "liquidation_context": liquidation_context,
                "onchain_context": onchain_context,
                "macro_context": macro_context,
                "ahr999_context": ahr999_context,
            }
        ),
        market_structure=market_structure,
        technical_patterns=technical_patterns,
        derivatives_context=derivatives_context,
        liquidation_context=liquidation_context,
        onchain_context=onchain_context,
        macro_context=macro_context,
        ahr999_context=ahr999_context,
        conditional_trade_framework=_conditional_trade_framework(
            market_structure=market_structure,
            technical_patterns=technical_patterns,
            derivatives_context=derivatives_context,
            liquidation_context=liquidation_context,
        ),
        data_gaps=data.data_gaps,
        conflicts=data.conflicts,
    )


def _result_status(sections: dict[str, AnalysisSection]) -> str:
    core_sections = (sections["market_structure"], sections["technical_patterns"])
    if any(section.status == AnalysisState.GAP for section in core_sections):
        return "insufficient"
    if any(section.status in {AnalysisState.GAP, AnalysisState.PARTIAL} for section in sections.values()):
        return "partial"
    return "ready"


def _readiness(data: CryptoLensInput) -> AnalysisReadiness:
    blocking_gap_ids = tuple(
        gap.gap_id
        for gap in data.data_gaps
        if gap.severity == GapSeverity.BLOCKER and _gap_blocks_core_market_analysis(gap)
    )
    non_blocking_gap_ids = tuple(
        gap.gap_id
        for gap in data.data_gaps
        if gap.severity != GapSeverity.BLOCKER or not _gap_blocks_core_market_analysis(gap)
    )
    missing_domains = tuple(
        domain
        for domain, status in data.domain_status.items()
        if status.value in {"missing", "error", "license_blocked"}
    )
    core_missing_domains = tuple(domain for domain in missing_domains if domain in _CORE_DOMAIN_KEYS)
    if blocking_gap_ids or core_missing_domains:
        status = ReadinessStatus.INSUFFICIENT
    elif (
        non_blocking_gap_ids
        or missing_domains
        or any(status.value in {"partial", "insufficient", "stale", "error", "license_blocked"} for status in data.domain_status.values())
    ):
        status = ReadinessStatus.PARTIAL
    else:
        status = ReadinessStatus.READY
    root_cause = "; ".join(gap.human_readable for gap in data.data_gaps[:3]) or None
    return AnalysisReadiness(
        status=status,
        coverage={domain: status.value for domain, status in data.domain_status.items()},
        required_domains=tuple(data.domain_status),
        missing_domains=missing_domains,
        blocking_gap_ids=blocking_gap_ids,
        non_blocking_gap_ids=non_blocking_gap_ids,
        root_cause=root_cause,
    )


def _gap_blocks_core_market_analysis(gap: object) -> bool:
    data_type = getattr(gap, "data_type", "")
    return str(data_type) in _CORE_DATA_TYPES


def _indicator_coverage(sections: dict[str, AnalysisSection]) -> dict[str, dict[str, object]]:
    return {
        name: {
            "status": section.status.value,
            "gap_ids": section.gap_ids,
            "notes": section.notes,
        }
        for name, section in sections.items()
    }


def _conditional_trade_framework(
    *,
    market_structure: AnalysisSection,
    technical_patterns: AnalysisSection,
    derivatives_context: AnalysisSection,
    liquidation_context: AnalysisSection,
) -> dict[str, object]:
    return {
        "purpose": "market_analyst_conditional_context",
        "evidence_sections": (
            "market_structure",
            "technical_patterns",
            "derivatives_context",
            "liquidation_context",
        ),
        "scenario_inputs": {
            "market_structure_status": market_structure.status.value,
            "technical_status": technical_patterns.status.value,
            "derivatives_status": derivatives_context.status.value,
            "liquidation_status": liquidation_context.status.value,
        },
        "invalidation_gap_ids": tuple(
            dict.fromkeys(
                market_structure.gap_ids
                + technical_patterns.gap_ids
                + derivatives_context.gap_ids
                + liquidation_context.gap_ids
            )
        ),
    }
