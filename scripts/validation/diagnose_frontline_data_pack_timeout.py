from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from claw_trade.reports import data_pack_bridge as bridge


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    @contextmanager
    def span(self, step: str, **details: Any) -> Any:
        start = time.monotonic()
        event: dict[str, Any] = {"step": step, "started_at": _now_iso(), **details}
        try:
            yield
        except Exception as exc:
            event["error"] = repr(exc)
            raise
        finally:
            event["finished_at"] = _now_iso()
            event["elapsed_ms"] = int((time.monotonic() - start) * 1000)
            self.events.append(event)


def _wrap_method(recorder: Recorder, owner: Any, method_name: str, step: str, detail_builder: Callable[..., dict[str, Any]] | None = None) -> None:
    original = getattr(owner, method_name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        details = detail_builder(*args, **kwargs) if detail_builder else {}
        with recorder.span(step, **details):
            return original(*args, **kwargs)

    setattr(owner, method_name, wrapped)


def _batch_details(batch: Any) -> dict[str, Any]:
    return {
        "batch_id": getattr(batch, "batch_id", None),
        "provider_id": getattr(batch, "provider_id", None),
        "endpoint_id": getattr(batch, "endpoint_id", None),
        "market": _value(getattr(batch, "market", None)),
        "data_type": getattr(batch, "data_type", None),
        "granularity": getattr(batch, "granularity", None),
        "request_ids": list(getattr(batch, "request_ids", ()) or ()),
        "symbol_ids": list(getattr(batch, "symbol_ids", ()) or ()),
        "gate_key": getattr(batch, "single_flight_key", None),
    }


def _payload_details(result: Any) -> dict[str, Any]:
    payload = getattr(result, "payload", None)
    details: dict[str, Any] = {
        "payload_type": type(payload).__name__,
    }
    if isinstance(payload, dict):
        rows = payload.get("rows")
        details["payload_key_count"] = len(payload)
        details["payload_row_count"] = len(rows) if isinstance(rows, list) else None
    elif isinstance(payload, list):
        details["payload_row_count"] = len(payload)
    return details


def _normalized_rows_details(rows: Any) -> dict[str, Any]:
    try:
        row_count = len(rows)
    except TypeError:
        row_count = None
    return {"normalized_row_count": row_count}


def _result_dataset(request_id: str) -> str:
    parts = request_id.split(":")
    if len(parts) < 5:
        return ""
    return parts[4]


def _result_domain(request_id: str) -> str:
    parts = request_id.split(":")
    if len(parts) < 5:
        return ""
    return parts[2]


def _row_field_sample(rows: Any) -> list[str]:
    if not isinstance(rows, list | tuple):
        return []
    fields: list[str] = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        for key in row:
            if key not in fields:
                fields.append(str(key))
            if len(fields) >= 20:
                return fields
    return fields


def _compact_gap(gap: Any, *, fallback_request_id: str) -> dict[str, Any]:
    if not isinstance(gap, dict):
        return {"request_id": fallback_request_id, "raw": str(gap)}
    return {
        "gap_id": gap.get("gap_id"),
        "request_id": gap.get("request_id") or fallback_request_id,
        "domain": _result_domain(str(gap.get("request_id") or fallback_request_id)),
        "dataset": _result_dataset(str(gap.get("request_id") or fallback_request_id)),
        "severity": gap.get("severity"),
        "reason": gap.get("reason"),
        "market": gap.get("market"),
        "symbol_id": gap.get("symbol_id"),
        "data_type": gap.get("data_type"),
        "granularity": gap.get("granularity"),
        "required_fields": list(gap.get("required_fields") or ()),
        "provider_ids_tried": list(gap.get("provider_ids_tried") or ()),
        "evidence_refs": list(gap.get("evidence_refs") or ()),
        "human_readable": gap.get("human_readable"),
    }


def _compact_data_results(data_results: Any) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    if not isinstance(data_results, list | tuple):
        return compact
    for item in data_results:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("request_id") or "")
        rows = item.get("rows") or ()
        dataset_refs = item.get("dataset_refs") or ()
        raw_refs = item.get("raw_refs") or ()
        attempt_refs = item.get("attempt_refs") or ()
        gaps = item.get("gaps") or ()
        compact.append(
            {
                "request_id": request_id,
                "domain": _result_domain(request_id),
                "dataset": _result_dataset(request_id),
                "status": item.get("status"),
                "row_count": len(rows) if isinstance(rows, list | tuple) else None,
                "row_fields_sample": _row_field_sample(rows),
                "dataset_ref_count": len(dataset_refs) if isinstance(dataset_refs, list | tuple) else None,
                "dataset_refs_sample": list(dataset_refs[:5]) if isinstance(dataset_refs, list | tuple) else [],
                "raw_ref_count": len(raw_refs) if isinstance(raw_refs, list | tuple) else None,
                "attempt_ref_count": len(attempt_refs) if isinstance(attempt_refs, list | tuple) else None,
                "attempt_refs_sample": list(attempt_refs[:5]) if isinstance(attempt_refs, list | tuple) else [],
                "gap_count": len(gaps) if isinstance(gaps, list | tuple) else None,
                "gaps": [_compact_gap(gap, fallback_request_id=request_id) for gap in gaps] if isinstance(gaps, list | tuple) else [],
                "source_summary": item.get("source_summary"),
                "freshness": item.get("freshness"),
            }
        )
    return compact


