from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import signal
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from claw_trade.data_gateway.api import DataAPI
from claw_trade.data_gateway.coordination.batch_planner import ProviderBatchPlanner
from claw_trade.data_gateway.coordination.coalescer import RequestCoalescer
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.execution import ProviderResultCache
from claw_trade.data_gateway.execution.fetch_engine import FetchEngine
from claw_trade.data_gateway.execution.gate import ExecutionGate
from claw_trade.data_gateway.execution.managed_http import ManagedHttp, UrllibHttpClient
from claw_trade.data_gateway.execution.rate_limit_policy import RateLimitPolicyResolver
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.models import DataRequest, FetchResult, IngestResult
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse
from claw_trade.ui_backend.mongo_settings_store import (
    UI_DATA_SOURCE_SETTINGS_COLLECTION,
    UI_SECRET_SETTINGS_COLLECTION,
    MongoDataSourceStore,
    MongoSecretStore,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ENV = ROOT / ".runtime" / "dev-services" / "runtime.env"
MARKETS = ("CN_A", "US", "HK", "CRYPTO")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=MARKETS, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output")
    parser.add_argument("--run-id")
    args = parser.parse_args()

    _load_runtime_env()
    run_id = args.run_id or f"data-layer-fullchain-{args.market.lower()}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_output = Path(args.summary_output) if args.summary_output else output.with_suffix(".md")

    main_db = _mongo_db()
    scratch_db_name = f"{_mongo_db_name()}_{run_id.replace('-', '_').replace(':', '_').lower()}"
    scratch_db = _mongo_client()[scratch_db_name]
    repository = DatasetRepository.from_database(scratch_db)
    registry = build_minimal_provider_registry()
    recorder = Recorder(run_id=run_id, market=args.market)
    managed_http = RecordingManagedHttp(ManagedHttp(UrllibHttpClient()), recorder)
    credential_resolver = DataSourceCredentialResolver(
        data_source_store=MongoDataSourceStore(main_db[UI_DATA_SOURCE_SETTINGS_COLLECTION]),
        secret_store=MongoSecretStore(main_db[UI_SECRET_SETTINGS_COLLECTION]),
    )
    rate_limiter = RateLimiter(repository)
    service = DataService(
        query_planner=RecordingQueryPlanner(QueryPlanner(), recorder),
        warehouse=RecordingWarehouse(Warehouse(repository), recorder),
        provider_selector=RecordingProviderSelector(
            ProviderSelector(registry, credential_resolver=credential_resolver),
            registry,
            recorder,
        ),
        coalescer=RecordingCoalescer(RequestCoalescer(), recorder),
        batch_planner=RecordingBatchPlanner(
            ProviderBatchPlanner(
                rate_limit_policy_resolver=RateLimitPolicyResolver(data_source_settings=credential_resolver),
            ),
            recorder,
        ),
        execution_gate=RecordingExecutionGate(
            ExecutionGate(
                cache=ProviderResultCache(repository),
                rate_limiter=rate_limiter,
                single_flight=SingleFlight(repository),
            ),
            recorder,
        ),
        fetch_engine=RecordingFetchEngine(
            FetchEngine(
                registry,
                managed_http=managed_http,
                credential_resolver=credential_resolver,
                rate_limiter=rate_limiter,
            ),
            recorder,
            rate_limit_db_name=scratch_db_name,
        ),
        ingest=RecordingIngest(
            IngestPipeline(
                raw_store=RawStore(repository=repository),
                normalizer=Normalizer(),
                normalized_store=NormalizedStore(repository=repository),
                attempt_log=AttemptLog(repository=repository),
            ),
            recorder,
        ),
    )
    api = DataAPI(service)
    requests = _requests_for(args.market, run_id=run_id)
    results = api.get_data_batch(requests)

    capabilities = _capabilities_for_market(args.market)
    provider_matrix = _provider_matrix(capabilities, requests, recorder.events)
    payload = {
        "schema_version": "data-layer-full-chain-market-probe-v1",
        "run_id": run_id,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "market": args.market,
        "scratch_database": scratch_db_name,
        "runtime_profile": _runtime_profile(),
        "configured_sources": _configured_source_summary(main_db),
        "request_count": len(requests),
        "requests": [_request_summary(req) for req in requests],
        "results": [_result_summary(result) for result in results],
        "provider_matrix": provider_matrix,
        "events": recorder.events,
        "collection_counts": _collection_counts(scratch_db),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(_markdown_summary(payload), encoding="utf-8")
    print(_console_summary(payload))
    return 0


class Recorder:
    def __init__(self, *, run_id: str, market: str) -> None:
        self.run_id = run_id
        self.market = market
        self.events: list[dict[str, Any]] = []

    def record(self, step: str, **payload: Any) -> None:
        self.events.append(
            {
                "at": datetime.now(tz=UTC).isoformat(),
                "step": step,
                **_jsonable(payload),
            }
        )


class RecordingManagedHttp:
    def __init__(self, inner: ManagedHttp, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def send_capture(self, request: Any) -> Any:
        self._recorder.record(
            "managed_http.request",
            method=getattr(request, "method", None),
            host=getattr(request, "host", None),
            path=getattr(request, "path", None),
            query_keys=tuple(sorted(str(key) for key in (getattr(request, "query", None) or {}).keys())),
        )
        capture = self._inner.send_capture(request)
        observation = capture.observation
        self._recorder.record(
            "managed_http.response",
            method=getattr(observation, "method", None),
            host=getattr(observation, "host", None),
            path=getattr(observation, "path", None),
            request_key=getattr(observation, "request_key", None),
            status_code=getattr(observation, "status_code", None),
            error_code=getattr(observation, "error_code", None),
            quota_signal=getattr(observation, "quota_signal", None),
        )
        return capture


class RecordingQueryPlanner:
    def __init__(self, inner: QueryPlanner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def validate_and_normalize(self, request: DataRequest) -> Any:
        return self.validate_and_normalize_many((request,))

    def validate_and_normalize_many(self, requests: Sequence[DataRequest]) -> Any:
        self._recorder.record("query_planner.input", request_ids=tuple(req.request_id for req in requests))
        plan = self._inner.validate_and_normalize_many(requests)
        self._recorder.record(
            "query_planner.output",
            request_ids=tuple(req.request_id for req in plan.normalized_requests),
            warehouse_checks=tuple(_check_summary(check) for check in plan.warehouse_checks),
            expected_outputs=tuple(plan.expected_outputs),
        )
        return plan


class RecordingWarehouse:
    def __init__(self, inner: Warehouse, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def check(self, checks: Sequence[Any], coverage: Any) -> Any:
        self._recorder.record("warehouse.check.start", checks=tuple(_check_summary(check) for check in checks))
        result = self._inner.check(checks, coverage)
        self._recorder.record("warehouse.check.end", **_warehouse_summary(result))
        return result

    def recheck(self, checks: Sequence[Any], coverage: Any) -> Any:
        self._recorder.record("warehouse.recheck.start", checks=tuple(_check_summary(check) for check in checks))
        result = self._inner.recheck(checks, coverage)
        self._recorder.record("warehouse.recheck.end", **_warehouse_summary(result))
        return result


class RecordingProviderSelector:
    def __init__(self, inner: ProviderSelector, registry: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._registry = registry
        self._recorder = recorder

    def select_candidates(self, gaps: Sequence[Any], plan: Any) -> tuple[Any, ...]:
        self._recorder.record("provider_selector.start", gaps=tuple(_gap_summary(gap) for gap in gaps))
        candidates = self._inner.select_candidates(gaps, plan)
        self._recorder.record(
            "provider_selector.end",
            candidates=tuple(_candidate_summary(candidate) for candidate in candidates),
        )
        return candidates

    def read_capabilities(self, candidates: Sequence[Any]) -> Any:
        snapshot = self._inner.read_capabilities(candidates)
        self._recorder.record(
            "provider_selector.capabilities",
            provider_ids=tuple(dict.fromkeys(getattr(candidate, "provider_id") for candidate in candidates)),
            count=len(snapshot.list()),
        )
        return snapshot


class RecordingCoalescer:
    def __init__(self, inner: RequestCoalescer, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def coalesce(self, gaps: Sequence[Any], candidates: Sequence[Any], capabilities: Any) -> tuple[Any, ...]:
        groups = self._inner.coalesce(gaps, candidates, capabilities)
        self._recorder.record("coalescer.end", groups=tuple(_group_summary(group) for group in groups))
        return groups


class RecordingBatchPlanner:
    def __init__(self, inner: ProviderBatchPlanner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def build_batches(self, groups: Sequence[Any], capabilities: Any) -> tuple[Any, ...]:
        batches = self._inner.build_batches(groups, capabilities)
        self._recorder.record("batch_planner.end", batches=tuple(_batch_summary(batch) for batch in batches))
        return batches


class RecordingExecutionGate:
    def __init__(self, inner: ExecutionGate, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def enter(self, batch: Any) -> Any:
        self._recorder.record("execution_gate.enter", batch=_batch_summary(batch))
        decision = self._inner.enter(batch)
        self._recorder.record(
            "execution_gate.decision",
            batch=_batch_summary(batch),
            kind=getattr(decision, "kind", None),
            owner_token=bool(getattr(decision, "owner_token", None)),
            evidence_refs=tuple(getattr(decision, "evidence_refs", ()) or ()),
        )
        return decision

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: Any) -> Any:
        ok = self._inner.publish_shared_result(single_flight_key, owner_token, ingest)
        self._recorder.record(
            "execution_gate.publish_shared_result",
            single_flight_key=single_flight_key,
            ok=ok,
            ingest=_ingest_summary(ingest),
        )
        return ok


class RecordingFetchEngine:
    def __init__(self, inner: FetchEngine, recorder: Recorder, *, rate_limit_db_name: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._rate_limit_db_name = rate_limit_db_name

    def fetch(self, batch: Any) -> Any:
        self._recorder.record("fetch.start", batch=_batch_summary(batch))
        timeout_seconds = _provider_probe_timeout_seconds(batch)
        result, child_events, timeout_error = _fetch_in_child_process(
            batch,
            timeout_seconds=timeout_seconds,
            run_id=self._recorder.run_id,
            market=self._recorder.market,
            rate_limit_db_name=self._rate_limit_db_name,
        )
        self._recorder.events.extend(child_events)
        if timeout_error is not None:
            self._recorder.record("fetch.timeout", batch=_batch_summary(batch), error=timeout_error)
        self._recorder.record("fetch.end", batch=_batch_summary(batch), result=_fetch_summary(result))
        return result


def _fetch_in_child_process(
    batch: Any,
    *,
    timeout_seconds: float,
    run_id: str,
    market: str,
    rate_limit_db_name: str,
) -> tuple[FetchResult, list[dict[str, Any]], str | None]:
    if timeout_seconds <= 0:
        return FetchResult.from_error(batch, status="error", error=TimeoutError(_timeout_message("provider_probe_timeout", batch))), [], _timeout_message("provider_probe_timeout", batch)
    ctx = mp.get_context("fork")
    result_queue: mp.Queue[Any] = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_fetch_child_entrypoint, args=(batch, run_id, market, rate_limit_db_name, result_queue))
    process.start()
    deadline = monotonic() + timeout_seconds
    payload: Any | None = None
    while monotonic() < deadline:
        try:
            payload = result_queue.get(timeout=min(0.2, max(deadline - monotonic(), 0.0)))
            break
        except queue.Empty:
            if not process.is_alive():
                break
    if payload is None and process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
        message = _timeout_message("provider_probe_timeout", batch)
        return FetchResult.from_error(batch, status="error", error=TimeoutError(message)), [], message
    process.join(2)
    if payload is None:
        try:
            payload = result_queue.get_nowait()
        except queue.Empty:
            payload = None
    if payload is None:
        message = f"provider_probe_no_result:{getattr(batch, 'provider_id', 'unknown_provider')}:{getattr(batch, 'endpoint_id', 'unknown_endpoint')}"
        return FetchResult.from_error(batch, status="error", error=RuntimeError(message)), [], message
    events = list(payload.get("events", ()) or ())
    if payload.get("ok") is True:
        return FetchResult.model_validate(payload.get("result")), events, None
    message = str(payload.get("error") or "provider_probe_child_error")
    return FetchResult.from_error(batch, status="error", error=RuntimeError(message)), events, message


def _fetch_child_entrypoint(batch: Any, run_id: str, market: str, rate_limit_db_name: str, result_queue: Any) -> None:
    recorder = Recorder(run_id=run_id, market=market)
    try:
        main_db = _mongo_db()
        registry = build_minimal_provider_registry()
        managed_http = RecordingManagedHttp(ManagedHttp(UrllibHttpClient()), recorder)
        credential_resolver = DataSourceCredentialResolver(
            data_source_store=MongoDataSourceStore(main_db[UI_DATA_SOURCE_SETTINGS_COLLECTION]),
            secret_store=MongoSecretStore(main_db[UI_SECRET_SETTINGS_COLLECTION]),
        )
        repository = DatasetRepository.from_database(_mongo_client()[rate_limit_db_name])
        result = FetchEngine(
            registry,
            managed_http=managed_http,
            credential_resolver=credential_resolver,
            rate_limiter=RateLimiter(repository),
        ).fetch(batch)
        result_queue.put({"ok": True, "result": result.model_dump(mode="python"), "events": recorder.events})
    except Exception as exc:  # noqa: BLE001
        result_queue.put({"ok": False, "error": f"{type(exc).__name__}:{exc}", "events": recorder.events})


class RecordingIngest:
    def __init__(self, inner: IngestPipeline, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def ingest(self, result: Any, batch: Any) -> Any:
        self._recorder.record("ingest.start", batch=_batch_summary(batch), fetch=_fetch_summary(result))
        try:
            with _timeout_after(_float_env("CLAW_TRADE_INGEST_PROBE_TIMEOUT_SECONDS", 20.0), _timeout_message("ingest_probe_timeout", batch)):
                ingest = self._inner.ingest(result, batch)
        except TimeoutError as exc:
            ingest = IngestResult.failed("evidence_write_failed", batch_id=str(getattr(batch, "batch_id", "batch:unknown")))
            self._recorder.record("ingest.timeout", batch=_batch_summary(batch), error=str(exc))
        self._recorder.record("ingest.end", batch=_batch_summary(batch), ingest=_ingest_summary(ingest))
        return ingest

    def record_gate_result(self, batch: Any, gate: Any) -> Any:
        try:
            with _timeout_after(_float_env("CLAW_TRADE_INGEST_PROBE_TIMEOUT_SECONDS", 20.0), _timeout_message("gate_record_probe_timeout", batch)):
                ingest = self._inner.record_gate_result(batch, gate)
        except TimeoutError as exc:
            ingest = IngestResult.failed("evidence_write_failed", batch_id=str(getattr(batch, "batch_id", "batch:unknown")))
            self._recorder.record("ingest.gate_timeout", batch=_batch_summary(batch), error=str(exc))
        self._recorder.record("ingest.gate_recorded", batch=_batch_summary(batch), ingest=_ingest_summary(ingest))
        return ingest


def _requests_for(market: str, *, run_id: str) -> list[DataRequest]:
    as_of = datetime.now(tz=UTC)
    specs = _request_specs(market)
    requests: list[DataRequest] = []
    for idx, spec in enumerate(specs, start=1):
        payload = {
                "request_id": f"{run_id}:{idx:02d}:{spec['data_type']}",
                "market": market,
                "symbol_id": spec["symbol_id"],
                "timezone": spec["timezone"],
                "calendar": spec["calendar"],
                "base_asset": spec.get("base_asset"),
                "quote_asset": spec.get("quote_asset"),
                "data_type": spec["data_type"],
                "granularity": spec["granularity"],
                "fields": tuple(spec["fields"]),
                "freshness_policy": spec.get("freshness_policy", "event_time"),
                "source_role_required": spec.get("source_role_required"),
                "consumer": "ui_probe",
                "consumer_id": run_id,
                "as_of": as_of,
            }
        range_days = spec.get("range_days")
        if range_days is not None:
            range_end = spec.get("date_range_end") or as_of.date()
            range_start = spec.get("date_range_start") or (range_end - timedelta(days=int(range_days)))
            payload["date_range_start"] = range_start
            payload["date_range_end"] = range_end
        requests.append(DataRequest.model_validate(payload))
    return requests


def _request_specs(market: str) -> list[dict[str, Any]]:
    if market == "CN_A":
        base = {"symbol_id": "600519.SH", "timezone": "Asia/Shanghai", "calendar": "CN_A_SSE_SZSE"}
        return [
            {**base, "data_type": "daily_bar", "granularity": "daily", "fields": ("open", "high", "low", "close", "volume", "amount"), "range_days": 365},
            {**base, "data_type": "daily_bar", "granularity": "daily", "fields": ("date", "open", "high", "low", "close", "volume", "amount", "adjustment"), "range_days": 365},
            {**base, "data_type": "intraday_bar", "granularity": "intraday", "fields": ("timestamp", "open", "high", "low", "close", "volume")},
            {**base, "data_type": "quote_snapshot", "granularity": "realtime", "fields": ("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id")},
            {**base, "data_type": "order_book_snapshot", "granularity": "realtime", "fields": ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id")},
            {**base, "data_type": "financial_statement", "granularity": "quarterly", "fields": ("period", "revenue", "net_income", "assets", "liabilities", "cash_flow")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("roe", "gross_margin", "eps")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("roe", "eps", "symbol_id")},
            {**base, "data_type": "valuation_metric", "granularity": "daily", "fields": ("pe", "pb", "ps", "market_cap"), "range_days": 365},
            {**base, "data_type": "valuation_metric", "granularity": "daily", "fields": ("pe", "pb", "ps"), "range_days": 365},
            {**base, "data_type": "valuation_metric", "granularity": "realtime", "fields": ("market_cap", "price", "symbol_id")},
            {**base, "data_type": "capital_flow", "granularity": "daily", "fields": ("date", "main_net", "symbol_id")},
            {**base, "data_type": "capital_flow", "granularity": "daily", "fields": ("date", "financing_balance", "margin_balance", "security_lending_volume", "symbol_id")},
            {**base, "data_type": "capital_flow", "granularity": "event", "fields": ("trade_date", "buyer", "seller", "price", "volume", "amount", "symbol_id")},
            {**base, "data_type": "sector_snapshot", "granularity": "event", "fields": ("sector_code", "sector_name", "main_net", "timestamp")},
            {**base, "data_type": "sector_snapshot", "granularity": "event", "fields": ("sector_name", "timestamp", "symbol_id")},
            {**base, "data_type": "corporate_action", "granularity": "event", "fields": ("event_type", "event_date", "title", "source", "holder_count", "symbol_id")},
            {**base, "data_type": "corporate_action", "granularity": "event", "fields": ("event_type", "event_date", "title", "source", "dividend", "symbol_id")},
            {**base, "data_type": "corporate_action", "granularity": "event", "fields": ("event_type", "event_date", "title", "source", "adjust_factor", "symbol_id")},
            {**base, "data_type": "corporate_action", "granularity": "event", "fields": ("event_type", "event_date", "title", "source", "adjustment", "symbol_id")},
            {**base, "data_type": "event_calendar", "granularity": "event", "fields": ("event_type", "event_date", "title", "source")},
            {**base, "data_type": "official_filing", "granularity": "event", "fields": ("title", "published_at", "url", "source", "body_ref"), "range_days": 365},
            {**base, "data_type": "hot_money_event", "granularity": "event", "fields": ("trade_date", "seat", "buy_amount", "sell_amount", "symbol_id")},
            {**base, "data_type": "lockup_event", "granularity": "event", "fields": ("unlock_date", "shares", "market_value", "holder")},
            {**base, "data_type": "company_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url")},
            {**base, "data_type": "macro_news", "granularity": "event", "fields": ("title", "published_at", "region", "summary", "url")},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "question", "answer", "symbol_id")},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "metrics", "symbol_id")},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "keyword", "score", "symbol_id")},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "related_symbol", "change_pct", "symbol_id")},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "topic", "reason", "symbol_id")},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "topic", "concept", "symbol_id")},
        ]
    if market == "US":
        base = {"symbol_id": "AAPL", "timezone": "America/New_York", "calendar": "US_NYSE_NASDAQ"}
        return [
            {**base, "data_type": "daily_bar", "granularity": "daily", "fields": ("open", "high", "low", "close", "volume"), "range_days": 365},
            {**base, "data_type": "quote_snapshot", "granularity": "realtime", "fields": ("price", "change", "change_pct", "volume", "timestamp", "symbol_id")},
            {**base, "data_type": "quote_snapshot", "granularity": "realtime", "fields": ("price", "change", "change_pct", "timestamp", "symbol_id")},
            {**base, "data_type": "valuation_metric", "granularity": "realtime", "fields": ("pe", "pb", "ps", "market_cap")},
            {**base, "data_type": "valuation_metric", "granularity": "realtime", "fields": ("pe", "market_cap")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("roe", "gross_margin", "profit_margin", "eps", "revenue_growth")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("roe", "roa", "profit_margin", "eps")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("gross_margin", "eps")},
            {**base, "data_type": "official_filing", "granularity": "event", "fields": ("title", "published_at", "source", "url", "symbol_id")},
            {**base, "data_type": "financial_statement", "granularity": "quarterly", "fields": ("period", "revenue", "net_income", "assets", "liabilities", "cash_flow")},
            {**base, "data_type": "macro_series", "granularity": "monthly", "fields": ("series_id", "date", "value", "unit", "region"), "symbol_id": "FEDFUNDS"},
            {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "message", "sentiment", "symbol_id")},
            {**base, "data_type": "company_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url")},
            {**base, "data_type": "macro_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url", "region")},
        ]
    if market == "HK":
        base = {"symbol_id": "00700.HK", "timezone": "Asia/Hong_Kong", "calendar": "HK_XHKG"}
        return [
            {**base, "data_type": "daily_bar", "granularity": "daily", "fields": ("open", "high", "low", "close", "volume"), "range_days": 365},
            {**base, "data_type": "valuation_metric", "granularity": "realtime", "fields": ("pe", "market_cap")},
            {**base, "data_type": "valuation_metric", "granularity": "daily", "fields": ("price", "market_cap", "symbol_id"), "range_days": 365},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("roe", "gross_margin", "eps")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("roe", "eps", "gross_profit")},
            {**base, "data_type": "financial_metric", "granularity": "quarterly", "fields": ("gross_margin", "eps")},
            {**base, "data_type": "financial_statement", "granularity": "quarterly", "fields": ("period", "revenue", "net_income")},
            {**base, "data_type": "official_filing", "granularity": "event", "fields": ("title", "published_at", "source", "url", "symbol_id")},
            {**base, "data_type": "event_calendar", "granularity": "event", "fields": ("event_date", "event_type", "title", "source", "url", "symbol_id")},
            {**base, "data_type": "company_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url")},
            {**base, "data_type": "macro_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url", "region")},
        ]
    base = {
        "symbol_id": "BTCUSDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": "BTC",
        "quote_asset": "USDT",
    }
    return [
        {**base, "data_type": "daily_bar", "granularity": "daily", "fields": ("open", "high", "low", "close", "volume", "amount"), "range_days": 365},
        {**base, "data_type": "quote_snapshot", "granularity": "realtime", "fields": ("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id")},
        {**base, "data_type": "order_book_snapshot", "granularity": "realtime", "fields": ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id")},
        {**base, "data_type": "order_book_snapshot", "granularity": "1h", "fields": ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp"), "range_days": 7},
        {**base, "data_type": "intraday_bar", "granularity": "1h", "fields": ("open", "high", "low", "close", "volume", "amount"), "range_days": 7},
        {**base, "data_type": "valuation_metric", "granularity": "realtime", "fields": ("price", "market_cap", "fdv", "circulating_supply", "total_supply", "volume")},
        {**base, "data_type": "defi_metric", "granularity": "realtime", "fields": ("tvl", "chains", "category", "symbol_id")},
        {**base, "data_type": "crypto_derivative_metric", "granularity": "realtime", "fields": ("open_interest", "timestamp", "symbol_id")},
        {**base, "data_type": "crypto_derivative_metric", "granularity": "1h", "fields": ("funding_rate", "timestamp", "symbol_id"), "range_days": 7},
        {**base, "data_type": "crypto_derivative_metric", "granularity": "1h", "fields": ("long_short_ratio", "timestamp", "symbol_id"), "range_days": 7},
        {**base, "data_type": "crypto_derivative_metric", "granularity": "1h", "fields": ("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id"), "range_days": 7},
        {**base, "data_type": "crypto_derivative_metric", "granularity": "1h", "fields": ("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id"), "range_days": 7},
        {**base, "data_type": "crypto_derivative_metric", "granularity": "realtime", "fields": ("net_inflow", "timestamp", "symbol_id")},
        {**base, "data_type": "crypto_onchain_metric", "granularity": "daily", "fields": ("timestamp", "metric", "value", "chain"), "range_days": 365},
        {**base, "data_type": "crypto_onchain_metric", "granularity": "event", "fields": ("timestamp", "metric", "value", "chain"), "range_days": 30},
        {**base, "data_type": "crypto_onchain_metric", "granularity": "realtime", "fields": ("timestamp", "metric", "value", "chain")},
        {**base, "data_type": "social_signal", "granularity": "event", "fields": ("source", "timestamp", "score", "sentiment", "symbol_id")},
        {**base, "data_type": "company_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url")},
        {**base, "data_type": "macro_news", "granularity": "event", "fields": ("title", "published_at", "source", "summary", "url", "region")},
    ]


def _capabilities_for_market(market: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plugin in iter_minimal_market_plugins():
        caps = plugin.capabilities()
        for endpoint in caps.endpoints:
            if str(endpoint.market) != market:
                continue
            rows.append(
                {
                    "provider_id": caps.provider_id,
                    "endpoint_id": endpoint.endpoint_id,
                    "market": endpoint.market,
                    "data_type": endpoint.data_type,
                    "source_role": endpoint.source_role,
                    "granularity": tuple(endpoint.granularity),
                    "fields": tuple(endpoint.fields),
                    "http_visibility": endpoint.http_visibility,
                    "priority_rank": endpoint.priority_rank,
                    "credential_required": bool(caps.credential_policy.credential_required),
                    "credential_names": tuple(caps.credential_policy.credential_names),
                }
            )
    return rows


def _provider_matrix(capabilities: Sequence[Mapping[str, Any]], requests: Sequence[DataRequest], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected_pairs: Counter[tuple[str, str]] = Counter()
    batch_pairs: Counter[tuple[str, str]] = Counter()
    fetch_results: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    gate_kinds: defaultdict[tuple[str, str], list[str]] = defaultdict(list)

    for event in events:
        if event.get("step") == "provider_selector.end":
            for candidate in event.get("candidates", ()) or ():
                selected_pairs[(str(candidate.get("provider_id")), str(candidate.get("endpoint_id")))] += 1
        if event.get("step") == "batch_planner.end":
            for batch in event.get("batches", ()) or ():
                batch_pairs[(str(batch.get("provider_id")), str(batch.get("endpoint_id")))] += 1
        if event.get("step") == "execution_gate.decision":
            batch = event.get("batch") or {}
            gate_kinds[(str(batch.get("provider_id")), str(batch.get("endpoint_id")))].append(str(event.get("kind")))
        if event.get("step") == "fetch.end":
            batch = event.get("batch") or {}
            result = event.get("result") or {}
            fetch_results[(str(batch.get("provider_id")), str(batch.get("endpoint_id")))].append(str(result.get("status")))

    rows: list[dict[str, Any]] = []
    for cap in capabilities:
        pair = (str(cap["provider_id"]), str(cap["endpoint_id"]))
        statuses = tuple(fetch_results.get(pair, ()))
        called = bool(statuses)
        rows.append(
            {
                **dict(cap),
                "selected_count": selected_pairs.get(pair, 0),
                "batch_count": batch_pairs.get(pair, 0),
                "fetch_count": len(statuses),
                "fetch_statuses": statuses,
                "gate_kinds": tuple(gate_kinds.get(pair, ())),
                "called": called,
                "not_called_reason": None if called else _not_called_reason(cap, requests, selected_pairs.get(pair, 0), batch_pairs.get(pair, 0), gate_kinds.get(pair, ())),
            }
        )
    return rows


def _not_called_reason(cap: Mapping[str, Any], requests: Sequence[DataRequest], selected_count: int, batch_count: int, gate_kinds: Sequence[str]) -> str:
    if gate_kinds:
        return "gate_non_remote:" + ",".join(dict.fromkeys(gate_kinds))
    if batch_count:
        return "batch_built_but_fetch_not_observed"
    if selected_count:
        return "selected_but_no_batch"
    for req in requests:
        if req.data_type != cap["data_type"]:
            continue
        if req.granularity not in cap["granularity"]:
            continue
        missing = tuple(field for field in req.fields if field not in cap["fields"])
        if not missing:
            return "matching_request_exists_but_not_selected"
    if any(req.data_type == cap["data_type"] for req in requests):
        return "request_fields_or_granularity_do_not_match_capability"
    return "no_request_for_data_type_in_this_probe"


def _runtime_profile() -> dict[str, Any]:
    return {
        "launcher": "uv run python scripts/validation/data_layer_full_chain_market_probe.py",
        "uv_environment": "claw-trade repo uv",
        "openviking": "not_started_not_used",
        "openclaw_gateway": "not_started_not_used",
        "invest_sidecar": "disabled",
        "openviking_config_file": "not_used",
        "openviking_data_dir": "not_used",
        "credential_source": "Mongo UI settings and secret store",
    }


def _request_summary(req: DataRequest) -> dict[str, Any]:
    return {
        "request_id": req.request_id,
        "market": req.market.value,
        "symbol_id": req.symbol_id,
        "data_type": req.data_type,
        "granularity": req.granularity,
        "fields": tuple(req.fields),
    }


def _result_summary(result: Any) -> dict[str, Any]:
    return {
        "request_id": getattr(result, "request_id", None),
        "status": _status_value(getattr(result, "status", None)),
        "row_count": len(tuple(getattr(result, "rows", ()) or ())),
        "dataset_refs": tuple(getattr(result, "dataset_refs", ()) or ()),
        "raw_refs": tuple(getattr(result, "raw_refs", ()) or ()),
        "attempt_refs": tuple(getattr(result, "attempt_refs", ()) or ()),
        "gaps": tuple(_gap_summary(gap) for gap in tuple(getattr(result, "gaps", ()) or ())),
    }


def _check_summary(check: Any) -> dict[str, Any]:
    return {
        "request_id": getattr(check, "request_id", None),
        "market": _status_value(getattr(check, "market", None)),
        "symbol_id": getattr(check, "symbol_id", None),
        "data_type": getattr(check, "data_type", None),
        "granularity": getattr(check, "granularity", None),
        "fields": tuple(getattr(check, "fields", ()) or ()),
    }


def _warehouse_summary(result: Any) -> dict[str, Any]:
    return {
        "satisfied": bool(getattr(result, "satisfied", False)),
        "row_count": len(tuple(getattr(result, "rows", ()) or ())),
        "dataset_refs": tuple(getattr(result, "dataset_refs", ()) or ()),
        "gaps": tuple(_gap_summary(gap) for gap in tuple(getattr(result, "gaps", ()) or ())),
    }


def _candidate_summary(candidate: Any) -> dict[str, Any]:
    return {
        "request_id": getattr(candidate, "request_id", None),
        "provider_id": getattr(candidate, "provider_id", None),
        "endpoint_id": getattr(candidate, "endpoint_id", None),
        "market": getattr(candidate, "market", None),
        "data_type": getattr(candidate, "data_type", None),
        "granularity": getattr(candidate, "granularity", None),
        "fields": tuple(getattr(candidate, "fields", ()) or ()),
        "source_role": getattr(candidate, "source_role", None),
        "priority_rank": getattr(candidate, "priority_rank", None),
    }


def _group_summary(group: Any) -> dict[str, Any]:
    return {
        "provider_id": getattr(group, "provider_id", None),
        "endpoint_id": getattr(group, "endpoint_id", None),
        "market": getattr(group, "market", None),
        "data_type": getattr(group, "data_type", None),
        "granularity": getattr(group, "granularity", None),
        "request_ids": tuple(getattr(group, "request_ids", ()) or ()),
        "symbol_ids": tuple(getattr(group, "symbol_ids", ()) or ()),
        "fields_union": tuple(getattr(group, "fields_union", ()) or ()),
    }


def _batch_summary(batch: Any) -> dict[str, Any]:
    return {
        "batch_id": getattr(batch, "batch_id", None),
        "provider_id": getattr(batch, "provider_id", None),
        "endpoint_id": getattr(batch, "endpoint_id", None),
        "market": getattr(batch, "market", None),
        "data_type": getattr(batch, "data_type", None),
        "granularity": getattr(batch, "granularity", None),
        "request_ids": tuple(getattr(batch, "request_ids", ()) or ()),
        "symbol_ids": tuple(getattr(batch, "symbol_ids", ()) or ()),
        "fields_union": tuple(getattr(batch, "fields_union", ()) or ()),
    }


def _fetch_summary(result: Any) -> dict[str, Any]:
    return {
        "status": _status_value(getattr(result, "status", None)),
        "row_count": getattr(result, "row_count", None),
        "http_observation_count": len(tuple(getattr(result, "http_observations", ()) or ())),
        "error": str(getattr(result, "error", "") or "")[:300],
        "error_message": str(getattr(result, "error_message", "") or "")[:300],
    }


def _ingest_summary(ingest: Any) -> dict[str, Any]:
    return {
        "status": getattr(ingest, "status", None),
        "remote_success": bool(getattr(ingest, "remote_success", False)),
        "dataset_refs": tuple(getattr(ingest, "dataset_refs", ()) or ()),
        "raw_refs": tuple(getattr(ingest, "raw_refs", ()) or ()),
        "attempt_refs": tuple(getattr(ingest, "attempt_refs", ()) or ()),
        "gaps": tuple(_gap_summary(gap) for gap in tuple(getattr(ingest, "gaps", ()) or ())),
    }


def _gap_summary(gap: Any) -> dict[str, Any]:
    if isinstance(gap, Mapping):
        return {
            "reason": _status_value(gap.get("reason")),
            "request_id": gap.get("request_id"),
            "data_type": gap.get("data_type"),
            "required_fields": tuple(gap.get("required_fields", ()) or ()),
            "message": gap.get("human_readable") or gap.get("message"),
        }
    return {
        "reason": _status_value(getattr(gap, "reason", None)),
        "request_id": getattr(gap, "request_id", None),
        "data_type": getattr(gap, "data_type", None),
        "required_fields": tuple(getattr(gap, "required_fields", ()) or ()),
        "message": getattr(gap, "human_readable", None),
    }


def _status_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _collection_counts(database: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in DatasetRepository.collection_names():
        try:
            counts[name] = int(database[name].count_documents({}))
        except Exception:
            counts[name] = -1
    return counts


def _configured_source_summary(database: Any) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for doc in database[UI_DATA_SOURCE_SETTINGS_COLLECTION].find({}):
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


def _console_summary(payload: Mapping[str, Any]) -> str:
    provider_counts = Counter()
    for row in payload["provider_matrix"]:
        key = "called" if row["called"] else "not_called"
        provider_counts[key] += 1
        for status in row.get("fetch_statuses", ()) or ():
            provider_counts[f"status:{status}"] += 1
    result_counts = Counter(str(item["status"]) for item in payload["results"])
    return json.dumps(
        {
            "market": payload["market"],
            "request_count": payload["request_count"],
            "result_counts": dict(result_counts),
            "provider_counts": dict(provider_counts),
            "output_schema": payload["schema_version"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _markdown_summary(payload: Mapping[str, Any]) -> str:
    lines = [
        f"# Data layer full-chain probe: {payload['market']}",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- scratch_database: `{payload['scratch_database']}`",
        f"- request_count: `{payload['request_count']}`",
        f"- collection_counts: `{json.dumps(payload['collection_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Provider calls",
        "",
        "| provider | endpoint | data_type | called | result | not called reason |",
        "|---|---|---|---:|---|---|",
    ]
    for row in payload["provider_matrix"]:
        statuses = ",".join(row.get("fetch_statuses", ()) or ())
        lines.append(
            f"| `{row['provider_id']}` | `{row['endpoint_id']}` | `{row['data_type']}` | "
            f"{'yes' if row['called'] else 'no'} | `{statuses}` | `{row.get('not_called_reason') or ''}` |"
        )
    lines.extend(["", "## Results", "", "| request | status | refs | gaps |", "|---|---|---:|---|"])
    for result in payload["results"]:
        gaps = ",".join(str(gap.get("reason")) for gap in result.get("gaps", ()) or ())
        lines.append(
            f"| `{result['request_id']}` | `{result['status']}` | "
            f"{len(result.get('dataset_refs', ()) or ())} datasets / {len(result.get('raw_refs', ()) or ())} raw / {len(result.get('attempt_refs', ()) or ())} attempts | `{gaps}` |"
        )
    lines.extend(["", "## Flow", "", "`DataAPI -> DataService.plan_batch -> QueryPlanner -> Warehouse.check -> ProviderSelector -> RequestCoalescer -> ProviderBatchPlanner -> ExecutionGate -> FetchEngine -> IngestPipeline(RawStore, Normalizer, NormalizedStore, AttemptLog) -> Warehouse.recheck -> DataResult`"])
    return "\n".join(lines) + "\n"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_jsonable(item) for item in value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _mongo_client() -> Any:
    from pymongo import MongoClient

    uri = os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("DATA_GATEWAY_MONGODB_URI missing")
    return MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _provider_probe_timeout_seconds(batch: Any) -> float:
    provider_id = str(getattr(batch, "provider_id", ""))
    if provider_id in {"cn_a_baostock_market", "cn_a_mootdx_market"}:
        return 15.0
    return _float_env("CLAW_TRADE_PROVIDER_PROBE_TIMEOUT_SECONDS", 35.0)


def _timeout_message(kind: str, batch: Any) -> str:
    return (
        f"{kind}:"
        f"{getattr(batch, 'provider_id', 'unknown_provider')}:"
        f"{getattr(batch, 'endpoint_id', 'unknown_endpoint')}:"
        f"{getattr(batch, 'batch_id', 'batch:unknown')}"
    )


@contextmanager
def _timeout_after(seconds: float, message: str) -> Any:
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(message)

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _mongo_db_name() -> str:
    return os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip() or os.environ.get("CN_A_MONGODB_DATABASE", "").strip() or "claw_trade"


def _mongo_db() -> Any:
    return _mongo_client()[_mongo_db_name()]


def _load_runtime_env() -> None:
    if not RUNTIME_ENV.exists():
        return
    allowed = {
        "DATA_GATEWAY_MONGODB_URI",
        "DATA_GATEWAY_MONGODB_DATABASE",
        "CN_A_MONGODB_URI",
        "CN_A_MONGODB_DATABASE",
    }
    for line in RUNTIME_ENV.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if key in allowed:
            os.environ.setdefault(key, value.strip().strip("'\""))


if __name__ == "__main__":
    raise SystemExit(main())
