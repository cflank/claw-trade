from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from claw_trade.config.selection_scheduler_settings import SelectionSchedulerSettings
from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
    SelectionTriggerSource,
)


class SelectionSchedulingError(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class SelectionScheduleContext:
    market: SelectionMarket
    profile: SelectionProfile
    trade_date: str | None
    trigger_source: SelectionTriggerSource
    supersedes_run_id: str | None = None


def resolve_default_schedule_time(
    trade_date: str,
    settings: SelectionSchedulerSettings,
) -> datetime:
    resolved_date = date.fromisoformat(trade_date)
    timezone = ZoneInfo(settings.timezone_name)
    return datetime(
        year=resolved_date.year,
        month=resolved_date.month,
        day=resolved_date.day,
        hour=settings.schedule_time_local.hour,
        minute=settings.schedule_time_local.minute,
        tzinfo=timezone,
    )


def schedule_selection_job(
    *,
    context: SelectionScheduleContext,
    resolve_closed_trade_date: Callable[[str | None], str],
    has_active_job: Callable[[SelectionMarket, SelectionProfile, str], bool],
    load_approved_strategy_config_ref: Callable[[SelectionMarket, SelectionProfile], str | None],
    build_provider_batch_plan: Callable[..., SelectionProviderBatchPlan],
    run_id_factory: Callable[[], str] | None = None,
) -> SelectionRunPlan:
    if context.market != SelectionMarket.CN_A or context.profile != SelectionProfile.CN_A:
        raise SelectionSchedulingError("market_strategy_unapproved", "SEL-03 仅允许 CN_A")

    if context.trigger_source not in {
        SelectionTriggerSource.SCHEDULED,
        SelectionTriggerSource.SELECT_COMMAND_REFRESH,
        SelectionTriggerSource.MANUAL_BACKFILL,
        SelectionTriggerSource.MANUAL_RERUN,
    }:
        raise SelectionSchedulingError("trigger_source_unapproved", "触发来源未批准")
    if context.supersedes_run_id is not None and context.trigger_source != SelectionTriggerSource.MANUAL_RERUN:
        raise SelectionSchedulingError("trigger_source_unapproved", "supersedes_run_id 仅允许 manual_rerun")

    resolved_trade_date = resolve_closed_trade_date(context.trade_date)
    if has_active_job(context.market, context.profile, resolved_trade_date):
        raise SelectionSchedulingError("already_running", "同一交易日已有 active selection job")

    approved_strategy_config_ref = str(load_approved_strategy_config_ref(context.market, context.profile)).strip()
    if not approved_strategy_config_ref:
        raise SelectionSchedulingError("strategy_config_unapproved", "缺少 approved strategy config")

    provider_batch_plan = build_provider_batch_plan(
        market=context.market,
        profile=context.profile,
        trade_date=resolved_trade_date,
    )
    if not isinstance(provider_batch_plan, SelectionProviderBatchPlan):
        raise SelectionSchedulingError("provider_batch_plan_missing", "provider batch plan 缺失")
    if provider_batch_plan.scope != SelectionBatchScope.SELECTION_BATCH:
        raise SelectionSchedulingError("provider_batch_plan_missing", "provider batch plan scope 非 selection_batch")
    if provider_batch_plan.market != context.market or provider_batch_plan.profile != context.profile:
        raise SelectionSchedulingError("provider_batch_plan_missing", "provider batch plan 市场或 profile 不匹配")
    if provider_batch_plan.trade_date != resolved_trade_date:
        raise SelectionSchedulingError("provider_batch_plan_missing", "provider batch plan trade_date 不匹配")
    if not provider_batch_plan.provider_candidates:
        raise SelectionSchedulingError("provider_batch_plan_missing", "provider candidates 不能为空")

    create_run_id = run_id_factory or _default_run_id
    return SelectionRunPlan(
        selection_run_id=create_run_id(),
        market=context.market,
        profile=context.profile,
        trade_date=resolved_trade_date,
        lookback_trading_days=provider_batch_plan.lookback_trading_days,
        universe_scope=provider_batch_plan.universe_scope,
        provider_batch_plan_ref=provider_batch_plan.plan_id,
        approved_strategy_config_ref=approved_strategy_config_ref,
        trigger_source=context.trigger_source,
        supersedes_run_id=context.supersedes_run_id,
    )


def normalize_now_to_shanghai_date(now: datetime) -> str:
    shanghai_now = _to_utc(now).astimezone(ZoneInfo("Asia/Shanghai"))
    return shanghai_now.date().isoformat()


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _default_run_id() -> str:
    return f"sel-run-{uuid4().hex}"
