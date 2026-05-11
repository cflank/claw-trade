from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
import re
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlsplit

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.results import UpdateResult

from config import (
    FND_CONFIG_INVALID,
    FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED,
    FND_CONFIG_SECRET_MISSING,
    FundamentalConfigError,
    FundamentalDataConfig,
)
from models import NormalizedInput, ProviderAttempt, ProviderResult
from security import sanitize_error

from cache_schema import FUNDAMENTAL_CACHE_PROVIDERS, FUNDAMENTAL_CACHE_SCHEMA_VERSION

FND_CACHE_READ_ONLY = "FND_CACHE_READ_ONLY"
FND_CACHE_PROVIDER_UNAPPROVED = "FND_CACHE_PROVIDER_UNAPPROVED"
FND_CACHE_API_NAME_MISSING = "FND_CACHE_API_NAME_MISSING"
FND_CACHE_EVIDENCE_MISSING = "FND_CACHE_EVIDENCE_MISSING"
FND_CACHE_HASH_INVALID = "FND_CACHE_HASH_INVALID"
FND_CACHE_INPUT_INVALID = "FND_CACHE_INPUT_INVALID"
FND_CACHE_WRITE_RAW_PAYLOAD_MISSING = "raw_payload_missing"
FND_CACHE_WRITE_NO_MAPPED_FIELDS = "no_mapped_fields"
FND_CACHE_WRITE_READ_ONLY = "cache_read_only"

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECORD_REQUIRED_KEYS = (
    "provider",
    "api_name",
    "report_period",
    "announce_date",
    "as_of",
    "fetched_at",
    "schema_version",
    "payload_hash",
    "raw_payload_ref",
    "metric_definition_version",
    "fields",
)
_APPROVED_PROVIDERS = frozenset(FUNDAMENTAL_CACHE_PROVIDERS)


@dataclass(frozen=True)
class CacheInspectionResult:
    attempt: ProviderAttempt
    reusable_fields: dict[str, dict[str, Any]]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class CacheUpsertResult:
    status: str
    reason: str | None
    matched_count: int
    modified_count: int
    upserted_id: Any | None


def create_mongo_client(config: FundamentalDataConfig) -> MongoClient[Any]:
    uri = config.mongodb_uri
    if uri is None or uri.strip() == "":
        raise FundamentalConfigError(FND_CONFIG_SECRET_MISSING, "CN_A_MONGODB_URI 缺失")
    _assert_mongo_uri_security(uri)
    return MongoClient(
        uri,
        maxPoolSize=10,
        minPoolSize=1,
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
    )


