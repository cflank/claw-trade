from __future__ import annotations

from claw_trade.data_gateway.analysis.crypto_lens import AnalysisState, CryptoLensInput
from claw_trade.data_gateway.analysis.crypto_lens.engine import analyze_crypto_lens
from claw_trade.data_gateway.models import (
    Conflict,
    CryptoDomainBundle,
    DataGap,
    DataGapReason,
    DomainReadiness,
    FreshnessStatus,
    GapSeverity,
    Market,
    NormalizedCryptoMarketBundle,
    PackDomain,
    ProviderSourceRef,
    ProviderStatus,
    SourceRole,
)


def _full_domains(*, ohlcv_rows: int = 180) -> CryptoDomainBundle:
    candles = [
        {
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 62000.0 + float(i) * 100.0 - 20.0,
            "high": 62000.0 + float(i) * 100.0 + 40.0,
            "low": 62000.0 + float(i) * 100.0 - 40.0,
            "close": 62000.0 + float(i) * 100.0,
            "volume": 1000.0 + float(i),
        }
        for i in range(ohlcv_rows)
    ]
    return CryptoDomainBundle(
        market={"price": 68000.0, "volume_24h": 123456789.0},
        ohlcv={"timeframe": "4h", "rows": ohlcv_rows, "candles": candles, "indicators": {"rsi": 58.0, "macd_hist": 12.3}},
        derivatives={"funding": 0.0003, "oi": 54321.0, "long_short_ratio": 1.08, "cvd_proxy": 1200.0},
        liquidation_map={"largest_cluster": {"price": 67000.0, "size": 12000000.0}},
        onchain={"exchange_netflow": -2500.0, "active_addresses": 812000},
        macro={"dxy": 103.2, "us10y": 4.18},
        events={"upcoming_unlock_days": 12},
        ahr999={"value": 1.12, "fitted_price": 64500.0},
    )


def _full_status() -> dict[str, DomainReadiness]:
    return {
        "market": DomainReadiness.READY,
        "ohlcv": DomainReadiness.READY,
        "derivatives": DomainReadiness.READY,
        "liquidation_map": DomainReadiness.READY,
        "onchain": DomainReadiness.READY,
        "macro": DomainReadiness.READY,
        "events": DomainReadiness.READY,
        "ahr999": DomainReadiness.READY,
    }


def _bundle(
    *,
    ticker: str = "BTC-USD",
    domains: CryptoDomainBundle | None = None,
    domain_status: dict[str, DomainReadiness] | None = None,
    data_gaps: tuple[DataGap, ...] = (),
) -> NormalizedCryptoMarketBundle:
    return NormalizedCryptoMarketBundle(
        run_id="run-crypto-lens",
        call_id="call-crypto-lens",
        ticker=ticker,
        market=Market.CRYPTO,
        quote="USD",
        as_of="2026-05-18T12:00:00+00:00",
        start_date="2026-05-01",
        end_date="2026-05-18",
        freshness=FreshnessStatus.FRESH_REMOTE,
        domains=domains or _full_domains(),
        domain_status=domain_status or _full_status(),
        data_gaps=data_gaps,
        conflicts=(
            Conflict(
                conflict_id="conflict-demo",
                field_path="market.price",
                values=("68000", "67980"),
                provider_refs=("source://a", "source://b"),
                resolution="prefer source a",
                confidence="medium",
            ),
        ),
        source_refs=(
            ProviderSourceRef(
                ref_id="source://openbb-demo",
                provider="openbb_demo",
                adapter_id="openbb.demo",
                endpoint="crypto_bundle",
                source_role=SourceRole.MARKET_DATA,
                status=ProviderStatus.REMOTE_SUCCESS,
                normalized_ref="norm://openbb-demo",
            ),
        ),
        attempt_refs=("attempt-1",),
        raw_refs=("raw://openbb-demo",),
        normalized_refs=("norm://openbb-demo",),
    )


