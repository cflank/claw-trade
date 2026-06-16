from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.models import GapReason
from claw_trade.data_gateway.needs import AttemptState, ExecutionGroup, ExecutionGroupKind, NeedPlan, NeedPriority, ProviderCallSpec


def _call(
    call_id: str,
    *,
    provider_id: str = "official_api_coinglass",
    bucket: str = "ratelimit:coinglass",
    need_id: str | None = None,
    deadline_at: datetime,
    priority: NeedPriority = NeedPriority.NORMAL,
) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_id=call_id,
        provider_id=provider_id,
        catalog_endpoint_id="coinglass.futures_funding_rate",
        official_path_or_api_name="/api/futures/funding-rate/oi-weight-history",
        params={"symbol": "BTC"},
        auth_scope="coinglass_header",
        rate_limit_bucket=bucket,
        http_visibility="managed_http",
        parser_status="normalized",
        batch_key=f"provider={provider_id}|bucket={bucket}|call={call_id}",
        official_doc_ref="https://docs.coinglass.com/reference",
        deadline_at=deadline_at,
        need_ids=(need_id or f"need-{call_id}",),
        priority=priority,
    )


def test_scheduler_schedules_calls_until_sliding_window_budget_is_exhausted() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("1", deadline_at=now + timedelta(seconds=120)),
            _call("2", deadline_at=now + timedelta(seconds=120)),
            _call("3", deadline_at=now + timedelta(seconds=120)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=2)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["1", "2", "3"]
    assert [call.earliest_start_at for call in scheduled.scheduled_calls] == [
        now,
        now + timedelta(seconds=30),
        now + timedelta(seconds=60),
    ]
    assert scheduled.skipped_needs == ()
    assert [item.outcome for item in scheduled.rate_limit_evidence] == ["scheduled", "scheduled", "scheduled"]


def test_scheduler_preserves_planner_candidate_order() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-ordered",
        planned_calls=(
            _call("z-normalized", deadline_at=now + timedelta(seconds=120)),
            _call("a-risky-sdk", deadline_at=now + timedelta(seconds=120)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=10)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-ordered", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["z-normalized", "a-risky-sdk"]
    assert [item.call_id for item in scheduled.rate_limit_evidence] == ["z-normalized", "a-risky-sdk"]


def test_scheduler_only_reserves_current_fallback_candidate_until_attempt_fails() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    first = _call("first", deadline_at=now + timedelta(seconds=120)).model_copy(
        update={
            "execution_group_id": "group-open-interest",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "fallback_order": 0,
        }
    )
    fallback = _call("fallback", deadline_at=now + timedelta(seconds=120)).model_copy(
        update={
            "execution_group_id": "group-open-interest",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "fallback_order": 1,
        }
    )
    plan = NeedPlan(
        plan_id="plan-chain",
        execution_groups=(
            ExecutionGroup(
                group_id="group-open-interest",
                kind=ExecutionGroupKind.FALLBACK_CHAIN,
                business_api_id="crypto.open_interest",
                planned_call_ids=("first", "fallback"),
                initial_call_ids=("first",),
                deferred_call_ids=("fallback",),
            ),
        ),
        planned_calls=(first, fallback),
        initial_scheduled_calls=(first,),
        deferred_calls=(fallback,),
        created_at=now,
    )
    scheduler = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=10)}
    )

    scheduled = scheduler.schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-chain", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["first"]
    assert [call.call_id for call in scheduled.initial_scheduled_calls] == ["first"]
    assert [item.call_id for item in scheduled.rate_limit_evidence] == ["first"]
    assert [call.call_id for call in scheduled.deferred_calls] == ["fallback"]

    next_plan = scheduler.schedule_next(
        scheduled,
        DataRunScheduleContext.for_plan(run_id="plan-chain", run_started_at=now),
        AttemptState(
            plan_id="plan-chain",
            execution_group_id="group-open-interest",
            call_id="first",
            attempt_outcome="provider_empty",
            contract_satisfied=False,
        ),
    )

    assert [call.call_id for call in next_plan.scheduled_calls] == ["fallback"]
    assert [item.call_id for item in next_plan.rate_limit_evidence] == ["first", "fallback"]


