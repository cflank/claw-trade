from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from claw_trade.data_gateway import _selection_batch as selection_batch_bridge
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.reports import data_pack_bridge as report_data_pack_bridge


@dataclass(frozen=True)
class BatchPolicy:
    supports_batch: bool
    batch_by: str
    max_symbols_per_call: int | None = None
    max_days_per_call: int | None = None
    mergeable_fields: tuple[str, ...] = ()
    pagination_policy: object = "none"
    split_policy: object = "strict"


@dataclass(frozen=True)
class CredentialPolicy:
    credential_required: bool
    credential_names: tuple[str, ...]
    credential_scope: str | None
    missing_behavior: str


@dataclass(frozen=True)
class LicensePolicy:
    raw_storage_mode: str
    normalized_storage_allowed: bool
    redistribution_allowed: bool
    retention_days: int | None


@dataclass(frozen=True)
class EndpointCapability:
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    supported_granularities: tuple[str, ...]
    coverage_fields: tuple[str, ...]
    freshness_supported: tuple[str, ...]
    http_visibility: str
    batch_policy: BatchPolicy
    priority_rank: int | None = None
    rate_limit_policy: object | None = None
    license_policy: LicensePolicy | None = None
    can_be_formal_fact_source: bool | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    plugin_version: str
    endpoints: tuple[EndpointCapability, ...]
    credentials: CredentialPolicy
    license_policy: LicensePolicy
    default_rate_limit_policy: object
    default_priority_rank: int = 100


class FakePlugin:
    def __init__(self, capabilities: ProviderCapabilities) -> None:
        self._capabilities = capabilities
        self.plugin_id = capabilities.provider_id
        self.version = capabilities.plugin_version

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: object) -> tuple[object, ...]:
        del batch
        return ()

    def fetch(self, task: object, ctx: object) -> object:
        del task
        del ctx
        raise NotImplementedError


class FakeQueryPlan:
    def __init__(self, request: object) -> None:
        self._request = request

    def request_for_gap(self, gap: object) -> object:
        del gap
        return self._request


class FakeCredentialResolver:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get_credential(self, name: str) -> str | None:
        return self._values.get(name)


def test_cn_a_daily_bar_prefers_tushare_when_token_is_configured() -> None:
    selector = ProviderSelector(
        build_minimal_provider_registry(),
        credential_resolver=FakeCredentialResolver({"data_source:tushare": "ts-token"}),
    )
    candidates = selector.select_candidates(
        (SimpleNamespace(request_id="gap-cn-a-daily"),),
        FakeQueryPlan(_cn_a_daily_bar_request()),
    )

    ordered = [(item.provider_id, item.endpoint_id) for item in candidates]
    assert ordered[:1] == [
        ("cn_a_primary", "daily_bar"),
    ]
    assert ("cn_a_primary", "daily_bar_by_trade_date") not in ordered
    assert ordered[1:4] == [
        ("cn_a_akshare_social_news", "stock_zh_a_hist"),
        ("cn_a_baostock_market", "daily_bar"),
        ("cn_a_eastmoney_market_data", "daily_bar"),
    ]


def test_cn_a_daily_bar_allows_trade_date_endpoint_for_universe_refresh() -> None:
    selector = ProviderSelector(
        build_minimal_provider_registry(),
        credential_resolver=FakeCredentialResolver({"data_source:tushare": "ts-token"}),
    )
    request = _cn_a_daily_bar_request()
    request.universe_ref = "all_a_shares"
    request.symbol_id = None
    candidates = selector.select_candidates(
        (SimpleNamespace(request_id="gap-cn-a-universe-daily"),),
        FakeQueryPlan(request),
    )

    ordered = [(item.provider_id, item.endpoint_id) for item in candidates]
    assert ("cn_a_primary", "daily_bar_by_trade_date") in ordered


def test_cn_a_daily_bar_falls_back_to_public_sources_when_tushare_token_missing() -> None:
    selector = ProviderSelector(
        build_minimal_provider_registry(),
        credential_resolver=FakeCredentialResolver({}),
    )
    candidates = selector.select_candidates(
        (SimpleNamespace(request_id="gap-cn-a-daily"),),
        FakeQueryPlan(_cn_a_daily_bar_request()),
    )

    ordered = [(item.provider_id, item.endpoint_id) for item in candidates]
    assert ("cn_a_primary", "daily_bar") not in ordered
    assert ordered[:3] == [
        ("cn_a_akshare_social_news", "stock_zh_a_hist"),
        ("cn_a_baostock_market", "daily_bar"),
        ("cn_a_eastmoney_market_data", "daily_bar"),
    ]


