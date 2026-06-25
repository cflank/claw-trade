from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Protocol
from uuid import uuid4

from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler
from claw_trade.data_gateway.crypto_prepackaged_importer import local_warehouse_empty_gap
from claw_trade.data_gateway.execution.rate_limit_policy import policy_to_namespace
from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResult,
    DataResultStatus,
    GapReason,
    GapSeverity,
    GateDecision,
    IngestResult,
    Market,
    QueryPlan,
    WarehouseResult,
)
from claw_trade.data_gateway.needs import DataNeed, DataNeedGap, NeedPriority, ProviderCallSpec
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.planner import plan_public_data_requests
from claw_trade.data_gateway.public_api import PublicDataRequest, public_output_contract_for_api
from claw_trade.instruments.resolver import InstrumentResolveError, resolve_crypto_provider_symbols

_LOGGER = logging.getLogger("uvicorn.error")
_DATA_NEED_LEASE_TTL_SECONDS = 30
_COMPANY_NAME_REMOTE_DEADLINE_SECONDS = 30
_CN_A_MARKET_DEFAULTS = {
    "exchange": "SSE",
    "currency": "CNY",
    "timezone": "Asia/Shanghai",
    "calendar": "CN_A_SSE_SZSE",
}


class QueryPlannerLike(Protocol):
    def validate_and_normalize(self, request: DataRequest) -> QueryPlan: ...

    def validate_and_normalize_many(self, requests: Sequence[DataRequest]) -> QueryPlan: ...


class WarehouseLike(Protocol):
    def check(self, checks: Sequence[Any], coverage: Any) -> WarehouseResult: ...

    def recheck(self, checks: Sequence[Any], coverage: Any) -> WarehouseResult: ...

    def resolve_company_names(
        self,
        *,
        market: Market | str,
        symbol_ids: Sequence[str],
        dataset: str = "daily_bar",
    ) -> Mapping[str, str]: ...


class DataRunSchedulerLike(Protocol):
    def schedule(self, batches: Sequence[Any], context: DataRunScheduleContext) -> tuple[Any, ...]: ...


class ExecutionGateLike(Protocol):
    def enter(self, batch: Any) -> GateDecision: ...

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult, *, batch: Any | None = None) -> None: ...

    def wait_after_rate_limited_fetch(self, batch: Any, fetch_result: Any) -> bool: ...


class FetchEngineLike(Protocol):
    def fetch(self, batch: Any) -> Any: ...


class IngestLike(Protocol):
    def ingest(self, result: Any, batch: Any) -> IngestResult: ...

    def record_gate_result(self, batch: Any, gate: GateDecision) -> IngestResult: ...

    def record_selector_skip(self, batch: Any, selector_skip: Any) -> IngestResult: ...


