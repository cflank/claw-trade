from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from claw_trade.data_gateway.execution.fetch_engine import FetchTask
from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec, ManagedHttp
from claw_trade.data_gateway.providers.base import (
    CapabilityError,
    ensure_remote_success_is_auditable,
)
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.providers.plugins import (
    build_cn_a_provider_plugin,
    build_crypto_provider_plugin,
    build_hk_provider_plugin,
    build_us_provider_plugin,
    iter_minimal_market_plugins,
)
from claw_trade.data_gateway.providers.plugins.cn_a import (
    AkShareSocialNewsPlugin,
    AStockSignalSocialPlugin,
    BaostockCNProviderPlugin,
    EastMoneyCNEventsPlugin,
    EastMoneyCNMarketDataPlugin,
    GoogleNewsDiscoveryPlugin,
    MootdxCNProviderPlugin,
    TushareFundamentalPlugin,
)
from claw_trade.data_gateway.providers.plugins.crypto import (
    AlternativeMeCryptoSentimentPlugin,
    BinanceSpotMarketPlugin,
    CoinGeckoCryptoPlugin,
    CoinglassCryptoPlugin,
    CryptoProjectNewsPlugin,
    DefiLlamaCryptoPlugin,
    GlassnodeCryptoOnchainPlugin,
    LunarCrushCryptoSocialPlugin,
    TokenTerminalCryptoFundamentalsPlugin,
)
from claw_trade.data_gateway.providers.plugins.hk import (
    HKAkShareFundamentalPlugin,
    HKEXNewsProviderPlugin,
    HKFinnhubDataPlugin,
    HKGoogleNewsDiscoveryPlugin,
    HKSinaDailyBarPlugin,
    HKTushareFundamentalPlugin,
    HKYahooFinancePlugin,
)
from claw_trade.data_gateway.providers.plugins.us import (
    USAlphaVantageDataPlugin,
    USFinnhubDataPlugin,
    USFREDMacroPlugin,
    USGoogleNewsDiscoveryPlugin,
    USSECProviderPlugin,
    USStocktwitsSocialPlugin,
    USYahooFinancePlugin,
)
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


class RecordingPlugin:
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


@dataclass
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str
    content: bytes | None = None


class _RecordingHttpClient:
    def __init__(self, response: _HttpResponse | list[_HttpResponse]) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.requests: list[HttpRequestSpec] = []

    def send(self, request: HttpRequestSpec) -> _HttpResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[index]


def _plugin(
    *,
    provider_id: str = "official_feed",
    source_role: str = "official",
    batch_policy: BatchPolicy | None = None,
    http_visibility: str = "managed_http",
    can_be_formal_fact_source: bool | None = None,
) -> RecordingPlugin:
    policy = batch_policy or BatchPolicy(
        supports_batch=True,
        batch_by="symbol",
        max_symbols_per_call=50,
        mergeable_fields=("close", "volume"),
    )
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
                http_visibility=http_visibility,
                batch_policy=policy,
                can_be_formal_fact_source=can_be_formal_fact_source,
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
        default_rate_limit_policy={"window_seconds": 60, "max_calls": 10},
    )
    return RecordingPlugin(caps)


def test_capabilities_schema_requires_market_and_batch_policy() -> None:
    registry = ProviderRegistry()
    plugin = _plugin(
        batch_policy=BatchPolicy(
            supports_batch=False,
            batch_by="symbol",
            mergeable_fields=("close",),
        )
    )
    with pytest.raises(CapabilityError, match="不支持批量时 batch_by 必须为 none"):
        registry.register(plugin)


def test_discovery_role_cannot_be_formal_fact_source() -> None:
    registry = ProviderRegistry()
    plugin = _plugin(source_role="discovery", can_be_formal_fact_source=True)
    with pytest.raises(CapabilityError, match="不能作正式事实源"):
        registry.register(plugin)


def test_sdk_internal_unknown_cannot_claim_audited_remote_success() -> None:
    capability = SimpleNamespace(http_visibility="sdk_internal_unknown")
    fetch_result = SimpleNamespace(status="success", http_observations=())
    with pytest.raises(CapabilityError, match="SDK HTTP 不可审计时不能包装成可审计 remote_success"):
        ensure_remote_success_is_auditable(fetch_result, capability)


def test_minimal_plugins_declare_required_capability_matrix_for_four_markets() -> None:
    registry = ProviderRegistry()
    plugins = iter_minimal_market_plugins()
    for plugin in plugins:
        registry.register(plugin)

    snapshot = registry.read_capabilities(("cn_a_primary", "us_primary", "hk_sina_public", "crypto_primary"))
    capabilities = snapshot.list()
    assert {cap.market for cap in capabilities} == {"CN_A", "US", "HK", "CRYPTO"}
    for cap in capabilities:
        assert cap.data_type == "daily_bar"
        assert cap.granularity
        assert cap.fields
        expected_source_roles = {
            "cn_a_primary": "paid_data",
            "us_primary": "paid_data",
            "hk_sina_public": "built_in_public",
            "crypto_primary": "official",
        }
        assert cap.source_role == expected_source_roles[cap.provider_id]
        assert getattr(cap.batch_policy, "supports_batch") is True

    for plugin in plugins:
        caps = plugin.capabilities()
        assert getattr(caps, "credential_policy").missing_behavior == "credential_missing"
        assert all("CLAW_TRADE_" not in name for name in getattr(caps, "credential_policy").credential_names)
        assert getattr(caps, "license_policy").raw_storage_mode in {"store_full", "metadata_only", "no_store"}


def test_missing_credential_returns_credential_missing_without_legacy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = build_us_provider_plugin()
    monkeypatch.setenv("IGNORED_PROVIDER_TOKEN", "ignored-env-token")
    task = SimpleNamespace(
        batch_id="batch:us",
        provider_id="us_primary",
        endpoint_id="daily_bar",
        market="US",
        data_type="daily_bar",
        symbol_ids=("AAPL",),
    )
    result = plugin.fetch(task, ctx=SimpleNamespace())
    assert str(result.status) == "FetchStatus.CREDENTIAL_MISSING" or result.status.value == "credential_missing"


def test_plugin_reads_credentials_from_settings_resolver_not_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IGNORED_PROVIDER_TOKEN", "ignored-env-token")
    plugin = build_us_provider_plugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"Time Series (Daily)":{"2026-05-29":{"1. open":"1.0","2. high":"2.0","3. low":"0.5","4. close":"1.5","6. volume":"100"}}}',
        )
    )
    resolver = DataSourceCredentialResolver(
        data_source_store=SimpleNamespace(
            list_instances=lambda: (
                {
                    "supported_type": "alpha_vantage",
                    "enabled": True,
                    "credential_ref": "data_source:alpha_vantage:1",
                    "endpoint_url": "https://alpha.example",
                },
            )
        ),
        secret_store=SimpleNamespace(get=lambda ref: "db-secret" if ref == "data_source:alpha_vantage:1" else None),
    )
    task = SimpleNamespace(
        batch_id="batch:us",
        provider_id="us_primary",
        endpoint_id="daily_bar",
        market="US",
        data_type="daily_bar",
        symbol_ids=("AAPL",),
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].query["apikey"] == "db-secret"
    assert client.requests[0].host == "https://alpha.example"
    assert client.requests[0].query["outputsize"] == "compact"


def test_alpha_vantage_daily_bar_uses_full_output_for_long_ranges() -> None:
    plugin = build_us_provider_plugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"Time Series (Daily)":{"2026-05-29":{"1. open":"1.0","2. high":"2.0","3. low":"0.5","4. close":"1.5","6. volume":"100"}}}',
        )
    )
    resolver = DataSourceCredentialResolver(
        data_source_store=SimpleNamespace(
            list_instances=lambda: (
                {
                    "supported_type": "alpha_vantage",
                    "enabled": True,
                    "credential_ref": "data_source:alpha_vantage:1",
                    "endpoint_url": "https://alpha.example",
                },
            )
        ),
        secret_store=SimpleNamespace(get=lambda ref: "db-secret" if ref == "data_source:alpha_vantage:1" else None),
    )
    task = SimpleNamespace(
        batch_id="batch:us",
        provider_id="us_primary",
        endpoint_id="daily_bar",
        market="US",
        data_type="daily_bar",
        symbol_ids=("AAPL",),
        date_range_start=date(2025, 1, 1),
        date_range_end=date(2026, 5, 29),
    )

    plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert client.requests[0].query["outputsize"] == "full"


def test_crypto_plugin_fetches_daily_bar_through_managed_http_without_environment_token() -> None:
    plugin = build_crypto_provider_plugin()
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in plugin.capabilities().endpoints}
    assert "volume_unit" in endpoints["spot_daily_bar"].fields
    assert "amount_unit" in endpoints["spot_daily_bar"].fields
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='[[1772236800000,"84000.0","85000.0","83000.0","84500.0","123.4",1772323199999,"10432100.0"]]',
        )
    )
    task = FetchTask(
        batch_id="batch:crypto",
        provider_id="crypto_primary",
        endpoint_id="spot_daily_bar",
        market="CRYPTO",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "high", "low", "close", "volume"),
        provider_config_version="1.0.0",
        params={},
    )
    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert result.http_observations
    assert client.requests[0].host == "data-api.binance.vision"
    assert client.requests[0].path == "/api/v3/klines"
    assert client.requests[0].query["symbol"] == "BTCUSDT"
    row = result.payload["rows"][0]
    assert row["symbol_id"] == "BTCUSDT"
    assert row["base_asset"] == "BTC"
    assert row["quote_asset"] == "USDT"
    assert row["volume_unit"] == "BTC"
    assert row["amount_unit"] == "USDT"
    assert row["timezone"] == "UTC"
    assert row["amount"] == 10432100.0
    assert row["universe_ref"] == "binance_spot_all_symbols"
    assert row["source_market_segment"] == "spot"


