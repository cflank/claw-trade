from __future__ import annotations

from datetime import UTC, datetime, timedelta

from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


def test_sliding_window_waits_for_eleventh_call_when_deadline_allows() -> None:
    clock = _Clock(datetime(2026, 6, 12, 12, 0, tzinfo=UTC))
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.tick(seconds)

    limiter = RateLimiter(now_fn=clock, sleep_fn=_sleep)
    policy = RateLimitPolicy(window_seconds=60, max_requests=10)

    for _ in range(10):
        assert limiter.reserve("ratelimit:coinglass", policy).allowed is True
    eleventh = limiter.reserve(
        "ratelimit:coinglass",
        policy,
        deadline_at=clock.now + timedelta(seconds=90),
    )

    assert eleventh.allowed is True
    assert sleep_calls == [60.0]
    assert clock.now == datetime(2026, 6, 12, 12, 1, tzinfo=UTC)


def test_sliding_window_returns_budget_gap_without_wait_or_network_slot() -> None:
    clock = _Clock(datetime(2026, 6, 12, 12, 0, tzinfo=UTC))
    sleep_calls: list[float] = []
    limiter = RateLimiter(now_fn=clock, sleep_fn=sleep_calls.append)
    policy = RateLimitPolicy(window_seconds=60, max_requests=10)

    for _ in range(10):
        assert limiter.reserve("ratelimit:tushare", policy).allowed is True
    eleventh = limiter.reserve(
        "ratelimit:tushare",
        policy,
        deadline_at=clock.now + timedelta(seconds=30),
    )

    assert eleventh.allowed is False
    assert eleventh.reason == "rate_limited_by_tool_budget"
    assert eleventh.retry_after == datetime(2026, 6, 12, 12, 1, tzinfo=UTC)
    assert sleep_calls == []
