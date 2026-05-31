from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    GapSeverity,
    PackRequest,
    ProviderAttempt,
    ProviderCallResult,
    ProviderCallSpec,
    ProviderResult,
    ProviderStatus,
    provider_status_requires_gap,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.run_plan import RateLimitPlanMetadata
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore
from claw_trade.data_gateway.store.mongo import mongo_ref
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.single_flight import MongoSingleFlightCoordinator


@dataclass(frozen=True)
class GateRateLimitPolicy:
    metadata: RateLimitPlanMetadata
    hard_limit: int | None = None


def run_provider_call_gate(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    cache_store: MongoCacheStore,
    rate_limit_store: MongoRateLimitStore,
    single_flight: MongoSingleFlightCoordinator,
    attempt_store: MongoAttemptStore,
    owner_call: Callable[[], ProviderResult],
    rate_limit_policy: GateRateLimitPolicy | None = None,
) -> ProviderCallResult:
    cache_decision = cache_store.get(spec, request)
    if cache_decision.status == ProviderStatus.CACHE_HIT:
        return _handle_cache_hit(
            request=request,
            spec=spec,
            cache_store=cache_store,
            cache_decision=cache_decision,
            attempt_store=attempt_store,
        )
    if cache_decision.status == ProviderStatus.CACHED_EMPTY:
        return _handle_cached_empty(
            request=request,
            spec=spec,
            cache_store=cache_store,
            cache_decision=cache_decision,
            attempt_store=attempt_store,
        )

    resolved_policy = rate_limit_policy or GateRateLimitPolicy(metadata=RateLimitPlanMetadata())
    reserve_allowed = rate_limit_store.reserve(
        spec,
        request,
        limit=resolved_policy.hard_limit,
        window_seconds=resolved_policy.metadata.window_seconds,
    )
    if not reserve_allowed:
        return _handle_rate_limited(
            request=request,
            spec=spec,
            attempt_store=attempt_store,
            rate_limit_store=rate_limit_store,
            window_seconds=resolved_policy.metadata.window_seconds,
        )

    result = single_flight.run(request=request, spec=spec, fn=owner_call)
    if result.status == ProviderStatus.SHARED_RESULT:
        return _result_from_shared_result(request=request, spec=spec, result=result)
    if result.status == ProviderStatus.REMOTE_SUCCESS:
        cache_store.put_success(
            spec=spec,
            request=request,
            raw_ref=result.raw_ref or "",
            normalized_ref=result.normalized_ref or "",
            evidence_hash=f"attempt:{result.attempt.attempt_id}",
            ttl_seconds=spec.cache_ttl_seconds,
        )
        return _result_from_owner_result(
            request=request,
            spec=spec,
            status=ProviderStatus.REMOTE_SUCCESS,
            attempt_ref=result.attempt.attempt_id,
            raw_ref=result.raw_ref,
            normalized_ref=result.normalized_ref,
            data_gaps=(),
        )
    if result.status in {ProviderStatus.EMPTY, ProviderStatus.CACHED_EMPTY}:
        cache_store.put_empty(
            spec=spec,
            request=request,
            ttl_seconds=spec.cache_ttl_seconds,
            evidence_hash=f"attempt:{result.attempt.attempt_id}",
        )
        gap = _gap_for_status(
            request=request,
            spec=spec,
            status=ProviderStatus.CACHED_EMPTY,
            attempt_ref=result.attempt.attempt_id,
            root_cause=result.error_message or "provider returned empty payload",
            evidence_refs=(cache_store.cache_entry_ref(spec, request),),
        )
        return _result_from_owner_result(
            request=request,
            spec=spec,
            status=ProviderStatus.CACHED_EMPTY,
            attempt_ref=result.attempt.attempt_id,
            raw_ref=None,
            normalized_ref=None,
            data_gaps=(gap,),
            cache_entry_ref=cache_store.cache_entry_ref(spec, request),
        )
    return _result_from_provider_failure(
        request=request,
        spec=spec,
        result=result,
    )