def test_crypto_lens_engine_returns_ready_sections_for_complete_bundle() -> None:
    result = analyze_crypto_lens(CryptoLensInput.from_normalized_bundle(_bundle()))
    assert result.market_structure.status == AnalysisState.READY
    assert result.technical_patterns.status == AnalysisState.READY
    assert result.derivatives_context.status == AnalysisState.READY
    assert result.liquidation_context.status == AnalysisState.READY
    assert result.onchain_context.status == AnalysisState.READY
    assert result.macro_context.status == AnalysisState.READY
    assert result.ahr999_context.status == AnalysisState.READY
    technical = result.technical_patterns
    assert "维加斯通道" in technical.summary
    assert "双线反转" in technical.summary
    assert "FVG" in technical.summary
    assert "KD(9,3,3)" in technical.summary
    assert "TD Sequential" in technical.summary
    assert technical.evidence["vegas"]["ema144"] is not None
    assert technical.evidence["double_line_reversal"]["state"] == "above_lines"
    assert technical.evidence["fvg"]["open_count"] > 0
    assert technical.evidence["kd_9_3_3"]["k"] is not None
    assert technical.evidence["td_sequential"]["signal"] == "bearish_reversal_countdown_13"


def test_crypto_lens_engine_marks_missing_derivatives_as_gap() -> None:
    status = _full_status()
    status["derivatives"] = DomainReadiness.MISSING
    domains = _full_domains()
    domains = CryptoDomainBundle(**{**domains.__dict__, "derivatives": None})
    gap = DataGap(
        gap_id="gap-derivatives",
        domain=PackDomain.MARKET,
        severity=GapSeverity.WARN,
        reason=DataGapReason.FIELD_MISSING,
        field_path="derivatives.funding",
        provider_candidates=("openbb_demo",),
        attempt_ids=("attempt-1",),
        root_cause="derivatives adapter missing funding field",
        next_action="retry with alternate source",
    )
    result = analyze_crypto_lens(CryptoLensInput.from_normalized_bundle(_bundle(domains=domains, domain_status=status, data_gaps=(gap,))))
    assert result.derivatives_context.status == AnalysisState.GAP
    assert result.derivatives_context.gap_ids == ("gap-derivatives",)


def test_crypto_lens_engine_marks_missing_liquidation_as_gap() -> None:
    status = _full_status()
    status["liquidation_map"] = DomainReadiness.MISSING
    domains = _full_domains()
    domains = CryptoDomainBundle(**{**domains.__dict__, "liquidation_map": None})
    gap = DataGap(
        gap_id="gap-liquidation",
        domain=PackDomain.MARKET,
        severity=GapSeverity.WARN,
        reason=DataGapReason.EMPTY,
        field_path="liquidation_map.largest_cluster",
        provider_candidates=("openbb_demo",),
        attempt_ids=("attempt-1",),
        root_cause="liquidation source returned empty",
        next_action="await next refresh",
    )
    result = analyze_crypto_lens(CryptoLensInput.from_normalized_bundle(_bundle(domains=domains, domain_status=status, data_gaps=(gap,))))
    assert result.liquidation_context.status == AnalysisState.GAP
    assert result.liquidation_context.gap_ids == ("gap-liquidation",)


def test_crypto_lens_engine_marks_insufficient_ohlcv_as_gap() -> None:
    status = _full_status()
    status["ohlcv"] = DomainReadiness.INSUFFICIENT
    domains = _full_domains(ohlcv_rows=12)
    gap = DataGap(
        gap_id="gap-ohlcv",
        domain=PackDomain.MARKET,
        severity=GapSeverity.WARN,
        reason=DataGapReason.FIELD_MISSING,
        field_path="ohlcv.rows",
        provider_candidates=("openbb_demo",),
        attempt_ids=("attempt-1",),
        root_cause="insufficient history rows",
        next_action="expand window",
    )
    result = analyze_crypto_lens(CryptoLensInput.from_normalized_bundle(_bundle(domains=domains, domain_status=status, data_gaps=(gap,))))
    assert result.technical_patterns.status == AnalysisState.GAP
    assert "样本不足" in result.technical_patterns.summary
    assert result.technical_patterns.gap_ids == ("gap-ohlcv",)


def test_crypto_lens_engine_sets_ahr999_not_applicable_for_non_btc() -> None:
    status = _full_status()
    status["ahr999"] = DomainReadiness.MISSING
    domains = _full_domains()
    domains = CryptoDomainBundle(**{**domains.__dict__, "ahr999": None})
    result = analyze_crypto_lens(CryptoLensInput.from_normalized_bundle(_bundle(ticker="ETH-USD", domains=domains, domain_status=status)))
    assert result.ahr999_context.status == AnalysisState.NOT_APPLICABLE
    assert "不适用" in result.ahr999_context.summary
