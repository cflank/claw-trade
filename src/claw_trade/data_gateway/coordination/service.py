from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from claw_trade.data_gateway.models import (
    DataGap,
    DataPlan,
    DataRequest,
    DataResult,
    DataResultStatus,
    GapReason,
    Market,
    GateDecision,
    IngestResult,
    QueryPlan,
    WarehouseResult,
)
from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler


class QueryPlannerLike(Protocol):
    def validate_and_normalize(self, request: DataRequest) -> QueryPlan: ...

    def validate_and_normalize_many(self, requests: Sequence[DataRequest]) -> QueryPlan: ...


class WarehouseLike(Protocol):
    def check(self, checks: Sequence[Any], coverage: Any) -> WarehouseResult: ...

    def recheck(self, checks: Sequence[Any], coverage: Any) -> WarehouseResult: ...


class ProviderSelectorLike(Protocol):
    def select_candidates(self, gaps: Sequence[Any], plan: QueryPlan) -> tuple[Any, ...]: ...

    def read_capabilities(self, candidates: Sequence[Any]) -> Any: ...


class RequestCoalescerLike(Protocol):
    def coalesce(self, gaps: Sequence[Any], candidates: Sequence[Any], capabilities: Any) -> tuple[Any, ...]: ...


class ProviderBatchPlannerLike(Protocol):
    def build_batches(self, groups: Sequence[Any], capabilities: Any) -> tuple[Any, ...]: ...


class DataRunSchedulerLike(Protocol):
    def schedule(self, batches: Sequence[Any], context: DataRunScheduleContext) -> tuple[Any, ...]: ...


class ExecutionGateLike(Protocol):
    def enter(self, batch: Any) -> GateDecision: ...

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None: ...


class FetchEngineLike(Protocol):
    def fetch(self, batch: Any) -> Any: ...


class IngestLike(Protocol):
    def ingest(self, result: Any, batch: Any) -> IngestResult: ...

    def record_gate_result(self, batch: Any, gate: GateDecision) -> IngestResult: ...


