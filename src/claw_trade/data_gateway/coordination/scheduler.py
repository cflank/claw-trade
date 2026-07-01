from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.models import GapReason
from claw_trade.data_gateway.needs import (
    AttemptState,
    DataNeedGap,
    ExecutionGroupKind,
    NeedPlan,
    NeedPriority,
    ProviderCallSpec,
    RateLimitEvidence,
    ScheduledCall,
)

_MIN_PROVIDER_EXECUTION_WINDOW_SECONDS = 12

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

    def update_rate_limit_policies(self, policies: dict[str, RateLimitPolicy]) -> None:
        self._rate_limit_policies.update(policies)

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
        return items

    def schedule_next(self, plan: NeedPlan, context: DataRunScheduleContext, attempt_state: AttemptState) -> NeedPlan:
        return self._schedule_need_plan(plan, context, attempt_state=attempt_state)

    def _schedule_need_plan(self, plan: NeedPlan, context: DataRunScheduleContext, attempt_state: AttemptState | None = None) -> NeedPlan:
        scheduled_calls: list[ScheduledCall] = []
        skipped_needs = list(plan.skipped_needs)
        rate_limit_evidence = list(plan.rate_limit_evidence)
        local_timestamps: dict[str, list[datetime]] = {}
        bucket_cursors: dict[str, datetime] = {}
        scheduled_fallback_groups: set[str] = set()

        calls_to_schedule = _calls_to_schedule(plan=plan, attempt_state=attempt_state)
        ordered_calls = tuple(
            call
            for _index, call in sorted(
                enumerate(calls_to_schedule),
                key=lambda item: _schedule_sort_key(item[1], original_index=item[0]),
            )
        )
        for call in ordered_calls:
            call_group_kind = _execution_group_kind(call)
            call_group_id = str(getattr(call, "execution_group_id", "") or "")
            if call_group_kind == ExecutionGroupKind.FALLBACK_CHAIN and call_group_id in scheduled_fallback_groups:
                continue
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
            if next_available_at > deadline_at or (
                call_group_kind == ExecutionGroupKind.FALLBACK_CHAIN
                and _has_later_fallback_candidate(plan=plan, call=call)
                and _slot_exceeds_deadline(next_available_at=next_available_at, deadline_at=deadline_at)
            ):
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
            reserved_at = self._reserve_rate_limit_slot(
                bucket=bucket,
                policy=policy,
                reserve_at=next_available_at,
                deadline_at=deadline_at,
            )
            if reserved_at is None:
                retry_after = self._retry_after_for(bucket=bucket, policy=policy, reserve_at=next_available_at)
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
                        next_available_at=retry_after,
                        deadline_at=deadline_at,
                        outcome="rate_limited_by_tool_budget",
                    )
                )
                continue

            timestamps.append(reserved_at)
            bucket_cursors[bucket] = max(current, reserved_at)
            scheduled_calls.append(
                ScheduledCall(
                    call_id=call.call_id,
                    need_ids=call.need_ids,
                    execution_group_id=call.execution_group_id,
                    execution_group_kind=call.execution_group_kind,
                    source_group_id=call.source_group_id,
                    fallback_order=call.fallback_order,
                    component_id=call.component_id,
                    provider_id=call.provider_id,
                    catalog_endpoint_id=call.catalog_endpoint_id,
                    params=call.params,
                    batch_key=call.batch_key,
                    rate_limit_bucket=bucket,
                    earliest_start_at=reserved_at,
                    rate_limit_reserved_at=None,
                    deadline_at=deadline_at,
                    priority=_priority_for(call),
                )
            )
            if call_group_kind == ExecutionGroupKind.FALLBACK_CHAIN and call_group_id:
                scheduled_fallback_groups.add(call_group_id)
            rate_limit_evidence.append(
                RateLimitEvidence(
                    rate_limit_bucket=bucket,
                    provider_id=call.provider_id,
                    call_id=call.call_id,
                    reserved_at=reserved_at,
                    next_available_at=reserved_at,
                    deadline_at=deadline_at,
                    outcome="scheduled",
                )
            )

        return plan.model_copy(
            update={
                "scheduled_calls": tuple(scheduled_calls),
                "deferred_calls": tuple(call for call in plan.planned_calls if call.call_id not in {item.call_id for item in scheduled_calls}),
                "skipped_needs": tuple(skipped_needs),
                "rate_limit_evidence": tuple(rate_limit_evidence),
                "merge_evidence": plan.merge_evidence,
            }
        )

    def _policy_for(self, call: Any) -> RateLimitPolicy:
        raw = self._rate_limit_policies.get(call.rate_limit_bucket) or getattr(call, "rate_limit_policy", None)
        return _policy_from_raw(raw)

    def _initial_timestamps(self, *, bucket: str, policy: RateLimitPolicy, now: datetime) -> list[datetime]:
        if self._rate_limiter is None or policy.max_requests is None:
            return []
        return list(self._rate_limiter.active_request_timestamps(bucket, policy, now=now))

    def _reserve_rate_limit_slot(
        self,
        *,
        bucket: str,
        policy: RateLimitPolicy,
        reserve_at: datetime,
        deadline_at: datetime,
    ) -> datetime | None:
        if policy.max_requests is None:
            return reserve_at
        if _effective_limit(policy) <= 0 or reserve_at > deadline_at:
            return None
        return reserve_at

    def _retry_after_for(self, *, bucket: str, policy: RateLimitPolicy, reserve_at: datetime) -> datetime:
        if self._rate_limiter is None:
            return reserve_at + timedelta(seconds=policy.window_seconds)
        return self._rate_limiter.peek_next_available(bucket, policy, now=reserve_at)


