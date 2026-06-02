from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.providers.registry import ProviderRegistry


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


def _plugin(provider_id: str, source_role: str, priority_rank: int) -> FakePlugin:
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
            credential_required=False,
            credential_names=(),
            credential_scope=None,
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
            "cn_a_eastmoney_market_data",
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