def test_crypto_plugin_reads_endpoint_from_settings_resolver_without_environment() -> None:
    plugin = build_crypto_provider_plugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='[[1772236800000,"84000.0","85000.0","83000.0","84500.0","123.4",1772323199999,"10432100.0"]]',
        )
    )
    resolver = SimpleNamespace(get_endpoint_url=lambda name: "https://binance.example/custom" if name == "data_source:binance" else None)
    task = FetchTask(
        batch_id="batch:crypto",
        provider_id="crypto_primary",
        endpoint_id="spot_daily_bar",
        market="CRYPTO",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "high", "low", "close", "volume"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://binance.example"
    assert client.requests[0].path == "/custom/api/v3/klines"


def test_crypto_spot_intraday_preserves_hour_timestamp_through_managed_http() -> None:
    plugin = BinanceSpotMarketPlugin()
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in plugin.capabilities().endpoints}
    assert "volume_unit" in endpoints["spot_intraday_bar"].fields
    assert "amount_unit" in endpoints["spot_intraday_bar"].fields
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='[[1772236800000,"84000.0","85000.0","83000.0","84500.0","123.4",1772240399999,"10432100.0"]]',
        )
    )
    task = FetchTask(
        batch_id="batch:crypto",
        provider_id="crypto_binance_spot_market",
        endpoint_id="spot_intraday_bar",
        market="CRYPTO",
        data_type="intraday_bar",
        granularity="1h",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "high", "low", "close", "volume"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "intraday_bar"
    assert row["granularity"] == "1h"
    assert row["period_start"] == datetime(2026, 2, 28, 0, 0, tzinfo=UTC)
    assert row["period_end"] == datetime(2026, 2, 28, 0, 59, 59, 999000, tzinfo=UTC)
    assert row["open_time"] == row["period_start"]
    assert row["close_time"] == row["period_end"]
    assert row["volume_unit"] == "BTC"
    assert row["amount_unit"] == "USDT"


def test_cn_a_plugin_uses_settings_credential_and_endpoint_through_managed_http() -> None:
    plugin = build_cn_a_provider_plugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","open","high","low","close","vol","amount"],"items":[["600519.SH","20260529",1600,1610,1590,1605,1000,1605000]]}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a",
        provider_id="cn_a_primary",
        endpoint_id="daily_bar",
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "high", "low", "close", "volume"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    assert client.requests[0].method == "POST"
    assert client.requests[0].host == "http://tushare.example:8020"
    row = result.payload["rows"][0]
    assert row["symbol_id"] == "600519.SH"
    assert row["date"].isoformat() == "2026-05-29"
    assert row["exchange"] == "XSHG"
    assert row["currency"] == "CNY"
    assert row["amount"] == 1605000000.0
    assert row["amount_unit"] == "CNY"


def test_cn_a_tushare_daily_bar_supports_trade_date_universe_fetch() -> None:
    plugin = build_cn_a_provider_plugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","open","high","low","close","vol","amount"],"items":[["600519.SH","20260604",1600,1610,1590,1605,1000,1605000],["000001.SZ","20260604",10,11,9,10.5,2000,21000],["430047.BJ","20260604",5,6,4,5.5,3000,16500]]}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a-universe",
        provider_id="cn_a_primary",
        endpoint_id="daily_bar_by_trade_date",
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=(),
        date_range_start=date(2026, 6, 4),
        date_range_end=date(2026, 6, 4),
        fields=("date", "open", "high", "low", "close", "volume", "amount"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    request_body = json.loads(client.requests[0].body)
    assert request_body["params"] == {"trade_date": "20260604"}
    assert result.row_count == 3
    assert {row["symbol_id"] for row in result.payload["rows"]} == {"600519.SH", "000001.SZ", "430047.BJ"}
    assert {row["date"].isoformat() for row in result.payload["rows"]} == {"2026-06-04"}
    assert {row["exchange"] for row in result.payload["rows"]} == {"XSHG", "XSHE", "BJSE"}


def test_hk_finnhub_daily_bar_uses_settings_credential_and_documented_stock_candle_path() -> None:
    plugin = build_hk_provider_plugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"s":"ok","t":[1772236800],"o":[300],"h":[305],"l":[298],"c":[302],"v":[1000]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "finnhub-token" if name == "data_source:finnhub" else None,
        get_endpoint_url=lambda name: "https://finnhub.example/api/v1" if name == "data_source:finnhub" else None,
    )
    task = FetchTask(
        batch_id="batch:hk",
        provider_id="hk_finnhub_data",
        endpoint_id="stock_candle_daily",
        market="HK",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("00700.HK",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "high", "low", "close", "volume"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://finnhub.example"
    assert client.requests[0].path == "/api/v1/stock/candle"
    assert client.requests[0].query["symbol"] == "00700.HK"
    assert client.requests[0].query["resolution"] == "D"
    assert client.requests[0].query["token"] == "finnhub-token"
    row = result.payload["rows"][0]
    assert row["symbol_id"] == "00700.HK"
    assert row["exchange"] == "XHKG"
    assert row["currency"] == "HKD"


def test_cn_a_migrated_provider_matrix_declares_business_dataset_capabilities() -> None:
    registry = ProviderRegistry()
    for plugin in iter_minimal_market_plugins():
        registry.register(plugin)

    expected = {
        "daily_bar": ("cn_a_primary", "daily_bar", "paid_data"),
        "financial_statement": ("cn_a_tushare_fundamental", "financial_statement", "paid_data"),
        "financial_metric": ("cn_a_tushare_fundamental", "financial_metric", "paid_data"),
        "valuation_metric": ("cn_a_tushare_fundamental", "valuation_metric", "paid_data"),
        "quote_snapshot": ("cn_a_mootdx_market", "quote_snapshot", "built_in_public"),
        "order_book_snapshot": ("cn_a_mootdx_market", "order_book_snapshot", "built_in_public"),
        "capital_flow": ("cn_a_tushare_fundamental", "moneyflow", "paid_data"),
        "sector_snapshot": ("cn_a_tushare_fundamental", "moneyflow_ind_dc", "paid_data"),
        "corporate_action": ("cn_a_eastmoney_market_data", "shareholder_count", "built_in_public"),
        "company_news": ("cn_a_google_news", "company_news", "discovery"),
        "macro_news": ("cn_a_google_news", "macro_news", "discovery"),
        "event_calendar": ("cn_a_cninfo_events", "event_calendar", "official"),
        "hot_money_event": ("cn_a_eastmoney_events", "hot_money_event", "built_in_public"),
        "lockup_event": ("cn_a_eastmoney_events", "lockup_event", "built_in_public"),
    }
    for data_type, (provider_id, endpoint_id, source_role) in expected.items():
        capabilities = registry.list_capabilities("CN_A", data_type)
        assert any(
            cap.provider_id == provider_id and cap.endpoint_id == endpoint_id and cap.source_role == source_role
            for cap in capabilities
        ), data_type
        for cap in capabilities:
            assert cap.fields
            assert getattr(cap.batch_policy, "batch_by") in {"none", "symbol", "date"}
            assert getattr(cap.license_policy, "raw_storage_mode") in {"store_full", "metadata_only", "no_store"}

    discovery = next(cap for cap in registry.list_capabilities("CN_A", "company_news") if cap.provider_id == "cn_a_google_news")
    assert discovery.can_be_formal_fact_source is False
    valuation = registry.list_capabilities("CN_A", "valuation_metric")[0]
    assert "ev_ebitda" not in valuation.fields
    tushare_statement = next(
        cap
        for cap in registry.list_capabilities("CN_A", "financial_statement")
        if cap.provider_id == "cn_a_tushare_fundamental" and cap.endpoint_id == "financial_statement"
    )
    assert {
        "amount_unit",
        "revenue_basis",
        "net_income_basis",
        "cash_flow_basis",
        "assets_basis",
        "liabilities_basis",
    }.issubset(set(tushare_statement.fields))
    tushare_valuation = next(
        cap
        for cap in registry.list_capabilities("CN_A", "valuation_metric")
        if cap.provider_id == "cn_a_tushare_fundamental" and cap.endpoint_id == "valuation_metric"
    )
    assert "market_cap_unit" in tushare_valuation.fields
    tushare_capital_flow = next(
        cap
        for cap in registry.list_capabilities("CN_A", "capital_flow")
        if cap.provider_id == "cn_a_tushare_fundamental" and cap.endpoint_id == "moneyflow"
    )
    assert "amount_unit" in tushare_capital_flow.fields
    tushare_sector_flow = next(
        cap
        for cap in registry.list_capabilities("CN_A", "sector_snapshot")
        if cap.provider_id == "cn_a_tushare_fundamental" and cap.endpoint_id == "moneyflow_ind_dc"
    )
    assert "amount_unit" in tushare_sector_flow.fields
    cn_a_daily_capabilities = registry.list_capabilities("CN_A", "daily_bar")
    cn_a_daily = cn_a_daily_capabilities[0]
    assert "date" in cn_a_daily.fields
    assert "amount" in cn_a_daily.fields
    assert "amount_unit" in cn_a_daily.fields
    assert any(cap.endpoint_id == "daily_bar_by_trade_date" for cap in cn_a_daily_capabilities)
    crypto_daily = registry.list_capabilities("CRYPTO", "daily_bar")[0]
    assert "amount" in crypto_daily.fields


def test_cn_a_free_source_plugins_declare_source_backed_interfaces_without_legacy_runtime() -> None:
    registry = ProviderRegistry()
    for plugin in (AkShareSocialNewsPlugin(), EastMoneyCNMarketDataPlugin(), BaostockCNProviderPlugin(), MootdxCNProviderPlugin()):
        registry.register(plugin)

    by_provider = {
        provider_id: {
            (cap.endpoint_id, cap.data_type, cap.http_visibility)
            for cap in registry.read_capabilities((provider_id,)).list()
        }
        for provider_id in ("cn_a_akshare_social_news", "cn_a_eastmoney_market_data", "cn_a_baostock_market", "cn_a_mootdx_market")
    }

    assert by_provider["cn_a_akshare_social_news"] >= {
        ("stock_individual_fund_flow", "capital_flow", "sdk_internal_unknown"),
        ("stock_sector_fund_flow_rank", "sector_snapshot", "sdk_internal_unknown"),
    }
    assert by_provider["cn_a_eastmoney_market_data"] >= {
        ("stock_fund_flow_daily", "capital_flow", "managed_http"),
        ("sector_fund_flow_rank", "sector_snapshot", "managed_http"),
        ("shareholder_count", "corporate_action", "managed_http"),
        ("block_trade", "capital_flow", "managed_http"),
        ("margin_trading_detail", "capital_flow", "managed_http"),
        ("dividend_event", "corporate_action", "managed_http"),
        ("spot_quote_batch", "quote_snapshot", "managed_http"),
        ("daily_bar", "daily_bar", "managed_http"),
        ("stock_info", "valuation_metric", "managed_http"),
        ("global_news_7x24", "macro_news", "managed_http"),
    }
    assert by_provider["cn_a_baostock_market"] >= {
        ("daily_bar", "daily_bar", "no_http"),
        ("intraday_bar", "intraday_bar", "no_http"),
        ("adjust_factor", "corporate_action", "no_http"),
        ("dividend", "corporate_action", "no_http"),
        ("financial_statement", "financial_statement", "no_http"),
        ("financial_metric", "financial_metric", "no_http"),
        ("valuation_metric", "valuation_metric", "no_http"),
        ("trade_calendar", "event_calendar", "no_http"),
        ("stock_industry", "sector_snapshot", "no_http"),
    }
    assert by_provider["cn_a_mootdx_market"] >= {
        ("quote_snapshot", "quote_snapshot", "no_http"),
        ("order_book_snapshot", "order_book_snapshot", "no_http"),
        ("intraday_bar", "intraday_bar", "no_http"),
        ("daily_bar", "daily_bar", "no_http"),
        ("corporate_action", "corporate_action", "no_http"),
        ("finance_snapshot", "financial_metric", "no_http"),
    }
    eastmoney_endpoints = {cap.endpoint_id: cap for cap in registry.read_capabilities(("cn_a_eastmoney_market_data",)).list()}
    assert "amount_unit" in eastmoney_endpoints["stock_fund_flow_daily"].fields
    assert "amount_unit" in eastmoney_endpoints["sector_fund_flow_rank"].fields
    assert "amount_unit" in eastmoney_endpoints["spot_quote_batch"].fields
    assert "amount_unit" in eastmoney_endpoints["daily_bar"].fields
    assert "market_cap_unit" in eastmoney_endpoints["stock_info"].fields
    akshare_endpoints = {cap.endpoint_id: cap for cap in registry.read_capabilities(("cn_a_akshare_social_news",)).list()}
    assert "amount_unit" in akshare_endpoints["stock_zh_a_hist"].fields
    assert "amount_unit" in akshare_endpoints["stock_zh_a_spot_em"].fields
    assert "market_cap_unit" in akshare_endpoints["stock_individual_info_em"].fields
    assert "amount_unit" in akshare_endpoints["stock_individual_fund_flow"].fields
    assert "amount_unit" in akshare_endpoints["stock_sector_fund_flow_rank"].fields
    mootdx_endpoints = {cap.endpoint_id: cap for cap in registry.read_capabilities(("cn_a_mootdx_market",)).list()}
    assert "amount_unit" in mootdx_endpoints["quote_snapshot"].fields
    assert "amount_unit" in mootdx_endpoints["daily_bar"].fields


def test_cn_a_baostock_valuation_does_not_claim_market_cap_without_source_field() -> None:
    capabilities = BaostockCNProviderPlugin().capabilities()
    valuation = next(endpoint for endpoint in capabilities.endpoints if endpoint.endpoint_id == "valuation_metric")

    assert set(valuation.fields) == {"pe", "pb", "ps"}


def test_us_hk_crypto_provider_matrices_declare_source_backed_interfaces_without_legacy_runtime() -> None:
    registry = ProviderRegistry()
    plugins = (
        USSECProviderPlugin(),
        USAlphaVantageDataPlugin(),
        USFinnhubDataPlugin(),
        USYahooFinancePlugin(),
        USFREDMacroPlugin(),
        USStocktwitsSocialPlugin(),
        USGoogleNewsDiscoveryPlugin(),
        HKEXNewsProviderPlugin(),
        HKFinnhubDataPlugin(),
        HKYahooFinancePlugin(),
        HKTushareFundamentalPlugin(),
        HKAkShareFundamentalPlugin(),
        HKSinaDailyBarPlugin(),
        HKGoogleNewsDiscoveryPlugin(),
        BinanceSpotMarketPlugin(),
        CoinGeckoCryptoPlugin(),
        DefiLlamaCryptoPlugin(),
        CoinglassCryptoPlugin(),
        AlternativeMeCryptoSentimentPlugin(),
        CryptoProjectNewsPlugin(),
    )
    for plugin in plugins:
        registry.register(plugin)

    expected = {
        ("US", "official_filing"): ("us_sec_official", "submissions", "official", "managed_http"),
        ("US", "financial_statement"): ("us_sec_official", "companyfacts", "official", "managed_http"),
        ("US", "company_news"): ("us_alpha_vantage_data", "news_sentiment", "paid_data", "managed_http"),
        ("US", "valuation_metric"): ("us_yahoo_finance", "quote_summary_valuation", "built_in_public", "managed_http"),
        ("US", "macro_series"): ("us_fred_macro", "series_observations", "official", "managed_http"),
        ("US", "social_signal"): ("us_stocktwits_social", "symbol_stream", "sentiment", "managed_http"),
        ("HK", "official_filing"): ("hk_hkexnews_official", "regulatory_announcements", "official", "managed_http"),
        ("HK", "financial_metric"): ("hk_tushare", "hk_fina_indicator", "paid_data", "managed_http"),
        ("HK", "financial_statement"): ("hk_akshare_fundamental", "stock_financial_hk_report_em_income", "built_in_public", "managed_http"),
        ("HK", "daily_bar"): ("hk_tushare", "hk_daily_adj", "paid_data", "managed_http"),
        ("HK", "social_signal"): ("hk_google_news", "social_signal_news_heat", "discovery", "managed_http"),
        ("CRYPTO", "quote_snapshot"): ("crypto_binance_spot_market", "ticker_24hr", "official", "managed_http"),
        ("CRYPTO", "valuation_metric"): ("crypto_coinglass_derivatives", "spot_coins_markets", "paid_data", "managed_http"),
        ("CRYPTO", "defi_metric"): ("crypto_defillama_defi", "protocol", "built_in_public", "managed_http"),
        ("CRYPTO", "crypto_derivative_metric"): ("crypto_coinglass_derivatives", "futures_open_interest", "paid_data", "managed_http"),
        ("CRYPTO", "company_news"): ("crypto_project_official_news", "github_releases", "official", "managed_http"),
        ("CRYPTO", "social_signal"): ("crypto_alternative_me_sentiment", "fear_greed", "sentiment", "managed_http"),
    }
    for (market, data_type), (provider_id, endpoint_id, source_role, http_visibility) in expected.items():
        capabilities = registry.list_capabilities(market, data_type)
        assert any(
            cap.provider_id == provider_id
            and cap.endpoint_id == endpoint_id
            and cap.source_role == source_role
            and cap.http_visibility == http_visibility
            for cap in capabilities
        ), (market, data_type)

    for cap in (
        registry.list_capabilities("US", "company_news")
        + registry.list_capabilities("HK", "company_news")
        + registry.list_capabilities("HK", "social_signal")
        + registry.list_capabilities("CRYPTO", "company_news")
    ):
        if cap.source_role == "discovery":
            assert cap.can_be_formal_fact_source is False


def test_crypto_coingecko_market_rows_preserve_quote_and_supply_units() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in CoinGeckoCryptoPlugin().capabilities().endpoints}
    coins_markets = endpoints["coins_markets"]
    for field in ("price_unit", "market_cap_unit", "fdv_unit", "supply_unit", "volume_unit"):
        assert field in coins_markets.fields

    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=json.dumps(
                [
                    {
                        "current_price": 62000,
                        "market_cap": 1245487746323,
                        "fully_diluted_valuation": 1245487746323,
                        "circulating_supply": 20039087,
                        "total_supply": 20039087,
                        "total_volume": 32016116765,
                        "last_updated": "2026-06-09T14:00:00Z",
                    }
                ]
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:coingecko",
        provider_id="crypto_coingecko_market",
        endpoint_id="coins_markets",
        market="CRYPTO",
        data_type="valuation_metric",
        granularity="realtime",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=(
            "price",
            "price_unit",
            "market_cap",
            "market_cap_unit",
            "fdv",
            "fdv_unit",
            "circulating_supply",
            "total_supply",
            "supply_unit",
            "volume",
            "volume_unit",
        ),
        provider_config_version="1.0.0",
        params={},
    )

    result = CoinGeckoCryptoPlugin().fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["price_unit"] == "USD"
    assert row["market_cap_unit"] == "USD"
    assert row["fdv_unit"] == "USD"
    assert row["volume_unit"] == "USD"
    assert row["supply_unit"] == "BTC"
    assert row["price"] == 62000.0


def test_crypto_coinglass_valuation_market_rows_are_paid_source() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in CoinglassCryptoPlugin().capabilities().endpoints}
    spot_markets = endpoints["spot_coins_markets"]
    assert spot_markets.data_type == "valuation_metric"
    assert spot_markets.source_role == "paid_data"
    assert {
        "price",
        "market_cap",
        "fdv",
        "circulating_supply",
        "total_supply",
        "volume",
        "price_unit",
        "market_cap_unit",
        "fdv_unit",
        "supply_unit",
        "volume_unit",
    } <= set(spot_markets.fields)

    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=json.dumps(
                {
                    "data": [
                        {"symbol": "BTC", "current_price": 62000, "market_cap": 1245487746323, "volume_usd_24h": 32016116765},
                        {
                            "symbol": "BNB",
                            "current_price": 640,
                            "market_cap": 98300000000,
                            "fully_diluted_valuation": 128000000000,
                            "circulating_supply": 153000000,
                            "total_supply": 200000000,
                            "volume_usd_24h": 1800000000,
                        },
                    ]
                }
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-spot-markets",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="spot_coins_markets",
        market="CRYPTO",
        data_type="valuation_metric",
        granularity="realtime",
        symbol_ids=("BNBUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("price", "price_unit", "market_cap", "market_cap_unit", "volume", "volume_unit"),
        provider_config_version="1.0.0",
        params={},
    )

    result = CoinglassCryptoPlugin().fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/api/spot/coins-markets"
    assert request.query["per_page"] == "500"
    row = result.payload["rows"][0]
    assert row["base_asset"] == "BNB"
    assert row["price"] == 640.0
    assert row["market_cap"] == 98300000000.0
    assert row["market_cap_unit"] == "USD"
    assert row["fdv"] == 128000000000.0
    assert row["fdv_unit"] == "USD"
    assert row["circulating_supply"] == 153000000.0
    assert row["total_supply"] == 200000000.0
    assert row["supply_unit"] == "BNB"


def test_crypto_coinglass_market_data_history_preserves_supply_units() -> None:
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=json.dumps(
                {
                    "data": [
                        {
                            "timestamp": 1772236800000,
                            "price": "640",
                            "circulating_supply": "153000000",
                            "market_cap": 97920000000,
                        }
                    ]
                }
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-market-history",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="coin_market_data_history",
        market="CRYPTO",
        data_type="valuation_metric",
        granularity="daily",
        symbol_ids=("BNBUSDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("price", "price_unit", "market_cap", "market_cap_unit", "circulating_supply", "supply_unit", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = CoinglassCryptoPlugin().fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/api/coin/market-data-history"
    assert request.query["symbol"] == "BNB"
    row = result.payload["rows"][0]
    assert row["circulating_supply"] == 153000000.0
    assert row["supply_unit"] == "BNB"
    assert row["price_unit"] == "USD"


def test_hk_tushare_declares_official_documented_hk_market_and_metric_interfaces() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in HKTushareFundamentalPlugin().capabilities().endpoints}

    assert endpoints["hk_daily"].data_type == "daily_bar"
    assert endpoints["hk_daily"].source_role == "paid_data"
    assert {"open", "high", "low", "close", "volume", "amount"} <= set(endpoints["hk_daily"].fields)
    assert endpoints["hk_daily_adj"].data_type == "daily_bar"
    assert "adjustment" in endpoints["hk_daily_adj"].fields
    assert endpoints["hk_daily_adj_valuation"].data_type == "valuation_metric"
    assert {"price", "market_cap"} <= set(endpoints["hk_daily_adj_valuation"].fields)
    assert endpoints["hk_fina_indicator"].data_type == "financial_metric"

    assert "hk_basic" not in endpoints


def test_hk_tushare_daily_adj_fetches_market_data_through_settings_endpoint() -> None:
    plugin = HKTushareFundamentalPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"code":0,"data":{"fields":["ts_code","trade_date","close","open","high","low","vol","amount","adj_factor","total_mv"],'
                '"items":[["00700.HK","20260529",390.5,388.0,392.0,386.0,1000000,390500000,1.2,3700000000000]]}}'
            ),
        )
    )
    ctx = SimpleNamespace(
        managed_http=ManagedHttp(client),
        credential_resolver=SimpleNamespace(
            get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
            get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
        ),
    )
    task = FetchTask(
        batch_id="batch:hk-tushare",
        provider_id="hk_tushare",
        endpoint_id="hk_daily_adj",
        market="HK",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("00700.HK",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("open", "high", "low", "close", "volume", "amount", "adjustment"),
        provider_config_version="1",
        params={},
    )

    result = plugin.fetch(task, ctx)

    assert result.status == "success"
    assert result.row_count == 1
    assert client.requests[0].host == "http://tushare.example:8020"
    body = json.loads(str(client.requests[0].body))
    assert body["api_name"] == "hk_daily_adj"
    assert body["token"] == "ts-token"
    assert body["params"] == {"ts_code": "00700.HK", "start_date": "20260501", "end_date": "20260531"}
    assert result.payload["rows"][0]["adjustment"] == 1.2


def test_us_yahoo_finance_fetches_valuation_metric_through_managed_http() -> None:
    plugin = USYahooFinancePlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"timeseries":{"result":['
                '{"trailingPeRatio":[{"asOfDate":"2026-05-29","reportedValue":{"raw":28.5}}]},'
                '{"trailingMarketCap":[{"asOfDate":"2026-05-29","reportedValue":{"raw":3000000000}}]}'
                '],"error":null}}'
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:us-yahoo",
        provider_id="us_yahoo_finance",
        endpoint_id="quote_summary_valuation",
        market="US",
        data_type="valuation_metric",
        granularity="realtime",
        symbol_ids=("AAPL",),
        date_range_start=None,
        date_range_end=None,
        fields=("pe", "market_cap"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://query1.finance.yahoo.com"
    assert client.requests[0].path == "/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL"
    row = result.payload["rows"][0]
    assert row["dataset"] == "valuation_metric"
    assert row["pe"] == 28.5
    assert row["market_cap"] == 3000000000.0


def test_us_alpha_vantage_declares_tradingagents_fundamental_and_news_interfaces() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in USAlphaVantageDataPlugin().capabilities().endpoints}

    assert endpoints["overview_valuation"].data_type == "valuation_metric"
    assert endpoints["overview_financial_metric"].data_type == "financial_metric"
    assert endpoints["financial_statement"].data_type == "financial_statement"
    assert endpoints["news_sentiment"].data_type == "company_news"
    assert endpoints["global_news_sentiment"].data_type == "macro_news"
    assert all(endpoint.source_role == "paid_data" for endpoint in endpoints.values())


def test_us_alpha_vantage_overview_reads_settings_credential_and_parses_metrics() -> None:
    plugin = USAlphaVantageDataPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"Symbol":"AAPL","PERatio":"28.5","PriceToBookRatio":"45.1",'
                '"PriceToSalesRatioTTM":"7.2","MarketCapitalization":"3000000000000",'
                '"LatestQuarter":"2026-03-31"}'
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "av-token" if name == "data_source:alpha_vantage" else None,
        get_endpoint_url=lambda name: "https://alpha.example" if name == "data_source:alpha_vantage" else None,
    )
    task = FetchTask(
        batch_id="batch:us-alpha-overview",
        provider_id="us_alpha_vantage_data",
        endpoint_id="overview_valuation",
        market="US",
        data_type="valuation_metric",
        granularity="realtime",
        symbol_ids=("AAPL",),
        date_range_start=None,
        date_range_end=None,
        fields=("pe", "pb", "ps", "market_cap"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://alpha.example"
    assert request.query["function"] == "OVERVIEW"
    assert request.query["apikey"] == "av-token"
    row = result.payload["rows"][0]
    assert row["dataset"] == "valuation_metric"
    assert row["pe"] == 28.5
    assert row["market_cap"] == 3000000000000.0


def test_us_alpha_vantage_news_sentiment_fetches_company_news_without_discovery_downgrade() -> None:
    plugin = USAlphaVantageDataPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"feed":[{"title":"Apple news","url":"https://example.test/a",'
                '"time_published":"20260529T153000","source":"Reuters","summary":"summary"}]}'
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "av-token" if name == "data_source:alpha_vantage" else None,
        get_endpoint_url=lambda name: "https://alpha.example" if name == "data_source:alpha_vantage" else None,
    )
    task = FetchTask(
        batch_id="batch:us-alpha-news",
        provider_id="us_alpha_vantage_data",
        endpoint_id="news_sentiment",
        market="US",
        data_type="company_news",
        granularity="event",
        symbol_ids=("AAPL",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.query["function"] == "NEWS_SENTIMENT"
    assert request.query["tickers"] == "AAPL"
    assert request.query["time_from"] == "20260501T0000"
    row = result.payload["rows"][0]
    assert row["dataset"] == "company_news"
    assert row["source_roles"] == ("paid_data",)
    assert row["title"] == "Apple news"


def test_us_alpha_vantage_missing_credential_fails_closed() -> None:
    task = FetchTask(
        batch_id="batch:us-alpha-missing",
        provider_id="us_alpha_vantage_data",
        endpoint_id="overview_valuation",
        market="US",
        data_type="valuation_metric",
        granularity="realtime",
        symbol_ids=("AAPL",),
        date_range_start=None,
        date_range_end=None,
        fields=("pe",),
        provider_config_version="1.0.0",
        params={},
    )

    result = USAlphaVantageDataPlugin().fetch(task, ctx=SimpleNamespace())

    assert result.status.value == "credential_missing"


def test_us_finnhub_declares_tradingagents_cn_used_interfaces() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in USFinnhubDataPlugin().capabilities().endpoints}

    assert endpoints["quote"].data_type == "quote_snapshot"
    assert endpoints["stock_metric_valuation"].data_type == "valuation_metric"
    assert endpoints["stock_metric_financial"].data_type == "financial_metric"
    assert endpoints["company_news"].data_type == "company_news"
    assert endpoints["stock_filings"].data_type == "official_filing"
    assert all(endpoint.source_role == "paid_data" for endpoint in endpoints.values())


def test_us_finnhub_quote_reads_settings_credential_and_records_http() -> None:
    plugin = USFinnhubDataPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"c":192.5,"d":1.25,"dp":0.65,"o":191.0,"h":193.0,"l":190.5,"pc":191.25,"t":1772236800}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "finnhub-token" if name == "data_source:finnhub" else None,
        get_endpoint_url=lambda name: "https://finnhub.example/api/v1" if name == "data_source:finnhub" else None,
    )
    task = FetchTask(
        batch_id="batch:us-finnhub-quote",
        provider_id="us_finnhub_data",
        endpoint_id="quote",
        market="US",
        data_type="quote_snapshot",
        granularity="realtime",
        symbol_ids=("AAPL",),
        date_range_start=None,
        date_range_end=None,
        fields=("price", "change", "change_pct", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://finnhub.example"
    assert request.path == "/api/v1/quote"
    assert request.query["symbol"] == "AAPL"
    assert request.query["token"] == "finnhub-token"
    row = result.payload["rows"][0]
    assert row["dataset"] == "quote_snapshot"
    assert row["price"] == 192.5
    assert row["source_roles"] == ("paid_data",)


def test_us_finnhub_stock_metric_maps_valuation_without_sdk_or_env() -> None:
    plugin = USFinnhubDataPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"metric":{"peBasicExclExtraTTM":28.5,"pbAnnual":45.1,"psAnnual":7.2,"marketCapitalization":3000000}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "finnhub-token" if name == "data_source:finnhub" else None,
        get_endpoint_url=lambda name: "https://finnhub.example/api/v1" if name == "data_source:finnhub" else None,
    )
    task = FetchTask(
        batch_id="batch:us-finnhub-metric",
        provider_id="us_finnhub_data",
        endpoint_id="stock_metric_valuation",
        market="US",
        data_type="valuation_metric",
        granularity="realtime",
        symbol_ids=("AAPL",),
        date_range_start=None,
        date_range_end=None,
        fields=("pe", "pb", "ps", "market_cap"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    assert client.requests[0].path == "/api/v1/stock/metric"
    assert client.requests[0].query["metric"] == "all"
    row = result.payload["rows"][0]
    assert row["dataset"] == "valuation_metric"
    assert row["pe"] == 28.5
    assert row["market_cap"] == 3000000.0


def test_us_finnhub_company_news_and_filings_use_documented_paths() -> None:
    plugin = USFinnhubDataPlugin()
    news_client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='[{"headline":"Apple news","datetime":1772236800,"source":"Finnhub","summary":"summary","url":"https://example.test/news"}]',
        )
    )
    filing_client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='[{"form":"10-K","acceptedDate":"2026-05-29 16:00:00","reportUrl":"https://example.test/10k.htm","filingUrl":"https://example.test/index.htm"}]',
        ),
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "finnhub-token" if name == "data_source:finnhub" else None,
        get_endpoint_url=lambda name: "https://finnhub.example/api/v1" if name == "data_source:finnhub" else None,
    )
    news_task = FetchTask(
        batch_id="batch:us-finnhub-news",
        provider_id="us_finnhub_data",
        endpoint_id="company_news",
        market="US",
        data_type="company_news",
        granularity="event",
        symbol_ids=("AAPL",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )
    filing_task = replace(
        news_task,
        batch_id="batch:us-finnhub-filing",
        endpoint_id="stock_filings",
        data_type="official_filing",
        fields=("title", "published_at", "source", "url", "body_ref", "symbol_id"),
    )

    news_result = plugin.fetch(news_task, ctx=SimpleNamespace(managed_http=ManagedHttp(news_client), credential_resolver=resolver))
    filing_result = plugin.fetch(filing_task, ctx=SimpleNamespace(managed_http=ManagedHttp(filing_client), credential_resolver=resolver))

    assert news_result.status.value == "success"
    assert filing_result.status.value == "success"
    assert news_client.requests[0].path == "/api/v1/company-news"
    assert news_client.requests[0].query["from"] == "2026-05-01"
    assert filing_client.requests[0].path == "/api/v1/stock/filings"
    assert filing_result.payload["rows"][0]["quality_flags"] == ("paid_aggregator_official_filing_url",)


def test_us_finnhub_missing_credential_fails_closed() -> None:
    task = FetchTask(
        batch_id="batch:us-finnhub-missing",
        provider_id="us_finnhub_data",
        endpoint_id="quote",
        market="US",
        data_type="quote_snapshot",
        granularity="realtime",
        symbol_ids=("AAPL",),
        date_range_start=None,
        date_range_end=None,
        fields=("price",),
        provider_config_version="1.0.0",
        params={},
    )

    result = USFinnhubDataPlugin().fetch(task, ctx=SimpleNamespace())

    assert result.status.value == "credential_missing"
    assert "FINNHUB_API_KEY" not in str(result.error_message)


def test_hk_finnhub_declares_tradingagents_cn_hk_interfaces_without_insider_or_target_price_overmapping() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in HKFinnhubDataPlugin().capabilities().endpoints}

    assert endpoints["quote"].data_type == "quote_snapshot"
    assert endpoints["stock_candle_daily"].data_type == "daily_bar"
    assert endpoints["stock_metric_valuation"].data_type == "valuation_metric"
    assert endpoints["stock_metric_financial"].data_type == "financial_metric"
    assert endpoints["company_news"].data_type == "company_news"
    assert all(endpoint.source_role == "paid_data" for endpoint in endpoints.values())
    assert "stock_insider_transactions" not in endpoints
    assert "price_target" not in endpoints


def test_hk_hkexnews_fetches_official_rss_through_managed_http() -> None:
    plugin = HKEXNewsProviderPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text="<rss><channel><item><title>00700 HK filing</title><link>https://www.hkexnews.hk/test.pdf</link><pubDate>Fri, 29 May 2026 00:00:00 GMT</pubDate><description>announcement</description></item></channel></rss>",
        )
    )
    task = FetchTask(
        batch_id="batch:hkex",
        provider_id="hk_hkexnews_official",
        endpoint_id="regulatory_announcements",
        market="HK",
        data_type="official_filing",
        granularity="event",
        symbol_ids=("00700.HK",),
        date_range_start=None,
        date_range_end=None,
        fields=("title", "published_at", "source", "url", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://www.hkex.com.hk"
    row = result.payload["rows"][0]
    assert row["dataset"] == "official_filing"
    assert row["source"] == "HKEXnews"
    assert row["exchange"] == "XHKG"


def test_hk_hkexnews_does_not_assign_unmatched_market_rss_to_symbol() -> None:
    plugin = HKEXNewsProviderPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text="<rss><channel><item><title>09999 unrelated HK filing</title><link>https://www.hkexnews.hk/test.pdf</link><pubDate>Fri, 29 May 2026 00:00:00 GMT</pubDate><description>announcement</description></item></channel></rss>",
        )
    )
    task = FetchTask(
        batch_id="batch:hkex",
        provider_id="hk_hkexnews_official",
        endpoint_id="regulatory_announcements",
        market="HK",
        data_type="official_filing",
        granularity="event",
        symbol_ids=("00700.HK",),
        date_range_start=None,
        date_range_end=None,
        fields=("title", "published_at", "source", "url", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "empty"
    assert result.http_observations


def test_hk_google_news_social_signal_fetches_discovery_rows_without_formal_fact_claim() -> None:
    plugin = HKGoogleNewsDiscoveryPlugin()
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in plugin.capabilities().endpoints}

    assert endpoints["social_signal_news_heat"].data_type == "social_signal"
    assert endpoints["social_signal_news_heat"].source_role == "discovery"
    assert endpoints["social_signal_news_heat"].can_be_formal_fact_source is False

    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text="<rss><channel><item><title>Tencent investor discussion</title><link>https://example.test/social</link><pubDate>Fri, 29 May 2026 00:00:00 GMT</pubDate><description>discussion summary</description></item></channel></rss>",
        )
    )
    task = FetchTask(
        batch_id="batch:hk-social",
        provider_id="hk_google_news",
        endpoint_id="social_signal_news_heat",
        market="HK",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("00700.HK",),
        date_range_start=None,
        date_range_end=None,
        fields=("source", "timestamp", "title", "url", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://news.google.com"
    assert request.query["q"] == "00700.HK Hong Kong stock investor discussion"
    row = result.payload["rows"][0]
    assert row["dataset"] == "social_signal"
    assert row["granularity"] == "event"
    assert row["source"] == "Google News"
    assert row["source_roles"] == ("discovery",)
    assert row["quality_flags"] == ("not_formal_fact_source",)
    assert row["timestamp"].isoformat() == "2026-05-29T00:00:00+00:00"


def test_hk_akshare_financial_metric_fetches_eastmoney_through_managed_http() -> None:
    plugin = HKAkShareFundamentalPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=json.dumps(
                {
                    "result": {
                        "data": [
                            {
                                "STD_REPORT_DATE": "2025-12-31 00:00:00",
                                "BASIC_EPS": "12.34",
                                "ROE": "18.5",
                                "GROSS_MARGIN": "47.2",
                            }
                        ]
                    }
                }
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:hk-akshare-metric",
        provider_id="hk_akshare_fundamental",
        endpoint_id="stock_financial_hk_analysis_indicator_em",
        market="HK",
        data_type="financial_metric",
        granularity="quarterly",
        symbol_ids=("00700.HK",),
        date_range_start=None,
        date_range_end=None,
        fields=("roe", "gross_margin", "eps"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://datacenter.eastmoney.com"
    row = result.payload["rows"][0]
    assert row["dataset"] == "financial_metric"
    assert row["symbol_id"] == "00700.HK"
    assert row["roe"] == 18.5
    assert row["gross_margin"] == 47.2
    assert row["eps"] == 12.34
    assert result.http_observations


def test_hk_akshare_income_statement_fetches_report_list_and_income_through_managed_http() -> None:
    plugin = HKAkShareFundamentalPlugin()
    client = _RecordingHttpClient(
        [
            _HttpResponse(
                status_code=200,
                headers={},
                text=json.dumps(
                    {
                        "result": {
                            "data": [
                                {
                                    "REPORT_LIST": [
                                        {"REPORT_DATE": "2025-12-31 00:00:00", "REPORT_TYPE": "年报"}
                                    ]
                                }
                            ]
                        }
                    }
                ),
            ),
            _HttpResponse(
                status_code=200,
                headers={},
                text=json.dumps(
                    {
                        "result": {
                            "data": [
                                {
                                    "REPORT_DATE": "2025-12-31 00:00:00",
                                    "STD_ITEM_NAME": "营业收入",
                                    "AMOUNT": "660000000000",
                                },
                                {
                                    "REPORT_DATE": "2025-12-31 00:00:00",
                                    "STD_ITEM_NAME": "归属于母公司股东的净利润",
                                    "AMOUNT": "225000000000",
                                },
                            ]
                        }
                    }
                ),
            ),
        ]
    )
    task = FetchTask(
        batch_id="batch:hk-akshare-income",
        provider_id="hk_akshare_fundamental",
        endpoint_id="stock_financial_hk_report_em_income",
        market="HK",
        data_type="financial_statement",
        granularity="annual",
        symbol_ids=("00700.HK",),
        date_range_start=None,
        date_range_end=None,
        fields=("period", "revenue", "net_income"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert len(client.requests) == 2
    assert all(request.host == "https://datacenter.eastmoney.com" for request in client.requests)
    row = result.payload["rows"][0]
    assert row["dataset"] == "financial_statement"
    assert row["period"] == "2025-12-31"
    assert row["revenue"] == 660000000000.0
    assert row["net_income"] == 225000000000.0
    assert result.http_observations


def test_crypto_alternative_me_fetches_market_sentiment_without_formal_social_claim() -> None:
    plugin = AlternativeMeCryptoSentimentPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"value":"72","value_classification":"Greed","timestamp":"1772236800"}]}',
        )
    )
    task = FetchTask(
        batch_id="batch:crypto-sentiment",
        provider_id="crypto_alternative_me_sentiment",
        endpoint_id="fear_greed",
        market="CRYPTO",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("BTC",),
        date_range_start=None,
        date_range_end=None,
        fields=("source", "timestamp", "score", "sentiment", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://api.alternative.me"
    row = result.payload["rows"][0]
    assert row["dataset"] == "social_signal"
    assert row["score"] == 72.0
    assert row["sentiment"] == "Greed"
    assert row["quality_flags"] == ("market_level_sentiment_not_platform_social",)


def test_crypto_coinglass_missing_credential_fails_closed() -> None:
    plugin = CoinglassCryptoPlugin()
    task = FetchTask(
        batch_id="batch:coinglass",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_open_interest",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        symbol_ids=("BTC",),
        date_range_start=None,
        date_range_end=None,
        fields=("open_interest",),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace())

    assert result.status.value == "credential_missing"
    assert "CLAW_TRADE_" not in str(result.error_message)


def test_crypto_coinglass_uses_configured_proxy_header_and_v4_path() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"openInterest":"12345","timestamp":1772236800000}]}',
        )
    )
    resolver = DataSourceCredentialResolver(
        data_source_store=SimpleNamespace(
            list_instances=lambda: (
                {
                    "supported_type": "coinglass",
                    "enabled": True,
                    "credential_ref": "data_source:coinglass:1",
                    "endpoint_url": "https://proxy.keystore.com.cn/api/v1/proxy/coinglass",
                    "header_name": "X-Api-Key",
                },
            )
        ),
        secret_store=SimpleNamespace(get=lambda ref: "coinglass-token" if ref == "data_source:coinglass:1" else None),
    )
    task = FetchTask(
        batch_id="batch:coinglass",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_open_interest",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("open_interest",),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://proxy.keystore.com.cn"
    assert request.path == "/api/v1/proxy/coinglass/v4/api/futures/open-interest/exchange-list"
    assert request.headers["X-Api-Key"] == "coinglass-token"
    assert "CG-API-KEY" not in request.headers
    assert request.query["symbol"] == "BTC"


def test_crypto_coinglass_open_interest_and_funding_keep_unit_metadata() -> None:
    plugin = CoinglassCryptoPlugin()
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )

    oi_client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"sumOpenInterestValue":"12345","timestamp":1772236800000}]}',
        )
    )
    oi_task = FetchTask(
        batch_id="batch:coinglass-oi",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_open_interest",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("open_interest",),
        provider_config_version="1.0.0",
        params={},
    )

    oi_result = plugin.fetch(oi_task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(oi_client)))

    assert oi_result.status.value == "success"
    oi_row = oi_result.payload["rows"][0]
    assert oi_row["open_interest"] == 12345.0
    assert oi_row["open_interest_unit"] == "USD"
    assert oi_row["open_interest_source_field"] == "sumOpenInterestValue"

    funding_client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"fundingRate":"0.00379","time":1772236800000}]}',
        )
    )
    funding_task = FetchTask(
        batch_id="batch:coinglass-funding",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_funding_rate",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("funding_rate", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    funding_result = plugin.fetch(funding_task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(funding_client)))

    assert funding_result.status.value == "success"
    funding_row = funding_result.payload["rows"][0]
    assert funding_row["funding_rate"] == 0.00379
    assert funding_row["funding_rate_unit"] == "percent"
    assert funding_row["funding_rate_source_field"] == "fundingRate"

    funding_value_only_client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[0.00223]}',
        )
    )
    funding_value_only_result = plugin.fetch(
        funding_task,
        ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(funding_value_only_client)),
    )

    assert funding_value_only_result.status.value == "success"
    funding_value_only_row = funding_value_only_result.payload["rows"][0]
    assert funding_value_only_row["funding_rate"] == 0.00223
    assert funding_value_only_row["funding_rate_unit"] == "percent"

    oi_value_only_client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[20848773.934706405]}',
        )
    )
    oi_value_only_result = plugin.fetch(
        oi_task,
        ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(oi_value_only_client)),
    )

    assert oi_value_only_result.status.value == "success"
    oi_value_only_row = oi_value_only_result.payload["rows"][0]
    assert oi_value_only_row["open_interest"] == 20848773.934706405
    assert oi_value_only_row["open_interest_unit"] == "USD"


