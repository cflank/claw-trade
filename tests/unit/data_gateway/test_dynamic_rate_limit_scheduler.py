from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.models import GapReason
from claw_trade.data_gateway.needs import NeedPlan, ProviderCallSpec


def _call(
    call_id: str,
    *,
    provider_id: str = "official_api_coinglass",
    bucket: str = "ratelimit:coinglass",
    need_id: str | None = None,
    deadline_at: datetime,
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
        now,
        now + timedelta(seconds=60),
    ]
    assert scheduled.skipped_needs == ()
    assert [item.outcome for item in scheduled.rate_limit_evidence] == ["scheduled", "scheduled", "scheduled"]


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
