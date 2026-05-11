from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Mapping

_METRIC_LABEL_ALLOWLIST: dict[str, frozenset[str]] = {
    "news_provider_attempt_total": frozenset({"provider", "endpoint", "status", "empty_reason"}),
    "news_provider_elapsed_ms_bucket": frozenset({"provider", "endpoint"}),
    "news_pack_status_total": frozenset({"status"}),
    "news_pack_missing_field_total": frozenset({"field"}),
    "news_pack_company_direct_count": frozenset(),
    "news_pack_provider_raw_count": frozenset(),
    "news_match_bucket_total": frozenset({"bucket"}),
    "news_match_type_total": frozenset({"match_type"}),
    "news_match_rejected_total": frozenset(),
    "news_dedup_removed_total": frozenset(),
    "news_dedup_reason_total": frozenset({"reason"}),
    "news_trimmed_total": frozenset(),
    "news_keyword_category_total": frozenset({"category"}),
}

_ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "stage",
        "worker_id",
        "call_id",
        "tool_name",
        "provider",
        "endpoint",
        "status",
        "count",
        "error_code",
        "path",
        "size",
        "bucket",
        "match_type",
        "reason",
        "field",
    }
)

_SENSITIVE_TOKENS: tuple[str, ...] = (
    "token",
    "api_key",
    "secret",
    "authorization",
    "cookie",
    "password",
    "passwd",
    "bearer",
    "summary",
    "title",
    "url",
    "content",
)
_MASKED_VALUE = "***"

_lock = threading.Lock()
_metric_points: list[dict[str, Any]] = []
_metric_totals: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_events: list[dict[str, Any]] = []
_spans: list[dict[str, Any]] = []
_json_lines: list[str] = []

_bound_fields_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "cn_a_news_obs_bound_fields", default={}
)
_span_stack_var: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "cn_a_news_obs_span_stack", default=[]
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_TOKENS)


def _normalize_label_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text if text else "none"
    return str(value)


def _normalize_field_value(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _sanitize_metric_labels(name: str, labels: Mapping[str, Any] | None) -> dict[str, str]:
    allowlist = _METRIC_LABEL_ALLOWLIST.get(name)
    if allowlist is None or labels is None:
        return {}
    sanitized: dict[str, str] = {}
    for key in sorted(labels.keys()):
        if key not in allowlist:
            continue
        if _is_sensitive_key(key):
            sanitized[key] = _MASKED_VALUE
            continue
        sanitized[key] = _normalize_label_value(labels[key])
    if "empty_reason" in allowlist and "empty_reason" not in sanitized:
        sanitized["empty_reason"] = "none"
    return sanitized


def _sanitize_log_fields(fields: Mapping[str, Any] | None) -> dict[str, Any]:
    if fields is None:
        return {}
    sanitized: dict[str, Any] = {}
    for key in sorted(fields.keys()):
        if key not in _ALLOWED_LOG_FIELDS:
            continue
        if _is_sensitive_key(key):
            sanitized[key] = _MASKED_VALUE
            continue
        value = _normalize_field_value(fields[key])
        sanitized[key] = value
    return sanitized


def _stable_json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _append_jsonl(payload: Mapping[str, Any]) -> None:
    _json_lines.append(_stable_json_line(payload))


def _merge_bound_fields(fields: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    bound_fields = _bound_fields_var.get()
    if bound_fields:
        merged.update(bound_fields)
    if fields:
        merged.update(dict(fields))
    return merged


class _BoundObservabilityContext:
    def __init__(self, fields: Mapping[str, Any]) -> None:
        self._fields = fields
        self._token: contextvars.Token[dict[str, str]] | None = None

    def __enter__(self) -> None:
        merged = _sanitize_log_fields(_merge_bound_fields(self._fields))
        self._token = _bound_fields_var.set(
            {key: _normalize_label_value(value) for key, value in merged.items()}
        )
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._token is not None:
            _bound_fields_var.reset(self._token)
        return False


def bind_observability_context(fields: Mapping[str, Any]) -> _BoundObservabilityContext:
    return _BoundObservabilityContext(fields)


def record_metric(name: str, labels: Mapping[str, Any] | None, value: float = 1) -> None:
    try:
        metric_value = float(value)
        metric_labels = _sanitize_metric_labels(name, labels)
        point = {
            "name": name,
            "labels": metric_labels,
            "value": metric_value,
            "timestamp": _utc_now_iso(),
        }
        with _lock:
            _metric_points.append(point)
            key = (name, tuple(sorted(metric_labels.items())))
            _metric_totals[key] = _metric_totals.get(key, 0.0) + metric_value
            _append_jsonl({"type": "metric", **point})
    except Exception:
        return


def record_event(event_name: str, fields: Mapping[str, Any] | None = None) -> None:
    try:
        sanitized_fields = _sanitize_log_fields(_merge_bound_fields(fields))
        event = {
            "event_name": event_name,
            "fields": sanitized_fields,
            "timestamp": _utc_now_iso(),
        }
        with _lock:
            _events.append(event)
            _append_jsonl({"type": "event", **event})
    except Exception:
        return


class _TraceSpanContext:
    def __init__(self, name: str, fields: Mapping[str, Any] | None = None) -> None:
        self._name = name
        self._fields = fields
        self._span_id = uuid.uuid4().hex
        self._started_at = _utc_now_iso()
        self._started_perf = time.perf_counter()
        self._status = "ok"
        self._error_code: str | None = None
        self._parent_span_id: str | None = None
        self._stack_token: contextvars.Token[list[str]] | None = None

    def __enter__(self) -> dict[str, str]:
        parent_stack = _span_stack_var.get()
        self._parent_span_id = parent_stack[-1] if parent_stack else None
        next_stack = [*parent_stack, self._span_id]
        self._stack_token = _span_stack_var.set(next_stack)
        return {"span_id": self._span_id}

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc is not None:
            self._status = "error"
            self._error_code = type(exc).__name__
        if self._stack_token is not None:
            _span_stack_var.reset(self._stack_token)
        try:
            duration_ms = max(0, int((time.perf_counter() - self._started_perf) * 1000))
            span_fields = _sanitize_log_fields(_merge_bound_fields(self._fields))
            span = {
                "name": self._name,
                "span_id": self._span_id,
                "parent_span_id": self._parent_span_id,
                "status": self._status,
                "error_code": self._error_code,
                "fields": span_fields,
                "start_time": self._started_at,
                "duration_ms": duration_ms,
            }
            with _lock:
                _spans.append(span)
                _append_jsonl({"type": "span", **span})
        except Exception:
            return False
        return False


def trace_span(name: str, fields: Mapping[str, Any] | None = None) -> _TraceSpanContext:
    return _TraceSpanContext(name=name, fields=fields)


def snapshot_observability() -> dict[str, Any]:
    with _lock:
        metric_totals = [
            {
                "name": metric_name,
                "labels": dict(label_pairs),
                "value": total,
            }
            for (metric_name, label_pairs), total in sorted(
                _metric_totals.items(), key=lambda item: (item[0][0], item[0][1])
            )
        ]
        return {
            "metric_points": [dict(item) for item in _metric_points],
            "metric_totals": metric_totals,
            "events": [dict(item) for item in _events],
            "spans": [dict(item) for item in _spans],
            "json_lines": list(_json_lines),
        }


def reset_observability() -> None:
    with _lock:
        _metric_points.clear()
        _metric_totals.clear()
        _events.clear()
        _spans.clear()
        _json_lines.clear()


def export_observability_jsonl() -> str:
    with _lock:
        if not _json_lines:
            return ""
        return "\n".join(_json_lines) + "\n"
