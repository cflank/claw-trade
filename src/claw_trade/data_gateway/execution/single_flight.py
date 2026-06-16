from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from claw_trade.data_gateway.warehouse.repository import DatasetRepository

from . import ResultRefs


class SupportsPublishResult(Protocol):
    status: str
    dataset_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    gaps: tuple[Any, ...]


@dataclass(frozen=True)
class PublishedFlightResult:
    refs: ResultRefs
    gap_summary: tuple[dict[str, Any], ...] = ()
    error_summary: str | None = None


@dataclass(frozen=True)
class SingleFlightDecision:
    kind: Literal["owner", "waiter", "shared"]
    owner_token: str | None = None
    published: PublishedFlightResult | None = None

    @classmethod
    def owner(cls, owner_token: str) -> "SingleFlightDecision":
        return cls(kind="owner", owner_token=owner_token)

    @classmethod
    def waiter(cls, owner_token: str) -> "SingleFlightDecision":
        return cls(kind="waiter", owner_token=owner_token)

    @classmethod
    def shared(cls, published: PublishedFlightResult) -> "SingleFlightDecision":
        return cls(kind="shared", published=published)


class SingleFlight:
    def __init__(
        self,
        repository: DatasetRepository | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._repository = repository or DatasetRepository()
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn or sleep

    def acquire(self, key: str, lease_ttl_seconds: int) -> SingleFlightDecision:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be > 0")
        token = uuid4().hex
        now = self._aware_utc(self._now()) or datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        if self._repository.try_insert_single_flight_call(
            key=key,
            owner_token=token,
            lease_expires_at=lease_expires_at,
        ):
            return SingleFlightDecision.owner(token)
        row = self._repository.read_single_flight_call(key)
        if row is None:
            return SingleFlightDecision.waiter(token)
        status = str(row.get("status", "pending"))
        lease_until = self._aware_utc(row.get("lease_expires_at"))
        if status == "published_success" and lease_until is not None and lease_until > now:
            return SingleFlightDecision.shared(self._published_from_row(row))
        if status == "published_error" and lease_until is not None and lease_until > now:
            return SingleFlightDecision.shared(self._published_from_row(row))
        if status == "pending" and lease_until is not None and lease_until > now:
            return SingleFlightDecision.waiter(str(row.get("owner_id") or ""))
        if self._repository.cas_takeover_expired_single_flight(
            key=key,
            expected_owner_token=str(row.get("owner_id") or ""),
            expected_version=int(row.get("version", 0)),
            owner_token=token,
            lease_expires_at=lease_expires_at,
        ):
            return SingleFlightDecision.owner(token)
        latest = self._repository.read_single_flight_call(key)
        if latest is None:
            return SingleFlightDecision.waiter(token)
        return SingleFlightDecision.waiter(str(latest.get("owner_id") or ""))

    def wait(self, key: str, timeout_seconds: int) -> PublishedFlightResult:
        deadline = monotonic() + max(timeout_seconds, 0)
        while True:
            row = self._repository.read_single_flight_call(key)
            if row is not None and str(row.get("status")) in {"published_success", "published_error"}:
                return self._published_from_row(row)
            if monotonic() >= deadline:
                raise TimeoutError(f"single-flight wait timed out for key={key}")
            self._sleep(0.01)

    def publish(self, key: str, owner_token: str, result: SupportsPublishResult) -> bool:
        row = self._repository.read_single_flight_call(key)
        if row is None:
            return False
        blocker = self._has_blocker_gap(result.gaps)
        status = "published_success" if (result.dataset_refs or result.raw_refs) and not blocker and result.status != "failed" else "published_error"
        return self._repository.cas_publish_single_flight_if_owner(
            key=key,
            owner_token=owner_token,
            expected_version=int(row.get("version", 0)),
            status=status,
            dataset_refs=tuple(result.dataset_refs),
            raw_refs=tuple(result.raw_refs),
            attempt_refs=tuple(result.attempt_refs),
            gap_summary=tuple(self._gap_to_summary(gap) for gap in result.gaps),
            error_summary="failed" if status == "published_error" else None,
        )

    @staticmethod
    def _aware_utc(value: Any) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _published_from_row(row: dict[str, Any]) -> PublishedFlightResult:
        return PublishedFlightResult(
            refs=ResultRefs(
                dataset_refs=tuple(row.get("result_dataset_refs", ())),
                raw_refs=tuple(row.get("result_raw_refs", ())),
                attempt_refs=tuple(row.get("result_attempt_refs", ())),
            ),
            gap_summary=tuple(row.get("gap_summary", ())),
            error_summary=row.get("error_summary"),
        )

    @staticmethod
    def _gap_to_summary(gap: Any) -> dict[str, Any]:
        reason = getattr(gap, "reason", None)
        if reason is None and isinstance(gap, dict):
            reason = gap.get("reason")
        return {"reason": reason or "unknown"}

    @staticmethod
    def _has_blocker_gap(gaps: tuple[Any, ...]) -> bool:
        if not gaps:
            return False
        blocker_reasons = {
            "credential_missing",
            "rate_limited",
            "provider_error",
            "empty_result",
            "field_missing",
            "date_range_missing",
            "granularity_mismatch",
            "license_blocked",
            "evidence_write_failed",
            "cached_empty",
            "cooldown_skipped",
            "sdk_http_unknown",
            "invalid_request",
        }
        for gap in gaps:
            reason = getattr(gap, "reason", None)
            if isinstance(gap, dict):
                reason = gap.get("reason", reason)
            reason = str(getattr(reason, "value", reason))
            if reason in blocker_reasons:
                return True
        return False