def _calls_to_schedule(*, plan: NeedPlan, attempt_state: AttemptState | None) -> tuple[ProviderCallSpec, ...]:
    if not plan.execution_groups:
        return tuple(plan.planned_calls)
    call_by_id = {call.call_id: call for call in plan.planned_calls}
    if attempt_state is None:
        return _initial_calls_for_plan(plan=plan, call_by_id=call_by_id)
    if attempt_state.contract_satisfied:
        return ()
    group = next((item for item in plan.execution_groups if item.group_id == attempt_state.execution_group_id), None)
    if group is None:
        return ()
    group_calls = tuple(call_by_id[call_id] for call_id in group.planned_call_ids if call_id in call_by_id)
    attempted = call_by_id.get(attempt_state.call_id)
    if attempted is None:
        return ()
    if group.kind == ExecutionGroupKind.COMPOSITION_GROUP:
        return _next_composition_source_calls(group_calls=group_calls, attempted=attempted)
    return _remaining_fallback_calls(group_calls=group_calls, attempted=attempted)


def _initial_calls_for_plan(*, plan: NeedPlan, call_by_id: dict[str, ProviderCallSpec]) -> tuple[ProviderCallSpec, ...]:
    if not plan.execution_groups:
        if plan.initial_scheduled_calls:
            return tuple(plan.initial_scheduled_calls)
        return tuple(plan.planned_calls)

    selected: list[ProviderCallSpec] = []
    selected_ids: set[str] = set()
    for group in plan.execution_groups:
        if group.kind == ExecutionGroupKind.FALLBACK_CHAIN:
            call_ids = group.planned_call_ids
        else:
            call_ids = group.initial_call_ids or group.planned_call_ids
        for call_id in call_ids:
            call = call_by_id.get(call_id)
            if call is None or call.call_id in selected_ids:
                continue
            selected.append(call)
            selected_ids.add(call.call_id)
    for call in plan.initial_scheduled_calls:
        if call.call_id not in selected_ids:
            selected.append(call)
            selected_ids.add(call.call_id)
    return tuple(selected)


def _next_fallback_call(*, group_calls: Sequence[ProviderCallSpec], attempted: ProviderCallSpec) -> tuple[ProviderCallSpec, ...]:
    candidates = _remaining_fallback_calls(group_calls=group_calls, attempted=attempted)
    return candidates[:1]


def _remaining_fallback_calls(*, group_calls: Sequence[ProviderCallSpec], attempted: ProviderCallSpec) -> tuple[ProviderCallSpec, ...]:
    source_orders: dict[str, int] = {}
    for call in group_calls:
        source = call.source_group_id or call.provider_id
        source_orders[source] = min(call.fallback_order, source_orders.get(source, call.fallback_order))
    attempted_source = attempted.source_group_id or attempted.provider_id
    attempted_source_order = source_orders.get(attempted_source, attempted.fallback_order)
    same_source_candidates = sorted(
        (
            call
            for call in group_calls
            if (call.source_group_id or call.provider_id) == attempted_source
            and call.fallback_order > attempted.fallback_order
        ),
        key=lambda call: (call.fallback_order, call.provider_id, call.catalog_endpoint_id),
    )
    ordered_candidates: list[ProviderCallSpec] = list(same_source_candidates)

    next_sources = sorted(
        (source for source, order in source_orders.items() if order > attempted_source_order),
        key=lambda source: (source_orders[source], source),
    )
    for next_source in next_sources:
        source_candidates = sorted(
            (
                call
                for call in group_calls
                if (call.source_group_id or call.provider_id) == next_source
                and call.fallback_order > attempted.fallback_order
            ),
            key=lambda call: (call.fallback_order, call.provider_id, call.catalog_endpoint_id),
        )
        ordered_candidates.extend(source_candidates)

    candidates = sorted(
        (call for call in group_calls if call.fallback_order > attempted.fallback_order),
        key=lambda call: (call.fallback_order, call.provider_id, call.catalog_endpoint_id),
    )
    seen = {call.call_id for call in ordered_candidates}
    ordered_candidates.extend(call for call in candidates if call.call_id not in seen)
    return tuple(ordered_candidates)


