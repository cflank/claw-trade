from __future__ import annotations

import os
import sys
from typing import Any, Mapping
from urllib.parse import urlparse

UI_EMBEDDING_SETTINGS_COLLECTION = "ui_embedding_settings"
UI_REPORT_MODEL_CONFIG_COLLECTION = "ui_report_model_config"


def export_runtime_settings_from_mongo() -> dict[str, str]:
    database = _open_settings_database_from_env()
    if database is None:
        return {}
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
    return {key: str(payload.get(key, "") or "").strip() for key in keys}


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


if __name__ == "__main__":
    raise SystemExit(main())