def test_report_prefetch_skips_mootdx_public_provider() -> None:
    selector = ProviderSelector(
        build_minimal_provider_registry(),
        credential_resolver=FakeCredentialResolver({}),
    )
    request = _cn_a_realtime_quote_request(consumer="report")
    candidates = selector.select_candidates(
        (SimpleNamespace(request_id="gap-cn-a-quote", required_level="required"),),
        FakeQueryPlan(request),
    )

    ordered = [(item.provider_id, item.endpoint_id) for item in candidates]
    assert ("cn_a_mootdx_market", "quote_snapshot") not in ordered


def test_non_report_requests_can_still_use_mootdx_public_provider() -> None:
    selector = ProviderSelector(
        build_minimal_provider_registry(),
        credential_resolver=FakeCredentialResolver({}),
    )
    request = _cn_a_realtime_quote_request(consumer="maintenance")
    candidates = selector.select_candidates(
        (SimpleNamespace(request_id="gap-cn-a-quote", required_level="required"),),
        FakeQueryPlan(request),
    )

    ordered = [(item.provider_id, item.endpoint_id) for item in candidates]
    assert ("cn_a_mootdx_market", "quote_snapshot") in ordered


def _cn_a_daily_bar_request() -> SimpleNamespace:
    return SimpleNamespace(
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        fields=("date", "open", "high", "low", "close", "volume", "amount"),
        symbol_id="600519.SH",
    )


def _cn_a_realtime_quote_request(*, consumer: str) -> SimpleNamespace:
    return SimpleNamespace(
        market="CN_A",
        data_type="quote_snapshot",
        granularity="realtime",
        fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
        symbol_id="600519.SH",
        consumer=consumer,
    )


_IMPORTANT_FIELD_FILTER_SOURCE_ROLES = frozenset({"official", "paid_data"})
_SYMBOL_BY_MARKET = {
    "CN_A": "600519.SH",
    "US": "AAPL",
    "HK": "00700.HK",
    "CRYPTO": "BTCUSDT",
}

_INTENTIONAL_PRODUCT_REQUEST_FIELD_EXCLUSIONS = {
    (
        "report",
        "US",
        "market",
        "quote_snapshot",
        "realtime",
        ("price", "change", "change_pct", "volume", "timestamp", "symbol_id"),
        "us_finnhub_data",
        "quote",
        ("volume",),
    ): "Finnhub quote does not expose volume in the normalized quote row.",
    (
        "report",
        "US",
        "fundamental",
        "financial_metric",
        "quarterly",
        ("roe", "gross_margin", "profit_margin", "eps", "revenue_growth"),
        "us_finnhub_data",
        "stock_metric_financial",
        ("gross_margin", "revenue_growth"),
    ): "Finnhub stock metric exposes ROE/ROA/profit margin/EPS, not gross margin or revenue growth.",
    (
        "report",
        "US",
        "fundamental",
        "financial_metric",
        "quarterly",
        ("roe", "roa", "profit_margin", "eps"),
        "us_alpha_vantage_data",
        "overview_financial_metric",
        ("roa",),
    ): "Alpha Vantage overview exposes gross margin/revenue growth, not ROA.",
    (
        "report",
        "HK",
        "fundamental",
        "financial_metric",
        "quarterly",
        ("roe", "gross_margin", "eps"),
        "hk_finnhub_data",
        "stock_metric_financial",
        ("gross_margin",),
    ): "Finnhub stock metric does not expose gross margin.",
    (
        "report",
        "HK",
        "fundamental",
        "financial_metric",
        "quarterly",
        ("roe", "gross_margin", "eps"),
        "hk_tushare",
        "hk_fina_indicator",
        ("gross_margin",),
    ): "HK Tushare indicator exposes gross profit, not gross margin.",
    (
        "report",
        "HK",
        "fundamental",
        "financial_metric",
        "quarterly",
        ("roe", "eps", "gross_profit"),
        "hk_finnhub_data",
        "stock_metric_financial",
        ("gross_profit",),
    ): "Finnhub stock metric does not expose gross profit.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "realtime",
        ("open_interest", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_coin_netflow",
        ("open_interest",),
    ): "Coinglass netflow and open-interest are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("funding_rate", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_long_short_ratio",
        ("funding_rate",),
    ): "Coinglass funding rate and long/short ratio are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("funding_rate", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_liquidation",
        ("funding_rate",),
    ): "Coinglass funding rate and liquidation are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("funding_rate", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_taker_buy_sell",
        ("funding_rate",),
    ): "Coinglass funding rate and taker buy/sell are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("long_short_ratio", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_funding_rate",
        ("long_short_ratio",),
    ): "Coinglass long/short ratio and funding rate are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("long_short_ratio", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_liquidation",
        ("long_short_ratio",),
    ): "Coinglass long/short ratio and liquidation are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("long_short_ratio", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_taker_buy_sell",
        ("long_short_ratio",),
    ): "Coinglass long/short ratio and taker buy/sell are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_funding_rate",
        ("taker_buy_sell_ratio", "taker_buy_volume", "taker_sell_volume"),
    ): "Coinglass taker buy/sell and funding rate are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_long_short_ratio",
        ("taker_buy_sell_ratio", "taker_buy_volume", "taker_sell_volume"),
    ): "Coinglass taker buy/sell and long/short ratio are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_liquidation",
        ("taker_buy_sell_ratio", "taker_buy_volume", "taker_sell_volume"),
    ): "Coinglass taker buy/sell and liquidation are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_funding_rate",
        ("liquidation_value", "long_liquidation", "short_liquidation"),
    ): "Coinglass liquidation and funding rate are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_long_short_ratio",
        ("liquidation_value", "long_liquidation", "short_liquidation"),
    ): "Coinglass liquidation and long/short ratio are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "1h",
        ("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_taker_buy_sell",
        ("liquidation_value", "long_liquidation", "short_liquidation"),
    ): "Coinglass liquidation and taker buy/sell are separate endpoints.",
    (
        "report",
        "CRYPTO",
        "market",
        "crypto_derivative_metric",
        "realtime",
        ("net_inflow", "timestamp", "symbol_id"),
        "crypto_coinglass_derivatives",
        "futures_open_interest",
        ("net_inflow",),
    ): "Coinglass netflow and open-interest are separate endpoints.",
}


