from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Literal, Mapping

from config import SOCIAL_SCHEMA_VERSION

SOCIAL_PACK_SCHEMA_INVALID = "SOCIAL_PACK_SCHEMA_INVALID"
SOCIAL_PROVIDER_ATTEMPTS_MISSING = "SOCIAL_PROVIDER_ATTEMPTS_MISSING"

QualityStatus = Literal["complete", "partial", "failed"]
CacheStatus = Literal["hit", "miss", "stale", "schema_invalid", "write_failed", "not_configured"]
ProviderStatus = Literal["success", "empty", "error", "timeout", "cancelled"]

REQUIRED_PACK_TOP_LEVEL_FIELDS = (
    "schema_version",
    "ok",
    "profile",
    "query_plan",
    "provider_attempts",
    "data",
    "quality",
    "reader_brief",
    "evidence",
)


class SocialPackSchemaError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class DateWindow:
    start_date: str
    end_date: str
    as_of_date: str


@dataclass(frozen=True)
class PublicSocialProfile:
    ticker: str
    company_name: str | None
    market: Literal["CN_A"]
    industry: str | None


@dataclass(frozen=True)
class QueryPlanView:
    start_date: str
    end_date: str
    keywords: tuple[str, ...]
    provider_endpoints: tuple[str, ...]


@dataclass
class ProviderAttempt:
    provider: str
    endpoint: str
    priority: Literal["P0", "P1"]
    query: str
    ok: bool
    status: ProviderStatus
    elapsed_ms: int
    raw_count: int
    accepted_count: int
    cache_status: CacheStatus
    cache_key: str | None
    payload_hash: str | None
    raw_payload_ref: str | None
    empty_reason: str | None
    error: dict[str, str] | None
    cancelled: bool


@dataclass
class ProviderExecutionResult:
    query: dict[str, Any]
    attempt: ProviderAttempt
    raw_rows: list[dict[str, Any]]
    raw_payload_ref: str | None
    payload_hash: str | None
    cache_inspection: dict[str, Any]


@dataclass
class SocialQuality:
    status: QualityStatus
    attention_signal_count: int
    topic_keyword_count: int
    related_symbol_count: int
    narrative_signal_count: int
    accepted_count: int
    missing_fields: list[str]
    social_judgment_allowed: bool
    warnings: list[dict[str, str]]


@dataclass(frozen=True)
class SocialEvidence:
    pack_path: str
    provider_attempts_path: str
    cache_inspection_path: str
    raw_payload_refs: list[str]
    content_hash: str


@dataclass
class SocialSentimentPack:
    ok: bool
    profile: PublicSocialProfile
    query_plan: QueryPlanView | dict[str, Any]
    provider_attempts: list[ProviderAttempt]
    data: dict[str, list[dict[str, Any]]]
    quality: SocialQuality
    reader_brief: str
    evidence: SocialEvidence
    schema_version: str = field(init=False, default=SOCIAL_SCHEMA_VERSION)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "schema_version":
            raise AttributeError("schema_version is fixed and cannot be reassigned")
        super().__setattr__(name, value)

    def __post_init__(self) -> None:
        if self.schema_version != SOCIAL_SCHEMA_VERSION:
            raise SocialPackSchemaError(
                SOCIAL_PACK_SCHEMA_INVALID,
                f"schema_version 必须为 {SOCIAL_SCHEMA_VERSION}",
            )
        if not self.provider_attempts:
            raise SocialPackSchemaError(
                SOCIAL_PROVIDER_ATTEMPTS_MISSING,
                "provider_attempts 不能为空",
            )

    def to_dict(self) -> dict[str, Any]:
        normalized = _to_canonical_json_value(self)
        if not isinstance(normalized, dict):
            raise TypeError("SocialSentimentPack canonical value must be dict")
        return normalized

    def to_canonical_json(self) -> str:
        return canonical_json_dumps(self)

    def to_canonical_json_bytes(self) -> bytes:
        return self.to_canonical_json().encode("utf-8")

    def canonical_hash(self) -> str:
        return canonical_json_sha256(self)


def canonical_json_dumps(value: Any) -> str:
    normalized = _to_canonical_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_sha256(value: Any) -> str:
    canonical = canonical_json_dumps(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate_social_pack_payload(payload: Mapping[str, Any]) -> None:
    missing = [field_name for field_name in REQUIRED_PACK_TOP_LEVEL_FIELDS if field_name not in payload]
    if missing:
        raise SocialPackSchemaError(
            SOCIAL_PACK_SCHEMA_INVALID,
            f"pack 缺少必需字段: {','.join(missing)}",
        )

    schema_version = payload.get("schema_version")
    if schema_version != SOCIAL_SCHEMA_VERSION:
        raise SocialPackSchemaError(
            SOCIAL_PACK_SCHEMA_INVALID,
            f"schema_version 必须为 {SOCIAL_SCHEMA_VERSION}",
        )

    provider_attempts = payload.get("provider_attempts")
    if not isinstance(provider_attempts, list) or len(provider_attempts) == 0:
        raise SocialPackSchemaError(
            SOCIAL_PROVIDER_ATTEMPTS_MISSING,
            "provider_attempts 不能为空",
        )


def _to_canonical_json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {
            dataclass_field.name: _to_canonical_json_value(getattr(value, dataclass_field.name))
            for dataclass_field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _to_canonical_json_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_to_canonical_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_to_canonical_json_value(item) for item in value]
    return value
