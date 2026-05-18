from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import PackRequest, ProviderCallSpec

from .mongo import OPENBB_RAW_PAYLOADS, mongo_ref, sha256_text, utc_now_iso

_RAW_EXPORT_POLICIES = frozenset({"metadata_only", "redacted", "full"})


def _to_bytes(payload: bytes | str | Mapping[str, Any]) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return rendered.encode("utf-8")


def _request_hash(request: PackRequest, spec: ProviderCallSpec) -> str:
    stable = json.dumps(
        {
            "run_id": request.run_id,
            "call_id": request.call_id,
            "worker_id": request.worker_id,
            "market": request.market.value,
            "domain": request.domain.value,
            "ticker": request.ticker,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "provider": spec.provider,
            "adapter_id": spec.adapter_id,
            "endpoint": spec.endpoint,
            "params": {k: spec.params[k] for k in sorted(spec.params.keys())},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256_text(stable)


@dataclass(frozen=True)
class RawPayloadWriteResult:
    raw_ref: str
    payload_hash: str
    payload_size_bytes: int


class MongoRawPayloadStore:
    def __init__(self, collection: Any, *, inline_max_bytes: int = 200_000) -> None:
        self.collection = collection
        self.inline_max_bytes = inline_max_bytes

    @property
    def collection_name(self) -> str:
        return OPENBB_RAW_PAYLOADS

    def write_raw(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        payload: bytes | str | Mapping[str, Any],
        content_type: str,
        source_url: str | None,
        raw_export_policy: str = "metadata_only",
    ) -> str:
        return self.write_raw_with_result(
            request=request,
            spec=spec,
            payload=payload,
            content_type=content_type,
            source_url=source_url,
            raw_export_policy=raw_export_policy,
        ).raw_ref

    def write_raw_with_result(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        payload: bytes | str | Mapping[str, Any],
        content_type: str,
        source_url: str | None,
        raw_export_policy: str = "metadata_only",
    ) -> RawPayloadWriteResult:
        payload_bytes = _to_bytes(payload)
        payload_hash = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
        request_hash = _request_hash(request, spec)
        payload_size_bytes = len(payload_bytes)
        if raw_export_policy not in _RAW_EXPORT_POLICIES:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"raw export policy not allowed: {raw_export_policy}",
            )

        created_at = utc_now_iso()
        storage_mode = "metadata_only"
        inline_payload: Any = None
        object_ref: str | None = None
        redacted_snapshot: str | None = None
        if raw_export_policy == "redacted":
            storage_mode = "redacted"
            redacted_snapshot = f"redacted payload hash={payload_hash} size={payload_size_bytes} content_type={content_type}"
        elif raw_export_policy == "full":
            if payload_size_bytes <= self.inline_max_bytes:
                storage_mode = "inline"
                inline_payload = _inline_payload(payload)
            else:
                storage_mode = "object_ref"
                object_ref = f"object://openbb/raw/{payload_hash}"
                redacted_snapshot = f"payload too large ({payload_size_bytes} bytes)"

        doc = {
            "_id": payload_hash,
            "run_id": request.run_id,
            "call_id": request.call_id,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "request_hash": request_hash,
            "payload_hash": payload_hash,
            "content_type": content_type,
            "payload_storage": storage_mode,
            "payload": inline_payload,
            "object_ref": object_ref,
            "redacted_snapshot": redacted_snapshot,
            "payload_size_bytes": payload_size_bytes,
            "contains_secret": False,
            "redaction_reason": None,
            "encryption_key_id": "dev-services-default",
            "ttl_expires_at": None,
            "created_at": created_at,
            "source_url": source_url,
            "license_policy_id": spec.license_policy_id,
            "raw_export_policy": raw_export_policy,
        }
        try:
            self.collection.update_one(
                {"_id": payload_hash},
                {
                    "$setOnInsert": doc,
                    "$set": {
                        "last_seen_run_id": request.run_id,
                        "last_seen_call_id": request.call_id,
                        "last_seen_at": created_at,
                    },
                },
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"raw payload write failed: {exc}",
            ) from exc
        return RawPayloadWriteResult(
            raw_ref=mongo_ref(self.collection_name, payload_hash),
            payload_hash=payload_hash,
            payload_size_bytes=payload_size_bytes,
        )


def _inline_payload(payload: bytes | str | Mapping[str, Any]) -> Any:
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    return payload
