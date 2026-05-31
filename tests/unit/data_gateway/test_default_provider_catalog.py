from __future__ import annotations

from claw_trade.data_gateway.models import HttpVisibility, Market, PackDomain, PrioritySource, ProviderKind, SourceRole, source_role_is_discovery_only
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


def test_default_capabilities_are_keyed_by_market_data_type_source_role_provider() -> None:
    capabilities = load_default_system_capabilities()
    keys = {
        (item.market, item.data_type, item.source_role, item.provider)
        for item in capabilities
    }
    assert len(keys) == len(capabilities)
    assert all(item.data_type for item in capabilities)


def test_discovery_and_event_expectation_cannot_be_formal_fact_or_worker_pack_sources() -> None:
    capabilities = load_default_system_capabilities()
    discovery_caps = [item for item in capabilities if source_role_is_discovery_only(item.source_role)]
    assert discovery_caps
    for item in discovery_caps:
        assert item.can_be_formal_fact_source is False
        assert item.can_enter_worker_pack is False


def test_default_http_visibility_does_not_guess_managed_http() -> None:
    capabilities = load_default_system_capabilities()
    assert capabilities
    for item in capabilities:
        assert item.http_visibility != HttpVisibility.MANAGED_HTTP


def test_mootdx_market_capabilities_are_no_http() -> None:
    capabilities = tuple(
        item
        for item in load_default_system_capabilities()
        if item.market == Market.CN_A and item.domain == PackDomain.MARKET and "mootdx" in f"{item.provider}:{item.adapter_id}:{item.endpoint}"
    )
    assert capabilities
    for item in capabilities:
        assert item.http_visibility == HttpVisibility.NO_HTTP


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
    hk_daily = next(spec for spec in plan.call_specs if spec.domain == PackDomain.MARKET and spec.endpoint == "hk_daily")
    assert hk_daily.adapter_id == "project.hk.market"
    assert hk_daily.params["ticker"] == "00700.HK"
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
        currency="USDT",
        profile="CRYPTO",
        current_date="2026-05-17",
        start_date="2026-05-01",
        end_date="2026-05-17",
        domains=(PackDomain.MARKET,),
        registry=registry,
        provider_config_version=provider_config_version,
    )

    crypto_price = next(spec for spec in plan.call_specs if spec.domain == PackDomain.MARKET and spec.endpoint == "crypto_price_historical")
    assert crypto_price.params["symbol"] == "BTCUSDT"
    assert crypto_price.params["requested_start_date"] == "2026-05-01"
    assert crypto_price.params["start_date"] == "2024-06-11"
    assert crypto_price.params["technical_lookback_reason"] == "vegas_purple_band_ema676_daily"
    assert crypto_price.params["timezone"] == "UTC"


