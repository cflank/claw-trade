from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from time import perf_counter
from typing import Any, Mapping

from cache import CacheInspectionCallState, CacheInspectionResult, CacheKey, inspect_provider_cache, upsert_provider_cache
from config import SOCIAL_SCHEMA_VERSION, SocialDataConfig, load_social_data_config
from evidence import (
    EvidenceWriteTarget,
    validate_signal_evidence,
    write_pack_evidence,
    try_write_raw_payload_evidence,
)
from matching import SocialMatchingError, match_deduplicate_and_bucket
from observability import emit_json_log, record_metric
from pack_schema import (
    ProviderAttempt,
    ProviderExecutionResult,
    PublicSocialProfile,
    QueryPlanView,
    SocialEvidence,
    SocialQuality,
    SocialSentimentPack,
    canonical_json_sha256,
)
from quality import QualityDecision, QualityInput, evaluate_social_quality
from reader_brief import ReaderBrief, ReaderBriefInput, SocialBriefError, build_reader_brief
from profile import (
    SOCIAL_INVALID_INPUT,
    SOCIAL_INVALID_TICKER_MARKET_PREFIX,
    ResolveDateWindow,
    SocialProfileError,
    SocialToolInput,
    build_query_keywords,
    resolve_social_target_profile,
)
from providers import (
    ProviderQuery,
    RawProviderResult,
    SocialProviderNormalizationError,
    SocialProviderPlanError,
    build_provider_plan,
    fetch_provider_payload,
    load_rows_by_raw_payload_ref,
    normalize_provider_rows,
    normalize_provider_payload,
)

SOCIAL_UNSUPPORTED_MARKET = "SOCIAL_UNSUPPORTED_MARKET"
SOCIAL_INVALID_TICKER = "SOCIAL_INVALID_TICKER"
SOCIAL_PROFILE_RESOLVE_FAILED = "SOCIAL_PROFILE_RESOLVE_FAILED"
SOCIAL_PROVIDER_PLAN_EMPTY = "SOCIAL_PROVIDER_PLAN_EMPTY"
SOCIAL_PACK_DEADLINE_EXCEEDED = "SOCIAL_PACK_DEADLINE_EXCEEDED"
SOCIAL_RAW_PAYLOAD_WRITE_FAILED = "SOCIAL_RAW_PAYLOAD_WRITE_FAILED"
SOCIAL_PACK_EVIDENCE_WRITE_FAILED = "SOCIAL_PACK_EVIDENCE_WRITE_FAILED"
SOCIAL_EVIDENCE_REF_MISSING = "SOCIAL_EVIDENCE_REF_MISSING"

_TOOL_NAME = "social_social_sentiment_pack"
_WORKER_ID = "social_analyst"
_STAGE = "frontline"


@dataclass(frozen=True)
class SocialToolRuntimeContext:
    run_id: str
    stage: str
    worker_id: str
    call_id: str
    tool_name: str
    evidence_root: str
    current_time: str


