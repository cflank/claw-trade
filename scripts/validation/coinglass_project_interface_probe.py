from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from claw_trade.data_gateway.execution.fetch_engine import FetchTask
from claw_trade.data_gateway.execution.managed_http import ManagedHttp, RequestsHttpClient
from claw_trade.data_gateway.models import Market
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint
from claw_trade.data_gateway.planner import plan_public_data_requests
from claw_trade.data_gateway.public_api import PublicDataRequest, PublicRequestPriority, public_api_contracts
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.providers.plugins.official_api import OFFICIAL_API_DATA_TYPE, OFFICIAL_API_SOURCE_SPECS, OfficialApiProviderPlugin
from claw_trade.ui_backend.mongo_settings_store import (
    UI_DATA_SOURCE_SETTINGS_COLLECTION,
    UI_SECRET_SETTINGS_COLLECTION,
    MongoDataSourceStore,
    MongoSecretStore,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ENV = ROOT / ".runtime" / "dev-services" / "runtime.env"
DEFAULT_START = date(2026, 5, 15)
DEFAULT_END = date(2026, 6, 15)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--sleep-seconds", type=float, default=8.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output")
    parser.add_argument("--catalog-only", action="store_true")
    args = parser.parse_args()

    _load_runtime_env()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    rows = _coverage_rows(symbol=args.symbol, quote=args.quote, start=start, end=end)
    if not args.catalog_only:
        _run_live(rows=rows, symbol=args.symbol, quote=args.quote, start=start, end=end, sleep_seconds=max(args.sleep_seconds, 0.0))

    payload = {
        "schema_version": "coinglass-project-interface-probe-v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "symbol": args.symbol.strip().upper(),
        "quote": args.quote.strip().upper(),
        "date_window": {"start": start.isoformat(), "end": end.isoformat()},
        "catalog_only": bool(args.catalog_only),
        "rows": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    summary_output = Path(args.summary_output) if args.summary_output else output.with_suffix(".md")
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(_markdown_summary(payload), encoding="utf-8")
    print(_console_summary(payload))
    return 0


def _coverage_rows(*, symbol: str, quote: str, start: date, end: date) -> list[dict[str, Any]]:
    endpoints = tuple(iter_official_catalog_endpoints())
    crypto_contracts = [contract for contract in public_api_contracts() if contract.market == Market.CRYPTO]
    rows: list[dict[str, Any]] = []
    for contract in crypto_contracts:
        matches = _coinglass_endpoints_for_api(contract.api_id, endpoints=endpoints)
        if not matches:
            rows.append(
                {
                    "api_id": contract.api_id,
                    "data_type": contract.output_contract["dataset"],
                    "project_required_fields": tuple(contract.output_contract["required_fields"]),
                    "coinglass_status": "not_implemented",
                    "endpoint_id": "",
                    "path": "",
                    "declared_fields": (),
                    "doc": "",
                }
            )
            continue
        for endpoint, output in matches:
            rows.append(
                {
                    "api_id": contract.api_id,
                    "data_type": contract.output_contract["dataset"],
                    "project_required_fields": tuple(contract.output_contract["required_fields"]),
                    "coinglass_status": "implemented",
                    "endpoint_id": endpoint.endpoint_id,
                    "path": endpoint.official_path_or_api_name,
                    "declared_fields": tuple(output.get("fields") or ()),
                    "doc": endpoint.official_doc_ref,
                    "plan_status": _plan_status(contract.api_id, endpoint=endpoint, symbol=symbol, quote=quote, start=start, end=end),
                }
            )
    return rows


def _coinglass_endpoints_for_api(
    api_id: str,
    *,
    endpoints: Sequence[OfficialEndpoint],
) -> tuple[tuple[OfficialEndpoint, Mapping[str, Any]], ...]:
    item = api_id.rsplit(".", 1)[-1]
    matches: list[tuple[OfficialEndpoint, Mapping[str, Any]]] = []
    for endpoint in endpoints:
        if endpoint.source_type != "coinglass":
            continue
        for output in _outputs(endpoint):
            public_ids = tuple(str(value) for value in output.get("public_api_ids") or ())
            if api_id in public_ids or item in public_ids or any(value.endswith(f".{item}") for value in public_ids):
                matches.append((endpoint, output))
    return tuple(matches)


def _plan_status(
    api_id: str,
    *,
    endpoint: OfficialEndpoint,
    symbol: str,
    quote: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    request = _request_for(api_id=api_id, symbol=symbol, quote=quote, start=start, end=end)
    plan = plan_public_data_requests((request,), catalog_endpoints=(endpoint,))
    return {
        "planned": bool(plan.planned_calls),
        "skipped": tuple(gap.human_readable for gap in plan.skipped_needs),
        "params": plan.planned_calls[0].params if plan.planned_calls else {},
    }


def _run_live(*, rows: list[dict[str, Any]], symbol: str, quote: str, start: date, end: date, sleep_seconds: float) -> None:
    resolver = _credential_resolver()
    managed_http = ManagedHttp(RequestsHttpClient())
    plugin = OfficialApiProviderPlugin(next(spec for spec in OFFICIAL_API_SOURCE_SPECS if spec.source_type == "coinglass"))
    seen: set[tuple[str, str]] = set()
    for row in rows:
        endpoint_id = str(row.get("endpoint_id") or "")
        api_id = str(row.get("api_id") or "")
        if not endpoint_id:
            continue
        key = (api_id, endpoint_id)
        if key in seen:
            continue
        seen.add(key)
        live = _live_one(
            plugin=plugin,
            resolver=resolver,
            managed_http=managed_http,
            api_id=api_id,
            endpoint_id=endpoint_id,
            symbol=symbol,
            quote=quote,
            start=start,
            end=end,
        )
        row["live"] = live
        print(
            f"{api_id}|{endpoint_id}|{live['status']}|rows={live.get('row_count')}|error={live.get('error') or live.get('error_code') or ''}",
            flush=True,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)


def _live_one(
    *,
    plugin: OfficialApiProviderPlugin,
    resolver: DataSourceCredentialResolver,
    managed_http: ManagedHttp,
    api_id: str,
    endpoint_id: str,
    symbol: str,
    quote: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    endpoint = next(item for item in iter_official_catalog_endpoints() if item.source_type == "coinglass" and item.endpoint_id == endpoint_id)
    request = _request_for(api_id=api_id, symbol=symbol, quote=quote, start=start, end=end)
    plan = plan_public_data_requests((request,), catalog_endpoints=(endpoint,))
    if not plan.planned_calls:
        return {"status": "plan_failed", "error": "; ".join(gap.human_readable for gap in plan.skipped_needs)}
    call_spec = plan.planned_calls[0]
    task = FetchTask(
        batch_id=f"coinglass-project-probe:{api_id}:{endpoint_id}",
        provider_id=endpoint.provider_id,
        endpoint_id=endpoint.endpoint_id,
        market="CRYPTO",
        data_type=OFFICIAL_API_DATA_TYPE,
        granularity="event",
        symbol_ids=(f"{symbol.strip().upper()}/{quote.strip().upper()}",),
        date_range_start=start,
        date_range_end=end,
        fields=("raw_payload",),
        provider_config_version="probe",
        params={"provider_call_spec": call_spec.model_dump(mode="python")},
        deadline_at=datetime.now(tz=UTC) + timedelta(seconds=90),
    )
    result = plugin.fetch(task, ctx=_Context(managed_http=managed_http, credential_resolver=resolver))
    rows = _rows_from_result(result)
    observed_fields = _observed_fields(rows)
    declared_fields = tuple(str(field) for field in _declared_fields(endpoint=endpoint, api_id=api_id))
    missing_declared_fields = tuple(field for field in declared_fields if field not in observed_fields)
    status = _status_value(result.status)
    if status == "success" and missing_declared_fields:
        status = "schema_mismatch"
    return {
        "status": status,
        "error_code": getattr(result, "error_code", None),
        "error": _error_text(result),
        "row_count": getattr(result, "row_count", None),
        "observed_fields": observed_fields,
        "missing_declared_fields": missing_declared_fields,
        "http_status_codes": tuple(
            getattr(observation, "status_code", None)
            for observation in tuple(getattr(result, "http_observations", ()) or ())
        ),
        "http_error_codes": tuple(
            getattr(observation, "error_code", None)
            for observation in tuple(getattr(result, "http_observations", ()) or ())
            if getattr(observation, "error_code", None)
        ),
    }


class _Context:
    def __init__(self, *, managed_http: ManagedHttp, credential_resolver: DataSourceCredentialResolver) -> None:
        self.managed_http = managed_http
        self.credential_resolver = credential_resolver


def _request_for(*, api_id: str, symbol: str, quote: str, start: date, end: date) -> PublicDataRequest:
    return PublicDataRequest(
        request_id=f"coinglass-probe:{api_id}",
        item=api_id.rsplit(".", 1)[-1],
        market=Market.CRYPTO,
        instrument=f"{symbol.strip().upper()}/{quote.strip().upper()}",
        time_range_start=start,
        time_range_end=end,
        granularity=None,
        priority=PublicRequestPriority.NORMAL,
        requested_by_worker="validation_probe",
        purpose="provider_interface_probe",
        deadline_at=datetime.now(tz=UTC) + timedelta(seconds=180),
        consumer="maintenance",
    )


def _outputs(endpoint: OfficialEndpoint) -> tuple[Mapping[str, Any], ...]:
    outputs = endpoint.response_shape.get("outputs")
    if not isinstance(outputs, Sequence) or isinstance(outputs, (str, bytes, bytearray)):
        return ()
    return tuple(output for output in outputs if isinstance(output, Mapping))


def _declared_fields(*, endpoint: OfficialEndpoint, api_id: str) -> tuple[str, ...]:
    for output in _outputs(endpoint):
        public_ids = tuple(str(value) for value in output.get("public_api_ids") or ())
        item = api_id.rsplit(".", 1)[-1]
        if api_id in public_ids or item in public_ids or any(value.endswith(f".{item}") for value in public_ids):
            return tuple(str(field) for field in output.get("fields") or ())
    return ()


def _credential_resolver() -> DataSourceCredentialResolver:
    db = _mongo_db()
    return DataSourceCredentialResolver(
        data_source_store=MongoDataSourceStore(db[UI_DATA_SOURCE_SETTINGS_COLLECTION]),
        secret_store=MongoSecretStore(db[UI_SECRET_SETTINGS_COLLECTION]),
    )


def _mongo_db() -> Any:
    from pymongo import MongoClient

    uri = os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("DATA_GATEWAY_MONGODB_URI missing")
    db_name = os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip() or os.environ.get("CN_A_MONGODB_DATABASE", "").strip() or "claw_trade"
    return MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]


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
    for row in rows[:5]:
        for key in row:
            text = str(key)
            if text not in keys:
                keys.append(text)
    return tuple(keys)


def _status_value(status: Any) -> str:
    value = getattr(status, "value", None)
    return value if isinstance(value, str) else str(status)


def _error_text(result: Any) -> str | None:
    error = getattr(result, "error", None)
    if error is not None:
        return str(error)[:500]
    return None


def _load_runtime_env() -> None:
    if not RUNTIME_ENV.exists():
        return
    for line in RUNTIME_ENV.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _markdown_summary(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Coinglass Project Interface Probe",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- symbol: `{payload['symbol']}/{payload['quote']}`",
        f"- catalog_only: `{payload['catalog_only']}`",
        "",
        "| api | endpoint | status | rows | missing declared fields | error |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in payload["rows"]:
        live = row.get("live") or {}
        lines.append(
            "| "
            + " | ".join(
                (
                    str(row.get("api_id") or ""),
                    str(row.get("endpoint_id") or row.get("coinglass_status") or ""),
                    str(live.get("status") or row.get("coinglass_status") or ""),
                    str(live.get("row_count") or ""),
                    ", ".join(str(item) for item in live.get("missing_declared_fields") or ()),
                    str(live.get("error") or live.get("error_code") or ""),
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _console_summary(payload: Mapping[str, Any]) -> str:
    counts: dict[str, int] = {}
    for row in payload["rows"]:
        live = row.get("live") or {}
        status = str(live.get("status") or row.get("coinglass_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return "coinglass_project_probe=" + json.dumps(counts, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
