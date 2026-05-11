from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from typing import Any, Mapping

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
from pymongo.operations import IndexModel

from config import SOCIAL_SCHEMA_VERSION, SocialDataConfig
from observability import emit_json_log, record_metric

SOCIAL_PROVIDER_CACHE_COLLECTION = "social_provider_cache"

INDEX_CACHE_ID_UNIQUE = "uq_social_provider_cache_cache_id"
INDEX_PRIMARY_QUERY_UNIQUE = "uq_social_provider_cache_primary_query"
INDEX_UPDATED_AT = "idx_social_provider_cache_updated_at"
INDEX_PAYLOAD_HASH = "idx_social_provider_cache_payload_hash"

SOCIAL_CACHE_NOT_CONFIGURED = "SOCIAL_CACHE_NOT_CONFIGURED"
SOCIAL_CACHE_REQUIRED_MISSING = "SOCIAL_CACHE_REQUIRED_MISSING"
SOCIAL_CACHE_MISS = "SOCIAL_CACHE_MISS"
SOCIAL_CACHE_STALE = "SOCIAL_CACHE_STALE"
SOCIAL_CACHE_SCHEMA_INVALID = "SOCIAL_CACHE_SCHEMA_INVALID"
SOCIAL_CACHE_REF_MISSING = "SOCIAL_CACHE_REF_MISSING"
SOCIAL_CACHE_WRITE_FAILED = "SOCIAL_CACHE_WRITE_FAILED"
SOCIAL_CACHE_INSPECTION_BYPASSED = "SOCIAL_CACHE_INSPECTION_BYPASSED"
SOCIAL_CACHE_QUERY_FAILED = "SOCIAL_CACHE_QUERY_FAILED"

