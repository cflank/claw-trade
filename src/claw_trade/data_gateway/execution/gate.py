from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import sleep
from types import SimpleNamespace
from typing import Any, Callable, Protocol

from . import GateDecision, ProviderResultCache, ResultRefs
from .rate_limiter import RateLimiter
from .single_flight import SingleFlight

_LOCAL_QUOTA_SIGNALS = {"local_rate_limited", "rate_limited_by_tool_budget"}
_MATERIAL_RESULT_GAP_REASONS = {"field_missing", "date_range_missing", "data_integrity_failed", "granularity_mismatch"}
_SINGLE_FLIGHT_WAIT_CAP_SECONDS = 8


class GateBatchPlan(Protocol):
    cache_key: str
    rate_limit_key: str
    cooldown_key: str | None
    rate_limit_policy: Any
    http_visibility: Any
    single_flight_key: str
    lease_ttl_seconds: int
    deadline_at: datetime | None
    earliest_start_at: datetime | None
    ignore_cached_empty: bool
    ignore_provider_cache: bool


class ExecutionGate:
    def __init__(
        self,
        *,
        cache: ProviderResultCache,
        rate_limiter: RateLimiter,
        single_flight: SingleFlight,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.single_flight = single_flight
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn or sleep

    def enter(self, batch: GateBatchPlan) -> GateDecision:
        cache_lookup = self.cache.get(batch.cache_key)
        ignore_provider_cache = bool(getattr(batch, "ignore_provider_cache", False))
        if cache_lookup.state == "fresh_success" and cache_lookup.entry is not None and not ignore_provider_cache:
            return GateDecision.cache_hit(cache_lookup.entry.refs)
        if (
            cache_lookup.state == "fresh_empty"
            and cache_lookup.entry is not None
            and not ignore_provider_cache
            and not bool(getattr(batch, "ignore_cached_empty", False))
        ):
            return GateDecision.cached_empty(cache_lookup.entry.refs)

        cooldown_key = _cooldown_key(batch)
        if cooldown_key != batch.rate_limit_key or _rate_limit_at_http(batch):
            cooldown = self.rate_limiter.check_cooldown(
                cooldown_key,
                batch.rate_limit_policy,
                deadline_at=_deadline_at(batch),
            )
            if not cooldown.allowed:
                return GateDecision.cooldown_skipped(cooldown.retry_after, cooldown.reason or "cooldown_skipped")

        flight = self.single_flight.acquire(batch.single_flight_key, batch.lease_ttl_seconds)
        if flight.kind == "shared" and flight.published is not None:
            return _shared_result_decision(flight.published)
        if flight.kind == "waiter":
            wait_budget_seconds = _single_flight_wait_budget_seconds(batch, now=self._now())
            if wait_budget_seconds <= 0:
                return GateDecision.rate_limited(_deadline_at(batch), "rate_limited_by_tool_budget")
            try:
                published = self.single_flight.wait(batch.single_flight_key, wait_budget_seconds)
            except TimeoutError:
                return GateDecision.rate_limited(_deadline_at(batch), "rate_limited_by_tool_budget")
            return _shared_result_decision(published)

        if flight.kind != "owner" or flight.owner_token is None:
            raise RuntimeError("single-flight returned unsupported decision")

        scheduler_skip_reason = getattr(batch, "scheduler_skip_reason", None)
        if scheduler_skip_reason:
            retry_after = getattr(batch, "scheduler_retry_after", None)
            decision = GateDecision.rate_limited(
                retry_after if isinstance(retry_after, datetime) else None,
                str(scheduler_skip_reason),
            )
            self._publish_owner_gate_failure(batch, flight.owner_token, decision.reason or "rate_limited")
            return decision

        earliest = _earliest_start_at(batch)
        if earliest is not None:
            wait_decision = self._wait_until_earliest_start(batch=batch, earliest_start_at=earliest)
            if wait_decision is not None:
                self._publish_owner_gate_failure(batch, flight.owner_token, wait_decision.reason or "rate_limited")
                return wait_decision

        if not _rate_limit_at_http(batch) and not _rate_limit_pre_reserved(batch):
            quota = self.rate_limiter.reserve(
                batch.rate_limit_key,
                batch.rate_limit_policy,
                deadline_at=_deadline_at(batch),
            )
            if not quota.allowed:
                if quota.reason == "cooldown_skipped":
                    self._publish_owner_gate_failure(batch, flight.owner_token, quota.reason or "cooldown_skipped")
                    return GateDecision.cooldown_skipped(quota.retry_after, quota.reason or "cooldown_skipped")
                decision = GateDecision.rate_limited(quota.retry_after, quota.reason or "rate_limited")
                self._publish_owner_gate_failure(batch, flight.owner_token, decision.reason or "rate_limited")
                return decision

        return GateDecision.owner(flight.owner_token)

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: Any, *, batch: Any | None = None) -> bool:
        published = self.single_flight.publish(single_flight_key, owner_token, ingest)
        if not published:
            return False
        now = datetime.now(UTC)
        default_fresh_seconds, default_stale_seconds = _default_cache_window_seconds(batch)
        fresh_until = getattr(ingest, "cache_fresh_until", None) or (now + timedelta(seconds=default_fresh_seconds))
        stale_until = getattr(ingest, "cache_stale_until", None) or (now + timedelta(seconds=default_stale_seconds))
        if ingest.status in {"ingested", "partial"} and (ingest.dataset_refs or ingest.raw_refs) and not _has_material_result_gap(ingest):
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
        elif ingest.status in {"failed", "partial"} and any(
            str(getattr(getattr(g, "reason", None), "value", getattr(g, "reason", ""))) in {"provider_empty", "empty_result"}
            for g in ingest.gaps
        ):
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
        if until is not None:
            self.rate_limiter.mark_cooldown(_cooldown_key(batch), until=until, reason="provider_429")

    def wait_after_rate_limited_fetch(self, batch: GateBatchPlan, fetch_result: Any) -> bool:
        status = getattr(getattr(fetch_result, "status", None), "value", getattr(fetch_result, "status", None))
        if status != "rate_limited":
            return False
        policy = getattr(batch, "rate_limit_policy", None)
        now = datetime.now(UTC)
        until = _cooldown_until(fetch_result=fetch_result, batch=batch, now=now)
        if until is None:
            return False
        deadline_at = _deadline_at(batch)
        if deadline_at is None or until > deadline_at:
            return False
        self.rate_limiter.mark_cooldown(_cooldown_key(batch), until=until, reason="provider_429")
        return self.rate_limiter.check_cooldown(_cooldown_key(batch), policy, deadline_at=deadline_at).allowed

    def _publish_owner_gate_failure(self, batch: GateBatchPlan, owner_token: str, reason: str) -> None:
        self.single_flight.publish(
            batch.single_flight_key,
            owner_token,
            SimpleNamespace(
                status="failed",
                dataset_refs=(),
                raw_refs=(),
                attempt_refs=(),
                gaps=({"reason": reason},),
            ),
        )

    def _wait_until_earliest_start(self, *, batch: GateBatchPlan, earliest_start_at: datetime) -> GateDecision | None:
        earliest_start_at = _normalize_datetime(earliest_start_at)
        now = _normalize_datetime(self._now())
        if earliest_start_at <= now:
            return None
        deadline_at = _deadline_at(batch)
        if deadline_at is not None and earliest_start_at > deadline_at:
            return GateDecision.rate_limited(earliest_start_at, "rate_limited_by_tool_budget")
        wait_seconds = max((earliest_start_at - now).total_seconds(), 0)
        if wait_seconds > 0:
            self._sleep(wait_seconds)
        return None