def test_crypto_coinglass_futures_pairs_markets_normalizes_binance_usdt_snapshot() -> None:
    plugin = CoinglassCryptoPlugin()
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=json.dumps(
                {
                    "data": [
                        {
                            "exchangeName": "OKX",
                            "instrumentId": "BTC-USDT",
                            "price": "84001",
                            "openInterestUsd": "900",
                        },
                        {
                            "exchangeName": "Binance",
                            "instrumentId": "BTCUSDT",
                            "price": "84000.5",
                            "volumeUsd24h": "1000000",
                            "longVolumeUsd": "560000",
                            "shortVolumeUsd": "440000",
                            "openInterestUsd": "123456789",
                            "fundingRate": "0.004",
                            "longLiquidationUsd24h": "1000",
                            "shortLiquidationUsd24h": "2500",
                            "time": 1772236800000,
                        },
                    ]
                }
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:coinglass-pairs-markets",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_pairs_markets",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("open_interest", "funding_rate", "liquidation_value", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].path == "/api/futures/pairs-markets"
    assert client.requests[0].query["symbol"] == "BTCUSDT"
    assert len(result.payload["rows"]) == 1
    row = result.payload["rows"][0]
    assert row["symbol_id"] == "BTCUSDT"
    assert row["price"] == 84000.5
    assert row["price_unit"] == "USDT"
    assert row["volume_usd"] == 1000000.0
    assert row["volume_unit"] == "USD"
    assert row["open_interest"] == 123456789.0
    assert row["open_interest_unit"] == "USD"
    assert row["funding_rate"] == 0.004
    assert row["funding_rate_unit"] == "percent"
    assert row["liquidation_value"] == 3500.0
    assert row["liquidation_value_unit"] == "USD"


def test_crypto_coinglass_history_limit_keeps_provider_compatible_default() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        [
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"fundingRate":"0.001","time":1751328000000}]}',
            ),
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"fundingRate":"0.002","time":1766880000000}]}',
            ),
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"fundingRate":"0.00379","time":1772236800000}]}',
            ),
        ]
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-funding-window",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_funding_rate",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2025, 6, 1),
        date_range_end=date(2026, 6, 1),
        fields=("funding_rate", "funding_rate_unit", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert len(client.requests) == 9
    assert {request.query["limit"] for request in client.requests} == {"1000"}
    assert client.requests[0].query["start_time"] == 1748736000000
    assert client.requests[-1].query["end_time"] == 1780358399999
    assert len(result.payload["rows"]) == 3


def test_crypto_coinglass_cvd_uses_official_window_limit_and_chunks_long_ranges() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        [
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"cum_vol_delta":"100","agg_taker_buy_vol":"200","agg_taker_sell_vol":"100","time":1751328000000}]}',
            ),
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"cum_vol_delta":"150","agg_taker_buy_vol":"250","agg_taker_sell_vol":"100","time":1766880000000}]}',
            ),
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"cum_vol_delta":"180","agg_taker_buy_vol":"280","agg_taker_sell_vol":"100","time":1772236800000}]}',
            ),
        ]
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-cvd-window",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="spot_cvd_history",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2025, 6, 1),
        date_range_end=date(2026, 6, 1),
        fields=("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert len(client.requests) == 3
    assert {request.query["limit"] for request in client.requests} == {"4500"}
    assert {request.query["symbol"] for request in client.requests} == {"BTC"}
    assert {request.query["exchange_list"] for request in client.requests} == {"Binance,OKX,Bybit"}
    assert client.requests[0].query["start_time"] == 1748736000000
    assert client.requests[-1].query["end_time"] == 1780358399999
    assert [row["cvd"] for row in result.payload["rows"]] == [100.0, 150.0, 180.0]


def test_crypto_coinglass_aggregated_coin_endpoints_use_coin_and_exchange_list() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"time":1772236800000,"takerBuyVolume":"100","takerSellVolume":"80","buySellRatio":"1.25"}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-taker",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_taker_buy_sell",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/api/futures/aggregated-taker-buy-sell-volume/history"
    assert request.query["symbol"] == "BTC"
    assert request.query["exchange_list"] == "Binance,OKX,Bybit"
    assert request.query["interval"] == "1h"
    row = result.payload["rows"][0]
    assert row["taker_buy_volume"] == 100.0
    assert row["taker_volume_unit"] == "USD"
    assert row["taker_buy_sell_ratio"] == 1.25