def inspect_fundamental_cache(
    collection: Collection[Any],
    normalized: NormalizedInput,
    *,
    now: datetime | None = None,
    approved_api_names: Iterable[str] | None = None,
) -> CacheInspectionResult:
    started = monotonic()
    started_at = _now_iso()
    now_dt = now or datetime.now(UTC)
    approved_api_name_set = set(approved_api_names or ())
    query: dict[str, Any] = {
        "ticker": normalized.canonical_code,
        "market": normalized.market,
    }
    if approved_api_name_set:
        query["api_name"] = {"$in": sorted(approved_api_name_set)}

    cursor = collection.find(query).sort(
        [("report_period", -1), ("as_of", -1), ("announce_date", -1), ("fetched_at", -1)]
    )
    documents = list(cursor)
    diagnostics: list[str] = []
    grouped_candidates: dict[str, list[dict[str, Any]]] = {}

    schema_invalid_seen = False
    stale_seen = False
    untrusted_seen = False

    for record in documents:
        missing = [key for key in _RECORD_REQUIRED_KEYS if key not in record]
        if missing:
            schema_invalid_seen = True
            diagnostics.append("cache.schema_invalid:missing_required_keys")
            continue
        if record.get("schema_version") != FUNDAMENTAL_CACHE_SCHEMA_VERSION:
            schema_invalid_seen = True
            diagnostics.append("cache.schema_invalid:schema_version_mismatch")
            continue

        provider = _as_non_empty_text(record.get("provider"))
        api_name = _as_non_empty_text(record.get("api_name"))
        if provider is None or api_name is None:
            schema_invalid_seen = True
            diagnostics.append("cache.schema_invalid:missing_provider_or_api")
            continue
        if provider not in _APPROVED_PROVIDERS:
            untrusted_seen = True
            diagnostics.append(f"cache.untrusted:{provider}:{api_name}:provider_not_approved")
            continue
        if approved_api_name_set and api_name not in approved_api_name_set:
            untrusted_seen = True
            diagnostics.append(f"cache.untrusted:{provider}:{api_name}:api_not_approved")
            continue

        payload_hash = _as_non_empty_text(record.get("payload_hash"))
        raw_payload_ref = _as_non_empty_text(record.get("raw_payload_ref"))
        if payload_hash is None or raw_payload_ref is None:
            untrusted_seen = True
            diagnostics.append(f"cache.untrusted:{provider}:{api_name}:evidence_missing")
            continue
        if _SHA256_PATTERN.match(payload_hash) is None:
            untrusted_seen = True
            diagnostics.append(f"cache.untrusted:{provider}:{api_name}:payload_hash_invalid")
            continue

        if _is_expired(record.get("expires_at"), now_dt):
            stale_seen = True
            diagnostics.append(f"cache.stale:{provider}:{api_name}")
            continue

        fields = record.get("fields")
        if not isinstance(fields, Mapping):
            schema_invalid_seen = True
            diagnostics.append(f"cache.schema_invalid:{provider}:{api_name}:fields_invalid")
            continue
        report_period = _as_cache_context_text(record.get("report_period"))
        announce_date = _as_cache_context_text(record.get("announce_date"))
        as_of = _as_cache_context_text(record.get("as_of"))
        fetched_at = _as_cache_context_text(record.get("fetched_at"))

        record_untrusted = False
        local_candidates: list[tuple[str, dict[str, Any]]] = []
        for field_key, payload in fields.items():
            if not isinstance(payload, Mapping):
                record_untrusted = True
                diagnostics.append(f"cache.untrusted:{provider}:{api_name}:field_payload_invalid")
                break
            field_path = _as_non_empty_text(payload.get("field_path"))
            if field_path is None:
                record_untrusted = True
                diagnostics.append(f"cache.untrusted:{provider}:{api_name}:field_path_missing")
                break
            if field_path != field_key:
                record_untrusted = True
                diagnostics.append(f"cache.untrusted:{provider}:{api_name}:field_path_mismatch")
                break
            if _is_numeric_value(payload.get("value")):
                unit = _as_non_empty_text(payload.get("unit"))
                scale = _as_non_empty_text(payload.get("scale"))
                if unit is None or scale is None:
                    record_untrusted = True
                    diagnostics.append(f"cache.untrusted:{provider}:{api_name}:unit_or_scale_missing")
                    break
            source_ref_path = _as_non_empty_text(payload.get("source_ref_path")) or f"field_sources.{field_path}"
            enriched_payload = dict(payload)
            enriched_payload["provider"] = provider
            enriched_payload["api_name"] = api_name
            enriched_payload["payload_hash"] = payload_hash
            enriched_payload["raw_payload_ref"] = raw_payload_ref
            enriched_payload["report_period"] = report_period
            enriched_payload["announce_date"] = announce_date
            enriched_payload["as_of"] = as_of
            enriched_payload["fetched_at"] = fetched_at
            enriched_payload["source_ref_path"] = source_ref_path
            local_candidates.append((field_path, enriched_payload))

        if record_untrusted:
            untrusted_seen = True
            continue
        for field_path, payload in local_candidates:
            grouped_candidates.setdefault(field_path, []).append({"provider": provider, "payload": payload})

    reusable_fields: dict[str, dict[str, Any]] = {}
    for field_path, candidates in grouped_candidates.items():
        provider_set = {candidate["provider"] for candidate in candidates}
        if len(provider_set) > 1:
            diagnostics.append(f"cache.untrusted:cross_provider_conflict:{field_path}")
            continue
        reusable_fields[field_path] = candidates[0]["payload"]

    ended_at = _now_iso()
    duration_ms = int((monotonic() - started) * 1000)
    attempt_status = "miss"
    attempt_reason = "cache_miss"
    if reusable_fields:
        attempt_status = "success"
        attempt_reason = "cache_hit"
    elif stale_seen:
        attempt_status = "stale"
        attempt_reason = "cache_stale"
    elif schema_invalid_seen:
        attempt_status = "schema_invalid"
        attempt_reason = "cache_schema_invalid"
    elif untrusted_seen:
        attempt_status = "schema_invalid"
        attempt_reason = "cache_untrusted"

    attempt = ProviderAttempt(
        provider="mongodb",
        role="cache",
        api_name=None,
        attempt_seq=None,
        status=attempt_status,
        reason=attempt_reason,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        retry_count=0,
        request_params_redacted={"ticker": normalized.canonical_code, "market": normalized.market},
        response_row_count=len(documents),
        response_col_count=len(documents[0]) if documents else 0,
        field_coverage=sorted(reusable_fields.keys()),
        report_period=None,
        announce_date=None,
        as_of=None,
        fetched_at=None,
        raw_payload_hash=None,
        raw_payload_ref=None,
        error_type=None,
        error_message_redacted=None,
    )
    return CacheInspectionResult(
        attempt=attempt,
        reusable_fields=reusable_fields,
        diagnostics=tuple(diagnostics),
    )


