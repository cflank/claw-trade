from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from hashlib import sha1
from typing import Any, Mapping
from urllib.parse import urlparse, urlsplit, urlunsplit

from claw_trade.ui_backend.data_source_settings import _SUPPORTED_SOURCE_PROFILES

UI_DATA_SOURCE_SETTINGS_COLLECTION = "ui_data_source_settings"
UI_EMBEDDING_SETTINGS_COLLECTION = "ui_embedding_settings"
UI_REPORT_MODEL_CONFIG_COLLECTION = "ui_report_model_config"
UI_SECRET_SETTINGS_COLLECTION = "ui_secret_settings"

def export_runtime_settings_from_mongo(*, import_env_data_sources: bool | None = None) -> dict[str, str]:
    database = _open_settings_database_from_env()
    if database is None:
        return {}
    should_import = import_env_data_sources if import_env_data_sources is not None else _should_import_env_data_sources(os.environ)
    if should_import:
        _import_env_data_sources(database, os.environ)
    values: dict[str, str] = {}
    values.update(_report_model_exports(database))
    values.update(_embedding_exports(database))
    return values


def main() -> int:
    values = export_runtime_settings_from_mongo()
    for key, value in values.items():
        sys.stdout.write(key)
        sys.stdout.write("\0")
        sys.stdout.write(value)
        sys.stdout.write("\0")
    return 0


def _report_model_exports(database: Any) -> dict[str, str]:
    payload = _payload(database[UI_REPORT_MODEL_CONFIG_COLLECTION].find_one({"_id": "report_model_config"}))
    if not payload:
        return {}
    provider = _optional_str(payload.get("provider"))
    model = _optional_str(payload.get("model"))
    api_key = _optional_str(payload.get("apiKey"))
    if not provider or not model or not api_key:
        return {}
    out = {
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER": provider,
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_MODEL": model,
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY": api_key,
    }
    optional = {
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_API": _optional_str(payload.get("api")),
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_BASE_URL": _optional_str(payload.get("endpointUrl") or payload.get("endpoint_url")),
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_MODEL_ID": _optional_str(payload.get("providerModelId")),
        "CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_NAME": _optional_str(payload.get("providerName")),
    }
    out.update({key: value for key, value in optional.items() if value})
    return out


def _embedding_exports(database: Any) -> dict[str, str]:
    payload = _payload(database[UI_EMBEDDING_SETTINGS_COLLECTION].find_one({"_id": "embedding"}))
    if not payload:
        return {}
    keys = (
        "OPENVIKING_EMBEDDING_PROVIDER",
        "OPENVIKING_EMBEDDING_MODEL",
        "OPENVIKING_EMBEDDING_API_KEY",
        "OPENVIKING_EMBEDDING_API_BASE",
        "OPENVIKING_EMBEDDING_DIMENSION",
    )
    out = {key: str(payload.get(key, "") or "").strip() for key in keys}
    out["OPENVIKING_EMBEDDING_API_BASE"] = _normalize_embedding_api_base_url(
        out["OPENVIKING_EMBEDDING_API_BASE"]
    )
    return out


def _normalize_embedding_api_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if path.lower().endswith("/embeddings"):
        path = path[: -len("/embeddings")].rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return value


