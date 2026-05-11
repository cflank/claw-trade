from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from time import perf_counter
from typing import Any, Iterator, Mapping

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_REDACTED = "***REDACTED***"
_REDACTED_PAYLOAD = "***REDACTED_PAYLOAD***"
_MAX_TEXT_LENGTH = 400
_REQUIRED_LOG_FIELDS = ("ts", "level", "run_id", "call_id", "worker_id", "endpoint", "event", "code")
_SENSITIVE_KEY_TOKENS = (
    "secret",
    "token",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "authorization",
    "credential",
    "cookie",
    "mongodb_uri",
    "mongo_uri",
    "uri",
)
_PAYLOAD_KEY_TOKENS = (
    "raw_payload",
    "payload",
    "response_body",
    "request_body",
    "body",
    "content",
)
_MONGODB_URI_PREFIXES = ("mongodb://", "mongodb+srv://")

_METRIC_POINTS: list[dict[str, Any]] = []
_LOG_RECORDS: list[dict[str, Any]] = []
_TRACE_SPANS: list[dict[str, Any]] = []


def record_metric(
    name: str,
    value: int | float = 1,
    *,
    metric_type: str = "count",
    tags: Mapping[str, Any] | None = None,
) -> None:
    try:
        _METRIC_POINTS.append(
            {
                "ts": _utc_now_iso(),
                "name": str(name),
                "value": value,
                "type": str(metric_type),
                "tags": _sanitize_value(dict(tags) if isinstance(tags, Mapping) else {}),
            }
        )
    except Exception:
        return


def emit_json_log(
    *,
    level: str,
    event: str,
    code: str,
    run_id: str | None = None,
    call_id: str | None = None,
    worker_id: str | None = None,
    endpoint: str | None = None,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts": _utc_now_iso(),
        "level": (level or "INFO").upper(),
        "run_id": run_id or "-",
        "call_id": call_id or "-",
        "worker_id": worker_id or "-",
        "endpoint": endpoint or "-",
        "event": event or "unknown",
        "code": code or "unknown",
    }
    if isinstance(fields, Mapping):
        payload.update(dict(fields))
    sanitized = sanitize_log_record(payload)
    _LOG_RECORDS.append(sanitized)
    return sanitized


def sanitize_log_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_value(dict(payload))
    if not isinstance(sanitized, dict):
        sanitized = {}
    for field_name in _REQUIRED_LOG_FIELDS:
        if field_name not in sanitized:
            sanitized[field_name] = "-"
    return sanitized


@contextmanager
def trace_span(
    name: str,
    *,
    enabled: bool | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
    worker_id: str | None = None,
    endpoint: str | None = None,
    attrs: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    trace_enabled = is_trace_enabled() if enabled is None else enabled
    started_at = perf_counter()
    started_ts = _utc_now_iso()
    error_code: str | None = None
    try:
        yield
    except Exception as exc:
        error_code = getattr(exc, "code", None) if isinstance(getattr(exc, "code", None), str) else exc.__class__.__name__
        raise
    finally:
        if not trace_enabled:
            return
        try:
            _TRACE_SPANS.append(
                {
                    "name": name,
                    "ts": started_ts,
                    "duration_ms": max(0, int((perf_counter() - started_at) * 1000)),
                    "run_id": run_id or "-",
                    "call_id": call_id or "-",
                    "worker_id": worker_id or "-",
                    "endpoint": endpoint or "-",
                    "status": "error" if error_code else "ok",
                    "code": error_code or "OK",
                    "attrs": _sanitize_value(dict(attrs) if isinstance(attrs, Mapping) else {}),
                }
            )
        except Exception:
            return


def is_trace_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = env if env is not None else os.environ
    raw = values.get("CN_A_SOCIAL_TRACE_ENABLED", "")
    return raw.strip().lower() in _TRUE_VALUES


def reset_observability_state() -> None:
    _METRIC_POINTS.clear()
    _LOG_RECORDS.clear()
    _TRACE_SPANS.clear()


def get_recorded_metrics() -> list[dict[str, Any]]:
    return [dict(point) for point in _METRIC_POINTS]


def get_recorded_logs() -> list[dict[str, Any]]:
    return [dict(record) for record in _LOG_RECORDS]


def get_recorded_spans() -> list[dict[str, Any]]:
    return [dict(span) for span in _TRACE_SPANS]


def _sanitize_value(value: Any, key_hint: str | None = None) -> Any:
    if _is_payload_key(key_hint):
        return _REDACTED_PAYLOAD

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                result[key] = _REDACTED
            elif _is_payload_key(key):
                result[key] = _REDACTED_PAYLOAD
            else:
                result[key] = _sanitize_value(raw_value, key_hint=key)
        return result

    if isinstance(value, list):
        if key_hint and _is_payload_key(key_hint):
            return _REDACTED_PAYLOAD
        return [_sanitize_value(item, key_hint=key_hint) for item in value]

    if isinstance(value, tuple):
        if key_hint and _is_payload_key(key_hint):
            return _REDACTED_PAYLOAD
        return [_sanitize_value(item, key_hint=key_hint) for item in value]

    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return ""
        if _looks_sensitive_string(text):
            return _REDACTED
        if len(text) > _MAX_TEXT_LENGTH:
            return f"{text[:_MAX_TEXT_LENGTH]}...(truncated)"
        return text
    return value


def _looks_sensitive_string(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith(_MONGODB_URI_PREFIXES):
        return True
    if "api_key=" in lowered or "token=" in lowered or "authorization" in lowered:
        return True
    return False


def _is_sensitive_key(key: str | None) -> bool:
    if key is None:
        return False
    lowered = key.strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def _is_payload_key(key: str | None) -> bool:
    if key is None:
        return False
    lowered = key.strip().lower()
    return any(token in lowered for token in _PAYLOAD_KEY_TOKENS)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def dumps_json_log(record: Mapping[str, Any]) -> str:
    return json.dumps(sanitize_log_record(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
