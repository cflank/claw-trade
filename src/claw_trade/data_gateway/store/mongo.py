from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Mapping

from pymongo.operations import IndexModel

OPENBB_PROVIDER_MANIFESTS = "openbb_provider_manifests"
OPENBB_PROVIDER_VALIDATION_RECEIPTS = "openbb_provider_validation_receipts"
OPENBB_PROVIDER_ATTEMPTS = "openbb_provider_attempts"
OPENBB_PROVIDER_HTTP_EVIDENCE = "openbb_provider_http_evidence"
OPENBB_RAW_PAYLOADS = "openbb_raw_payloads"
OPENBB_CACHE_ENTRIES = "openbb_cache_entries"
OPENBB_NORMALIZED = "openbb_normalized"
OPENBB_RATE_LIMITS = "openbb_rate_limits"
OPENBB_RUN_PROVIDER_PLANS = "openbb_run_provider_plans"
OPENBB_SINGLE_FLIGHT_CALLS = "openbb_single_flight_calls"
CRYPTO_LENS_ANALYSIS_EVIDENCE = "crypto_lens_analysis_evidence"


@dataclass(frozen=True)
class CollectionIndexes:
    collection: str
    models: tuple[IndexModel, ...]


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def iso_to_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def mongo_ref(collection: str, document_id: str) -> str:
    return f"mongo://{collection}/{document_id}"


def parse_mongo_ref(ref: str) -> tuple[str, str]:
    if not ref.startswith("mongo://"):
        raise ValueError(f"invalid mongo ref: {ref}")
    body = ref.removeprefix("mongo://")
    collection, sep, document_id = body.partition("/")
    if not sep or not collection or not document_id:
        raise ValueError(f"invalid mongo ref: {ref}")
    return collection, document_id


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_hash(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(rendered)


def openbb_collection_indexes() -> tuple[CollectionIndexes, ...]:
    return (
        CollectionIndexes(
            OPENBB_PROVIDER_MANIFESTS,
            (
                IndexModel([("adapter_id", 1), ("config_version", 1)], unique=True),
                IndexModel([("admission_status", 1), ("enabled", 1)]),
                IndexModel([("markets", 1), ("domains", 1), ("source_role", 1), ("coverage_group", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_PROVIDER_VALIDATION_RECEIPTS,
            (
                IndexModel([("provider_id", 1), ("config_version", 1), ("validated_at", -1)]),
                IndexModel([("adapter_id", 1), ("config_version", 1), ("validated_at", -1)]),
                IndexModel([("status", 1), ("validated_at", -1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_PROVIDER_ATTEMPTS,
            (
                IndexModel([("run_id", 1), ("call_id", 1), ("pack", 1)]),
                IndexModel([("provider", 1), ("endpoint", 1), ("status", 1), ("started_at", -1)]),
                IndexModel([("source_role", 1), ("status", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_PROVIDER_HTTP_EVIDENCE,
            (
                IndexModel([("run_id", 1), ("call_id", 1), ("pack", 1)]),
                IndexModel([("provider", 1), ("endpoint", 1), ("status", 1), ("requested_at", -1)]),
                IndexModel([("provider_request_id", 1)]),
                IndexModel([("raw_ref", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_RAW_PAYLOADS,
            (
                IndexModel([("payload_hash", 1)], unique=True),
                IndexModel([("run_id", 1), ("call_id", 1)]),
                IndexModel([("provider", 1), ("endpoint", 1), ("created_at", -1)]),
                IndexModel([("ttl_expires_at", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_CACHE_ENTRIES,
            (
                IndexModel([("provider", 1), ("endpoint", 1), ("params_hash", 1)]),
                IndexModel([("expires_at", 1)]),
                IndexModel([("market", 1), ("domain", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_NORMALIZED,
            (
                IndexModel([("normalized_hash", 1)], unique=True),
                IndexModel([("run_id", 1), ("call_id", 1)]),
                IndexModel([("provider", 1), ("endpoint", 1), ("schema_id", 1), ("created_at", -1)]),
                IndexModel([("market", 1), ("domain", 1), ("ticker", 1)]),
                IndexModel([("source_raw_ref", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_RATE_LIMITS,
            (
                IndexModel([("provider", 1), ("endpoint", 1), ("window_start", 1)], unique=True),
                IndexModel([("updated_at", -1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_RUN_PROVIDER_PLANS,
            (
                IndexModel([("run_id", 1)], unique=True),
                IndexModel([("provider_config_version", 1), ("market", 1), ("ticker", 1)]),
            ),
        ),
        CollectionIndexes(
            OPENBB_SINGLE_FLIGHT_CALLS,
            (
                IndexModel([("run_id", 1), ("call_key", 1)], unique=True),
                IndexModel([("status", 1), ("lease_expires_at", 1)]),
                IndexModel([("owner_attempt_id", 1)]),
            ),
        ),
        CollectionIndexes(
            CRYPTO_LENS_ANALYSIS_EVIDENCE,
            (
                IndexModel([("analysis_id", 1)], unique=True),
                IndexModel([("run_id", 1), ("call_id", 1)]),
                IndexModel([("worker_id", 1), ("engine_name", 1), ("engine_version", 1)]),
                IndexModel([("referenced_normalized_refs", 1)]),
            ),
        ),
    )


def ensure_openbb_store_indexes(database: Any) -> dict[str, list[str]]:
    created: dict[str, list[str]] = {}
    for entry in openbb_collection_indexes():
        created[entry.collection] = list(database[entry.collection].create_indexes(list(entry.models)))
    return created
