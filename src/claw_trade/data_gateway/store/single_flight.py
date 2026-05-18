from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import time
from typing import Callable
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    FreshnessStatus,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    SourceRole,
)

from .attempts import MongoAttemptStore
from .mongo import OPENBB_SINGLE_FLIGHT_CALLS, mongo_ref, sha256_text, utc_now_iso


class MongoSingleFlightCoordinator:
    def __init__(
        self,
        *,
        collection: object,
        attempt_store: MongoAttemptStore,
        lease_seconds: int = 30,
        wait_timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self.collection = collection
        self.attempt_store = attempt_store
        self.lease_seconds = lease_seconds
        self.wait_timeout_seconds = wait_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    @property
    def collection_name(self) -> str:
        return OPENBB_SINGLE_FLIGHT_CALLS

    def run(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        fn: Callable[[], ProviderResult],
    ) -> ProviderResult:
        key = self._document_id(request.run_id, spec.call_key)
        lease_owner = f"{os.getpid()}:{uuid4().hex}"
        owner_acquired = self._try_acquire_owner(key=key, request=request, spec=spec, lease_owner=lease_owner)

        if owner_acquired:
            return self._run_as_owner(key=key, request=request, spec=spec, lease_owner=lease_owner, fn=fn)
        return self._wait_or_consume(key=key, request=request, spec=spec, lease_owner=lease_owner, fn=fn)

    def _run_as_owner(
        self,
        *,
        key: str,
        request: PackRequest,
        spec: ProviderCallSpec,
        lease_owner: str,
        fn: Callable[[], ProviderResult],
    ) -> ProviderResult:
        try:
            result = fn()
        except DataGatewayError as exc:
            self._mark_failed(
                key=key,
                error_code=exc.code.value,
                error_message=exc.root_cause,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(
                key=key,
                error_code=DataGatewayErrorCode.REMOTE_ERROR.value,
                error_message=str(exc),
            )
            raise

        if result.status == ProviderStatus.REMOTE_SUCCESS and result.attempt.status != ProviderStatus.REMOTE_SUCCESS:
            self._mark_failed(
                key=key,
                error_code=DataGatewayErrorCode.SINGLE_FLIGHT_OWNER_FAILED.value,
                error_message="owner result status/attempt status mismatch",
            )
            raise DataGatewayError(
                DataGatewayErrorCode.SINGLE_FLIGHT_OWNER_FAILED,
                "owner result status/attempt status mismatch",
            )

        if result.status == ProviderStatus.REMOTE_SUCCESS and (not result.raw_ref or not result.normalized_ref):
            self._mark_failed(
                key=key,
                error_code=DataGatewayErrorCode.EVIDENCE_WRITE_FAILED.value,
                error_message="owner remote_success missing evidence refs",
            )
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                "owner remote_success missing evidence refs",
            )

        if result.status == ProviderStatus.REMOTE_SUCCESS:
            self._mark_succeeded(
                key=key,
                owner_call_id=request.call_id,
                owner_attempt_id=result.attempt.attempt_id,
                raw_ref=result.raw_ref,
                normalized_ref=result.normalized_ref,
                cache_receipt_ref=_cache_receipt_ref(result),
            )
            return result

        self._mark_failed(
            key=key,
            error_code=result.error_code or result.status.value,
            error_message=result.error_message,
            owner_attempt_id=result.attempt.attempt_id,
        )
        return result

    def _wait_or_consume(
        self,
        *,
        key: str,
        request: PackRequest,
        spec: ProviderCallSpec,
        lease_owner: str,
        fn: Callable[[], ProviderResult],
    ) -> ProviderResult:
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            doc = self.collection.find_one({"_id": key})
            if doc is None:
                if self._try_acquire_owner(key=key, request=request, spec=spec, lease_owner=lease_owner):
                    return self._run_as_owner(key=key, request=request, spec=spec, lease_owner=lease_owner, fn=fn)
                time.sleep(self.poll_interval_seconds)
                continue

            status = str(doc.get("status") or "pending")
            if status == "succeeded":
                return self._consume_success(request=request, spec=spec, doc=doc)
            if status == "failed":
                return self._consume_failure(request=request, spec=spec, doc=doc)

            lease_expires_at = _parse_iso(doc.get("lease_expires_at"))
            now = datetime.now(tz=UTC)
            if lease_expires_at is not None and lease_expires_at <= now:
                takeover = self.collection.find_one_and_update(
                    {
                        "_id": key,
                        "status": "pending",
                        "lease_expires_at": {"$lte": now.replace(microsecond=0).isoformat()},
                    },
                    {
                        "$set": {
                            "lease_owner": lease_owner,
                            "owner_call_id": request.call_id,
                            "lease_expires_at": (now + timedelta(seconds=self.lease_seconds))
                            .replace(microsecond=0)
                            .isoformat(),
                            "updated_at": utc_now_iso(),
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                )
                if takeover is not None:
                    return self._run_as_owner(key=key, request=request, spec=spec, lease_owner=lease_owner, fn=fn)
            time.sleep(self.poll_interval_seconds)

        raise DataGatewayError(
            DataGatewayErrorCode.SINGLE_FLIGHT_TIMEOUT,
            f"single-flight timeout run_id={request.run_id} call_key={spec.call_key}",
        )

    def _consume_success(self, *, request: PackRequest, spec: ProviderCallSpec, doc: dict[str, object]) -> ProviderResult:
        owner_attempt_id = _optional_str(doc.get("owner_attempt_id"))
        raw_ref = _optional_str(doc.get("raw_ref"))
        normalized_ref = _optional_str(doc.get("normalized_ref"))
        if not owner_attempt_id or not raw_ref or not normalized_ref:
            raise DataGatewayError(
                DataGatewayErrorCode.SINGLE_FLIGHT_OWNER_FAILED,
                "single-flight owner success missing refs",
            )
        owner_attempt = self.attempt_store.get(owner_attempt_id)
        if owner_attempt is None:
            raise DataGatewayError(
                DataGatewayErrorCode.SINGLE_FLIGHT_OWNER_FAILED,
                f"owner attempt not found: {owner_attempt_id}",
            )
        consumer_attempt_id = f"{request.call_id}:{spec.adapter_id}:consumer"
        consumer_attempt = ProviderAttempt(
            attempt_id=consumer_attempt_id,
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=spec.provider_kind.value,
            provider_kind=ProviderKind(spec.provider_kind.value),
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=SourceRole(spec.source_role.value),
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            status=ProviderStatus.SHARED_RESULT,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=PrioritySource(spec.priority_source.value),
            user_preferred=spec.user_preferred,
            from_cache=owner_attempt.from_cache,
            cache_status=owner_attempt.cache_status,
            single_flight_role="consumer",
            shared_from_attempt_id=owner_attempt_id,
            latency_ms=0,
            row_count=owner_attempt.row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=None,
            error_message=None,
            schema_id=owner_attempt.schema_id,
            license_note=owner_attempt.license_note,
        )
        self.attempt_store.write(consumer_attempt)
        freshness = FreshnessStatus.FRESH_CACHE if owner_attempt.from_cache else FreshnessStatus.FRESH_REMOTE
        return ProviderResult(
            spec=spec,
            status=ProviderStatus.SHARED_RESULT,
            request_id=None,
            requested_at=utc_now_iso(),
            latency_ms=0,
            source_role=spec.source_role,
            freshness=freshness,
            license_note=owner_attempt.license_note,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            rows=(),
            row_count=owner_attempt.row_count or 0,
            cache_receipt=None,
            attempt=consumer_attempt,
        )

    def _consume_failure(self, *, request: PackRequest, spec: ProviderCallSpec, doc: dict[str, object]) -> ProviderResult:
        owner_attempt_id = _optional_str(doc.get("owner_attempt_id"))
        status = ProviderStatus.REMOTE_ERROR
        error_code = _optional_str(doc.get("error_code"))
        error_message = _optional_str(doc.get("error_message"))
        schema_id = spec.expected_schema_id
        if owner_attempt_id:
            owner_attempt = self.attempt_store.get(owner_attempt_id)
            if owner_attempt is not None and owner_attempt.status != ProviderStatus.REMOTE_SUCCESS:
                status = owner_attempt.status
                schema_id = owner_attempt.schema_id
        consumer_attempt = ProviderAttempt(
            attempt_id=f"{request.call_id}:{spec.adapter_id}:consumer-fail",
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=spec.provider_kind.value,
            provider_kind=ProviderKind(spec.provider_kind.value),
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=SourceRole(spec.source_role.value),
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            status=status,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=PrioritySource(spec.priority_source.value),
            user_preferred=spec.user_preferred,
            from_cache=False,
            cache_status=None,
            single_flight_role="consumer",
            shared_from_attempt_id=owner_attempt_id,
            latency_ms=0,
            row_count=None,
            raw_ref=None,
            normalized_ref=None,
            error_code=error_code,
            error_message=error_message,
            schema_id=schema_id,
            license_note="single-flight owner failed",
        )
        self.attempt_store.write(consumer_attempt)
        return ProviderResult(
            spec=spec,
            status=status,
            request_id=None,
            requested_at=utc_now_iso(),
            latency_ms=0,
            source_role=spec.source_role,
            freshness=FreshnessStatus.NOT_FETCHED,
            license_note=consumer_attempt.license_note,
            raw_ref=None,
            normalized_ref=None,
            rows=(),
            row_count=0,
            cache_receipt=None,
            attempt=consumer_attempt,
            error_code=error_code,
            error_message=error_message,
        )

    def _try_acquire_owner(self, *, key: str, request: PackRequest, spec: ProviderCallSpec, lease_owner: str) -> bool:
        now = datetime.now(tz=UTC).replace(microsecond=0)
        lease_expires = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        doc = {
            "_id": key,
            "run_id": request.run_id,
            "call_key": spec.call_key,
            "status": "pending",
            "owner_call_id": request.call_id,
            "owner_attempt_id": None,
            "lease_owner": lease_owner,
            "lease_expires_at": lease_expires,
            "raw_ref": None,
            "normalized_ref": None,
            "cache_receipt_ref": None,
            "result_hash": None,
            "error_code": None,
            "error_message": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        try:
            self.collection.insert_one(doc)
            return True
        except DuplicateKeyError:
            return False
        except PyMongoError as exc:
            raise DataGatewayError(DataGatewayErrorCode.EVIDENCE_WRITE_FAILED, f"single-flight lease write failed: {exc}") from exc

    def _mark_succeeded(
        self,
        *,
        key: str,
        owner_call_id: str,
        owner_attempt_id: str,
        raw_ref: str | None,
        normalized_ref: str | None,
        cache_receipt_ref: str | None,
    ) -> None:
        result_hash = sha256_text(f"{owner_attempt_id}|{raw_ref or ''}|{normalized_ref or ''}|{cache_receipt_ref or ''}")
        self.collection.update_one(
            {"_id": key},
            {
                "$set": {
                    "status": "succeeded",
                    "owner_call_id": owner_call_id,
                    "owner_attempt_id": owner_attempt_id,
                    "raw_ref": raw_ref,
                    "normalized_ref": normalized_ref,
                    "cache_receipt_ref": cache_receipt_ref,
                    "result_hash": result_hash,
                    "updated_at": utc_now_iso(),
                }
            },
        )

    def _mark_failed(
        self,
        *,
        key: str,
        error_code: str,
        error_message: str | None,
        owner_attempt_id: str | None = None,
    ) -> None:
        self.collection.update_one(
            {"_id": key},
            {
                "$set": {
                    "status": "failed",
                    "owner_attempt_id": owner_attempt_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "updated_at": utc_now_iso(),
                }
            },
        )

    @staticmethod
    def _document_id(run_id: str, call_key: str) -> str:
        return f"{run_id}:{call_key}"


def _parse_iso(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(UTC)
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cache_receipt_ref(result: ProviderResult) -> str | None:
    if result.cache_receipt is None:
        return None
    return mongo_ref("openbb_cache_entries", result.cache_receipt.cache_key)