def _cooldown_key(batch: GateBatchPlan) -> str:
    return str(getattr(batch, "cooldown_key", None) or batch.rate_limit_key)


def _rate_limit_at_http(batch: GateBatchPlan) -> bool:
    return _as_string(getattr(batch, "http_visibility", "") or "") == "managed_http"


def _rate_limit_pre_reserved(batch: GateBatchPlan) -> bool:
    return isinstance(getattr(batch, "rate_limit_reserved_at", None), datetime)


def _deadline_at(batch: GateBatchPlan) -> datetime | None:
    value = getattr(batch, "deadline_at", None)
    return value if isinstance(value, datetime) else None


def _earliest_start_at(batch: GateBatchPlan) -> datetime | None:
    value = getattr(batch, "earliest_start_at", None)
    return value if isinstance(value, datetime) else None


def _single_flight_wait_budget_seconds(batch: GateBatchPlan, *, now: datetime) -> int:
    deadline_at = _deadline_at(batch)
    if deadline_at is None:
        return 0
    remaining = int(max((deadline_at - now).total_seconds(), 0))
    return min(remaining, _SINGLE_FLIGHT_WAIT_CAP_SECONDS)


def _default_cache_window_seconds(batch: Any | None) -> tuple[int, int]:
    if batch is None:
        return 60, 300
    granularity = _as_string(getattr(batch, "granularity", "") or "").strip().lower()
    if granularity in {"realtime", "intraday", "1m", "5m", "15m", "30m"}:
        return 300, 900
    if granularity in {"hourly", "1h", "60m"}:
        return 600, 1800
    return 1800, 3600


def _as_string(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if value.tzinfo != UTC:
        return value.astimezone(UTC)
    return value


def _shared_result_decision(published: Any) -> GateDecision:
    refs = published.refs
    return GateDecision(
        kind="shared_result",
        refs=refs,
        evidence_refs=refs.attempt_refs,
        reason=_shared_result_reason(published),
    )


def _shared_result_reason(published: Any) -> str | None:
    gap_summary = tuple(getattr(published, "gap_summary", ()) or ())
    for item in gap_summary:
        if isinstance(item, dict):
            reason = item.get("reason")
        else:
            reason = getattr(item, "reason", None)
        text = _as_string(reason).strip()
        if text and text != "None":
            return text
    error_summary = str(getattr(published, "error_summary", "") or "").strip()
    return error_summary or None


def _has_material_result_gap(ingest: Any) -> bool:
    for gap in tuple(getattr(ingest, "gaps", ()) or ()):
        raw_reason = getattr(gap, "reason", "")
        reason = str(getattr(raw_reason, "value", raw_reason))
        if reason in _MATERIAL_RESULT_GAP_REASONS:
            return True
    return False


def _cooldown_until(*, fetch_result: Any, batch: GateBatchPlan, now: datetime) -> datetime | None:
    saw_provider_quota_signal = False
    for observation in tuple(getattr(fetch_result, "http_observations", ()) or ()):
        if getattr(observation, "quota_signal", None) in _LOCAL_QUOTA_SIGNALS:
            continue
        if getattr(observation, "status_code", None) == 429 or getattr(observation, "quota_signal", None):
            saw_provider_quota_signal = True
        headers = getattr(observation, "response_headers_redacted", None)
        if not isinstance(headers, dict):
            continue
        retry_after = _retry_after_until(headers.get("retry-after"), now=now)
        if retry_after is not None:
            return retry_after
    if not saw_provider_quota_signal:
        return None
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