def upsert_fundamental_cache(
    collection: Collection[Any],
    normalized: NormalizedInput,
    provider_result: ProviderResult,
    *,
    metric_definition_version: str,
    cache_read_only: bool,
) -> CacheUpsertResult:
    if cache_read_only:
        return CacheUpsertResult(status="skipped", reason=FND_CACHE_READ_ONLY, matched_count=0, modified_count=0, upserted_id=None)

    attempt = provider_result.attempt
    provider = attempt.provider
    if provider not in _APPROVED_PROVIDERS:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_PROVIDER_UNAPPROVED,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    api_name = _as_non_empty_text(attempt.api_name)
    if api_name is None:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_API_NAME_MISSING,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    payload_hash = _as_non_empty_text(provider_result.raw_payload_hash or attempt.raw_payload_hash)
    raw_payload_ref = _as_non_empty_text(provider_result.raw_payload_ref or attempt.raw_payload_ref)
    if payload_hash is None or raw_payload_ref is None:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_EVIDENCE_MISSING,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    if _SHA256_PATTERN.match(payload_hash) is None:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_HASH_INVALID,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    if metric_definition_version.strip() == "":
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_INPUT_INVALID,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )

    now = datetime.now(UTC)
    fetched_at = _parse_datetime_or_none(attempt.fetched_at) or now
    fields = _build_cache_fields(provider_result.extracted_fields)
    expires_at = _resolve_expires_at(fields, fetched_at)

    filter_doc = {
        "provider": provider,
        "api_name": api_name,
        "ticker": normalized.canonical_code,
        "report_period": attempt.report_period,
        "announce_date": attempt.announce_date,
        "as_of": attempt.as_of,
        "payload_hash": payload_hash,
    }
    full_doc = {
        "ticker": normalized.canonical_code,
        "market": normalized.market,
        "provider": provider,
        "api_name": api_name,
        "report_period": attempt.report_period,
        "announce_date": attempt.announce_date,
        "as_of": attempt.as_of,
        "fetched_at": fetched_at,
        "schema_version": FUNDAMENTAL_CACHE_SCHEMA_VERSION,
        "payload_hash": payload_hash,
        "raw_payload_ref": raw_payload_ref,
        "metric_definition_version": metric_definition_version,
        "expires_at": expires_at,
        "fields": fields,
        "updated_at": now,
    }
    update_doc = {"$set": full_doc, "$setOnInsert": {"created_at": now}}

    try:
        result: UpdateResult = collection.update_one(filter_doc, update_doc, upsert=True)
    except Exception as exc:  # noqa: BLE001
        reason = sanitize_error(f"{exc.__class__.__name__}:{exc}")
        return CacheUpsertResult(status="error", reason=reason, matched_count=0, modified_count=0, upserted_id=None)

    return CacheUpsertResult(
        status="upserted",
        reason=None,
        matched_count=result.matched_count,
        modified_count=result.modified_count,
        upserted_id=result.upserted_id,
    )


