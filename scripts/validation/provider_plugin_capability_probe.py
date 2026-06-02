from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from claw_trade.data_gateway.execution.fetch_engine import FetchTask
from claw_trade.data_gateway.execution.managed_http import ManagedHttp, UrllibHttpClient
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.ui_backend.mongo_settings_store import (
    MongoDataSourceStore,
    MongoSecretStore,
    UI_DATA_SOURCE_SETTINGS_COLLECTION,
    UI_SECRET_SETTINGS_COLLECTION,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ENV = ROOT / ".runtime" / "dev-services" / "runtime.env"
SYMBOLS = {
    "CN_A": "600519.SH",
    "US": "AAPL",
    "HK": "00700.HK",
    "CRYPTO": "BTCUSDT",
}
START = date(2024, 6, 1)
END = date(2026, 6, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    _load_runtime_env()
    resolver = _credential_resolver()
    managed_http = ManagedHttp(UrllibHttpClient())
    results: list[dict[str, Any]] = []
    for plugin in iter_minimal_market_plugins():
        caps = plugin.capabilities()
        for endpoint in caps.endpoints:
            task = _task_for(plugin_id=caps.provider_id, version=caps.plugin_version, endpoint=endpoint)
            result = plugin.fetch(task, ctx=_Context(managed_http=managed_http, credential_resolver=resolver))
            rows = _rows_from_result(result)
            observed_fields = _observed_fields(rows)
            declared_fields = tuple(str(item) for item in endpoint.fields)
            results.append(
                {
                    "provider_id": caps.provider_id,
                    "endpoint_id": endpoint.endpoint_id,
                    "market": endpoint.market,
                    "data_type": endpoint.data_type,
                    "source_role": endpoint.source_role,
                    "http_visibility": endpoint.http_visibility,
                    "declared_fields": declared_fields,
                    "status": _status_value(result.status),
                    "row_count": getattr(result, "row_count", None),
                    "observed_fields": observed_fields,
                    "missing_declared_fields": tuple(field for field in declared_fields if field not in observed_fields),
                    "extra_observed_fields": tuple(field for field in observed_fields if field not in declared_fields),
                    "http_observation_count": len(tuple(getattr(result, "http_observations", ()) or ())),
                    "error": _error_text(result),
                }
            )
    payload = {
        "schema_version": "provider-plugin-capability-probe-v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "symbols": SYMBOLS,
        "date_window": {"start": START.isoformat(), "end": END.isoformat()},
        "configured_sources": _configured_source_summary(),
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    _print_summary(payload)
    return 0


class _Context:
    def __init__(self, *, managed_http: ManagedHttp, credential_resolver: Any) -> None:
        self.managed_http = managed_http
        self.credential_resolver = credential_resolver


def _task_for(*, plugin_id: str, version: str, endpoint: Any) -> FetchTask:
    market = str(endpoint.market)
    return FetchTask(
        batch_id=f"probe:{plugin_id}:{endpoint.endpoint_id}",
        provider_id=plugin_id,
        endpoint_id=str(endpoint.endpoint_id),
        market=market,
        data_type=str(endpoint.data_type),
        granularity=str(endpoint.granularity[0] if endpoint.granularity else ""),
        symbol_ids=(SYMBOLS[market],),
        date_range_start=START,
        date_range_end=END,
        fields=tuple(str(item) for item in endpoint.fields),
        provider_config_version=version,
        params={},
    )


def _rows_from_result(result: Any) -> tuple[Mapping[str, Any], ...]:
    payload = getattr(result, "payload", None)
    if not isinstance(payload, Mapping):
        return ()
    rows = payload.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return ()
    return tuple(row for row in rows if isinstance(row, Mapping))


def _observed_fields(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    keys: list[str] = []
    for row in rows[:3]:
        for key in row:
            text = str(key)
            if text not in keys:
                keys.append(text)
    return tuple(keys)


def _status_value(status: Any) -> str:
    value = getattr(status, "value", None)
    if isinstance(value, str):
        return value
    return str(status)


def _error_text(result: Any) -> str | None:
    error = getattr(result, "error", None)
    if error is None:
        return None
    return str(error)[:300]


def _credential_resolver() -> DataSourceCredentialResolver:
    db = _mongo_db()
    return DataSourceCredentialResolver(
        data_source_store=MongoDataSourceStore(db[UI_DATA_SOURCE_SETTINGS_COLLECTION]),
        secret_store=MongoSecretStore(db[UI_SECRET_SETTINGS_COLLECTION]),
    )


def _configured_source_summary() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for doc in _mongo_db()[UI_DATA_SOURCE_SETTINGS_COLLECTION].find({}):
        record = doc.get("record", doc) if isinstance(doc, Mapping) else {}
        rows.append(
            {
                "supported_type": record.get("supported_type"),
                "enabled": bool(record.get("enabled")),
                "has_credential_ref": bool(record.get("credential_ref")),
                "has_endpoint_url": bool(record.get("endpoint_url") or record.get("endpointUrl")),
            }
        )
    return tuple(rows)


def _mongo_db() -> Any:
    from pymongo import MongoClient

    uri = os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("DATA_GATEWAY_MONGODB_URI missing")
    db_name = os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip() or os.environ.get("CN_A_MONGODB_DATABASE", "").strip() or "claw_trade"
    return MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]


def _load_runtime_env() -> None:
    if not RUNTIME_ENV.exists():
        return
    for line in RUNTIME_ENV.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _print_summary(payload: Mapping[str, Any]) -> None:
    counts: dict[str, int] = {}
    for result in payload["results"]:
        status = str(result["status"])
        counts[status] = counts.get(status, 0) + 1
    print("provider_plugin_probe=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    for result in payload["results"]:
        print(
            "|".join(
                (
                    str(result["provider_id"]),
                    str(result["endpoint_id"]),
                    str(result["status"]),
                    "declared=" + ",".join(result["declared_fields"]),
                    "observed=" + ",".join(result["observed_fields"][:12]),
                    "missing=" + ",".join(result["missing_declared_fields"]),
                    "extra=" + ",".join(result["extra_observed_fields"][:12]),
                )
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
