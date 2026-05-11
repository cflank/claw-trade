from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
from threading import Lock
from time import perf_counter
from typing import Any, Iterator, Mapping

from .runtime_context import ToolRuntimeContext
from .security import REDACTED, redact_secret


_LOGGER = logging.getLogger("frontline_data_pack.observability")
_STRUCTURED_LOG_FIELDS = (
    "run_id",
    "dispatch_id",
    "call_id",
    "stage",
    "worker_id",
    "tool_name",
    "domain",
    "ticker",
    "provider",
    "endpoint",
    "role",
    "attempt_status",
    "quality_status",
    "status",
    "elapsed_ms",
    "timeout_ms",
    "raw_count",
    "accepted_count",
    "error_code",
    "evidence_kind",
    "l2_ref_present",
    "mongo_ref_present",
)
_SENSITIVE_KEY_TOKENS = (
    "authorization",
    "token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "signature",
    "sign",
    "mongodb_uri",
    "openviking",
)


@dataclass(frozen=True)
class SpanRecord:
    name: str
    status: str
    elapsed_ms: int
    fields: Mapping[str, Any]


class _InMemoryObservability:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = {}
        self._logs: list[dict[str, Any]] = []
        self._spans: list[SpanRecord] = []

    def increment(self, name: str, labels: Mapping[str, str], amount: int = 1) -> None:
        key = (name, _normalize_labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def observe(self, name: str, labels: Mapping[str, str], value: int) -> None:
        key = (name, _normalize_labels(labels))
        with self._lock:
            series = self._histograms.get(key)
            if series is None:
                self._histograms[key] = [value]
            else:
                series.append(value)

    def append_log(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._logs.append(dict(payload))

    def append_span(self, record: SpanRecord) -> None:
        with self._lock:
            self._spans.append(record)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = {
                _render_metric_key(name, labels): value
                for (name, labels), value in self._counters.items()
            }
            histograms = {
                _render_metric_key(name, labels): list(value)
                for (name, labels), value in self._histograms.items()
            }
            logs = [dict(item) for item in self._logs]
            spans = [record for record in self._spans]
        return {
            "counters": counters,
            "histograms": histograms,
            "logs": logs,
            "spans": spans,
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._logs.clear()
            self._spans.clear()


_OBS = _InMemoryObservability()


def reset_observability_state() -> None:
    _OBS.reset()


def snapshot_observability_state() -> dict[str, Any]:
    return _OBS.snapshot()


def emit_structured_log(payload: Mapping[str, Any], *, level: int = logging.INFO) -> dict[str, Any]:
    canonical: dict[str, Any] = {key: payload.get(key) for key in _STRUCTURED_LOG_FIELDS}
    if canonical["attempt_status"] is None and payload.get("status") is not None:
        canonical["attempt_status"] = payload.get("status")
    if canonical["quality_status"] is None and payload.get("status") is not None:
        canonical["quality_status"] = payload.get("status")
    for key, value in payload.items():
        canonical.setdefault(key, value)
    sanitized = sanitize_log_payload(canonical)
    _LOGGER.log(level, json.dumps(sanitized, ensure_ascii=False, sort_keys=True, default=str))
    _OBS.append_log(sanitized)
    return sanitized


@contextmanager
def trace_span(name: str, *, fields: Mapping[str, Any] | None = None) -> Iterator[None]:
    started = perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
        sanitized_fields = sanitize_log_payload(fields or {})
        _OBS.append_span(
            SpanRecord(
                name=name,
                status=status,
                elapsed_ms=elapsed_ms,
                fields=sanitized_fields,
            )
        )


def record_span(name: str, *, status: str, elapsed_ms: int, fields: Mapping[str, Any] | None = None) -> None:
    _OBS.append_span(
        SpanRecord(
            name=name,
            status=status,
            elapsed_ms=max(0, int(elapsed_ms)),
            fields=sanitize_log_payload(fields or {}),
        )
    )


def record_counter(name: str, *, labels: Mapping[str, str] | None = None, amount: int = 1) -> None:
    _OBS.increment(name, labels or {}, amount=amount)


def record_histogram(name: str, *, labels: Mapping[str, str] | None = None, value: int) -> None:
    _OBS.observe(name, labels or {}, value)


def record_pack_build(*, domain: str, status: str, elapsed_ms: int) -> None:
    labels = {"domain": domain, "status": status}
    _OBS.increment("pack_build_total", labels)
    _OBS.observe("pack_latency_ms", {"domain": domain}, elapsed_ms)
    _OBS.increment("pack_quality_status_total", labels)


def record_provider_attempt(
    *,
    domain: str,
    provider: str,
    endpoint: str,
    status: str,
    elapsed_ms: int,
) -> None:
    labels = {
        "domain": domain,
        "provider": provider,
        "endpoint": endpoint,
        "status": status,
    }
    _OBS.increment("provider_attempt_total", labels)
    _OBS.observe(
        "provider_elapsed_ms",
        {"domain": domain, "provider": provider, "endpoint": endpoint},
        elapsed_ms,
    )


def record_provider_rows(*, domain: str, provider: str, endpoint: str, rows: int) -> None:
    _OBS.increment(
        "provider_rows_total",
        {"domain": domain, "provider": provider, "endpoint": endpoint},
        amount=max(0, int(rows)),
    )


def record_mongo_cache_inspect(*, status: str) -> None:
    _OBS.increment("mongo_cache_inspect_total", {"status": status})


def record_mongo_latency(*, operation: str, elapsed_ms: int) -> None:
    _OBS.observe("mongo_latency_ms", {"operation": operation}, elapsed_ms)


def record_mongo_upsert(*, kind: str, status: str, count: int = 1) -> None:
    _OBS.increment("mongo_upsert_total", {"kind": kind, "status": status}, amount=max(0, int(count)))


def record_l2_write(*, kind: str, status: str) -> None:
    _OBS.increment("l2_write_total", {"kind": kind, "status": status})


def record_l2_write_bytes(*, kind: str, status: str, size_bytes: int) -> None:
    _OBS.increment(
        "l2_write_bytes_total",
        {"kind": kind, "status": status},
        amount=max(0, int(size_bytes)),
    )


def record_l2_readback_failed(*, kind: str) -> None:
    _OBS.increment("l2_readback_failed_total", {"kind": kind})


def record_brief_validation_failed(*, domain: str) -> None:
    _OBS.increment("brief_validation_failed_total", {"domain": domain})


def record_brief_build(*, domain: str, status: str) -> None:
    _OBS.increment("brief_build_total", {"domain": domain, "status": status})


def record_pack_schema_error(*, domain: str) -> None:
    _OBS.increment("pack_schema_error_total", {"domain": domain})


def record_market_rows(*, count: int) -> None:
    _OBS.increment("market_rows_total", {}, amount=max(0, int(count)))


def record_market_chart(*, status: str, count: int = 1) -> None:
    _OBS.increment("market_chart_total", {"status": status}, amount=max(0, int(count)))


def record_market_provider_attempt(*, status: str) -> None:
    _OBS.increment("market_provider_attempt_total", {"status": status})


def record_news_raw_total(*, count: int) -> None:
    _OBS.increment("news_raw_total", {}, amount=max(0, int(count)))


def record_news_accepted_total(*, bucket: str, count: int) -> None:
    _OBS.increment("news_accepted_total", {"bucket": bucket}, amount=max(0, int(count)))


def record_news_context_error() -> None:
    _OBS.increment("news_context_error_total", {})


def record_social_signal_accepted_total(*, count: int) -> None:
    _OBS.increment("social_signal_accepted_total", {}, amount=max(0, int(count)))


def record_social_signal_rejected_total(*, count: int) -> None:
    _OBS.increment("social_signal_rejected_total", {}, amount=max(0, int(count)))


def record_social_evidence_write_failed_total(*, count: int = 1) -> None:
    _OBS.increment("social_evidence_write_failed_total", {}, amount=max(0, int(count)))


def record_fundamental_field_mapped_total(*, count: int) -> None:
    _OBS.increment("fundamental_field_mapped_total", {}, amount=max(0, int(count)))


def record_fundamental_missing_core_total(*, count: int) -> None:
    _OBS.increment("fundamental_missing_core_total", {}, amount=max(0, int(count)))


def record_fundamental_conflict_total(*, count: int) -> None:
    _OBS.increment("fundamental_conflict_total", {}, amount=max(0, int(count)))


def record_techlab_indicator_total(*, status: str) -> None:
    _OBS.increment("techlab_indicator_total", {"status": status})


def record_techlab_chart_total(*, status: str, count: int = 1) -> None:
    _OBS.increment("techlab_chart_total", {"status": status}, amount=max(0, int(count)))


def log_provider_attempt(
    *,
    context: ToolRuntimeContext | None,
    domain: str,
    ticker: str | None,
    provider: str,
    endpoint: str,
    role: str | None,
    status: str,
    elapsed_ms: int,
    timeout_ms: int | None,
    error_code: str | None,
    raw_count: int,
    accepted_count: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": context.run_id if context else None,
        "dispatch_id": context.dispatch_id if context else None,
        "call_id": context.call_id if context else None,
        "stage": context.stage if context else None,
        "worker_id": context.worker_id if context else None,
        "tool_name": context.tool_name if context else None,
        "domain": domain,
        "ticker": ticker,
        "provider": provider,
        "endpoint": endpoint,
        "role": role,
        "attempt_status": status,
        "quality_status": None,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "timeout_ms": timeout_ms,
        "raw_count": raw_count,
        "accepted_count": accepted_count,
        "error_code": error_code,
        "evidence_kind": None,
        "l2_ref_present": None,
        "mongo_ref_present": None,
    }
    return emit_structured_log(payload)


def sanitize_log_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = str(key)
        if _is_sensitive_key(normalized_key):
            sanitized[normalized_key] = REDACTED
            continue
        sanitized[normalized_key] = _sanitize_value(value)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return sanitize_log_payload(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return redact_secret(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    for token in _SENSITIVE_KEY_TOKENS:
        if token in lowered:
            return True
    return False


def _normalize_labels(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    normalized = []
    for key, value in labels.items():
        normalized.append((str(key), str(value)))
    normalized.sort()
    return tuple(normalized)


def _render_metric_key(name: str, labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return name
    rendered = ",".join(f"{key}={value}" for key, value in labels)
    return f"{name}|{rendered}"


__all__ = [
    "emit_structured_log",
    "log_provider_attempt",
    "record_brief_build",
    "record_brief_validation_failed",
    "record_counter",
    "record_fundamental_conflict_total",
    "record_fundamental_field_mapped_total",
    "record_fundamental_missing_core_total",
    "record_histogram",
    "record_l2_readback_failed",
    "record_l2_write",
    "record_l2_write_bytes",
    "record_market_chart",
    "record_market_provider_attempt",
    "record_market_rows",
    "record_mongo_upsert",
    "record_mongo_cache_inspect",
    "record_mongo_latency",
    "record_news_accepted_total",
    "record_news_context_error",
    "record_news_raw_total",
    "record_pack_build",
    "record_pack_schema_error",
    "record_provider_attempt",
    "record_provider_rows",
    "record_social_evidence_write_failed_total",
    "record_social_signal_accepted_total",
    "record_social_signal_rejected_total",
    "record_span",
    "record_techlab_chart_total",
    "record_techlab_indicator_total",
    "reset_observability_state",
    "snapshot_observability_state",
    "trace_span",
]
