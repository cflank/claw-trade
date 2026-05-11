from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import time
from typing import Any, Callable, Iterable, Mapping

from pymongo import MongoClient

from cache import SOCIAL_CACHE_REQUIRED_MISSING, SOCIAL_CACHE_WRITE_FAILED
from config import SocialDataConfig
from evidence import (
    SOCIAL_OPENVIKING_AUTH_FAILED,
    SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH,
    OpenVikingEvidenceWriter,
    resolve_openviking_evidence_writer,
)
from profile import DateWindow
from providers import ProviderQuery, SOCIAL_PROVIDER_TIMEOUT, fetch_provider_payload

SOCIAL_STARTUP_CHECK_FAILED = "SOCIAL_STARTUP_CHECK_FAILED"
SOCIAL_SHUTTING_DOWN = "SOCIAL_SHUTTING_DOWN"
P0_ALL_FAILED = "P0_ALL_FAILED"

ALERT_PRIORITY_HIGH = "high"
ALERT_PRIORITY_MEDIUM = "medium"
ALERT_PRIORITY_INFO = "info"

_DEFAULT_HEALTH_COMPONENTS = ("mongodb", "openviking", "provider")
_HIGH_PRIORITY_ALERT_CODES = frozenset(
    {
        SOCIAL_OPENVIKING_AUTH_FAILED,
        SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH,
        P0_ALL_FAILED,
    }
)
_MEDIUM_PRIORITY_ALERT_CODES = frozenset(
    {
        SOCIAL_CACHE_WRITE_FAILED,
        SOCIAL_PROVIDER_TIMEOUT,
    }
)


@dataclass(frozen=True)
class StartupCheckItem:
    name: str
    ok: bool
    blocking: bool
    code: str | None
    detail: str | None


@dataclass(frozen=True)
class StartupCheckResult:
    ok: bool
    checks: tuple[StartupCheckItem, ...]


@dataclass(frozen=True)
class AlertEvent:
    code: str
    priority: str
    message: str | None
    component: str | None
    occurred_at: str
    context: dict[str, str]


@dataclass(frozen=True)
class RuntimeHealthSnapshot:
    consecutive_errors: dict[str, int]
    last_success_at: dict[str, str | None]


@dataclass(frozen=True)
class CancelledEndpoint:
    pack_id: str
    endpoint: str


@dataclass(frozen=True)
class ShutdownDrainResult:
    drained: bool
    cancelled: tuple[CancelledEndpoint, ...]
    remaining_pack_ids: tuple[str, ...]


@dataclass
class _ActivePack:
    endpoints: set[str] = field(default_factory=set)
    completed_endpoints: set[str] = field(default_factory=set)


class RuntimeHealthTracker:
    def __init__(self, components: Iterable[str] | None = None) -> None:
        items = tuple(components or _DEFAULT_HEALTH_COMPONENTS)
        self._consecutive_errors: dict[str, int] = {name: 0 for name in items}
        self._last_success_at: dict[str, str | None] = {name: None for name in items}

    def record_success(self, component: str, *, now: datetime | None = None) -> None:
        ts = _to_utc_iso(now)
        self._consecutive_errors[component] = 0
        self._last_success_at[component] = ts

    def record_failure(self, component: str) -> int:
        self._consecutive_errors[component] = self._consecutive_errors.get(component, 0) + 1
        self._last_success_at.setdefault(component, None)
        return self._consecutive_errors[component]

    def snapshot(self) -> RuntimeHealthSnapshot:
        return RuntimeHealthSnapshot(
            consecutive_errors=dict(self._consecutive_errors),
            last_success_at=dict(self._last_success_at),
        )