def _import_env_data_sources(database: Any, env: Mapping[str, str]) -> int:
    imported = 0
    for profile in _SUPPORTED_SOURCE_PROFILES:
        api_key = _first_env_value(env, _profile_env_keys(profile, "api_key"))
        endpoint_url = _first_env_value(env, _profile_env_keys(profile, "endpoint_url"))
        proxy_url = _first_env_value(env, _profile_env_keys(profile, "proxy_url"))
        header_name = _first_env_value(env, _profile_env_keys(profile, "header_name"))
        rate_limit_max_calls = _first_env_value(env, _profile_env_keys(profile, "rate_limit_max_calls"))
        rate_limit_window_seconds = _first_env_value(env, _profile_env_keys(profile, "rate_limit_window_seconds"))
        rate_limit_safety_margin = _first_env_value(env, _profile_env_keys(profile, "rate_limit_safety_margin"))
        existing = _data_source_record(database, profile.supported_type)
        existing_credential_ref = _optional_str(existing.get("credential_ref")) if existing else None
        if (
            not api_key
            and not endpoint_url
            and not proxy_url
            and not header_name
            and not rate_limit_max_calls
            and not rate_limit_window_seconds
            and not rate_limit_safety_margin
        ):
            continue

        credential_ref = existing_credential_ref
        if api_key:
            credential_ref = _upsert_secret(database, value=api_key, old_ref=existing_credential_ref)
        has_credential = bool(credential_ref)
        enabled = bool(has_credential or not profile.requires_key)
        record = dict(existing or {})
        record.update(
            {
                "id": profile.supported_type,
                "supported_type": profile.supported_type,
                "group": profile.group,
                "display_name": profile.default_display_name,
                "enabled": enabled,
                "credential_ref": credential_ref,
                "priority": int(record.get("priority") or 100),
                "state": "env_imported" if enabled else "env_imported_missing_credential",
                "last_test_at": _now_iso(),
                "requires_key": profile.requires_key,
            }
        )
        if endpoint_url:
            record["endpoint_url"] = endpoint_url
        else:
            record.setdefault("endpoint_url", getattr(profile, "default_endpoint_url", None))
        if header_name:
            record["header_name"] = header_name
        else:
            record.setdefault("header_name", None)
        if proxy_url:
            record["proxy_url"] = proxy_url
        else:
            record.setdefault("proxy_url", None)
        if rate_limit_max_calls:
            record["rate_limit_max_calls"] = int(rate_limit_max_calls)
        else:
            record.setdefault("rate_limit_max_calls", None)
        if rate_limit_window_seconds:
            record["rate_limit_window_seconds"] = int(rate_limit_window_seconds)
        else:
            record.setdefault("rate_limit_window_seconds", None)
        if rate_limit_safety_margin:
            record["rate_limit_safety_margin"] = int(rate_limit_safety_margin)
        else:
            record.setdefault("rate_limit_safety_margin", None)
        record.setdefault("last_success_at", None)
        database[UI_DATA_SOURCE_SETTINGS_COLLECTION].replace_one(
            {"_id": profile.supported_type},
            {
                "_id": profile.supported_type,
                "record": record,
                "schemaVersion": "ui-data-source-settings-v1",
                "updatedAt": _now_iso(),
            },
            upsert=True,
        )
        imported += 1
    return imported


def _data_source_record(database: Any, supported_type: str) -> dict[str, Any] | None:
    doc = database[UI_DATA_SOURCE_SETTINGS_COLLECTION].find_one({"_id": supported_type})
    if not isinstance(doc, Mapping):
        return None
    record = doc.get("record")
    return dict(record) if isinstance(record, Mapping) else None


def _upsert_secret(database: Any, *, value: str, old_ref: str | None) -> str:
    ref = f"data_source:{sha1(value.encode('utf-8')).hexdigest()[:12]}"
    database[UI_SECRET_SETTINGS_COLLECTION].replace_one(
        {"_id": ref},
        {
            "_id": ref,
            "scope": "data_source",
            "value": value,
            "schemaVersion": "ui-secret-settings-v1",
            "updatedAt": _now_iso(),
        },
        upsert=True,
    )
    if old_ref and old_ref != ref:
        database[UI_SECRET_SETTINGS_COLLECTION].delete_one({"_id": old_ref})
    return ref


def _profile_env_keys(profile: Any, setting_name: str) -> tuple[str, ...]:
    value = profile.env_key_map.get(setting_name)
    return (str(value),) if value else ()


def _first_env_value(env: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _optional_str(env.get(key))
        if value:
            return value
    return None


def _should_import_env_data_sources(env: Mapping[str, str]) -> bool:
    if _truthy_env(env.get("CLAW_TRADE_SKIP_ENV_DATA_SOURCE_IMPORT")):
        return False
    explicit = _optional_str(env.get("CLAW_TRADE_IMPORT_ENV_DATA_SOURCES"))
    if explicit is not None:
        return _truthy_env(explicit)
    return False


def _truthy_env(value: object) -> bool:
    text = _optional_str(value)
    if text is None:
        return False
    return text.lower() not in {"0", "false", "no", "off"}


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


def _payload(doc: object) -> Mapping[str, Any]:
    if not isinstance(doc, Mapping):
        return {}
    payload = doc.get("payload")
    return payload if isinstance(payload, Mapping) else {}


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


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
