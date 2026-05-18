from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pymongo.errors import DuplicateKeyError, PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import PrioritySource, ProviderAttempt, ProviderKind, ProviderStatus, SourceRole

from .mongo import OPENBB_PROVIDER_ATTEMPTS


class MongoAttemptStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_PROVIDER_ATTEMPTS

    def write(self, attempt: ProviderAttempt) -> str:
        self._validate_single_flight_contract(attempt)
        doc = _serialize_attempt(attempt)
        try:
            self.collection.insert_one(doc)
        except DuplicateKeyError:
            existing = self.collection.find_one({"_id": attempt.attempt_id})
            if existing is None:
                raise DataGatewayError(
                    DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                    f"attempt duplicate but not readable: {attempt.attempt_id}",
                )
            if _documents_equal_without_id(existing, doc):
                return attempt.attempt_id
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"attempt id conflict with different payload: {attempt.attempt_id}",
            )
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"attempt write failed: {exc}",
            ) from exc
        return attempt.attempt_id

    def get(self, attempt_id: str) -> ProviderAttempt | None:
        doc = self.collection.find_one({"_id": attempt_id})
        if doc is None:
            return None
        return _deserialize_attempt(doc)

    def _validate_single_flight_contract(self, attempt: ProviderAttempt) -> None:
        if attempt.single_flight_role == "consumer" and attempt.status == ProviderStatus.REMOTE_SUCCESS:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                "single-flight consumer cannot write remote_success; use shared_result or owner-equivalent failure",
            )
        if attempt.status == ProviderStatus.REMOTE_SUCCESS and (not attempt.raw_ref or not attempt.normalized_ref):
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                "remote_success attempt missing raw_ref/normalized_ref",
            )


def _serialize_attempt(attempt: ProviderAttempt) -> dict[str, Any]:
    data = asdict(attempt)
    data["_id"] = attempt.attempt_id
    data["provider_kind"] = attempt.provider_kind.value
    data["source_role"] = attempt.source_role.value
    data["status"] = attempt.status.value
    data["priority_source"] = attempt.priority_source.value
    data["cache_status"] = attempt.cache_status.value if attempt.cache_status is not None else None
    return data


def _deserialize_attempt(doc: dict[str, Any]) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id=str(doc["_id"]),
        run_id=str(doc["run_id"]),
        call_id=str(doc["call_id"]),
        worker_id=str(doc["worker_id"]),
        pack=str(doc["pack"]),
        provider=str(doc["provider"]),
        adapter_id=str(doc["adapter_id"]),
        adapter_kind=str(doc["adapter_kind"]),
        provider_kind=ProviderKind(str(doc["provider_kind"])),
        provider_config_version=str(doc["provider_config_version"]),
        endpoint=str(doc["endpoint"]),
        source_role=SourceRole(str(doc["source_role"])),
        started_at=str(doc["started_at"]),
        finished_at=str(doc["finished_at"]),
        status=ProviderStatus(str(doc["status"])),
        required=bool(doc["required"]),
        attempt_required=bool(doc["attempt_required"]),
        coverage_group=doc.get("coverage_group"),
        coverage_quorum=doc.get("coverage_quorum"),
        priority_source=PrioritySource(str(doc["priority_source"])),
        user_preferred=bool(doc["user_preferred"]),
        from_cache=bool(doc["from_cache"]),
        cache_status=ProviderStatus(str(doc["cache_status"])) if doc.get("cache_status") else None,
        single_flight_role=str(doc["single_flight_role"]),
        shared_from_attempt_id=doc.get("shared_from_attempt_id"),
        latency_ms=int(doc["latency_ms"]),
        row_count=doc.get("row_count"),
        raw_ref=doc.get("raw_ref"),
        normalized_ref=doc.get("normalized_ref"),
        error_code=doc.get("error_code"),
        error_message=doc.get("error_message"),
        schema_id=str(doc["schema_id"]),
        license_note=str(doc["license_note"]),
    )


def _documents_equal_without_id(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_copy = dict(left)
    right_copy = dict(right)
    left_copy.pop("_id", None)
    right_copy.pop("_id", None)
    return left_copy == right_copy