CacheStatus = str

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_CACHE_RECORD_FIELDS = (
    "cache_id",
    "market",
    "ticker",
    "provider",
    "endpoint",
    "query_fingerprint",
    "date_window",
    "as_of_date",
    "fetched_at",
    "schema_version",
    "payload_hash",
    "raw_payload_ref",
    "row_count",
    "fields",
    "ttl_seconds",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class CacheKey:
    market: str
    ticker: str
    provider: str
    endpoint: str
    query_fingerprint: str
    date_window: str
    schema_version: str

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "market": self.market,
                "ticker": self.ticker,
                "provider": self.provider,
                "endpoint": self.endpoint,
                "query_fingerprint": self.query_fingerprint,
                "date_window": self.date_window,
                "schema_version": self.schema_version,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def cache_id(self) -> str:
        digest = hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
        return f"sha256:{digest}"


@dataclass(frozen=True)
class CacheInspectionResult:
    status: CacheStatus
    cache_key: str
    record: dict[str, Any] | None
    reason: str | None
    payload_hash: str | None
    raw_payload_ref: str | None


@dataclass
class CacheInspectionCallState:
    query_error_streak: int = 0
    mongo_unavailable_for_call: bool = False


def build_social_provider_cache_json_schema() -> dict[str, Any]:
    return {
        "bsonType": "object",
        "required": [
            "cache_id",
            "market",
            "ticker",
            "provider",
            "endpoint",
            "query_fingerprint",
            "date_window",
            "as_of_date",
            "fetched_at",
            "schema_version",
            "payload_hash",
            "raw_payload_ref",
            "row_count",
            "fields",
            "ttl_seconds",
            "created_at",
            "updated_at",
        ],
        "properties": {
            "cache_id": {"bsonType": "string"},
            "market": {"enum": ["CN_A"]},
            "ticker": {"bsonType": "string"},
            "provider": {"bsonType": "string"},
            "endpoint": {"bsonType": "string"},
            "query_fingerprint": {"bsonType": "string"},
            "date_window": {"bsonType": "string"},
            "as_of_date": {"bsonType": "string"},
            "fetched_at": {"bsonType": "string"},
            "schema_version": {"enum": ["cn_a_social_pack.v1"]},
            "payload_hash": {"bsonType": "string"},
            "raw_payload_ref": {"bsonType": "string"},
            "row_count": {"bsonType": "int"},
            "fields": {"bsonType": "object"},
            "ttl_seconds": {"bsonType": "int"},
            "created_at": {"bsonType": "string"},
            "updated_at": {"bsonType": "string"},
        },
    }


def build_social_provider_cache_validator() -> dict[str, Any]:
    return {"$jsonSchema": build_social_provider_cache_json_schema()}


def build_social_provider_cache_indexes() -> tuple[IndexModel, IndexModel, IndexModel, IndexModel]:
    return (
        IndexModel([("cache_id", 1)], unique=True, name=INDEX_CACHE_ID_UNIQUE),
        IndexModel(
            [
                ("market", 1),
                ("ticker", 1),
                ("provider", 1),
                ("endpoint", 1),
                ("query_fingerprint", 1),
                ("date_window", 1),
                ("schema_version", 1),
            ],
            unique=True,
            name=INDEX_PRIMARY_QUERY_UNIQUE,
        ),
        IndexModel([("updated_at", 1)], name=INDEX_UPDATED_AT),
        IndexModel([("payload_hash", 1)], name=INDEX_PAYLOAD_HASH),
    )


def ensure_social_provider_cache_collection(
    client: MongoClient[Any],
    database_name: str,
    collection_name: str = SOCIAL_PROVIDER_CACHE_COLLECTION,
) -> Collection[Any]:
    database = client.get_database(database_name)
    validator = build_social_provider_cache_validator()

    if collection_name not in database.list_collection_names(filter={"name": collection_name}):
        database.create_collection(
            collection_name,
            validator=validator,
            validationLevel="strict",
            validationAction="error",
        )
    else:
        database.command(
            {
                "collMod": collection_name,
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    collection = database.get_collection(collection_name)
    collection.create_indexes(list(build_social_provider_cache_indexes()))
    return collection


def inspect_provider_cache(
    key: CacheKey,
    now_iso: str,
    config: SocialDataConfig,
    call_state: CacheInspectionCallState | None = None,
) -> CacheInspectionResult:
    record_metric("social.cache.inspect.count", tags={"endpoint": key.endpoint})
    cache_key_id = key.cache_id()

    if call_state is not None and call_state.mongo_unavailable_for_call:
        return CacheInspectionResult(
            status="not_configured",
            cache_key=cache_key_id,
            record=None,
            reason=SOCIAL_CACHE_INSPECTION_BYPASSED,
            payload_hash=None,
            raw_payload_ref=None,
        )

    if config.cache_required and config.mongodb_uri is None:
        return CacheInspectionResult(
            status="not_configured",
            cache_key=cache_key_id,
            record=None,
            reason=SOCIAL_CACHE_REQUIRED_MISSING,
            payload_hash=None,
            raw_payload_ref=None,
        )

    if config.mongodb_uri is None:
        return CacheInspectionResult(
            status="not_configured",
            cache_key=cache_key_id,
            record=None,
            reason=SOCIAL_CACHE_NOT_CONFIGURED,
            payload_hash=None,
            raw_payload_ref=None,
        )

    query = {
        "market": key.market,
        "ticker": key.ticker,
        "provider": key.provider,
        "endpoint": key.endpoint,
        "query_fingerprint": key.query_fingerprint,
        "date_window": key.date_window,
        "schema_version": key.schema_version,
    }
    try:
        record = _find_cache_record(
            mongodb_uri=config.mongodb_uri,
            database_name=config.mongodb_database,
            collection_name=config.mongodb_cache_collection,
            query=query,
        )
    except Exception as exc:
        if call_state is not None:
            call_state.query_error_streak += 1
            if call_state.query_error_streak >= 3:
                call_state.mongo_unavailable_for_call = True
        emit_json_log(
            level="ERROR",
            event="social.cache.inspect.error",
            code=SOCIAL_CACHE_QUERY_FAILED,
            endpoint=key.endpoint,
            fields={"reason": _redact_error_message(exc), "cache_key": cache_key_id},
        )
        return CacheInspectionResult(
            status="not_configured",
            cache_key=cache_key_id,
            record=None,
            reason=f"{SOCIAL_CACHE_QUERY_FAILED}:{_redact_error_message(exc)}",
            payload_hash=None,
            raw_payload_ref=None,
        )

    if call_state is not None:
        call_state.query_error_streak = 0

    if record is None:
        return CacheInspectionResult(
            status="miss",
            cache_key=cache_key_id,
            record=None,
            reason=SOCIAL_CACHE_MISS,
            payload_hash=None,
            raw_payload_ref=None,
        )

    missing_fields = [field for field in _REQUIRED_CACHE_RECORD_FIELDS if field not in record]
    if missing_fields:
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=f"{SOCIAL_CACHE_SCHEMA_INVALID}:missing_record_fields",
            payload_hash=None,
            raw_payload_ref=None,
        )

    if record.get("schema_version") != key.schema_version or key.schema_version != SOCIAL_SCHEMA_VERSION:
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=SOCIAL_CACHE_SCHEMA_INVALID,
            payload_hash=None,
            raw_payload_ref=None,
        )

    payload_hash = _non_empty_text(record.get("payload_hash"))
    raw_payload_ref = _non_empty_text(record.get("raw_payload_ref"))
    if payload_hash is None or raw_payload_ref is None:
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=SOCIAL_CACHE_REF_MISSING,
            payload_hash=None,
            raw_payload_ref=None,
        )
    if not _SHA256_PATTERN.match(payload_hash):
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=f"{SOCIAL_CACHE_SCHEMA_INVALID}:invalid_payload_hash",
            payload_hash=None,
            raw_payload_ref=None,
        )
    if not raw_payload_ref.startswith("viking://"):
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=SOCIAL_CACHE_SCHEMA_INVALID,
            payload_hash=None,
            raw_payload_ref=None,
        )

    fetched_at = _parse_datetime(record.get("fetched_at"))
    if fetched_at is None:
        return CacheInspectionResult(
            status="miss",
            cache_key=cache_key_id,
            record=None,
            reason=f"{SOCIAL_CACHE_MISS}:missing_fetched_at",
            payload_hash=None,
            raw_payload_ref=None,
        )

    ttl_seconds = record.get("ttl_seconds")
    if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=f"{SOCIAL_CACHE_SCHEMA_INVALID}:invalid_ttl_seconds",
            payload_hash=None,
            raw_payload_ref=None,
        )

    now = _parse_datetime(now_iso)
    if now is None:
        raise ValueError("now_iso 不是有效的 ISO 8601 时间")

    if now > fetched_at + timedelta(seconds=ttl_seconds):
        return CacheInspectionResult(
            status="stale",
            cache_key=cache_key_id,
            record=record,
            reason=SOCIAL_CACHE_STALE,
            payload_hash=None,
            raw_payload_ref=None,
        )

    fields = record.get("fields")
    if not isinstance(fields, Mapping) or _endpoint_required_fields_missing(key.endpoint, fields):
        return CacheInspectionResult(
            status="schema_invalid",
            cache_key=cache_key_id,
            record=record,
            reason=SOCIAL_CACHE_SCHEMA_INVALID,
            payload_hash=None,
            raw_payload_ref=None,
        )

    return CacheInspectionResult(
        status="hit",
        cache_key=cache_key_id,
        record=record,
        reason=None,
        payload_hash=payload_hash,
        raw_payload_ref=raw_payload_ref,
    )