def test_scheduler_fallback_exhausts_same_source_before_next_source_group() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    coinglass_primary = _call("coinglass-primary", deadline_at=now + timedelta(seconds=120)).model_copy(
        update={
            "execution_group_id": "group-funding",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "source_group_id": "coinglass",
            "fallback_order": 0,
        }
    )
    coinglass_raw = _call("coinglass-raw", deadline_at=now + timedelta(seconds=120)).model_copy(
        update={
            "execution_group_id": "group-funding",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "source_group_id": "coinglass",
            "fallback_order": 1,
        }
    )
    binance = _call(
        "binance",
        provider_id="crypto_binance_spot_market",
        bucket="ratelimit:binance",
        deadline_at=now + timedelta(seconds=120),
    ).model_copy(
        update={
            "execution_group_id": "group-funding",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "source_group_id": "binance",
            "fallback_order": 2,
        }
    )
    plan = NeedPlan(
        plan_id="plan-source-fallback",
        execution_groups=(
            ExecutionGroup(
                group_id="group-funding",
                kind=ExecutionGroupKind.FALLBACK_CHAIN,
                business_api_id="crypto.funding_rate",
                planned_call_ids=("coinglass-primary", "coinglass-raw", "binance"),
                initial_call_ids=("coinglass-primary",),
                deferred_call_ids=("coinglass-raw", "binance"),
            ),
        ),
        planned_calls=(coinglass_primary, coinglass_raw, binance),
        initial_scheduled_calls=(coinglass_primary,),
        deferred_calls=(coinglass_raw, binance),
        created_at=now,
    )
    scheduler = DataRunScheduler(
        rate_limit_policies={
            "ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=10),
            "ratelimit:binance": RateLimitPolicy(window_seconds=60, max_requests=10),
        }
    )

    scheduled = scheduler.schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-source-fallback", run_started_at=now))
    next_plan = scheduler.schedule_next(
        scheduled,
        DataRunScheduleContext.for_plan(run_id="plan-source-fallback", run_started_at=now),
        AttemptState(
            plan_id="plan-source-fallback",
            execution_group_id="group-funding",
            call_id="coinglass-primary",
            attempt_outcome="provider_error",
            contract_satisfied=False,
        ),
    )

    assert [call.call_id for call in next_plan.scheduled_calls] == ["coinglass-raw"]

    final_plan = scheduler.schedule_next(
        next_plan,
        DataRunScheduleContext.for_plan(run_id="plan-source-fallback", run_started_at=now),
        AttemptState(
            plan_id="plan-source-fallback",
            execution_group_id="group-funding",
            call_id="coinglass-raw",
            attempt_outcome="provider_error",
            contract_satisfied=False,
        ),
    )

    assert [call.call_id for call in final_plan.scheduled_calls] == ["binance"]