def maybe_write_cache(
    collection: Collection[Any],
    normalized: NormalizedInput,
    provider_result: ProviderResult,
    *,
    metric_definition_version: str,
    cache_read_only: bool,
) -> CacheUpsertResult:
    payload_hash = _as_non_empty_text(provider_result.raw_payload_hash or provider_result.attempt.raw_payload_hash)
    raw_payload_ref = _as_non_empty_text(provider_result.raw_payload_ref or provider_result.attempt.raw_payload_ref)
    if payload_hash is None or raw_payload_ref is None:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_WRITE_RAW_PAYLOAD_MISSING,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    if len(provider_result.extracted_fields) == 0:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_WRITE_NO_MAPPED_FIELDS,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    if cache_read_only:
        return CacheUpsertResult(
            status="skipped",
            reason=FND_CACHE_WRITE_READ_ONLY,
            matched_count=0,
            modified_count=0,
            upserted_id=None,
        )
    return upsert_fundamental_cache(
        collection,
        normalized,
        provider_result,
        metric_definition_version=metric_definition_version,
        cache_read_only=False,
    )


def _assert_mongo_uri_security(uri: str) -> None:
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        raise FundamentalConfigError(FND_CONFIG_SECRET_MISSING, sanitize_error("CN_A_MONGODB_URI 非法")) from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise FundamentalConfigError(FND_CONFIG_INVALID, "CN_A_MONGODB_URI scheme 非法")
    query = parse_qs(parsed.query, keep_blank_values=True)
    auth_source_values = [value.strip() for value in query.get("authSource", []) if value.strip()]
    credentials_present = bool(parsed.username or parsed.password)
    if credentials_present and not auth_source_values:
        raise FundamentalConfigError(
            FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED,
            "CN_A_MONGODB_URI 带认证信息时必须显式配置 authSource",
        )


def _is_expired(expires_at: Any, now: datetime) -> bool:
    if expires_at is None:
        return False
    parsed = _parse_datetime_or_none(expires_at)
    if parsed is None:
        return False
    return parsed < now


def _is_numeric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _build_cache_fields(extracted_fields: list[tuple[str, Any, str]]) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for item in extracted_fields:
        if len(item) != 3:
            continue
        field_path, value, _source_ref = item
        if not isinstance(field_path, str):
            continue
        key = field_path.strip()
        if key == "":
            continue
        normalized_value = value
        unit: str | None = None
        scale: str | None = None
        if isinstance(value, Mapping):
            normalized_value = value.get("value")
            unit = _as_non_empty_text(value.get("unit"))
            scale = _as_non_empty_text(value.get("scale"))
        fields[key] = {
            "field_path": key,
            "value": normalized_value,
            "unit": unit,
            "scale": scale,
        }
    return fields


def _resolve_expires_at(fields: Mapping[str, dict[str, Any]], fetched_at: datetime) -> datetime | None:
    for field_path in fields.keys():
        if field_path.startswith("price_context.") or field_path.startswith("valuation."):
            return fetched_at + timedelta(days=7)
    return None


def _as_non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _as_cache_context_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    text = _as_non_empty_text(value)
    if text is not None:
        return text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = _as_non_empty_text(value)
    if text is None:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
