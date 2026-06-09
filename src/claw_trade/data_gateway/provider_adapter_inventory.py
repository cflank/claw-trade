from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class SourceCatalogEntry:
    source_type: str
    source_group: str
    visible_in_settings: bool
    reason: str


@dataclass(frozen=True)
class ProviderSourceMapping:
    provider_id: str
    source_types: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ImplementedAdapterCapability:
    source_types: tuple[str, ...]
    provider_id: str
    plugin_version: str
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    granularities: tuple[str, ...]
    fields: tuple[str, ...]
    credential_required: bool
    http_visibility: str
    can_be_formal_fact_source: bool


@dataclass(frozen=True)
class KnownMissingAdapterCapability:
    source_type: str
    market: str
    data_type: str
    granularity: str
    provider_endpoint_hint: str
    reason: str


_INTERNAL_SOURCE_CATALOG: tuple[SourceCatalogEntry, ...] = (
    SourceCatalogEntry("baidu_finance", "cn_a_social", False, "internal A-share related-block signal path"),
    SourceCatalogEntry("google_news", "global_news", False, "internal discovery provider used by market adapters"),
    SourceCatalogEntry("mootdx", "cn_a_data", False, "internal built-in public A-share market provider"),
    SourceCatalogEntry("project_official", "crypto_news", False, "internal official project news resolver"),
)


_PROVIDER_SOURCE_MAPPINGS: tuple[ProviderSourceMapping, ...] = (
    ProviderSourceMapping("cn_a_primary", ("tushare",), "Tushare daily A-share market adapter"),
    ProviderSourceMapping("cn_a_tushare_fundamental", ("tushare",), "Tushare A-share financial, flow, filing, and interaction adapters"),
    ProviderSourceMapping("cn_a_akshare_social_news", ("akshare",), "AKShare A-share market, news, social, and flow adapters"),
    ProviderSourceMapping("cn_a_astock_signal_social", ("tonghuashun", "baidu_finance"), "A-share signal adapter backed by Tonghuashun and Baidu Finance paths"),
    ProviderSourceMapping("cn_a_baostock_market", ("baostock",), "BaoStock A-share market and financial adapters"),
    ProviderSourceMapping("cn_a_cninfo_events", ("cninfo",), "CNInfo event and filing adapters"),
    ProviderSourceMapping("cn_a_eastmoney_events", ("eastmoney",), "EastMoney A-share event adapters"),
    ProviderSourceMapping("cn_a_eastmoney_market_data", ("eastmoney",), "EastMoney A-share market, flow, valuation, and macro-news adapters"),
    ProviderSourceMapping("cn_a_google_news", ("google_news",), "Google News A-share discovery adapter"),
    ProviderSourceMapping("cn_a_mootdx_market", ("mootdx",), "Mootdx A-share market adapter"),
    ProviderSourceMapping("hk_akshare_fundamental", ("akshare",), "AKShare HK financial adapter"),
    ProviderSourceMapping("hk_finnhub_data", ("finnhub",), "Finnhub HK data adapter"),
    ProviderSourceMapping("hk_google_news", ("google_news",), "Google News HK discovery adapter"),
    ProviderSourceMapping("hk_hkexnews_official", ("hkex",), "HKEX official filing adapter"),
    ProviderSourceMapping("hk_sina_public", ("sina_finance",), "Sina HK public market adapter"),
    ProviderSourceMapping("hk_tushare", ("tushare",), "Tushare HK market and financial adapter"),
    ProviderSourceMapping("hk_yahoo_finance", ("yahoo_finance",), "Yahoo Finance HK adapter"),
    ProviderSourceMapping("us_alpha_vantage_data", ("alpha_vantage",), "Alpha Vantage US data adapter"),
    ProviderSourceMapping("us_finnhub_data", ("finnhub",), "Finnhub US data adapter"),
    ProviderSourceMapping("us_fred_macro", ("fred",), "FRED macro adapter"),
    ProviderSourceMapping("us_google_news", ("google_news",), "Google News US discovery adapter"),
    ProviderSourceMapping("us_primary", ("alpha_vantage",), "Alpha Vantage US primary daily bar adapter"),
    ProviderSourceMapping("us_sec_official", ("sec_edgar",), "SEC EDGAR official filing adapter"),
    ProviderSourceMapping("us_stocktwits_social", ("stocktwits",), "Stocktwits social adapter"),
    ProviderSourceMapping("us_yahoo_finance", ("yahoo_finance",), "Yahoo Finance US adapter"),
    ProviderSourceMapping("crypto_alternative_me_sentiment", ("alternative_me",), "Alternative.me crypto sentiment adapter"),
    ProviderSourceMapping("crypto_binance_spot_market", ("binance",), "Binance spot market adapter"),
    ProviderSourceMapping("crypto_coingecko_market", ("coingecko", "coingecko_pro"), "CoinGecko market adapter"),
    ProviderSourceMapping("crypto_coinglass_derivatives", ("coinglass",), "Coinglass derivatives, ETF flow, order-flow, and on-chain adapter"),
    ProviderSourceMapping("crypto_defillama_defi", ("defillama",), "DeFiLlama protocol adapter"),
    ProviderSourceMapping("crypto_glassnode_onchain", ("glassnode",), "Glassnode on-chain adapter"),
    ProviderSourceMapping("crypto_google_news", ("google_news",), "Google News crypto discovery adapter"),
    ProviderSourceMapping("crypto_lunarcrush_social", ("lunarcrush",), "LunarCrush social adapter"),
    ProviderSourceMapping("crypto_primary", ("binance",), "Binance spot daily bar adapter"),
    ProviderSourceMapping("crypto_project_official_news", ("project_official",), "Official project release/news adapter"),
    ProviderSourceMapping("crypto_token_terminal_fundamentals", ("token_terminal",), "Token Terminal fundamentals adapter"),
)


