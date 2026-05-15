from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Mapping, Literal

from pymongo.errors import PyMongoError

from .errors import (
    MONGO_CONFIG_INVALID,
    MONGO_SCHEMA_INVALID,
    MONGO_UNAVAILABLE,
    MONGO_WRITE_FAILED,
    FrontlineValidationError,
)
from .models import (
    CacheInspection,
    Domain,
    ProviderAttempt,
    ProviderCacheDocument,
    ProviderCacheKey,
    stable_json_dumps,
)
from .mongo_store import COLLECTION_PROVIDER_ATTEMPTS, COLLECTION_PROVIDER_CACHE
from .security import summarize_provider_error


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_VIKING_URI_PREFIX = "viking://"


def inspect_provider_cache(
    key: ProviderCacheKey,
    *,
    now_utc: str,
    collection: Any | None,
    cache_required: bool = False,
) -> CacheInspection:
    now = _parse_utc_iso8601(now_utc, field_name="now_utc")
    if collection is None:
        return _cache_error(key, code=MONGO_CONFIG_INVALID, cache_required=cache_required)

    query = _cache_key_query(key)
    try:
        doc = collection.find_one(query)
    except PyMongoError as exc:
        return _cache_error(key, code=MONGO_UNAVAILABLE, cache_required=cache_required, error=exc)
    except Exception as exc:
        return _cache_error(key, code=MONGO_UNAVAILABLE, cache_required=cache_required, error=exc)

    if doc is None:
        return CacheInspection(
            key=key,
            status="cache_miss",
            cache_ref=None,
            freshness_age_seconds=None,
            reason=None,
        )
    if not isinstance(doc, Mapping):
        return CacheInspection(
            key=key,
            status="schema_invalid",
            cache_ref=None,
            freshness_age_seconds=None,
            reason=f"{MONGO_SCHEMA_INVALID}:document_not_mapping",
        )

    cache_ref = _build_cache_ref(doc.get("_id"))
    if _cache_doc_schema_invalid(doc, key):
        return CacheInspection(
            key=key,
            status="schema_invalid",
            cache_ref=cache_ref,
            freshness_age_seconds=None,
            reason=f"{MONGO_SCHEMA_INVALID}:schema_or_evidence_invalid",
        )

    try:
        expires_at = _parse_utc_iso8601(doc["expires_at"], field_name="expires_at")
    except FrontlineValidationError:
        return CacheInspection(
            key=key,
            status="schema_invalid",
            cache_ref=cache_ref,
            freshness_age_seconds=None,
            reason=f"{MONGO_SCHEMA_INVALID}:expires_at_invalid",
        )
    if expires_at <= now:
        return CacheInspection(
            key=key,
            status="cache_stale",
            cache_ref=cache_ref,
            freshness_age_seconds=_freshness_age_seconds(doc, now),
            reason=None,
        )

    return CacheInspection(
        key=key,
        status="cache_hit",
        cache_ref=cache_ref,
        freshness_age_seconds=_freshness_age_seconds(doc, now),
        reason=None,
    )


def upsert_provider_cache(
    document: ProviderCacheDocument,
    *,
    collection: Any | None,
) -> str:
    if collection is None:
        raise FrontlineValidationError(MONGO_CONFIG_INVALID, "MongoDB collection 未配置")
    if document.key.market not in {"CN_A", "HK"}:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, "provider cache 只允许 CN_A 或 HK")

    expected_id = build_provider_cache_document_id(document.key)
    if document._id != expected_id:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, "provider cache _id 与 key 不匹配")
    if not _SHA256_RE.fullmatch(document.payload_hash):
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, "provider cache payload_hash 必须是 sha256")
    if not document.raw_payload_ref.startswith(_VIKING_URI_PREFIX):
        raise FrontlineValidationError(
            MONGO_SCHEMA_INVALID,
            "provider cache raw_payload_ref 必须是 viking:// URI",
        )

    try:
        collection.update_one(
            _cache_key_query(document.key),
            {
                "$set": _provider_cache_set_fields(document),
                "$setOnInsert": {"_id": document._id},
            },
            upsert=True,
        )
    except PyMongoError as exc:
        raise FrontlineValidationError(MONGO_WRITE_FAILED, summarize_provider_error(exc)) from exc
    except Exception as exc:
        raise FrontlineValidationError(MONGO_WRITE_FAILED, summarize_provider_error(exc)) from exc

    return f"{COLLECTION_PROVIDER_CACHE}:{document._id}"


