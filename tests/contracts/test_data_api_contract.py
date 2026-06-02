from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.data_gateway.api import DataAPI
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResult,
    DataResultStatus,
    GapReason,
    GapSeverity,
    Market,
)


class _Service:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_data(self, request: DataRequest) -> DataResult:
        if request.request_id == "req-ready":
            return DataResult(
                request_id=request.request_id,
                status=DataResultStatus.READY,
                dataset_refs=("dataset:ok",),
                as_of=datetime(2026, 5, 31, tzinfo=UTC),
            )
        return DataResult(
            request_id=request.request_id,
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap(
                    gap_id=f"gap:{request.request_id}",
                    request_id=request.request_id,
                    severity=GapSeverity.BLOCKER,
                    reason=GapReason.WAREHOUSE_MISSING,
                    market=request.market,
                    symbol_id=request.symbol_id,
                    data_type=request.data_type,
                    granularity=request.granularity,
                    human_readable="warehouse_missing",
                    as_of=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            ),
            as_of=datetime(2026, 5, 31, tzinfo=UTC),
        )

    def get_data_batch(self, requests: list[DataRequest]) -> list[DataResult]:
        self.calls.append("get_data_batch")
        return [self.get_data(item) for item in requests]

    def plan_batch(self, requests: list[DataRequest]) -> list[DataRequest]:
        self.calls.append("plan_batch")
        return list(requests)

    def execute_plan(self, plan: list[DataRequest]) -> list[DataResult]:
        self.calls.append("execute_plan")
        return [self.get_data(item) for item in plan]


def _request_payload(request_id: str) -> dict[str, object]:
    return {
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


def test_get_data_invalid_request_returns_error_with_gap() -> None:
    api = DataAPI(_Service())
    result = api.get_data(
        {
            "request_id": "req-invalid",
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "data_type": "daily_bar",
            "granularity": "daily",
            "fields": (),
            "freshness_policy": "trading_day",
            "consumer": "report",
            "consumer_id": "market_analyst",
            "as_of": datetime(2026, 5, 31, tzinfo=UTC),
        }
    )
    assert result.status == DataResultStatus.ERROR
    assert result.gaps
    assert result.gaps[0].reason == GapReason.INVALID_REQUEST


def test_get_data_batch_preserves_input_order() -> None:
    service = _Service()
    api = DataAPI(service)
    requests = [
        _request_payload("req-ready"),
        {**_request_payload("req-invalid"), "fields": ()},
        _request_payload("req-missing"),
    ]
    results = api.get_data_batch(requests)
    assert [item.request_id for item in results] == ["req-ready", "req-invalid", "req-missing"]
    assert results[0].status == DataResultStatus.READY
    assert results[1].status == DataResultStatus.ERROR
    assert results[2].status == DataResultStatus.MISSING
    assert service.calls == ["plan_batch", "execute_plan"]


def test_get_data_ready_requires_dataset_refs_and_missing_requires_gap() -> None:
    api = DataAPI(_Service())
    ready = api.get_data(_request_payload("req-ready"))
    missing = api.get_data(_request_payload("req-missing"))
    assert ready.status == DataResultStatus.READY
    assert ready.dataset_refs == ("dataset:ok",)
    assert missing.status == DataResultStatus.MISSING
    assert missing.gaps[0].reason == GapReason.WAREHOUSE_MISSING
    assert ready.status not in {DataResultStatus.MISSING, DataResultStatus.ERROR}
    assert missing.status != DataResultStatus.READY
    assert ready.gaps == ()
    assert missing.dataset_refs == ()
    assert missing.gaps[0].market == Market.CN_A
