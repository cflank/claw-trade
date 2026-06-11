from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Callable, Literal

from claw_trade.data_gateway.warehouse.repository import DatasetRepository


@dataclass(frozen=True)
class RateLimitPolicy:
    window_seconds: int
    max_requests: int | None
    safety_margin: int = 0
    overflow: Literal["wait", "fail_fast"] = "fail_fast"
    wait_timeout_seconds: int = 0
    window_anchor: datetime | None = None


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    reason: str | None = None
    retry_after: datetime | None = None

    @classmethod
    def reserved(cls) -> "RateLimitDecision":
        return cls(allowed=True)

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

    def reserve(self, key: str, policy: RateLimitPolicy, cost: int = 1) -> RateLimitDecision:
        if policy.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if cost <= 0:
            raise ValueError("cost must be > 0")

        while True:
            now = self._now()
            active_cooldown = self._repository.get_active_rate_limit_cooldown(rate_limit_key=key, now=now)
            if active_cooldown is not None:
                if self._wait_for_cooldown(now=now, cooldown_until=active_cooldown, policy=policy):
                    continue
                return RateLimitDecision.blocked("cooldown_skipped", retry_after=active_cooldown)

            pacing_decision = self._wait_for_sliding_pace(key=key, now=now, policy=policy, cost=cost)
            if pacing_decision is not None:
                if pacing_decision.allowed:
                    continue
                return pacing_decision

            if policy.max_requests is None:
                window_start = self._window_start(now, policy)
                state = self._repository.get_or_init_rate_limit_record(
                    rate_limit_key=key,
                    window_start=window_start,
                    window_seconds=policy.window_seconds,
                    max_requests=policy.max_requests,
                    safety_margin=policy.safety_margin,
                    overflow_policy=policy.overflow,
                )
            else:
                state = self._repository.reserve_sliding_rate_limit(
                    rate_limit_key=key,
                    now=now,
                    window_seconds=policy.window_seconds,
                    max_requests=policy.max_requests,
                    safety_margin=policy.safety_margin,
                    overflow_policy=policy.overflow,
                    cost=cost,
                )
            cooldown_until = state.get("cooldown_until")
            if isinstance(cooldown_until, datetime) and now < cooldown_until:
                if self._wait_for_cooldown(now=now, cooldown_until=cooldown_until, policy=policy):
                    continue
                return RateLimitDecision.blocked("cooldown_skipped", retry_after=cooldown_until)

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
            if policy.overflow == "fail_fast":
                return RateLimitDecision.blocked("rate_limited", retry_after=retry_after)

            wait_deadline = now + timedelta(seconds=max(policy.wait_timeout_seconds, 0))
            if retry_after > wait_deadline:
                return RateLimitDecision.blocked("rate_limited", retry_after=retry_after)

            wait_seconds = max((retry_after - self._now()).total_seconds(), 0)
            if wait_seconds > 0:
                self._sleep(wait_seconds)

    def check_cooldown(self, key: str, policy: RateLimitPolicy) -> RateLimitDecision:
        now = self._now()
        active_cooldown = self._repository.get_active_rate_limit_cooldown(rate_limit_key=key, now=now)
        if active_cooldown is None:
            return RateLimitDecision.reserved()
        if self._wait_for_cooldown(now=now, cooldown_until=active_cooldown, policy=policy):
            return RateLimitDecision.reserved()
        return RateLimitDecision.blocked("cooldown_skipped", retry_after=active_cooldown)

    def mark_cooldown(self, key: str, until: datetime, reason: str) -> None:
        self._repository.mark_rate_limit_cooldown(
            rate_limit_key=key,
            now=self._now(),
            until=until,
            reason=reason,
        )

    def _wait_for_sliding_pace(
        self,
        *,
        key: str,
        now: datetime,
        policy: RateLimitPolicy,
        cost: int,
    ) -> RateLimitDecision | None:
        if policy.max_requests is None or policy.overflow != "wait":
            return None
        effective_limit = max(policy.max_requests - policy.safety_margin, 0)
        if effective_limit <= 0:
            return None
        latest_timestamp = self._latest_sliding_request_timestamp(key=key, now=now, window_seconds=policy.window_seconds)
        if latest_timestamp is None:
            return None

        min_spacing_seconds = (policy.window_seconds / effective_limit) * cost
        retry_after = latest_timestamp + timedelta(seconds=min_spacing_seconds)
        if retry_after <= now:
            return None

        max_wait_seconds = policy.wait_timeout_seconds if policy.wait_timeout_seconds > 0 else policy.window_seconds
        if retry_after > now + timedelta(seconds=max_wait_seconds):
            return RateLimitDecision.blocked("rate_limited", retry_after=retry_after)

        wait_seconds = max((retry_after - self._now()).total_seconds(), 0)
        if wait_seconds > 0:
            self._sleep(wait_seconds)
        return RateLimitDecision.reserved()

    def _latest_sliding_request_timestamp(self, *, key: str, now: datetime, window_seconds: int) -> datetime | None:
        state = self._repository.get_latest_rate_limit_record(rate_limit_key=key)
        if state is None or state.get("window_mode") != "sliding":
            return None
        cutoff = now - timedelta(seconds=window_seconds)
        timestamps: list[datetime] = []
        for raw in state.get("request_timestamps", ()):
            if not isinstance(raw, datetime):
                continue
            timestamp = raw if raw.tzinfo else raw.replace(tzinfo=UTC)
            if timestamp.tzinfo != UTC:
                timestamp = timestamp.astimezone(UTC)
            if timestamp > cutoff:
                timestamps.append(timestamp)
        return max(timestamps) if timestamps else None

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

    def _wait_for_cooldown(self, *, now: datetime, cooldown_until: datetime, policy: RateLimitPolicy) -> bool:
        if cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=UTC)
        if cooldown_until.tzinfo != UTC:
            cooldown_until = cooldown_until.astimezone(UTC)
        if cooldown_until <= now:
            return True

        max_wait_seconds = policy.wait_timeout_seconds if policy.wait_timeout_seconds > 0 else policy.window_seconds
        if max_wait_seconds <= 0:
            return False
        if cooldown_until > now + timedelta(seconds=max_wait_seconds):
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