def build_social_sentiment_pack(
    tool_input: SocialToolInput,
    context: SocialToolRuntimeContext,
    config: SocialDataConfig | None = None,
) -> SocialSentimentPack:
    started_at = perf_counter()
    record_metric(
        "social.pack.call.count",
        tags={
            "worker_id": context.worker_id,
            "call_id": context.call_id,
            "run_id": context.run_id,
        },
    )
    resolved_config = config or load_social_data_config(os.environ)

    runtime_validation_error = _validate_runtime_context(context)
    if runtime_validation_error is not None:
        return _build_failed_pack(
            code=SOCIAL_INVALID_INPUT,
            message=runtime_validation_error,
            tool_input=tool_input,
            context=context,
            started_at=started_at,
        )

    if tool_input.market.strip().upper() != "CN_A":
        return _build_failed_pack(
            code=SOCIAL_UNSUPPORTED_MARKET,
            message=f"market 必须为 CN_A: {tool_input.market}",
            tool_input=tool_input,
            context=context,
            started_at=started_at,
        )

    try:
        target_profile = resolve_social_target_profile(tool_input)
    except SocialProfileError as exc:
        failure_code = SOCIAL_INVALID_TICKER if exc.code == SOCIAL_INVALID_TICKER_MARKET_PREFIX else SOCIAL_PROFILE_RESOLVE_FAILED
        return _build_failed_pack(
            code=failure_code,
            message=exc.message,
            tool_input=tool_input,
            context=context,
            started_at=started_at,
        )

    try:
        date_window = ResolveDateWindow(
            start_date=tool_input.start_date,
            end_date=tool_input.end_date,
            current_time=context.current_time,
        )
    except SocialProfileError as exc:
        return _build_failed_pack(
            code=SOCIAL_INVALID_INPUT,
            message=exc.message,
            tool_input=tool_input,
            context=context,
            started_at=started_at,
        )

    try:
        provider_plan = build_provider_plan(target_profile, date_window, resolved_config)
    except SocialProviderPlanError as exc:
        return _build_failed_pack(
            code=exc.code,
            message=exc.message,
            tool_input=tool_input,
            context=context,
            started_at=started_at,
        )
    if not provider_plan:
        return _build_failed_pack(
            code=SOCIAL_PROVIDER_PLAN_EMPTY,
            message="provider 计划为空",
            tool_input=tool_input,
            context=context,
            started_at=started_at,
        )

    query_plan = QueryPlanView(
        start_date=date_window.start_date,
        end_date=date_window.end_date,
        keywords=build_query_keywords(target_profile),
        provider_endpoints=tuple(query.endpoint for query in provider_plan),
    )
    cache_call_state = CacheInspectionCallState()
    deadline_at = started_at + max(1, resolved_config.pack_timeout_seconds)
    execution_results, cache_inspections, processed_count = _execute_provider_plan(
        provider_plan=provider_plan,
        target_ticker=target_profile.ticker,
        target_ticker_plain=target_profile.ticker_plain,
        date_window=date_window,
        context=context,
        config=resolved_config,
        cache_call_state=cache_call_state,
        deadline_at=deadline_at,
        started_at=started_at,
    )

    if processed_count < len(provider_plan):
        cancelled_results = _mark_unfinished_queries_cancelled(
            provider_plan=provider_plan,
            start_index=processed_count,
            target_ticker=target_profile.ticker,
            date_window=date_window,
            schema_version=resolved_config.schema_version,
            started_at=started_at,
        )
        execution_results.extend(cancelled_results)
        cache_inspections.extend(result.cache_inspection for result in cancelled_results)

    profile = PublicSocialProfile(
        ticker=target_profile.ticker,
        company_name=target_profile.company_name,
        market="CN_A",
        industry=target_profile.industry,
    )
    attempts = [result.attempt for result in execution_results]
    raw_payload_refs = _collect_raw_payload_refs(execution_results)

    try:
        normalized_rows = normalize_provider_rows(execution_results)
        bucketed = match_deduplicate_and_bucket(normalized_rows, target_profile, resolved_config)
        quality_decision = evaluate_social_quality(
            QualityInput(
                attempts=tuple(attempts),
                buckets=bucketed,
                required_p0_endpoints=_required_p0_endpoints(provider_plan),
            )
        )
        brief = build_reader_brief(
            ReaderBriefInput(
                profile=target_profile,
                attempts=tuple(attempts),
                buckets=bucketed,
                quality=quality_decision,
                date_window=date_window,
            )
        )
    except SocialProviderNormalizationError as exc:
        return _build_failed_pack_from_pipeline(
            code=exc.code,
            message=exc.message,
            profile=profile,
            query_plan=query_plan,
            attempts=attempts,
            data=_empty_data_buckets(),
            context=context,
            raw_payload_refs=raw_payload_refs,
        )
    except SocialMatchingError as exc:
        return _build_failed_pack_from_pipeline(
            code=exc.code,
            message=exc.message,
            profile=profile,
            query_plan=query_plan,
            attempts=attempts,
            data=_empty_data_buckets(),
            context=context,
            raw_payload_refs=raw_payload_refs,
        )
    except SocialBriefError as exc:
        return _build_failed_pack_from_pipeline(
            code=exc.code,
            message=exc.message,
            profile=profile,
            query_plan=query_plan,
            attempts=attempts,
            data=_empty_data_buckets(),
            context=context,
            raw_payload_refs=raw_payload_refs,
        )

    data = _bucketed_signals_to_dict(bucketed)
    quality = _quality_decision_to_pack_quality(quality_decision=quality_decision, data=data)
    pack_body = _build_pack_body(
        profile=profile,
        query_plan=query_plan,
        attempts=attempts,
        data=data,
        quality=quality,
        brief=brief,
    )
    pack_body_hash = canonical_json_sha256(pack_body)
    evidence_target = EvidenceWriteTarget(
        run_id=context.run_id,
        stage=context.stage,
        worker_id=context.worker_id,
        call_id=context.call_id,
        evidence_root=context.evidence_root,
        openviking_l2_write_target_root=resolved_config.openviking_l2_write_target_root,
    )
    evidence_result = write_pack_evidence(
        target=evidence_target,
        pack_body=pack_body,
        attempts=attempts,
        cache_inspections=cache_inspections,
        pack_body_hash=pack_body_hash,
    )
    if evidence_result is None:
        error_code = SOCIAL_PACK_EVIDENCE_WRITE_FAILED
    elif not evidence_result.ok or evidence_result.evidence is None:
        error_code = evidence_result.cause_error_code or evidence_result.error_code or SOCIAL_PACK_EVIDENCE_WRITE_FAILED
    else:
        error_code = None

    if error_code is not None:
        emit_json_log(
            level="ERROR",
            event="social.evidence.write_pack.failed",
            code=error_code,
            run_id=context.run_id,
            call_id=context.call_id,
            worker_id=context.worker_id,
            endpoint="pack",
        )
        return _build_failed_pack_from_pipeline(
            code=error_code,
            message="pack evidence 写入失败",
            profile=profile,
            query_plan=query_plan,
            attempts=attempts,
            data=data,
            context=context,
            raw_payload_refs=raw_payload_refs,
        )

    evidence_payload = evidence_result.evidence  # type: ignore[union-attr]
    final_evidence = _finalize_pack_evidence(evidence_payload)
    pack = SocialSentimentPack(
        ok=quality.status != "failed",
        profile=profile,
        query_plan=query_plan,
        provider_attempts=attempts,
        data=data,
        quality=quality,
        reader_brief=brief.text,
        evidence=final_evidence,
    )
    if pack.quality.status != "failed" and _has_accepted_signal_missing_evidence(pack.data):
        downgraded_pack = _downgrade_to_failed(
            pack=pack,
            code=SOCIAL_EVIDENCE_REF_MISSING,
            message="accepted signal 缺少 raw_payload_ref 或 content_hash",
        )
        _record_pack_completion(downgraded_pack, context=context, started_at=started_at)
        return downgraded_pack
    _record_pack_completion(pack, context=context, started_at=started_at)
    return pack


