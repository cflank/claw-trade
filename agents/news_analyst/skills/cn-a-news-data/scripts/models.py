from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Literal

CN_A_NEWS_PACK_SCHEMA_VERSION = "cn_a_news_pack.v1"

QualityStatus = Literal["complete", "partial", "failed"]
EmptyReason = Literal[
    "no_result",
    "provider_error",
    "permission_missing",
    "rate_limited",
    "timeout",
    "schema_changed",
    "not_configured",
]
MatchType = Literal[
    "ticker_exact",
    "company_full_name",
    "approved_alias",
    "announcement_subject",
    "industry_keyword",
    "macro_keyword",
    "unknown",
]
Bucket = Literal[
    "company_news",
    "industry_news",
    "policy_macro_news",
    "announcements",
    "rejected",
]
MatchConfidence = Literal["high", "medium", "low"]


def _require_non_empty_str(field_name: str, value: str) -> None:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _to_stable_json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {
            dataclass_field.name: _to_stable_json_value(getattr(value, dataclass_field.name))
            for dataclass_field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _to_stable_json_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_to_stable_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_to_stable_json_value(item) for item in value]
    return value


def stable_json_dumps(value: Any) -> str:
    normalized = _to_stable_json_value(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_json_bytes(value: Any) -> bytes:
    return stable_json_dumps(value).encode("utf-8")


@dataclass(frozen=True)
class ToolRuntimeContext:
    run_id: str
    stage: str
    worker_id: str
    call_id: str
    tool_name: str
    evidence_root: str

    def __post_init__(self) -> None:
        _require_non_empty_str("run_id", self.run_id)
        _require_non_empty_str("stage", self.stage)
        _require_non_empty_str("worker_id", self.worker_id)
        _require_non_empty_str("call_id", self.call_id)
        _require_non_empty_str("tool_name", self.tool_name)
        _require_non_empty_str("evidence_root", self.evidence_root)


@dataclass(frozen=True)
class ToolInput:
    ticker: str
    market: str
    start_date: str | None = None
    end_date: str | None = None
    profile_artifact_ref: str | None = None
    fundamentals_artifact_ref: str | None = None
    market_artifact_ref: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("ticker", self.ticker)
        _require_non_empty_str("market", self.market)


@dataclass(frozen=True)
class NewsDataPackRequest:
    ticker: str
    exchange_ticker: str
    market: Literal["CN_A"]
    company_name: str | None
    industry: str | None
    start_date: str
    end_date: str
    approved_aliases: list[str] = field(default_factory=list)
    approved_historical_names: list[str] = field(default_factory=list)
    profile_missing_fields: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_non_empty_str("ticker", self.ticker)
        _require_non_empty_str("exchange_ticker", self.exchange_ticker)
        _require_non_empty_str("market", self.market)
        _require_non_empty_str("start_date", self.start_date)
        _require_non_empty_str("end_date", self.end_date)


@dataclass(frozen=True)
class ResolvedProfile:
    company_name: str | None
    industry: str | None
    approved_aliases: list[str]
    approved_historical_names: list[str]
    missing_fields: list[str]


@dataclass
class QueryPlan:
    ticker: str
    exchange_ticker: str
    company_name: str | None
    industry: str | None
    start_date: str
    end_date: str
    company_keywords: list[str]
    industry_keywords: list[str]
    macro_keywords: list[str]

    def __post_init__(self) -> None:
        _require_non_empty_str("ticker", self.ticker)
        _require_non_empty_str("exchange_ticker", self.exchange_ticker)
        _require_non_empty_str("start_date", self.start_date)
        _require_non_empty_str("end_date", self.end_date)


@dataclass
class ProviderAttempt:
    provider: str
    endpoint: str
    query: str
    ok: bool
    elapsed_ms: int
    raw_count: int
    accepted_count: int
    empty_reason: EmptyReason | None
    error: str | None
    cancelled: bool

    def __post_init__(self) -> None:
        _require_non_empty_str("provider", self.provider)
        _require_non_empty_str("endpoint", self.endpoint)
        _require_non_empty_str("query", self.query)
        _require_non_negative_int("elapsed_ms", self.elapsed_ms)
        _require_non_negative_int("raw_count", self.raw_count)
        _require_non_negative_int("accepted_count", self.accepted_count)
        if (not self.ok or self.raw_count == 0) and self.empty_reason is None:
            raise ValueError("empty_reason is required when provider returns empty or failed result")


@dataclass
class Quality:
    status: QualityStatus
    company_direct_news_count: int
    industry_background_count: int
    policy_macro_count: int
    total_raw_count: int
    after_dedup_count: int
    accepted_count: int
    missing_fields: list[str]
    directional_judgment_allowed: bool
    warnings: list[str]

    def __post_init__(self) -> None:
        _require_non_negative_int("company_direct_news_count", self.company_direct_news_count)
        _require_non_negative_int("industry_background_count", self.industry_background_count)
        _require_non_negative_int("policy_macro_count", self.policy_macro_count)
        _require_non_negative_int("total_raw_count", self.total_raw_count)
        _require_non_negative_int("after_dedup_count", self.after_dedup_count)
        _require_non_negative_int("accepted_count", self.accepted_count)


@dataclass(frozen=True)
class ProviderQuery:
    endpoint: str
    ticker: str
    exchange_ticker: str
    company_name: str | None
    keywords: list[str]
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        _require_non_empty_str("endpoint", self.endpoint)
        _require_non_empty_str("ticker", self.ticker)
        _require_non_empty_str("exchange_ticker", self.exchange_ticker)
        _require_non_empty_str("start_date", self.start_date)
        _require_non_empty_str("end_date", self.end_date)


@dataclass
class RawNewsItem:
    raw_id: str
    title: str
    summary: str | None
    source: str | None
    publish_time: str | None
    url: str | None
    data_source: str
    raw_payload_ref: str | None
    source_fetch_time: str

    def __post_init__(self) -> None:
        _require_non_empty_str("raw_id", self.raw_id)
        _require_non_empty_str("title", self.title)
        _require_non_empty_str("data_source", self.data_source)
        _require_non_empty_str("source_fetch_time", self.source_fetch_time)


@dataclass
class ProviderFetchResult:
    ok: bool
    attempt: ProviderAttempt
    raw_items: list[RawNewsItem]


@dataclass
class MatchResult:
    match_type: MatchType
    match_evidence_span: str | None
    match_confidence: MatchConfidence
    bucket: Bucket
    matched_keywords: list[str]

    def __post_init__(self) -> None:
        if self.match_type != "unknown":
            if self.match_evidence_span is None or self.match_evidence_span.strip() == "":
                raise ValueError("match_evidence_span must be non-empty when match_type is not unknown")


@dataclass
class KeywordObservations:
    matched_terms: list[str]
    keyword_categories: list[str]
    method: str
    is_sentiment_judgment: bool

    def __post_init__(self) -> None:
        _require_non_empty_str("method", self.method)


@dataclass
class NewsItem:
    news_id: str
    title: str
    summary: str | None
    source: str | None
    publish_time: str | None
    url: str | None
    data_source: str
    matched_keywords: list[str]
    match_type: MatchType
    match_evidence_span: str
    match_confidence: MatchConfidence
    bucket: Bucket
    keyword_observations: KeywordObservations
    source_fetch_time: str
    content_hash: str
    is_primary_source: bool
    merged_from: list[str]
    evidence_gap: str | None

    def __post_init__(self) -> None:
        _require_non_empty_str("news_id", self.news_id)
        _require_non_empty_str("title", self.title)
        _require_non_empty_str("data_source", self.data_source)
        _require_non_empty_str("match_evidence_span", self.match_evidence_span)
        _require_non_empty_str("source_fetch_time", self.source_fetch_time)
        _require_non_empty_str("content_hash", self.content_hash)


@dataclass
class DedupStats:
    removed_count: int
    merged_raw_ids: list[str]
    merge_reasons: list[str]

    def __post_init__(self) -> None:
        _require_non_negative_int("removed_count", self.removed_count)


@dataclass(frozen=True)
class KeywordRule:
    term: str
    category: str
    markets: list[str]

    def __post_init__(self) -> None:
        _require_non_empty_str("term", self.term)
        _require_non_empty_str("category", self.category)
        if "CN_A" not in self.markets:
            raise ValueError("markets must include CN_A")


@dataclass
class KeywordObservationResult:
    matched_terms: list[str]
    keyword_categories: list[str]
    method: str
    is_sentiment_judgment: bool

    def __post_init__(self) -> None:
        _require_non_empty_str("method", self.method)


@dataclass(frozen=True)
class QualityInput:
    provider_attempts: list[ProviderAttempt]
    items: list[NewsItem]
    total_raw_count: int
    after_dedup_count: int
    rejected_count: int
    request: NewsDataPackRequest

    def __post_init__(self) -> None:
        _require_non_negative_int("total_raw_count", self.total_raw_count)
        _require_non_negative_int("after_dedup_count", self.after_dedup_count)
        _require_non_negative_int("rejected_count", self.rejected_count)


@dataclass(frozen=True)
class QualityDecision:
    ok: bool
    quality: Quality


@dataclass(frozen=True)
class BriefInput:
    quality: Quality
    provider_attempts: list[ProviderAttempt]
    items: list[NewsItem]
    query_plan: QueryPlan


@dataclass(frozen=True)
class BriefSection:
    title: str
    lines: list[str]

    def __post_init__(self) -> None:
        _require_non_empty_str("title", self.title)


@dataclass
class NewsDataPack:
    ok: bool
    profile: dict[str, str | None]
    query_plan: QueryPlan
    provider_attempts: list[ProviderAttempt]
    data: dict[str, list[NewsItem]]
    quality: Quality
    reader_brief: str
    evidence: dict[str, str] | None = None
    schema_version: str = field(init=False, default=CN_A_NEWS_PACK_SCHEMA_VERSION)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "schema_version":
            raise AttributeError("schema_version is fixed and cannot be reassigned")
        super().__setattr__(name, value)

    def __post_init__(self) -> None:
        if self.schema_version != CN_A_NEWS_PACK_SCHEMA_VERSION:
            raise ValueError("schema_version must be cn_a_news_pack.v1")

    def to_dict(self) -> dict[str, Any]:
        stable_value = _to_stable_json_value(self)
        if not isinstance(stable_value, dict):
            raise TypeError("NewsDataPack stable value must be a dict")
        return stable_value

    def to_stable_json(self) -> str:
        return stable_json_dumps(self)

    def to_stable_json_bytes(self) -> bytes:
        return stable_json_bytes(self)


@dataclass(frozen=True)
class EvidenceWriteRequest:
    context: ToolRuntimeContext
    pack: NewsDataPack
    provider_raw_refs: list[str]


@dataclass(frozen=True)
class EvidenceRefs:
    pack_path: str
    provider_attempts_path: str
    provider_raw_paths: list[str]
    content_hash: str

    def __post_init__(self) -> None:
        _require_non_empty_str("pack_path", self.pack_path)
        _require_non_empty_str("provider_attempts_path", self.provider_attempts_path)
        _require_non_empty_str("content_hash", self.content_hash)
