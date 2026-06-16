from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from claw_trade.data_gateway._selection_batch import (
    _LocalFeatureRowsResult,
    fetch_selection_batch_from_data_gateway,
)
from claw_trade.data_gateway.models import DataResult, DataResultStatus
from claw_trade.data_gateway.needs import DataNeed
from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse
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

    def read_warehouse_batch(self, requests):  # type: ignore[no-untyped-def]
        self.requests = tuple(requests)
        return self._results


class _FakeGateway:
    def __init__(self, api: _FakeDataAPI) -> None:
        self.data_service = api
        self.repository = object()
        self.data_need_calls: list[tuple[DataNeed, ...]] = []

    def data_need_executor(self, _plan: SelectionRunPlan, needs: tuple[DataNeed, ...]) -> tuple[DataResult, ...]:
        self.data_need_calls.append(tuple(needs))
        return ()


@pytest.mark.integration
def test_sel13_fetches_selection_batch_through_current_data_gateway(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(tmp_path / "columnar"))
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
                        "source_ref": "dataset://normalized/CN_A/daily/sel13-sample",
                    },
                ),
                dataset_refs=("dataset:daily_bar:CN_A:sel13",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:sel13",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "统一数据层样本",
                    "industry": "样本行业",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:sel13",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 17.77,
                    "volume": 1000000.0,
                    "amount": 300000000.0,
                },
            ),
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan)

    assert result.data_need_audit.scope == SelectionBatchScope.SELECTION_BATCH
    assert result.data_need_audit.plan_id == plan.data_need_audit_ref
    assert result.attempt_refs == ("attempt:cn_a_primary:daily_bar:sel13",)
    assert result.normalized_refs == ("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:sel13",)
    assert result.warehouse_check_ref == "warehouse-check://selection-columnar/CN_A/CN_A/2026-05-26"
    assert result.columnar_manifest_ref is not None
    assert result.columnar_manifest_sha256 is not None
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
        data_need_audit_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _columnar_feature_result(
    plan: SelectionRunPlan,
    *,
    rows: tuple[dict[str, object], ...],
) -> _LocalFeatureRowsResult:
    normalized_refs = ("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:sel13",)
    attempt_refs = ("attempt:cn_a_primary:daily_bar:sel13",)
    writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    writer.add_daily_rows(
        {
            "ticker": str(row.get("ticker")),
            "date": plan.trade_date,
            "close": float(row.get("close") or 10.0),
            "amount": float(row.get("amount") or 1000000.0),
            "source_ref": str(row.get("source_ref") or normalized_refs[0]),
        }
        for row in rows
    )
    writer.add_feature_rows(rows)
    manifest = writer.commit(provider_attempt_refs=attempt_refs, normalized_refs=normalized_refs)
    return _LocalFeatureRowsResult(
        rows=rows,
        normalized_refs=normalized_refs,
        attempt_refs=attempt_refs,
        data_gaps=(),
        columnar_manifest_ref=manifest.manifest_ref,
        columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(manifest.manifest_ref),
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