def insert_provider_attempt(
    attempt: ProviderAttempt,
    *,
    run_id: str,
    call_id: str,
    ticker: str,
    domain: Domain,
    worker_id: str,
    collection: Any | None,
    stage: str = "frontline",
    market: Literal["CN_A", "HK"] = "CN_A",
) -> str:
    if collection is None:
        raise FrontlineValidationError(MONGO_CONFIG_INVALID, "MongoDB collection 未配置")
    if stage != "frontline":
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, "provider attempt.stage 必须为 frontline")
    if market not in {"CN_A", "HK"}:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, "provider attempt.market 必须为 CN_A 或 HK")

    _ensure_non_empty("run_id", run_id)
    _ensure_non_empty("call_id", call_id)
    _ensure_non_empty("ticker", ticker)
    _ensure_non_empty("worker_id", worker_id)

    attempt_id = build_provider_attempt_id(
        run_id=run_id,
        call_id=call_id,
        provider=attempt.provider,
        endpoint=attempt.endpoint,
        started_at=attempt.started_at,
    )
    document = {
        "_id": attempt_id,
        "run_id": run_id,
        "stage": stage,
        "worker_id": worker_id,
        "call_id": call_id,
        "market": market,
        "ticker": ticker,
        "domain": domain,
        "provider": attempt.provider,
        "endpoint": attempt.endpoint,
        "role": attempt.role,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "elapsed_ms": attempt.elapsed_ms,
        "timeout_ms": attempt.timeout_ms,
        "query_fingerprint": attempt.query_fingerprint,
        "raw_count": attempt.raw_count,
        "accepted_count": attempt.accepted_count,
        "payload_hash": attempt.payload_hash,
        "raw_payload_ref": attempt.raw_payload_ref,
        "error_code": attempt.error_code,
        "error_message_redacted": attempt.error_message_redacted,
    }
    try:
        collection.insert_one(document)
    except PyMongoError as exc:
        raise FrontlineValidationError(MONGO_WRITE_FAILED, summarize_provider_error(exc)) from exc
    except Exception as exc:
        raise FrontlineValidationError(MONGO_WRITE_FAILED, summarize_provider_error(exc)) from exc

    return f"{COLLECTION_PROVIDER_ATTEMPTS}:{attempt_id}"


def build_provider_cache_document_id(key: ProviderCacheKey) -> str:
    stable_json = stable_json_dumps(_cache_key_query(key))
    digest = hashlib.sha256(stable_json.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_provider_attempt_id(
    *,
    run_id: str,
    call_id: str,
    provider: str,
    endpoint: str,
    started_at: str,
) -> str:
    _ensure_non_empty("run_id", run_id)
    _ensure_non_empty("call_id", call_id)
    _ensure_non_empty("provider", provider)
    _ensure_non_empty("endpoint", endpoint)
    _ensure_non_empty("started_at", started_at)
    stable_text = "\n".join((run_id, call_id, provider, endpoint, started_at))
    digest = hashlib.sha256(stable_text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _cache_error(
    key: ProviderCacheKey,
    *,
    code: str,
    cache_required: bool,
    error: Exception | None = None,
) -> CacheInspection:
    reason = code
    if error is not None:
        reason = f"{code}:{summarize_provider_error(error)}"
    if cache_required:
        reason = f"{reason};cache_required=true"
    return CacheInspection(
        key=key,
        status="error",
        cache_ref=None,
        freshness_age_seconds=None,
        reason=reason,
    )


def _cache_doc_schema_invalid(doc: Mapping[str, Any], key: ProviderCacheKey) -> bool:
    required_key_values = _cache_key_query(key)
    for field_name, expected in required_key_values.items():
        if doc.get(field_name) != expected:
            return True

    payload_hash = doc.get("payload_hash")
    raw_payload_ref = doc.get("raw_payload_ref")
    if not isinstance(payload_hash, str) or _SHA256_RE.fullmatch(payload_hash) is None:
        return True
    if not isinstance(raw_payload_ref, str) or not raw_payload_ref.startswith(_VIKING_URI_PREFIX):
        return True

    raw_payload_size = doc.get("raw_payload_size_bytes")
    if not isinstance(raw_payload_size, int) or raw_payload_size <= 0:
        return True
    return False


def _provider_cache_set_fields(document: ProviderCacheDocument) -> dict[str, Any]:
    return {
        "market": document.key.market,
        "domain": document.key.domain,
        "ticker": document.key.ticker,
        "provider": document.key.provider,
        "endpoint": document.key.endpoint,
        "query_fingerprint": document.key.query_fingerprint,
        "schema_version": document.key.schema_version,
        "fetched_at": document.fetched_at,
        "expires_at": document.expires_at,
        "payload_hash": document.payload_hash,
        "raw_payload_ref": document.raw_payload_ref,
        "raw_payload_size_bytes": document.raw_payload_size_bytes,
        "normalized_ref": document.normalized_ref,
        "raw_count": document.raw_count,
        "accepted_count": document.accepted_count,
        "schema_validated_at": document.schema_validated_at,
    }


def _cache_key_query(key: ProviderCacheKey) -> dict[str, str]:
    return {
        "market": key.market,
        "domain": key.domain,
        "ticker": key.ticker,
        "provider": key.provider,
        "endpoint": key.endpoint,
        "query_fingerprint": key.query_fingerprint,
        "schema_version": key.schema_version,
    }


def _build_cache_ref(cache_id: Any) -> str | None:
    if isinstance(cache_id, str) and cache_id.strip():
        return f"{COLLECTION_PROVIDER_CACHE}:{cache_id.strip()}"
    return None


def _freshness_age_seconds(doc: Mapping[str, Any], now: datetime) -> int | None:
    fetched_at = doc.get("fetched_at")
    if not isinstance(fetched_at, str):
        return None
    try:
        fetched = _parse_utc_iso8601(fetched_at, field_name="fetched_at")
    except FrontlineValidationError:
        return None
    age = int((now - fetched).total_seconds())
    if age < 0:
        return 0
    return age


def _parse_utc_iso8601(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须是非空 ISO 时间字符串")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 不是合法 ISO 时间") from exc
    if parsed.tzinfo is None:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须包含时区")
    return parsed.astimezone(UTC)


def _ensure_non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须是非空字符串")


__all__ = [
    "build_provider_attempt_id",
    "build_provider_cache_document_id",
    "insert_provider_attempt",
    "inspect_provider_cache",
    "upsert_provider_cache",
]
