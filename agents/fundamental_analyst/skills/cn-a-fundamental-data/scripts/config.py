from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from security import sanitize_error

FND_CONFIG_INVALID = "FND_CONFIG_INVALID"
FND_CONFIG_SECRET_MISSING = "FND_CONFIG_SECRET_MISSING"
FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED = "FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED"
FND_CONFIG_OPENVIKING_REQUIRED = "FND_CONFIG_OPENVIKING_REQUIRED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class FundamentalDataConfig:
    tushare_token: str | None = field(repr=False)
    mongodb_uri: str | None = field(repr=False)
    mongodb_database: str | None
    mongodb_cache_collection: str | None
    openviking_endpoint: str | None
    openviking_api_key: str | None = field(repr=False)
    openviking_workspace: str | None
    tool_enabled: bool
    disable_tushare: bool
    disable_akshare: bool
    cache_read_only: bool


class FundamentalConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def load_fundamental_data_config(env: Mapping[str, str]) -> FundamentalDataConfig:
    tool_enabled = _parse_bool(env, "CN_A_FUNDAMENTAL_TOOL_ENABLED", default=True)
    disable_tushare = _parse_bool(env, "CN_A_FUNDAMENTAL_DISABLE_TUSHARE", default=False)
    disable_akshare = _parse_bool(env, "CN_A_FUNDAMENTAL_DISABLE_AKSHARE", default=False)
    cache_read_only = _parse_bool(env, "CN_A_FUNDAMENTAL_CACHE_READ_ONLY", default=False)

    tushare_token = _read_secret(env, "TUSHARE_TOKEN")
    mongodb_uri = _read_secret(env, "CN_A_MONGODB_URI")
    mongodb_database = _read_optional_plain(env, "CN_A_MONGODB_DATABASE")
    mongodb_cache_collection = _read_optional_plain(env, "CN_A_MONGODB_CACHE_COLLECTION")
    openviking_endpoint = _read_secret(env, "OPENVIKING_ENDPOINT")
    openviking_api_key = _read_secret(env, "OPENVIKING_API_KEY")
    openviking_workspace = _read_optional_plain(env, "OPENVIKING_WORKSPACE")

    if tool_enabled:
        _require_present(mongodb_uri, "CN_A_MONGODB_URI")
        _require_present(mongodb_database, "CN_A_MONGODB_DATABASE")
        _require_present(mongodb_cache_collection, "CN_A_MONGODB_CACHE_COLLECTION")
        _require_present(openviking_workspace, "OPENVIKING_WORKSPACE")
        if mongodb_uri is not None:
            _validate_mongodb_uri(mongodb_uri)
        _validate_openviking(
            endpoint=openviking_endpoint,
            api_key=openviking_api_key,
            workspace=openviking_workspace,
        )

    return FundamentalDataConfig(
        tushare_token=tushare_token,
        mongodb_uri=mongodb_uri,
        mongodb_database=mongodb_database,
        mongodb_cache_collection=mongodb_cache_collection,
        openviking_endpoint=openviking_endpoint,
        openviking_api_key=openviking_api_key,
        openviking_workspace=openviking_workspace,
        tool_enabled=tool_enabled,
        disable_tushare=disable_tushare,
        disable_akshare=disable_akshare,
        cache_read_only=cache_read_only,
    )


def _validate_mongodb_uri(uri: str) -> None:
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        raise FundamentalConfigError(FND_CONFIG_INVALID, "CN_A_MONGODB_URI 非法") from exc

    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise FundamentalConfigError(FND_CONFIG_INVALID, "CN_A_MONGODB_URI scheme 非法")

    query = parse_qs(parsed.query, keep_blank_values=True)
    auth_source_values = [value.strip() for value in query.get("authSource", []) if value.strip()]
    credentials_present = bool(parsed.username or parsed.password)
    if credentials_present and not auth_source_values:
        raise FundamentalConfigError(
            FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED,
            "CN_A_MONGODB_URI 带认证信息时必须显式配置 authSource",
        )


def _validate_openviking(*, endpoint: str | None, api_key: str | None, workspace: str | None) -> None:
    if endpoint is None or endpoint.strip() == "":
        raise FundamentalConfigError(FND_CONFIG_OPENVIKING_REQUIRED, "OPENVIKING_ENDPOINT 缺失")
    if api_key is None or api_key.strip() == "":
        raise FundamentalConfigError(FND_CONFIG_OPENVIKING_REQUIRED, "OPENVIKING_API_KEY 缺失")
    if workspace is None or workspace.strip() == "":
        raise FundamentalConfigError(FND_CONFIG_OPENVIKING_REQUIRED, "OPENVIKING_WORKSPACE 缺失")


def _read_optional_plain(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _require_present(value: str | None, key: str) -> None:
    if value is None or value.strip() == "":
        raise FundamentalConfigError(FND_CONFIG_SECRET_MISSING, f"{key} 缺失")


def _read_secret(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None:
        return None
    value = raw.strip()
    if value == "":
        return None
    return value


def _parse_bool(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise FundamentalConfigError(
        FND_CONFIG_INVALID,
        sanitize_error(f"{key} 需要布尔值（true/false）"),
    )
