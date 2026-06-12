from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from claw_trade.data_gateway.execution import ProviderResultCache, ResultRefs
from claw_trade.data_gateway.execution.gate import ExecutionGate
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.ingest import DataGap, IngestResult
from claw_trade.data_gateway.models import HttpVisibility
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


@dataclass(frozen=True)
class _Batch:
    cache_key: str = "cache:key"
    rate_limit_key: str = "rl:key"
    cooldown_key: str | None = None
    rate_limit_policy: RateLimitPolicy = RateLimitPolicy(window_seconds=60, max_requests=10)
    http_visibility: str = "sdk_internal_unknown"
    single_flight_key: str = "sf:key"
    lease_ttl_seconds: int = 30
    wait_timeout_seconds: int = 1
    provider_id: str = "provider"
    endpoint_id: str = "endpoint"
    market: str = "CN_A"
    data_type: str = "daily_bar"
    deadline_at: datetime | None = None


def test_gate_returns_cache_hit_before_rate_limit_or_single_flight() -> None:
    now = datetime.now(UTC)
    cache = ProviderResultCache()
    cache.put_remote_success(
        cache_key="cache:key",
        refs=ResultRefs(dataset_refs=("dataset:1",), raw_refs=("raw:1",), attempt_refs=("attempt:1",)),
        fresh_until=now + timedelta(seconds=30),
        stale_until=now + timedelta(seconds=300),
    )
    gate = ExecutionGate(cache=cache, rate_limiter=RateLimiter(), single_flight=SingleFlight())
    decision = gate.enter(_Batch())
    assert decision.kind == "cache_hit"
    assert decision.refs.dataset_refs == ("dataset:1",)


def test_gate_returns_cached_empty_from_cache() -> None:
    now = datetime.now(UTC)
    cache = ProviderResultCache()
    cache.put_cached_empty(
        cache_key="cache:key",
        refs=ResultRefs(attempt_refs=("attempt:empty",)),
        fresh_until=now + timedelta(seconds=30),
        stale_until=now + timedelta(seconds=300),
    )
    gate = ExecutionGate(cache=cache, rate_limiter=RateLimiter(), single_flight=SingleFlight())
    decision = gate.enter(_Batch())
    assert decision.kind == "cached_empty"
    assert decision.refs.attempt_refs == ("attempt:empty",)


def test_cache_unknown_status_is_not_treated_as_remote_success() -> None:
    now = datetime.now(UTC)
    repository = DatasetRepository()
    repository.write_provider_result_cache(
        cache_key="cache:key",
        status="sdk_http_unknown",
        dataset_refs=("dataset:bad",),
        raw_refs=("raw:bad",),
        attempt_refs=("attempt:bad",),
        fresh_until=now + timedelta(seconds=30),
        stale_until=now + timedelta(seconds=300),
    )
    cache = ProviderResultCache(repository=repository)
    lookup = cache.get("cache:key", now=now)
    assert lookup.state == "miss"


def test_cache_accepts_naive_cached_datetime_when_now_is_aware() -> None:
    repository = DatasetRepository()
    repository.write_provider_result_cache(
        cache_key="cache:key",
        status="remote_success",
        dataset_refs=("dataset:ok",),
        raw_refs=(),
        attempt_refs=("attempt:ok",),
        fresh_until=datetime(2026, 6, 1, 12, 5),
        stale_until=datetime(2026, 6, 1, 12, 30),
    )
    cache = ProviderResultCache(repository=repository)

    lookup = cache.get("cache:key", now=datetime(2026, 6, 1, 12, 0, tzinfo=UTC))

    assert lookup.state == "fresh_success"
    assert lookup.entry is not None
    assert lookup.entry.refs.dataset_refs == ("dataset:ok",)


def test_gate_returns_rate_limited_when_quota_blocked() -> None:
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    batch = _Batch(rate_limit_policy=policy)
    limiter = RateLimiter()
    assert limiter.reserve(batch.rate_limit_key, policy).allowed is True
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=limiter, single_flight=SingleFlight())
    decision = gate.enter(batch)
    assert decision.kind == "rate_limited"


