from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from claw_trade.data_gateway.warehouse.repository import DatasetRepository


@dataclass(frozen=True)
class RawPayloadRecord:
    raw_ref: str
    metadata_ref: str
    provider_id: str
    endpoint_id: str
    payload_hash: str
    object_uri: str | None
    storage_mode: str


class RawStore:
    def __init__(self, repository: DatasetRepository | None = None) -> None:
        self._repository = repository or DatasetRepository()

    def save(self, result: Any, batch: Any) -> tuple[str, ...]:
        if result.payload is None:
            return ()
        storage_mode = _resolve_raw_storage_mode(batch)
        payload_bytes = repr(result.payload).encode("utf-8")
        payload_hash = f"sha256:{sha256(payload_bytes).hexdigest()}"
        request_hash = _request_hash(batch)
        license_policy = _license_policy_dict(batch, storage_mode)
        market = _enum_value(getattr(batch, "market", "UNKNOWN"))
        raw_ref = f"raw:{market}:{batch.provider_id}:{payload_hash[7:19]}"
        metadata_ref = f"rawmeta:{market}:{batch.provider_id}:{payload_hash[7:19]}"
        object_uri = f"object://raw/{payload_hash}" if storage_mode == "store_full" else None
        record = RawPayloadRecord(
            raw_ref=raw_ref,
            metadata_ref=metadata_ref,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            payload_hash=payload_hash,
            object_uri=object_uri,
            storage_mode=storage_mode,
        )
        persisted_ref = self._repository.insert_raw_payload(
            {
                "raw_ref": record.raw_ref,
                "metadata_ref": record.metadata_ref,
                "payload_hash": record.payload_hash,
                "request_hash": request_hash,
                "provider": record.provider_id,
                "endpoint": record.endpoint_id,
                "market": market,
                "symbol_ids": tuple(getattr(batch, "symbol_ids", ())),
                "params_fingerprint": _params_fingerprint(batch),
                "object_uri": record.object_uri,
                "storage_mode": record.storage_mode,
                "content_type": getattr(result, "content_type", None),
                "payload_size_bytes": len(payload_bytes),
                "license_policy": license_policy,
                "redaction_meta": {
                    "payload_body_stored": storage_mode == "store_full",
                    "mongo_body_stored": False,
                },
                "created_at": getattr(result, "fetched_at", None),
            }
        )
        return (persisted_ref,)


def _resolve_raw_storage_mode(batch: Any) -> str:
    mode = getattr(batch, "raw_storage_mode", None)
    if mode is None:
        license_policy = getattr(batch, "license_policy", None)
        if isinstance(license_policy, dict):
            mode = license_policy.get("raw_storage_mode")
        elif license_policy is not None:
            mode = getattr(license_policy, "raw_storage_mode", None)
    resolved = str(mode or "store_full")
    if resolved not in {"store_full", "metadata_only", "no_store"}:
        raise ValueError(f"unsupported raw storage mode: {resolved}")
    return resolved


def _license_policy_dict(batch: Any, storage_mode: str) -> dict[str, Any]:
    license_policy = getattr(batch, "license_policy", None)
    if isinstance(license_policy, dict):
        policy = dict(license_policy)
    elif license_policy is not None:
        policy = {
            key: getattr(license_policy, key)
            for key in ("raw_storage_mode", "normalized_storage_allowed", "redistribution_allowed", "retention_days")
            if hasattr(license_policy, key)
        }
    else:
        policy = {}
    policy["raw_storage_mode"] = storage_mode
    return policy


def _request_hash(batch: Any) -> str:
    material = (
        getattr(batch, "provider_id", None),
        getattr(batch, "endpoint_id", None),
        getattr(batch, "market", None),
        tuple(getattr(batch, "symbol_ids", ()) or ()),
        getattr(batch, "date_range_start", None),
        getattr(batch, "date_range_end", None),
        tuple(getattr(batch, "fields_union", ()) or ()),
        getattr(batch, "provider_config_version", None),
    )
    return f"sha256:{sha256(repr(material).encode('utf-8')).hexdigest()}"


def _params_fingerprint(batch: Any) -> str:
    params = getattr(batch, "params_redacted", None)
    if params is None:
        params = getattr(batch, "params", {})
    return f"sha256:{sha256(repr(params).encode('utf-8')).hexdigest()}"


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)
