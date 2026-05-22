from __future__ import annotations

from claw_trade.data_gateway.models import Market, PackDomain, SourceRole
from claw_trade.data_gateway.providers.defaults import default_provider_config_version, load_default_system_capabilities
from claw_trade.data_gateway.providers.news_source_roles import is_news_search_discovery_provider
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner
from claw_trade.data_gateway.providers.social_source_roles import is_social_search_discovery_provider


def test_default_provider_catalog_covers_all_markets_and_domains() -> None:
    capabilities = load_default_system_capabilities()
    covered = {(item.market, item.domain) for item in capabilities}

    expected = {
        (Market.CN_A, PackDomain.MARKET),
        (Market.CN_A, PackDomain.FUNDAMENTAL),
        (Market.CN_A, PackDomain.NEWS),
        (Market.CN_A, PackDomain.SOCIAL),
        (Market.CN_A, PackDomain.POLICY),
        (Market.CN_A, PackDomain.HOT_MONEY),
        (Market.CN_A, PackDomain.LOCKUP),
        (Market.HK, PackDomain.MARKET),
        (Market.HK, PackDomain.FUNDAMENTAL),
        (Market.HK, PackDomain.NEWS),
        (Market.HK, PackDomain.SOCIAL),
        (Market.US, PackDomain.MARKET),
        (Market.US, PackDomain.FUNDAMENTAL),
        (Market.US, PackDomain.NEWS),
        (Market.US, PackDomain.SOCIAL),
        (Market.CRYPTO, PackDomain.MARKET),
        (Market.CRYPTO, PackDomain.FUNDAMENTAL),
        (Market.CRYPTO, PackDomain.NEWS),
        (Market.CRYPTO, PackDomain.SOCIAL),
    }
    assert expected.issubset(covered)


def test_default_search_providers_are_discovery_only() -> None:
    capabilities = load_default_system_capabilities()

    for item in capabilities:
        hint = f"{item.provider}:{item.adapter_id}:{item.endpoint}"
        if item.domain == PackDomain.NEWS and is_news_search_discovery_provider(hint):
            assert item.source_role == SourceRole.SEARCH_DISCOVERY
        if item.domain == PackDomain.SOCIAL and is_social_search_discovery_provider(hint):
            assert item.source_role == SourceRole.SEARCH_DISCOVERY


def test_run_provider_plan_preserves_hk_stock_hk_daily_params() -> None:
    capabilities = load_default_system_capabilities()
    registry = ProviderRegistry(capabilities=capabilities)
    provider_config_version = default_provider_config_version(capabilities)

    plan = RunProviderPlanner().build_run_plan(
        run_id="run-hk-plan",
        market=Market.HK,
        ticker="00700.HK",
        company_name="腾讯控股",
        currency="HKD",
        profile="HK",
        current_date="2026-05-17",
        start_date="2026-04-17",
        end_date="2026-05-17",
        domains=(PackDomain.MARKET, PackDomain.NEWS, PackDomain.SOCIAL),
        registry=registry,
        provider_config_version=provider_config_version,
    )

    stock_hk_daily = next(spec for spec in plan.call_specs if spec.domain == PackDomain.MARKET and spec.endpoint == "stock_hk_daily")
    assert stock_hk_daily.params["symbol"] == "00700"
    assert stock_hk_daily.params["adjust"] == "qfq"
    assert stock_hk_daily.params["interval"] == "daily"
    assert stock_hk_daily.params["currency"] == "HKD"
    assert stock_hk_daily.params["timezone"] == "Asia/Hong_Kong"
    assert plan.remote_prefetch_allowed is False


