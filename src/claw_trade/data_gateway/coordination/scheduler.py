from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Sequence

from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.models import GapReason
from claw_trade.data_gateway.needs import DataNeedGap, NeedPlan, NeedPriority, RateLimitEvidence, ScheduledCall

@dataclass(frozen=True)
class DataRunScheduleContext:
    run_id: str
    run_started_at: datetime

    @classmethod
    def for_plan(cls, *, run_id: str, run_started_at: datetime) -> "DataRunScheduleContext":
        started_at = run_started_at if run_started_at.tzinfo else run_started_at.replace(tzinfo=UTC)
        if started_at.tzinfo != UTC:
            started_at = started_at.astimezone(UTC)
        return cls(run_id=run_id, run_started_at=started_at)


class DataRunScheduler:
    def __init__(
        self,
        *,
        rate_limit_policies: dict[str, RateLimitPolicy] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._rate_limit_policies = dict(rate_limit_policies or {})
        self._rate_limiter = rate_limiter

    def schedule(self, planned: Sequence[Any] | NeedPlan, context: DataRunScheduleContext) -> Any:
        if isinstance(planned, NeedPlan):
            return self._schedule_need_plan(planned, context)
        items = tuple(planned)
        if items and all(_looks_like_provider_call(item) for item in items):
            plan = NeedPlan(
                plan_id=context.run_id,
                planned_calls=items,
                created_at=context.run_started_at,
            )
            return self._schedule_need_plan(plan, context)
        if items and all(_looks_like_provider_batch(item) for item in items):
            return self._schedule_provider_batches(items, context)
        return items

    def _schedule_need_plan(self, plan: NeedPlan, context: DataRunScheduleContext) -> NeedPlan:
        scheduled_calls: list[ScheduledCall] = []
        skipped_needs = list(plan.skipped_needs)
        rate_limit_evidence = list(plan.rate_limit_evidence)
        local_timestamps: dict[str, list[datetime]] = {}
        bucket_cursors: dict[str, datetime] = {}

        for call in sorted(plan.planned_calls, key=_call_sort_key):
            bucket = call.rate_limit_bucket
            policy = self._policy_for(call)
            current = bucket_cursors.get(bucket, context.run_started_at)
            timestamps = local_timestamps.setdefault(
                bucket,
                self._initial_timestamps(bucket=bucket, policy=policy, now=context.run_started_at),
            )
            next_available_at = _next_available_at(
                timestamps=timestamps,
                policy=policy,
                now=current,
            )
            deadline_at = _normalize_datetime(call.deadline_at)
            if next_available_at > deadline_at:
                skipped_needs.extend(
                    DataNeedGap(
                        need_id=need_id,
                        reason=GapReason.RATE_LIMITED_BY_TOOL_BUDGET,
                        evidence_refs=_rate_limit_evidence_refs(bucket),
                        human_readable="provider sliding-window slot exceeds task deadline",
                        provider_ids_tried=(call.provider_id,),
                    )
                    for need_id in call.need_ids
                )
                rate_limit_evidence.append(
                    RateLimitEvidence(
                        rate_limit_bucket=bucket,
                        provider_id=call.provider_id,
                        call_id=call.call_id,
                        next_available_at=next_available_at,
                        deadline_at=deadline_at,
                        outcome="rate_limited_by_tool_budget",
                    )
                )
                continue

            timestamps.append(next_available_at)
            bucket_cursors[bucket] = max(current, next_available_at)
            scheduled_calls.append(
                ScheduledCall(
                    call_id=call.call_id,
                    need_ids=call.need_ids,
                    provider_id=call.provider_id,
                    catalog_endpoint_id=call.catalog_endpoint_id,
                    params=call.params,
                    batch_key=call.batch_key,
                    rate_limit_bucket=bucket,
                    earliest_start_at=next_available_at,
                    deadline_at=deadline_at,
                    priority=_priority_for(call),
                )
            )
            rate_limit_evidence.append(
                RateLimitEvidence(
                    rate_limit_bucket=bucket,
                    provider_id=call.provider_id,
                    call_id=call.call_id,
                    reserved_at=next_available_at,
                    next_available_at=next_available_at,
                    deadline_at=deadline_at,
                    outcome="scheduled",
                )
            )

        return plan.model_copy(
            update={
                "scheduled_calls": tuple(scheduled_calls),
                "skipped_needs": tuple(skipped_needs),
                "rate_limit_evidence": tuple(rate_limit_evidence),
                "merge_evidence": plan.merge_evidence,
            }
        )

    def _policy_for(self, call: Any) -> RateLimitPolicy:
        raw = self._rate_limit_policies.get(call.rate_limit_bucket) or getattr(call, "rate_limit_policy", None)
        return _policy_from_raw(raw)

    def _policy_for_batch(self, batch: Any) -> RateLimitPolicy:
        bucket = _read_attr(batch, "rate_limit_key", None)
        raw = self._rate_limit_policies.get(str(bucket)) if bucket else None
        return _policy_from_raw(raw or _read_attr(batch, "rate_limit_policy", None))

    def _schedule_provider_batches(self, batches: tuple[Any, ...], context: DataRunScheduleContext) -> tuple[Any, ...]:
        scheduled: list[Any] = []
        local_timestamps: dict[str, list[datetime]] = {}
        bucket_cursors: dict[str, datetime] = {}

        for batch in batches:
            bucket = str(_read_attr(batch, "rate_limit_key", "") or "")
            deadline_at = _read_attr(batch, "deadline_at", None)
            if not bucket or not isinstance(deadline_at, datetime):
                scheduled.append(batch)
                continue
            policy = self._policy_for_batch(batch)
            current = bucket_cursors.get(bucket, context.run_started_at)
            timestamps = local_timestamps.setdefault(
                bucket,
                self._initial_timestamps(bucket=bucket, policy=policy, now=context.run_started_at),
            )
            next_available_at = _next_available_at(
                timestamps=timestamps,
                policy=policy,
                now=current,
            )
            deadline_at = _normalize_datetime(deadline_at)
            if next_available_at > deadline_at:
                scheduled.append(
                    _BatchScheduleProxy(
                        batch,
                        scheduler_skip_reason=GapReason.RATE_LIMITED_BY_TOOL_BUDGET.value,
                        scheduler_retry_after=next_available_at,
                    )
                )
                continue
            timestamps.append(next_available_at)
            bucket_cursors[bucket] = max(current, next_available_at)
            scheduled.append(_BatchScheduleProxy(batch, scheduler_earliest_start_at=next_available_at))

        return tuple(scheduled)

    def _initial_timestamps(self, *, bucket: str, policy: RateLimitPolicy, now: datetime) -> list[datetime]:
        if self._rate_limiter is None or policy.max_requests is None:
            return []
        return list(self._rate_limiter.active_request_timestamps(bucket, policy, now=now))


def _policy_from_raw(raw: Any | None) -> RateLimitPolicy:
    if isinstance(raw, RateLimitPolicy):
        return raw
    if raw is None:
        return RateLimitPolicy(window_seconds=60, max_requests=None)
    return RateLimitPolicy(
        window_seconds=int(_read_attr(raw, "window_seconds", 60) or 60),
        max_requests=_optional_int(_read_attr(raw, "max_requests", _read_attr(raw, "max_calls", None))),
        safety_margin=int(_read_attr(raw, "safety_margin", 0) or 0),
    )


def _looks_like_provider_call(item: Any) -> bool:
    return all(hasattr(item, attr) for attr in ("call_id", "rate_limit_bucket", "deadline_at", "need_ids"))


def _rate_limit_evidence_refs(bucket: str) -> tuple[str, ...]:
    return (f"rate_limit:{bucket}",)


def _looks_like_provider_batch(item: Any) -> bool:
    return all(hasattr(item, attr) for attr in ("batch_id", "rate_limit_key", "request_ids", "provider_id", "endpoint_id"))


class _BatchScheduleProxy(SimpleNamespace):
    def __init__(self, source: Any, **overrides: Any) -> None:
        super().__init__(**overrides)
        self._source = source

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


def _call_sort_key(call: Any) -> tuple[int, datetime, str]:
    return (_priority_rank(_priority_for(call)), _normalize_datetime(call.deadline_at), str(call.call_id))


def _priority_for(call: Any) -> NeedPriority:
    value = getattr(call, "priority", NeedPriority.NORMAL)
    if isinstance(value, NeedPriority):
        return value
    try:
        return NeedPriority(str(value))
    except ValueError:
        return NeedPriority.NORMAL


def _priority_rank(priority: NeedPriority) -> int:
    return {
        NeedPriority.REQUIRED: 0,
        NeedPriority.NORMAL: 1,
        NeedPriority.OPTIONAL: 2,
        NeedPriority.EXPENSIVE: 3,
    }[priority]


def _next_available_at(*, timestamps: list[datetime], policy: RateLimitPolicy, now: datetime) -> datetime:
    current = _normalize_datetime(now)
    if policy.max_requests is None:
        return current
    effective_limit = max(policy.max_requests - policy.safety_margin, 0)
    if effective_limit <= 0:
        return datetime.max.replace(tzinfo=UTC)
    cutoff = current - timedelta(seconds=policy.window_seconds)
    active = sorted(timestamp for timestamp in timestamps if timestamp > cutoff)
    timestamps[:] = active
    if len(active) < effective_limit:
        return current
    return active[0] + timedelta(seconds=policy.window_seconds)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if value.tzinfo != UTC:
        return value.astimezone(UTC)
    return value
