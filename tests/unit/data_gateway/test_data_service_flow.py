from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResultStatus,
    FetchResult,
    FetchStatus,
    GapReason,
    GapSeverity,
    GateDecision,
    IngestResult,
    Market,
    ProviderBatchPlan,
    RequiredLevel,
    WarehouseResult,
)


def _request(request_id: str = "req-service-1") -> DataRequest:
    return DataRequest.model_validate(
        {
            "request_id": request_id,
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "data_type": "daily_bar",
            "granularity": "daily",
            "fields": ("close",),
            "freshness_policy": "trading_day",
            "consumer": "report",
            "consumer_id": "market_analyst",
            "as_of": datetime(2026, 5, 31, tzinfo=UTC),
        }
    )


def _symbol_request(request_id: str, symbol_id: str) -> DataRequest:
    payload = _request(request_id).model_dump()
    payload["symbol_id"] = symbol_id
    return DataRequest.model_validate(payload)


def _gap(request_id: str) -> DataGap:
    return DataGap(
        gap_id=f"gap:{request_id}",
        request_id=request_id,
        severity=GapSeverity.BLOCKER,
        reason=GapReason.WAREHOUSE_MISSING,
        market=Market.CN_A,
        symbol_id="600519.SH",
        data_type="daily_bar",
        granularity="daily",
        human_readable="warehouse_missing",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )


def _batch_plan(request_id: str) -> ProviderBatchPlan:
    return ProviderBatchPlan(
        batch_id=f"batch:{request_id}",
        plan_id="plan:1",
        provider_id="provider-1",
        endpoint_id="daily",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        request_ids=(request_id,),
        symbol_ids=("600519.SH",),
        fields_union=("close",),
        params_redacted={"symbol": "600519.SH"},
        priority_rank=1,
        required_level=RequiredLevel.REQUIRED,
        cache_key="cache:1",
        rate_limit_key="rate:1",
        single_flight_key="flight:1",
        provider_config_version="cfg:1",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )


class _Warehouse:
    def __init__(
        self,
        events: list[str],
        *,
        satisfied_on_check: bool,
        attempt_refs: tuple[str, ...] = (),
    ) -> None:
        self._events = events
        self._satisfied_on_check = satisfied_on_check
        self._attempt_refs = attempt_refs

    def check(self, checks, coverage) -> WarehouseResult:
        self._events.append("warehouse.check")
        if self._satisfied_on_check:
            return WarehouseResult(
                satisfied=True,
                rows=({"close": 1.0},),
                dataset_refs=("dataset:warehouse",),
                attempt_refs=self._attempt_refs,
                freshness={"policy": "trading_day"},
            )
        request_id = checks[0].request_id
        return WarehouseResult(satisfied=False, gaps=(_gap(request_id),))

    def recheck(self, checks, coverage) -> WarehouseResult:
        self._events.append("warehouse.recheck")
        return WarehouseResult(
            satisfied=True,
            rows=({"close": 2.0},),
            dataset_refs=("dataset:recheck",),
            freshness={"policy": "trading_day"},
        )


class _MetadataWarehouse(_Warehouse):
    def check_coverage(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        self._events.append("warehouse.check_coverage")
        return WarehouseResult(
            satisfied=True,
            rows=(),
            dataset_refs=("dataset:metadata-only",),
            freshness={"policy": "warehouse_only"},
        )


class _MultiSymbolWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        return WarehouseResult(satisfied=False, gaps=tuple(_gap(check.request_id) for check in checks))

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        return WarehouseResult(
            satisfied=True,
            rows=(
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "symbol_id": "600519.SH",
                    "granularity": "daily",
                    "close": 2.0,
                },
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "symbol_id": "000001.SZ",
                    "granularity": "daily",
                    "close": 3.0,
                },
            ),
            dataset_refs=("dataset:600519", "dataset:000001"),
            freshness={"policy": "trading_day", "checked_requests": ("req-a", "req-b")},
        )


class _PartialBatchWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        return WarehouseResult(satisfied=False, gaps=tuple(_gap(check.request_id) for check in checks))

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        return WarehouseResult(
            satisfied=False,
            rows=(
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "symbol_id": "600519.SH",
                    "granularity": "daily",
                    "close": 2.0,
                },
            ),
            dataset_refs=("dataset:600519",),
            gaps=(_gap("req-b"),),
            freshness={"policy": "trading_day", "checked_requests": ("req-a", "req-b")},
        )


