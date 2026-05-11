from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Mapping, cast
from urllib.parse import urlsplit

from .errors import (
    CONFIG_INVALID,
    MONGO_CONFIG_INVALID,
    OPENVIKING_CONFIG_INVALID,
    FrontlineConfigError,
)


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_VALID_OPENVIKING_AUTH_MODES = frozenset({"none", "bearer"})

DEFAULT_PROVIDER_TIMEOUT_MS = 10000
DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS = 30000
DEFAULT_PROVIDER_MAX_CONCURRENCY = 3
DEFAULT_PROVIDER_CACHE_REQUIRED = False


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    default_timeout_ms: int
    total_timeout_ms: int
    max_concurrency: int


@dataclass(frozen=True)
class MongoConfig:
    uri: str = field(repr=False)
    database: str
    cache_required: bool


@dataclass(frozen=True)
class OpenVikingConfig:
    base_uri: str
    auth_mode: Literal["none", "bearer"]
    token: str | None = field(repr=False, default=None)


@dataclass(frozen=True)
class ProviderFlags:
    market_enable_akshare: bool
    market_enable_eastmoney_direct: bool
    market_enable_sina: bool
    market_enable_tencent: bool
    market_enable_baostock: bool
    market_enable_efinance: bool
    market_enable_tushare: bool
    news_enable_bocha: bool
    news_enable_tavily: bool
    news_enable_jina: bool
    news_enable_newsnow: bool
    news_enable_minimax: bool
    social_enable_search_enrichment: bool
    fundamental_enable_tushare: bool
    fundamental_disable_tushare: bool
    news_bocha_api_key: str | None = field(default=None, repr=False)
    news_tavily_api_key: str | None = field(default=None, repr=False)
    jina_api_key: str | None = field(default=None, repr=False)
    news_newsnow_api_key: str | None = field(default=None, repr=False)
    news_minimax_api_key: str | None = field(default=None, repr=False)
    news_minimax_base_url: str = "https://api.minimaxi.com/v1"
    news_minimax_model: str = "MiniMax-M2.7"


@dataclass(frozen=True)
class FrontlineProviderConfig:
    mongodb: MongoConfig
    openviking: OpenVikingConfig
    provider_runtime: ProviderRuntimeConfig
    provider_flags: ProviderFlags