def _execute_provider_plan(
    *,
    provider_plan: list[ProviderQuery],
    target_ticker: str,
    target_ticker_plain: str,
    date_window: Any,
    context: SocialToolRuntimeContext,
    config: SocialDataConfig,
    cache_call_state: CacheInspectionCallState,
    deadline_at: float,
    started_at: float,
) -> tuple[list[ProviderExecutionResult], list[dict[str, Any]], int]:
    execution_results: list[ProviderExecutionResult] = []
    cache_inspections: list[dict[str, Any]] = []
    processed_count = 0
    max_workers = max(1, min(config.provider_max_concurrency, len(provider_plan)))
    pending: list[Future[ProviderExecutionResult]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for query in provider_plan:
            if _deadline_exceeded(deadline_at):
                break
            pending.append(
                executor.submit(
                    _execute_provider_query,
                    query=query,
                    target_ticker=target_ticker,
                    target_ticker_plain=target_ticker_plain,
                    date_window=date_window,
                    context=context,
                    config=config,
                    cache_call_state=cache_call_state,
                    started_at=started_at,
                )
            )
            processed_count += 1
            if len(pending) >= max_workers:
                _collect_provider_futures(pending, execution_results, cache_inspections)
                pending.clear()

        if pending:
            _collect_provider_futures(pending, execution_results, cache_inspections)

    return execution_results, cache_inspections, processed_count


def _collect_provider_futures(
    futures: list[Future[ProviderExecutionResult]],
    execution_results: list[ProviderExecutionResult],
    cache_inspections: list[dict[str, Any]],
) -> None:
    for future in futures:
        execution = future.result()
        execution_results.append(execution)
        cache_inspections.append(execution.cache_inspection)


def _execute_provider_query(
    *,
    query: ProviderQuery,
    target_ticker: str,
    target_ticker_plain: str,
    date_window: Any,
    context: SocialToolRuntimeContext,
    config: SocialDataConfig,
    cache_call_state: CacheInspectionCallState,
    started_at: float,
) -> ProviderExecutionResult:
    now_iso = datetime.now(UTC).isoformat()
    cache_key = _build_cache_key(
        query=query,
        target_ticker=target_ticker,
        date_window=date_window,
        schema_version=config.schema_version,
    )
    cache_result = inspect_provider_cache(
        key=cache_key,
        now_iso=now_iso,
        config=config,
        call_state=cache_call_state,
    )
    record_metric(
        "social.cache.inspect.count",
        tags={"endpoint": query.endpoint, "status": cache_result.status},
    )
    emit_json_log(
        level="INFO",
        event="social.cache.inspect",
        code=cache_result.reason or cache_result.status,
        run_id=context.run_id,
        call_id=context.call_id,
        worker_id=context.worker_id,
        endpoint=query.endpoint,
        fields={"cache_status": cache_result.status},
    )

    if cache_result.status == "hit":
        return _execute_from_cache_hit(
            query=query,
            cache_result=cache_result,
            started_at=started_at,
        )

    live_result = fetch_provider_payload(query, ticker_plain=target_ticker_plain)
    if live_result.ok:
        target = EvidenceWriteTarget(
            run_id=context.run_id,
            stage=context.stage,
            worker_id=context.worker_id,
            call_id=context.call_id,
            evidence_root=context.evidence_root,
            openviking_l2_write_target_root=config.openviking_l2_write_target_root,
        )
        raw_evidence = try_write_raw_payload_evidence(
            target=target,
            provider=query.provider,
            endpoint=query.endpoint,
            raw_payload=live_result.raw_payload,
        )
        if raw_evidence is None:
            error_code = SOCIAL_RAW_PAYLOAD_WRITE_FAILED
        elif not raw_evidence.ok or raw_evidence.evidence is None:
            error_code = raw_evidence.error_code or SOCIAL_RAW_PAYLOAD_WRITE_FAILED
        else:
            error_code = None

        if error_code is not None:
            emit_json_log(
                level="ERROR",
                event="social.evidence.write_raw.failed",
                code=error_code,
                run_id=context.run_id,
                call_id=context.call_id,
                worker_id=context.worker_id,
                endpoint=query.endpoint,
            )
            attempt = _build_attempt_from_error(
                query=query,
                status="error",
                cache_status=cache_result.status,
                cache_key=cache_result.cache_key,
                raw_count=live_result.raw_count,
                empty_reason=error_code,
                error={"code": error_code, "message": "raw payload 证据写入失败"},
                elapsed_ms=_elapsed_ms(started_at),
            )
            return ProviderExecutionResult(
                query=_provider_query_to_dict(query),
                attempt=attempt,
                raw_rows=[],
                raw_payload_ref=None,
                payload_hash=None,
                cache_inspection=_cache_inspection_to_dict(cache_result),
            )

        raw_payload_evidence = raw_evidence.evidence  # type: ignore[union-attr]
        raw_ref = raw_payload_evidence.raw_payload_ref
        cache_write = upsert_provider_cache(
            key=cache_key,
            raw_result=asdict(live_result),
            raw_payload_ref=raw_ref,
            ttl_seconds=_resolve_endpoint_ttl(endpoint=query.endpoint, config=config),
            now_iso=now_iso,
            config=config,
        )
        record_metric(
            "social.cache.upsert.count",
            tags={"endpoint": query.endpoint, "status": cache_write.status},
        )
        rows = normalize_provider_payload(query=query, raw_result=live_result, raw_payload_ref=raw_ref)
        row_payload = _normalized_rows_to_dicts(rows)
        attempt = ProviderAttempt(
            provider=query.provider,
            endpoint=query.endpoint,
            priority=query.priority,
            query=_canonical_query_json(query.query),
            ok=True,
            status="success",
            elapsed_ms=max(0, live_result.elapsed_ms),
            raw_count=live_result.raw_count,
            accepted_count=0,
            cache_status=_resolve_live_cache_status(
                cache_status=cache_result.status,
                cache_write_status=cache_write.status,
            ),
            cache_key=cache_result.cache_key,
            payload_hash=raw_payload_evidence.payload_hash,
            raw_payload_ref=raw_ref,
            empty_reason=None,
            error=None,
            cancelled=False,
        )
        return ProviderExecutionResult(
            query=_provider_query_to_dict(query),
            attempt=attempt,
            raw_rows=row_payload,
            raw_payload_ref=raw_ref,
            payload_hash=raw_payload_evidence.payload_hash,
            cache_inspection=_cache_inspection_to_dict(cache_result),
        )

    attempt = _build_attempt_from_provider_result(
        query=query,
        result=live_result,
        cache_status=cache_result.status,
        cache_key=cache_result.cache_key,
    )
    return ProviderExecutionResult(
        query=_provider_query_to_dict(query),
        attempt=attempt,
        raw_rows=[],
        raw_payload_ref=None,
        payload_hash=None,
        cache_inspection=_cache_inspection_to_dict(cache_result),
    )


def _execute_from_cache_hit(
    *,
    query: ProviderQuery,
    cache_result: CacheInspectionResult,
    started_at: float,
) -> ProviderExecutionResult:
    raw_ref = cache_result.raw_payload_ref or ""
    try:
        cached_rows = load_rows_by_raw_payload_ref(ref=raw_ref, expected_hash=cache_result.payload_hash)
        cached_result = RawProviderResult(
            provider=query.provider,
            endpoint=query.endpoint,
            query=dict(query.query),
            ok=True,
            status="success",
            raw_payload=cached_rows,
            raw_count=len(cached_rows),
            elapsed_ms=_elapsed_ms(started_at),
            empty_reason=None,
            error=None,
            payload_hash=cache_result.payload_hash,
            row_count=len(cached_rows),
            fields=_cache_record_fields(cache_result.record),
            as_of_date=_as_optional_text((cache_result.record or {}).get("as_of_date")),
            fetched_at=_as_optional_text((cache_result.record or {}).get("fetched_at")) or datetime.now(UTC).isoformat(),
        )
        rows = normalize_provider_payload(
            query=query,
            raw_result=cached_result,
            raw_payload_ref=raw_ref,
        )
        row_payload = _normalized_rows_to_dicts(rows)
        is_success = len(row_payload) > 0
        attempt = ProviderAttempt(
            provider=query.provider,
            endpoint=query.endpoint,
            priority=query.priority,
            query=_canonical_query_json(query.query),
            ok=is_success,
            status="success" if is_success else "empty",
            elapsed_ms=_elapsed_ms(started_at),
            raw_count=len(cached_rows),
            accepted_count=0,
            cache_status="hit",
            cache_key=cache_result.cache_key,
            payload_hash=cache_result.payload_hash,
            raw_payload_ref=raw_ref,
            empty_reason=None if is_success else "SOCIAL_PROVIDER_EMPTY",
            error=None,
            cancelled=False,
        )
        return ProviderExecutionResult(
            query=_provider_query_to_dict(query),
            attempt=attempt,
            raw_rows=row_payload,
            raw_payload_ref=raw_ref,
            payload_hash=cache_result.payload_hash,
            cache_inspection=_cache_inspection_to_dict(cache_result),
        )
    except SocialProviderNormalizationError as exc:
        attempt = _build_attempt_from_error(
            query=query,
            status="error",
            cache_status="hit",
            cache_key=cache_result.cache_key,
            raw_count=0,
            empty_reason=exc.code,
            error={"code": exc.code, "message": exc.message},
            elapsed_ms=_elapsed_ms(started_at),
        )
        return ProviderExecutionResult(
            query=_provider_query_to_dict(query),
            attempt=attempt,
            raw_rows=[],
            raw_payload_ref=None,
            payload_hash=None,
            cache_inspection=_cache_inspection_to_dict(cache_result),
        )


def _mark_unfinished_queries_cancelled(
    *,
    provider_plan: list[ProviderQuery],
    start_index: int,
    target_ticker: str,
    date_window: Any,
    schema_version: str,
    started_at: float,
) -> list[ProviderExecutionResult]:
    cancelled_results: list[ProviderExecutionResult] = []
    for query in provider_plan[start_index:]:
        cache_key = _build_cache_key(
            query=query,
            target_ticker=target_ticker,
            date_window=date_window,
            schema_version=schema_version,
        )
        attempt = _build_attempt_from_error(
            query=query,
            status="cancelled",
            cache_status="not_configured",
            cache_key=cache_key.cache_id(),
            raw_count=0,
            empty_reason=SOCIAL_PACK_DEADLINE_EXCEEDED,
            error={
                "code": SOCIAL_PACK_DEADLINE_EXCEEDED,
                "message": "pack deadline 已超时，query 被取消",
            },
            elapsed_ms=_elapsed_ms(started_at),
            cancelled=True,
        )
        cancelled_results.append(
            ProviderExecutionResult(
                query=_provider_query_to_dict(query),
                attempt=attempt,
                raw_rows=[],
                raw_payload_ref=None,
                payload_hash=None,
                cache_inspection={
                    "status": "not_configured",
                    "cache_key": cache_key.cache_id(),
                    "record": None,
                    "reason": SOCIAL_PACK_DEADLINE_EXCEEDED,
                    "payload_hash": None,
                    "raw_payload_ref": None,
                },
            )
        )
    return cancelled_results


def _build_cache_key(*, query: ProviderQuery, target_ticker: str, date_window: Any, schema_version: str) -> CacheKey:
    return CacheKey(
        market="CN_A",
        ticker=target_ticker,
        provider=query.provider,
        endpoint=query.endpoint,
        query_fingerprint=query.query_fingerprint,
        date_window=f"{date_window.start_date}:{date_window.end_date}",
        schema_version=schema_version,
    )


def _build_attempt_from_provider_result(
    *,
    query: ProviderQuery,
    result: RawProviderResult,
    cache_status: str,
    cache_key: str | None,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=query.provider,
        endpoint=query.endpoint,
        priority=query.priority,
        query=_canonical_query_json(query.query),
        ok=False,
        status=result.status,
        elapsed_ms=max(0, result.elapsed_ms),
        raw_count=max(0, result.raw_count),
        accepted_count=0,
        cache_status=cache_status,
        cache_key=cache_key,
        payload_hash=result.payload_hash,
        raw_payload_ref=None,
        empty_reason=result.empty_reason,
        error=_normalize_error_payload(result.error),
        cancelled=result.status == "cancelled",
    )


def _build_attempt_from_error(
    *,
    query: ProviderQuery,
    status: str,
    cache_status: str,
    cache_key: str | None,
    raw_count: int,
    empty_reason: str | None,
    error: dict[str, str],
    elapsed_ms: int,
    cancelled: bool = False,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=query.provider,
        endpoint=query.endpoint,
        priority=query.priority,
        query=_canonical_query_json(query.query),
        ok=False,
        status=status,
        elapsed_ms=max(0, elapsed_ms),
        raw_count=max(0, raw_count),
        accepted_count=0,
        cache_status=cache_status,
        cache_key=cache_key,
        payload_hash=None,
        raw_payload_ref=None,
        empty_reason=empty_reason,
        error=error,
        cancelled=cancelled,
    )


def _collect_raw_payload_refs(results: list[ProviderExecutionResult]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for result in results:
        ref = _as_optional_text(result.raw_payload_ref)
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def _cache_inspection_to_dict(result: CacheInspectionResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "cache_key": result.cache_key,
        "record": dict(result.record) if isinstance(result.record, Mapping) else None,
        "reason": result.reason,
        "payload_hash": result.payload_hash,
        "raw_payload_ref": result.raw_payload_ref,
    }


def _cache_record_fields(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        return {}
    fields = record.get("fields")
    if not isinstance(fields, Mapping):
        return {}
    return dict(fields)


def _provider_query_to_dict(query: ProviderQuery) -> dict[str, Any]:
    return {
        "provider": query.provider,
        "endpoint": query.endpoint,
        "priority": query.priority,
        "query": dict(query.query),
        "query_fingerprint": query.query_fingerprint,
        "date_window": {
            "start_date": query.date_window.start_date,
            "end_date": query.date_window.end_date,
            "as_of_date": query.date_window.as_of_date,
        },
        "timeout_seconds": query.timeout_seconds,
    }


def _resolve_endpoint_ttl(*, endpoint: str, config: SocialDataConfig) -> int:
    ttl = config.ttl_by_endpoint.get(endpoint)
    if isinstance(ttl, int) and ttl > 0:
        return ttl
    return config.provider_timeout_seconds


def _resolve_live_cache_status(*, cache_status: str, cache_write_status: str) -> str:
    if cache_status in {"stale", "schema_invalid"}:
        return cache_status
    if cache_write_status in {"write_failed", "not_configured"}:
        return cache_write_status
    return cache_status


def _normalize_error_payload(error: object) -> dict[str, str] | None:
    if not isinstance(error, Mapping):
        return None
    code = _as_optional_text(error.get("code"))
    message = _as_optional_text(error.get("message"))
    if code is None and message is None:
        return None
    payload: dict[str, str] = {}
    if code is not None:
        payload["code"] = code
    if message is not None:
        payload["message"] = message
    return payload


def _normalized_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            normalized.append(dict(row))
            continue
        normalized.append(asdict(row))
    return normalized


def _as_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _canonical_query_json(query: Mapping[str, Any]) -> str:
    return json.dumps(query, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required_p0_endpoints(provider_plan: list[ProviderQuery]) -> tuple[str, ...]:
    endpoints = [query.endpoint for query in provider_plan if query.priority == "P0"]
    return tuple(sorted(set(endpoints)))


def _bucketed_signals_to_dict(bucketed: Any) -> dict[str, list[dict[str, Any]]]:
    payload: dict[str, list[dict[str, Any]]] = {}
    for key in (
        "attention_signals",
        "topic_keyword_signals",
        "related_symbol_signals",
        "narrative_signals",
        "rejected_signals",
    ):
        bucket_rows = getattr(bucketed, key, [])
        signals: list[dict[str, Any]] = []
        for signal in bucket_rows:
            if isinstance(signal, Mapping):
                signals.append(dict(signal))
            else:
                signals.append(asdict(signal))
        payload[key] = signals
    return payload


def _quality_decision_to_pack_quality(
    *,
    quality_decision: QualityDecision,
    data: Mapping[str, list[dict[str, Any]]],
) -> SocialQuality:
    attention_count = len(data.get("attention_signals", []))
    keyword_count = len(data.get("topic_keyword_signals", []))
    related_count = len(data.get("related_symbol_signals", []))
    narrative_count = len(data.get("narrative_signals", []))
    warnings = [_quality_warning_to_dict(item) for item in quality_decision.warnings]
    return SocialQuality(
        status=quality_decision.status,
        attention_signal_count=attention_count,
        topic_keyword_count=keyword_count,
        related_symbol_count=related_count,
        narrative_signal_count=narrative_count,
        accepted_count=attention_count + keyword_count + related_count + narrative_count,
        missing_fields=list(quality_decision.missing_fields),
        social_judgment_allowed=quality_decision.social_judgment_allowed,
        warnings=warnings,
    )


def _quality_warning_to_dict(warning: Any) -> dict[str, str]:
    code = _as_optional_text(getattr(warning, "code", None)) or ""
    message = _as_optional_text(getattr(warning, "message", None)) or code or "quality_warning"
    payload = {"code": code or "quality_warning", "message": message}
    evidence_ref = _as_optional_text(getattr(warning, "evidence_ref", None))
    if evidence_ref is not None:
        payload["evidence_ref"] = evidence_ref
    return payload


def _build_pack_body(
    *,
    profile: PublicSocialProfile,
    query_plan: QueryPlanView,
    attempts: list[ProviderAttempt],
    data: dict[str, list[dict[str, Any]]],
    quality: SocialQuality,
    brief: ReaderBrief,
) -> dict[str, Any]:
    return {
        "schema_version": SOCIAL_SCHEMA_VERSION,
        "ok": quality.status != "failed",
        "profile": asdict(profile),
        "query_plan": asdict(query_plan),
        "provider_attempts": [asdict(attempt) for attempt in attempts],
        "data": data,
        "quality": asdict(quality),
        "reader_brief": brief.text,
    }


def _finalize_pack_evidence(raw_evidence: Any) -> SocialEvidence:
    pack_path = _as_optional_text(getattr(raw_evidence, "pack_path", None)) or ""
    provider_attempts_path = _as_optional_text(getattr(raw_evidence, "provider_attempts_path", None)) or ""
    cache_inspection_path = _as_optional_text(getattr(raw_evidence, "cache_inspection_path", None)) or ""
    raw_payload_refs_obj = getattr(raw_evidence, "raw_payload_refs", [])
    raw_payload_refs = sorted(_iter_valid_raw_refs(raw_payload_refs_obj))
    content_hash = canonical_json_sha256(
        {
            "pack_path": pack_path,
            "provider_attempts_path": provider_attempts_path,
            "cache_inspection_path": cache_inspection_path,
            "raw_payload_refs": raw_payload_refs,
        }
    )
    return SocialEvidence(
        pack_path=pack_path,
        provider_attempts_path=provider_attempts_path,
        cache_inspection_path=cache_inspection_path,
        raw_payload_refs=raw_payload_refs,
        content_hash=content_hash,
    )


def _iter_valid_raw_refs(value: Any) -> list[str]:
    refs: list[str] = []
    for item in _to_list(value):
        ref = _as_optional_text(item)
        if ref is None:
            continue
        refs.append(ref)
    return refs


def _to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _has_accepted_signal_missing_evidence(data: Mapping[str, Any]) -> bool:
    for key in ("attention_signals", "topic_keyword_signals", "related_symbol_signals", "narrative_signals"):
        signals = _to_list(data.get(key))
        for signal in signals:
            signal_payload = dict(signal) if isinstance(signal, Mapping) else asdict(signal)
            if not validate_signal_evidence(signal_payload):
                return True
    return False


def _downgrade_to_failed(*, pack: SocialSentimentPack, code: str, message: str) -> SocialSentimentPack:
    warnings = [dict(item) for item in pack.quality.warnings if isinstance(item, Mapping)]
    if not any(_as_optional_text(item.get("code")) == code for item in warnings):
        warnings.append({"code": code, "message": message})
    failed_quality = SocialQuality(
        status="failed",
        attention_signal_count=pack.quality.attention_signal_count,
        topic_keyword_count=pack.quality.topic_keyword_count,
        related_symbol_count=pack.quality.related_symbol_count,
        narrative_signal_count=pack.quality.narrative_signal_count,
        accepted_count=pack.quality.accepted_count,
        missing_fields=list(pack.quality.missing_fields),
        social_judgment_allowed=False,
        warnings=warnings,
    )
    return SocialSentimentPack(
        ok=False,
        profile=pack.profile,
        query_plan=pack.query_plan,
        provider_attempts=pack.provider_attempts,
        data=pack.data,
        quality=failed_quality,
        reader_brief=pack.reader_brief,
        evidence=pack.evidence,
    )


def _build_failed_pack_from_pipeline(
    *,
    code: str,
    message: str,
    profile: PublicSocialProfile,
    query_plan: QueryPlanView,
    attempts: list[ProviderAttempt],
    data: dict[str, list[dict[str, Any]]],
    context: SocialToolRuntimeContext,
    raw_payload_refs: list[str],
) -> SocialSentimentPack:
    warnings = [{"code": code, "message": message}]
    quality = SocialQuality(
        status="failed",
        attention_signal_count=len(data.get("attention_signals", [])),
        topic_keyword_count=len(data.get("topic_keyword_signals", [])),
        related_symbol_count=len(data.get("related_symbol_signals", [])),
        narrative_signal_count=len(data.get("narrative_signals", [])),
        accepted_count=(
            len(data.get("attention_signals", []))
            + len(data.get("topic_keyword_signals", []))
            + len(data.get("related_symbol_signals", []))
            + len(data.get("narrative_signals", []))
        ),
        missing_fields=[],
        social_judgment_allowed=False,
        warnings=warnings,
    )
    return SocialSentimentPack(
        ok=False,
        profile=profile,
        query_plan=query_plan,
        provider_attempts=attempts,
        data=data,
        quality=quality,
        reader_brief=f"资料包失败：{code}。{message}",
        evidence=_build_evidence(context, raw_payload_refs=raw_payload_refs),
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _deadline_exceeded(deadline_at: float) -> bool:
    return perf_counter() >= deadline_at


def _validate_runtime_context(context: SocialToolRuntimeContext) -> str | None:
    if context.worker_id != _WORKER_ID:
        return f"worker_id 必须为 {_WORKER_ID}: {context.worker_id}"
    if context.stage != _STAGE:
        return f"stage 必须为 {_STAGE}: {context.stage}"
    if context.tool_name != _TOOL_NAME:
        return f"tool_name 必须为 {_TOOL_NAME}: {context.tool_name}"
    if not context.run_id.strip():
        return "run_id 不能为空"
    if not context.call_id.strip():
        return "call_id 不能为空"
    if not context.evidence_root.strip():
        return "evidence_root 不能为空"
    if not context.current_time.strip():
        return "current_time 不能为空"
    return None


def _build_failed_pack(
    *,
    code: str,
    message: str,
    tool_input: SocialToolInput,
    context: SocialToolRuntimeContext,
    started_at: float,
) -> SocialSentimentPack:
    query_window = _best_effort_date_window(
        start_date=tool_input.start_date,
        end_date=tool_input.end_date,
        current_time=context.current_time,
    )
    attempt = _build_single_failure_attempt(
        code=code,
        message=message,
        tool_input=tool_input,
        elapsed_ms=int((perf_counter() - started_at) * 1000),
    )
    quality = SocialQuality(
        status="failed",
        attention_signal_count=0,
        topic_keyword_count=0,
        related_symbol_count=0,
        narrative_signal_count=0,
        accepted_count=0,
        missing_fields=[],
        social_judgment_allowed=False,
        warnings=[{"code": code, "message": message}],
    )
    query_plan = QueryPlanView(
        start_date=query_window["start_date"],
        end_date=query_window["end_date"],
        keywords=(_safe_keyword(tool_input.ticker),),
        provider_endpoints=(),
    )
    return SocialSentimentPack(
        ok=False,
        profile=PublicSocialProfile(
            ticker=_safe_public_ticker(tool_input.ticker),
            company_name=_safe_optional_text(tool_input.company_name),
            market="CN_A",
            industry=_safe_optional_text(tool_input.industry),
        ),
        query_plan=query_plan,
        provider_attempts=[attempt],
        data=_empty_data_buckets(),
        quality=quality,
        reader_brief=f"资料包失败：{code}。{message}",
        evidence=_build_evidence(context, raw_payload_refs=[]),
    )


def _build_single_failure_attempt(
    *,
    code: str,
    message: str,
    tool_input: SocialToolInput,
    elapsed_ms: int,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider="social",
        endpoint="build_social_sentiment_pack",
        priority="P0",
        query=json.dumps(
            {
                "ticker": tool_input.ticker,
                "market": tool_input.market,
                "start_date": tool_input.start_date,
                "end_date": tool_input.end_date,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        ok=False,
        status="error",
        elapsed_ms=max(0, elapsed_ms),
        raw_count=0,
        accepted_count=0,
        cache_status="not_configured",
        cache_key=None,
        payload_hash=None,
        raw_payload_ref=None,
        empty_reason=code,
        error={"code": code, "message": message},
        cancelled=False,
    )


def _best_effort_date_window(*, start_date: str | None, end_date: str | None, current_time: str) -> dict[str, str]:
    try:
        window = ResolveDateWindow(start_date=start_date, end_date=end_date, current_time=current_time)
        return {"start_date": window.start_date, "end_date": window.end_date}
    except SocialProfileError:
        today = datetime.now(UTC).date().isoformat()
        return {"start_date": today, "end_date": today}


def _build_evidence(context: SocialToolRuntimeContext, *, raw_payload_refs: list[str]) -> SocialEvidence:
    run_id = _safe_path_segment(context.run_id, default_segment="unknown-run")
    stage = _safe_path_segment(context.stage, default_segment="unknown-stage")
    worker = _safe_path_segment(context.worker_id, default_segment="unknown-worker")
    call_id = _safe_path_segment(context.call_id, default_segment="unknown-call")
    base_path = f"viking://resources/workflow/{run_id}/{stage}/{worker}/{call_id}/evidence/pack"
    pack_path = f"{base_path}/social_sentiment_pack.json"
    provider_attempts_path = f"{base_path}/provider_attempts.json"
    cache_inspection_path = f"{base_path}/cache_inspection.json"
    content_hash = canonical_json_sha256(
        {
            "schema_version": SOCIAL_SCHEMA_VERSION,
            "pack_path": pack_path,
            "provider_attempts_path": provider_attempts_path,
            "cache_inspection_path": cache_inspection_path,
            "raw_payload_refs": raw_payload_refs,
        }
    )
    return SocialEvidence(
        pack_path=pack_path,
        provider_attempts_path=provider_attempts_path,
        cache_inspection_path=cache_inspection_path,
        raw_payload_refs=list(raw_payload_refs),
        content_hash=content_hash,
    )


def _record_pack_completion(pack: SocialSentimentPack, *, context: SocialToolRuntimeContext, started_at: float) -> None:
    elapsed_ms = _elapsed_ms(started_at)
    record_metric(
        "social.pack.duration_ms",
        elapsed_ms,
        metric_type="gauge",
        tags={"status": pack.quality.status, "worker_id": context.worker_id},
    )
    record_metric(
        "social.pack.status.count",
        tags={"status": pack.quality.status},
    )
    emit_json_log(
        level="INFO" if pack.ok else "ERROR",
        event="social.pack.complete",
        code=pack.quality.status,
        run_id=context.run_id,
        call_id=context.call_id,
        worker_id=context.worker_id,
        endpoint="pack",
        fields={"duration_ms": elapsed_ms},
    )


def _safe_public_ticker(raw_ticker: str) -> str:
    value = raw_ticker.strip().upper()
    if value:
        return value
    return "000000.SH"


def _safe_keyword(raw_ticker: str) -> str:
    value = raw_ticker.strip().upper()
    if value:
        return value
    return "000000"


def _safe_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _safe_path_segment(value: str, *, default_segment: str) -> str:
    text = value.strip()
    if not text:
        return default_segment
    return text.replace("/", "_")


def _empty_data_buckets() -> dict[str, list[dict[str, str]]]:
    return {
        "attention_signals": [],
        "topic_keyword_signals": [],
        "related_symbol_signals": [],
        "narrative_signals": [],
        "rejected_signals": [],
    }