def test_run_provider_plan_parses_slash_crypto_pair_for_provider_symbols() -> None:
    capabilities = load_default_system_capabilities()
    registry = ProviderRegistry(capabilities=capabilities)
    provider_config_version = default_provider_config_version(capabilities)

    plan = RunProviderPlanner().build_run_plan(
        run_id="run-ar-crypto-plan",
        market=Market.CRYPTO,
        ticker="AR/USDT",
        company_name="Arweave",
        currency="USDT",
        profile="CRYPTO",
        current_date="2026-05-17",
        start_date="2026-05-01",
        end_date="2026-05-17",
        domains=(PackDomain.MARKET, PackDomain.FUNDAMENTAL, PackDomain.NEWS, PackDomain.SOCIAL),
        registry=registry,
        provider_config_version=provider_config_version,
    )

    crypto_price = next(spec for spec in plan.call_specs if spec.domain == PackDomain.MARKET and spec.endpoint == "crypto_price_historical")
    derivatives = next(spec for spec in plan.call_specs if spec.domain == PackDomain.MARKET and spec.endpoint == "futures_oi_funding")
    coingecko = next(spec for spec in plan.call_specs if spec.domain == PackDomain.FUNDAMENTAL and spec.provider == "coingecko")

    assert crypto_price.params["ticker"] == "AR/USDT"
    assert crypto_price.params["symbol"] == "ARUSDT"
    assert derivatives.params["symbol"] == "ARUSDT"
    assert coingecko.params["ticker"] == "AR/USDT"


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

    assert by_endpoint["stock_zh_a_spot_em"].provider == "akshare_spot"
    assert by_endpoint["stock_zh_a_spot_em"].coverage_group == "cn_a_market_quote"

    assert by_endpoint["kline_baidu"].provider == "baidu_kline"
    assert by_endpoint["kline_baidu"].required is True
    assert by_endpoint["kline_baidu"].coverage_group == "cn_a_market_kline"
    assert by_endpoint["kline_baidu"].coverage_quorum == 1

    assert by_endpoint["daily"].provider == "tushare_kline_fallback"
    assert by_endpoint["daily"].required is False
    assert by_endpoint["daily"].coverage_group == "cn_a_market_kline"
    assert by_endpoint["daily"].coverage_quorum == 1

    assert by_endpoint["stock_zh_a_hist"].provider == "akshare_kline"
    assert by_endpoint["stock_zh_a_hist"].required is False
    assert by_endpoint["stock_zh_a_hist"].coverage_group == "cn_a_market_kline"
    assert by_endpoint["stock_zh_a_hist"].coverage_quorum == 1

    assert by_endpoint["stock_zh_a_hist_min_em"].provider == "akshare_minute"
    assert by_endpoint["stock_zh_a_hist_min_em"].required is False
    assert by_endpoint["stock_zh_a_hist_min_em"].coverage_group is None

    assert by_endpoint["stock_board_industry_name_em"].provider == "akshare_board_industry"
    assert by_endpoint["stock_board_industry_name_em"].coverage_fields == ("板块名称", "板块代码")
    assert by_endpoint["stock_board_concept_name_em"].provider == "akshare_board_concept"
    assert by_endpoint["stock_board_concept_name_em"].coverage_fields == ("板块名称", "板块代码")


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


def test_default_fundamental_catalog_keeps_paid_openbb_providers_unconfigured() -> None:
    capabilities = tuple(item for item in load_default_system_capabilities() if item.domain == PackDomain.FUNDAMENTAL)
    provider_ids = {item.provider for item in capabilities}
    adapter_ids = {item.adapter_id for item in capabilities}

    assert "fmp" not in provider_ids
    assert "polygon" not in provider_ids
    assert "intrinio" not in provider_ids
    assert "fundamental.yfinance.us" in adapter_ids
    assert "fundamental.fmp.us" not in adapter_ids


def test_us_hk_global_stock_data_market_sources_are_openbb_framework_candidates() -> None:
    capabilities = tuple(item for item in load_default_system_capabilities() if item.domain == PackDomain.MARKET)
    by_market_endpoint = {(item.market, item.endpoint): item for item in capabilities}

    us_yfinance = by_market_endpoint[(Market.US, "equity_price_historical")]
    assert us_yfinance.adapter_id == "project.us.market"
    assert us_yfinance.provider == "openbb_yfinance"
    assert us_yfinance.provider_kind == ProviderKind.OPENBB_NATIVE
    assert us_yfinance.priority == 0

    hk_akshare = by_market_endpoint[(Market.HK, "stock_hk_daily")]
    assert hk_akshare.adapter_id == "project.hk.market"
    assert hk_akshare.provider == "akshare_hk"
    assert hk_akshare.priority == 1

    hk_quote = by_market_endpoint[(Market.HK, "quote")]
    assert hk_quote.adapter_id == "project.hk.market"
    assert hk_quote.provider == "eastmoney_hk"