def _compact_gaps(data_results: Any) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not isinstance(data_results, list | tuple):
        return gaps
    for item in data_results:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("request_id") or "")
        for gap in item.get("gaps") or ():
            gaps.append(_compact_gap(gap, fallback_request_id=request_id))
    return gaps


def _source_call_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        if event.get("step") != "ingest.ingest":
            continue
        calls.append(
            {
                "provider_id": event.get("provider_id"),
                "endpoint_id": event.get("endpoint_id"),
                "data_type": event.get("data_type"),
                "granularity": event.get("granularity"),
                "symbol_ids": event.get("symbol_ids"),
                "request_ids": event.get("request_ids"),
                "fetch_status": event.get("fetch_status"),
                "elapsed_ms": event.get("elapsed_ms"),
            }
        )
    return calls


def _attempt_details(*args: Any, **kwargs: Any) -> dict[str, Any]:
    batch = kwargs.get("batch")
    details = _batch_details(batch) if batch is not None else {}
    raw_refs = kwargs.get("raw_refs") or ()
    dataset_refs = kwargs.get("dataset_refs") or ()
    gaps = kwargs.get("gaps") or ()
    details.update(
        {
            "raw_ref_count": len(raw_refs),
            "dataset_ref_count": len(dataset_refs),
            "gap_count": len(gaps),
            "remote_success": kwargs.get("remote_success"),
        }
    )
    return details


def _value(value: Any) -> Any:
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else value