def test_crypto_coinglass_business_error_code_is_not_reported_as_empty_result() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"code":"400","msg":"Invalid symbol"}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-error-code",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_taker_buy_sell",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("taker_buy_volume", "taker_sell_volume", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "error"
    assert result.error_code == "RuntimeError"
    assert result.error_message == "coinglass_api_400:Invalid symbol"


def test_crypto_coinglass_orderbook_uses_official_ask_bids_history() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"time":1772236800000,"bids_usd":"1000000","bids_quantity":"16.4","asks_usd":"900000","asks_quantity":"14.6"}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-orderbook",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_order_book",
        market="CRYPTO",
        data_type="order_book_snapshot",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("bids_usd", "bids_quantity", "asks_usd", "asks_quantity", "timestamp"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/api/futures/orderbook/ask-bids-history"
    assert request.query["exchange"] == "Binance"
    assert request.query["symbol"] == "BTCUSDT"
    assert request.query["limit"] == "1000"
    row = result.payload["rows"][0]
    assert row["bids_usd"] == 1000000.0
    assert row["bids_quantity"] == 16.4
    assert row["asks_usd"] == 900000.0
    assert row["asks_quantity"] == 14.6


def test_crypto_coinglass_orderbook_history_keeps_all_rows() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"time":1772236800000,"bids_usd":"1000000","bids_quantity":"16.4","asks_usd":"900000","asks_quantity":"14.6"},{"time":1772240400000,"bids_usd":"1100000","bids_quantity":"17.4","asks_usd":"950000","asks_quantity":"15.6"}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-orderbook-history",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_order_book",
        market="CRYPTO",
        data_type="order_book_snapshot",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("bids_usd", "bids_quantity", "asks_usd", "asks_quantity", "timestamp"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert result.row_count == 2
    assert [row["bids_usd"] for row in result.payload["rows"]] == [1000000.0, 1100000.0]


def test_crypto_coinglass_liquidation_heatmap_fetches_and_normalizes_points() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"price":"60000","size":"1200","side":"long","timestamp":1772236800000}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-heatmap",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_liquidation_heatmap",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("liquidation_price", "liquidation_size", "side", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/api/futures/liquidation/aggregated-heatmap/model1"
    assert request.query["symbol"] == "BTC"
    row = result.payload["rows"][0]
    assert row["liquidation_price"] == 60000.0
    assert row["liquidation_size"] == 1200.0
    assert row["side"] == "long"


def test_crypto_coinglass_heatmap_range_follows_requested_window() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"price":"60000","size":"1200","side":"long","timestamp":1772236800000}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-heatmap-window",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_liquidation_heatmap",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2025, 6, 1),
        date_range_end=date(2026, 6, 1),
        fields=("liquidation_price", "liquidation_size", "side", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].query["range"] == "1y"


