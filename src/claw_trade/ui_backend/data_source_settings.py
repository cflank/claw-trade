from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from typing import Any, Callable, Mapping
from uuid import uuid4

from claw_trade.data_gateway.models import ProviderDisplayDecision, ProviderDisplayStatus
from claw_trade.data_gateway.providers.market_policy import default_market_policy_registry, ui_display_decisions
from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError
from claw_trade.ui_contracts.scope_guard import FirstVersionScopeError
from claw_trade.ui_contracts.scope_guard import assert_data_source_type_supported

@dataclass(frozen=True)
class _SupportedSourceProfile:
    supported_type: str
    group: str
    default_display_name: str
    requires_key: bool
    env_key_map: dict[str, str]


_SUPPORTED_SOURCE_PROFILES: tuple[_SupportedSourceProfile, ...] = (
    _SupportedSourceProfile(
        supported_type="tushare",
        group="cn_a_data",
        default_display_name="Tushare",
        requires_key=True,
        env_key_map={"api_key": "TUSHARE_TOKEN", "endpoint_url": "TUSHARE_HTTP_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="akshare",
        group="cn_a_data",
        default_display_name="AKShare",
        requires_key=False,
        env_key_map={"endpoint_url": "AKSHARE_HTTP_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eastmoney",
        group="cn_a_data",
        default_display_name="东方财富",
        requires_key=False,
        env_key_map={"endpoint_url": "EASTMONEY_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eastmoney_guba",
        group="cn_a_social",
        default_display_name="东方财富股吧",
        requires_key=False,
        env_key_map={"endpoint_url": "EASTMONEY_GUBA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="xueqiu",
        group="cn_a_social",
        default_display_name="雪球",
        requires_key=False,
        env_key_map={"api_key": "XUEQIU_TOKEN", "endpoint_url": "XUEQIU_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="baostock",
        group="cn_a_data",
        default_display_name="BaoStock",
        requires_key=False,
        env_key_map={"endpoint_url": "BAOSTOCK_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="tonghuashun",
        group="cn_a_social",
        default_display_name="同花顺",
        requires_key=False,
        env_key_map={"api_key": "TONGHUASHUN_TOKEN", "endpoint_url": "TONGHUASHUN_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="cninfo",
        group="cn_a_news",
        default_display_name="巨潮资讯",
        requires_key=False,
        env_key_map={"endpoint_url": "CNINFO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="sina_finance",
        group="cn_a_data",
        default_display_name="新浪财经",
        requires_key=False,
        env_key_map={"endpoint_url": "SINA_FINANCE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="tencent_finance",
        group="cn_a_data",
        default_display_name="腾讯财经",
        requires_key=False,
        env_key_map={"endpoint_url": "TENCENT_FINANCE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="csindex",
        group="cn_a_data",
        default_display_name="中证指数",
        requires_key=False,
        env_key_map={"endpoint_url": "CSINDEX_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="csmar",
        group="cn_a_data",
        default_display_name="CSMAR",
        requires_key=True,
        env_key_map={"api_key": "CSMAR_API_KEY", "endpoint_url": "CSMAR_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="resset",
        group="cn_a_data",
        default_display_name="RESSET",
        requires_key=True,
        env_key_map={"api_key": "RESSET_API_KEY", "endpoint_url": "RESSET_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="joinquant",
        group="cn_a_data",
        default_display_name="JoinQuant",
        requires_key=True,
        env_key_map={"api_key": "JOINQUANT_TOKEN", "endpoint_url": "JOINQUANT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="ricequant",
        group="cn_a_data",
        default_display_name="RiceQuant",
        requires_key=True,
        env_key_map={"api_key": "RICEQUANT_TOKEN", "endpoint_url": "RICEQUANT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="wind",
        group="cn_a_data",
        default_display_name="Wind",
        requires_key=True,
        env_key_map={"api_key": "WIND_API_KEY", "endpoint_url": "WIND_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="choice",
        group="cn_a_data",
        default_display_name="Choice",
        requires_key=True,
        env_key_map={"api_key": "CHOICE_API_KEY", "endpoint_url": "CHOICE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="ifind",
        group="cn_a_data",
        default_display_name="iFinD",
        requires_key=True,
        env_key_map={"api_key": "IFIND_API_KEY", "endpoint_url": "IFIND_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="hkex",
        group="hk_data",
        default_display_name="HKEX",
        requires_key=False,
        env_key_map={"endpoint_url": "HKEX_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="aastocks",
        group="hk_data",
        default_display_name="AASTOCKS",
        requires_key=False,
        env_key_map={"endpoint_url": "AASTOCKS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="futu",
        group="global_data",
        default_display_name="富途 OpenD",
        requires_key=False,
        env_key_map={"api_key": "FUTU_API_KEY", "endpoint_url": "FUTU_OPEND_HOST"},
    ),
    _SupportedSourceProfile(
        supported_type="longport",
        group="global_data",
        default_display_name="LongPort",
        requires_key=True,
        env_key_map={"api_key": "LONGPORT_APP_SECRET", "endpoint_url": "LONGPORT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="yahoo_finance",
        group="global_data",
        default_display_name="Yahoo Finance",
        requires_key=False,
        env_key_map={"endpoint_url": "YAHOO_FINANCE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="openbb",
        group="global_data",
        default_display_name="OpenBB",
        requires_key=False,
        env_key_map={"api_key": "OPENBB_API_KEY", "endpoint_url": "OPENBB_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="iex_cloud",
        group="global_data",
        default_display_name="IEX Cloud",
        requires_key=True,
        env_key_map={"api_key": "IEX_CLOUD_API_KEY", "endpoint_url": "IEX_CLOUD_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="twelve_data",
        group="global_data",
        default_display_name="Twelve Data",
        requires_key=True,
        env_key_map={"api_key": "TWELVE_DATA_API_KEY", "endpoint_url": "TWELVE_DATA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="marketstack",
        group="global_data",
        default_display_name="Marketstack",
        requires_key=True,
        env_key_map={"api_key": "MARKETSTACK_API_KEY", "endpoint_url": "MARKETSTACK_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eodhd",
        group="global_data",
        default_display_name="EODHD",
        requires_key=True,
        env_key_map={"api_key": "EODHD_API_KEY", "endpoint_url": "EODHD_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="intrinio",
        group="global_data",
        default_display_name="Intrinio",
        requires_key=True,
        env_key_map={"api_key": "INTRINIO_API_KEY", "endpoint_url": "INTRINIO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="alpha_vantage",
        group="global_data",
        default_display_name="Alpha Vantage",
        requires_key=True,
        env_key_map={"api_key": "ALPHA_VANTAGE_API_KEY", "endpoint_url": "ALPHA_VANTAGE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="fmp",
        group="global_data",
        default_display_name="FMP",
        requires_key=True,
        env_key_map={"api_key": "FMP_API_KEY", "endpoint_url": "FMP_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="polygon",
        group="global_data",
        default_display_name="Polygon",
        requires_key=True,
        env_key_map={"api_key": "POLYGON_API_KEY", "endpoint_url": "POLYGON_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="finnhub",
        group="global_data",
        default_display_name="Finnhub",
        requires_key=True,
        env_key_map={"api_key": "FINNHUB_TOKEN", "endpoint_url": "FINNHUB_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="tiingo",
        group="global_data",
        default_display_name="Tiingo",
        requires_key=True,
        env_key_map={"api_key": "TIINGO_TOKEN", "endpoint_url": "TIINGO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="nasdaq_data_link",
        group="global_data",
        default_display_name="Nasdaq Data Link",
        requires_key=True,
        env_key_map={"api_key": "NASDAQ_DATA_LINK_API_KEY", "endpoint_url": "NASDAQ_DATA_LINK_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="bloomberg",
        group="global_data",
        default_display_name="Bloomberg",
        requires_key=True,
        env_key_map={"api_key": "BLOOMBERG_API_KEY", "endpoint_url": "BLOOMBERG_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="lseg_refinitiv",
        group="global_data",
        default_display_name="LSEG Refinitiv",
        requires_key=True,
        env_key_map={"api_key": "LSEG_REFINITIV_API_KEY", "endpoint_url": "LSEG_REFINITIV_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="factset",
        group="global_data",
        default_display_name="FactSet",
        requires_key=True,
        env_key_map={"api_key": "FACTSET_API_KEY", "endpoint_url": "FACTSET_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="morningstar",
        group="global_data",
        default_display_name="Morningstar",
        requires_key=True,
        env_key_map={"api_key": "MORNINGSTAR_API_KEY", "endpoint_url": "MORNINGSTAR_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="sp_capital_iq",
        group="global_data",
        default_display_name="S&P Capital IQ",
        requires_key=True,
        env_key_map={"api_key": "SP_CAPITAL_IQ_API_KEY", "endpoint_url": "SP_CAPITAL_IQ_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="tradingview",
        group="global_data",
        default_display_name="TradingView",
        requires_key=True,
        env_key_map={"api_key": "TRADINGVIEW_API_KEY", "endpoint_url": "TRADINGVIEW_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="barchart",
        group="global_data",
        default_display_name="Barchart",
        requires_key=True,
        env_key_map={"api_key": "BARCHART_API_KEY", "endpoint_url": "BARCHART_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="finazon",
        group="global_data",
        default_display_name="Finazon",
        requires_key=True,
        env_key_map={"api_key": "FINAZON_API_KEY", "endpoint_url": "FINAZON_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="sec_edgar",
        group="global_news",
        default_display_name="SEC EDGAR",
        requires_key=False,
        env_key_map={"endpoint_url": "SEC_EDGAR_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="benzinga",
        group="global_news",
        default_display_name="Benzinga",
        requires_key=True,
        env_key_map={"api_key": "BENZINGA_API_KEY", "endpoint_url": "BENZINGA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="newsapi",
        group="global_news",
        default_display_name="NewsAPI",
        requires_key=True,
        env_key_map={"api_key": "NEWSAPI_KEY", "endpoint_url": "NEWSAPI_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="reuters",
        group="global_news",
        default_display_name="Reuters",
        requires_key=True,
        env_key_map={"api_key": "REUTERS_API_KEY", "endpoint_url": "REUTERS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="dow_jones",
        group="global_news",
        default_display_name="Dow Jones",
        requires_key=True,
        env_key_map={"api_key": "DOW_JONES_API_KEY", "endpoint_url": "DOW_JONES_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="gdelt",
        group="global_news",
        default_display_name="GDELT",
        requires_key=False,
        env_key_map={"endpoint_url": "GDELT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="x",
        group="global_social",
        default_display_name="X.com",
        requires_key=True,
        env_key_map={"api_key": "X_BEARER_TOKEN", "endpoint_url": "X_API_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="reddit",
        group="global_social",
        default_display_name="Reddit",
        requires_key=True,
        env_key_map={"api_key": "REDDIT_CLIENT_SECRET", "endpoint_url": "REDDIT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="stocktwits",
        group="global_social",
        default_display_name="Stocktwits",
        requires_key=False,
        env_key_map={"endpoint_url": "STOCKTWITS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="fred",
        group="global_macro",
        default_display_name="FRED",
        requires_key=True,
        env_key_map={"api_key": "FRED_API_KEY", "endpoint_url": "FRED_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="world_bank",
        group="global_macro",
        default_display_name="World Bank",
        requires_key=False,
        env_key_map={"endpoint_url": "WORLD_BANK_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="oecd",
        group="global_macro",
        default_display_name="OECD",
        requires_key=False,
        env_key_map={"endpoint_url": "OECD_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="imf",
        group="global_macro",
        default_display_name="IMF",
        requires_key=False,
        env_key_map={"endpoint_url": "IMF_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="bis",
        group="global_macro",
        default_display_name="BIS",
        requires_key=False,
        env_key_map={"endpoint_url": "BIS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eurostat",
        group="global_macro",
        default_display_name="Eurostat",
        requires_key=False,
        env_key_map={"endpoint_url": "EUROSTAT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="ecb",
        group="global_macro",
        default_display_name="ECB",
        requires_key=False,
        env_key_map={"endpoint_url": "ECB_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="bls",
        group="global_macro",
        default_display_name="BLS",
        requires_key=False,
        env_key_map={"api_key": "BLS_API_KEY", "endpoint_url": "BLS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="bea",
        group="global_macro",
        default_display_name="BEA",
        requires_key=True,
        env_key_map={"api_key": "BEA_API_KEY", "endpoint_url": "BEA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eia",
        group="global_macro",
        default_display_name="EIA",
        requires_key=True,
        env_key_map={"api_key": "EIA_API_KEY", "endpoint_url": "EIA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="us_treasury",
        group="global_macro",
        default_display_name="US Treasury",
        requires_key=False,
        env_key_map={"endpoint_url": "US_TREASURY_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coingecko",
        group="crypto_data",
        default_display_name="CoinGecko",
        requires_key=False,
        env_key_map={"api_key": "COINGECKO_DEMO_API_KEY", "endpoint_url": "COINGECKO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coingecko_pro",
        group="crypto_data",
        default_display_name="CoinGecko Pro",
        requires_key=True,
        env_key_map={"api_key": "COINGECKO_PRO_API_KEY", "endpoint_url": "COINGECKO_PRO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coinmarketcap",
        group="crypto_data",
        default_display_name="CoinMarketCap",
        requires_key=True,
        env_key_map={"api_key": "CMC_PRO_API_KEY", "endpoint_url": "COINMARKETCAP_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="binance",
        group="crypto_data",
        default_display_name="Binance",
        requires_key=False,
        env_key_map={"endpoint_url": "BINANCE_SPOT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="okx",
        group="crypto_data",
        default_display_name="OKX",
        requires_key=False,
        env_key_map={"endpoint_url": "OKX_API_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="ccxt",
        group="crypto_data",
        default_display_name="CCXT",
        requires_key=False,
        env_key_map={"endpoint_url": "CCXT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coinglass",
        group="crypto_data",
        default_display_name="Coinglass",
        requires_key=True,
        env_key_map={
            "api_key": "COINGLASS_API_KEY",
            "endpoint_url": "COINGLASS_API_BASE",
            "header_name": "COINGLASS_API_HEADER_NAME",
        },
    ),
    _SupportedSourceProfile(
        supported_type="cryptocompare",
        group="crypto_data",
        default_display_name="CryptoCompare",
        requires_key=True,
        env_key_map={"api_key": "CRYPTOCOMPARE_API_KEY", "endpoint_url": "CRYPTOCOMPARE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="messari",
        group="crypto_data",
        default_display_name="Messari",
        requires_key=True,
        env_key_map={"api_key": "MESSARI_API_KEY", "endpoint_url": "MESSARI_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="glassnode",
        group="crypto_data",
        default_display_name="Glassnode",
        requires_key=True,
        env_key_map={"api_key": "GLASSNODE_API_KEY", "endpoint_url": "GLASSNODE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="santiment",
        group="crypto_data",
        default_display_name="Santiment",
        requires_key=True,
        env_key_map={"api_key": "SANTIMENT_API_KEY", "endpoint_url": "SANTIMENT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coinmetrics",
        group="crypto_data",
        default_display_name="Coin Metrics",
        requires_key=True,
        env_key_map={"api_key": "COINMETRICS_API_KEY", "endpoint_url": "COINMETRICS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="dune",
        group="crypto_data",
        default_display_name="Dune",
        requires_key=True,
        env_key_map={"api_key": "DUNE_API_KEY", "endpoint_url": "DUNE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="nansen",
        group="crypto_data",
        default_display_name="Nansen",
        requires_key=True,
        env_key_map={"api_key": "NANSEN_API_KEY", "endpoint_url": "NANSEN_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="token_terminal",
        group="crypto_data",
        default_display_name="Token Terminal",
        requires_key=True,
        env_key_map={"api_key": "TOKEN_TERMINAL_API_KEY", "endpoint_url": "TOKEN_TERMINAL_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="the_graph",
        group="crypto_data",
        default_display_name="The Graph",
        requires_key=True,
        env_key_map={"api_key": "THE_GRAPH_API_KEY", "endpoint_url": "THE_GRAPH_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="kaiko",
        group="crypto_data",
        default_display_name="Kaiko",
        requires_key=True,
        env_key_map={"api_key": "KAIKO_API_KEY", "endpoint_url": "KAIKO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="amberdata",
        group="crypto_data",
        default_display_name="Amberdata",
        requires_key=True,
        env_key_map={"api_key": "AMBERDATA_API_KEY", "endpoint_url": "AMBERDATA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="lunarcrush",
        group="crypto_social",
        default_display_name="LunarCrush",
        requires_key=True,
        env_key_map={"api_key": "LUNARCRUSH_API_KEY", "endpoint_url": "LUNARCRUSH_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="alternative_me",
        group="crypto_social",
        default_display_name="Alternative.me",
        requires_key=False,
        env_key_map={"endpoint_url": "ALTERNATIVE_ME_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="defillama",
        group="crypto_data",
        default_display_name="DeFiLlama",
        requires_key=False,
        env_key_map={"endpoint_url": "DEFILLAMA_BASE_URL"},
    ),
)

_SUPPORTED_SOURCE_BY_TYPE: dict[str, _SupportedSourceProfile] = {
    item.supported_type: item for item in _SUPPORTED_SOURCE_PROFILES
}
_SETTINGS_SOURCE_PROFILES: tuple[_SupportedSourceProfile, ...] = tuple(
    item for item in _SUPPORTED_SOURCE_PROFILES if item.requires_key
)
_SETTINGS_SOURCE_BY_TYPE: dict[str, _SupportedSourceProfile] = {
    item.supported_type: item for item in _SETTINGS_SOURCE_PROFILES
}
SUPPORTED_DATA_SOURCE_TYPES: tuple[str, ...] = tuple(item.supported_type for item in _SETTINGS_SOURCE_PROFILES)

_DATA_SOURCE_ENV_KEY_MAP: dict[str, dict[str, str]] = {
    item.supported_type: dict(item.env_key_map) for item in _SETTINGS_SOURCE_PROFILES
}

_DATA_SOURCE_ENV_ALLOWLIST: tuple[str, ...] = tuple(
    sorted({value for item in _DATA_SOURCE_ENV_KEY_MAP.values() for value in item.values()})
)


@dataclass(frozen=True)
class DataSourceInstanceForUser:
    instance_id: str
    supported_type: str
    group: str
    display_name: str
    enabled: bool
    api_key_masked: str | None
    endpoint_url: str | None
    state: str
    last_success_at: str | None
    last_test_at: str | None

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "supportedType": self.supported_type,
            "group": self.group,
            "displayName": self.display_name,
            "enabled": self.enabled,
            "apiKeyMasked": self.api_key_masked,
            "endpointUrl": self.endpoint_url,
            "state": self.state,
            "lastSuccessAt": self.last_success_at,
            "lastTestAt": self.last_test_at,
        }


@dataclass(frozen=True)
class DataSourceTestResult:
    state: str
    health_event: dict[str, Any]
    can_enable: bool

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "healthEvent": self.health_event,
            "canEnable": self.can_enable,
        }


class InMemoryDataSourceStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def get(self, instance_id: str) -> dict[str, Any] | None:
        return self._items.get(instance_id)

    def list_instances(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._items.values())

    def upsert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        instance_id = str(record.get("id") or record.get("instance_id") or uuid4().hex)
        saved = dict(record)
        saved["id"] = instance_id
        self._items[instance_id] = saved
        return saved

    def clear(self) -> None:
        self._items.clear()


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def replace(self, *, old_ref: str | None, value: str, scope: str) -> str:
        _ = old_ref
        ref = f"{scope}:{sha1(value.encode('utf-8')).hexdigest()[:12]}"
        self._values[ref] = value
        return ref

    def get(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        return self._values.get(secret_ref)

    def clear(self) -> None:
        self._values.clear()


class DataSourceSettingsService:
    def __init__(
        self,
        *,
        data_source_store: InMemoryDataSourceStore | None = None,
        secret_store: InMemorySecretStore | None = None,
        env_writer: EnvLocalAllowlistWriter | None = None,
        health_tester: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        display_decision_source: Callable[[], tuple[ProviderDisplayDecision, ...]] | None = None,
    ) -> None:
        self._store = data_source_store or InMemoryDataSourceStore()
        self._secret_store = secret_store or InMemorySecretStore()
        self._env_writer = env_writer
        self._health_tester = health_tester or _default_health_tester
        self._display_decision_source = display_decision_source or _default_display_decision_source
        self._idempotency: dict[str, Any] = {}

    def list_data_sources(self) -> dict[str, Any]:
        visible_profiles = self._visible_settings_profiles()
        stored_by_type: dict[str, Mapping[str, Any]] = {}
        for item in self._store.list_instances():
            supported_type = str(item.get("supported_type", "")).strip()
            if supported_type in visible_profiles:
                stored_by_type[supported_type] = item
        instances = [
            to_data_source_instance_for_user(stored_by_type.get(supported_type) or _built_in_source_row(supported_type)).to_user_dict()
            for supported_type in visible_profiles
        ]
        return {
            "supportedTypes": tuple(visible_profiles),
            "instances": instances,
        }

    def test_data_source_instance(self, instance_draft: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        normalized = self._validate_supported_input(instance_draft)
        existing = self._store.get(normalized["instanceId"]) if normalized.get("instanceId") else None
        out = self._probe_health(
            normalized,
            requires_key=self._resolve_requires_key(normalized=normalized, existing=existing),
            existing_credential_ref=self._credential_ref(existing),
        )
        self._idempotency[request_id] = out
        return out

    def save_data_source_instance(self, data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        normalized = self._validate_supported_input(data)
        existing = self._store.get(normalized["instanceId"]) if normalized.get("instanceId") else None
        credential_ref = str(existing.get("credential_ref")) if existing and existing.get("credential_ref") else None
        api_key_replacement = str(data.get("apiKeyReplacement", "")).strip()
        if api_key_replacement:
            credential_ref = self._secret_store.replace(
                old_ref=credential_ref,
                value=api_key_replacement,
                scope="data_source",
            )
        enabled = bool(normalized["enabled"])
        state = str(normalized["state"])
        requires_key = self._resolve_requires_key(normalized=normalized, existing=existing)
        if enabled:
            self._probe_health(
                normalized,
                requires_key=requires_key,
                existing_credential_ref=credential_ref,
            )
            state = "validated"
        saved = self._store.upsert(
            {
                "id": normalized.get("instanceId") or None,
                "supported_type": normalized["supportedType"],
                "group": normalized["group"],
                "display_name": normalized["displayName"],
                "enabled": enabled,
                "credential_ref": credential_ref,
                "endpoint_url": normalized["endpoint_url"],
                "proxy_url": normalized["proxy_url"],
                "header_name": normalized["header_name"],
                "priority": int(normalized["priority"]),
                "state": state,
                "last_success_at": normalized["last_success_at"],
                "last_test_at": normalized["last_test_at"] or _now_iso(),
                "requires_key": requires_key,
            }
        )
        self._write_env_updates(saved, api_key_replacement)
        out = to_data_source_instance_for_user(saved).to_user_dict()
        self._idempotency[request_id] = out
        return out

    def reset_to_defaults(self, request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        self._store.clear()
        self._secret_store.clear()
        self._idempotency.clear()
        if self._env_writer is not None:
            self._env_writer.clear_allowed_env_keys(_DATA_SOURCE_ENV_ALLOWLIST)
        out = {
            "status": "reset",
            "updatedAt": _now_iso(),
            **self.list_data_sources(),
        }
        self._idempotency[request_id] = out
        return out

    def _validate_supported_input(self, data: Mapping[str, Any]) -> dict[str, Any]:
        supported_type = str(data.get("supportedType", "")).strip()
        if not supported_type:
            raise UiBoundaryError("INVALID_INPUT", "数据源类型不能为空。")
        try:
            assert_data_source_type_supported(supported_type)
        except FirstVersionScopeError as exc:
            raise UiBoundaryError("INVALID_INPUT", str(exc)) from exc
        visible_profiles = self._visible_settings_profiles()
        profile = visible_profiles.get(supported_type)
        if profile is None:
            raise UiBoundaryError("INVALID_INPUT", f"当前数据源未进入 /report 或 /select 主链路，设置页暂不支持配置：{supported_type}。")
        if data.get("customJsonMapping") or data.get("custom_json_mapping") or data.get("customScript"):
            raise UiBoundaryError("INVALID_INPUT", "当前不支持自定义数据映射或脚本。")
        return {
            "instanceId": str(data.get("instanceId", "")).strip() or None,
            "supportedType": supported_type,
            "group": profile.group,
            "displayName": profile.default_display_name,
            "enabled": bool(data.get("enabled", False)),
            "endpointUrl": _optional_str(data.get("endpointUrl")),
            "endpoint_url": _optional_str(data.get("endpointUrl")),
            "proxy_url": None,
            "header_name": None,
            "priority": 100,
            "state": str(data.get("state", "draft")).strip() or "draft",
            "last_success_at": _optional_str(data.get("lastSuccessAt")),
            "last_test_at": _optional_str(data.get("lastTestAt")),
            "requiresKey": profile.requires_key,
            "apiKeyReplacement": _optional_str(data.get("apiKeyReplacement")),
        }

    def _probe_health(
        self,
        normalized: Mapping[str, Any],
        *,
        requires_key: bool,
        existing_credential_ref: str | None,
    ) -> dict[str, Any]:
        existing_credential_value = self._secret_store.get(existing_credential_ref)
        self._require_probe_key(
            normalized=normalized,
            requires_key=requires_key,
            has_existing_credential=bool(existing_credential_ref),
        )
        probe_input = dict(normalized)
        if not _optional_str(probe_input.get("apiKeyReplacement")) and existing_credential_value:
            probe_input["apiKeyReplacement"] = existing_credential_value
        try:
            health = self._health_tester(probe_input)
            status = str(health.get("status", "")).strip().lower()
            if status not in {"validated", "enabled_candidate", "enabled"}:
                message = str(health.get("message", "数据源测试未通过。")).strip() or "数据源测试未通过。"
                raise UiBoundaryError("DATASOURCE_TEST_FAILED", message)
            return DataSourceTestResult(
                state="validated",
                health_event=to_data_source_health_event_for_user(
                    {
                        "displayName": normalized["displayName"],
                        "status": status,
                        "userMessage": str(health.get("message", "连接测试通过。")),
                        "impact": str(health.get("impact", "low")),
                        "occurredAt": _now_iso(),
                    }
                ),
                can_enable=True,
            ).to_user_dict()
        except UiBoundaryError:
            raise
        except Exception as exc:  # pragma: no cover - 防御性路径
            raise UiBoundaryError("DATASOURCE_TEST_FAILED", _probe_error_message(exc)) from exc

    @staticmethod
    def _resolve_requires_key(*, normalized: Mapping[str, Any], existing: Mapping[str, Any] | None) -> bool:
        if existing is not None and "requires_key" in existing:
            return bool(existing.get("requires_key"))
        if existing is not None and "requiresKey" in existing:
            return bool(existing.get("requiresKey"))
        return bool(normalized.get("requiresKey", True))

    @staticmethod
    def _credential_ref(existing: Mapping[str, Any] | None) -> str | None:
        if existing is None:
            return None
        return _optional_str(existing.get("credential_ref"))

    @staticmethod
    def _require_probe_key(
        *,
        normalized: Mapping[str, Any],
        requires_key: bool,
        has_existing_credential: bool,
    ) -> None:
        if not requires_key:
            return
        if has_existing_credential:
            return
        if _optional_str(normalized.get("apiKeyReplacement")):
            return
        raise UiBoundaryError("INVALID_INPUT", "需要先填写密钥，再进行测试或启用。")

    def _write_env_updates(self, saved: Mapping[str, Any], api_key_replacement: str) -> None:
        if self._env_writer is None:
            return
        mapping = _DATA_SOURCE_ENV_KEY_MAP.get(str(saved["supported_type"]), {})
        updates: dict[str, str] = {}
        if api_key_replacement and mapping.get("api_key"):
            updates[mapping["api_key"]] = api_key_replacement
        endpoint = _optional_str(saved.get("endpoint_url"))
        proxy = _optional_str(saved.get("proxy_url"))
        header = _optional_str(saved.get("header_name"))
        if endpoint and mapping.get("endpoint_url"):
            updates[mapping["endpoint_url"]] = endpoint
        if proxy and mapping.get("proxy_url"):
            updates[mapping["proxy_url"]] = proxy
        if header and mapping.get("header_name"):
            updates[mapping["header_name"]] = header
        if updates:
            self._env_writer.write_allowed_env_keys(updates)

    def _visible_settings_profiles(self) -> dict[str, _SupportedSourceProfile]:
        decisions = self._display_decision_source()
        visible: dict[str, _SupportedSourceProfile] = {}
        tushare = _SETTINGS_SOURCE_BY_TYPE.get("tushare")
        if tushare is not None:
            visible[tushare.supported_type] = tushare
        for decision in decisions:
            if decision.display_status != ProviderDisplayStatus.SHOW or not decision.requires_user_credential:
                continue
            for supported_type in _supported_types_for_display_decision(decision):
                profile = _SETTINGS_SOURCE_BY_TYPE.get(supported_type)
                if profile is not None and profile.requires_key:
                    visible[profile.supported_type] = profile
        return visible


def to_data_source_instance_for_user(instance: Mapping[str, Any]) -> DataSourceInstanceForUser:
    supported_type = str(instance.get("supported_type", ""))
    profile = _SUPPORTED_SOURCE_BY_TYPE.get(supported_type)
    credential_ref = _optional_str(instance.get("credential_ref"))
    return DataSourceInstanceForUser(
        instance_id=str(instance.get("id", "")),
        supported_type=supported_type,
        group=profile.group if profile is not None else str(instance.get("group", "custom")),
        display_name=profile.default_display_name if profile is not None else str(instance.get("display_name", "")),
        enabled=bool(instance.get("enabled", False)),
        api_key_masked=mask_secret_ref(credential_ref),
        endpoint_url=_optional_str(instance.get("endpoint_url")),
        state=str(instance.get("state", "draft")),
        last_success_at=_optional_str(instance.get("last_success_at")),
        last_test_at=_optional_str(instance.get("last_test_at")),
    )


def to_data_source_health_event_for_user(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "displayName": str(event.get("displayName", "")),
        "status": str(event.get("status", "unknown")),
        "userMessage": str(event.get("userMessage", "")),
        "impact": str(event.get("impact", "medium")),
        "occurredAt": _optional_str(event.get("occurredAt")) or _now_iso(),
    }


def mask_secret_ref(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    suffix = secret_ref[-4:] if len(secret_ref) >= 4 else secret_ref
    return f"***{suffix}"


def allowed_data_source_env_keys() -> tuple[str, ...]:
    return _DATA_SOURCE_ENV_ALLOWLIST


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_health_tester(_instance: Mapping[str, Any]) -> Mapping[str, Any]:
    raise UiBoundaryError("DATASOURCE_TEST_FAILED", "当前环境未接入可用的数据源测试能力。")


def _probe_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "credential_missing" in text:
        return "密钥未配置，请先补全后再测试。"
    if "probe_not_supported" in text:
        return "当前数据源类型暂不支持实时测试，不能仅凭设置页探测启用。"
    return "数据源连接或鉴权失败，请到设置页更新后重试。"


def _built_in_source_row(supported_type: str) -> dict[str, Any]:
    profile = _SUPPORTED_SOURCE_BY_TYPE[supported_type]
    return {
        "id": f"builtin-{supported_type}",
        "supported_type": profile.supported_type,
        "group": profile.group,
        "display_name": profile.default_display_name,
        "enabled": False,
        "credential_ref": None,
        "endpoint_url": None,
        "proxy_url": None,
        "header_name": None,
        "priority": 100,
        "state": "draft",
        "last_success_at": None,
        "last_test_at": None,
        "requires_key": profile.requires_key,
    }


def _supported_type_label() -> str:
    return ", ".join(SUPPORTED_DATA_SOURCE_TYPES)


def _default_display_decision_source() -> tuple[ProviderDisplayDecision, ...]:
    return ui_display_decisions(default_market_policy_registry(), evidence_by_provider={})


def _supported_types_for_display_decision(decision: ProviderDisplayDecision) -> tuple[str, ...]:
    direct = _SETTINGS_SOURCE_BY_TYPE.get(decision.provider_id)
    if direct is not None:
        return (direct.supported_type,)

    registry = default_market_policy_registry()
    matched_env_keys = {
        item.user_config_key
        for item in registry.all_capabilities()
        if item.market == decision.market and item.provider == decision.provider_id and item.user_config_key
    }
    if not matched_env_keys:
        return ()
    return tuple(
        profile.supported_type
        for profile in _SETTINGS_SOURCE_PROFILES
        if matched_env_keys.intersection(profile.env_key_map.values())
    )