def _product_request_specs() -> tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...]:
    specs: list[tuple[str, str, str, str, str, tuple[str, ...]]] = []
    for market, domain_specs in (
        ("CN_A", report_data_pack_bridge._CN_A_DOMAIN_DATASETS),
        ("US", report_data_pack_bridge._US_DOMAIN_DATASETS),
        ("HK", report_data_pack_bridge._HK_DOMAIN_DATASETS),
        ("CRYPTO", report_data_pack_bridge._CRYPTO_DOMAIN_DATASETS),
    ):
        for domain, datasets in domain_specs.items():
            for data_type, granularity, fields in datasets:
                specs.append(("report", market, domain, data_type, granularity, fields))
    for data_type, granularity, fields in selection_batch_bridge._SELECTION_REQUESTS:
        specs.append(("select", "CN_A", "selection", data_type, granularity, fields))
    return tuple(specs)


def _request(*, market: str, data_type: str, granularity: str, fields: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        market=market,
        data_type=data_type,
        granularity=granularity,
        fields=fields,
        source_role_required=None,
        symbol_id=_SYMBOL_BY_MARKET[market],
        universe_ref=None,
        exchange=None,
        currency=None,
        timezone=None,
        calendar=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )


def _plugin(
    provider_id: str,
    source_role: str,
    priority_rank: int,
    *,
    credential_required: bool = False,
    credential_names: tuple[str, ...] = (),
) -> FakePlugin:
    caps = ProviderCapabilities(
        provider_id=provider_id,
        plugin_version="1.0.0",
        endpoints=(
            EndpointCapability(
                endpoint_id="daily",
                market="US",
                data_type="daily_bar",
                source_role=source_role,
                supported_granularities=("daily",),
                coverage_fields=("close", "volume"),
                freshness_supported=("trading_day",),
                http_visibility="managed_http",
                priority_rank=priority_rank,
                batch_policy=BatchPolicy(
                    supports_batch=True,
                    batch_by="symbol",
                    max_symbols_per_call=20,
                    mergeable_fields=("close", "volume"),
                ),
            ),
        ),
        credentials=CredentialPolicy(
            credential_required=credential_required,
            credential_names=credential_names,
            credential_scope="user" if credential_required else None,
            missing_behavior="credential_missing",
        ),
        license_policy=LicensePolicy(
            raw_storage_mode="metadata_only",
            normalized_storage_allowed=True,
            redistribution_allowed=False,
            retention_days=30,
        ),
        default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
    )
    return FakePlugin(caps)