class DataService:
    _NON_REMOTE_GATE_KINDS = {"cache_hit", "cached_empty", "rate_limited", "shared_result", "cooldown_skipped"}
    _NON_REMOTE_GAP_REASONS = {
        "cache_hit",
        "shared_result",
        "rate_limited",
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
        provider_selector: ProviderSelectorLike,
        coalescer: RequestCoalescerLike,
        batch_planner: ProviderBatchPlannerLike,
        execution_gate: ExecutionGateLike,
        fetch_engine: FetchEngineLike,
        ingest: IngestLike,
        scheduler: DataRunSchedulerLike | None = None,
    ) -> None:
        self.query_planner = query_planner
        self.warehouse = warehouse
        self.provider_selector = provider_selector
        self.coalescer = coalescer
        self.batch_planner = batch_planner
        self.scheduler = scheduler or DataRunScheduler()
        self.execution_gate = execution_gate
        self.fetch_engine = fetch_engine
        self.ingest = ingest

    def get_data(self, request: DataRequest) -> DataResult:
        query_plan = self.query_planner.validate_and_normalize(request)
        return self._execute_query_plan(query_plan)[0]

    def get_data_batch(self, requests: Sequence[DataRequest]) -> list[DataResult]:
        plan = self.plan_batch(requests)
        return self.execute_plan(plan)

    def plan_batch(self, requests: Sequence[DataRequest]) -> DataPlan:
        query_plan = self.query_planner.validate_and_normalize_many(requests)
        return DataPlan(
            plan_id=f"plan:{uuid4().hex}",
            request_ids=tuple(req.request_id for req in requests),
            query_plan=query_plan,
            created_at=datetime.now(tz=UTC),
        )

    def execute_plan(self, plan: DataPlan) -> list[DataResult]:
        return self._execute_query_plan(
            plan.query_plan,
            schedule_context=DataRunScheduleContext.for_plan(run_id=plan.plan_id, run_started_at=plan.created_at),
        )

    def _execute_query_plan(self, query_plan: QueryPlan, *, schedule_context: DataRunScheduleContext | None = None) -> list[DataResult]:
        requests = query_plan.normalized_requests
        if not requests:
            return []

        initial = self.warehouse.check(query_plan.warehouse_checks, query_plan.required_coverage)
        if initial.satisfied:
            return [
                self._result_from_warehouse(request.request_id, self._slice_warehouse_result(initial, request))
                for request in requests
            ]

        candidates = self.provider_selector.select_candidates(initial.gaps, query_plan)
        capabilities = self.provider_selector.read_capabilities(candidates)
        groups = self.coalescer.coalesce(initial.gaps, candidates, capabilities)
        batches = self.batch_planner.build_batches(groups, capabilities)
        if schedule_context is not None:
            batches = self.scheduler.schedule(batches, schedule_context)

        ingest_results: list[tuple[Any, IngestResult]] = []
        for batch in batches:
            gate = self.execution_gate.enter(batch)
            if gate.kind in self._NON_REMOTE_GATE_KINDS:
                ingest_results.append((batch, self.ingest.record_gate_result(batch, gate)))
                continue
            fetch_result = self.fetch_engine.fetch(batch)
            cooldown_marker = getattr(self.execution_gate, "mark_cooldown_after_fetch", None)
            if callable(cooldown_marker):
                cooldown_marker(batch, fetch_result)
            ingest_result = self.ingest.ingest(fetch_result, batch)
            if self._is_non_remote_ingest(ingest_result):
                ingest_result = self._force_non_remote(ingest_result)
            ingest_results.append((batch, ingest_result))
            if gate.kind == "owner" and gate.owner_token:
                self.execution_gate.publish_shared_result(batch.single_flight_key, gate.owner_token, ingest_result)

        final = self.warehouse.recheck(query_plan.warehouse_checks, query_plan.required_coverage)
        results: list[DataResult] = []
        for request in requests:
            final_per_request = self._slice_warehouse_result(final, request)
            ingest_per_request = self._slice_ingest_results(ingest_results, request.request_id)
            results.append(self._compose_result(request.request_id, final_per_request, ingest_per_request))
        return results

    @staticmethod
    def _result_from_warehouse(request_id: str, warehouse_result: WarehouseResult) -> DataResult:
        return DataResult(
            request_id=request_id,
            status=DataResultStatus.READY,
            rows=warehouse_result.rows,
            dataset_refs=warehouse_result.dataset_refs,
            gaps=warehouse_result.gaps,
            freshness=warehouse_result.freshness,
            as_of=datetime.now(tz=UTC),
        )

    def _compose_result(
        self,
        request_id: str,
        final_warehouse: WarehouseResult,
        ingest_results: Sequence[IngestResult],
    ) -> DataResult:
        dataset_refs: list[str] = list(final_warehouse.dataset_refs)
        raw_refs: list[str] = []
        attempt_refs: list[str] = []
        gaps: list[DataGap] = [self._coerce_gap(gap, request_id=request_id) for gap in final_warehouse.gaps]
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
            return gap
        if isinstance(gap, dict):
            reason = gap.get("reason", GapReason.PROVIDER_ERROR.value)
            message = str(gap.get("human_readable") or reason)
            required_fields = tuple(gap.get("required_fields", ()) or ())
            evidence_refs = tuple(gap.get("evidence_refs", ()) or ())
            return DataGap.by_reason(
                reason,
                request_id=str(gap.get("request_id") or request_id),
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
            request_id=str(getattr(gap, "request_id", request_id)),
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
        paired_rows = tuple(
            (row, dataset_ref)
            for row, dataset_ref in zip(warehouse_result.rows, warehouse_result.dataset_refs, strict=False)
            if DataService._row_matches_request(row, request)
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
            dataset_refs = warehouse_result.dataset_refs
        checked_requests = tuple(warehouse_result.freshness.get("checked_requests", ()) or ())
        if checked_requests:
            freshness: dict[str, Any] = {k: v for k, v in warehouse_result.freshness.items() if k != "checked_requests"}
            if request_id in checked_requests:
                freshness["checked_requests"] = [request_id]
            else:
                freshness["checked_requests"] = []
        else:
            freshness = dict(warehouse_result.freshness)
        return WarehouseResult(
            satisfied=not request_gaps and bool(dataset_refs),
            rows=rows,
            dataset_refs=dataset_refs,
            gaps=request_gaps,
            freshness=freshness,
        )

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
        if request.symbol_id and row.get("symbol_id") and str(row.get("symbol_id")) != request.symbol_id:
            return False
        if request.universe_ref and row.get("universe_ref") and str(row.get("universe_ref")) != request.universe_ref:
            return False
        if row.get("granularity") and str(row.get("granularity")) != request.granularity:
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