def upsert_provider_cache(
    key: CacheKey,
    raw_result: Mapping[str, Any],
    raw_payload_ref: str,
    ttl_seconds: int,
    now_iso: str,
    config: SocialDataConfig,
) -> CacheInspectionResult:
    record_metric("social.cache.upsert.count", tags={"endpoint": key.endpoint})
    cache_key_id = key.cache_id()

    if raw_result.get("ok") is not True:
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="raw_result_not_success",
            payload_hash=None,
            raw_payload_ref=None,
        )
    if config.cache_required and config.mongodb_uri is None:
        return CacheInspectionResult(
            status="not_configured",
            cache_key=cache_key_id,
            record=None,
            reason=SOCIAL_CACHE_REQUIRED_MISSING,
            payload_hash=None,
            raw_payload_ref=None,
        )
    if config.mongodb_uri is None:
        return CacheInspectionResult(
            status="not_configured",
            cache_key=cache_key_id,
            record=None,
            reason=SOCIAL_CACHE_NOT_CONFIGURED,
            payload_hash=None,
            raw_payload_ref=None,
        )

    payload_hash = _non_empty_text(raw_result.get("payload_hash"))
    if payload_hash is None or not _SHA256_PATTERN.match(payload_hash):
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="missing_evidence_ref",
            payload_hash=None,
            raw_payload_ref=None,
        )

    evidence_ref = _non_empty_text(raw_payload_ref)
    if evidence_ref is None or not evidence_ref.startswith("viking://"):
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="missing_evidence_ref",
            payload_hash=None,
            raw_payload_ref=None,
        )

    row_count = raw_result.get("row_count")
    if not isinstance(row_count, int) or row_count < 0:
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="invalid_row_count",
            payload_hash=None,
            raw_payload_ref=None,
        )

    fields = raw_result.get("fields")
    if not isinstance(fields, Mapping):
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="invalid_fields",
            payload_hash=None,
            raw_payload_ref=None,
        )

    if ttl_seconds <= 0:
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="invalid_ttl_seconds",
            payload_hash=None,
            raw_payload_ref=None,
        )

    now = _parse_datetime(now_iso)
    if now is None:
        raise ValueError("now_iso 不是有效的 ISO 8601 时间")

    fetched_at_text = _non_empty_text(raw_result.get("fetched_at")) or now.isoformat()
    fetched_at = _parse_datetime(fetched_at_text)
    if fetched_at is None:
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason="invalid_fetched_at",
            payload_hash=None,
            raw_payload_ref=None,
        )

    as_of_date = _non_empty_text(raw_result.get("as_of_date")) or fetched_at.date().isoformat()
    record: dict[str, Any] = {
        "cache_id": cache_key_id,
        "market": key.market,
        "ticker": key.ticker,
        "provider": key.provider,
        "endpoint": key.endpoint,
        "query_fingerprint": key.query_fingerprint,
        "date_window": key.date_window,
        "as_of_date": as_of_date,
        "fetched_at": fetched_at.isoformat(),
        "schema_version": key.schema_version,
        "payload_hash": payload_hash,
        "raw_payload_ref": evidence_ref,
        "row_count": row_count,
        "fields": dict(fields),
        "ttl_seconds": ttl_seconds,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        _upsert_cache_record(
            mongodb_uri=config.mongodb_uri,
            database_name=config.mongodb_database,
            collection_name=config.mongodb_cache_collection,
            record=record,
        )
    except Exception as exc:
        emit_json_log(
            level="ERROR",
            event="social.cache.upsert.error",
            code=SOCIAL_CACHE_WRITE_FAILED,
            endpoint=key.endpoint,
            fields={"reason": _redact_error_message(exc), "cache_key": cache_key_id},
        )
        return CacheInspectionResult(
            status="write_failed",
            cache_key=cache_key_id,
            record=None,
            reason=f"{SOCIAL_CACHE_WRITE_FAILED}:{_redact_error_message(exc)}",
            payload_hash=None,
            raw_payload_ref=None,
        )

    return CacheInspectionResult(
        status="miss",
        cache_key=cache_key_id,
        record=record,
        reason=None,
        payload_hash=payload_hash,
        raw_payload_ref=evidence_ref,
    )


def _find_cache_record(
    *,
    mongodb_uri: str,
    database_name: str,
    collection_name: str,
    query: dict[str, str],
) -> dict[str, Any] | None:
    client: MongoClient[Any] = MongoClient(mongodb_uri, serverSelectionTimeoutMS=3000)
    try:
        collection = client.get_database(database_name).get_collection(collection_name)
        document = collection.find_one(query)
        if document is None:
            return None
        if "_id" in document:
            document = dict(document)
            document.pop("_id", None)
        return document
    finally:
        client.close()


def _upsert_cache_record(
    *,
    mongodb_uri: str,
    database_name: str,
    collection_name: str,
    record: dict[str, Any],
) -> None:
    client: MongoClient[Any] = MongoClient(mongodb_uri, serverSelectionTimeoutMS=3000)
    try:
        collection = client.get_database(database_name).get_collection(collection_name)
        set_doc = dict(record)
        created_at = set_doc.pop("created_at")
        update_doc = {"$set": set_doc, "$setOnInsert": {"created_at": created_at}}

        try:
            collection.update_one({"cache_id": record["cache_id"]}, update_doc, upsert=True)
        except DuplicateKeyError:
            # 并发 upsert 场景下可能触发唯一索引冲突，退回主查询键更新即可收敛为单记录。
            collection.update_one(
                {
                    "market": record["market"],
                    "ticker": record["ticker"],
                    "provider": record["provider"],
                    "endpoint": record["endpoint"],
                    "query_fingerprint": record["query_fingerprint"],
                    "date_window": record["date_window"],
                    "schema_version": record["schema_version"],
                },
                update_doc,
                upsert=True,
            )
    finally:
        client.close()


def _endpoint_required_fields_missing(endpoint: str, fields: Mapping[str, Any]) -> bool:
    if endpoint == "stock_hot_rank_latest_em":
        return _missing_any(fields, ("symbol", "rank", "heat", "as_of_date"))
    if endpoint == "stock_hot_keyword_em":
        if _missing_any(fields, ("symbol", "top_keywords", "keyword_count", "as_of_date")):
            return True
        top_keywords = fields.get("top_keywords")
        return not isinstance(top_keywords, list) or len(top_keywords) == 0
    if endpoint == "stock_hot_rank_relate_em":
        if _missing_any(fields, ("symbol", "related_symbols", "related_count", "as_of_date")):
            return True
        related_symbols = fields.get("related_symbols")
        return not isinstance(related_symbols, list) or len(related_symbols) == 0
    if endpoint == "stock_hot_rank_em":
        if _missing_any(fields, ("target_present", "sample_size", "as_of_date")):
            return True
        sample_size = fields.get("sample_size")
        if not isinstance(sample_size, int) or sample_size <= 0:
            return True
        target_present = fields.get("target_present")
        if target_present is True and fields.get("target_rank") is None:
            return True
        return False
    if endpoint == "stock_hot_up_em":
        if _missing_any(fields, ("target_present", "target_rank_change", "sample_size", "as_of_date")):
            return True
        sample_size = fields.get("sample_size")
        if not isinstance(sample_size, int) or sample_size <= 0:
            return True
        target_present = fields.get("target_present")
        if target_present is True and fields.get("target_rank_change") is None:
            return True
        return False
    return True


def _missing_any(fields: Mapping[str, Any], required_keys: tuple[str, ...]) -> bool:
    for key in required_keys:
        if key not in fields or fields.get(key) is None:
            return True
    return False


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _redact_error_message(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    if text == "":
        return exc.__class__.__name__
    if len(text) > 200:
        text = text[:200]
    return f"{exc.__class__.__name__}:{text}"
