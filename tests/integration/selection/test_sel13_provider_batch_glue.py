from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from claw_trade.data_gateway.models import DataResult, DataResultStatus
from claw_trade.data_gateway.selection_batch import fetch_selection_batch_from_data_gateway
from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)


class _FakeDataAPI:
    def __init__(self, results: tuple[DataResult, ...]) -> None:
        self.requests = ()
        self._results = results

    def get_data_batch(self, requests):  # type: ignore[no-untyped-def]
        self.requests = tuple(requests)
        return self._results


class _FakeGateway:
    def __init__(self, api: _FakeDataAPI) -> None:
        self.data_api = api
        self.provider_candidates = ("cn_a_primary",)


@pytest.mark.integration
def test_sel13_fetches_selection_batch_through_current_data_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _selection_run_plan()
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.READY,
                rows=(
                    {
                        "ticker": "600204.SH",
                        "company_name": "统一数据层样本",
                        "industry": "样本行业",
                        "history": _history_rows(),
                        "private_placement_event_date": "none",
                        "private_placement_days_since": 9999.0,
                        "source_ref": "normalized://mongo/normalized_datasets/sel13-sample",
                    },
                ),
                dataset_refs=("dataset:daily_bar:CN_A:sel13",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:sel13",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )

    result = fetch_selection_batch_from_data_gateway(plan)

    assert result.provider_batch_plan.scope == SelectionBatchScope.SELECTION_BATCH
    assert result.provider_batch_plan.plan_id == plan.provider_batch_plan_ref
    assert result.attempt_refs == ("attempt:cn_a_primary:daily_bar:sel13",)
    assert result.normalized_refs == ("normalized://mongo/normalized_datasets/dataset:daily_bar:CN_A:sel13",)
    assert result.warehouse_check_ref == "warehouse-check://selection/sel13-current-glue/2026-05-26/data-api"
    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "600204.SH"
    assert result.data_gaps == ()
    assert [request.consumer for request in fake_api.requests] == ["select"]
    assert [request.data_type for request in fake_api.requests] == ["daily_bar"]


def _selection_run_plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel13-current-glue",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _history_rows() -> tuple[dict[str, object], ...]:
    start = datetime(2025, 5, 27)
    rows: list[dict[str, object]] = []
    for index in range(260):
        close = 10.0 + index * 0.03
        rows.append(
            {
                "date": (start + timedelta(days=index)).date().isoformat(),
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 1_000_000.0 + index,
                "amount": close * (1_000_000.0 + index),
            }
        )
    return tuple(rows)
