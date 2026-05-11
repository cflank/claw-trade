from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from config import FundamentalDataConfig
from security import sanitize_error

MONGO_READINESS_TIMEOUT_MS = 500
OPENVIKING_READINESS_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class HealthDependencyStatus:
    ok: bool
    latency_ms: int
    reason: str | None


@dataclass(frozen=True)
class LivenessStatus:
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class ReadinessStatus:
    ok: bool
    token: HealthDependencyStatus
    mongodb: HealthDependencyStatus
    openviking: HealthDependencyStatus


def liveness(config: FundamentalDataConfig | None, *, config_error: Exception | None = None) -> LivenessStatus:
    if config_error is not None:
        return LivenessStatus(ok=False, reason=sanitize_error(config_error))
    if config is None:
        return LivenessStatus(ok=False, reason="config_not_loaded")
    return LivenessStatus(ok=True, reason=None)


def readiness(
    config: FundamentalDataConfig,
    *,
    mongo_ping: Callable[[int], bool],
    openviking_health: Callable[[int], bool],
) -> ReadinessStatus:
    token_ok = bool(config.tushare_token and config.tushare_token.strip())
    token_status = HealthDependencyStatus(
        ok=token_ok,
        latency_ms=0,
        reason=None if token_ok else "missing_token",
    )
    mongodb_status = _probe_dependency(
        timeout_ms=MONGO_READINESS_TIMEOUT_MS,
        probe=mongo_ping,
    )
    openviking_status = _probe_dependency(
        timeout_ms=OPENVIKING_READINESS_TIMEOUT_MS,
        probe=openviking_health,
    )
    return ReadinessStatus(
        ok=token_status.ok and mongodb_status.ok and openviking_status.ok,
        token=token_status,
        mongodb=mongodb_status,
        openviking=openviking_status,
    )


def _probe_dependency(*, timeout_ms: int, probe: Callable[[int], bool]) -> HealthDependencyStatus:
    started = perf_counter()
    try:
        ok = bool(probe(timeout_ms))
    except Exception as exc:
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        return HealthDependencyStatus(ok=False, latency_ms=duration_ms, reason=sanitize_error(exc))
    duration_ms = max(0, int((perf_counter() - started) * 1000))
    return HealthDependencyStatus(ok=ok, latency_ms=duration_ms, reason=None if ok else "probe_failed")
