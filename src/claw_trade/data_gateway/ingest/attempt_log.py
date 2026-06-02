from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from . import DataGap, NON_REMOTE_ATTEMPT_STATUSES
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


@dataclass(frozen=True)
class AttemptRecord:
    attempt_ref: str
    provider_id: str
    endpoint_id: str
    status: str
    remote_attempted: bool
    remote_success: bool
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
        record = AttemptRecord(
            attempt_ref=attempt_ref,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            status=status,
            remote_attempted=fetch_result is not None,
            remote_success=remote_success,
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
                "dataset_refs": record.dataset_refs,
                "raw_refs": record.raw_refs,
                "gap_codes": tuple(getattr(code, "value", str(code)) for code in record.gap_codes),
                "owner_attempt_ref": record.owner_attempt_ref,
                "created_at": getattr(fetch_result, "fetched_at", None),
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
            return "empty_result"
        if status == "error":
            return "provider_error"
        return status