def test_gate_uses_batch_deadline_for_non_managed_http_quota_wait() -> None:
    clock = _Clock(datetime(2026, 6, 11, 12, 0, tzinfo=UTC))
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.tick(seconds)

    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    batch = _Batch(rate_limit_policy=policy, deadline_at=clock.now + timedelta(seconds=90))
    limiter = RateLimiter(now_fn=clock, sleep_fn=_sleep)
    assert limiter.reserve(batch.rate_limit_key, policy).allowed is True
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=limiter, single_flight=SingleFlight())

    decision = gate.enter(batch)

    assert decision.kind == "owner"
    assert sleep_calls == [60.0]


def test_gate_returns_tool_budget_rate_limit_when_deadline_too_short() -> None:
    clock = _Clock(datetime(2026, 6, 11, 12, 0, tzinfo=UTC))
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    batch = _Batch(rate_limit_policy=policy, deadline_at=clock.now + timedelta(seconds=30))
    limiter = RateLimiter(now_fn=clock)
    assert limiter.reserve(batch.rate_limit_key, policy).allowed is True
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=limiter, single_flight=SingleFlight())

    decision = gate.enter(batch)

    assert decision.kind == "rate_limited"
    assert decision.reason == "rate_limited_by_tool_budget"


def test_gate_skips_source_quota_for_managed_http_batches() -> None:
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    first = _Batch(
        cache_key="cache:first",
        single_flight_key="sf:first",
        rate_limit_policy=policy,
        http_visibility="managed_http",
    )
    second = _Batch(
        cache_key="cache:second",
        single_flight_key="sf:second",
        rate_limit_policy=policy,
        http_visibility="managed_http",
    )

    assert gate.enter(first).kind == "owner"
    assert gate.enter(second).kind == "owner"


def test_gate_skips_source_quota_for_managed_http_enum_batches() -> None:
    policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    first = _Batch(
        cache_key="cache:first",
        single_flight_key="sf:first",
        rate_limit_policy=policy,
        http_visibility=HttpVisibility.MANAGED_HTTP,
    )
    second = _Batch(
        cache_key="cache:second",
        single_flight_key="sf:second",
        rate_limit_policy=policy,
        http_visibility=HttpVisibility.MANAGED_HTTP,
    )

    assert gate.enter(first).kind == "owner"
    assert gate.enter(second).kind == "owner"


def test_gate_marks_cooldown_after_provider_429_fetch_result() -> None:
    batch = _Batch(rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=10))
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    owner = gate.enter(batch)
    assert owner.kind == "owner"

    gate.mark_cooldown_after_fetch(
        batch,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(response_headers_redacted={"retry-after": "120"}),
            ),
        ),
    )

    decision = gate.enter(batch)
    assert decision.kind == "cooldown_skipped"
    assert decision.retry_after is not None


def test_gate_does_not_mark_provider_cooldown_for_local_rate_limit() -> None:
    batch = _Batch(rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=10))
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    owner = gate.enter(batch)
    assert owner.kind == "owner"

    gate.mark_cooldown_after_fetch(
        batch,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(status_code=None, quota_signal="local_rate_limited", response_headers_redacted={}),
            ),
        ),
    )

    next_batch = _Batch(cache_key="cache:next", single_flight_key="sf:next", rate_limit_policy=batch.rate_limit_policy)
    assert gate.enter(next_batch).kind == "owner"


def test_gate_does_not_mark_provider_cooldown_for_tool_budget_rate_limit() -> None:
    batch = _Batch(rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=10))
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    owner = gate.enter(batch)
    assert owner.kind == "owner"

    gate.mark_cooldown_after_fetch(
        batch,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(status_code=None, quota_signal="rate_limited_by_tool_budget", response_headers_redacted={}),
            ),
        ),
    )

    next_batch = _Batch(cache_key="cache:next", single_flight_key="sf:next", rate_limit_policy=batch.rate_limit_policy)
    assert gate.enter(next_batch).kind == "owner"


def test_gate_waits_after_provider_429_when_deadline_allows_wait() -> None:
    batch = _Batch(
        rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=10),
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())

    should_retry = gate.wait_after_rate_limited_fetch(
        batch,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(status_code=429, response_headers_redacted={"retry-after": "0"}),
            ),
        ),
    )

    assert should_retry is True


