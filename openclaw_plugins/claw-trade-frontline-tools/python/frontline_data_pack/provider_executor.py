from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import threading
import time
from typing import Any, Callable, Mapping

from .errors import MARKET_INVALID, PROVIDER_NOT_ENABLED, FrontlineValidationError
from .models import AttemptStatus, EvidenceRef, FieldSource, ProviderAttempt, ProviderQuery, ProviderResult, ProviderSpec
from .observability import (
    log_provider_attempt,
    record_market_provider_attempt,
    record_pack_schema_error,
    record_provider_attempt,
    record_provider_rows,
    record_span,
)
from .runtime_context import ToolRuntimeContext
from .security import summarize_provider_error
from .shutdown import PACK_TERMINATED, install_sigterm_handler, is_termination_requested


PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_SCHEMA_INVALID = "PROVIDER_SCHEMA_INVALID"
PROVIDER_ERROR = "PROVIDER_ERROR"

_CACHE_ATTEMPT_STATUSES = frozenset({"cache_hit", "cache_miss", "cache_stale", "schema_invalid", "error"})

@dataclass(frozen=True)
class ProviderCallContext:
    deadline_monotonic: float
    cancel_event: threading.Event

    def remaining_timeout_ms(self, *, monotonic: Callable[[], float] | None = None) -> int:
        monotonic_fn = time.monotonic if monotonic is None else monotonic
        return max(0, int(math.ceil((self.deadline_monotonic - monotonic_fn()) * 1000)))

    def is_cancelled(self, *, monotonic: Callable[[], float] | None = None) -> bool:
        if self.cancel_event.is_set():
            return True
        return self.remaining_timeout_ms(monotonic=monotonic) <= 0

    def raise_if_cancelled(self, *, monotonic: Callable[[], float] | None = None) -> None:
        if self.is_cancelled(monotonic=monotonic):
            raise TimeoutError("provider call cancelled by budget deadline")


ProviderCallable = Callable[[ProviderSpec, ProviderQuery, ToolRuntimeContext, ProviderCallContext], Any]
ProviderNormalizer = Callable[
    [ProviderSpec, ProviderQuery, Any, EvidenceRef | None],
    tuple[list[dict[str, Any]], dict[str, FieldSource]],
]


class _SchemaInvalidError(ValueError):
    """Raised when payload cannot be normalized under the current provider contract."""


class ProviderCallError(RuntimeError):
    def __init__(self, error_code: str, message_redacted: str | None = None) -> None:
        self.error_code = error_code
        self.message_redacted = message_redacted
        super().__init__(error_code)


@dataclass(frozen=True)
class _PendingProviderCall:
    index: int
    started_at: str
    started_monotonic: float


def execute_provider_attempt(
    spec: ProviderSpec,
    query: ProviderQuery,
    context: ToolRuntimeContext,
    *,
    call_registry: Mapping[tuple[str, str], ProviderCallable] | None = None,
    normalizer_registry: Mapping[tuple[str, str], ProviderNormalizer] | None = None,
    now_utc: Callable[[], str] | None = None,
    monotonic: Callable[[], float] | None = None,
    provider_call_context: ProviderCallContext | None = None,
) -> ProviderResult:
    now_utc_fn = _utc_now if now_utc is None else now_utc
    monotonic_fn = time.monotonic if monotonic is None else monotonic

    _ = context
    if query.market != "CN_A":
        raise FrontlineValidationError(MARKET_INVALID, "provider query.market 必须为 CN_A")

    started_at = now_utc_fn()
    started_monotonic = monotonic_fn()
    if provider_call_context is None:
        provider_call_context = ProviderCallContext(
            deadline_monotonic=started_monotonic + (spec.timeout_ms / 1000.0),
            cancel_event=threading.Event(),
        )
    if not spec.enabled:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="config_blocked",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_NOT_ENABLED,
            error_message_redacted=None,
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            runtime_context=context,
        )

    caller = _resolve_callable(spec=spec, call_registry=call_registry)
    if caller is None:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="error",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_ERROR,
            error_message_redacted="provider caller 未注册",
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            runtime_context=context,
        )

    try:
        provider_call_context.raise_if_cancelled(monotonic=monotonic_fn)
        payload = caller(spec, query, context, provider_call_context)
    except ProviderCallError as exc:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="error",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=exc.error_code,
            error_message_redacted=(
                summarize_provider_error(exc.message_redacted)
                if exc.message_redacted
                else None
            ),
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            runtime_context=context,
        )
    except TimeoutError:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="timeout",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_TIMEOUT,
            error_message_redacted=None,
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            runtime_context=context,
        )
    except Exception as exc:  # noqa: BLE001
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="error",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_ERROR,
            error_message_redacted=summarize_provider_error(exc),
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            runtime_context=context,
        )

    elapsed_ms = _elapsed_ms(started_monotonic, monotonic_fn)
    if elapsed_ms > spec.timeout_ms:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="timeout",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_TIMEOUT,
            error_message_redacted=None,
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            elapsed_ms_override=elapsed_ms,
            runtime_context=context,
        )

    if spec.mode == "cache":
        return _execute_cache_attempt(
            spec=spec,
            query=query,
            payload=payload,
            started_at=started_at,
            started_monotonic=started_monotonic,
            timeout_ms=spec.timeout_ms,
            now_utc=now_utc_fn,
            monotonic=monotonic_fn,
            normalizer_registry=normalizer_registry,
            runtime_context=context,
        )
    return _execute_remote_attempt(
        spec=spec,
        query=query,
        payload=payload,
        started_at=started_at,
        started_monotonic=started_monotonic,
        timeout_ms=spec.timeout_ms,
        now_utc=now_utc_fn,
        monotonic=monotonic_fn,
        normalizer_registry=normalizer_registry,
        runtime_context=context,
    )