def test_run_provider_plan_extends_crypto_ohlcv_for_daily_vegas_purple_band() -> None:
    capabilities = load_default_system_capabilities()
    registry = ProviderRegistry(capabilities=capabilities)
    provider_config_version = default_provider_config_version(capabilities)

    plan = RunProviderPlanner().build_run_plan(
        run_id="run-crypto-plan",
        market=Market.CRYPTO,
        ticker="BTC",
        company_name="Bitcoin",
        currency="USD",
        profile="CRYPTO",
        current_date="2026-05-17",
        start_date="2026-05-01",
        end_date="2026-05-17",
        domains=(PackDomain.MARKET,),
        registry=registry,
        provider_config_version=provider_config_version,
    )

    crypto_price = next(spec for spec in plan.call_specs if spec.domain == PackDomain.MARKET and spec.endpoint == "crypto_price_historical")
    assert crypto_price.params["symbol"] == "BTCUSD"
    assert crypto_price.params["requested_start_date"] == "2026-05-01"
    assert crypto_price.params["start_date"] == "2024-06-11"
    assert crypto_price.params["technical_lookback_reason"] == "vegas_purple_band_ema676_daily"
    assert crypto_price.params["timezone"] == "UTC"


def test_cn_a_market_default_catalog_keeps_required_and_fallback_kline_quote_pairs() -> None:
    capabilities = tuple(
        item
        for item in load_default_system_capabilities()
        if item.domain == PackDomain.MARKET and item.market == Market.CN_A
    )
    by_endpoint = {item.endpoint: item for item in capabilities}

    assert by_endpoint["stock_quote"].provider == "mootdx_quote"
    assert by_endpoint["stock_quote"].required is True
    assert by_endpoint["stock_quote"].coverage_group == "cn_a_market_quote"
    assert by_endpoint["stock_quote"].coverage_quorum == 1

    assert by_endpoint["quote_tencent"].provider == "tencent_quote"
    assert by_endpoint["quote_tencent"].required is False
    assert by_endpoint["quote_tencent"].coverage_group == "cn_a_market_quote"
    assert by_endpoint["quote_tencent"].coverage_quorum == 1

    assert by_endpoint["kline_baidu"].provider == "baidu_kline"
    assert by_endpoint["kline_baidu"].required is True
    assert by_endpoint["kline_baidu"].coverage_group == "cn_a_market_kline"
    assert by_endpoint["kline_baidu"].coverage_quorum == 1

    assert by_endpoint["daily"].provider == "tushare_kline_fallback"
    assert by_endpoint["daily"].required is False
    assert by_endpoint["daily"].coverage_group == "cn_a_market_kline"
    assert by_endpoint["daily"].coverage_quorum == 1


def test_cn_a_market_default_catalog_keeps_orderbook_sources_explicit() -> None:
    capabilities = tuple(
        item
        for item in load_default_system_capabilities()
        if item.domain == PackDomain.MARKET and item.market == Market.CN_A
    )
    by_endpoint = {item.endpoint: item for item in capabilities}

    assert by_endpoint["orderbook"].provider == "mootdx_orderbook"
    assert by_endpoint["orderbook_tencent"].provider == "tencent_orderbook"
    assert "orderbook_eastmoney" not in by_endpoint
    assert "quote" not in by_endpoint
    assert "stock_zh_a_hist" not in by_endpoint


def test_default_fundamental_catalog_keeps_paid_openbb_providers_unconfigured() -> None:
    capabilities = tuple(item for item in load_default_system_capabilities() if item.domain == PackDomain.FUNDAMENTAL)
    provider_ids = {item.provider for item in capabilities}
    adapter_ids = {item.adapter_id for item in capabilities}

    assert "fmp" not in provider_ids
    assert "polygon" not in provider_ids
    assert "intrinio" not in provider_ids
    assert "fundamental.yfinance.us" in adapter_ids
    assert "fundamental.fmp.us" not in adapter_ids


def test_hk_fundamental_catalog_uses_independent_provider_candidates() -> None:
    capabilities = tuple(
        item
        for item in load_default_system_capabilities()
        if item.domain == PackDomain.FUNDAMENTAL and item.market == Market.HK
    )
    adapter_ids = {item.adapter_id for item in capabilities}

    assert {
        "fundamental.tushare.hk",
        "fundamental.akshare.hk",
        "fundamental.akshare.hk.income",
        "fundamental.yfinance.hk",
        "fundamental.hk.official",
    }.issubset(adapter_ids)
    assert "fundamental.hk.financial" not in adapter_ids