def test_scheduler_skips_late_fallback_candidates_and_schedules_available_source() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    coinglass_policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    limiter = RateLimiter(now_fn=lambda: now)
    assert limiter.reserve("ratelimit:coinglass", coinglass_policy).allowed is True
    coinglass_primary = _call("coinglass-primary", deadline_at=now + timedelta(seconds=65)).model_copy(
        update={
            "execution_group_id": "group-funding",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "source_group_id": "coinglass",
            "fallback_order": 0,
        }
    )
    coinglass_raw = _call("coinglass-raw", deadline_at=now + timedelta(seconds=65)).model_copy(
        update={
            "execution_group_id": "group-funding",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "source_group_id": "coinglass",
            "fallback_order": 1,
        }
    )
    binance = _call(
        "binance",
        provider_id="crypto_binance_spot_market",
        bucket="ratelimit:binance",
        deadline_at=now + timedelta(seconds=65),
    ).model_copy(
        update={
            "execution_group_id": "group-funding",
            "execution_group_kind": ExecutionGroupKind.FALLBACK_CHAIN,
            "source_group_id": "binance",
            "fallback_order": 2,
        }
    )
    plan = NeedPlan(
        plan_id="plan-late-paid-fallback",
        execution_groups=(
            ExecutionGroup(
                group_id="group-funding",
                kind=ExecutionGroupKind.FALLBACK_CHAIN,
                business_api_id="crypto.funding_rate",
                planned_call_ids=("coinglass-primary", "coinglass-raw", "binance"),
                initial_call_ids=("coinglass-primary",),
                deferred_call_ids=("coinglass-raw", "binance"),
            ),
        ),
        planned_calls=(coinglass_primary, coinglass_raw, binance),
        initial_scheduled_calls=(coinglass_primary,),
        deferred_calls=(coinglass_raw, binance),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={
            "ratelimit:coinglass": coinglass_policy,
            "ratelimit:binance": RateLimitPolicy(window_seconds=60, max_requests=10),
        },
        rate_limiter=limiter,
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-late-paid-fallback", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["binance"]
    assert [gap.need_id for gap in scheduled.skipped_needs] == ["need-coinglass-primary", "need-coinglass-raw"]
    assert [item.outcome for item in scheduled.rate_limit_evidence] == [
        "rate_limited_by_tool_budget",
        "rate_limited_by_tool_budget",
        "scheduled",
    ]


def test_scheduler_orders_same_priority_by_deadline_before_planner_order() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-same-priority-deadlines",
        planned_calls=(
            _call("planner-first-later-deadline", deadline_at=now + timedelta(seconds=120)),
            _call("planner-second-earlier-deadline", deadline_at=now + timedelta(seconds=30)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=10)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-same-priority-deadlines", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == [
        "planner-second-earlier-deadline",
        "planner-first-later-deadline",
    ]


def test_scheduler_uses_earlier_deadline_first_so_later_call_can_wait() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-deadline-budget",
        planned_calls=(
            _call("later-deadline", deadline_at=now + timedelta(seconds=120), need_id="need-later"),
            _call("earlier-deadline", deadline_at=now + timedelta(seconds=30), need_id="need-earlier"),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=1)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-deadline-budget", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["earlier-deadline", "later-deadline"]
    assert scheduled.skipped_needs == ()


def test_scheduler_prioritizes_required_calls_when_budget_is_tight() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-priority",
        planned_calls=(
            _call("optional", deadline_at=now + timedelta(seconds=30), priority=NeedPriority.OPTIONAL, need_id="need-optional"),
            _call("required", deadline_at=now + timedelta(seconds=30), priority=NeedPriority.REQUIRED, need_id="need-required"),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=1)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-priority", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["required"]
    assert [gap.need_id for gap in scheduled.skipped_needs] == ["need-optional"]


def test_scheduler_advances_future_slots_for_multiple_waiting_calls() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("1", deadline_at=now + timedelta(seconds=180)),
            _call("2", deadline_at=now + timedelta(seconds=180)),
            _call("3", deadline_at=now + timedelta(seconds=180)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=1)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert [call.earliest_start_at for call in scheduled.scheduled_calls] == [
        now,
        now + timedelta(seconds=60),
        now + timedelta(seconds=120),
    ]


def test_scheduler_smooths_ten_per_minute_bucket_instead_of_bursting() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-smooth",
        planned_calls=tuple(_call(str(index), deadline_at=now + timedelta(seconds=120)) for index in range(1, 4)),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=10)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-smooth", run_started_at=now))

    assert [call.earliest_start_at for call in scheduled.scheduled_calls] == [
        now,
        now + timedelta(seconds=6),
        now + timedelta(seconds=12),
    ]