_KNOWN_MISSING_ADAPTERS: tuple[KnownMissingAdapterCapability, ...] = (
    KnownMissingAdapterCapability(
        source_type="tushare",
        market="CN_A",
        data_type="quote_snapshot",
        granularity="realtime",
        provider_endpoint_hint="realtime_quote",
        reason="Tushare official realtime quote endpoint exists, but no claw-trade adapter is implemented yet",
    ),
    KnownMissingAdapterCapability(
        source_type="tushare",
        market="CN_A",
        data_type="order_book_snapshot",
        granularity="realtime",
        provider_endpoint_hint="realtime_quote",
        reason="Tushare realtime quote includes bid/ask levels, but no claw-trade order-book adapter is implemented yet",
    ),
    KnownMissingAdapterCapability(
        source_type="tushare",
        market="CN_A",
        data_type="intraday_bar",
        granularity="intraday",
        provider_endpoint_hint="intraday minute/pro_bar",
        reason="Tushare minute data exists, but no claw-trade intraday adapter is implemented yet",
    ),
    KnownMissingAdapterCapability(
        source_type="mootdx",
        market="CN_A",
        data_type="trade_tick",
        granularity="realtime",
        provider_endpoint_hint="transaction/transactions",
        reason="Mootdx tick transaction interfaces are known from project memory, but claw-trade has no normalized adapter schema yet",
    ),
)


def iter_internal_source_catalog_entries() -> tuple[SourceCatalogEntry, ...]:
    return _INTERNAL_SOURCE_CATALOG


def iter_provider_source_mappings() -> tuple[ProviderSourceMapping, ...]:
    return _PROVIDER_SOURCE_MAPPINGS


def known_missing_adapter_capabilities() -> tuple[KnownMissingAdapterCapability, ...]:
    return _KNOWN_MISSING_ADAPTERS


def source_types_for_provider(provider_id: str) -> tuple[str, ...]:
    for mapping in _PROVIDER_SOURCE_MAPPINGS:
        if mapping.provider_id == provider_id:
            return mapping.source_types
    return ()


def implemented_adapters_from_registry(registry: Any) -> tuple[ImplementedAdapterCapability, ...]:
    return tuple(_adapter_from_capability(cap) for cap in registry.list_all_capabilities())


def unmapped_provider_ids(registry: Any) -> tuple[str, ...]:
    mapped = {mapping.provider_id for mapping in _PROVIDER_SOURCE_MAPPINGS}
    actual = {str(cap.provider_id) for cap in registry.list_all_capabilities()}
    return tuple(sorted(actual - mapped))


def provider_mapping_without_capability(registry: Any) -> tuple[str, ...]:
    actual = {str(cap.provider_id) for cap in registry.list_all_capabilities()}
    mapped = {mapping.provider_id for mapping in _PROVIDER_SOURCE_MAPPINGS}
    return tuple(sorted(mapped - actual))


def _adapter_from_capability(capability: Any) -> ImplementedAdapterCapability:
    source_types = source_types_for_provider(str(capability.provider_id))
    return ImplementedAdapterCapability(
        source_types=source_types,
        provider_id=str(capability.provider_id),
        plugin_version=str(capability.plugin_version),
        endpoint_id=str(capability.endpoint_id),
        market=str(capability.market),
        data_type=str(capability.data_type),
        source_role=str(capability.source_role),
        granularities=_strings(capability.supported_granularities),
        fields=_strings(capability.coverage_fields),
        credential_required=bool(capability.credential_required),
        http_visibility=str(capability.http_visibility),
        can_be_formal_fact_source=bool(capability.can_be_formal_fact_source),
    )


def _strings(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)