def test_crypto_coinglass_liquidation_heatmap_array_payload_does_not_treat_timestamp_as_price() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[[1772236800000,60000,1200]]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-heatmap-array",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_liquidation_heatmap",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("liquidation_price", "liquidation_size", "side", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["liquidation_price"] == 60000.0
    assert row["liquidation_size"] == 1200.0
    assert row["liquidation_price"] != 1772236800000.0


def test_crypto_coinglass_liquidation_heatmap_official_axis_payload_maps_price_index() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=json.dumps(
                {
                    "data": {
                        "y_axis": [150, 151, 152],
                        "liquidation_leverage_data": [[1, 1, 4389152.66]],
                        "price_candlesticks": [
                            [1772236500, "61486", "61596.4", "61434.4", "61539.9", "63753192.1129"],
                            [1772236800, "61539.9", "61610.0", "61480.0", "61590.5", "42311820.8720"],
                        ],
                    }
                }
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-heatmap-small-array",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="futures_liquidation_heatmap",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("liquidation_price", "liquidation_size", "side", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["liquidation_price"] == 151.0
    assert row["liquidation_size"] == 4389152.66
    assert row["timestamp"].year >= 2020


def test_crypto_coinglass_options_cvd_and_etf_flow_fetch_and_normalize() -> None:
    plugin = CoinglassCryptoPlugin()
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    cases = (
        (
            "options_open_interest",
            "crypto_derivative_metric",
            "1h",
            ("options_open_interest", "options_volume", "timestamp", "symbol_id"),
            [
                '{"data":{"time_list":[1772323200000,1772409600000],"data_map":{"Deribit":["1000","1100"],"CME":["200",null]}}}',
                '{"data":{"time_list":[1772323200000,1772409600000],"data_map":{"Deribit":["25","30"],"CME":["5",null]}}}',
            ],
            "/api/option/exchange-oi-history",
            {"symbol": "BTC", "unit": "USD", "range": "all"},
            {
                "options_open_interest": 1200.0,
                "options_open_interest_unit": "USD",
                "options_volume": 30.0,
                "options_volume_unit": "USD",
            },
        ),
        (
            "spot_cvd_history",
            "crypto_derivative_metric",
            "1h",
            ("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "timestamp", "symbol_id"),
            '{"data":[{"time":1772236800000,"cum_vol_delta":"-2500","agg_taker_buy_vol":"100","agg_taker_sell_vol":"2600"}]}',
            "/api/spot/aggregated-cvd/history",
            {"symbol": "BTC", "exchange_list": "Binance,OKX,Bybit", "interval": "1h", "unit": "usd", "limit": "4500"},
            {"cvd": -2500.0, "taker_buy_volume": 100.0, "taker_sell_volume": 2600.0, "taker_volume_unit": "USD"},
        ),
        (
            "futures_cvd_history",
            "crypto_derivative_metric",
            "1h",
            ("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "timestamp", "symbol_id"),
            '{"data":[{"time":1772236800000,"cum_vol_delta":"-2500","agg_taker_buy_vol":"100","agg_taker_sell_vol":"2600"}]}',
            "/api/futures/aggregated-cvd/history",
            {"symbol": "BTC", "exchange_list": "Binance,OKX,Bybit", "interval": "1h", "unit": "usd", "limit": "4500"},
            {"cvd": -2500.0, "taker_buy_volume": 100.0, "taker_sell_volume": 2600.0, "taker_volume_unit": "USD"},
        ),
        (
            "etf_flow_history",
            "crypto_derivative_metric",
            "daily",
            ("etf_flow_usd", "price", "timestamp", "symbol_id"),
            '{"data":[{"timestamp":1772236800000,"flow_usd":"1234","price_usd":"60000"}]}',
            "/api/etf/bitcoin/flow-history",
            {},
            {"etf_flow_usd": 1234.0, "price": 60000.0},
        ),
    )

    for endpoint_id, data_type, granularity, fields, body, expected_path, expected_query, expected_values in cases:
        if isinstance(body, list):
            client = _RecordingHttpClient([_HttpResponse(status_code=200, headers={}, text=item) for item in body])
        else:
            client = _RecordingHttpClient(_HttpResponse(status_code=200, headers={}, text=body))
        task = FetchTask(
            batch_id=f"batch:{endpoint_id}",
            provider_id="crypto_coinglass_derivatives",
            endpoint_id=endpoint_id,
            market="CRYPTO",
            data_type=data_type,
            granularity=granularity,
            symbol_ids=("BTC/USDT",),
            date_range_start=date(2026, 3, 1),
            date_range_end=date(2026, 3, 2),
            fields=fields,
            provider_config_version="1.0.0",
            params={},
        )

        result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

        assert result.status.value == "success", endpoint_id
        request = client.requests[0]
        assert request.path == expected_path
        if endpoint_id == "options_open_interest":
            assert [item.path for item in client.requests] == [
                "/api/option/exchange-oi-history",
                "/api/option/exchange-vol-history",
            ]
        for key, value in expected_query.items():
            assert request.query[key] == value
        if endpoint_id == "etf_flow_history":
            assert request.query == {}
        row = result.payload["rows"][0]
        for key, value in expected_values.items():
            assert row[key] == value


def test_crypto_coinglass_etf_flow_supports_official_solana_and_hype_paths() -> None:
    plugin = CoinglassCryptoPlugin()
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )

    for symbol, expected_path in {
        "SOL/USDT": "/api/etf/solana/flow-history",
        "HYPE/USDT": "/api/etf/hype/flow-history",
    }.items():
        client = _RecordingHttpClient(
            _HttpResponse(
                status_code=200,
                headers={},
                text='{"data":[{"timestamp":1772236800000,"flow_usd":"1234","price_usd":"150"}]}',
            )
        )
        task = FetchTask(
            batch_id=f"batch:coinglass-etf:{symbol}",
            provider_id="crypto_coinglass_derivatives",
            endpoint_id="etf_flow_history",
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            granularity="daily",
            symbol_ids=(symbol,),
            date_range_start=date(2026, 3, 1),
            date_range_end=date(2026, 3, 2),
            fields=("etf_flow_usd", "price", "timestamp", "symbol_id"),
            provider_config_version="1.0.0",
            params={},
        )

        result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

        assert result.status.value == "success"
        assert client.requests[0].path == expected_path
        assert client.requests[0].query == {}


def test_crypto_lunarcrush_social_uses_bearer_token_and_topic_slug() -> None:
    plugin = LunarCrushCryptoSocialPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":{"timestamp":1772236800,"galaxy_score":"72","sentiment":"bullish","social_dominance":"3.5","num_posts":"120","interactions":"5000"}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "lunar-token" if name == "data_source:lunarcrush" else None,
        get_endpoint_url=lambda name: "https://lunar.example/api4" if name == "data_source:lunarcrush" else None,
    )
    task = FetchTask(
        batch_id="batch:lunarcrush",
        provider_id="crypto_lunarcrush_social",
        endpoint_id="topic",
        market="CRYPTO",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("source", "timestamp", "score", "sentiment", "social_dominance", "num_posts", "interactions", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://lunar.example"
    assert request.path == "/api4/public/topic/bitcoin/v1"
    assert request.headers["authorization"] == "Bearer lunar-token"
    row = result.payload["rows"][0]
    assert row["source"] == "LunarCrush"
    assert row["score"] == 72.0
    assert row["social_dominance"] == 3.5
    assert row["num_posts"] == 120.0


def test_crypto_glassnode_deep_onchain_fetches_metric_paths() -> None:
    plugin = GlassnodeCryptoOnchainPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='[{"t":1772236800,"v":"12.5"}]',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "glass-token" if name == "data_source:glassnode" else None,
        get_endpoint_url=lambda name: "https://glassnode.example" if name == "data_source:glassnode" else None,
    )
    task = FetchTask(
        batch_id="batch:glassnode",
        provider_id="crypto_glassnode_onchain",
        endpoint_id="deep_onchain_metrics",
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        granularity="daily",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("timestamp", "metric", "value", "value_unit", "chain", "source_metric"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert len(client.requests) == 5
    assert {request.path for request in client.requests} == {
        "/v1/metrics/addresses/active_count",
        "/v1/metrics/indicators/mvrv_account_based",
        "/v1/metrics/indicators/sopr_less_155",
        "/v1/metrics/indicators/sopr_more_155",
        "/v1/metrics/indicators/net_unrealized_profit_loss",
    }
    assert all(request.query["api_key"] == "glass-token" for request in client.requests)
    assert {row["source_metric"] for row in result.payload["rows"]} == {"active_addresses", "mvrv", "sth_sopr", "lth_sopr", "nupl"}
    assert {row["value_unit"] for row in result.payload["rows"]} == {"count", "dimensionless"}


def test_crypto_token_terminal_protocol_revenue_uses_bearer_token() -> None:
    plugin = TokenTerminalCryptoFundamentalsPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"date":"2026-03-01","revenue":"12345","fees":"25000"}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "tt-token" if name == "data_source:token_terminal" else None,
        get_endpoint_url=lambda name: "https://token-terminal.example" if name == "data_source:token_terminal" else None,
    )
    task = FetchTask(
        batch_id="batch:token-terminal",
        provider_id="crypto_token_terminal_fundamentals",
        endpoint_id="protocol_revenue",
        market="CRYPTO",
        data_type="defi_metric",
        granularity="daily",
        symbol_ids=("ETH/USDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("protocol_revenue", "fees", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/v2/metrics/revenue"
    assert request.query["project_ids"] == "ethereum"
    assert request.query["start"] == "2026-03-01"
    assert request.headers["authorization"] == "Bearer tt-token"
    row = result.payload["rows"][0]
    assert row["protocol_revenue"] == 12345.0
    assert row["fees"] == 25000.0


def test_crypto_defillama_reads_current_chain_tvls_when_top_level_tvl_missing() -> None:
    plugin = DefiLlamaCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"chains":["Bitcoin","Ethereum"],"currentChainTvls":{"Bitcoin":100.5,"Ethereum":"20.25"},"category":"Yield"}',
        )
    )
    resolver = SimpleNamespace(get_endpoint_url=lambda name: "https://api.llama.fi" if name == "data_source:defillama" else None)
    task = FetchTask(
        batch_id="batch:defillama",
        provider_id="crypto_defillama_defi",
        endpoint_id="protocol",
        market="CRYPTO",
        data_type="defi_metric",
        granularity="realtime",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("tvl", "chains", "category", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert result.payload["rows"][0]["tvl"] == 120.75
    assert result.payload["rows"][0]["category"] == "Yield"


def test_crypto_coinglass_declares_memory_required_paid_domains() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in CoinglassCryptoPlugin().capabilities().endpoints}

    assert {
        "futures_open_interest",
        "futures_funding_rate",
        "futures_long_short_ratio",
        "futures_taker_buy_sell",
        "futures_liquidation",
        "futures_price_history",
        "spot_price_history",
        "futures_order_book",
        "spot_order_book",
        "onchain_exchange_balance",
        "onchain_whale_transfer",
        "spot_coin_netflow",
        "futures_coin_netflow",
        "bitcoin_ahr999",
    } <= set(endpoints)
    assert endpoints["bitcoin_ahr999"].data_type == "crypto_onchain_metric"
    assert "value_unit" in endpoints["bitcoin_ahr999"].fields
    assert endpoints["spot_order_book"].data_type == "order_book_snapshot"


def test_crypto_coinglass_exchange_balance_data_map_returns_value_and_source_field() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"time_list":[1772150400000,1772236800000],"price_list":[60000,61000],"data_map":{"Binance":[1000,1250],"OKX":[800]}}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-exchange-balance",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="onchain_exchange_balance",
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        granularity="daily",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 2),
        fields=("timestamp", "metric", "value", "value_unit", "chain"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].query == {"symbol": "BTC"}
    row = result.payload["rows"][0]
    assert row["metric"] == "exchange_balance"
    assert row["value"] == 1250.0
    assert row["value_unit"] == "BTC"
    assert row["value_source_field"] == "data_map.Binance"
    assert row["timestamp"] == datetime(2026, 2, 28, tzinfo=UTC)


def test_crypto_coinglass_spot_coin_netflow_preserves_usd_unit() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":{"net_inflow":"-31681531","time":1772236800000}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-spot-netflow",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="spot_coin_netflow",
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        granularity="realtime",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("timestamp", "metric", "value", "value_unit", "chain"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["metric"] == "spot_coin_netflow"
    assert row["value"] == -31681531.0
    assert row["value_unit"] == "USD"


def test_crypto_coinglass_ahr999_preserves_dimensionless_unit() -> None:
    plugin = CoinglassCryptoPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":{"ahr999":"0.3187","time":1772236800000}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "coinglass-token" if name == "data_source:coinglass" else None,
        get_endpoint_url=lambda name: "https://open-api-v4.coinglass.com" if name == "data_source:coinglass" else None,
    )
    task = FetchTask(
        batch_id="batch:coinglass-ahr999",
        provider_id="crypto_coinglass_derivatives",
        endpoint_id="bitcoin_ahr999",
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        granularity="daily",
        symbol_ids=("BTC/USDT",),
        date_range_start=None,
        date_range_end=None,
        fields=("timestamp", "metric", "value", "value_unit", "chain"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["metric"] == "ahr999"
    assert row["value"] == 0.3187
    assert row["value_unit"] == "dimensionless"


def test_us_fred_requires_series_id_in_params_not_equity_symbol() -> None:
    plugin = USFREDMacroPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"observations":[{"date":"2026-05-01","value":"5.25"}]}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "fred-token" if name == "data_source:fred" else None,
        get_endpoint_url=lambda name: "https://fred.example" if name == "data_source:fred" else None,
    )
    task = FetchTask(
        batch_id="batch:fred",
        provider_id="us_fred_macro",
        endpoint_id="series_observations",
        market="US",
        data_type="macro_series",
        granularity="daily",
        symbol_ids=("FEDFUNDS",),
        date_range_start=None,
        date_range_end=None,
        fields=("series_id", "date", "value", "unit", "region"),
        provider_config_version="1.0.0",
        params={"series_id": "FEDFUNDS"},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].query["series_id"] == "FEDFUNDS"

    missing = plugin.fetch(replace(task, params={}), ctx=SimpleNamespace(credential_resolver=resolver, managed_http=ManagedHttp(client)))
    assert missing.status.value == "success"
    assert client.requests[-1].query["series_id"] == "FEDFUNDS"


def test_baostock_daily_and_intraday_use_endpoint_specific_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class _Result:
        error_code = "1"
        error_msg = "stop_after_fields"

    class _Login:
        error_code = "0"

    fake_bs = SimpleNamespace(
        login=lambda: _Login(),
        logout=lambda: None,
        query_history_k_data_plus=lambda code, fields, **kwargs: calls.append((fields, str(kwargs["frequency"]))) or _Result(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)
    plugin = BaostockCNProviderPlugin()

    daily_task = FetchTask(
        batch_id="batch:bs",
        provider_id="cn_a_baostock_market",
        endpoint_id="daily_bar",
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "close"),
        provider_config_version="1.0.0",
        params={},
    )
    intraday_task = replace(daily_task, endpoint_id="intraday_bar", data_type="intraday_bar", granularity="intraday")

    plugin.fetch(daily_task, ctx=SimpleNamespace())
    plugin.fetch(intraday_task, ctx=SimpleNamespace())

    daily_fields, daily_frequency = calls[0]
    intraday_fields, intraday_frequency = calls[1]
    assert daily_frequency == "d"
    assert "time" not in daily_fields.split(",")
    assert "turn" in daily_fields.split(",")
    assert intraday_frequency == "5"
    assert "time" in intraday_fields.split(",")
    assert "turn" not in intraday_fields.split(",")
    assert "peTTM" not in intraday_fields.split(",")


def test_baostock_fetch_sets_and_restores_socket_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import claw_trade.data_gateway.providers.plugins.cn_a.provider_matrix as cn_a_matrix

    timeout_calls: list[float | None] = []

    class _Result:
        error_code = "1"
        error_msg = "stop_after_timeout_check"

    class _Login:
        error_code = "0"

    fake_bs = SimpleNamespace(
        login=lambda: _Login(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: _Result(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)
    monkeypatch.setattr(cn_a_matrix.socket, "getdefaulttimeout", lambda: 12.0)
    monkeypatch.setattr(cn_a_matrix.socket, "setdefaulttimeout", lambda value: timeout_calls.append(value))

    plugin = BaostockCNProviderPlugin()
    task = FetchTask(
        batch_id="batch:bs-timeout",
        provider_id="cn_a_baostock_market",
        endpoint_id="daily_bar",
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("open", "close"),
        provider_config_version="1.0.0",
        params={},
    )

    plugin.fetch(task, ctx=SimpleNamespace())

    assert timeout_calls == [8.0, 12.0]


def test_mootdx_factory_uses_explicit_socket_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    factory_kwargs: list[dict[str, object]] = []

    class _Client:
        def quotes(self, **kwargs: object) -> list[dict[str, object]]:
            return []

        def close(self) -> None:
            return None

    class _Quotes:
        @staticmethod
        def factory(**kwargs: object) -> _Client:
            factory_kwargs.append(dict(kwargs))
            return _Client()

    monkeypatch.setitem(sys.modules, "mootdx.quotes", SimpleNamespace(Quotes=_Quotes))
    monkeypatch.setenv("CLAW_TRADE_MOOTDX_FETCH_TIMEOUT_SECONDS", "0")
    plugin = MootdxCNProviderPlugin()
    task = FetchTask(
        batch_id="batch:mootdx-timeout",
        provider_id="cn_a_mootdx_market",
        endpoint_id="quote_snapshot",
        market="CN_A",
        data_type="quote_snapshot",
        granularity="realtime",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("price", "timestamp"),
        provider_config_version="1.0.0",
        params={},
    )

    plugin.fetch(task, ctx=SimpleNamespace())

    assert factory_kwargs[0]["timeout"] == 5.0


def test_mootdx_fetch_returns_provider_error_when_sdk_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    if "fork" not in mp.get_all_start_methods():
        pytest.skip("mootdx hard timeout uses forked provider process")

    def _hang(self: MootdxCNProviderPlugin, task: object) -> object:
        del self, task
        time.sleep(5)

    monkeypatch.setattr(MootdxCNProviderPlugin, "_fetch_direct", _hang)
    monkeypatch.setenv("CLAW_TRADE_MOOTDX_FETCH_TIMEOUT_SECONDS", "0.2")
    task = FetchTask(
        batch_id="batch:mootdx-hang",
        provider_id="cn_a_mootdx_market",
        endpoint_id="quote_snapshot",
        market="CN_A",
        data_type="quote_snapshot",
        granularity="realtime",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("price", "timestamp"),
        provider_config_version="1.0.0",
        params={},
    )

    result = MootdxCNProviderPlugin().fetch(task, ctx=SimpleNamespace())

    assert result.status.value == "error"
    assert result.error_message == "mootdx_fetch_timeout:0.2s"


def test_cn_a_social_signal_declares_text_and_heat_sources_without_full_sentiment_claim() -> None:
    registry = ProviderRegistry()
    for plugin in iter_minimal_market_plugins():
        registry.register(plugin)

    capabilities = registry.list_capabilities("CN_A", "social_signal")

    assert {
        (cap.provider_id, cap.endpoint_id)
        for cap in capabilities
    } >= {
        ("cn_a_tushare_fundamental", "investor_interaction"),
        ("cn_a_akshare_social_news", "stock_hot_rank_latest_em"),
        ("cn_a_akshare_social_news", "stock_hot_keyword_em"),
        ("cn_a_akshare_social_news", "stock_hot_rank_relate_em"),
        ("cn_a_astock_signal_social", "ths_hot_reason"),
        ("cn_a_astock_signal_social", "baidu_concept_blocks"),
    }
    assert all(cap.source_role == "sentiment" for cap in capabilities)
    assert all(cap.can_be_formal_fact_source is False for cap in capabilities)
    assert not any({"score", "mentions", "sentiment"}.issubset(set(cap.fields)) for cap in capabilities)


def test_cn_a_tushare_fundamental_reads_settings_credential_and_parses_metric() -> None:
    plugin = TushareFundamentalPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"code":0,"msg":"","data":{"fields":["ts_code","end_date","roe","roa","grossprofit_margin","debt_to_assets","eps"],"items":[["600519.SH","20260331",28.1,17.2,91.5,24.0,12.3]]}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a-fundamental",
        provider_id="cn_a_tushare_fundamental",
        endpoint_id="financial_metric",
        market="CN_A",
        data_type="financial_metric",
        granularity="quarterly",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    assert client.requests[0].host == "http://tushare.example:8020"
    body = json.loads(str(client.requests[0].body))
    assert body["api_name"] == "fina_indicator"
    assert body["token"] == "ts-token"
    row = result.payload["rows"][0]
    assert row["dataset"] == "financial_metric"
    assert row["symbol_id"] == "600519.SH"
    assert row["roe"] == 28.1
    assert row["gross_margin"] == 91.5
    assert row["currency"] == "CNY"


def test_cn_a_tushare_fundamental_missing_credential_fails_closed() -> None:
    plugin = TushareFundamentalPlugin()
    task = FetchTask(
        batch_id="batch:cn-a-fundamental",
        provider_id="cn_a_tushare_fundamental",
        endpoint_id="financial_metric",
        market="CN_A",
        data_type="financial_metric",
        granularity="quarterly",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("roe",),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace())

    assert result.status.value == "credential_missing"


def test_cn_a_tushare_anns_d_fetches_official_filing_url_without_claiming_body_text() -> None:
    plugin = TushareFundamentalPlugin()
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in plugin.capabilities().endpoints}
    assert endpoints["anns_d"].data_type == "official_filing"
    assert endpoints["anns_d"].source_role == "paid_data"

    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"code":0,"msg":"","data":{"fields":["ts_code","ann_date","ann_time","title","url"],'
                '"items":[["600519.SH","20260529","2026-05-29 18:30:00","年度权益分派公告","https://example.test/a.pdf"]]}}'
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a-filing",
        provider_id="cn_a_tushare_fundamental",
        endpoint_id="anns_d",
        market="CN_A",
        data_type="official_filing",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("title", "published_at", "url", "source", "body_ref", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    body = json.loads(str(client.requests[0].body))
    assert body["api_name"] == "anns_d"
    assert body["params"] == {"ts_code": "600519.SH", "start_date": "20260501", "end_date": "20260531"}
    row = result.payload["rows"][0]
    assert row["dataset"] == "official_filing"
    assert row["source_roles"] == ("paid_data",)
    assert row["title"] == "年度权益分派公告"
    assert row["body_ref"] == "https://example.test/a.pdf"
    assert row["quality_flags"] == ("paid_aggregator_official_filing_url",)


def test_cn_a_tushare_investor_interaction_fetches_text_material_without_sentiment_score() -> None:
    plugin = TushareFundamentalPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"code":0,"msg":"","data":{"fields":["ts_code","name","trade_date","q","a","pub_time"],"items":[["600519.SH","贵州茅台","20260529","请问分红计划？","请关注公告。","2026-05-29 15:30:00"]]}}',
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a-social",
        provider_id="cn_a_tushare_fundamental",
        endpoint_id="investor_interaction",
        market="CN_A",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("source", "timestamp", "question", "answer", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    body = json.loads(str(client.requests[0].body))
    assert body["api_name"] == "irm_qa_sh"
    row = result.payload["rows"][0]
    assert row["dataset"] == "social_signal"
    assert row["source"] == "irm_qa_sh"
    assert row["question"] == "请问分红计划？"
    assert row["answer"] == "请关注公告。"
    assert "score" not in row
    assert "sentiment" not in row


def test_cn_a_tushare_registers_and_fetches_paid_fund_flow_before_public_sources() -> None:
    plugin = TushareFundamentalPlugin()
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in plugin.capabilities().endpoints}
    assert endpoints["moneyflow"].data_type == "capital_flow"
    assert endpoints["moneyflow"].source_role == "paid_data"
    assert endpoints["moneyflow_ind_dc"].data_type == "sector_snapshot"
    assert endpoints["moneyflow_ind_dc"].source_role == "paid_data"

    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","buy_sm_amount","sell_sm_amount",'
                '"buy_md_amount","sell_md_amount","buy_lg_amount","sell_lg_amount","buy_elg_amount","sell_elg_amount",'
                '"net_mf_amount"],"items":[["600519.SH","20260529",12,2,20,5,30,10,40,15,70]]}}'
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a-moneyflow",
        provider_id="cn_a_tushare_fundamental",
        endpoint_id="moneyflow",
        market="CN_A",
        data_type="capital_flow",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("date", "main_net", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    body = json.loads(str(client.requests[0].body))
    assert body["api_name"] == "moneyflow"
    assert body["params"] == {"ts_code": "600519.SH", "start_date": "20260501", "end_date": "20260531"}
    row = result.payload["rows"][0]
    assert row["dataset"] == "capital_flow"
    assert row["source_roles"] == ("paid_data",)
    assert row["main_net"] == 70.0
    assert row["small_net"] == 10.0
    assert row["large_net"] == 20.0
    assert row["super_net"] == 25.0


def test_cn_a_tushare_sector_fund_flow_does_not_send_stock_code_filter() -> None:
    plugin = TushareFundamentalPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"code":0,"msg":"","data":{"fields":["trade_date","content_type","ts_code","name","pct_change","close",'
                '"net_amount","net_amount_rate","buy_elg_amount","buy_lg_amount","buy_md_amount","buy_sm_amount","rank"],'
                '"items":[["20260529","行业","BK0475","白酒",1.2,3000,123456789,3.5,100,90,80,70,1]]}}'
            ),
        )
    )
    resolver = SimpleNamespace(
        get_credential=lambda name: "ts-token" if name == "data_source:tushare" else None,
        get_endpoint_url=lambda name: "http://tushare.example:8020" if name == "data_source:tushare" else None,
    )
    task = FetchTask(
        batch_id="batch:cn-a-sector-flow",
        provider_id="cn_a_tushare_fundamental",
        endpoint_id="moneyflow_ind_dc",
        market="CN_A",
        data_type="sector_snapshot",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=date(2026, 5, 31),
        fields=("sector_name", "main_net", "timestamp"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=resolver))

    assert result.status.value == "success"
    body = json.loads(str(client.requests[0].body))
    assert body["api_name"] == "moneyflow_ind_dc"
    assert body["params"] == {"end_date": "20260531", "content_type": "行业"}
    row = result.payload["rows"][0]
    assert row["dataset"] == "sector_snapshot"
    assert row["sector_code"] == "BK0475"
    assert row["sector_name"] == "白酒"
    assert row["main_net"] == 123456789.0
    assert row["source_roles"] == ("paid_data",)


def test_cn_a_akshare_fund_flow_sdk_is_registered_as_public_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = AkShareSocialNewsPlugin()
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in plugin.capabilities().endpoints}
    assert endpoints["stock_individual_fund_flow"].data_type == "capital_flow"
    assert endpoints["stock_sector_fund_flow_rank"].data_type == "sector_snapshot"

    fake_akshare = SimpleNamespace(
        stock_individual_fund_flow=lambda **_: [
            {
                "日期": "2026-05-29",
                "主力净流入-净额": 1000,
                "超大单净流入-净额": 400,
                "大单净流入-净额": 300,
                "中单净流入-净额": 200,
                "小单净流入-净额": 100,
            }
        ],
        stock_sector_fund_flow_rank=lambda **_: [
            {
                "序号": 1,
                "名称": "白酒",
                "主力净流入-净额": 5000,
                "超大单净流入-净额": 2000,
                "大单净流入-净额": 1500,
                "中单净流入-净额": 1000,
                "小单净流入-净额": 500,
            }
        ],
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    ctx = SimpleNamespace(managed_http=ManagedHttp(_RecordingHttpClient(_HttpResponse(status_code=200, headers={}, text="{}"))))
    stock_task = FetchTask(
        batch_id="batch:ak-flow",
        provider_id="cn_a_akshare_social_news",
        endpoint_id="stock_individual_fund_flow",
        market="CN_A",
        data_type="capital_flow",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("date", "main_net", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )
    sector_task = replace(
        stock_task,
        batch_id="batch:ak-sector-flow",
        endpoint_id="stock_sector_fund_flow_rank",
        data_type="sector_snapshot",
        granularity="event",
        fields=("sector_name", "main_net", "timestamp"),
    )

    stock_result = plugin.fetch(stock_task, ctx=ctx)
    sector_result = plugin.fetch(sector_task, ctx=ctx)

    assert stock_result.status.value == "success"
    assert stock_result.payload["rows"][0]["main_net"] == 1000.0
    assert sector_result.status.value == "success"
    assert sector_result.payload["rows"][0]["sector_name"] == "白酒"
    assert sector_result.payload["rows"][0]["quality_flags"] == ("sector_code_not_provided_by_source",)


def test_cn_a_akshare_social_fetches_heat_keyword_through_managed_http() -> None:
    plugin = AkShareSocialNewsPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":[{"time":"2026-06-01 10:00:00","sc":"SH600519","name":"白酒","code":"BK0896","heat":10836,"flag":"x"}]}',
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-akshare-social",
        provider_id="cn_a_akshare_social_news",
        endpoint_id="stock_hot_keyword_em",
        market="CN_A",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("source", "timestamp", "keyword", "score", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://emappdata.eastmoney.com"
    assert request.path == "/stockrank/getHotStockRankList"
    assert json.loads(str(request.body))["srcSecurityCode"] == "SH600519"
    row = result.payload["rows"][0]
    assert row["dataset"] == "social_signal"
    assert row["source"] == "stock_hot_keyword_em"
    assert row["keyword"] == "白酒"
    assert row["score"] == 10836.0
    assert row["quality_flags"] == ("aggregate_signal_without_sentiment_label",)


def test_cn_a_akshare_declares_public_market_data_interfaces_without_sdk_black_box() -> None:
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in AkShareSocialNewsPlugin().capabilities().endpoints}

    assert endpoints["stock_zh_a_hist"].data_type == "daily_bar"
    assert endpoints["stock_zh_a_hist"].http_visibility == "managed_http"
    assert endpoints["stock_zh_a_spot_em"].data_type == "quote_snapshot"
    assert endpoints["stock_zh_a_spot_em"].batch_policy.supports_batch is True
    assert endpoints["stock_individual_info_em"].data_type == "valuation_metric"


def test_cn_a_akshare_stock_zh_a_hist_uses_auditable_eastmoney_request_shape() -> None:
    plugin = AkShareSocialNewsPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":{"klines":["2026-05-29,1600,1610,1620,1590,1000,1600000,0,0,0,0"]}}',
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-akshare-hist",
        provider_id="cn_a_akshare_social_news",
        endpoint_id="stock_zh_a_hist",
        market="CN_A",
        data_type="daily_bar",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("date", "open", "high", "low", "close", "volume", "amount", "adjustment"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://push2his.eastmoney.com"
    assert request.path == "/api/qt/stock/kline/get"
    assert request.query["secid"] == "1.600519"
    row = result.payload["rows"][0]
    assert row["provider_lineage"] == {"provider_id": "cn_a_akshare_social_news", "endpoint_id": "stock_zh_a_hist"}
    assert row["dataset"] == "daily_bar"
    assert row["close"] == 1610.0


def test_cn_a_astock_social_fetches_ths_hot_reason_from_reference_shape() -> None:
    plugin = AStockSignalSocialPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={"content-type": "application/json; charset=GBK"},
            text="",
            content=(
                '{"data":[{'
                '"code":"600519","name":"贵州茅台","reason":"白酒+高股息",'
                '"zhangfu":"2.5","huanshou":"0.8","chengjiaoe":"123456",'
                '"chengjiaoliang":"1000","ddejingliang":"0.12","close":"1605"'
                '}]}'
            ).encode("gb18030"),
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-astock-ths",
        provider_id="cn_a_astock_signal_social",
        endpoint_id="ths_hot_reason",
        market="CN_A",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=date(2026, 5, 29),
        fields=("source", "timestamp", "topic", "reason", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "http://zx.10jqka.com.cn"
    assert request.path == "/event/api/getharden/date/2026-05-29/orderby/date/orderway/desc/charset/GBK/"
    row = result.payload["rows"][0]
    assert row["dataset"] == "social_signal"
    assert row["source"] == "ths_hot_reason"
    assert row["topic"] == "白酒+高股息"
    assert row["reason"] == "白酒+高股息"
    assert row["change_pct"] == 2.5
    assert row["turnover_rate"] == 0.8
    assert row["quality_flags"] == ("astock_signal_without_sentiment_label", "topic_reason_signal")


def test_cn_a_astock_social_fetches_baidu_concept_blocks_from_reference_shape() -> None:
    plugin = AStockSignalSocialPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"ResultCode":0,"Result":['
                '{"type":"概念板块","list":[{"name":"白酒","increase":"1.2","desc":"消费题材"}]},'
                '{"type":"行业板块","list":[{"name":"酿酒行业","increase":"0.5","desc":"行业板块"}]}'
                ']}'
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-astock-baidu",
        provider_id="cn_a_astock_signal_social",
        endpoint_id="baidu_concept_blocks",
        market="CN_A",
        data_type="social_signal",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("source", "timestamp", "topic", "concept", "industry", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://finance.pae.baidu.com"
    assert request.path == "/api/getrelatedblock"
    assert request.query["code"] == "600519"
    assert request.query["market"] == "ab"
    rows = result.payload["rows"]
    assert rows[0]["dataset"] == "social_signal"
    assert rows[0]["source"] == "baidu_concept_blocks"
    assert rows[0]["topic"] == "白酒"
    assert rows[0]["concept"] == "白酒"
    assert rows[0]["change_pct"] == 1.2
    assert rows[0]["quality_flags"] == ("astock_signal_without_sentiment_label", "related_block_signal")
    assert rows[1]["industry"] == "酿酒行业"


def test_cn_a_eastmoney_hot_money_fetches_through_managed_http() -> None:
    plugin = EastMoneyCNEventsPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"result":{"data":[{"TRADE_DATE":"2026-05-29","OPERATEDEPT_NAME":"test seat","BILLBOARD_BUY_AMT":"1000","BILLBOARD_SELL_AMT":"250"}]}}',
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-hot-money",
        provider_id="cn_a_eastmoney_events",
        endpoint_id="hot_money_event",
        market="CN_A",
        data_type="hot_money_event",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("trade_date", "seat", "buy_amount", "sell_amount", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://datacenter-web.eastmoney.com"
    assert client.requests[0].query["reportName"] == "RPT_DAILYBILLBOARD_DETAILSNEW"
    row = result.payload["rows"][0]
    assert row["dataset"] == "hot_money_event"
    assert row["seat"] == "test seat"
    assert row["buy_amount"] == 1000.0
    assert row["sell_amount"] == 250.0


def test_cn_a_eastmoney_lockup_uses_live_free_date_fields() -> None:
    plugin = EastMoneyCNEventsPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                '{"success":true,"result":{"data":[{'
                '"FREE_DATE":"2026-06-15 00:00:00",'
                '"FREE_SHARES":"403780",'
                '"LIFT_MARKET_CAP":"1148330.17",'
                '"BATCH_HOLDER_NUM":"1",'
                '"FREE_SHARES_TYPE":"首发原股东限售股份"'
                '}]}}'
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-lockup",
        provider_id="cn_a_eastmoney_events",
        endpoint_id="lockup_event",
        market="CN_A",
        data_type="lockup_event",
        granularity="event",
        symbol_ids=("688783.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("unlock_date", "shares", "market_value", "holder"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].query["reportName"] == "RPT_LIFT_STAGE"
    assert client.requests[0].query["sortColumns"] == "FREE_DATE"
    row = result.payload["rows"][0]
    assert row["unlock_date"].isoformat() == "2026-06-15"
    assert row["shares"] == 403780.0
    assert row["market_value"] == 1148330.17
    assert row["holder"] is None
    assert row["holder_count"] == 1.0
    assert row["share_type"] == "首发原股东限售股份"
    assert row["quality_flags"] == ("holder_detail_missing",)


def test_cn_a_eastmoney_datacenter_rejects_schema_errors_not_empty() -> None:
    plugin = EastMoneyCNEventsPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"success":false,"message":"LIFT_DATE排序列不存在","code":9501}',
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-lockup-error",
        provider_id="cn_a_eastmoney_events",
        endpoint_id="lockup_event",
        market="CN_A",
        data_type="lockup_event",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("unlock_date", "shares", "market_value", "holder"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "error"
    assert result.error_message == "provider_rejected:9501:LIFT_DATE排序列不存在"
    assert result.http_observations


def test_cn_a_eastmoney_market_data_fund_flow_uses_source_request_shape() -> None:
    plugin = EastMoneyCNMarketDataPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":{"klines":["2026-05-29,100,10,20,30,40,1,0.1,0.2,0.3,0.4,1600,2.5,0,0"]}}',
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-fund-flow",
        provider_id="cn_a_eastmoney_market_data",
        endpoint_id="stock_fund_flow_daily",
        market="CN_A",
        data_type="capital_flow",
        granularity="daily",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("date", "main_net", "small_net", "mid_net", "large_net", "super_net", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://push2his.eastmoney.com"
    assert request.path == "/api/qt/stock/fflow/daykline/get"
    assert request.timeout_seconds == 6.0
    assert request.query["secid"] == "1.600519"
    row = result.payload["rows"][0]
    assert row["dataset"] == "capital_flow"
    assert row["date"].isoformat() == "2026-05-29"
    assert row["main_net"] == 100.0
    assert row["super_net"] == 40.0
    assert result.http_observations


def test_cn_a_eastmoney_spot_quote_uses_target_secids_not_first_page_scan() -> None:
    plugin = EastMoneyCNMarketDataPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text='{"data":{"f43":"1600","f170":"1.2","f169":"18","f47":"1000","f48":"1600000","f86":"1772236800"}}',
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-quote",
        provider_id="cn_a_eastmoney_market_data",
        endpoint_id="spot_quote_batch",
        market="CN_A",
        data_type="quote_snapshot",
        granularity="realtime",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://push2.eastmoney.com"
    assert request.path == "/api/qt/stock/get"
    assert request.timeout_seconds == 6.0
    assert request.query["secid"] == "1.600519"
    row = result.payload["rows"][0]
    assert row["symbol_id"] == "600519.SH"
    assert row["price"] == 1600.0


def test_cn_a_eastmoney_sector_snapshot_uses_short_http_timeout() -> None:
    plugin = EastMoneyCNMarketDataPlugin()
    client = _RecordingHttpClient(_HttpResponse(status_code=502, headers={}, text="bad gateway"))
    task = FetchTask(
        batch_id="batch:cn-a-sector",
        provider_id="cn_a_eastmoney_market_data",
        endpoint_id="sector_fund_flow_rank",
        market="CN_A",
        data_type="sector_snapshot",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("sector_name", "main_net", "timestamp"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "error"
    assert client.requests[0].timeout_seconds == 6.0


def test_cn_a_google_news_discovery_fetches_rss_without_formal_fact_claim() -> None:
    plugin = GoogleNewsDiscoveryPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text="<rss><channel><item><title>贵州茅台 news</title><link>https://example.test/news</link><pubDate>Fri, 29 May 2026 00:00:00 GMT</pubDate><description>summary</description></item></channel></rss>",
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-news",
        provider_id="cn_a_google_news",
        endpoint_id="company_news",
        market="CN_A",
        data_type="company_news",
        granularity="event",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("title", "published_at", "source", "summary", "url"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert client.requests[0].host == "https://news.google.com"
    row = result.payload["rows"][0]
    assert row["dataset"] == "company_news"
    assert row["source"] == "google_news_rss"
    assert row["quality_flags"] == ("discovery_not_formal_fact_source",)


def test_cn_a_google_news_filters_000001_index_pollution() -> None:
    plugin = GoogleNewsDiscoveryPlugin()
    client = _RecordingHttpClient(
        _HttpResponse(
            status_code=200,
            headers={},
            text=(
                "<rss><channel>"
                "<item><title>上证指数(000001)实时行情</title><link>https://example.test/index</link>"
                "<pubDate>Fri, 29 May 2026 00:00:00 GMT</pubDate><description>上证指数走势</description></item>"
                "<item><title>平安银行获机构关注</title><link>https://example.test/pab</link>"
                "<pubDate>Fri, 29 May 2026 01:00:00 GMT</pubDate><description>平安银行新闻</description></item>"
                "</channel></rss>"
            ),
        )
    )
    task = FetchTask(
        batch_id="batch:cn-a-news-000001",
        provider_id="cn_a_google_news",
        endpoint_id="company_news",
        market="CN_A",
        data_type="company_news",
        granularity="event",
        symbol_ids=("000001.SZ",),
        date_range_start=None,
        date_range_end=None,
        fields=("title", "published_at", "source", "summary", "url"),
        provider_config_version="1.0.0",
        params={},
    )

    result = plugin.fetch(task, ctx=SimpleNamespace(managed_http=ManagedHttp(client)))

    assert result.status.value == "success"
    assert "平安银行" in client.requests[0].query["q"]
    titles = [row["title"] for row in result.payload["rows"]]
    assert titles == ["平安银行获机构关注"]
