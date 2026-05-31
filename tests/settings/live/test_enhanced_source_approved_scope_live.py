from __future__ import annotations

from claw_trade.data_gateway.models import Market, PackDomain
from claw_trade.data_gateway.providers.defaults import (
    build_default_provider_registry,
    default_provider_config_version,
    load_default_system_capabilities,
)
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner, report_run_plan_domains

FIXED_SUPPORTED_SOURCES: tuple[str, ...] = (
    "tushare",
    "akshare",
    "alpha_vantage",
    "fmp",
    "polygon",
    "finnhub",
    "tiingo",
    "nasdaq_data_link",
    "coingecko",
    "coinmarketcap",
    "binance",
    "okx",
)

_SOURCE_PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "tushare": ("tushare",),
    "akshare": ("akshare",),
    "alpha_vantage": ("alpha_vantage",),
    "fmp": ("fmp",),
    "polygon": ("polygon",),
    "finnhub": ("finnhub",),
    "tiingo": ("tiingo",),
    "nasdaq_data_link": ("nasdaq_data_link",),
    "coingecko": ("coingecko",),
    "coinmarketcap": ("coinmarketcap",),
    "binance": ("binance",),
    "okx": ("okx",),
}


def _scope_key(*, market: Market, domain: PackDomain, adapter_id: str, endpoint: str, coverage_group: str | None) -> tuple[str, str, str, str, str]:
    return (
        market.value,
        domain.value,
        adapter_id,
        endpoint,
        coverage_group or "",
    )


def _match_fixed_source(provider: str) -> str | None:
    for source, prefixes in _SOURCE_PROVIDER_PREFIXES.items():
        if any(provider.startswith(prefix) for prefix in prefixes):
            return source
    return None


def test_live_enhanced_source_attempt_scope_is_only_from_catalog_run_plan_coverage_metadata() -> None:
    capabilities = load_default_system_capabilities()
    approved_scope_by_source: dict[str, set[tuple[str, str, str, str, str]]] = {
        source: set() for source in FIXED_SUPPORTED_SOURCES
    }
    for item in capabilities:
        source = _match_fixed_source(item.provider)
        if source is None:
            continue
        approved_scope_by_source[source].add(
            _scope_key(
                market=item.market,
                domain=item.domain,
                adapter_id=item.adapter_id,
                endpoint=item.endpoint,
                coverage_group=item.coverage_group,
            )
        )

    assert set(approved_scope_by_source) == set(FIXED_SUPPORTED_SOURCES)
    assert any(approved_scope_by_source.values()), "approved scope evidence missing: no fixed-source entries in provider capability catalog"

    registry = build_default_provider_registry()
    provider_config_version = default_provider_config_version(capabilities)
    planner = RunProviderPlanner()
    observed_scope_by_source_market: dict[tuple[str, Market], set[tuple[str, str, str, str, str]]] = {
        (source, market): set()
        for source in FIXED_SUPPORTED_SOURCES
        for market in (Market.CN_A, Market.HK, Market.US, Market.CRYPTO)
    }
    observed_scope_by_source: dict[str, set[tuple[str, str, str, str, str]]] = {
        source: set() for source in FIXED_SUPPORTED_SOURCES
    }

    for market, ticker, company, currency, profile in (
        (Market.CN_A, "600519.SH", "贵州茅台", "CNY", "CN_A"),
        (Market.HK, "00700.HK", "腾讯控股", "HKD", "HK"),
        (Market.US, "AAPL", "Apple", "USD", "US"),
        (Market.CRYPTO, "BTC", "Bitcoin", "USD", "CRYPTO"),
    ):
        plan = planner.build_run_plan(
            run_id=f"s05-live-approved-scope-{market.value}",
            market=market,
            ticker=ticker,
            company_name=company,
            currency=currency,
            profile=profile,
            current_date="2026-05-23",
            start_date="2026-05-01",
            end_date="2026-05-23",
            domains=report_run_plan_domains(market),
            registry=registry,
            provider_config_version=provider_config_version,
        )
        for spec in plan.call_specs:
            source = _match_fixed_source(spec.provider)
            if source is None:
                continue
            key = _scope_key(
                market=spec.market,
                domain=spec.domain,
                adapter_id=spec.adapter_id,
                endpoint=spec.endpoint,
                coverage_group=spec.coverage_group,
            )
            assert key in approved_scope_by_source[source]
            observed_scope_by_source[source].add(key)
            observed_scope_by_source_market[(source, market)].add(key)

    for source in FIXED_SUPPORTED_SOURCES:
        if approved_scope_by_source[source]:
            assert observed_scope_by_source[source].issubset(approved_scope_by_source[source])
            continue
        assert not observed_scope_by_source[source], (
            f"fixed source {source} has no system capability metadata but appears in run plan attempts"
        )

    for source in FIXED_SUPPORTED_SOURCES:
        approved_markets = {item[0] for item in approved_scope_by_source[source]}
        for market in (Market.CN_A, Market.HK, Market.US, Market.CRYPTO):
            if market.value in approved_markets:
                continue
            assert not observed_scope_by_source_market[(source, market)], (
                f"fixed source {source} attempted in {market.value} without approved market coverage metadata"
            )