def execute_provider_plan(
    specs: list[ProviderSpec],
    query: ProviderQuery,
    context: ToolRuntimeContext,
    *,
    max_concurrency: int,
    total_timeout_ms: int,
    call_registry: Mapping[tuple[str, str], ProviderCallable] | None = None,
    normalizer_registry: Mapping[tuple[str, str], ProviderNormalizer] | None = None,
    now_utc: Callable[[], str] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> list[ProviderResult]:
    install_sigterm_handler()
    now_utc_fn = _utc_now if now_utc is None else now_utc
    monotonic_fn = time.monotonic if monotonic is None else monotonic

    if max_concurrency < 1:
        raise ValueError("max_concurrency 必须 >= 1")
    if total_timeout_ms < 1:
        raise ValueError("total_timeout_ms 必须 >= 1")

    plan_started_monotonic = monotonic_fn()
    plan_deadline_monotonic = plan_started_monotonic + (total_timeout_ms / 1000.0)
    budget_cancel_event = threading.Event()
    pending: dict[Future[ProviderResult], _PendingProviderCall] = {}
    indexed_results: dict[int, ProviderResult] = {}
    next_index = 0
    unfinished_budget_timed_out = False
    terminated = is_termination_requested()

    pool = ThreadPoolExecutor(max_workers=max_concurrency)
    try:
        while True:
            while next_index < len(specs) and len(pending) < max_concurrency:
                if is_termination_requested():
                    terminated = True
                    budget_cancel_event.set()
                    break
                submit_monotonic = monotonic_fn()
                if submit_monotonic >= plan_deadline_monotonic:
                    budget_cancel_event.set()
                    break
                spec = specs[next_index]
                started_at = now_utc_fn()
                call_deadline_monotonic = min(
                    plan_deadline_monotonic,
                    submit_monotonic + (spec.timeout_ms / 1000.0),
                )
                future = pool.submit(
                    execute_provider_attempt,
                    spec,
                    query,
                    context,
                    call_registry=call_registry,
                    normalizer_registry=normalizer_registry,
                    now_utc=now_utc_fn,
                    monotonic=monotonic_fn,
                    provider_call_context=ProviderCallContext(
                        deadline_monotonic=call_deadline_monotonic,
                        cancel_event=budget_cancel_event,
                    ),
                )
                pending[future] = _PendingProviderCall(
                    index=next_index,
                    started_at=started_at,
                    started_monotonic=submit_monotonic,
                )
                next_index += 1

            if not pending:
                break

            remaining_seconds = plan_deadline_monotonic - monotonic_fn()
            if remaining_seconds <= 0:
                budget_cancel_event.set()
                break

            done, _ = wait(
                tuple(pending.keys()),
                timeout=remaining_seconds,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                budget_cancel_event.set()
                break

            for future in done:
                pending_call = pending.pop(future)
                index = pending_call.index
                try:
                    indexed_results[index] = future.result()
                except Exception as exc:  # noqa: BLE001
                    indexed_results[index] = _build_internal_error_result(
                        spec=specs[index],
                        query=query,
                        timeout_ms=specs[index].timeout_ms,
                        error=exc,
                        now_utc=now_utc_fn,
                        monotonic=monotonic_fn,
                        runtime_context=context,
                    )

        budget_exhausted_monotonic = max(monotonic_fn(), plan_deadline_monotonic)
        budget_exhausted_at = now_utc_fn()
        if is_termination_requested():
            terminated = True

        done_now, _ = wait(tuple(pending.keys()), timeout=0)
        for future in done_now:
            pending_call = pending.pop(future)
            index = pending_call.index
            try:
                indexed_results[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                indexed_results[index] = _build_internal_error_result(
                    spec=specs[index],
                    query=query,
                    timeout_ms=specs[index].timeout_ms,
                    error=exc,
                    now_utc=now_utc_fn,
                    monotonic=monotonic_fn,
                    runtime_context=context,
                )

        if pending:
            budget_cancel_event.set()
            unfinished_budget_timed_out = True

        for future, pending_call in pending.items():
            future.cancel()
            indexed_results[pending_call.index] = _build_total_budget_timeout_result(
                spec=specs[pending_call.index],
                query=query,
                timeout_ms=specs[pending_call.index].timeout_ms,
                now_utc=now_utc_fn,
                monotonic=monotonic_fn,
                runtime_context=context,
                started_at=pending_call.started_at,
                elapsed_ms_override=_elapsed_ms_between(
                    pending_call.started_monotonic,
                    budget_exhausted_monotonic,
                ),
            )

        for index in range(next_index, len(specs)):
            if terminated and specs[index].required_for_complete:
                indexed_results[index] = _build_pack_terminated_result(
                    spec=specs[index],
                    query=query,
                    timeout_ms=specs[index].timeout_ms,
                    now_utc=now_utc_fn,
                    monotonic=monotonic_fn,
                    runtime_context=context,
                    started_at=budget_exhausted_at,
                    elapsed_ms_override=0,
                )
            else:
                indexed_results[index] = _build_total_budget_timeout_result(
                    spec=specs[index],
                    query=query,
                    timeout_ms=specs[index].timeout_ms,
                    now_utc=now_utc_fn,
                    monotonic=monotonic_fn,
                    runtime_context=context,
                    started_at=budget_exhausted_at,
                    elapsed_ms_override=0,
                )
    finally:
        pool.shutdown(wait=not unfinished_budget_timed_out, cancel_futures=True)

    ordered_indices = sorted(indexed_results.keys())
    results = [indexed_results[index] for index in ordered_indices]
    plan_elapsed_ms = _elapsed_ms_between(plan_started_monotonic, monotonic_fn())
    span_status = "terminated" if terminated else ("timeout" if unfinished_budget_timed_out else "success")
    record_span(
        "provider.plan",
        status=span_status,
        elapsed_ms=plan_elapsed_ms,
        fields={
            "run_id": context.run_id,
            "call_id": context.call_id,
            "worker_id": context.worker_id,
            "tool_name": context.tool_name,
            "domain": specs[0].domain if specs else "unknown",
            "status": span_status,
            "elapsed_ms": plan_elapsed_ms,
            "provider_count": len(specs),
            "result_count": len(results),
        },
    )
    return results


def normalize_provider_payload(
    spec: ProviderSpec,
    query: ProviderQuery,
    raw_payload: Any,
    raw_ref: EvidenceRef | None,
    *,
    normalizer_registry: Mapping[tuple[str, str], ProviderNormalizer] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, FieldSource]]:
    normalizer = _resolve_normalizer(spec=spec, normalizer_registry=normalizer_registry)
    rows, field_sources = normalizer(spec, query, raw_payload, raw_ref)

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _SchemaInvalidError("normalized row 必须是 object")
        normalized_rows.append({str(key): value for key, value in row.items()})

    for field_path, source in field_sources.items():
        if not field_path:
            raise _SchemaInvalidError("field source key 不能为空")
        if not isinstance(source, FieldSource):
            raise _SchemaInvalidError("field source 必须是 FieldSource")
    return normalized_rows, field_sources


def _execute_remote_attempt(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    payload: Any,
    started_at: str,
    started_monotonic: float,
    timeout_ms: int,
    now_utc: Callable[[], str],
    monotonic: Callable[[], float],
    normalizer_registry: Mapping[tuple[str, str], ProviderNormalizer] | None,
    runtime_context: ToolRuntimeContext | None,
) -> ProviderResult:
    try:
        rows, field_sources = normalize_provider_payload(
            spec,
            query,
            payload,
            None,
            normalizer_registry=normalizer_registry,
        )
    except _SchemaInvalidError:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="schema_invalid",
            raw_payload=payload,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_SCHEMA_INVALID,
            error_message_redacted=None,
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    status: AttemptStatus = "success" if rows else "empty"
    return _finalize_result(
        spec=spec,
        query=query,
        started_at=started_at,
        started_monotonic=started_monotonic,
        status=status,
        raw_payload=payload,
        normalized_rows=rows,
        field_sources=field_sources,
        error_code=None,
        error_message_redacted=None,
        timeout_ms=timeout_ms,
        now_utc=now_utc,
        monotonic=monotonic,
        runtime_context=runtime_context,
    )


def _execute_cache_attempt(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    payload: Any,
    started_at: str,
    started_monotonic: float,
    timeout_ms: int,
    now_utc: Callable[[], str],
    monotonic: Callable[[], float],
    normalizer_registry: Mapping[tuple[str, str], ProviderNormalizer] | None,
    runtime_context: ToolRuntimeContext | None,
) -> ProviderResult:
    status = _extract_cache_status(payload)
    if status is None:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="schema_invalid",
            raw_payload=payload,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_SCHEMA_INVALID,
            error_message_redacted=None,
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    if status in {"cache_miss", "cache_stale"}:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status=status,
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=None,
            error_message_redacted=None,
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    if status == "error":
        code = _extract_error_code(payload) or PROVIDER_ERROR
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="error",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=code,
            error_message_redacted=_extract_error_message(payload),
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    if status == "schema_invalid":
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="schema_invalid",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_SCHEMA_INVALID,
            error_message_redacted=None,
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    raw_payload = _extract_raw_payload(payload)
    if raw_payload is None:
        rows = _extract_cache_rows(payload)
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="cache_hit",
            raw_payload=None,
            normalized_rows=rows,
            field_sources={},
            error_code=None,
            error_message_redacted=None,
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    try:
        rows, field_sources = normalize_provider_payload(
            spec,
            query,
            raw_payload,
            None,
            normalizer_registry=normalizer_registry,
        )
    except _SchemaInvalidError:
        return _finalize_result(
            spec=spec,
            query=query,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="schema_invalid",
            raw_payload=None,
            normalized_rows=[],
            field_sources={},
            error_code=PROVIDER_SCHEMA_INVALID,
            error_message_redacted=None,
            timeout_ms=timeout_ms,
            now_utc=now_utc,
            monotonic=monotonic,
            runtime_context=runtime_context,
        )

    return _finalize_result(
        spec=spec,
        query=query,
        started_at=started_at,
        started_monotonic=started_monotonic,
        status="cache_hit",
        raw_payload=raw_payload,
        normalized_rows=rows,
        field_sources=field_sources,
        error_code=None,
        error_message_redacted=None,
        timeout_ms=timeout_ms,
        now_utc=now_utc,
        monotonic=monotonic,
        runtime_context=runtime_context,
    )


def _resolve_callable(
    *,
    spec: ProviderSpec,
    call_registry: Mapping[tuple[str, str], ProviderCallable] | None,
) -> ProviderCallable | None:
    if call_registry is None:
        return None
    return call_registry.get((spec.provider, spec.endpoint))


def _resolve_normalizer(
    *,
    spec: ProviderSpec,
    normalizer_registry: Mapping[tuple[str, str], ProviderNormalizer] | None,
) -> ProviderNormalizer:
    if normalizer_registry is None:
        return _default_normalizer
    return normalizer_registry.get((spec.provider, spec.endpoint), _default_normalizer)


def _default_normalizer(
    spec: ProviderSpec,
    query: ProviderQuery,
    raw_payload: Any,
    raw_ref: EvidenceRef | None,
) -> tuple[list[dict[str, Any]], dict[str, FieldSource]]:
    _ = spec, query, raw_ref
    rows_payload: Any
    if isinstance(raw_payload, Mapping):
        if isinstance(raw_payload.get("normalized_rows"), list):
            rows_payload = raw_payload["normalized_rows"]
        elif isinstance(raw_payload.get("rows"), list):
            rows_payload = raw_payload["rows"]
        else:
            raise _SchemaInvalidError("payload 缺少 rows/normalized_rows")
    elif isinstance(raw_payload, list):
        rows_payload = raw_payload
    else:
        raise _SchemaInvalidError("payload 不是可识别结构")

    rows: list[dict[str, Any]] = []
    for row in rows_payload:
        if not isinstance(row, Mapping):
            raise _SchemaInvalidError("rows 元素必须是 object")
        rows.append({str(key): value for key, value in row.items()})
    return rows, {}


def _extract_cache_status(payload: Any) -> AttemptStatus | None:
    if isinstance(payload, Mapping):
        status = payload.get("status")
        if isinstance(status, str) and status in _CACHE_ATTEMPT_STATUSES:
            return status
        return None
    status = getattr(payload, "status", None)
    if isinstance(status, str) and status in _CACHE_ATTEMPT_STATUSES:
        return status
    return None


def _extract_cache_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    raw_rows = payload.get("normalized_rows")
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if isinstance(row, Mapping):
            rows.append({str(key): value for key, value in row.items()})
    return rows


def _extract_raw_payload(payload: Any) -> Any | None:
    if not isinstance(payload, Mapping):
        return None
    if "raw_payload" in payload:
        return payload.get("raw_payload")
    return None


def _extract_error_code(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        value = payload.get("error_code")
        if isinstance(value, str) and value:
            return value
        reason = payload.get("reason")
        if isinstance(reason, str) and reason:
            return reason.split(":", 1)[0]
        return None
    reason = getattr(payload, "reason", None)
    if isinstance(reason, str) and reason:
        return reason.split(":", 1)[0]
    return None


def _extract_error_message(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        value = payload.get("error_message_redacted")
        if isinstance(value, str) and value:
            return summarize_provider_error(value)
        reason = payload.get("reason")
        if isinstance(reason, str) and reason:
            return summarize_provider_error(reason)
        return None
    reason = getattr(payload, "reason", None)
    if isinstance(reason, str) and reason:
        return summarize_provider_error(reason)
    return None


def _build_internal_error_result(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    timeout_ms: int,
    error: Exception,
    now_utc: Callable[[], str],
    monotonic: Callable[[], float],
    runtime_context: ToolRuntimeContext | None = None,
) -> ProviderResult:
    started_at = now_utc()
    started_monotonic = monotonic()
    return _finalize_result(
        spec=spec,
        query=query,
        started_at=started_at,
        started_monotonic=started_monotonic,
        status="error",
        raw_payload=None,
        normalized_rows=[],
        field_sources={},
        error_code=PROVIDER_ERROR,
        error_message_redacted=summarize_provider_error(error),
        timeout_ms=timeout_ms,
        now_utc=now_utc,
        monotonic=monotonic,
        runtime_context=runtime_context,
    )


def _build_pack_terminated_result(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    timeout_ms: int,
    now_utc: Callable[[], str],
    monotonic: Callable[[], float],
    runtime_context: ToolRuntimeContext | None,
    started_at: str,
    elapsed_ms_override: int,
) -> ProviderResult:
    started_monotonic = monotonic()
    return _finalize_result(
        spec=spec,
        query=query,
        started_at=started_at,
        started_monotonic=started_monotonic,
        status="error",
        raw_payload=None,
        normalized_rows=[],
        field_sources={},
        error_code=PACK_TERMINATED,
        error_message_redacted="pack terminated before provider attempt started",
        timeout_ms=timeout_ms,
        now_utc=now_utc,
        monotonic=monotonic,
        elapsed_ms_override=elapsed_ms_override,
        runtime_context=runtime_context,
    )


def _build_total_budget_timeout_result(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    timeout_ms: int,
    now_utc: Callable[[], str],
    monotonic: Callable[[], float],
    runtime_context: ToolRuntimeContext | None = None,
    started_at: str | None = None,
    elapsed_ms_override: int | None = None,
) -> ProviderResult:
    started_at_value = now_utc() if started_at is None else started_at
    started_monotonic = monotonic()
    return _finalize_result(
        spec=spec,
        query=query,
        started_at=started_at_value,
        started_monotonic=started_monotonic,
        status="timeout",
        raw_payload=None,
        normalized_rows=[],
        field_sources={},
        error_code=PROVIDER_TIMEOUT,
        error_message_redacted=None,
        timeout_ms=timeout_ms,
        now_utc=now_utc,
        monotonic=monotonic,
        elapsed_ms_override=elapsed_ms_override,
        runtime_context=runtime_context,
    )


def _finalize_result(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    started_at: str,
    started_monotonic: float,
    status: AttemptStatus,
    raw_payload: Any | None,
    normalized_rows: list[dict[str, Any]],
    field_sources: dict[str, FieldSource],
    error_code: str | None,
    error_message_redacted: str | None,
    timeout_ms: int,
    now_utc: Callable[[], str],
    monotonic: Callable[[], float],
    runtime_context: ToolRuntimeContext | None = None,
    elapsed_ms_override: int | None = None,
) -> ProviderResult:
    finished_at = now_utc()
    elapsed_ms = elapsed_ms_override
    if elapsed_ms is None:
        elapsed_ms = _elapsed_ms(started_monotonic, monotonic)
    raw_count = _count_rows(raw_payload)
    accepted_count = len(normalized_rows)
    if accepted_count > raw_count:
        raw_count = accepted_count
    attempt = ProviderAttempt(
        provider=spec.provider,
        endpoint=spec.endpoint,
        role=spec.role,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_ms=elapsed_ms,
        timeout_ms=timeout_ms,
        query_fingerprint=query.query_fingerprint,
        raw_count=raw_count,
        accepted_count=accepted_count,
        payload_hash=None,
        raw_payload_ref=None,
        error_code=error_code,
        error_message_redacted=error_message_redacted,
    )
    record_provider_attempt(
        domain=spec.domain,
        provider=spec.provider,
        endpoint=spec.endpoint,
        status=attempt.status,
        elapsed_ms=attempt.elapsed_ms,
    )
    if attempt.status == "schema_invalid":
        record_pack_schema_error(domain=spec.domain)
    if spec.domain == "market":
        record_market_provider_attempt(status=attempt.status)
    record_provider_rows(
        domain=spec.domain,
        provider=spec.provider,
        endpoint=spec.endpoint,
        rows=attempt.raw_count,
    )
    log_provider_attempt(
        context=runtime_context,
        domain=spec.domain,
        ticker=query.ticker,
        provider=spec.provider,
        endpoint=spec.endpoint,
        role=spec.role,
        status=attempt.status,
        elapsed_ms=attempt.elapsed_ms,
        timeout_ms=attempt.timeout_ms,
        error_code=attempt.error_code,
        raw_count=attempt.raw_count,
        accepted_count=attempt.accepted_count,
    )
    record_span(
        "provider.execute",
        status=attempt.status,
        elapsed_ms=attempt.elapsed_ms,
        fields={
            "run_id": runtime_context.run_id if runtime_context else None,
            "call_id": runtime_context.call_id if runtime_context else None,
            "worker_id": runtime_context.worker_id if runtime_context else None,
            "tool_name": runtime_context.tool_name if runtime_context else None,
            "domain": spec.domain,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "status": attempt.status,
            "elapsed_ms": attempt.elapsed_ms,
        },
    )
    return ProviderResult(
        spec=spec,
        attempt=attempt,
        raw_payload=raw_payload,
        normalized_rows=normalized_rows,
        field_sources=field_sources,
    )


def _count_rows(raw_payload: Any | None) -> int:
    if raw_payload is None:
        return 0
    if isinstance(raw_payload, list):
        return len(raw_payload)
    if isinstance(raw_payload, Mapping):
        for key in ("rows", "normalized_rows", "data", "items", "klines"):
            value = raw_payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def _elapsed_ms(started_monotonic: float, monotonic: Callable[[], float]) -> int:
    return _elapsed_ms_between(started_monotonic, monotonic())


def _elapsed_ms_between(started_monotonic: float, finished_monotonic: float) -> int:
    elapsed = finished_monotonic - started_monotonic
    return max(0, int(math.ceil(elapsed * 1000)))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "PROVIDER_ERROR",
    "PROVIDER_SCHEMA_INVALID",
    "PROVIDER_TIMEOUT",
    "ProviderCallError",
    "ProviderCallContext",
    "ProviderCallable",
    "ProviderNormalizer",
    "execute_provider_attempt",
    "execute_provider_plan",
    "normalize_provider_payload",
]
