from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Callable

from claw_trade.data_gateway.warehouse.repository import DatasetRepository


@dataclass(frozen=True)
class RateLimitPolicy:
    window_seconds: int
    max_requests: int | None
    safety_margin: int = 0
    window_anchor: datetime | None = None


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    reason: str | None = None
    retry_after: datetime | None = None
    reserved_at: datetime | None = None

    @classmethod
    def reserved(cls, reserved_at: datetime | None = None) -> "RateLimitDecision":
        return cls(allowed=True, reserved_at=reserved_at)

    @classmethod
    def blocked(cls, reason: str, retry_after: datetime | None = None) -> "RateLimitDecision":
        return cls(allowed=False, reason=reason, retry_after=retry_after)


class RateLimiter:
    def __init__(
        self,
        repository: DatasetRepository | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._repository = repository or DatasetRepository()
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn or sleep

    def reserve(
        self,
        key: str,
        policy: RateLimitPolicy,
        cost: int = 1,
        *,
        deadline_at: datetime | None = None,
    ) -> RateLimitDecision:
        if policy.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if cost <= 0:
            raise ValueError("cost must be > 0")
        deadline_at = _normalize_deadline(deadline_at)

        while True:
            now = self._now()
            active_cooldown = self._repository.get_active_rate_limit_cooldown(rate_limit_key=key, now=now)
            if active_cooldown is not None:
                if self._wait_for_cooldown(now=now, cooldown_until=active_cooldown, deadline_at=deadline_at):
                    continue
                return _blocked_for_deadline(active_cooldown, deadline_at, fallback_reason="cooldown_skipped")

            if policy.max_requests is None:
                window_start = self._window_start(now, policy)
                state = self._repository.get_or_init_rate_limit_record(
                    rate_limit_key=key,
                    window_start=window_start,
                    window_seconds=policy.window_seconds,
                    max_requests=policy.max_requests,
                    safety_margin=policy.safety_margin,
                )
            else:
                if _effective_limit(policy) <= 0:
                    retry_after = now + timedelta(seconds=policy.window_seconds)
                    reason = "rate_limited_by_tool_budget" if deadline_at is not None else "rate_limited"
                    return RateLimitDecision.blocked(reason, retry_after=retry_after)
                state = self._repository.reserve_sliding_rate_limit(
                    rate_limit_key=key,
                    now=now,
                    window_seconds=policy.window_seconds,
                    max_requests=policy.max_requests,
                    safety_margin=policy.safety_margin,
                    cost=cost,
                )
            cooldown_until = state.get("cooldown_until")
            if isinstance(cooldown_until, datetime) and now < cooldown_until:
                if self._wait_for_cooldown(now=now, cooldown_until=cooldown_until, deadline_at=deadline_at):
                    continue
                return _blocked_for_deadline(cooldown_until, deadline_at, fallback_reason="cooldown_skipped")

            if policy.max_requests is None:
                if self._repository.cas_increment_rate_limit_used(
                    rate_limit_key=key,
                    window_start=window_start,
                    expected_version=int(state.get("version", 0)),
                    cost=cost,
                ):
                    return RateLimitDecision.reserved()
                continue

            if state.get("allowed") is True:
                return RateLimitDecision.reserved()

            retry_after = state.get("retry_after")
            if not isinstance(retry_after, datetime):
                retry_after = now + timedelta(seconds=policy.window_seconds)
            retry_after = _normalize_datetime(retry_after)
            if deadline_at is None:
                return RateLimitDecision.blocked("rate_limited", retry_after=retry_after)
            if retry_after > deadline_at:
                return RateLimitDecision.blocked("rate_limited_by_tool_budget", retry_after=retry_after)

            wait_seconds = max((retry_after - self._now()).total_seconds(), 0)
            if wait_seconds > 0:
                self._sleep(wait_seconds)

    def check_cooldown(
        self,
        key: str,
        policy: RateLimitPolicy,
        *,
        deadline_at: datetime | None = None,
    ) -> RateLimitDecision:
        deadline_at = _normalize_deadline(deadline_at)
        now = self._now()
        active_cooldown = self._repository.get_active_rate_limit_cooldown(rate_limit_key=key, now=now)
        if active_cooldown is None:
            return RateLimitDecision.reserved()
        if self._wait_for_cooldown(now=now, cooldown_until=active_cooldown, deadline_at=deadline_at):
            return RateLimitDecision.reserved()
        return _blocked_for_deadline(active_cooldown, deadline_at, fallback_reason="cooldown_skipped")

    def reserve_at(
        self,
        key: str,
        policy: RateLimitPolicy,
        *,
        reserve_at: datetime,
        cost: int = 1,
        deadline_at: datetime | None = None,
    ) -> RateLimitDecision:
        if policy.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if cost <= 0:
            raise ValueError("cost must be > 0")
        deadline_at = _normalize_deadline(deadline_at)
        target = _normalize_datetime(reserve_at)

        if policy.max_requests is None:
            return RateLimitDecision.reserved(target)
        if _effective_limit(policy) <= 0:
            retry_after = target + timedelta(seconds=policy.window_seconds)
            return _blocked_for_deadline(retry_after, deadline_at, fallback_reason="rate_limited")

        while True:
            active_cooldown = self._repository.get_active_rate_limit_cooldown(rate_limit_key=key, now=target)
            if active_cooldown is not None and active_cooldown > target:
                retry_after = _normalize_datetime(active_cooldown)
                if deadline_at is not None and retry_after > deadline_at:
                    return RateLimitDecision.blocked("rate_limited_by_tool_budget", retry_after=retry_after)
                target = retry_after
                continue

            state = self._repository.reserve_sliding_rate_limit(
                rate_limit_key=key,
                now=target,
                window_seconds=policy.window_seconds,
                max_requests=policy.max_requests,
                safety_margin=policy.safety_margin,
                cost=cost,
            )
            if state.get("allowed") is True:
                return RateLimitDecision.reserved(target)

            retry_after = state.get("retry_after")
            if not isinstance(retry_after, datetime):
                retry_after = target + timedelta(seconds=policy.window_seconds)
            retry_after = _normalize_datetime(retry_after)
            if deadline_at is not None and retry_after > deadline_at:
                return RateLimitDecision.blocked("rate_limited_by_tool_budget", retry_after=retry_after)
            target = retry_after

    def peek_next_available(self, key: str, policy: RateLimitPolicy, *, now: datetime | None = None) -> datetime:
        current = _normalize_datetime(now or self._now())
        cooldown_until = self._repository.get_active_rate_limit_cooldown(rate_limit_key=key, now=current)
        if cooldown_until is not None:
            return _normalize_datetime(cooldown_until)
        if policy.max_requests is None:
            return current
        effective_limit = _effective_limit(policy)
        if effective_limit <= 0:
            return datetime.max.replace(tzinfo=UTC)
        state = self._repository.get_latest_rate_limit_record(rate_limit_key=key)
        if state is None or state.get("window_mode") != "sliding":
            return current
        cutoff = current - timedelta(seconds=policy.window_seconds)
        timestamps = tuple(
            timestamp
            for timestamp in (_coerce_timestamp(raw) for raw in state.get("request_timestamps", ()))
            if timestamp is not None and cutoff < timestamp <= current
        )
        if len(timestamps) < effective_limit:
            return current
        return min(timestamps) + timedelta(seconds=policy.window_seconds)

    def active_request_timestamps(
        self,
        key: str,
        policy: RateLimitPolicy,
        *,
        now: datetime | None = None,
    ) -> tuple[datetime, ...]:
        if policy.max_requests is None:
            return ()
        current = _normalize_datetime(now or self._now())
        state = self._repository.get_latest_rate_limit_record(rate_limit_key=key)
        if state is None or state.get("window_mode") != "sliding":
            return ()
        cutoff = current - timedelta(seconds=policy.window_seconds)
        return tuple(
            timestamp
            for timestamp in (_coerce_timestamp(raw) for raw in state.get("request_timestamps", ()))
            if timestamp is not None and cutoff < timestamp <= current
        )

    def mark_cooldown(self, key: str, until: datetime, reason: str) -> None:
        self._repository.mark_rate_limit_cooldown(
            rate_limit_key=key,
            now=self._now(),
            until=until,
            reason=reason,
        )

    @classmethod
    def _window_start(cls, now: datetime, policy: RateLimitPolicy) -> datetime:
        if policy.window_anchor is None:
            return cls._floor_to_window(now, policy.window_seconds)
        anchor = policy.window_anchor if policy.window_anchor.tzinfo else policy.window_anchor.replace(tzinfo=UTC)
        if anchor.tzinfo != UTC:
            anchor = anchor.astimezone(UTC)
        elapsed = max((now - anchor).total_seconds(), 0)
        windows_elapsed = int(elapsed // policy.window_seconds)
        return anchor + timedelta(seconds=windows_elapsed * policy.window_seconds)

    def _wait_for_cooldown(
        self,
        *,
        now: datetime,
        cooldown_until: datetime,
        deadline_at: datetime | None,
    ) -> bool:
        cooldown_until = _normalize_datetime(cooldown_until)
        if cooldown_until <= now:
            return True
        if deadline_at is None:
            return False
        if cooldown_until > deadline_at:
            return False

        wait_seconds = max((cooldown_until - self._now()).total_seconds(), 0)
        if wait_seconds > 0:
            self._sleep(wait_seconds)
        return True

    @staticmethod
    def _floor_to_window(now: datetime, window_seconds: int) -> datetime:
        epoch = int(now.timestamp())
        floored = epoch - (epoch % window_seconds)
        return datetime.fromtimestamp(floored, tz=UTC)


def _normalize_deadline(deadline_at: datetime | None) -> datetime | None:
    return None if deadline_at is None else _normalize_datetime(deadline_at)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if value.tzinfo != UTC:
        return value.astimezone(UTC)
    return value


def _coerce_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, datetime):
        return None
    return _normalize_datetime(raw)


def _effective_limit(policy: RateLimitPolicy) -> int:
    if policy.max_requests is None:
        return 0
    return max(policy.max_requests - policy.safety_margin, 0)


def _blocked_for_deadline(retry_after: datetime, deadline_at: datetime | None, *, fallback_reason: str) -> RateLimitDecision:
    retry_after = _normalize_datetime(retry_after)
    if deadline_at is not None and retry_after > deadline_at:
        return RateLimitDecision.blocked("rate_limited_by_tool_budget", retry_after=retry_after)
    return RateLimitDecision.blocked(fallback_reason, retry_after=retry_after)