class DataService:
    _COMPANY_NAME_FIELDS = ("company_name", "name", "stock_name", "code_name", "security_name")
    _SOURCE_ROLE_ORDER = {
        "official": 0,
        "paid_data": 1,
        "built_in_public": 2,
        "sentiment": 3,
        "discovery": 4,
        "event_expectation": 5,
    }
    _NON_REMOTE_GATE_KINDS = {"cache_hit", "cached_empty", "rate_limited", "shared_result", "cooldown_skipped"}
    _NON_REMOTE_GAP_REASONS = {
        "cache_hit",
        "shared_result",
        "rate_limited",
        "rate_limited_by_tool_budget",
        "cached_empty",
        "cooldown_skipped",
        "sdk_http_unknown",
        "evidence_write_failed",
    }

    def __init__(
        self,
        *,
        query_planner: QueryPlannerLike,
        warehouse: WarehouseLike,
        execution_gate: ExecutionGateLike,
        fetch_engine: FetchEngineLike,
        ingest: IngestLike,
        scheduler: DataRunSchedulerLike | None = None,
        rate_limit_policy_resolver: Any | None = None,
    ) -> None:
        self.query_planner = query_planner
        self.warehouse = warehouse
        self.scheduler = scheduler or DataRunScheduler()
        self.execution_gate = execution_gate
        self.fetch_engine = fetch_engine
        self.ingest = ingest
        self.rate_limit_policy_resolver = rate_limit_policy_resolver

    def read_warehouse_batch(self, requests: Sequence[DataRequest]) -> list[DataResult]:
        query_plan = self.query_planner.validate_and_normalize_many(requests)
        normalized_requests = query_plan.normalized_requests
        if not normalized_requests:
            return []
        metadata_only = self._metadata_only_warehouse_check(normalized_requests)
        warehouse_result = self._check_warehouse(query_plan, metadata_only=metadata_only)
        if warehouse_result.satisfied:
            return [
                self._result_from_warehouse(request.request_id, self._slice_warehouse_result(warehouse_result, request))
                for request in normalized_requests
            ]
        return [
            self._compose_result(request.request_id, self._slice_warehouse_result(warehouse_result, request), ())
            for request in normalized_requests
        ]

    def request_data(self, requests: Sequence[PublicDataRequest]) -> list[DataResult]:
        ordered_requests = tuple(requests)
        if not ordered_requests:
            return []
        now = datetime.now(tz=UTC)
        plan = plan_public_data_requests(ordered_requests)
        policies = {call.rate_limit_bucket: self._data_need_rate_limit_policy(call) for call in plan.planned_calls}
        scheduler = self.scheduler
        policy_updater = getattr(scheduler, "update_rate_limit_policies", None)
        if callable(policy_updater):
            policy_updater(policies)
        scheduled_plan = scheduler.schedule(
            plan,
            DataRunScheduleContext.for_plan(run_id=f"public-data:{uuid4().hex}", run_started_at=now),
        )
        need_by_id = {need.need_id: need for need in scheduled_plan.needs}
        call_by_id = {call.call_id: call for call in scheduled_plan.planned_calls}
        results_by_request_id: dict[str, DataResult] = {}

        for gap in scheduled_plan.skipped_needs:
            need = need_by_id.get(gap.need_id)
            if need is not None and gap.need_id not in results_by_request_id:
                results_by_request_id[gap.need_id] = self._data_result_from_need_gap(need=need, gap=gap)

        for scheduled in scheduled_plan.scheduled_calls:
            call = call_by_id.get(scheduled.call_id)
            if call is None:
                continue
            pending_need_list: list[DataNeed] = []
            for need_id in call.need_ids:
                need = need_by_id.get(need_id)
                if need is None:
                    continue
                existing = results_by_request_id.get(need_id)
                if existing is not None and existing.status == DataResultStatus.READY:
                    continue
                pending_need_list.append(need)
            pending_needs = tuple(pending_need_list)
            if not pending_needs:
                continue
            batch_need = pending_needs[0]
            batch = self._provider_call_batch(
                call=call,
                need=batch_need,
                policy=policies[call.rate_limit_bucket],
                earliest_start_at=scheduled.earliest_start_at,
                rate_limit_reserved_at=scheduled.rate_limit_reserved_at,
            )
            gate = self.execution_gate.enter(batch)
            if gate.kind in self._NON_REMOTE_GATE_KINDS:
                ingest = self.ingest.record_gate_result(batch, gate)
                for need in pending_needs:
                    result = self._data_result_from_ingest(need=need, ingest=ingest)
                    results_by_request_id[need.need_id] = self._merge_data_need_result(results_by_request_id.get(need.need_id), result)
                continue
            fetch_result = self.fetch_engine.fetch(batch)
            retry_waiter = getattr(self.execution_gate, "wait_after_rate_limited_fetch", None)
            if callable(retry_waiter) and retry_waiter(batch, fetch_result):
                fetch_result = self.fetch_engine.fetch(batch)
            ingest = self.ingest.ingest(fetch_result, batch)
            if gate.kind == "owner" and gate.owner_token:
                self.execution_gate.publish_shared_result(batch.single_flight_key, gate.owner_token, ingest, batch=batch)
            for need in pending_needs:
                result = self._data_result_from_fetch(need=need, call=call, batch=batch, fetch_result=fetch_result, ingest=ingest)
                if result is None:
                    result = self._data_result_from_ingest(need=need, ingest=ingest)
                results_by_request_id[need.need_id] = self._merge_data_need_result(results_by_request_id.get(need.need_id), result)

        return [
            results_by_request_id.get(
                request.request_id,
                self._data_result_from_need_gap(
                    need=need_by_id.get(request.request_id)
                    or DataNeed(
                        need_id=request.request_id,
                        api_id=request.api_id,
                        market=request.market,
                        instrument=request.instrument,
                        granularity=request.granularity,
                        requested_by_worker=request.requested_by_worker,
                        purpose=request.purpose,
                        freshness_policy=request.freshness_policy,
                        deadline_at=request.deadline_at,
                        consumer=request.consumer,
                    ),
                    gap=DataNeedGap(
                        need_id=request.request_id,
                        reason=GapReason.CATALOG_MATCH_MISSING,
                        human_readable=f"project data item has no scheduled provider API binding: {request.market.value}:{request.item}",
                    ),
                ),
            )
            for request in ordered_requests
        ]

    def resolve_company_names(
        self,
        *,
        market: Market | str,
        symbol_ids: Sequence[str],
        dataset: str = "daily_bar",
    ) -> Mapping[str, str]:
        symbols = tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in symbol_ids if str(symbol).strip()))
        if not symbols:
            return {}
        resolved_market = self._coerce_market(market)
        resolver = getattr(self.warehouse, "resolve_company_names", None)
        names: dict[str, str] = {}
        if callable(resolver):
            names.update(resolver(market=resolved_market, symbol_ids=symbols, dataset=dataset))
        missing = tuple(symbol for symbol in symbols if not str(names.get(symbol) or "").strip())
        if not missing or resolved_market != Market.CN_A:
            return names

        names.update(self._resolve_company_names_from_data_needs(resolved_market=resolved_market, symbols=missing))
        return names

    def _resolve_company_names_from_data_needs(self, *, resolved_market: Market, symbols: Sequence[str]) -> dict[str, str]:
        now = datetime.now(tz=UTC)
        requests = tuple(
            PublicDataRequest(
                request_id=f"company-name:{symbol}:quote_snapshot:{now.strftime('%Y%m%d%H%M%S%f')}",
                item="实时价",
                market=resolved_market,
                instrument=symbol,
                granularity="realtime",
                purpose="company_name_resolver",
                priority=NeedPriority.REQUIRED,
                requested_by_worker="ui_probe",
                freshness_policy="trading_day",
                deadline_at=now + timedelta(seconds=_COMPANY_NAME_REMOTE_DEADLINE_SECONDS),
                consumer="ui_probe",
            )
            for symbol in symbols
        )
        if not requests:
            return {}

        plan = plan_public_data_requests(requests)
        policies = {call.rate_limit_bucket: self._data_need_rate_limit_policy(call) for call in plan.planned_calls}
        scheduler = self.scheduler
        policy_updater = getattr(scheduler, "update_rate_limit_policies", None)
        if callable(policy_updater):
            policy_updater(policies)
        scheduled_plan = scheduler.schedule(
            plan,
            DataRunScheduleContext.for_plan(run_id=f"company-name:{uuid4().hex}", run_started_at=now),
        )
        need_by_id = {need.need_id: need for need in scheduled_plan.needs}
        call_by_id = {call.call_id: call for call in scheduled_plan.planned_calls}
        names: dict[str, str] = {}
        for scheduled in scheduled_plan.scheduled_calls:
            call = call_by_id.get(scheduled.call_id)
            if call is None:
                continue
            need = need_by_id.get(call.need_ids[0])
            if need is None:
                continue
            batch = self._data_need_call_batch(
                call=call,
                need=need,
                policy=policies[call.rate_limit_bucket],
                earliest_start_at=scheduled.earliest_start_at,
                rate_limit_reserved_at=scheduled.rate_limit_reserved_at,
            )
            gate = self.execution_gate.enter(batch)
            if gate.kind in self._NON_REMOTE_GATE_KINDS:
                self.ingest.record_gate_result(batch, gate)
                continue
            fetch_result = self.fetch_engine.fetch(batch)
            retry_waiter = getattr(self.execution_gate, "wait_after_rate_limited_fetch", None)
            if callable(retry_waiter) and retry_waiter(batch, fetch_result):
                fetch_result = self.fetch_engine.fetch(batch)
            ingest = self.ingest.ingest(fetch_result, batch)
            if gate.kind == "owner" and gate.owner_token:
                self.execution_gate.publish_shared_result(batch.single_flight_key, gate.owner_token, ingest, batch=batch)
            names.update(self._company_names_from_fetch_result(fetch_result))

        resolver = getattr(self.warehouse, "resolve_company_names", None)
        if callable(resolver):
            names.update(
                resolver(
                    market=resolved_market,
                    symbol_ids=tuple(symbol for symbol in symbols if not str(names.get(symbol) or "").strip()),
                    dataset="quote_snapshot",
                )
            )
        return names

    def _data_need_rate_limit_policy(self, call: ProviderCallSpec) -> RateLimitPolicy:
        resolver = self.rate_limit_policy_resolver
        if resolver is not None and callable(getattr(resolver, "resolve", None)):
            return resolver.resolve(provider_id=call.provider_id, rate_limit_bucket=call.rate_limit_bucket, default_policy=None)
        return RateLimitPolicy(window_seconds=60, max_requests=None)

    def _data_need_call_batch(
        self,
        *,
        call: ProviderCallSpec,
        need: DataNeed,
        policy: RateLimitPolicy,
        earliest_start_at: datetime | None = None,
        rate_limit_reserved_at: datetime | None = None,
    ) -> Any:
        return SimpleNamespace(
            batch_id=call.call_id,
            request_ids=call.need_ids,
            provider_id=call.provider_id,
            endpoint_id=call.catalog_endpoint_id if call.provider_id.startswith("official_api_") else _adapter_endpoint_id(call),
            market=need.market,
            symbol_ids=(need.instrument,),
            data_type="quote_snapshot",
            granularity="realtime",
            fields_union=("symbol_id", "name", "company_name"),
            capability_fields=("symbol_id", "name", "company_name"),
            date_range_start=need.time_range_start,
            date_range_end=need.time_range_end,
            exchange=_CN_A_MARKET_DEFAULTS["exchange"],
            currency=_CN_A_MARKET_DEFAULTS["currency"],
            timezone=_CN_A_MARKET_DEFAULTS["timezone"],
            calendar=_CN_A_MARKET_DEFAULTS["calendar"],
            base_asset=None,
            quote_asset=None,
            provider_config_version=None,
            params={"provider_call_spec": call.model_dump(mode="json")} if call.provider_id.startswith("official_api_") else dict(call.params),
            http_visibility=call.http_visibility,
            rate_limit_key=call.rate_limit_bucket,
            cooldown_key=None,
            rate_limit_policy=policy_to_namespace(policy),
            deadline_at=call.deadline_at,
            earliest_start_at=earliest_start_at,
            rate_limit_reserved_at=rate_limit_reserved_at,
            single_flight_key=call.batch_key,
            lease_ttl_seconds=_DATA_NEED_LEASE_TTL_SECONDS,
            cache_key=f"data-need:{call.batch_key}",
            parser_status=call.parser_status,
        )

    def _provider_call_batch(
        self,
        *,
        call: ProviderCallSpec,
        need: DataNeed,
        policy: RateLimitPolicy,
        earliest_start_at: datetime | None = None,
        rate_limit_reserved_at: datetime | None = None,
    ) -> Any:
        from claw_trade.data_gateway.report_evidence import _provider_call_batch

        return _provider_call_batch(
            call=call,
            need=need,
            policy=policy,
            runtime=None,
            earliest_start_at=earliest_start_at,
            rate_limit_reserved_at=rate_limit_reserved_at,
        )

    def _data_result_from_fetch(
        self,
        *,
        need: DataNeed,
        call: ProviderCallSpec,
        batch: Any,
        fetch_result: Any,
        ingest: IngestResult,
    ) -> DataResult | None:
        from claw_trade.data_gateway.report_evidence import _data_need_result_from_fetch

        result = _data_need_result_from_fetch(
            need=need,
            call=call,
            batch=batch,
            fetch_result=fetch_result,
            ingest=ingest,
            index=0,
            as_of=datetime.now(tz=UTC),
        )
        if result is None:
            return None
        return self._normalize_data_need_result(need=need, result=result)

    def _data_result_from_ingest(self, *, need: DataNeed, ingest: IngestResult) -> DataResult:
        if ingest.dataset_refs:
            warehouse_result = self._warehouse_result_for_ingest_need(need)
            if warehouse_result.rows or warehouse_result.gaps:
                return self._compose_result(need.need_id, warehouse_result, (ingest,))
        gaps = tuple(
            self._coerce_gap(gap, request_id=need.need_id)
            for gap in (ingest.gaps or self._fallback_no_refs_gap(need=need, ingest=ingest))
        )
        status = DataResultStatus.READY if ingest.dataset_refs and not gaps else DataResultStatus.PARTIAL
        if not ingest.dataset_refs and not ingest.raw_refs:
            status = DataResultStatus.MISSING
        if ingest.status == "failed":
            status = DataResultStatus.ERROR
        return DataResult(
            request_id=need.need_id,
            status=status,
            rows=(),
            dataset_refs=ingest.dataset_refs,
            raw_refs=ingest.raw_refs,
            attempt_refs=ingest.attempt_refs,
            gaps=gaps,
            as_of=datetime.now(tz=UTC),
        )

    def _warehouse_result_for_ingest_need(self, need: DataNeed) -> WarehouseResult:
        request = _data_request_from_need(need)
        query_plan = self.query_planner.validate_and_normalize(request)
        warehouse_result = self._check_warehouse(query_plan)
        normalized_request = query_plan.normalized_requests[0] if query_plan.normalized_requests else request
        return self._slice_warehouse_result(warehouse_result, normalized_request)

    def _normalize_data_need_result(self, *, need: DataNeed, result: DataResult) -> DataResult:
        return result.model_copy(
            update={
                "request_id": need.need_id,
                "gaps": tuple(self._coerce_gap(gap, request_id=need.need_id) for gap in result.gaps),
            }
        )

    @staticmethod
    def _fallback_no_refs_gap(*, need: DataNeed, ingest: IngestResult) -> tuple[DataGap, ...]:
        if ingest.dataset_refs or ingest.raw_refs:
            return ()
        return (
            DataGap.by_reason(
                "evidence_write_failed",
                request_id=need.need_id,
                market=need.market,
                data_type=_need_api_suffix(need),
                granularity=need.granularity or "unknown",
                evidence_refs=ingest.attempt_refs,
                message="ingest returned no refs and no gaps",
                symbol_id=need.instrument,
            ),
        )

    @staticmethod
    def _merge_data_need_result(existing: DataResult | None, incoming: DataResult) -> DataResult:
        if existing is None:
            return incoming
        if existing.status == DataResultStatus.READY:
            return existing
        if incoming.status == DataResultStatus.READY:
            return incoming.model_copy(
                update={
                    "attempt_refs": _unique_refs((*existing.attempt_refs, *incoming.attempt_refs)),
                }
            )

        existing_has_data = bool(existing.dataset_refs or existing.raw_refs or existing.rows)
        incoming_has_data = bool(incoming.dataset_refs or incoming.raw_refs or incoming.rows)
        if existing_has_data or incoming_has_data:
            rows = _unique_rows((*existing.rows, *incoming.rows))
            dataset_refs = _unique_refs((*existing.dataset_refs, *incoming.dataset_refs))
            raw_refs = _unique_refs((*existing.raw_refs, *incoming.raw_refs))
            attempt_refs = _unique_refs((*existing.attempt_refs, *incoming.attempt_refs))
            gaps = _unique_gaps((*existing.gaps, *incoming.gaps))
            status = DataResultStatus.READY if dataset_refs and not gaps else DataResultStatus.PARTIAL
            return existing.model_copy(
                update={
                    "status": status,
                    "rows": rows,
                    "dataset_refs": dataset_refs,
                    "raw_refs": raw_refs,
                    "attempt_refs": attempt_refs,
                    "gaps": gaps,
                    "as_of": max(existing.as_of or datetime.min.replace(tzinfo=UTC), incoming.as_of or datetime.min.replace(tzinfo=UTC)),
                }
            )

        base = incoming if _data_result_rank(incoming) < _data_result_rank(existing) else existing
        return base.model_copy(
            update={
                "attempt_refs": _unique_refs((*existing.attempt_refs, *incoming.attempt_refs)),
                "gaps": _unique_gaps((*existing.gaps, *incoming.gaps)),
                "as_of": max(existing.as_of or datetime.min.replace(tzinfo=UTC), incoming.as_of or datetime.min.replace(tzinfo=UTC)),
            }
        )

    def _data_result_from_need_gap(self, *, need: DataNeed, gap: DataNeedGap) -> DataResult:
        data_gap = DataGap.by_reason(
            gap.reason,
            request_id=need.need_id,
            market=need.market,
            data_type=_need_api_suffix(need),
            granularity=need.granularity or "unknown",
            severity=GapSeverity.BLOCKER,
            evidence_refs=gap.evidence_refs,
            message=gap.human_readable,
            symbol_id=need.instrument,
        )
        return DataResult(
            request_id=need.need_id,
            status=DataResultStatus.MISSING,
            gaps=(data_gap,),
            as_of=datetime.now(tz=UTC),
        )

    @classmethod
    def _company_names_from_results(cls, results: Sequence[DataResult]) -> dict[str, str]:
        names: dict[str, str] = {}
        for result in results:
            names.update(cls._company_names_from_rows(tuple(row for row in result.rows if isinstance(row, Mapping))))
        return names

    @classmethod
    def _company_names_from_fetch_result(cls, result: Any) -> dict[str, str]:
        payload = getattr(result, "payload", None)
        rows: Sequence[Any]
        if isinstance(payload, Mapping):
            candidate = payload.get("rows")
            rows = candidate if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)) else ()
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            rows = payload
        else:
            rows = ()
        return cls._company_names_from_rows(tuple(row for row in rows if isinstance(row, Mapping)))

    @classmethod
    def _company_names_from_rows(cls, rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
        names: dict[str, str] = {}
        for row in rows:
            symbol = str(row.get("symbol_id") or row.get("ts_code") or row.get("code") or "").strip().upper()
            if not symbol or symbol in names:
                continue
            name = cls._company_name_from_row(row)
            if name:
                names[symbol] = name
        return names

    @classmethod
    def _company_name_from_row(cls, row: Mapping[str, Any]) -> str | None:
        for field in cls._COMPANY_NAME_FIELDS:
            name = str(row.get(field) or "").strip()
            if name:
                return name
        return None

    @staticmethod
    def _log_data_service_event(event: str, **fields: Any) -> None:
        parts = [f"data service {event}"]
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        _LOGGER.info(" ".join(parts))

    @staticmethod
    def _warehouse_only(requests: Sequence[DataRequest]) -> bool:
        return all(str(request.freshness_policy).strip() == "warehouse_only" for request in requests)

    @staticmethod
    def _metadata_only_warehouse_check(requests: Sequence[DataRequest]) -> bool:
        return all(str(request.freshness_policy).strip() == "warehouse_only" for request in requests) and all(
            str(request.consumer or "").strip() == "select"
            and str(request.consumer_id or "").strip().endswith(":coverage_check")
            for request in requests
        )

    @staticmethod
    def _force_provider_refresh(requests: Sequence[DataRequest]) -> bool:
        return bool(requests) and all(
            str(request.consumer or "").strip() == "select"
            and ":selection:universe_refresh:" in str(request.request_id)
            and str(request.freshness_policy).strip() == "trading_day"
            for request in requests
        )

    @staticmethod
    def _forced_refresh_warehouse_result(requests: Sequence[DataRequest]) -> WarehouseResult:
        return WarehouseResult(
            satisfied=False,
            rows=(),
            dataset_refs=(),
            gaps=tuple(
                DataGap.by_reason(
                    GapReason.WAREHOUSE_MISSING.value,
                    request_id=request.request_id,
                    market=request.market,
                    symbol_id=request.symbol_id,
                    data_type=request.data_type,
                    granularity=request.granularity,
                    message="select universe refresh request skips pre-refresh warehouse lookup",
                    as_of=request.as_of,
                )
                for request in requests
            ),
            freshness={
                "policy": "provider_refresh",
                "checked_requests": tuple(request.request_id for request in requests),
                "warehouse_check_skipped": True,
            },
        )

    def _check_warehouse(self, query_plan: QueryPlan, *, metadata_only: bool = False) -> WarehouseResult:
        if metadata_only:
            check_coverage = getattr(self.warehouse, "check_coverage", None)
            if callable(check_coverage):
                return check_coverage(query_plan.warehouse_checks, query_plan.required_coverage)
        return self.warehouse.check(query_plan.warehouse_checks, query_plan.required_coverage)

    @staticmethod
    def _read_any_attr(obj: Any, name: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            value = obj.get(name, default)
        else:
            value = getattr(obj, name, default)
        enum_value = getattr(value, "value", None)
        if enum_value is not None:
            return enum_value
        return value

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        return (value,)

    @staticmethod
    def _result_from_warehouse(request_id: str, warehouse_result: WarehouseResult) -> DataResult:
        return DataResult(
            request_id=request_id,
            status=DataResultStatus.READY,
            rows=warehouse_result.rows,
            dataset_refs=warehouse_result.dataset_refs,
            attempt_refs=warehouse_result.attempt_refs,
            gaps=warehouse_result.gaps,
            freshness=warehouse_result.freshness,
            as_of=datetime.now(tz=UTC),
        )

    def _compose_result(
        self,
        request_id: str,
        final_warehouse: WarehouseResult,
        ingest_results: Sequence[IngestResult],
        *,
        preserved_gaps: Sequence[DataGap] = (),
    ) -> DataResult:
        dataset_refs: list[str] = list(final_warehouse.dataset_refs)
        raw_refs: list[str] = []
        attempt_refs: list[str] = list(final_warehouse.attempt_refs)
        gaps: list[DataGap] = [self._coerce_gap(gap, request_id=request_id) for gap in preserved_gaps]
        if final_warehouse.satisfied and final_warehouse.dataset_refs:
            gaps = [self._demote_preserved_gap_after_fill(gap) for gap in gaps]
        gaps.extend(self._coerce_gap(gap, request_id=request_id) for gap in final_warehouse.gaps)
        for ingest_result in ingest_results:
            if not final_warehouse.dataset_refs:
                dataset_refs.extend(ingest_result.dataset_refs)
            raw_refs.extend(ingest_result.raw_refs)
            attempt_refs.extend(ingest_result.attempt_refs)
            if not final_warehouse.satisfied:
                gaps.extend(self._coerce_gap(gap, request_id=request_id) for gap in ingest_result.gaps)

        dataset_refs = list(dict.fromkeys(dataset_refs))
        raw_refs = list(dict.fromkeys(raw_refs))
        attempt_refs = list(dict.fromkeys(attempt_refs))
        gaps = self._drop_satisfied_date_range_missing_gaps(gaps, final_warehouse.satisfied)
        gaps = self._drop_filled_local_warehouse_empty_gaps(gaps, final_warehouse.rows)
        gaps = self._drop_satisfied_field_missing_gaps(gaps, final_warehouse.rows)
        gaps = self._drop_filled_field_missing_gaps(gaps, final_warehouse.satisfied, dataset_refs)
        gaps = self._drop_shadowed_field_missing_gaps(gaps, final_warehouse.rows)
        gaps = self._dedupe_gaps(gaps)
        status = self._resolve_status(final_warehouse.satisfied, dataset_refs, gaps)
        return DataResult(
            request_id=request_id,
            status=status,
            rows=final_warehouse.rows,
            dataset_refs=tuple(dataset_refs),
            raw_refs=tuple(raw_refs),
            attempt_refs=tuple(attempt_refs),
            gaps=tuple(gaps),
            freshness=final_warehouse.freshness,
            as_of=datetime.now(tz=UTC),
        )

    @staticmethod
    def _preserved_initial_local_gaps(request: DataRequest, initial_warehouse: WarehouseResult) -> tuple[DataGap, ...]:
        if request.market != Market.CRYPTO or request.data_type not in {"daily_bar", "intraday_bar"}:
            return ()
        preserved: list[DataGap] = []
        if any(gap.reason == GapReason.WAREHOUSE_MISSING for gap in initial_warehouse.gaps):
            preserved.append(local_warehouse_empty_gap(request))
        preserved.extend(gap for gap in initial_warehouse.gaps if gap.reason == GapReason.DATE_RANGE_MISSING)
        return tuple(preserved)

    @staticmethod
    def _dedupe_gaps(gaps: Sequence[DataGap]) -> list[DataGap]:
        deduped: list[DataGap] = []
        seen: set[tuple[Any, ...]] = set()
        for gap in gaps:
            key = (
                gap.request_id,
                getattr(gap.reason, "value", gap.reason),
                gap.market,
                gap.symbol_id,
                gap.data_type,
                gap.granularity,
                gap.required_fields,
                gap.human_readable,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(gap)
        return deduped

    @staticmethod
    def _drop_satisfied_field_missing_gaps(gaps: Sequence[DataGap], rows: Sequence[Mapping[str, Any]]) -> list[DataGap]:
        if not rows:
            return list(gaps)
        row_fields = DataService._fields_present_in_rows(rows)
        filtered: list[DataGap] = []
        for gap in gaps:
            if gap.reason == GapReason.FIELD_MISSING and gap.required_fields:
                required = {str(field) for field in gap.required_fields}
                if required.issubset(row_fields):
                    continue
            filtered.append(gap)
        return filtered

    @staticmethod
    def _drop_filled_field_missing_gaps(gaps: Sequence[DataGap], satisfied: bool, dataset_refs: Sequence[str]) -> list[DataGap]:
        if not satisfied or not dataset_refs:
            return list(gaps)
        return [gap for gap in gaps if gap.reason != GapReason.FIELD_MISSING]

    @staticmethod
    def _drop_shadowed_field_missing_gaps(gaps: Sequence[DataGap], rows: Sequence[Mapping[str, Any]]) -> list[DataGap]:
        if rows:
            return list(gaps)
        concrete_reasons = {
            GapReason.CREDENTIAL_MISSING,
            GapReason.EMPTY_RESULT,
            GapReason.RATE_LIMITED,
            GapReason.COOLDOWN_SKIPPED,
            GapReason.NOT_APPLICABLE,
            GapReason.WAREHOUSE_MISSING,
        }
        has_concrete_gap_by_request: set[str] = {
            gap.request_id
            for gap in gaps
            if gap.reason in concrete_reasons
        }
        return [
            gap
            for gap in gaps
            if not (
                gap.reason == GapReason.FIELD_MISSING
                and gap.request_id in has_concrete_gap_by_request
            )
        ]

    @staticmethod
    def _drop_satisfied_date_range_missing_gaps(gaps: Sequence[DataGap], satisfied: bool) -> list[DataGap]:
        if not satisfied:
            return list(gaps)
        return [gap for gap in gaps if gap.reason != GapReason.DATE_RANGE_MISSING]

    @staticmethod
    def _drop_filled_local_warehouse_empty_gaps(gaps: Sequence[DataGap], rows: Sequence[Mapping[str, Any]]) -> list[DataGap]:
        if not rows:
            return list(gaps)
        return [
            gap
            for gap in gaps
            if not (
                gap.reason == GapReason.WAREHOUSE_MISSING
                and "local_warehouse_empty" in str(gap.human_readable)
            )
        ]

    @staticmethod
    def _fields_present_in_rows(rows: Sequence[Mapping[str, Any]]) -> set[str]:
        fields: set[str] = set()
        for row in rows:
            fields.update(str(key) for key in row)
            fields.update(str(item) for item in tuple(row.get("field_set", ()) or ()))
            nested_row = row.get("row")
            if isinstance(nested_row, Mapping):
                fields.update(str(key) for key in nested_row)
        return fields

    @staticmethod
    def _demote_preserved_gap_after_fill(gap: DataGap) -> DataGap:
        if gap.reason not in {GapReason.WAREHOUSE_MISSING, GapReason.DATE_RANGE_MISSING}:
            return gap
        if gap.severity != GapSeverity.BLOCKER:
            return gap
        return gap.model_copy(update={"severity": GapSeverity.WARN})

    @staticmethod
    def _resolve_status(satisfied: bool, dataset_refs: Sequence[str], gaps: Sequence[Any]) -> DataResultStatus:
        if satisfied and dataset_refs:
            return DataResultStatus.READY
        if dataset_refs:
            return DataResultStatus.PARTIAL
        if gaps:
            return DataResultStatus.MISSING
        return DataResultStatus.ERROR

    @staticmethod
    def _coerce_gap(gap: Any, *, request_id: str) -> DataGap:
        if isinstance(gap, DataGap):
            if gap.request_id == request_id:
                return gap
            reason = getattr(gap.reason, "value", gap.reason)
            return gap.model_copy(
                update={
                    "request_id": request_id,
                    "gap_id": f"gap:{request_id}:{reason}:{uuid4().hex[:10]}",
                }
            )
        if isinstance(gap, dict):
            reason = gap.get("reason", GapReason.PROVIDER_ERROR.value)
            message = str(gap.get("human_readable") or reason)
            required_fields = tuple(gap.get("required_fields", ()) or ())
            evidence_refs = tuple(gap.get("evidence_refs", ()) or ())
            return DataGap.by_reason(
                reason,
                request_id=request_id,
                market=DataService._coerce_market(gap.get("market")),
                data_type=str(gap.get("data_type") or "unknown"),
                granularity=str(gap.get("granularity") or "unknown"),
                required_fields=required_fields,
                evidence_refs=evidence_refs,
                message=message,
                symbol_id=gap.get("symbol_id"),
            )
        reason = getattr(gap, "reason", GapReason.PROVIDER_ERROR.value)
        message = getattr(gap, "message", None) or getattr(gap, "human_readable", None) or str(reason)
        return DataGap.by_reason(
            reason,
            request_id=request_id,
            market=DataService._coerce_market(getattr(gap, "market", None)),
            data_type=str(getattr(gap, "data_type", "unknown")),
            granularity=str(getattr(gap, "granularity", "unknown")),
            required_fields=tuple(getattr(gap, "required_fields", ()) or ()),
            evidence_refs=tuple(getattr(gap, "evidence_refs", ()) or ()),
            message=message,
            symbol_id=getattr(gap, "symbol_id", None),
        )

    @staticmethod
    def _coerce_market(raw_market: Any) -> Market:
        if isinstance(raw_market, Market):
            return raw_market
        if isinstance(raw_market, str):
            try:
                return Market(raw_market)
            except ValueError:
                return Market.CN_A
        return Market.CN_A

    @staticmethod
    def _slice_warehouse_result(warehouse_result: WarehouseResult, request: DataRequest) -> WarehouseResult:
        request_id = request.request_id
        request_gaps = tuple(gap for gap in warehouse_result.gaps if getattr(gap, "request_id", None) == request_id)
        coverage_dataset_refs = DataService._coverage_dataset_refs_for_request(
            warehouse_result=warehouse_result,
            request_id=request_id,
        )
        coverage_ref_set = set(coverage_dataset_refs) if coverage_dataset_refs is not None else None
        paired_rows = tuple(
            (row, dataset_ref)
            for row, dataset_ref in zip(warehouse_result.rows, warehouse_result.dataset_refs, strict=False)
            if (coverage_ref_set is None or dataset_ref in coverage_ref_set)
            and DataService._dataset_ref_matches_request(dataset_ref, request)
            and DataService._row_matches_request(row, request)
        )
        if paired_rows:
            rows = tuple(row for row, _dataset_ref in paired_rows)
            dataset_refs = tuple(dict.fromkeys(dataset_ref for _row, dataset_ref in paired_rows))
        elif any(isinstance(row, dict) for row in warehouse_result.rows):
            rows = ()
            dataset_refs = ()
            if not request_gaps:
                request_gaps = (
                    DataGap.by_reason(
                        GapReason.WAREHOUSE_MISSING,
                        request_id=request_id,
                        market=request.market,
                        data_type=request.data_type,
                        granularity=request.granularity,
                        symbol_id=request.symbol_id,
                        message="warehouse_recheck_missing_for_request",
                    ),
                )
        else:
            rows = warehouse_result.rows
            dataset_refs = coverage_dataset_refs if coverage_dataset_refs is not None else warehouse_result.dataset_refs
        freshness = DataService._slice_freshness_for_request(warehouse_result=warehouse_result, request_id=request_id)
        dataset_ref_set = set(dataset_refs)
        return WarehouseResult(
            satisfied=not request_gaps and bool(dataset_refs),
            rows=rows,
            dataset_refs=dataset_refs,
            attempt_refs=DataService._warehouse_attempt_refs_for_dataset_refs(
                warehouse_result=warehouse_result,
                dataset_refs=dataset_refs,
            ),
            attempt_refs_by_dataset_ref={
                dataset_ref: refs
                for dataset_ref, refs in warehouse_result.attempt_refs_by_dataset_ref.items()
                if dataset_ref in dataset_ref_set
            },
            gaps=request_gaps,
            freshness=freshness,
        )

    @staticmethod
    def _slice_freshness_for_request(
        *,
        warehouse_result: WarehouseResult,
        request_id: str,
    ) -> dict[str, Any]:
        freshness = dict(warehouse_result.freshness)
        checked_requests = tuple(freshness.get("checked_requests", ()) or ())
        if checked_requests:
            freshness["checked_requests"] = [request_id] if request_id in checked_requests else []
        coverage_items = freshness.get("coverage_by_request")
        if isinstance(coverage_items, Sequence) and not isinstance(coverage_items, (str, bytes, bytearray)):
            freshness["coverage_by_request"] = tuple(
                item
                for item in coverage_items
                if isinstance(item, Mapping) and str(item.get("request_id") or "") == request_id
            )
        return freshness

    @staticmethod
    def _coverage_dataset_refs_for_request(
        *,
        warehouse_result: WarehouseResult,
        request_id: str,
    ) -> tuple[str, ...] | None:
        coverage_items = warehouse_result.freshness.get("coverage_by_request")
        if not isinstance(coverage_items, Sequence) or isinstance(coverage_items, (str, bytes, bytearray)):
            return None
        for item in coverage_items:
            if not isinstance(item, Mapping) or str(item.get("request_id") or "") != request_id:
                continue
            raw_refs = item.get("dataset_refs")
            if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes, bytearray)):
                return ()
            return tuple(str(ref) for ref in raw_refs)
        return ()

    @staticmethod
    def _warehouse_attempt_refs_for_dataset_refs(
        *,
        warehouse_result: WarehouseResult,
        dataset_refs: Sequence[str],
    ) -> tuple[str, ...]:
        if not dataset_refs:
            return ()
        refs_by_dataset = warehouse_result.attempt_refs_by_dataset_ref
        if not refs_by_dataset:
            return warehouse_result.attempt_refs
        return tuple(
            dict.fromkeys(
                attempt_ref
                for dataset_ref in dataset_refs
                for attempt_ref in refs_by_dataset.get(dataset_ref, ())
            )
        )

    @staticmethod
    def _dataset_ref_matches_request(dataset_ref: str, request: DataRequest) -> bool:
        parts = str(dataset_ref).split(":")
        if len(parts) < 4 or parts[0] != "dataset":
            return True
        ref_dataset = parts[1]
        ref_market = parts[2]
        request_market = getattr(request.market, "value", request.market)
        return ref_dataset == request.data_type and ref_market == str(request_market)

    @staticmethod
    def _row_matches_request(row: Any, request: DataRequest) -> bool:
        if not isinstance(row, dict):
            return True
        if row.get("dataset") and str(row.get("dataset")) != request.data_type:
            return False
        row_market = getattr(row.get("market"), "value", row.get("market"))
        request_market = getattr(request.market, "value", request.market)
        if row_market and str(row_market) != str(request_market):
            return False
        if request.symbol_id and row.get("symbol_id") and not _symbol_matches_request(str(row.get("symbol_id")), request):
            return False
        if request.universe_ref and row.get("universe_ref") and str(row.get("universe_ref")) != request.universe_ref:
            return False
        if row.get("granularity") and str(row.get("granularity")) != request.granularity:
            return False
        if request.fields:
            row_fields = set(str(key) for key in row)
            row_fields.update(str(item) for item in tuple(row.get("field_set", ()) or ()))
            nested_row = row.get("row")
            if isinstance(nested_row, Mapping):
                row_fields.update(str(key) for key in nested_row)
            if not all(str(field) in row_fields for field in request.fields):
                return False
        row_start = DataService._date_key(row.get("period_start"))
        row_end = DataService._date_key(row.get("period_end"))
        request_start = DataService._date_key(request.date_range_start)
        request_end = DataService._date_key(request.date_range_end)
        if request_start is not None and row_end is not None and row_end < request_start:
            return False
        if request_end is not None and row_start is not None and row_start > request_end:
            return False
        return True

    @staticmethod
    def _date_key(value: Any) -> str | None:
        if value is None:
            return None
        value_date = getattr(value, "date", None)
        if callable(value_date):
            return value_date().isoformat()
        text = str(value).strip()
        return text[:10] if text else None

    @staticmethod
    def _slice_ingest_results(ingest_results: Sequence[tuple[Any, IngestResult]], request_id: str) -> tuple[IngestResult, ...]:
        selected: list[IngestResult] = []
        for batch, ingest_result in ingest_results:
            batch_request_ids = tuple(getattr(batch, "request_ids", ()) or ())
            if request_id in batch_request_ids:
                selected.append(ingest_result)
        return tuple(selected)

    def _is_non_remote_ingest(self, ingest_result: IngestResult) -> bool:
        return any(str(getattr(gap.reason, "value", gap.reason)) in self._NON_REMOTE_GAP_REASONS for gap in ingest_result.gaps)

    @staticmethod
    def _force_non_remote(ingest_result: IngestResult) -> IngestResult:
        if not ingest_result.remote_success:
            return ingest_result
        payload = ingest_result.model_dump()
        payload["remote_success"] = False
        return IngestResult.model_validate(payload)


def _symbol_matches_request(row_symbol: str, request: DataRequest) -> bool:
    request_symbol = str(request.symbol_id or "")
    if row_symbol == request_symbol:
        return True
    if request.market != Market.CRYPTO:
        return False
    return _crypto_provider_symbol(row_symbol) == _crypto_provider_symbol(request_symbol)


def _crypto_provider_symbol(value: str) -> str | None:
    try:
        return resolve_crypto_provider_symbols(value).crypto_provider_symbol
    except InstrumentResolveError:
        return None


def _data_request_from_need(need: DataNeed) -> DataRequest:
    contract = public_output_contract_for_api(str(need.api_id))
    data_type = str(contract.get("dataset") or _need_api_suffix(need))
    granularity = str(need.granularity or contract.get("granularity") or "unknown")
    fields = tuple(str(item) for item in tuple(contract.get("required_fields", ())) if str(item).strip()) or ("symbol_id",)
    market_defaults = _market_defaults(need.market)
    base_asset, quote_asset = _crypto_assets(need.instrument) if need.market == Market.CRYPTO else (None, None)
    return DataRequest(
        request_id=need.need_id,
        market=need.market,
        symbol_id=_warehouse_symbol_id(need),
        universe_ref=None,
        exchange=market_defaults["exchange"],
        currency=quote_asset or market_defaults["currency"],
        timezone=str(market_defaults["timezone"]),
        calendar=str(market_defaults["calendar"]),
        base_asset=base_asset,
        quote_asset=quote_asset,
        data_type=data_type,
        granularity=granularity,
        fields=fields,
        date_range_start=need.time_range_start,
        date_range_end=need.time_range_end,
        freshness_policy=need.freshness_policy,
        consumer=need.consumer,  # type: ignore[arg-type]
        consumer_id=need.consumer,
        as_of=datetime.now(tz=UTC),
        deadline_at=need.deadline_at,
    )


def _market_defaults(market: Market) -> dict[str, str | None]:
    if market == Market.CRYPTO:
        return {"exchange": "BINANCE", "currency": "USDT", "timezone": "UTC", "calendar": "CRYPTO_24_7"}
    if market == Market.US:
        return {"exchange": "NASDAQ", "currency": "USD", "timezone": "America/New_York", "calendar": "US_NYSE_NASDAQ"}
    if market == Market.HK:
        return {"exchange": "XHKG", "currency": "HKD", "timezone": "Asia/Hong_Kong", "calendar": "HK_XHKG"}
    return dict(_CN_A_MARKET_DEFAULTS)


def _crypto_assets(instrument: str) -> tuple[str | None, str | None]:
    symbols = resolve_crypto_provider_symbols(instrument)
    return symbols.crypto_base_symbol, symbols.crypto_quote_symbol


def _warehouse_symbol_id(need: DataNeed) -> str:
    if need.market == Market.CRYPTO:
        return resolve_crypto_provider_symbols(need.instrument).crypto_provider_symbol or need.instrument
    return need.instrument


def _adapter_endpoint_id(call: ProviderCallSpec) -> str:
    endpoint = _official_catalog_endpoint(call.catalog_endpoint_id)
    request_template = getattr(endpoint, "request_template", None)
    if isinstance(request_template, Mapping):
        explicit = _optional_text(request_template.get("adapter_endpoint_id"))
        if explicit:
            return explicit
    for candidate in _structured_endpoint_id_candidates(call):
        stripped = _strip_market_prefix(candidate)
        if stripped:
            return stripped
    return call.catalog_endpoint_id


def _unique_refs(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _unique_rows(values: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    seen: set[str] = set()
    rows: list[Mapping[str, Any]] = []
    for row in values:
        key = repr(sorted(dict(row).items()))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return tuple(rows)


def _unique_gaps(values: Sequence[DataGap]) -> tuple[DataGap, ...]:
    seen: set[tuple[Any, ...]] = set()
    gaps: list[DataGap] = []
    for gap in values:
        reason = getattr(getattr(gap, "reason", None), "value", getattr(gap, "reason", None))
        key = (
            getattr(gap, "request_id", None),
            reason,
            getattr(gap, "data_type", None),
            getattr(gap, "granularity", None),
            tuple(getattr(gap, "required_fields", ()) or ()),
            getattr(gap, "human_readable", None),
        )
        if key in seen:
            continue
        seen.add(key)
        gaps.append(gap)
    return tuple(gaps)


def _data_result_rank(result: DataResult) -> int:
    return {
        DataResultStatus.READY: 4,
        DataResultStatus.PARTIAL: 3,
        DataResultStatus.MISSING: 2,
        DataResultStatus.ERROR: 1,
    }.get(result.status, 0)


def _official_catalog_endpoint(endpoint_id: str) -> Any | None:
    return next((endpoint for endpoint in iter_official_catalog_endpoints() if endpoint.endpoint_id == endpoint_id), None)


def _structured_endpoint_id_candidates(call: ProviderCallSpec) -> tuple[str, ...]:
    raw = call.catalog_endpoint_id
    pieces: list[str] = []
    if "." in raw:
        pieces.append(raw.rsplit(".", 1)[1])
        pieces.append(raw.split(".", 1)[1])
        pieces.append(raw.split(".", 1)[1].replace(".", "_"))
    pieces.extend((raw.replace(".", "_"), raw, call.official_path_or_api_name))
    return tuple(_dedupe(piece for piece in pieces if piece))


def _strip_market_prefix(value: str) -> str:
    text = str(value or "").strip()
    for prefix in ("cn_a_", "us_", "hk_", "crypto_"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _need_api_suffix(need: DataNeed) -> str:
    return str(need.api_id or "").strip().lower().rsplit(".", 1)[-1]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: Sequence[Any]) -> tuple[Any, ...]:
    seen: set[Any] = set()
    output: list[Any] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return tuple(output)
