from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlparse

UI_DATA_SOURCE_SETTINGS_COLLECTION = "ui_data_source_settings"
UI_SECRET_SETTINGS_COLLECTION = "ui_secret_settings"

_RUNTIME_DATA_SOURCE_ENV_KEY_MAP: dict[str, dict[str, str]] = {
    "tushare": {"api_key": "TUSHARE_TOKEN", "endpoint_url": "TUSHARE_HTTP_URL"},
    "coinglass": {
        "api_key": "COINGLASS_API_KEY",
        "endpoint_url": "COINGLASS_API_BASE",
        "header_name": "COINGLASS_API_HEADER_NAME",
    },
    "coingecko": {"api_key": "COINGECKO_DEMO_API_KEY", "endpoint_url": "COINGECKO_BASE_URL"},
    "coingecko_pro": {"api_key": "COINGECKO_PRO_API_KEY", "endpoint_url": "COINGECKO_PRO_BASE_URL"},
    "coinmarketcap": {"api_key": "CMC_PRO_API_KEY", "endpoint_url": "COINMARKETCAP_BASE_URL"},
    "fred": {"api_key": "FRED_API_KEY", "endpoint_url": "FRED_BASE_URL"},
    "x": {"api_key": "X_BEARER_TOKEN", "endpoint_url": "X_API_BASE_URL"},
    "lunarcrush": {"api_key": "LUNARCRUSH_API_KEY", "endpoint_url": "LUNARCRUSH_BASE_URL"},
    "tavily": {"api_key": "TAVILY_API_KEY", "endpoint_url": "TAVILY_BASE_URL"},
    "tavily_search": {"api_key": "TAVILY_API_KEY", "endpoint_url": "TAVILY_BASE_URL"},
}

_RUNTIME_DATA_SOURCE_ENV_KEYS: frozenset[str] = frozenset(
    value for mapping in _RUNTIME_DATA_SOURCE_ENV_KEY_MAP.values() for value in mapping.values()
)


def build_runtime_data_source_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    database = _open_settings_database_from_env()
    base_env = dict(os.environ)
    if database is None:
        return base_env
    return merge_runtime_data_source_settings(base_env, database)


def merge_runtime_data_source_settings(base_env: Mapping[str, str], database: Any) -> dict[str, str]:
    merged = dict(base_env)
    for key in _RUNTIME_DATA_SOURCE_ENV_KEYS:
        merged.pop(key, None)
    for record in _iter_data_source_records(database):
        if not bool(record.get("enabled")):
            continue
        supported_type = str(record.get("supported_type") or record.get("supportedType") or "").strip()
        mapping = _RUNTIME_DATA_SOURCE_ENV_KEY_MAP.get(supported_type)
        if mapping is None:
            continue
        secret = _secret_value(database, _optional_str(record.get("credential_ref") or record.get("credentialRef")))
        api_key_env = mapping.get("api_key")
        if api_key_env and secret:
            merged[api_key_env] = secret
        endpoint = _optional_str(record.get("endpoint_url") or record.get("endpointUrl"))
        endpoint_env = mapping.get("endpoint_url")
        if endpoint_env and endpoint:
            merged[endpoint_env] = endpoint
        header = _optional_str(record.get("header_name") or record.get("headerName"))
        header_env = mapping.get("header_name")
        if header_env and header:
            merged[header_env] = header
    return merged


def runtime_data_source_env_keys() -> tuple[str, ...]:
    return tuple(sorted(_RUNTIME_DATA_SOURCE_ENV_KEYS))


def _open_settings_database_from_env() -> Any | None:
    uri = (
        os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip()
        or os.environ.get("CN_A_MONGODB_URI", "").strip()
    )
    if not uri:
        return None
    from pymongo import MongoClient

    database_name = (
        os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip()
        or os.environ.get("CN_A_MONGODB_DATABASE", "").strip()
        or _database_name_from_uri(uri)
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client[database_name]


def _iter_data_source_records(database: Any) -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    for doc in database[UI_DATA_SOURCE_SETTINGS_COLLECTION].find({}):
        if not isinstance(doc, Mapping):
            continue
        record = doc.get("record")
        if isinstance(record, Mapping):
            records.append(record)
    return tuple(records)


def _secret_value(database: Any, secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    doc = database[UI_SECRET_SETTINGS_COLLECTION].find_one({"_id": secret_ref})
    if not isinstance(doc, Mapping):
        return None
    value = doc.get("value")
    return value if isinstance(value, str) and value.strip() else None


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