def test_gate_does_not_wait_after_provider_429_when_deadline_is_missing() -> None:
    batch = _Batch(rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=10), deadline_at=None)
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())

    should_retry = gate.wait_after_rate_limited_fetch(
        batch,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(status_code=429, response_headers_redacted={"retry-after": "0"}),
            ),
        ),
    )

    assert should_retry is False


def test_gate_does_not_wait_after_provider_429_when_deadline_is_too_short() -> None:
    batch = _Batch(
        rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=10),
        deadline_at=datetime.now(UTC) + timedelta(seconds=5),
    )
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())

    should_retry = gate.wait_after_rate_limited_fetch(
        batch,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(status_code=429, response_headers_redacted={"retry-after": "60"}),
            ),
        ),
    )

    assert should_retry is False


def test_gate_scopes_provider_429_cooldown_to_endpoint_key_not_source_quota() -> None:
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    source_key = "ratelimit:coinglass"
    first_endpoint = _Batch(
        cache_key="cache:first",
        rate_limit_key=source_key,
        cooldown_key="cooldown:coinglass:futures_open_interest",
        single_flight_key="sf:first",
    )
    other_endpoint = _Batch(
        cache_key="cache:other",
        rate_limit_key=source_key,
        cooldown_key="cooldown:coinglass:futures_funding_rate",
        single_flight_key="sf:other",
    )

    owner = gate.enter(first_endpoint)
    assert owner.kind == "owner"
    gate.mark_cooldown_after_fetch(
        first_endpoint,
        SimpleNamespace(
            status="rate_limited",
            http_observations=(
                SimpleNamespace(response_headers_redacted={"retry-after": "120"}),
            ),
        ),
    )

    same_endpoint = gate.enter(first_endpoint)
    assert same_endpoint.kind == "cooldown_skipped"

    other_owner = gate.enter(other_endpoint)
    assert other_owner.kind == "owner"


def test_gate_uses_single_flight_shared_result_for_waiter() -> None:
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    batch = _Batch()
    owner = gate.enter(batch)
    assert owner.kind == "owner"

    published = IngestResult(
        status="ingested",
        dataset_refs=("dataset:owner",),
        raw_refs=("raw:owner",),
        attempt_refs=("attempt:owner",),
        gaps=(),
        remote_success=True,
    )
    assert gate.publish_shared_result(batch.single_flight_key, owner.owner_token or "", published) is True

    consumer = gate.enter(batch)
    assert consumer.kind in {"cache_hit", "shared_result"}
    assert consumer.refs.dataset_refs == ("dataset:owner",)


def test_gate_records_empty_owner_result_as_cached_empty() -> None:
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=SingleFlight())
    batch = _Batch()
    owner = gate.enter(batch)
    assert owner.kind == "owner"
    failed = IngestResult(
        status="failed",
        dataset_refs=(),
        raw_refs=("raw:empty",),
        attempt_refs=("attempt:empty",),
        gaps=(DataGap.by_reason("empty_result"),),
        remote_success=False,
        cache_key=batch.cache_key,
    )
    assert gate.publish_shared_result(batch.single_flight_key, owner.owner_token or "", failed) is True
    decision = gate.enter(batch)
    assert decision.kind == "cached_empty"


def test_gate_persists_non_remote_state_across_instances_with_same_repository() -> None:
    repository = DatasetRepository()
    batch = _Batch()
    gate_owner = ExecutionGate(
        cache=ProviderResultCache(repository=repository),
        rate_limiter=RateLimiter(repository=repository),
        single_flight=SingleFlight(repository=repository),
    )
    owner = gate_owner.enter(batch)
    assert owner.kind == "owner"
    published = IngestResult(
        status="failed",
        dataset_refs=(),
        raw_refs=("raw:empty",),
        attempt_refs=("attempt:empty",),
        gaps=(DataGap.by_reason("empty_result"),),
        remote_success=False,
        cache_key=batch.cache_key,
    )
    assert gate_owner.publish_shared_result(batch.single_flight_key, owner.owner_token or "", published) is True

    gate_consumer = ExecutionGate(
        cache=ProviderResultCache(repository=repository),
        rate_limiter=RateLimiter(repository=repository),
        single_flight=SingleFlight(repository=repository),
    )
    reused = gate_consumer.enter(batch)
    assert reused.kind == "cached_empty"
    assert reused.refs.attempt_refs == ("attempt:empty",)