def load_frontline_provider_config(env: Mapping[str, str] | None = None) -> FrontlineProviderConfig:
    source = os.environ if env is None else env

    default_timeout_ms = _parse_int_range(
        source,
        "CN_A_PROVIDER_DEFAULT_TIMEOUT_MS",
        default=DEFAULT_PROVIDER_TIMEOUT_MS,
        minimum=1000,
        maximum=30000,
    )
    total_timeout_ms = _parse_int_range(
        source,
        "CN_A_PROVIDER_TOTAL_TIMEOUT_MS",
        default=DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS,
        minimum=5000,
        maximum=60000,
    )
    if total_timeout_ms <= default_timeout_ms:
        raise FrontlineConfigError(
            CONFIG_INVALID,
            "CN_A_PROVIDER_TOTAL_TIMEOUT_MS 必须大于 CN_A_PROVIDER_DEFAULT_TIMEOUT_MS",
        )
    max_concurrency = _parse_int_range(
        source,
        "CN_A_PROVIDER_MAX_CONCURRENCY",
        default=DEFAULT_PROVIDER_MAX_CONCURRENCY,
        minimum=1,
        maximum=6,
    )
    cache_required = _parse_bool(source, "CN_A_PROVIDER_CACHE_REQUIRED", default=DEFAULT_PROVIDER_CACHE_REQUIRED)

    mongodb_uri = _read_required(source, "CN_A_MONGODB_URI", code=MONGO_CONFIG_INVALID)
    mongodb_database = _parse_mongodb_database(mongodb_uri)
    mongodb = MongoConfig(uri=mongodb_uri, database=mongodb_database, cache_required=cache_required)

    openviking_base_uri = _read_required(source, "CLAW_TRADE_OPENVIKING_BASE_URI", code=OPENVIKING_CONFIG_INVALID)
    _validate_openviking_base_uri(openviking_base_uri)
    openviking_auth_mode = _parse_openviking_auth_mode(source, "CLAW_TRADE_OPENVIKING_AUTH_MODE", default="none")
    openviking_token = _read_optional(source, "CLAW_TRADE_OPENVIKING_TOKEN")
    if openviking_auth_mode == "bearer" and not openviking_token:
        raise FrontlineConfigError(
            OPENVIKING_CONFIG_INVALID,
            "CLAW_TRADE_OPENVIKING_AUTH_MODE=bearer 时必须提供 CLAW_TRADE_OPENVIKING_TOKEN",
        )
    openviking = OpenVikingConfig(
        base_uri=openviking_base_uri,
        auth_mode=openviking_auth_mode,
        token=openviking_token,
    )

    provider_flags = ProviderFlags(
        market_enable_akshare=_parse_bool(source, "CN_A_MARKET_ENABLE_AKSHARE", default=True),
        market_enable_eastmoney_direct=_parse_bool(source, "CN_A_MARKET_ENABLE_EASTMONEY_DIRECT", default=True),
        market_enable_sina=_parse_bool(source, "CN_A_MARKET_ENABLE_SINA", default=True),
        market_enable_tencent=_parse_bool(source, "CN_A_MARKET_ENABLE_TENCENT", default=True),
        market_enable_baostock=_parse_bool(source, "CN_A_MARKET_ENABLE_BAOSTOCK", default=True),
        market_enable_efinance=_parse_bool(source, "CN_A_MARKET_ENABLE_EFINANCE", default=True),
        market_enable_tushare=_parse_bool(source, "CN_A_MARKET_ENABLE_TUSHARE", default=True),
        news_enable_bocha=_parse_bool(source, "CN_A_NEWS_ENABLE_BOCHA", default=True),
        news_enable_tavily=_parse_bool(source, "CN_A_NEWS_ENABLE_TAVILY", default=True),
        news_enable_jina=_parse_bool(source, "CN_A_NEWS_ENABLE_JINA", default=True),
        news_enable_newsnow=_parse_bool(source, "CN_A_NEWS_ENABLE_NEWSNOW", default=True),
        news_enable_minimax=_parse_bool(source, "CN_A_NEWS_ENABLE_MINIMAX", default=True),
        social_enable_search_enrichment=_parse_bool(source, "CN_A_SOCIAL_ENABLE_SEARCH_ENRICHMENT", default=True),
        fundamental_enable_tushare=_parse_bool(source, "CN_A_FUNDAMENTAL_ENABLE_TUSHARE", default=True),
        fundamental_disable_tushare=_parse_bool(source, "CN_A_FUNDAMENTAL_DISABLE_TUSHARE", default=False),
        news_bocha_api_key=_read_optional(source, "CN_A_NEWS_BOCHA_API_KEY"),
        news_tavily_api_key=_read_optional(source, "CN_A_NEWS_TAVILY_API_KEY"),
        jina_api_key=_read_optional(source, "JINA_API_KEY"),
        news_newsnow_api_key=_read_optional(source, "CN_A_NEWS_NEWSNOW_API_KEY"),
        news_minimax_api_key=_read_optional(source, "CN_A_NEWS_MINIMAX_API_KEY"),
        news_minimax_base_url=_read_optional(source, "CN_A_NEWS_MINIMAX_BASE_URL")
        or "https://api.minimaxi.com/v1",
        news_minimax_model=_read_optional(source, "CN_A_NEWS_MINIMAX_MODEL") or "MiniMax-M2.7",
    )

    return FrontlineProviderConfig(
        mongodb=mongodb,
        openviking=openviking,
        provider_runtime=ProviderRuntimeConfig(
            default_timeout_ms=default_timeout_ms,
            total_timeout_ms=total_timeout_ms,
            max_concurrency=max_concurrency,
        ),
        provider_flags=provider_flags,
    )


