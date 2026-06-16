from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from claw_trade.data_gateway.warehouse.repository import DatasetRepository

from . import NON_REMOTE_ATTEMPT_STATUSES, DataGap

_LOCAL_QUOTA_SIGNALS = {"local_rate_limited", "rate_limited_by_tool_budget"}


@dataclass(frozen=True)
class AttemptRecord:
    attempt_ref: str
    provider_id: str
    endpoint_id: str
    status: str
    remote_attempted: bool
    remote_success: bool
    http_visibility: str
    http_audit_status: str
    dataset_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    gap_codes: tuple[str, ...]
    owner_attempt_ref: str | None = None


class AttemptLog:
    def __init__(self, repository: DatasetRepository | None = None) -> None:
        self._repository = repository or DatasetRepository()

    def record(
        self,
        *,
        batch: Any,
        fetch_result: Any | None = None,
        gate: Any | None = None,
        raw_refs: tuple[str, ...] = (),
        dataset_refs: tuple[str, ...] = (),
        gaps: tuple[DataGap, ...] = (),
        remote_success: bool = False,
    ) -> tuple[str, ...]:
        status = self._status_from(fetch_result=fetch_result, gate=gate)
        if status in NON_REMOTE_ATTEMPT_STATUSES and remote_success:
            raise ValueError(f"{status} must not be marked remote_success")
        if status == "sdk_http_unknown" and getattr(fetch_result, "http_observations", ()):
            raise ValueError("sdk_http_unknown must not fabricate http evidence")

        attempt_ref = f"attempt:{batch.provider_id}:{batch.endpoint_id}:{uuid4().hex[:12]}"
        http_visibility = _http_visibility(batch)
        record = AttemptRecord(
            attempt_ref=attempt_ref,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            status=status,
            remote_attempted=_remote_attempted(fetch_result),
            remote_success=remote_success,
            http_visibility=http_visibility,
            http_audit_status=_http_audit_status(fetch_result=fetch_result, http_visibility=http_visibility),
            dataset_refs=tuple(dataset_refs),
            raw_refs=tuple(raw_refs),
            gap_codes=tuple(gap.reason for gap in gaps),
            owner_attempt_ref=getattr(gate, "owner_attempt_ref", None),
        )
        persisted_ref = self._repository.insert_provider_attempt(
            {
                "attempt_ref": record.attempt_ref,
                "provider": record.provider_id,
                "endpoint": record.endpoint_id,
                "status": record.status,
                "remote_attempted": record.remote_attempted,
                "remote_success": record.remote_success,
                "http_visibility": record.http_visibility,
                "http_audit_status": record.http_audit_status,
                "dataset_refs": record.dataset_refs,
                "raw_refs": record.raw_refs,
                "gap_codes": tuple(getattr(code, "value", str(code)) for code in record.gap_codes),
                "owner_attempt_ref": record.owner_attempt_ref,
                "created_at": getattr(fetch_result, "fetched_at", None),
                "error_code": getattr(fetch_result, "error_code", None),
                "error_message": getattr(fetch_result, "error_message", None),
                **_http_observation_summary(fetch_result),
            }
        )
        return (persisted_ref,)

    def record_selector_skip(
        self,
        *,
        batch: Any,
        selector_skip: Any,
        gaps: tuple[DataGap, ...] = (),
    ) -> tuple[str, ...]:
        raw_status = getattr(selector_skip, "reason", "credential_missing") or "credential_missing"
        status = str(getattr(raw_status, "value", raw_status))
        attempt_ref = f"attempt:{batch.provider_id}:{batch.endpoint_id}:{uuid4().hex[:12]}"
        http_visibility = _http_visibility(batch)
        record = AttemptRecord(
            attempt_ref=attempt_ref,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            status=status,
            remote_attempted=bool(getattr(selector_skip, "remote_attempted", False)),
            remote_success=False,
            http_visibility=http_visibility,
            http_audit_status="no_remote_attempt",
            dataset_refs=(),
            raw_refs=(),
            gap_codes=tuple(gap.reason for gap in gaps),
            owner_attempt_ref=None,
        )
        persisted_ref = self._repository.insert_provider_attempt(
            {
                "attempt_ref": record.attempt_ref,
                "provider": record.provider_id,
                "endpoint": record.endpoint_id,
                "status": record.status,
                "remote_attempted": record.remote_attempted,
                "remote_success": record.remote_success,
                "http_visibility": record.http_visibility,
                "http_audit_status": record.http_audit_status,
                "dataset_refs": record.dataset_refs,
                "raw_refs": record.raw_refs,
                "gap_codes": tuple(getattr(code, "value", str(code)) for code in record.gap_codes),
                "owner_attempt_ref": record.owner_attempt_ref,
                "created_at": None,
            }
        )
        return (persisted_ref,)

    @staticmethod
    def _status_from(*, fetch_result: Any | None, gate: Any | None) -> str:
        if gate is not None:
            return str(gate.kind)
        if fetch_result is None:
            return "not_applicable"
        raw_status = getattr(fetch_result, "status", "")
        status = str(getattr(raw_status, "value", raw_status))
        if status == "empty":
            return "provider_empty"
        if status == "error":
            return "provider_error"
        return status