def test_us_hk_global_stock_data_fundamental_sources_are_prioritized() -> None:
    capabilities = tuple(item for item in load_default_system_capabilities() if item.domain == PackDomain.FUNDAMENTAL)
    by_adapter = {item.adapter_id: item for item in capabilities}

    assert by_adapter["fundamental.global_yahoo.us"].provider == "yahoo_quote_summary"
    assert by_adapter["fundamental.global_yahoo.us"].provider_kind == ProviderKind.PROJECT_EXTENSION
    assert by_adapter["fundamental.global_yahoo.us"].coverage_group == "us_fundamental_core"
    assert by_adapter["fundamental.global_yahoo.us"].priority < by_adapter["fundamental.yfinance.us"].priority

    assert by_adapter["fundamental.global_yahoo.hk"].provider == "yahoo_quote_summary"
    assert by_adapter["fundamental.global_yahoo.hk"].provider_kind == ProviderKind.PROJECT_EXTENSION
    assert by_adapter["fundamental.global_yahoo.hk"].coverage_group == "hk_fundamental_core"
    assert "fundamental.yfinance.hk" not in by_adapter


def test_hk_fundamental_catalog_uses_independent_provider_candidates() -> None:
    capabilities = tuple(
        item
        for item in load_default_system_capabilities()
        if item.domain == PackDomain.FUNDAMENTAL and item.market == Market.HK
    )
    adapter_ids = {item.adapter_id for item in capabilities}

    assert {
        "fundamental.global_yahoo.hk",
        "fundamental.tushare.hk",
        "fundamental.akshare.hk",
        "fundamental.akshare.hk.income",
        "fundamental.hk.official",
    }.issubset(adapter_ids)
    assert "fundamental.yfinance.hk" not in adapter_ids
    assert "fundamental.hk.financial" not in adapter_ids


def test_cn_a_fundamental_catalog_includes_tushare_as_paid_candidate() -> None:
    capabilities = tuple(
        item
        for item in load_default_system_capabilities()
        if item.domain == PackDomain.FUNDAMENTAL and item.market == Market.CN_A
    )
    by_adapter = {item.adapter_id: item for item in capabilities}

    tushare = by_adapter["fundamental.tushare.cn_a"]
    assert tushare.provider == "tushare"
    assert tushare.required is False
    assert tushare.credential_requirements == ("TUSHARE_TOKEN",)
    assert tushare.coverage_group == "cn_a_fundamental_financials"


def test_cn_a_akshare_catalog_includes_news_announcements_and_flow_candidates() -> None:
    capabilities = load_default_system_capabilities()
    by_adapter = {item.adapter_id: item for item in capabilities}

    assert by_adapter["news.akshare_company.cn_a"].provider == "akshare_stock_news"
    assert by_adapter["news.akshare_company.cn_a"].coverage_group == "cn_a_news_company"
    assert by_adapter["news.akshare_cninfo.cn_a"].provider == "akshare_cninfo"
    assert by_adapter["news.akshare_cninfo.cn_a"].coverage_group == "cn_a_news_announcement"

    assert by_adapter["hot_money.akshare.individual_fund_flow.cn_a"].coverage_group == "cn_a_hot_money_fund_flow"
    assert by_adapter["hot_money.akshare.individual_fund_flow_rank.cn_a"].coverage_group == "cn_a_hot_money_fund_flow_rank"
    assert by_adapter["hot_money.akshare.sector_fund_flow_industry.cn_a"].coverage_group == "cn_a_hot_money_sector_flow"
    assert by_adapter["hot_money.akshare.sector_fund_flow_concept.cn_a"].coverage_group == "cn_a_hot_money_sector_flow"


def test_tushare_runtime_env_marks_report_capabilities_user_preferred() -> None:
    capabilities = load_default_system_capabilities(env={"TUSHARE_TOKEN": "token"})
    by_adapter = {item.adapter_id: item for item in capabilities}

    assert by_adapter["fundamental.tushare.cn_a"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["project.cn_a.market.tushare_fallback"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["hot_money.tushare.moneyflow.cn_a"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["hot_money.tushare.moneyflow_ths.cn_a"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["hot_money.tushare.moneyflow_hsgt.cn_a"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["hot_money.tushare.moneyflow_ind_dc.cn_a"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["hot_money.tushare.moneyflow_ind_ths.cn_a"].priority_source == PrioritySource.USER_PREFERRED
    assert by_adapter["lockup.tushare.flow120d.cn_a"].priority_source == PrioritySource.USER_PREFERRED