def _wrap_api(recorder: Recorder, api: Any) -> Any:
    service = api._data_service
    _wrap_method(recorder, service, "plan_batch", "service.plan_batch", lambda requests: {"request_count": len(requests)})
    _wrap_method(recorder, service, "execute_plan", "service.execute_plan", lambda plan: {"request_count": len(plan.query_plan.normalized_requests)})
    _wrap_method(recorder, service.warehouse, "check", "warehouse.check", lambda checks, coverage=None: {"check_count": len(checks)})
    _wrap_method(recorder, service.warehouse, "recheck", "warehouse.recheck", lambda checks, coverage=None: {"check_count": len(checks)})
    _wrap_method(recorder, service.provider_selector, "select_candidates", "provider_selector.select_candidates", lambda gaps, plan: {"gap_count": len(gaps)})
    _wrap_method(recorder, service.provider_selector, "read_capabilities", "provider_selector.read_capabilities", lambda candidates: {"candidate_count": len(candidates)})
    _wrap_method(recorder, service.coalescer, "coalesce", "coalescer.coalesce", lambda gaps, candidates, capabilities: {"gap_count": len(gaps), "candidate_count": len(candidates)})
    _wrap_method(recorder, service.batch_planner, "build_batches", "batch_planner.build_batches", lambda groups, capabilities: {"group_count": len(groups)})
    _wrap_method(recorder, service.execution_gate, "enter", "execution_gate.enter", lambda batch: _batch_details(batch))
    _wrap_method(recorder, service.execution_gate, "publish_shared_result", "execution_gate.publish_shared_result", lambda single_flight_key, owner_token, ingest: {"single_flight_key": single_flight_key, "ingest_status": getattr(ingest, "status", None)})
    _wrap_method(recorder, service.fetch_engine, "fetch", "fetch_engine.fetch", lambda batch: _batch_details(batch))
    _wrap_method(recorder, service.ingest.raw_store, "save", "ingest.raw_store.save", lambda result, batch: {**_batch_details(batch), **_payload_details(result)})
    _wrap_method(recorder, service.ingest.normalizer, "normalize", "ingest.normalizer.normalize", lambda result, batch, raw_refs: {**_batch_details(batch), **_payload_details(result), "raw_ref_count": len(raw_refs)})
    _wrap_method(recorder, service.ingest.normalized_store, "upsert", "ingest.normalized_store.upsert", _normalized_rows_details)
    _wrap_method(recorder, service.ingest.attempt_log, "record", "ingest.attempt_log.record", _attempt_details)
    _wrap_method(recorder, service.ingest, "ingest", "ingest.ingest", lambda fetch_result, batch: {**_batch_details(batch), "fetch_status": getattr(fetch_result, "status", None)})
    return api


def _tool_input(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ticker": args.ticker,
        "company_name": args.company_name,
        "market": args.market,
        "currency": args.currency,
        "currency_symbol": args.currency_symbol,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "current_date": args.current_date,
    }


def _runtime_context(args: argparse.Namespace) -> dict[str, Any]:
    run_id = f"diagnose-{args.market.lower()}-{args.domain}-{int(time.time())}"
    return {
        "run_id": run_id,
        "call_id": f"{run_id}-call",
        "worker_id": args.worker_id,
        "stage": "frontline",
        "profile": args.market,
        "tool_name": args.tool_name,
        "pack_domain": args.domain,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "current_date": args.current_date,
        "evidence_root": str(Path(".runtime/dev-services/diagnostics").resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company-name", default="")
    parser.add_argument("--currency", default="")
    parser.add_argument("--currency-symbol", default="")
    parser.add_argument("--current-date", default="2026-06-01")
    parser.add_argument("--start-date", default="2025-06-01")
    parser.add_argument("--end-date", default="2026-06-01")
    parser.add_argument("--worker-id", default="market_analyst")
    parser.add_argument("--tool-name", default="claw_get_market_pack")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    recorder = Recorder()
    original_build_data_api = bridge.build_data_api_from_env

    def instrumented_build_data_api() -> Any:
        with recorder.span("bridge.build_data_api_from_env"):
            api = original_build_data_api()
        return _wrap_api(recorder, api)

    bridge.build_data_api_from_env = instrumented_build_data_api
    started = time.monotonic()
    with recorder.span("bridge.run_frontline_data_pack", market=args.market, domain=args.domain):
        result = bridge.run_frontline_data_pack(_tool_input(args), _runtime_context(args))

    data_results = result.get("data_results", [])
    output = {
        "schema": "frontline-data-pack-timeout-diagnosis-v1",
        "generated_at": _now_iso(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "result_status": result.get("status"),
        "result_ok": result.get("ok"),
        "data_result_statuses": [item.get("status") for item in data_results if isinstance(item, dict)],
        "data_results_summary": _compact_data_results(data_results),
        "gaps_summary": _compact_gaps(data_results),
        "source_calls_summary": _source_call_summary(recorder.events),
        "events": recorder.events,
        "env": {
            "CN_A_PROVIDER_TOTAL_TIMEOUT_MS": os.environ.get("CN_A_PROVIDER_TOTAL_TIMEOUT_MS"),
            "DATA_GATEWAY_MONGODB_DATABASE": os.environ.get("DATA_GATEWAY_MONGODB_DATABASE"),
        },
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": args.output, "elapsed_ms": output["elapsed_ms"], "result_status": output["result_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
