from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
import time
from typing import Any, Callable, Mapping

METRIC_LABEL_ALLOWLIST = frozenset(
    {
        "worker_id",
        "market",
        "provider",
        "api_name",
        "status",
        "quality_status",
    }
)

FUNDAMENTAL_FAILED_RATIO_5M_THRESHOLD = 0.05
FUNDAMENTAL_MISSING_TOKEN_15M_THRESHOLD = 0
FUNDAMENTAL_RAW_WRITE_ERROR_5M_THRESHOLD = 0
FUNDAMENTAL_UNSUPPORTED_CLAIM_10M_MIN_GROWTH = 1

_lock = Lock()
_metric_points: list[dict[str, Any]] = []


@dataclass(frozen=True)
class FundamentalAlertSnapshot:
    failed_pack_total_5m: int
    pack_total_5m: int
    missing_token_15m: int
    raw_write_error_5m: int
    unsupported_claim_10m: int
    unsupported_claim_prev_10m: int


@dataclass(frozen=True)
class FundamentalAlertEvent:
    reason: str
    run_id: str
    dispatch_id: str
    observed: float
    threshold: float


@dataclass(frozen=True)
class FundamentalAuditLogRecord:
    run_id: str
    dispatch_id: str
    worker_id: str
    market: str
    event: str
    status: str
    reason: str | None
    timestamp: str


@dataclass(frozen=True)
class ShutdownResult:
    unfinished_request_ids: tuple[str, ...]
    waited_seconds: float


class FundamentalShutdownHook:
    def __init__(self, *, max_wait_seconds: int = 30) -> None:
        self._max_wait_seconds = max_wait_seconds
        self._accepting_new = True
        self._inflight: set[str] = set()
        self._lock = Lock()

    def begin_request(self, request_id: str) -> bool:
        with self._lock:
            if not self._accepting_new:
                return False
            self._inflight.add(request_id)
            return True

    def finish_request(self, request_id: str) -> None:
        with self._lock:
            self._inflight.discard(request_id)

    def graceful_shutdown(
        self,
        *,
        write_unfinished_failed_diagnostics: Callable[[tuple[str, ...]], None],
        close_mongo_pool: Callable[[], None],
        flush_metrics_and_audit_logs: Callable[[], None],
        sleep_interval_seconds: float = 0.05,
    ) -> ShutdownResult:
        started = time.monotonic()
        with self._lock:
            self._accepting_new = False

        while True:
            with self._lock:
                has_inflight = bool(self._inflight)
            elapsed = time.monotonic() - started
            if not has_inflight or elapsed >= self._max_wait_seconds:
                break
            time.sleep(sleep_interval_seconds)

        with self._lock:
            unfinished = tuple(sorted(self._inflight))
            self._inflight.clear()

        if unfinished:
            write_unfinished_failed_diagnostics(unfinished)
        close_mongo_pool()
        flush_metrics_and_audit_logs()
        return ShutdownResult(unfinished_request_ids=unfinished, waited_seconds=time.monotonic() - started)


def sanitize_metric_labels(labels: Mapping[str, Any] | None) -> dict[str, str]:
    if labels is None:
        return {}
    sanitized: dict[str, str] = {}
    for key in sorted(labels.keys()):
        if key not in METRIC_LABEL_ALLOWLIST:
            continue
        sanitized[key] = _normalize_label_value(labels[key])
    return sanitized


def record_provider_attempt(labels: Mapping[str, Any], duration_ms: int) -> None:
    _record_metric("fundamental_provider_attempt_total", labels=labels, value=1)
    _record_metric("fundamental_provider_attempt_duration_ms", labels=labels, value=max(0, duration_ms))


def record_pack_quality(labels: Mapping[str, Any]) -> None:
    _record_metric("fundamental_pack_quality_status_total", labels=labels, value=1)


def record_raw_payload_write(labels: Mapping[str, Any]) -> None:
    _record_metric("fundamental_raw_payload_write_total", labels=labels, value=1)


def record_gate_fail(labels: Mapping[str, Any]) -> None:
    _record_metric("fundamental_gate_fail_total", labels=labels, value=1)


def snapshot_metrics() -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _metric_points]


def evaluate_alerts(
    snapshot: FundamentalAlertSnapshot,
    *,
    run_id: str,
    dispatch_id: str,
) -> list[FundamentalAlertEvent]:
    events: list[FundamentalAlertEvent] = []
    failed_ratio = 0.0
    if snapshot.pack_total_5m > 0:
        failed_ratio = snapshot.failed_pack_total_5m / snapshot.pack_total_5m
    if failed_ratio > FUNDAMENTAL_FAILED_RATIO_5M_THRESHOLD:
        events.append(
            FundamentalAlertEvent(
                reason="failed_ratio_5m",
                run_id=run_id,
                dispatch_id=dispatch_id,
                observed=failed_ratio,
                threshold=FUNDAMENTAL_FAILED_RATIO_5M_THRESHOLD,
            )
        )
    if snapshot.missing_token_15m > FUNDAMENTAL_MISSING_TOKEN_15M_THRESHOLD:
        events.append(
            FundamentalAlertEvent(
                reason="missing_token_15m",
                run_id=run_id,
                dispatch_id=dispatch_id,
                observed=float(snapshot.missing_token_15m),
                threshold=float(FUNDAMENTAL_MISSING_TOKEN_15M_THRESHOLD),
            )
        )
    if snapshot.raw_write_error_5m > FUNDAMENTAL_RAW_WRITE_ERROR_5M_THRESHOLD:
        events.append(
            FundamentalAlertEvent(
                reason="raw_write_error_5m",
                run_id=run_id,
                dispatch_id=dispatch_id,
                observed=float(snapshot.raw_write_error_5m),
                threshold=float(FUNDAMENTAL_RAW_WRITE_ERROR_5M_THRESHOLD),
            )
        )
    unsupported_growth = snapshot.unsupported_claim_10m - snapshot.unsupported_claim_prev_10m
    if unsupported_growth >= FUNDAMENTAL_UNSUPPORTED_CLAIM_10M_MIN_GROWTH:
        events.append(
            FundamentalAlertEvent(
                reason="unsupported_claim_10m_increase",
                run_id=run_id,
                dispatch_id=dispatch_id,
                observed=float(unsupported_growth),
                threshold=float(FUNDAMENTAL_UNSUPPORTED_CLAIM_10M_MIN_GROWTH),
            )
        )
    return events


def _record_metric(name: str, *, labels: Mapping[str, Any], value: float) -> None:
    point = {
        "name": name,
        "labels": sanitize_metric_labels(labels),
        "value": float(value),
    }
    with _lock:
        _metric_points.append(point)


def _normalize_label_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else "none"
    return str(value)