def _handle_cache_hit(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    cache_store: MongoCacheStore,
    cache_decision: object,
    attempt_store: MongoAttemptStore,
) -> ProviderCallResult:
    attempt = _build_attempt(
        request=request,
        spec=spec,
        status=ProviderStatus.CACHE_HIT,
        from_cache=True,
        cache_status=ProviderStatus.CACHE_HIT,
        raw_ref=getattr(cache_decision, "usable_raw_ref"),
        normalized_ref=getattr(cache_decision, "usable_normalized_ref"),
        error_code=None,
        error_message=None,
    )
    attempt_ref = attempt_store.write(attempt)
    return ProviderCallResult(
        result_id=_result_id(request=request, spec=spec, status=ProviderStatus.CACHE_HIT),
        spec=spec,
        status=ProviderStatus.CACHE_HIT,
        rows=(),
        raw_payload_ref=attempt.raw_ref,
        normalized_ref=attempt.normalized_ref,
        attempt_ref=attempt_ref,
        http_evidence_refs=(),
        cache_entry_ref=cache_store.cache_entry_ref(spec, request),
        shared_owner_attempt_ref=None,
        data_gaps=(),
        remote_success=False,
        created_at=datetime.now(tz=UTC),
    )


def _handle_cached_empty(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    cache_store: MongoCacheStore,
    cache_decision: object,
    attempt_store: MongoAttemptStore,
) -> ProviderCallResult:
    attempt = _build_attempt(
        request=request,
        spec=spec,
        status=ProviderStatus.CACHED_EMPTY,
        from_cache=True,
        cache_status=ProviderStatus.CACHED_EMPTY,
        raw_ref=None,
        normalized_ref=None,
        error_code=ProviderStatus.CACHED_EMPTY.value,
        error_message="cached empty entry reused",
    )
    attempt_ref = attempt_store.write(attempt)
    gap = _gap_for_status(
        request=request,
        spec=spec,
        status=ProviderStatus.CACHED_EMPTY,
        attempt_ref=attempt_ref,
        root_cause=getattr(cache_decision, "reason", None) or "cached empty entry reused",
        evidence_refs=(cache_store.cache_entry_ref(spec, request),),
    )
    return ProviderCallResult(
        result_id=_result_id(request=request, spec=spec, status=ProviderStatus.CACHED_EMPTY),
        spec=spec,
        status=ProviderStatus.CACHED_EMPTY,
        rows=(),
        raw_payload_ref=None,
        normalized_ref=None,
        attempt_ref=attempt_ref,
        http_evidence_refs=(),
        cache_entry_ref=cache_store.cache_entry_ref(spec, request),
        shared_owner_attempt_ref=None,
        data_gaps=(gap,),
        remote_success=False,
        created_at=datetime.now(tz=UTC),
    )


def _handle_rate_limited(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    attempt_store: MongoAttemptStore,
    rate_limit_store: MongoRateLimitStore,
    window_seconds: int,
) -> ProviderCallResult:
    attempt = _build_attempt(
        request=request,
        spec=spec,
        status=ProviderStatus.RATE_LIMITED,
        from_cache=False,
        cache_status=None,
        raw_ref=None,
        normalized_ref=None,
        error_code=ProviderStatus.RATE_LIMITED.value,
        error_message="rate limit blocked before remote call",
    )
    attempt_ref = attempt_store.write(attempt)
    rate_limit_ref = _rate_limit_ref(spec=spec, collection_name=rate_limit_store.collection_name, window_seconds=window_seconds)
    gap = _gap_for_status(
        request=request,
        spec=spec,
        status=ProviderStatus.RATE_LIMITED,
        attempt_ref=attempt_ref,
        root_cause="rate limit blocked before remote call",
        evidence_refs=(rate_limit_ref,),
    )
    return ProviderCallResult(
        result_id=_result_id(request=request, spec=spec, status=ProviderStatus.RATE_LIMITED),
        spec=spec,
        status=ProviderStatus.RATE_LIMITED,
        rows=(),
        raw_payload_ref=None,
        normalized_ref=None,
        attempt_ref=attempt_ref,
        http_evidence_refs=(),
        cache_entry_ref=None,
        shared_owner_attempt_ref=None,
        data_gaps=(gap,),
        remote_success=False,
        created_at=datetime.now(tz=UTC),
    )


