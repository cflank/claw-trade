from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectRequest,
)
from claw_trade.selection.refresh import SelectionDataRefreshService
from claw_trade.selection.store import SelectionRunStore


def test_selection_refresh_service_starts_background_job_and_dedupes_active_run() -> None:
    store = SelectionRunStore()
    run_calls: list[str] = []
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda plan: run_calls.append(plan.selection_run_id),  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-05-26",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_provider_batch_plan=_provider_batch_plan,
        now_fn=lambda: datetime(2026, 5, 26, 10, tzinfo=UTC),
        run_id_factory=lambda: "sel-refresh-1",
    )

    first = service.request_refresh(
        request=_request(),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-1",
    )
    second = service.request_refresh(
        request=_request(),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-2",
    )

    assert first.status == "started"
    assert first.selection_run_id == "sel-refresh-1"
    assert second.status == "already_running"
    assert second.selection_run_id == "sel-refresh-1"


def _request() -> SelectRequest:
    return SelectRequest(
        request_id="req-refresh",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        user_id="u",
        created_at="2026-05-26T10:00:00+00:00",
    )


def _provider_batch_plan(*, market: SelectionMarket, profile: SelectionProfile, trade_date: str) -> SelectionProviderBatchPlan:
    return SelectionProviderBatchPlan(
        plan_id=f"plan://selection/cn_a/{trade_date}/batch-v1",
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=market,
        profile=profile,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        coverage_groups=("daily",),
        provider_candidates=("cn_a_primary",),
        ttl_policy_ref="ttl://selection/cn_a",
        lineage_root_ref=f"lineage://selection/cn_a/{trade_date}",
    )