def _next_composition_source_calls(
    *,
    group_calls: Sequence[ProviderCallSpec],
    attempted: ProviderCallSpec,
) -> tuple[ProviderCallSpec, ...]:
    source_orders: dict[str, int] = {}
    for call in group_calls:
        source = call.source_group_id or call.provider_id
        source_orders[source] = min(call.fallback_order, source_orders.get(source, call.fallback_order))
    attempted_source = attempted.source_group_id or attempted.provider_id
    attempted_order = source_orders.get(attempted_source, attempted.fallback_order)
    next_sources = sorted(
        (source for source, order in source_orders.items() if order > attempted_order),
        key=lambda source: (source_orders[source], source),
    )
    if not next_sources:
        return ()
    next_source = next_sources[0]
    return tuple(
        call
        for call in sorted(group_calls, key=lambda item: (item.fallback_order, item.provider_id, item.catalog_endpoint_id))
        if (call.source_group_id or call.provider_id) == next_source
    )


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


def _effective_limit(policy: RateLimitPolicy) -> int:
    if policy.max_requests is None:
        return 0
    return max(policy.max_requests - policy.safety_margin, 0)


def _looks_like_provider_call(item: Any) -> bool:
    return all(hasattr(item, attr) for attr in ("call_id", "rate_limit_bucket", "deadline_at", "need_ids"))


def _rate_limit_evidence_refs(bucket: str) -> tuple[str, ...]:
    return (f"rate_limit:{bucket}",)


def _priority_for(call: Any) -> NeedPriority:
    value = getattr(call, "priority", NeedPriority.NORMAL)
    if isinstance(value, NeedPriority):
        return value
    try:
        return NeedPriority(str(value))
    except ValueError:
        return NeedPriority.NORMAL


def _schedule_sort_key(call: Any, *, original_index: int) -> tuple[int, datetime, int]:
    return (
        _priority_rank(getattr(call, "priority", NeedPriority.NORMAL)),
        _normalize_datetime(call.deadline_at),
        original_index,
    )


def _priority_rank(value: Any) -> int:
    if isinstance(value, NeedPriority):
        priority = value
    else:
        try:
            priority = NeedPriority(str(value))
        except ValueError:
            priority = NeedPriority.NORMAL
    return {
        NeedPriority.REQUIRED: 0,
        NeedPriority.NORMAL: 1,
        NeedPriority.OPTIONAL: 2,
        NeedPriority.EXPENSIVE: 3,
    }.get(priority, 1)


def _execution_group_kind(call: Any) -> ExecutionGroupKind | None:
    raw = getattr(call, "execution_group_kind", None)
    if isinstance(raw, ExecutionGroupKind):
        return raw
    try:
        return ExecutionGroupKind(str(raw))
    except ValueError:
        return None


def _slot_exceeds_deadline(*, next_available_at: datetime, deadline_at: datetime) -> bool:
    if next_available_at >= datetime.max.replace(tzinfo=UTC) - timedelta(seconds=_MIN_PROVIDER_EXECUTION_WINDOW_SECONDS):
        return True
    return next_available_at + timedelta(seconds=_MIN_PROVIDER_EXECUTION_WINDOW_SECONDS) > deadline_at


def _has_later_fallback_candidate(*, plan: NeedPlan, call: ProviderCallSpec) -> bool:
    group_id = call.execution_group_id or call.call_id
    for candidate in plan.planned_calls:
        if candidate.call_id == call.call_id:
            continue
        if (candidate.execution_group_id or candidate.call_id) != group_id:
            continue
        if candidate.fallback_order > call.fallback_order:
            return True
    return False


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
    if active and len(active) < effective_limit:
        spacing_seconds = policy.window_seconds / effective_limit
        return max(current, active[-1] + timedelta(seconds=spacing_seconds))
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
