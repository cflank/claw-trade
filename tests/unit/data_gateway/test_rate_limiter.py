from __future__ import annotations

from datetime import UTC, datetime, timedelta

from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


def test_rate_limiter_fail_fast_blocks_after_budget() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    limiter = RateLimiter(now_fn=clock)
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    assert limiter.reserve("k", policy).allowed is True
    blocked = limiter.reserve("k", policy)
    assert blocked.allowed is False
    assert blocked.reason == "rate_limited"


def test_rate_limiter_waits_for_short_cooldown_and_reserves() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.tick(seconds + 0.01)

    limiter = RateLimiter(now_fn=clock, sleep_fn=_sleep)
    limiter.mark_cooldown("k", until=clock.now + timedelta(seconds=30), reason="provider_429")
    decision = limiter.reserve(
        "k",
        RateLimitPolicy(window_seconds=60, max_requests=100),
        deadline_at=clock.now + timedelta(seconds=60),
    )

    assert decision.allowed is True
    assert sleep_calls == [30.0]


def test_rate_limiter_skips_cooldown_beyond_deadline() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    limiter = RateLimiter(now_fn=clock)
    limiter.mark_cooldown("k", until=clock.now + timedelta(seconds=120), reason="provider_429")
    decision = limiter.reserve(
        "k",
        RateLimitPolicy(window_seconds=60, max_requests=100),
        deadline_at=clock.now + timedelta(seconds=60),
    )
    assert decision.allowed is False
    assert decision.reason == "rate_limited_by_tool_budget"


def test_rate_limiter_can_recover_after_sleep_before_deadline() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC))
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.tick(seconds + 0.01)

    limiter = RateLimiter(now_fn=clock, sleep_fn=_sleep)
    policy = RateLimitPolicy(window_seconds=5, max_requests=1)
    assert limiter.reserve("k", policy).allowed is True
    decision = limiter.reserve("k", policy, deadline_at=clock.now + timedelta(seconds=10))
    assert decision.allowed is True
    assert sleep_calls


def test_rate_limiter_does_not_pace_before_sliding_window_is_full() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC))
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.tick(seconds)

    limiter = RateLimiter(now_fn=clock, sleep_fn=_sleep)
    policy = RateLimitPolicy(window_seconds=60, max_requests=9, safety_margin=1)

    assert limiter.reserve("paced", policy).allowed is True
    assert limiter.reserve("paced", policy).allowed is True

    assert sleep_calls == []


def test_rate_limiter_returns_tool_budget_reason_without_sleep_when_deadline_too_short() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC))
    sleep_calls: list[float] = []
    limiter = RateLimiter(now_fn=clock, sleep_fn=sleep_calls.append)
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)

    assert limiter.reserve("k", policy).allowed is True
    decision = limiter.reserve("k", policy, deadline_at=clock.now + timedelta(seconds=30))

    assert decision.allowed is False
    assert decision.reason == "rate_limited_by_tool_budget"
    assert decision.retry_after == datetime(2026, 5, 31, 12, 1, tzinfo=UTC)
    assert sleep_calls == []


def test_rate_limiter_uses_sliding_window_across_fixed_minute_boundary() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, 55, tzinfo=UTC))
    limiter = RateLimiter(now_fn=clock)
    policy = RateLimitPolicy(window_seconds=60, max_requests=2)

    assert limiter.reserve("k", policy).allowed is True
    assert limiter.reserve("k", policy).allowed is True
    clock.tick(10)
    blocked = limiter.reserve("k", policy)
    assert blocked.allowed is False
    assert blocked.retry_after == datetime(2026, 5, 31, 12, 1, 55, tzinfo=UTC)
    clock.tick(50)
    assert limiter.reserve("k", policy).allowed is True


def test_rate_limiter_safety_margin_reduces_sliding_window_budget() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    limiter = RateLimiter(now_fn=clock)
    policy = RateLimitPolicy(window_seconds=60, max_requests=3, safety_margin=1)

    assert limiter.reserve("k", policy).allowed is True
    assert limiter.reserve("k", policy).allowed is True
    blocked = limiter.reserve("k", policy)
    assert blocked.allowed is False
    assert blocked.retry_after == datetime(2026, 5, 31, 12, 1, tzinfo=UTC)


def test_rate_limiter_fails_immediately_when_safety_margin_consumes_budget() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    sleep_calls: list[float] = []
    limiter = RateLimiter(now_fn=clock, sleep_fn=sleep_calls.append)
    policy = RateLimitPolicy(window_seconds=60, max_requests=1, safety_margin=1)

    blocked = limiter.reserve("k", policy, deadline_at=clock.now + timedelta(seconds=120))

    assert blocked.allowed is False
    assert blocked.reason == "rate_limited_by_tool_budget"
    assert sleep_calls == []


def test_rate_limiter_persists_state_across_instances_with_same_repository() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    repository = DatasetRepository()
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)

    limiter_a = RateLimiter(repository=repository, now_fn=clock)
    assert limiter_a.reserve("k", policy).allowed is True

    limiter_b = RateLimiter(repository=repository, now_fn=clock)
    blocked = limiter_b.reserve("k", policy)
    assert blocked.allowed is False
    assert blocked.reason == "rate_limited"


def test_rate_limiter_handles_naive_cooldown_records_from_storage() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    repository = DatasetRepository()
    repository.mark_rate_limit_cooldown(
        rate_limit_key="k",
        now=clock.now,
        until=datetime(2026, 5, 31, 12, 2),
        reason="provider_429",
    )

    limiter = RateLimiter(repository=repository, now_fn=clock)
    decision = limiter.reserve(
        "k",
        RateLimitPolicy(window_seconds=60, max_requests=100),
        deadline_at=clock.now + timedelta(seconds=60),
    )

    assert decision.allowed is False
    assert decision.reason == "rate_limited_by_tool_budget"