class _Selector:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def select_candidates(self, gaps, plan):
        self._events.append("provider_selector.select_candidates")
        return ({"provider_id": "provider-1"},)

    def read_capabilities(self, candidates):
        self._events.append("provider_selector.read_capabilities")
        return {"count": len(candidates)}


class _Coalescer:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def coalesce(self, gaps, candidates, capabilities):
        self._events.append("coalescer.coalesce")
        assert capabilities["count"] == 1
        return ({"group_id": "group-1", "request_id": gaps[0].request_id},)


class _BatchPlanner:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def build_batches(self, groups, capabilities):
        self._events.append("batch_planner.build_batches")
        assert capabilities["count"] == 1
        return (_batch_plan(groups[0]["request_id"]),)


class _Scheduler:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.context = None

    def schedule(self, batches, context):
        self._events.append("scheduler.schedule")
        self.context = context
        return batches


class _ExecutionGate:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def enter(self, batch):
        self._events.append("execution_gate.enter")
        return GateDecision(kind="owner", owner_token="owner-1")

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
        self._events.append("execution_gate.publish_shared_result")


class _FetchEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def fetch(self, batch):
        self._events.append("fetch_engine.fetch")
        return FetchResult(
            fetch_id="fetch-1",
            batch_id=batch.batch_id,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=batch.market,
            symbol_ids=batch.symbol_ids,
            status=FetchStatus.SUCCESS,
            payload={"rows": [{"close": 2.0}]},
            row_count=1,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _Ingest:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        return IngestResult(
            ingest_id="ingest-1",
            batch_id=batch.batch_id,
            status="ingested",
            dataset_refs=("dataset:ingest",),
            raw_refs=("raw:1",),
            attempt_refs=("attempt:1",),
            remote_success=True,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )

    def record_gate_result(self, batch, gate):
        self._events.append("ingest.record_gate_result")
        return IngestResult(
            ingest_id="ingest-gate",
            batch_id=batch.batch_id,
            status="non_remote_recorded",
            attempt_refs=("attempt:gate",),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _IngestWithSupersededGap(_Ingest):
    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        return IngestResult(
            ingest_id="ingest-superseded-gap",
            batch_id=batch.batch_id,
            status="partial",
            dataset_refs=("dataset:backup",),
            raw_refs=("raw:backup",),
            attempt_refs=("attempt:backup",),
            gaps=(
                DataGap.by_reason(
                    "field_missing",
                    request_id=batch.request_ids[0],
                    market=batch.market,
                    data_type=batch.data_type,
                    granularity=batch.granularity,
                    required_fields=("volume",),
                    symbol_id=batch.symbol_ids[0],
                ),
            ),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _IngestWithoutDatasetRefs(_Ingest):
    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        return IngestResult(
            ingest_id="ingest-no-dataset",
            batch_id=batch.batch_id,
            status="failed",
            raw_refs=("raw:failed",),
            attempt_refs=("attempt:failed",),
            gaps=(
                DataGap.by_reason(
                    "empty_result",
                    request_id=batch.request_ids[0],
                    market=batch.market,
                    data_type=batch.data_type,
                    granularity=batch.granularity,
                    symbol_id=batch.symbol_ids[0],
                ),
            ),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _Planner(QueryPlanner):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def validate_and_normalize(self, request: DataRequest):
        self._events.append("query_planner.validate_and_normalize")
        return super().validate_and_normalize(request)

    def validate_and_normalize_many(self, requests):
        self._events.append("query_planner.validate_and_normalize_many")
        return super().validate_and_normalize_many(requests)


def test_data_service_warehouse_hit_does_not_fetch_remote() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=True),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    result = service.get_data(_request("req-hit"))
    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:warehouse",)
    assert events == ["query_planner.validate_and_normalize", "warehouse.check"]


def test_data_service_warehouse_hit_returns_attempt_refs() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=True, attempt_refs=("attempt:warehouse",)),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    result = service.get_data(_request("req-hit-lineage"))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:warehouse",)
    assert result.attempt_refs == ("attempt:warehouse",)
    assert events == ["query_planner.validate_and_normalize", "warehouse.check"]


def test_data_service_follows_fixed_ten_step_order_when_warehouse_missing() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    result = service.get_data(_request("req-miss"))
    assert result.status == DataResultStatus.READY
    assert "dataset:recheck" in result.dataset_refs
    assert "raw:1" in result.raw_refs
    assert "attempt:1" in result.attempt_refs
    assert events == [
        "query_planner.validate_and_normalize",
        "warehouse.check",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_warehouse_only_does_not_fetch_remote_when_missing() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    request = _request("req-warehouse-only").model_copy(update={"freshness_policy": "warehouse_only"})

    result = service.get_data(request)

    assert result.status == DataResultStatus.MISSING
    assert result.dataset_refs == ()
    assert [gap.reason for gap in result.gaps] == [GapReason.WAREHOUSE_MISSING]
    assert events == ["query_planner.validate_and_normalize", "warehouse.check"]


def test_data_service_warehouse_only_uses_metadata_coverage_without_rows() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_MetadataWarehouse(events, satisfied_on_check=True),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    request = _request("req-warehouse-only-ready").model_copy(
        update={"freshness_policy": "warehouse_only", "consumer": "select", "consumer_id": "select-run:coverage_check"}
    )

    result = service.get_data(request)

    assert result.status == DataResultStatus.READY
    assert result.rows == ()
    assert result.dataset_refs == ("dataset:metadata-only",)
    assert events == ["query_planner.validate_and_normalize", "warehouse.check_coverage"]


def test_data_service_select_universe_refresh_skips_pre_refresh_warehouse_check() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    payload = _request("sel-unit:selection:universe_refresh:1:all_a_shares:daily_bar").model_dump()
    payload.update({"symbol_id": None, "universe_ref": "all_a_shares", "consumer": "select", "consumer_id": "sel-unit"})

    result = service.get_data(DataRequest.model_validate(payload))

    assert result.status == DataResultStatus.READY
    assert "warehouse.check" not in events
    assert events == [
        "query_planner.validate_and_normalize",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_ready_result_drops_superseded_ingest_gaps() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_IngestWithSupersededGap(events),
    )

    result = service.get_data(_request("req-superseded-gap"))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:recheck",)
    assert result.raw_refs == ("raw:backup",)
    assert result.attempt_refs == ("attempt:backup",)
    assert result.gaps == ()


def test_data_service_skips_recheck_when_remote_attempt_writes_no_dataset_refs() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_IngestWithoutDatasetRefs(events),
    )

    result = service.get_data(_request("req-no-dataset"))

    assert result.status == DataResultStatus.MISSING
    assert result.dataset_refs == ()
    assert result.raw_refs == ("raw:failed",)
    assert result.attempt_refs == ("attempt:failed",)
    assert "warehouse.recheck" not in events


def test_data_service_dedupes_identical_gaps() -> None:
    gap = DataGap.by_reason(
        "field_missing",
        request_id="req-gap",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        required_fields=("volume",),
        symbol_id="600519.SH",
    )
    duplicate = gap.model_copy(update={"gap_id": "gap:req-gap:field_missing:duplicate"})

    assert DataService._dedupe_gaps((gap, duplicate)) == [gap]


def test_data_service_batch_uses_unified_ten_step_flow_once() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    req1 = _request("req-batch-1")
    req2 = _request("req-batch-2")
    results = service.get_data_batch((req1, req2))
    assert [item.request_id for item in results] == ["req-batch-1", "req-batch-2"]
    assert all(item.status == DataResultStatus.READY for item in results)
    assert events == [
        "query_planner.validate_and_normalize_many",
        "warehouse.check",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_execute_plan_passes_plan_start_to_scheduler() -> None:
    events: list[str] = []
    scheduler = _Scheduler(events)
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        scheduler=scheduler,
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    plan = service.plan_batch((_request("req-scheduled"),))

    service.execute_plan(plan)

    assert scheduler.context is not None
    assert scheduler.context.run_id == plan.plan_id
    assert scheduler.context.run_started_at == plan.created_at
    assert events == [
        "query_planner.validate_and_normalize_many",
        "warehouse.check",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "scheduler.schedule",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_batch_slices_recheck_dataset_refs_per_request() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_MultiSymbolWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    results = service.get_data_batch(
        (
            _symbol_request("req-a", "600519.SH"),
            _symbol_request("req-b", "000001.SZ"),
            _symbol_request("req-c", "999999.SH"),
        )
    )

    assert results[0].dataset_refs == ("dataset:600519",)
    assert results[0].rows == (
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "granularity": "daily",
            "close": 2.0,
        },
    )
    assert results[1].dataset_refs == ("dataset:000001",)
    assert results[1].rows == (
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "000001.SZ",
            "granularity": "daily",
            "close": 3.0,
        },
    )
    assert results[2].status == DataResultStatus.MISSING
    assert results[2].dataset_refs == ()
    assert results[2].gaps[0].reason == GapReason.WAREHOUSE_MISSING


def test_data_service_batch_ready_request_not_downgraded_by_other_request_gap() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_PartialBatchWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_IngestWithSupersededGap(events),
    )

    results = service.get_data_batch(
        (
            _symbol_request("req-a", "600519.SH"),
            _symbol_request("req-b", "000001.SZ"),
        )
    )

    assert results[0].status == DataResultStatus.READY
    assert results[0].dataset_refs == ("dataset:600519",)
    assert results[0].gaps == ()
    assert results[1].status == DataResultStatus.MISSING
    assert results[1].gaps[0].request_id == "req-b"


def test_data_service_rechecks_between_provider_priority_waves_and_skips_slow_fallback() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_WaveWarehouse(events),
        provider_selector=_FallbackSelector(events),
        coalescer=_PassThroughCoalescer(events),
        batch_planner=_CandidateBatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_ProviderTrackingFetchEngine(events),
        ingest=_Ingest(events),
    )

    [result] = service.get_data_batch((_request("req-wave"),))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:wave",)
    assert "fetch:provider-fast" in events
    assert "fetch:provider-slow" not in events


