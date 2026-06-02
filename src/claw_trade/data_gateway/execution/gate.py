from __future__ import annotations

from email.utils import parsedate_to_datetime
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from . import GateDecision, ProviderResultCache, ResultRefs
from .rate_limiter import RateLimiter
from .single_flight import SingleFlight


class GateBatchPlan(Protocol):
    cache_key: str
    rate_limit_key: str
    rate_limit_policy: Any
    single_flight_key: str
    lease_ttl_seconds: int
    wait_timeout_seconds: int


class ExecutionGate:
    def __init__(
        self,
        *,
        cache: ProviderResultCache,
        rate_limiter: RateLimiter,
        single_flight: SingleFlight,
    ) -> None:
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.single_flight = single_flight

    def enter(self, batch: GateBatchPlan) -> GateDecision:
        cache_lookup = self.cache.get(batch.cache_key)
        if cache_lookup.state == "fresh_success" and cache_lookup.entry is not None:
            return GateDecision.cache_hit(cache_lookup.entry.refs)
        if cache_lookup.state == "fresh_empty" and cache_lookup.entry is not None:
            return GateDecision.cached_empty(cache_lookup.entry.refs)

        quota = self.rate_limiter.reserve(batch.rate_limit_key, batch.rate_limit_policy)
        if not quota.allowed:
            if quota.reason == "cooldown_skipped":
                return GateDecision.cooldown_skipped(quota.retry_after, quota.reason or "cooldown_skipped")
            return GateDecision.rate_limited(quota.retry_after, quota.reason or "rate_limited")

        flight = self.single_flight.acquire(batch.single_flight_key, batch.lease_ttl_seconds)
        if flight.kind == "shared" and flight.published is not None:
            return GateDecision.shared_result(flight.published.refs)
        if flight.kind == "waiter":
            published = self.single_flight.wait(batch.single_flight_key, batch.wait_timeout_seconds)
            return GateDecision.shared_result(published.refs)
        if flight.kind == "owner" and flight.owner_token is not None:
            return GateDecision.owner(flight.owner_token)
        raise RuntimeError("single-flight returned unsupported decision")

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: Any) -> bool:
        published = self.single_flight.publish(single_flight_key, owner_token, ingest)
        if not published:
            return False
        now = datetime.now(UTC)
        fresh_until = getattr(ingest, "cache_fresh_until", None) or (now + timedelta(seconds=60))
        stale_until = getattr(ingest, "cache_stale_until", None) or (now + timedelta(seconds=300))
        if ingest.status in {"ingested", "partial"} and ingest.dataset_refs:
            refs = ResultRefs(
                dataset_refs=tuple(ingest.dataset_refs),
                raw_refs=tuple(ingest.raw_refs),
                attempt_refs=tuple(ingest.attempt_refs),
            )
            self.cache.put_remote_success(
                cache_key=getattr(ingest, "cache_key", single_flight_key),
                refs=refs,
                fresh_until=fresh_until,
                stale_until=stale_until,
            )
        elif ingest.status in {"failed", "partial"} and any(getattr(g, "reason", None) == "empty_result" for g in ingest.gaps):
            refs = ResultRefs(
                dataset_refs=(),
                raw_refs=tuple(ingest.raw_refs),
                attempt_refs=tuple(ingest.attempt_refs),
            )
            self.cache.put_cached_empty(
                cache_key=getattr(ingest, "cache_key", single_flight_key),
                refs=refs,
                fresh_until=fresh_until,
                stale_until=stale_until,
            )
        return True

    def mark_cooldown_after_fetch(self, batch: GateBatchPlan, fetch_result: Any) -> None:
        status = getattr(getattr(fetch_result, "status", None), "value", getattr(fetch_result, "status", None))
        if status != "rate_limited":
            return
        now = datetime.now(UTC)
        until = _cooldown_until(fetch_result=fetch_result, batch=batch, now=now)
        self.rate_limiter.mark_cooldown(batch.rate_limit_key, until=until, reason="provider_429")


def _cooldown_until(*, fetch_result: Any, batch: GateBatchPlan, now: datetime) -> datetime:
    for observation in tuple(getattr(fetch_result, "http_observations", ()) or ()):
        headers = getattr(observation, "response_headers_redacted", None)
        if not isinstance(headers, dict):
            continue
        retry_after = _retry_after_until(headers.get("retry-after"), now=now)
        if retry_after is not None:
            return retry_after
    window_seconds = int(getattr(getattr(batch, "rate_limit_policy", None), "window_seconds", 60) or 60)
    return now + timedelta(seconds=max(window_seconds, 1))


def _retry_after_until(value: Any, *, now: datetime) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return now + timedelta(seconds=max(int(text), 0))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