def _result_from_shared_result(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    result: ProviderResult,
) -> ProviderCallResult:
    return ProviderCallResult(
        result_id=_result_id(request=request, spec=spec, status=ProviderStatus.SHARED_RESULT),
        spec=spec,
        status=ProviderStatus.SHARED_RESULT,
        rows=result.rows,
        raw_payload_ref=result.raw_ref,
        normalized_ref=result.normalized_ref,
        attempt_ref=result.attempt.attempt_id,
        http_evidence_refs=(),
        cache_entry_ref=None,
        shared_owner_attempt_ref=result.attempt.shared_from_attempt_id,
        data_gaps=(),
        remote_success=False,
        created_at=datetime.now(tz=UTC),
    )


def _result_from_owner_result(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    status: ProviderStatus,
    attempt_ref: str,
    raw_ref: str | None,
    normalized_ref: str | None,
    data_gaps: tuple[DataGap, ...],
    cache_entry_ref: str | None = None,
) -> ProviderCallResult:
    return ProviderCallResult(
        result_id=_result_id(request=request, spec=spec, status=status),
        spec=spec,
        status=status,
        rows=(),
        raw_payload_ref=raw_ref,
        normalized_ref=normalized_ref,
        attempt_ref=attempt_ref,
        http_evidence_refs=(),
        cache_entry_ref=cache_entry_ref,
        shared_owner_attempt_ref=None,
        data_gaps=data_gaps,
        remote_success=status == ProviderStatus.REMOTE_SUCCESS,
        created_at=datetime.now(tz=UTC),
    )


def _result_from_provider_failure(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    result: ProviderResult,
) -> ProviderCallResult:
    data_gaps: tuple[DataGap, ...]
    if provider_status_requires_gap(result.status):
        gap = _gap_for_status(
            request=request,
            spec=spec,
            status=result.status,
            attempt_ref=result.attempt.attempt_id,
            root_cause=result.error_message or result.status.value,
            evidence_refs=(),
        )
        data_gaps = (gap,)
    else:
        data_gaps = ()
    return ProviderCallResult(
        result_id=_result_id(request=request, spec=spec, status=result.status),
        spec=spec,
        status=result.status,
        rows=result.rows,
        raw_payload_ref=result.raw_ref,
        normalized_ref=result.normalized_ref,
        attempt_ref=result.attempt.attempt_id,
        http_evidence_refs=(),
        cache_entry_ref=None,
        shared_owner_attempt_ref=result.attempt.shared_from_attempt_id,
        data_gaps=data_gaps,
        remote_success=False,
        created_at=datetime.now(tz=UTC),
    )


def _build_attempt(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    status: ProviderStatus,
    from_cache: bool,
    cache_status: ProviderStatus | None,
    raw_ref: str | None,
    normalized_ref: str | None,
    error_code: str | None,
    error_message: str | None,
) -> ProviderAttempt:
    now_text = utc_now_iso()
    attempt_id = f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}:gate:{status.value}:{uuid4().hex[:10]}"
    return ProviderAttempt(
        attempt_id=attempt_id,
        run_id=request.run_id,
        call_id=request.call_id,
        worker_id=request.worker_id,
        pack=request.domain.value,
        provider=spec.provider,
        adapter_id=spec.adapter_id,
        adapter_kind=spec.provider_kind.value,
        provider_kind=spec.provider_kind,
        provider_config_version=spec.provider_config_version,
        endpoint=spec.endpoint,
        source_role=spec.source_role,
        started_at=now_text,
        finished_at=now_text,
        status=status,
        required=spec.required,
        attempt_required=spec.attempt_required,
        coverage_group=spec.coverage_group,
        coverage_quorum=spec.coverage_quorum,
        priority_source=spec.priority_source,
        user_preferred=spec.user_preferred,
        from_cache=from_cache,
        cache_status=cache_status,
        single_flight_role="none",
        shared_from_attempt_id=None,
        latency_ms=0,
        row_count=None,
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        error_code=error_code,
        error_message=error_message,
        schema_id=spec.expected_schema_id,
        license_note=_license_note_for_status(status),
    )