def _http_observation_summary(fetch_result: Any | None) -> dict[str, Any]:
    observations = tuple(getattr(fetch_result, "http_observations", ()) or ()) if fetch_result is not None else ()
    if not observations:
        return {
            "http_observations": (),
            "http_status_codes": (),
            "quota_signals": (),
            "rate_limit_origin": None,
        }
    serialized = tuple(_serialize_http_observation(observation) for observation in observations)
    status_codes = tuple(
        item["status_code"]
        for item in serialized
        if item.get("status_code") is not None
    )
    quota_signals = tuple(
        item["quota_signal"]
        for item in serialized
        if item.get("quota_signal")
    )
    return {
        "http_observations": serialized,
        "http_status_codes": status_codes,
        "quota_signals": quota_signals,
        "rate_limit_origin": _rate_limit_origin(serialized),
    }


def _http_visibility(batch: Any) -> str:
    raw = getattr(batch, "http_visibility", "managed_http")
    return str(getattr(raw, "value", raw) or "managed_http")


def _http_audit_status(*, fetch_result: Any | None, http_visibility: str) -> str:
    observations = tuple(getattr(fetch_result, "http_observations", ()) or ()) if fetch_result is not None else ()
    if http_visibility == "sdk_internal_unknown" and not observations:
        return "sdk_internal_unknown"
    if http_visibility == "no_http":
        return "no_http"
    if observations:
        return "http_observed"
    return "http_not_observed"


def _serialize_http_observation(observation: Any) -> Mapping[str, Any]:
    return {
        "request_key": getattr(observation, "request_key", None),
        "sent_at": getattr(observation, "sent_at", None),
        "method": getattr(observation, "method", None),
        "host": getattr(observation, "host", None),
        "path": getattr(observation, "path", None),
        "status_code": getattr(observation, "status_code", None),
        "quota_signal": getattr(observation, "quota_signal", None),
        "error_code": getattr(observation, "error_code", None),
        "elapsed_ms": getattr(observation, "elapsed_ms", None),
        "response_headers_redacted": dict(getattr(observation, "response_headers_redacted", None) or {}),
    }


def _rate_limit_origin(observations: tuple[Mapping[str, Any], ...]) -> str | None:
    local = any(item.get("quota_signal") in _LOCAL_QUOTA_SIGNALS for item in observations)
    remote = any(
        item.get("status_code") == 429
        or (item.get("quota_signal") not in {None, "", *_LOCAL_QUOTA_SIGNALS})
        for item in observations
    )
    if local and remote:
        return "mixed"
    if local:
        return "local"
    if remote:
        return "remote"
    return None


def _remote_attempted(fetch_result: Any | None) -> bool:
    if fetch_result is None:
        return False
    observations = tuple(getattr(fetch_result, "http_observations", ()) or ())
    if not observations:
        return True
    return any(
        getattr(observation, "status_code", None) is not None
        or getattr(observation, "quota_signal", None) not in _LOCAL_QUOTA_SIGNALS
        for observation in observations
    )
