from __future__ import annotations

import pytest

from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionDataNeedAudit,
    SelectionMarket,
    SelectionProfile,
    SelectionTriggerSource,
)
from claw_trade.selection.scheduler import (
    SelectionSchedulingError,
    SelectionScheduleContext,
    schedule_selection_job,
)


def _resolve_trade_date(_: str | None) -> str:
    return "2026-06-16"


def _build_data_need_audit(
    *,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str,
) -> SelectionDataNeedAudit:
    return SelectionDataNeedAudit(
        plan_id="plan://unit-scheduler",
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=market,
        profile=profile,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        coverage_groups=("selection",),
        ttl_policy_ref="ttl://unit",
        lineage_root_ref="lineage://unit",
    )


def test_schedule_selection_job_allows_cn_a_and_crypto() -> None:
    call_count = 0

    def load_approved_strategy_config_ref(market: SelectionMarket, profile: SelectionProfile) -> str | None:
        nonlocal call_count
        call_count += 1
        assert market in {SelectionMarket.CN_A, SelectionMarket.CRYPTO}
        return f"config://{market.value.lower()}"

    for market, profile in (
        (SelectionMarket.CN_A, SelectionProfile.CN_A),
        (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO),
    ):
        plan = schedule_selection_job(
            context=SelectionScheduleContext(
                market=market,
                profile=profile,
                trade_date="2026-06-16",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            resolve_closed_trade_date=_resolve_trade_date,
            has_active_job=lambda *_args: False,
            load_approved_strategy_config_ref=load_approved_strategy_config_ref,
            build_data_need_audit=_build_data_need_audit,
            run_id_factory=lambda: f"sel-{market.value.lower()}",
        )
        assert plan.market == market
        assert plan.profile == profile
        assert plan.selection_run_id == f"sel-{market.value.lower()}"
        assert plan.approved_strategy_config_ref == f"config://{market.value.lower()}"

    assert call_count == 2


def test_schedule_selection_job_rejects_unsupported_market_and_profile() -> None:
    with pytest.raises(SelectionSchedulingError, match="market_strategy_unapproved"):
        schedule_selection_job(
            context=SelectionScheduleContext(
                market=SelectionMarket.HK,
                profile=SelectionProfile.HK,
                trade_date="2026-06-16",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            resolve_closed_trade_date=_resolve_trade_date,
            has_active_job=lambda *_args: False,
            load_approved_strategy_config_ref=lambda *_args: "config://ignored",
            build_data_need_audit=_build_data_need_audit,
        )