def _gap_for_status(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    status: ProviderStatus,
    attempt_ref: str,
    root_cause: str,
    evidence_refs: tuple[str, ...],
) -> DataGap:
    reason = _gap_reason_for_status(status)
    severity = GapSeverity.FAIL if spec.required else GapSeverity.WARN
    return DataGap(
        gap_id=f"{request.run_id}:{request.call_id}:{spec.call_key}:{status.value}:gap",
        domain=request.domain,
        severity=severity,
        reason=reason,
        field_path=request.domain.value,
        provider_candidates=(spec.provider,),
        attempt_ids=(attempt_ref,),
        root_cause=root_cause,
        next_action=_next_action_for_reason(reason),
        requirement_id=spec.requirement_id,
        market=request.market,
        data_type=spec.data_type,
        ticker=request.ticker,
        evidence_refs=evidence_refs,
    )


def _gap_reason_for_status(status: ProviderStatus) -> DataGapReason:
    mapping = {
        ProviderStatus.REMOTE_ERROR: DataGapReason.REMOTE_ERROR,
        ProviderStatus.CREDENTIAL_MISSING: DataGapReason.CREDENTIAL_MISSING,
        ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
        ProviderStatus.COOLDOWN_SKIPPED: DataGapReason.COOLDOWN_SKIPPED,
        ProviderStatus.EMPTY: DataGapReason.EMPTY,
        ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
        ProviderStatus.FIELD_MISSING: DataGapReason.FIELD_MISSING,
        ProviderStatus.SCHEMA_INVALID: DataGapReason.SCHEMA_INVALID,
        ProviderStatus.NOT_APPLICABLE: DataGapReason.NOT_APPLICABLE,
        ProviderStatus.SDK_HTTP_UNKNOWN: DataGapReason.SDK_HTTP_UNKNOWN,
        ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
        ProviderStatus.LICENSE_BLOCKED: DataGapReason.LICENSE_BLOCKED,
    }
    return mapping.get(status, DataGapReason.REMOTE_ERROR)


def _next_action_for_reason(reason: DataGapReason) -> str:
    if reason == DataGapReason.RATE_LIMITED:
        return "wait for rate-limit window or lower request frequency"
    if reason == DataGapReason.CACHED_EMPTY:
        return "wait for cache ttl or retry with broader time range"
    if reason == DataGapReason.COOLDOWN_SKIPPED:
        return "wait for cooldown window before retrying"
    if reason == DataGapReason.CREDENTIAL_MISSING:
        return "configure required provider credentials"
    if reason == DataGapReason.FIELD_MISSING:
        return "try next provider candidate with required fields"
    if reason == DataGapReason.SDK_HTTP_UNKNOWN:
        return "collect provider-visible evidence before claiming success"
    return "inspect provider attempt and retry with next candidate"


def _license_note_for_status(status: ProviderStatus) -> str:
    if status == ProviderStatus.CACHE_HIT:
        return "cache hit"
    if status == ProviderStatus.CACHED_EMPTY:
        return "cached empty"
    if status == ProviderStatus.RATE_LIMITED:
        return "rate limited"
    return "approved"


def _rate_limit_ref(*, spec: ProviderCallSpec, collection_name: str, window_seconds: int) -> str:
    now = datetime.now(tz=UTC).replace(microsecond=0)
    window_start = _window_start(now, window_seconds)
    doc_id = f"{spec.provider}:{spec.endpoint}:{window_start.isoformat()}:{window_seconds}"
    return mongo_ref(collection_name, doc_id)


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = int((now - epoch).total_seconds())
    aligned = elapsed - (elapsed % window_seconds)
    return epoch + timedelta(seconds=aligned)


def _result_id(*, request: PackRequest, spec: ProviderCallSpec, status: ProviderStatus) -> str:
    return f"{request.run_id}:{request.call_id}:{spec.call_key}:{status.value}"