def summarize_frontline_provider_config(config: FrontlineProviderConfig) -> dict[str, object]:
    return {
        "mongodb_database": config.mongodb.database,
        "mongodb_configured": bool(config.mongodb.uri),
        "cache_required": config.mongodb.cache_required,
        "provider_default_timeout_ms": config.provider_runtime.default_timeout_ms,
        "provider_total_timeout_ms": config.provider_runtime.total_timeout_ms,
        "provider_max_concurrency": config.provider_runtime.max_concurrency,
        "openviking_base_uri": config.openviking.base_uri,
        "openviking_auth_mode": config.openviking.auth_mode,
        "openviking_token_configured": bool(config.openviking.token),
        "provider_flags": {
            "market_enable_akshare": config.provider_flags.market_enable_akshare,
            "market_enable_eastmoney_direct": config.provider_flags.market_enable_eastmoney_direct,
            "market_enable_sina": config.provider_flags.market_enable_sina,
            "market_enable_tencent": config.provider_flags.market_enable_tencent,
            "market_enable_baostock": config.provider_flags.market_enable_baostock,
            "market_enable_efinance": config.provider_flags.market_enable_efinance,
            "market_enable_tushare": config.provider_flags.market_enable_tushare,
            "news_enable_bocha": config.provider_flags.news_enable_bocha,
            "news_enable_tavily": config.provider_flags.news_enable_tavily,
            "news_enable_jina": config.provider_flags.news_enable_jina,
            "news_enable_newsnow": config.provider_flags.news_enable_newsnow,
            "news_enable_minimax": config.provider_flags.news_enable_minimax,
            "social_enable_search_enrichment": config.provider_flags.social_enable_search_enrichment,
            "fundamental_enable_tushare": config.provider_flags.fundamental_enable_tushare,
            "fundamental_disable_tushare": config.provider_flags.fundamental_disable_tushare,
            "news_bocha_api_key_configured": bool(config.provider_flags.news_bocha_api_key),
            "news_tavily_api_key_configured": bool(config.provider_flags.news_tavily_api_key),
            "jina_api_key_configured": bool(config.provider_flags.jina_api_key),
            "news_newsnow_api_key_configured": bool(config.provider_flags.news_newsnow_api_key),
            "news_minimax_api_key_configured": bool(config.provider_flags.news_minimax_api_key),
            "news_minimax_base_url": config.provider_flags.news_minimax_base_url,
            "news_minimax_model": config.provider_flags.news_minimax_model,
        },
    }


def _parse_bool(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise FrontlineConfigError(CONFIG_INVALID, f"{key} 只接受 true/false/1/0/yes/no/on/off")


def _parse_int_range(
    env: Mapping[str, str],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(key)
    if raw is None:
        value = default
    else:
        text = raw.strip()
        if text == "":
            raise FrontlineConfigError(CONFIG_INVALID, f"{key} 不能为空")
        try:
            value = int(text)
        except ValueError as exc:
            raise FrontlineConfigError(CONFIG_INVALID, f"{key} 需要整数") from exc
    if value < minimum or value > maximum:
        raise FrontlineConfigError(CONFIG_INVALID, f"{key} 允许范围为 {minimum} 到 {maximum}")
    return value


def _read_optional(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None:
        return None
    value = raw.strip()
    if value == "":
        return None
    return value


def _read_required(env: Mapping[str, str], key: str, *, code: str) -> str:
    value = _read_optional(env, key)
    if value is None:
        raise FrontlineConfigError(code, f"{key} 缺失")
    return value


def _parse_mongodb_database(uri: str) -> str:
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        raise FrontlineConfigError(MONGO_CONFIG_INVALID, "CN_A_MONGODB_URI 非法") from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise FrontlineConfigError(
            MONGO_CONFIG_INVALID,
            "CN_A_MONGODB_URI 必须以 mongodb:// 或 mongodb+srv:// 开头",
        )
    path = parsed.path.strip("/")
    if not path:
        raise FrontlineConfigError(
            MONGO_CONFIG_INVALID,
            "CN_A_MONGODB_URI 必须在 path 中提供 database 名称",
        )
    if "/" in path:
        raise FrontlineConfigError(MONGO_CONFIG_INVALID, "CN_A_MONGODB_URI database path 非法")
    return path


def _parse_openviking_auth_mode(
    env: Mapping[str, str],
    key: str,
    *,
    default: Literal["none", "bearer"],
) -> Literal["none", "bearer"]:
    raw = env.get(key)
    if raw is None:
        return default
    mode = raw.strip().lower()
    if mode not in _VALID_OPENVIKING_AUTH_MODES:
        raise FrontlineConfigError(
            OPENVIKING_CONFIG_INVALID,
            f"{key} 只允许 none 或 bearer",
        )
    return cast(Literal["none", "bearer"], mode)


def _validate_openviking_base_uri(base_uri: str) -> None:
    try:
        parsed = urlsplit(base_uri)
    except ValueError as exc:
        raise FrontlineConfigError(OPENVIKING_CONFIG_INVALID, "CLAW_TRADE_OPENVIKING_BASE_URI 非法") from exc
    if parsed.scheme not in {"http", "https", "viking"}:
        raise FrontlineConfigError(
            OPENVIKING_CONFIG_INVALID,
            "CLAW_TRADE_OPENVIKING_BASE_URI 只允许 http/https/viking",
        )