class _WaveWarehouse:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        self._events.append("warehouse.check")
        return WarehouseResult(satisfied=False, gaps=(_gap(checks[0].request_id),))

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        self._events.append("warehouse.recheck")
        if "fetch:provider-fast" not in self._events:
            return WarehouseResult(satisfied=False, gaps=(_gap("req-wave"),))
        return WarehouseResult(
            satisfied=True,
            rows=({"close": 9.0},),
            dataset_refs=("dataset:wave",),
            freshness={"policy": "trading_day"},
        )


class _FallbackSelector:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def select_candidates(self, gaps, plan):
        del plan
        self._events.append("provider_selector.select_candidates")
        request_id = gaps[0].request_id
        return (
            {
                "request_id": request_id,
                "provider_id": "provider-fast",
                "endpoint_id": "daily",
                "market": Market.CN_A,
                "data_type": "daily_bar",
                "granularity": "daily",
                "source_role": "built_in_public",
                "priority_rank": 1,
                "symbol_id": "600519.SH",
                "fields": ("close",),
            },
            {
                "request_id": request_id,
                "provider_id": "provider-slow",
                "endpoint_id": "daily",
                "market": Market.CN_A,
                "data_type": "daily_bar",
                "granularity": "daily",
                "source_role": "built_in_public",
                "priority_rank": 9,
                "symbol_id": "600519.SH",
                "fields": ("close",),
            },
        )

    def read_capabilities(self, candidates):
        self._events.append("provider_selector.read_capabilities")
        return {"count": len(candidates)}


class _PassThroughCoalescer:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def coalesce(self, gaps, candidates, capabilities):
        del gaps, capabilities
        self._events.append("coalescer.coalesce")
        return tuple(candidates)


class _CandidateBatchPlanner:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def build_batches(self, groups, capabilities):
        del capabilities
        self._events.append("batch_planner.build_batches")
        return tuple(
            _batch_plan(group["request_id"]).model_copy(
                update={
                    "batch_id": f"batch:{group['provider_id']}",
                    "provider_id": group["provider_id"],
                    "endpoint_id": group["endpoint_id"],
                    "priority_rank": group["priority_rank"],
                }
            )
            for group in groups
        )


class _ProviderTrackingFetchEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def fetch(self, batch):
        self._events.append(f"fetch:{batch.provider_id}")
        return FetchResult(
            fetch_id=f"fetch:{batch.provider_id}",
            batch_id=batch.batch_id,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=batch.market,
            symbol_ids=batch.symbol_ids,
            status=FetchStatus.SUCCESS,
            payload={"rows": [{"close": 9.0}]},
            row_count=1,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )
