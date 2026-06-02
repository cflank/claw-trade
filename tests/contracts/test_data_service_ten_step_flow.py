from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResultStatus,
    GapReason,
    GapSeverity,
    GateDecision,
    IngestResult,
    Market,
    ProviderBatchPlan,
    RequiredLevel,
    WarehouseResult,
)


def _request() -> DataRequest:
    return DataRequest.model_validate(
        {
            "request_id": "req-contract-flow",
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


def _gap() -> DataGap:
    return DataGap(
        gap_id="gap:flow",
        request_id="req-contract-flow",
        severity=GapSeverity.BLOCKER,
        reason=GapReason.WAREHOUSE_MISSING,
        market=Market.CN_A,
        symbol_id="600519.SH",
        data_type="daily_bar",
        granularity="daily",
        human_readable="warehouse_missing",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )


def _batch() -> ProviderBatchPlan:
    return ProviderBatchPlan(
        batch_id="batch:flow",
        plan_id="plan:flow",
        provider_id="provider-1",
        endpoint_id="daily",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        request_ids=("req-contract-flow",),
        symbol_ids=("600519.SH",),
        fields_union=("close",),
        params_redacted={},
        priority_rank=1,
        required_level=RequiredLevel.REQUIRED,
        cache_key="cache:flow",
        rate_limit_key="rate:flow",
        single_flight_key="flight:flow",
        provider_config_version="cfg:flow",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )


def test_data_service_flow_order_gap_to_recheck() -> None:
    steps: list[str] = []

    class _Warehouse:
        def check(self, checks, coverage):
            steps.append("warehouse")
            return WarehouseResult(satisfied=False, gaps=(_gap(),))

        def recheck(self, checks, coverage):
            steps.append("recheck")
            return WarehouseResult(satisfied=True, dataset_refs=("dataset:after",))

    class _Selector:
        def select_candidates(self, gaps, plan):
            steps.append("candidates")
            return ({"provider_id": "provider-1"},)

        def read_capabilities(self, candidates):
            steps.append("capabilities")
            assert candidates
            return {"count": len(candidates)}

    class _Coalescer:
        def coalesce(self, gaps, candidates, capabilities):
            steps.append("coalesce")
            assert capabilities["count"] == 1
            return ({"id": "group"},)

    class _BatchPlanner:
        def build_batches(self, groups, capabilities):
            steps.append("batch")
            assert capabilities["count"] == 1
            return (_batch(),)

    class _Gate:
        def enter(self, batch):
            steps.append("gate")
            return GateDecision(kind="owner", owner_token="owner-1")

        def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
            steps.append("publish")

    class _Fetch:
        def fetch(self, batch):
            steps.append("fetch")
            return type("R", (), {"status": "success"})()

    class _Ingest:
        def ingest(self, result, batch):
            steps.append("ingest")
            return IngestResult(
                ingest_id="ing:1",
                batch_id=batch.batch_id,
                status="ingested",
                dataset_refs=("dataset:ingest",),
                raw_refs=("raw:1",),
                attempt_refs=("attempt:1",),
                remote_success=True,
                created_at=datetime(2026, 5, 31, tzinfo=UTC),
            )

        def record_gate_result(self, batch, gate):
            steps.append("record_gate")
            return IngestResult(
                ingest_id="ing:gate",
                batch_id=batch.batch_id,
                status="non_remote_recorded",
                attempt_refs=("attempt:gate",),
                remote_success=False,
                created_at=datetime(2026, 5, 31, tzinfo=UTC),
            )

    class _Planner(QueryPlanner):
        def validate_and_normalize(self, request):
            steps.append("plan")
            return super().validate_and_normalize(request)

    service = DataService(
        query_planner=_Planner(),
        warehouse=_Warehouse(),
        provider_selector=_Selector(),
        coalescer=_Coalescer(),
        batch_planner=_BatchPlanner(),
        execution_gate=_Gate(),
        fetch_engine=_Fetch(),
        ingest=_Ingest(),
    )
    result = service.get_data(_request())
    assert result.status == DataResultStatus.READY
    assert steps == [
        "plan",
        "warehouse",
        "candidates",
        "capabilities",
        "coalesce",
        "batch",
        "gate",
        "fetch",
        "ingest",
        "publish",
        "recheck",
    ]


def test_data_service_non_remote_gate_records_attempt_without_fetch() -> None:
    steps: list[str] = []

    class _Warehouse:
        def check(self, checks, coverage):
            return WarehouseResult(satisfied=False, gaps=(_gap(),))

        def recheck(self, checks, coverage):
            return WarehouseResult(satisfied=False, gaps=(_gap(),))

    class _Selector:
        def select_candidates(self, gaps, plan):
            return ({"provider_id": "provider-1"},)

        def read_capabilities(self, candidates):
            return {"count": len(candidates)}

    class _Coalescer:
        def coalesce(self, gaps, candidates, capabilities):
            assert capabilities["count"] == 1
            return ({"id": "group"},)

    class _BatchPlanner:
        def build_batches(self, groups, capabilities):
            assert capabilities["count"] == 1
            return (_batch(),)

    class _Gate:
        def enter(self, batch):
            return GateDecision(kind="cache_hit")

        def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
            steps.append("publish")

    class _Fetch:
        def fetch(self, batch):
            steps.append("fetch")
            raise AssertionError("cache_hit 分支不应执行 fetch")

    class _Ingest:
        def ingest(self, result, batch):
            steps.append("ingest")
            raise AssertionError("cache_hit 分支不应执行 ingest")

        def record_gate_result(self, batch, gate):
            steps.append("record_gate")
            return IngestResult(
                ingest_id="ing:gate",
                batch_id=batch.batch_id,
                status="non_remote_recorded",
                attempt_refs=("attempt:gate",),
                gaps=(_gap(),),
                remote_success=False,
                created_at=datetime(2026, 5, 31, tzinfo=UTC),
            )

    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=_Warehouse(),
        provider_selector=_Selector(),
        coalescer=_Coalescer(),
        batch_planner=_BatchPlanner(),
        execution_gate=_Gate(),
        fetch_engine=_Fetch(),
        ingest=_Ingest(),
    )
    result = service.get_data(_request())
    assert result.status == DataResultStatus.MISSING
    assert steps == ["record_gate"]


def test_data_service_code_boundary_no_direct_http_or_mongo_calls() -> None:
    service_file = Path("src/claw_trade/data_gateway/coordination/service.py")
    content = service_file.read_text(encoding="utf-8")
    assert "import requests" not in content
    assert "from requests" not in content
    assert "import pymongo" not in content
    assert "from pymongo" not in content


def test_data_service_batch_flow_runs_single_unified_pipeline() -> None:
    steps: list[str] = []

    class _Warehouse:
        def check(self, checks, coverage):
            steps.append("warehouse")
            return WarehouseResult(satisfied=False, gaps=(_gap(),))

        def recheck(self, checks, coverage):
            steps.append("recheck")
            return WarehouseResult(satisfied=True, dataset_refs=("dataset:after",))

    class _Selector:
        def select_candidates(self, gaps, plan):
            steps.append("candidates")
            return ({"provider_id": "provider-1"},)

        def read_capabilities(self, candidates):
            steps.append("capabilities")
            return {"count": len(candidates)}

    class _Coalescer:
        def coalesce(self, gaps, candidates, capabilities):
            steps.append("coalesce")
            assert capabilities["count"] == 1
            return ({"id": "group"},)

    class _BatchPlanner:
        def build_batches(self, groups, capabilities):
            steps.append("batch")
            assert capabilities["count"] == 1
            return (_batch(),)

    class _Gate:
        def enter(self, batch):
            steps.append("gate")
            return GateDecision(kind="owner", owner_token="owner-1")

        def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
            steps.append("publish")

    class _Fetch:
        def fetch(self, batch):
            steps.append("fetch")
            return type("R", (), {"status": "success"})()

    class _Ingest:
        def ingest(self, result, batch):
            steps.append("ingest")
            return IngestResult(
                ingest_id="ing:1",
                batch_id=batch.batch_id,
                status="ingested",
                dataset_refs=("dataset:ingest",),
                raw_refs=("raw:1",),
                attempt_refs=("attempt:1",),
                remote_success=True,
                created_at=datetime(2026, 5, 31, tzinfo=UTC),
            )

        def record_gate_result(self, batch, gate):
            steps.append("record_gate")
            return IngestResult(
                ingest_id="ing:gate",
                batch_id=batch.batch_id,
                status="non_remote_recorded",
                attempt_refs=("attempt:gate",),
                remote_success=False,
                created_at=datetime(2026, 5, 31, tzinfo=UTC),
            )

    class _Planner(QueryPlanner):
        def validate_and_normalize(self, request):
            steps.append("plan")
            return super().validate_and_normalize(request)

        def validate_and_normalize_many(self, requests):
            steps.append("plan_many")
            return super().validate_and_normalize_many(requests)

    req1 = _request()
    req2 = req1.model_copy(update={"request_id": "req-contract-flow-2"})
    service = DataService(
        query_planner=_Planner(),
        warehouse=_Warehouse(),
        provider_selector=_Selector(),
        coalescer=_Coalescer(),
        batch_planner=_BatchPlanner(),
        execution_gate=_Gate(),
        fetch_engine=_Fetch(),
        ingest=_Ingest(),
    )
    result = service.get_data_batch((req1, req2))
    assert [item.request_id for item in result] == ["req-contract-flow", "req-contract-flow-2"]
    assert all(item.status == DataResultStatus.READY for item in result)
    assert steps == [
        "plan_many",
        "warehouse",
        "candidates",
        "capabilities",
        "coalesce",
        "batch",
        "gate",
        "fetch",
        "ingest",
        "publish",
        "recheck",
    ]