def test_scheduler_accepts_max_calls_policy_snapshot_alias() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("1", deadline_at=now + timedelta(seconds=120)),
            _call("2", deadline_at=now + timedelta(seconds=120)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={
            "ratelimit:coinglass": SimpleNamespace(window_seconds=60, max_calls=1, safety_margin=0)
        }
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert [call.earliest_start_at for call in scheduled.scheduled_calls] == [
        now,
        now + timedelta(seconds=60),
    ]


def test_scheduler_accounts_for_existing_sliding_window_usage() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    limiter = RateLimiter(now_fn=lambda: now)
    assert limiter.reserve("ratelimit:coinglass", policy).allowed is True
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("1", deadline_at=now + timedelta(seconds=120)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": policy},
        rate_limiter=limiter,
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert scheduled.scheduled_calls[0].earliest_start_at == now + timedelta(seconds=60)


def test_scheduler_shares_future_bucket_reservations_across_separate_plans() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    policy = RateLimitPolicy(window_seconds=60, max_requests=2)
    limiter = RateLimiter(now_fn=lambda: now)
    scheduler = DataRunScheduler(
        rate_limit_policies={"ratelimit:any-paid-source": policy},
        rate_limiter=limiter,
    )

    scheduled_times = []
    for index in range(3):
        plan = NeedPlan(
            plan_id=f"plan-{index}",
            planned_calls=(
                _call(
                    str(index),
                    bucket="ratelimit:any-paid-source",
                    deadline_at=now + timedelta(seconds=180),
                ),
            ),
            created_at=now,
        )
        scheduled = scheduler.schedule(plan, DataRunScheduleContext.for_plan(run_id=f"plan-{index}", run_started_at=now))
        scheduled_times.append(scheduled.scheduled_calls[0].earliest_start_at)
        assert scheduled.scheduled_calls[0].rate_limit_reserved_at == scheduled.scheduled_calls[0].earliest_start_at

    assert scheduled_times == [
        now,
        now + timedelta(seconds=30),
        now + timedelta(seconds=60),
    ]


def test_scheduler_skips_need_when_next_slot_exceeds_deadline() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("1", deadline_at=now + timedelta(seconds=30)),
            _call("2", deadline_at=now + timedelta(seconds=30), need_id="need-late"),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=1)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert [call.call_id for call in scheduled.scheduled_calls] == ["1"]
    assert len(scheduled.skipped_needs) == 1
    assert scheduled.skipped_needs[0].need_id == "need-late"
    assert scheduled.skipped_needs[0].reason == GapReason.RATE_LIMITED_BY_TOOL_BUDGET
    assert scheduled.skipped_needs[0].evidence_refs == ("rate_limit:ratelimit:coinglass",)
    assert scheduled.rate_limit_evidence[-1].outcome == "rate_limited_by_tool_budget"
    assert scheduled.rate_limit_evidence[-1].next_available_at == now + timedelta(seconds=60)


def test_scheduler_skips_when_safety_margin_consumes_budget() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("1", deadline_at=now + timedelta(seconds=120), need_id="need-no-budget"),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=1, safety_margin=1)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert scheduled.scheduled_calls == ()
    assert scheduled.skipped_needs[0].need_id == "need-no-budget"
    assert scheduled.skipped_needs[0].reason == GapReason.RATE_LIMITED_BY_TOOL_BUDGET
    assert scheduled.skipped_needs[0].evidence_refs == ("rate_limit:ratelimit:coinglass",)


def test_scheduler_shares_bucket_between_structured_and_official_provider_calls() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    plan = NeedPlan(
        plan_id="plan-1",
        planned_calls=(
            _call("structured", provider_id="crypto_coinglass_derivatives", deadline_at=now + timedelta(seconds=120)),
            _call("official", provider_id="official_api_coinglass", deadline_at=now + timedelta(seconds=120)),
        ),
        created_at=now,
    )

    scheduled = DataRunScheduler(
        rate_limit_policies={"ratelimit:coinglass": RateLimitPolicy(window_seconds=60, max_requests=1)}
    ).schedule(plan, DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=now))

    assert [call.earliest_start_at for call in scheduled.scheduled_calls] == [
        now,
        now + timedelta(seconds=60),
    ]
    assert {item.rate_limit_bucket for item in scheduled.rate_limit_evidence} == {"ratelimit:coinglass"}