class GracefulShutdownController:
    def __init__(
        self,
        *,
        pack_timeout_seconds: int,
        monotonic_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.pack_timeout_seconds = max(1, int(pack_timeout_seconds))
        self._monotonic = monotonic_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self._accepting_new_calls = True
        self._shutdown_deadline: float | None = None
        self._active_packs: dict[str, _ActivePack] = {}

    @property
    def accepting_new_calls(self) -> bool:
        return self._accepting_new_calls

    def begin_pack(self, pack_id: str, endpoints: Iterable[str]) -> bool:
        if not self._accepting_new_calls:
            return False
        endpoint_set = {name for name in endpoints if isinstance(name, str) and name.strip()}
        self._active_packs[pack_id] = _ActivePack(endpoints=endpoint_set, completed_endpoints=set())
        return True

    def mark_endpoint_complete(self, pack_id: str, endpoint: str) -> None:
        active = self._active_packs.get(pack_id)
        if active is None:
            return
        if endpoint in active.endpoints:
            active.completed_endpoints.add(endpoint)

    def finish_pack(self, pack_id: str) -> None:
        self._active_packs.pop(pack_id, None)

    def begin_shutdown(self) -> None:
        self._accepting_new_calls = False
        self._shutdown_deadline = self._monotonic() + self.pack_timeout_seconds

    def drain(self, *, poll_interval_seconds: float = 0.05) -> ShutdownDrainResult:
        if self._shutdown_deadline is None:
            return ShutdownDrainResult(drained=True, cancelled=(), remaining_pack_ids=tuple(sorted(self._active_packs)))

        while self._active_packs and self._monotonic() < self._shutdown_deadline:
            self._sleep(max(0.01, poll_interval_seconds))

        if not self._active_packs:
            return ShutdownDrainResult(drained=True, cancelled=(), remaining_pack_ids=())

        cancelled: list[CancelledEndpoint] = []
        for pack_id, active in self._active_packs.items():
            pending = active.endpoints - active.completed_endpoints
            for endpoint in sorted(pending):
                cancelled.append(CancelledEndpoint(pack_id=pack_id, endpoint=endpoint))
        remaining = tuple(sorted(self._active_packs))
        return ShutdownDrainResult(drained=False, cancelled=tuple(cancelled), remaining_pack_ids=remaining)


def run_startup_checks(
    config: SocialDataConfig,
    *,
    writer: OpenVikingEvidenceWriter | None = None,
    mongodb_probe: Callable[[SocialDataConfig], tuple[bool, str | None]] | None = None,
    openviking_binding_probe: Callable[[OpenVikingEvidenceWriter | None], tuple[bool, str | None]] | None = None,
    akshare_probe: Callable[[SocialDataConfig], tuple[bool, str | None]] | None = None,
) -> StartupCheckResult:
    mongo_probe = mongodb_probe or _probe_mongodb_connectivity
    writer_probe = openviking_binding_probe or _probe_openviking_binding
    provider_probe = akshare_probe or _probe_akshare_reachability

    mongo_ok, mongo_detail = mongo_probe(config)
    mongo_blocking = bool(config.cache_required)
    mongo_code: str | None = None
    if not mongo_ok and config.cache_required:
        mongo_code = SOCIAL_CACHE_REQUIRED_MISSING

    writer_ok, writer_detail = writer_probe(writer)
    provider_ok, provider_detail = provider_probe(config)

    checks = (
        StartupCheckItem(
            name="mongodb_connectivity",
            ok=mongo_ok,
            blocking=mongo_blocking,
            code=mongo_code,
            detail=mongo_detail,
        ),
        StartupCheckItem(
            name="openviking_writer_binding",
            ok=writer_ok,
            blocking=True,
            code=SOCIAL_STARTUP_CHECK_FAILED if not writer_ok else None,
            detail=writer_detail,
        ),
        StartupCheckItem(
            name="akshare_basic_reachability",
            ok=provider_ok,
            blocking=True,
            code=SOCIAL_STARTUP_CHECK_FAILED if not provider_ok else None,
            detail=provider_detail,
        ),
    )
    passed = all(item.ok or not item.blocking for item in checks)
    return StartupCheckResult(ok=passed, checks=checks)


def build_alert_event(
    *,
    code: str,
    message: str | None = None,
    component: str | None = None,
    occurred_at: datetime | None = None,
    context: Mapping[str, Any] | None = None,
) -> AlertEvent:
    return AlertEvent(
        code=code,
        priority=_resolve_alert_priority(code),
        message=message,
        component=component,
        occurred_at=_to_utc_iso(occurred_at),
        context=_stringify_context(context),
    )


def _resolve_alert_priority(code: str) -> str:
    if code in _HIGH_PRIORITY_ALERT_CODES:
        return ALERT_PRIORITY_HIGH
    if code in _MEDIUM_PRIORITY_ALERT_CODES:
        return ALERT_PRIORITY_MEDIUM
    return ALERT_PRIORITY_INFO


def _probe_mongodb_connectivity(config: SocialDataConfig) -> tuple[bool, str | None]:
    if not config.mongodb_uri:
        if config.cache_required:
            return False, "cache_required=true 且缺少 MongoDB URI"
        return True, "MongoDB 未配置（cache_required=false）"

    client: MongoClient[Any] = MongoClient(config.mongodb_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
        return True, None
    except Exception as exc:
        return False, f"{exc.__class__.__name__}:{exc}"
    finally:
        client.close()


def _probe_openviking_binding(writer: OpenVikingEvidenceWriter | None) -> tuple[bool, str | None]:
    try:
        resolved = resolve_openviking_evidence_writer(writer)
    except Exception as exc:
        return False, f"{exc.__class__.__name__}:{exc}"
    if not callable(getattr(resolved, "write_json", None)):
        return False, "writer 缺少 write_json"
    return True, None


def _probe_akshare_reachability(config: SocialDataConfig) -> tuple[bool, str | None]:
    today = datetime.now(UTC).date().isoformat()
    query = ProviderQuery(
        provider="akshare",
        endpoint="stock_hot_rank_em",
        priority="P0",
        query={},
        query_fingerprint="startup-probe",
        date_window=DateWindow(start_date=today, end_date=today, as_of_date=today),
        timeout_seconds=config.provider_timeout_seconds,
    )
    result = fetch_provider_payload(query)
    if result.status in {"timeout", "error", "cancelled"}:
        code = result.error["code"] if isinstance(result.error, dict) and "code" in result.error else result.status
        return False, f"{code}:{result.error}"
    return True, None


def _to_utc_iso(now: datetime | None) -> str:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _stringify_context(context: Mapping[str, Any] | None) -> dict[str, str]:
    if context is None:
        return {}
    rendered: dict[str, str] = {}
    for key, value in context.items():
        if not isinstance(key, str) or not key.strip():
            continue
        rendered[key] = str(value)
    return rendered