def test_selector_orders_official_source_first_without_dropping_later_candidates() -> None:
    registry = ProviderRegistry()
    registry.register(_plugin("official_feed", "official", 10))
    registry.register(_plugin("search_feed", "discovery", 1))

    request = SimpleNamespace(
        market="US",
        data_type="daily_bar",
        granularity="daily",
        fields=("close",),
        source_role_required=None,
        symbol_id="AAPL",
        universe_ref=None,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        calendar="US_NYSE_NASDAQ",
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-1", symbol_id="AAPL", required_level="required")

    selector = ProviderSelector(registry)
    candidates = selector.select_candidates((gap,), FakeQueryPlan(request))

    assert [getattr(candidate, "provider_id") for candidate in candidates] == ["official_feed", "search_feed"]
    assert getattr(candidates[0], "currency") == "USD"
    assert getattr(candidates[0], "timezone") == "America/New_York"


def test_selector_filters_by_required_source_role_and_fields() -> None:
    registry = ProviderRegistry()
    registry.register(_plugin("sentiment_feed", "sentiment", 5))
    registry.register(_plugin("official_feed", "official", 1))

    request = SimpleNamespace(
        market="US",
        data_type="daily_bar",
        granularity="daily",
        fields=("close", "volume"),
        source_role_required="sentiment",
        symbol_id="AAPL",
        universe_ref=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-2", symbol_id="AAPL", required_level="required")

    selector = ProviderSelector(registry)
    candidates = selector.select_candidates((gap,), FakeQueryPlan(request))

    assert [getattr(candidate, "provider_id") for candidate in candidates] == ["sentiment_feed"]


def test_selector_skips_credential_required_provider_without_configured_api() -> None:
    registry = ProviderRegistry()
    registry.register(
        _plugin(
            "paid_feed",
            "paid_data",
            1,
            credential_required=True,
            credential_names=("data_source:tushare",),
        )
    )
    registry.register(_plugin("public_feed", "built_in_public", 10))
    request = SimpleNamespace(
        market="US",
        data_type="daily_bar",
        granularity="daily",
        fields=("close", "volume"),
        source_role_required=None,
        symbol_id="AAPL",
        universe_ref=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-credential", symbol_id="AAPL", required_level="required")

    missing_selector = ProviderSelector(registry, credential_resolver=FakeCredentialResolver({}))
    missing_candidates = missing_selector.select_candidates((gap,), FakeQueryPlan(request))

    assert [getattr(candidate, "provider_id") for candidate in missing_candidates] == ["public_feed"]

    configured_selector = ProviderSelector(
        registry,
        credential_resolver=FakeCredentialResolver({"data_source:tushare": "token"}),
    )
    configured_candidates = configured_selector.select_candidates((gap,), FakeQueryPlan(request))

    assert [getattr(candidate, "provider_id") for candidate in configured_candidates] == [
        "paid_feed",
        "public_feed",
    ]


def test_selector_with_minimal_plugins_respects_market_boundary() -> None:
    registry = build_minimal_provider_registry()
    request = SimpleNamespace(
        market="US",
        data_type="daily_bar",
        granularity="daily",
        fields=("open", "high", "low", "close", "volume"),
        source_role_required=None,
        symbol_id="AAPL",
        universe_ref=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-3", symbol_id="AAPL", required_level="required")

    selector = ProviderSelector(registry)
    candidates = selector.select_candidates((gap,), FakeQueryPlan(request))

    assert [getattr(candidate, "provider_id") for candidate in candidates] == ["us_primary", "us_yahoo_finance"]


def test_selector_with_cn_a_selection_daily_fields_keeps_tushare_first() -> None:
    registry = build_minimal_provider_registry()
    request = SimpleNamespace(
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        fields=("date", "open", "high", "low", "close", "volume", "amount"),
        source_role_required=None,
        symbol_id="600519.SH",
        universe_ref=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-cn-a-selection-daily", symbol_id="600519.SH", required_level="required")

    selector = ProviderSelector(registry)
    candidates = selector.select_candidates((gap,), FakeQueryPlan(request))

    assert [getattr(candidate, "provider_id") for candidate in candidates][0] == "cn_a_primary"


def test_selector_with_cn_a_selection_daily_uses_public_order_when_tushare_api_missing() -> None:
    registry = build_minimal_provider_registry()
    request = SimpleNamespace(
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        fields=("date", "open", "high", "low", "close", "volume", "amount"),
        source_role_required=None,
        symbol_id="600519.SH",
        universe_ref=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-cn-a-selection-daily", symbol_id="600519.SH", required_level="required")

    selector = ProviderSelector(registry, credential_resolver=FakeCredentialResolver({}))
    candidates = selector.select_candidates((gap,), FakeQueryPlan(request))
    provider_ids = [getattr(candidate, "provider_id") for candidate in candidates]

    assert "cn_a_primary" not in provider_ids
    assert provider_ids[:4] == [
        "cn_a_akshare_social_news",
        "cn_a_baostock_market",
        "cn_a_eastmoney_market_data",
        "cn_a_mootdx_market",
    ]


def test_selector_with_cn_a_selection_daily_keeps_tushare_first_when_api_configured() -> None:
    registry = build_minimal_provider_registry()
    request = SimpleNamespace(
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        fields=("date", "open", "high", "low", "close", "volume", "amount"),
        source_role_required=None,
        symbol_id="600519.SH",
        universe_ref=None,
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    gap = SimpleNamespace(request_id="req-cn-a-selection-daily", symbol_id="600519.SH", required_level="required")

    selector = ProviderSelector(
        registry,
        credential_resolver=FakeCredentialResolver({"data_source:tushare": "token"}),
    )
    candidates = selector.select_candidates((gap,), FakeQueryPlan(request))

    assert (getattr(candidates[0], "provider_id"), getattr(candidates[0], "endpoint_id")) == (
        "cn_a_primary",
        "daily_bar",
    )


def test_selector_with_migrated_cn_a_matrix_picks_domain_providers() -> None:
    registry = build_minimal_provider_registry()
    selector = ProviderSelector(registry)
    cases = (
        (
            SimpleNamespace(
                market="CN_A",
                data_type="financial_metric",
                granularity="quarterly",
                fields=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
                source_role_required=None,
                symbol_id="600519.SH",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("cn_a_tushare_fundamental", "cn_a_baostock_market"),
        ),
        (
            SimpleNamespace(
                market="CN_A",
                data_type="hot_money_event",
                granularity="event",
                fields=("trade_date", "seat", "buy_amount", "sell_amount", "symbol_id"),
                source_role_required=None,
                symbol_id="600519.SH",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            "cn_a_eastmoney_events",
        ),
        (
            SimpleNamespace(
                market="CN_A",
                data_type="company_news",
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url"),
                source_role_required=None,
                symbol_id="600519.SH",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("cn_a_akshare_social_news", "cn_a_google_news"),
        ),
        (
            SimpleNamespace(
                market="CN_A",
                data_type="quote_snapshot",
                granularity="realtime",
                fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                source_role_required=None,
                symbol_id="600519.SH",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("cn_a_eastmoney_market_data", "cn_a_mootdx_market", "cn_a_akshare_social_news"),
        ),
        (
            SimpleNamespace(
                market="CN_A",
                data_type="capital_flow",
                granularity="daily",
                fields=("date", "main_net", "small_net", "mid_net", "large_net", "super_net", "symbol_id"),
                source_role_required=None,
                symbol_id="600519.SH",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("cn_a_tushare_fundamental", "cn_a_akshare_social_news", "cn_a_eastmoney_market_data"),
        ),
        (
            SimpleNamespace(
                market="CN_A",
                data_type="social_signal",
                granularity="event",
                fields=("source", "timestamp", "symbol_id", "topic", "reason"),
                source_role_required=None,
                symbol_id="600519.SH",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            "cn_a_astock_signal_social",
        ),
    )

    for request, provider_id in cases:
        expected_provider_ids = provider_id if isinstance(provider_id, tuple) else (provider_id,)
        gap = SimpleNamespace(request_id=f"req-{expected_provider_ids[0]}", symbol_id="600519.SH", required_level="required")
        candidates = selector.select_candidates((gap,), FakeQueryPlan(request))
        assert [getattr(candidate, "provider_id") for candidate in candidates] == list(expected_provider_ids)


def test_selector_with_migrated_us_hk_crypto_matrices_picks_domain_providers() -> None:
    registry = build_minimal_provider_registry()
    selector = ProviderSelector(registry)
    cases = (
        (
            SimpleNamespace(
                market="US",
                data_type="official_filing",
                granularity="event",
                fields=("title", "published_at", "source", "url", "symbol_id"),
                source_role_required=None,
                symbol_id="AAPL",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("us_sec_official", "us_finnhub_data"),
        ),
        (
            SimpleNamespace(
                market="US",
                data_type="valuation_metric",
                granularity="realtime",
                fields=("pe", "pb", "ps", "market_cap"),
                source_role_required=None,
                symbol_id="AAPL",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("us_alpha_vantage_data", "us_finnhub_data"),
        ),
        (
            SimpleNamespace(
                market="HK",
                data_type="daily_bar",
                granularity="daily",
                fields=("open", "high", "low", "close", "volume"),
                source_role_required=None,
                symbol_id="00700.HK",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("hk_finnhub_data", "hk_tushare", "hk_tushare", "hk_sina_public", "hk_yahoo_finance"),
        ),
        (
            SimpleNamespace(
                market="HK",
                data_type="financial_metric",
                granularity="quarterly",
                fields=("roe", "gross_margin", "eps"),
                source_role_required=None,
                symbol_id="00700.HK",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("hk_akshare_fundamental",),
        ),
        (
            SimpleNamespace(
                market="HK",
                data_type="social_signal",
                granularity="event",
                fields=("source", "timestamp", "title", "url", "symbol_id"),
                source_role_required=None,
                symbol_id="00700.HK",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("hk_google_news",),
        ),
        (
            SimpleNamespace(
                market="CRYPTO",
                data_type="quote_snapshot",
                granularity="realtime",
                fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                source_role_required=None,
                symbol_id="BTC",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("crypto_binance_spot_market",),
        ),
        (
            SimpleNamespace(
                market="CRYPTO",
                data_type="company_news",
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url"),
                source_role_required=None,
                symbol_id="BTC",
                universe_ref=None,
                date_range_start=None,
                date_range_end=None,
            ),
            ("crypto_project_official_news", "crypto_google_news"),
        ),
    )

    for request, expected_provider_ids in cases:
        gap = SimpleNamespace(request_id=f"req-{expected_provider_ids[0]}", symbol_id=request.symbol_id, required_level="required")
        candidates = selector.select_candidates((gap,), FakeQueryPlan(request))
        assert [getattr(candidate, "provider_id") for candidate in candidates] == list(expected_provider_ids)


def test_product_request_field_filters_have_explicit_provider_exclusions() -> None:
    registry = build_minimal_provider_registry()
    selector = ProviderSelector(registry)
    gap = SimpleNamespace(request_id="product-request-field-filter", symbol_id="product", required_level="required")
    actual: dict[tuple[object, ...], tuple[str, ...]] = {}

    for origin, market, domain, data_type, granularity, fields in _product_request_specs():
        request = _request(market=market, data_type=data_type, granularity=granularity, fields=fields)
        selected = {
            (candidate.provider_id, candidate.endpoint_id)
            for candidate in selector.select_candidates((gap,), FakeQueryPlan(request))
        }
        assert selected, (origin, market, domain, data_type, granularity, fields)

        loose_request = _request(market=market, data_type=data_type, granularity=granularity, fields=())
        loose_candidates = selector.select_candidates((gap,), FakeQueryPlan(loose_request))
        capability_by_endpoint = {
            (cap.provider_id, cap.endpoint_id): cap
            for cap in registry.list_capabilities(market=market, data_type=data_type)
        }
        for candidate in loose_candidates:
            endpoint_key = (candidate.provider_id, candidate.endpoint_id)
            if endpoint_key in selected:
                continue
            capability = capability_by_endpoint[endpoint_key]
            if capability.source_role not in _IMPORTANT_FIELD_FILTER_SOURCE_ROLES:
                continue
            missing = tuple(sorted(set(fields) - set(capability.coverage_fields)))
            if not missing:
                continue
            key = (
                origin,
                market,
                domain,
                data_type,
                granularity,
                fields,
                candidate.provider_id,
                candidate.endpoint_id,
                missing,
            )
            actual[key] = capability.coverage_fields

    assert set(actual) == set(_INTENTIONAL_PRODUCT_REQUEST_FIELD_EXCLUSIONS), actual
    assert all(reason for reason in _INTENTIONAL_PRODUCT_REQUEST_FIELD_EXCLUSIONS.values())
