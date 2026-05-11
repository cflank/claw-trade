from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field, fields as dataclass_fields, is_dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping

from .errors import PACK_SCHEMA_INVALID, FrontlineValidationError


Domain = Literal["market", "fundamental", "news", "social"]
QualityStatus = Literal["complete", "partial", "failed"]
FreshnessStatus = Literal["fresh", "stale", "unknown"]
AttemptStatus = Literal[
    "success",
    "empty",
    "timeout",
    "error",
    "cache_hit",
    "cache_miss",
    "cache_stale",
    "schema_invalid",
    "config_blocked",
]

ProviderPriority = Literal["P0", "P1", "P2"]
ProviderMode = Literal["cache", "remote"]
ProviderParamSource = Literal[
    "ticker_code_6",
    "ticker_exchange_suffix",
    "ticker_secid",
    "eastmoney_symbol",
    "start_date_yyyymmdd",
    "end_date_yyyymmdd",
    "adjust_qfq",
    "market_cn_a",
    "company_name",
    "industry",
    "page_size",
    "locale_zh_cn",
]
NewsMatchType = Literal[
    "code",
    "company_name",
    "approved_alias",
    "announcement_subject",
    "industry_keyword",
]
SocialSourcePlatform = Literal[
    "eastmoney",
    "akshare",
    "bocha",
    "jina",
    "tavily",
    "alphaear_news_source_list",
    "xueqiu_guba",
]
FundamentalFieldName = Literal[
    "company_profile.industry",
    "company_profile.main_business",
    "valuation.pe_ttm",
    "valuation.pb",
    "valuation.total_mv",
    "price_context.close",
    "price_context.trade_date",
    "price_context.volume",
    "financial_indicators.roe",
    "financial_indicators.roa",
    "financial_indicators.gross_margin",
    "financial_indicators.netprofit_margin",
    "financial_indicators.debt_to_assets",
    "income_statement.revenue",
    "income_statement.net_profit",
    "income_statement.eps",
    "cash_flow.operating_cash_flow",
]
FundamentalUnit = Literal[
    "text",
    "raw",
    "ratio",
    "cny",
    "cny_per_share",
    "share",
    "%",
    "date",
]


PACK_SCHEMA_VERSION = "cn_a_frontline_pack.v1"
_DOMAIN_SCHEMA_VERSIONS = {
    "market": "cn_a_market_pack.v1",
    "news": "cn_a_news_pack.v1",
    "social": "cn_a_social_pack.v1",
    "fundamental": "cn_a_fundamental_pack.v1",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_URI_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        payload: dict[str, Any] = {}
        for field in dataclass_fields(value):
            payload[field.name] = to_jsonable(getattr(value, field.name))
        return payload
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    return value


def stable_json_dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def stable_json_bytes(value: Any) -> bytes:
    return stable_json_dumps(value).encode("utf-8")


@dataclass(frozen=True)
class PackInput:
    ticker: str
    market: Literal["CN_A"]
    company_name: str | None
    industry: str | None
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("input.ticker", self.ticker)
        _ensure_cn_a_market("input.market", self.market)
        _ensure_date("input.start_date", self.start_date)
        _ensure_date("input.end_date", self.end_date)
        if self.end_date < self.start_date:
            _schema_error("input.end_date 不能早于 input.start_date")
        _ensure_optional_str("input.company_name", self.company_name)
        _ensure_optional_str("input.industry", self.industry)


@dataclass(frozen=True)
class Quality:
    status: QualityStatus
    coverage_score: float
    freshness_status: FreshnessStatus
    warnings: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.coverage_score, (int, float)):
            _schema_error("quality.coverage_score 必须是数值")
        if self.coverage_score < 0.0 or self.coverage_score > 1.0:
            _schema_error("quality.coverage_score 必须在 [0.0, 1.0] 区间")
        if len(self.warnings) > 20:
            _schema_error("quality.warnings 最多 20 条")
        for index, warning in enumerate(self.warnings):
            _ensure_non_empty_str(f"quality.warnings[{index}]", warning)


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    endpoint: str
    role: str
    status: AttemptStatus
    started_at: str
    finished_at: str
    elapsed_ms: int
    timeout_ms: int
    query_fingerprint: str
    raw_count: int
    accepted_count: int
    payload_hash: str | None
    raw_payload_ref: str | None
    error_code: str | None
    error_message_redacted: str | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("provider_attempt.provider", self.provider)
        _ensure_non_empty_str("provider_attempt.endpoint", self.endpoint)
        _ensure_non_empty_str("provider_attempt.role", self.role)
        _ensure_non_empty_str("provider_attempt.started_at", self.started_at)
        _ensure_non_empty_str("provider_attempt.finished_at", self.finished_at)
        _ensure_sha256("provider_attempt.query_fingerprint", self.query_fingerprint)
        _ensure_int_min("provider_attempt.elapsed_ms", self.elapsed_ms, 0)
        _ensure_int_min("provider_attempt.timeout_ms", self.timeout_ms, 1)
        _ensure_int_min("provider_attempt.raw_count", self.raw_count, 0)
        _ensure_int_min("provider_attempt.accepted_count", self.accepted_count, 0)
        if self.accepted_count > self.raw_count:
            _schema_error("provider_attempt.accepted_count 不能大于 raw_count")
        _ensure_optional_sha256("provider_attempt.payload_hash", self.payload_hash)
        _ensure_optional_non_empty_str("provider_attempt.raw_payload_ref", self.raw_payload_ref)
        _ensure_optional_non_empty_str("provider_attempt.error_code", self.error_code)
        _ensure_optional_non_empty_str(
            "provider_attempt.error_message_redacted",
            self.error_message_redacted,
        )
        if self.error_message_redacted and len(self.error_message_redacted) > 500:
            _schema_error("provider_attempt.error_message_redacted 不能超过 500 字符")


@dataclass(frozen=True)
class FieldSource:
    field_path: str
    provider: str
    endpoint: str
    payload_hash: str
    raw_payload_ref: str
    observed_at: str
    source_time: str | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("field_source.field_path", self.field_path)
        _ensure_non_empty_str("field_source.provider", self.provider)
        _ensure_non_empty_str("field_source.endpoint", self.endpoint)
        _ensure_sha256("field_source.payload_hash", self.payload_hash)
        _ensure_non_empty_str("field_source.raw_payload_ref", self.raw_payload_ref)
        _ensure_non_empty_str("field_source.observed_at", self.observed_at)
        _ensure_optional_str("field_source.source_time", self.source_time)


@dataclass(frozen=True)
class EvidenceRef:
    uri: str
    sha256: str
    size_bytes: int
    kind: str
    readback_verified: bool

    def __post_init__(self) -> None:
        _ensure_non_empty_str("evidence_ref.uri", self.uri)
        _ensure_sha256("evidence_ref.sha256", self.sha256)
        _ensure_int_min("evidence_ref.size_bytes", self.size_bytes, 1)
        _ensure_non_empty_str("evidence_ref.kind", self.kind)


@dataclass(frozen=True)
class PackEnvelope:
    ok: bool
    schema_version: Literal["cn_a_frontline_pack.v1"]
    domain: Domain
    run_id: str
    stage: Literal["frontline"]
    worker_id: str
    call_id: str
    tool_name: str
    input: PackInput
    quality: Quality
    provider_attempts: list[ProviderAttempt]
    field_sources: dict[str, FieldSource]
    raw_payload_refs: list[EvidenceRef]
    mongo_cache_refs: list[str]
    openviking_l2_refs: list[EvidenceRef]
    diagnostic_flags: list[str]
    reader_brief: str
    domain_data: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != PACK_SCHEMA_VERSION:
            _schema_error(f"schema_version 必须为 {PACK_SCHEMA_VERSION}")
        if self.stage != "frontline":
            _schema_error("stage 必须为 frontline")
        _ensure_non_empty_str("pack.run_id", self.run_id)
        _ensure_non_empty_str("pack.worker_id", self.worker_id)
        _ensure_non_empty_str("pack.call_id", self.call_id)
        _ensure_non_empty_str("pack.tool_name", self.tool_name)
        if not self.provider_attempts:
            _schema_error("provider_attempts 至少需要一条记录")
        for key, source in self.field_sources.items():
            _ensure_non_empty_str("field_sources key", key)
            if not isinstance(source, FieldSource):
                _schema_error("field_sources 值必须是 FieldSource")
        for index, cache_ref in enumerate(self.mongo_cache_refs):
            _ensure_non_empty_str(f"mongo_cache_refs[{index}]", cache_ref)
        for index, diagnostic in enumerate(self.diagnostic_flags):
            _ensure_non_empty_str(f"diagnostic_flags[{index}]", diagnostic)
        _ensure_non_empty_str("reader_brief", self.reader_brief)
        if len(self.reader_brief) > 3000:
            _schema_error("reader_brief 不能超过 3000 字符")
        if _URI_RE.search(self.reader_brief):
            _schema_error("reader_brief 不允许包含 URI 主体")
        if self.domain_data.get("schema_version") != _DOMAIN_SCHEMA_VERSIONS[self.domain]:
            _schema_error("domain_data.schema_version 与 domain 不匹配")
        try:
            stable_json_dumps(self.domain_data)
        except (TypeError, ValueError) as exc:
            _schema_error(f"domain_data 不是合法 JSON: {exc}")

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_stable_json(self) -> str:
        return stable_json_dumps(self)

    def to_stable_json_bytes(self) -> bytes:
        return stable_json_bytes(self)


@dataclass(frozen=True)
class ProviderQueryParameter:
    name: str
    source: ProviderParamSource
    required: bool
    fixed_value: str | int | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("provider_query_parameter.name", self.name)
        if self.fixed_value is not None and not isinstance(self.fixed_value, (str, int)):
            _schema_error("provider_query_parameter.fixed_value 只允许 str/int/None")


@dataclass(frozen=True)
class ProviderSpec:
    domain: Domain
    priority: ProviderPriority
    provider: str
    endpoint: str
    role: str
    enabled: bool
    mode: ProviderMode
    timeout_ms: int
    required_for_complete: bool
    query_parameters: list[ProviderQueryParameter]

    def __post_init__(self) -> None:
        _ensure_non_empty_str("provider_spec.provider", self.provider)
        _ensure_non_empty_str("provider_spec.endpoint", self.endpoint)
        _ensure_non_empty_str("provider_spec.role", self.role)
        _ensure_int_range("provider_spec.timeout_ms", self.timeout_ms, 1000, 30000)


@dataclass(frozen=True)
class ProviderQuery:
    market: Literal["CN_A"]
    ticker: str
    company_name: str | None
    industry: str | None
    start_date: str
    end_date: str
    adjust: str | None
    query_fingerprint: str

    def __post_init__(self) -> None:
        _ensure_cn_a_market("provider_query.market", self.market)
        _ensure_non_empty_str("provider_query.ticker", self.ticker)
        _ensure_optional_str("provider_query.company_name", self.company_name)
        _ensure_optional_str("provider_query.industry", self.industry)
        _ensure_date("provider_query.start_date", self.start_date)
        _ensure_date("provider_query.end_date", self.end_date)
        if self.end_date < self.start_date:
            _schema_error("provider_query.end_date 不能早于 provider_query.start_date")
        _ensure_optional_non_empty_str("provider_query.adjust", self.adjust)
        _ensure_sha256("provider_query.query_fingerprint", self.query_fingerprint)


@dataclass(frozen=True)
class ProviderResult:
    spec: ProviderSpec
    attempt: ProviderAttempt
    raw_payload: Any | None
    normalized_rows: list[dict[str, Any]]
    field_sources: dict[str, FieldSource]

    def __post_init__(self) -> None:
        for key, value in self.field_sources.items():
            _ensure_non_empty_str("provider_result.field_sources key", key)
            if not isinstance(value, FieldSource):
                _schema_error("provider_result.field_sources 值必须是 FieldSource")


def replace_attempt_evidence(
    result: ProviderResult,
    *,
    payload_hash: str,
    raw_payload_ref: str,
) -> ProviderResult:
    _ensure_sha256("payload_hash", payload_hash)
    _ensure_non_empty_str("raw_payload_ref", raw_payload_ref)
    updated_attempt = replace(
        result.attempt,
        payload_hash=payload_hash,
        raw_payload_ref=raw_payload_ref,
    )
    return replace(result, attempt=updated_attempt)


@dataclass(frozen=True)
class ProviderCacheKey:
    market: Literal["CN_A"]
    domain: Domain
    ticker: str
    provider: str
    endpoint: str
    query_fingerprint: str
    schema_version: str

    def __post_init__(self) -> None:
        _ensure_cn_a_market("provider_cache_key.market", self.market)
        _ensure_non_empty_str("provider_cache_key.ticker", self.ticker)
        _ensure_non_empty_str("provider_cache_key.provider", self.provider)
        _ensure_non_empty_str("provider_cache_key.endpoint", self.endpoint)
        _ensure_sha256("provider_cache_key.query_fingerprint", self.query_fingerprint)
        _ensure_non_empty_str("provider_cache_key.schema_version", self.schema_version)


@dataclass(frozen=True)
class ProviderCacheDocument:
    _id: str
    key: ProviderCacheKey
    fetched_at: str
    expires_at: str
    payload_hash: str
    raw_payload_ref: str
    raw_payload_size_bytes: int
    normalized_ref: str | None
    raw_count: int
    accepted_count: int
    schema_validated_at: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("provider_cache_document._id", self._id)
        _ensure_non_empty_str("provider_cache_document.fetched_at", self.fetched_at)
        _ensure_non_empty_str("provider_cache_document.expires_at", self.expires_at)
        _ensure_sha256("provider_cache_document.payload_hash", self.payload_hash)
        _ensure_non_empty_str("provider_cache_document.raw_payload_ref", self.raw_payload_ref)
        _ensure_int_min(
            "provider_cache_document.raw_payload_size_bytes",
            self.raw_payload_size_bytes,
            1,
        )
        _ensure_optional_non_empty_str("provider_cache_document.normalized_ref", self.normalized_ref)
        _ensure_int_min("provider_cache_document.raw_count", self.raw_count, 0)
        _ensure_int_min("provider_cache_document.accepted_count", self.accepted_count, 0)
        _ensure_non_empty_str("provider_cache_document.schema_validated_at", self.schema_validated_at)


@dataclass(frozen=True)
class CacheInspection:
    key: ProviderCacheKey
    status: AttemptStatus
    cache_ref: str | None
    freshness_age_seconds: int | None
    reason: str | None

    def __post_init__(self) -> None:
        _ensure_optional_non_empty_str("cache_inspection.cache_ref", self.cache_ref)
        if self.freshness_age_seconds is not None:
            _ensure_int_min("cache_inspection.freshness_age_seconds", self.freshness_age_seconds, 0)
        _ensure_optional_non_empty_str("cache_inspection.reason", self.reason)


@dataclass(frozen=True)
class L2WriteTarget:
    run_id: str
    stage: Literal["frontline"]
    worker_id: str
    call_id: str
    relative_path: str
    content_type: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("l2_target.run_id", self.run_id)
        if self.stage != "frontline":
            _schema_error("l2_target.stage 必须为 frontline")
        _ensure_non_empty_str("l2_target.worker_id", self.worker_id)
        _ensure_non_empty_str("l2_target.call_id", self.call_id)
        _ensure_non_empty_str("l2_target.content_type", self.content_type)
        _validate_relative_path(self.relative_path)


@dataclass(frozen=True)
class L2WriteRequest:
    target: L2WriteTarget
    content_bytes: bytes
    metadata: dict[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.content_bytes, bytes) or len(self.content_bytes) == 0:
            _schema_error("l2_write_request.content_bytes 必须是非空 bytes")
        for key, value in self.metadata.items():
            _ensure_non_empty_str("l2_write_request.metadata key", key)
            _ensure_non_empty_str(f"l2_write_request.metadata[{key}]", value)


@dataclass(frozen=True)
class L2WriteReceipt:
    uri: str
    sha256: str
    size_bytes: int
    write_receipt_id: str | None
    readback_verified: bool
    stat_verified: bool
    written_at: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("l2_write_receipt.uri", self.uri)
        _ensure_sha256("l2_write_receipt.sha256", self.sha256)
        _ensure_int_min("l2_write_receipt.size_bytes", self.size_bytes, 1)
        _ensure_optional_non_empty_str("l2_write_receipt.write_receipt_id", self.write_receipt_id)
        _ensure_non_empty_str("l2_write_receipt.written_at", self.written_at)


@dataclass(frozen=True)
class MarketToolInput:
    ticker: str
    market: Literal["CN_A"]
    company_name: str | None
    start_date: str | None
    end_date: str | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("market_input.ticker", self.ticker)
        _ensure_cn_a_market("market_input.market", self.market)
        _ensure_optional_str("market_input.company_name", self.company_name)
        _ensure_optional_date("market_input.start_date", self.start_date)
        _ensure_optional_date("market_input.end_date", self.end_date)


@dataclass(frozen=True)
class MarketPriceRow:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None
    adjust: Literal["qfq"]
    source_ref: str

    def __post_init__(self) -> None:
        _ensure_date("market_price_row.trade_date", self.trade_date)
        _ensure_positive("market_price_row.open", self.open)
        _ensure_positive("market_price_row.high", self.high)
        _ensure_positive("market_price_row.low", self.low)
        _ensure_positive("market_price_row.close", self.close)
        _ensure_non_negative("market_price_row.volume", self.volume)
        if self.amount is not None:
            _ensure_non_negative("market_price_row.amount", self.amount)
        if self.high < max(self.open, self.close, self.low):
            _schema_error("market_price_row.high 必须不小于 open/close/low")
        _ensure_non_empty_str("market_price_row.source_ref", self.source_ref)


@dataclass(frozen=True)
class MarketDateRange:
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        _ensure_date("market_date_range.start_date", self.start_date)
        _ensure_date("market_date_range.end_date", self.end_date)
        if self.end_date < self.start_date:
            _schema_error("market_date_range.end_date 不能早于 start_date")


@dataclass(frozen=True)
class MarketPriceHistory:
    ticker: str
    adjust: Literal["qfq"]
    row_count: int
    date_range: MarketDateRange
    recent_rows: list[MarketPriceRow]
    source_refs: list[str]

    def __post_init__(self) -> None:
        _ensure_non_empty_str("market_price_history.ticker", self.ticker)
        _ensure_int_min("market_price_history.row_count", self.row_count, 0)
        if len(self.recent_rows) > 10:
            _schema_error("market_price_history.recent_rows 最多 10 条")
        for index, source_ref in enumerate(self.source_refs):
            _ensure_non_empty_str(f"market_price_history.source_refs[{index}]", source_ref)


@dataclass(frozen=True)
class MovingAverageIndicators:
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None


@dataclass(frozen=True)
class MacdIndicators:
    dif: float | None
    dea: float | None
    macd: float | None


@dataclass(frozen=True)
class RsiIndicators:
    rsi6: float | None
    rsi12: float | None
    rsi24: float | None


@dataclass(frozen=True)
class BollIndicators:
    mid: float | None
    upper: float | None
    lower: float | None


@dataclass(frozen=True)
class KdjIndicators:
    k: float | None
    d: float | None
    j: float | None


@dataclass(frozen=True)
class AtrIndicators:
    atr14: float | None


@dataclass(frozen=True)
class MarketIndicators:
    ma: MovingAverageIndicators
    macd: MacdIndicators
    rsi: RsiIndicators
    boll: BollIndicators
    kdj: KdjIndicators
    atr: AtrIndicators


@dataclass(frozen=True)
class ChartRef:
    kind: Literal["market_structure", "volume", "indicator"]
    path: str
    openviking_ref: str
    sha256: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("chart_ref.path", self.path)
        _ensure_non_empty_str("chart_ref.openviking_ref", self.openviking_ref)
        _ensure_sha256("chart_ref.sha256", self.sha256)


@dataclass(frozen=True)
class SupportResistanceLevel:
    kind: Literal["support", "resistance"]
    price: float
    basis: Literal["high_low_window", "moving_average", "bollinger_band"]
    window_days: int
    source_refs: list[str]

    def __post_init__(self) -> None:
        _ensure_positive("support_resistance.price", self.price)
        _ensure_int_min("support_resistance.window_days", self.window_days, 1)
        for index, source_ref in enumerate(self.source_refs):
            _ensure_non_empty_str(f"support_resistance.source_refs[{index}]", source_ref)


@dataclass(frozen=True)
class MarketSupportResistance:
    levels: list[SupportResistanceLevel]
    calculation_window_days: int
    diagnostics: list[str]

    def __post_init__(self) -> None:
        _ensure_int_min(
            "market_support_resistance.calculation_window_days",
            self.calculation_window_days,
            1,
        )
        for index, diagnostic in enumerate(self.diagnostics):
            _ensure_non_empty_str(f"market_support_resistance.diagnostics[{index}]", diagnostic)


@dataclass(frozen=True)
class MarketVolumeProfileBucket:
    price_low: float
    price_high: float
    volume: float
    amount: float | None
    trade_days: int

    def __post_init__(self) -> None:
        _ensure_positive("market_volume_profile_bucket.price_low", self.price_low)
        _ensure_positive("market_volume_profile_bucket.price_high", self.price_high)
        if self.price_high < self.price_low:
            _schema_error("market_volume_profile_bucket.price_high 不能小于 price_low")
        _ensure_non_negative("market_volume_profile_bucket.volume", self.volume)
        if self.amount is not None:
            _ensure_non_negative("market_volume_profile_bucket.amount", self.amount)
        _ensure_int_min("market_volume_profile_bucket.trade_days", self.trade_days, 1)


@dataclass(frozen=True)
class MarketVolumeProfile:
    buckets: list[MarketVolumeProfileBucket]
    dominant_price_low: float | None
    dominant_price_high: float | None
    source_refs: list[str]
    diagnostics: list[str]

    def __post_init__(self) -> None:
        if self.dominant_price_low is not None:
            _ensure_positive("market_volume_profile.dominant_price_low", self.dominant_price_low)
        if self.dominant_price_high is not None:
            _ensure_positive("market_volume_profile.dominant_price_high", self.dominant_price_high)
        if self.dominant_price_low is not None and self.dominant_price_high is not None:
            if self.dominant_price_high < self.dominant_price_low:
                _schema_error("market_volume_profile.dominant_price_high 不能小于 dominant_price_low")
        for index, source_ref in enumerate(self.source_refs):
            _ensure_non_empty_str(f"market_volume_profile.source_refs[{index}]", source_ref)
        for index, diagnostic in enumerate(self.diagnostics):
            _ensure_non_empty_str(f"market_volume_profile.diagnostics[{index}]", diagnostic)


@dataclass(frozen=True)
class MarketDomainData:
    schema_version: Literal["cn_a_market_pack.v1"]
    price_history: MarketPriceHistory
    technical_indicators: MarketIndicators
    chart_refs: list[ChartRef]
    support_resistance: MarketSupportResistance
    volume_profile: MarketVolumeProfile

    def __post_init__(self) -> None:
        if self.schema_version != "cn_a_market_pack.v1":
            _schema_error("market domain schema_version 必须为 cn_a_market_pack.v1")


@dataclass(frozen=True)
class NewsToolInput:
    ticker: str
    market: Literal["CN_A"]
    company_name: str | None
    industry: str | None
    start_date: str | None
    end_date: str | None
    aliases: list[str] | None
    approved_aliases: list[str] | None
    approved_artifact_refs: list[str] | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("news_input.ticker", self.ticker)
        _ensure_cn_a_market("news_input.market", self.market)
        _ensure_optional_str("news_input.company_name", self.company_name)
        _ensure_optional_str("news_input.industry", self.industry)
        _ensure_optional_date("news_input.start_date", self.start_date)
        _ensure_optional_date("news_input.end_date", self.end_date)
        if self.aliases is not None:
            for index, alias in enumerate(self.aliases):
                _ensure_non_empty_str(f"news_input.aliases[{index}]", alias)
        if self.approved_aliases is not None:
            for index, alias in enumerate(self.approved_aliases):
                _ensure_non_empty_str(f"news_input.approved_aliases[{index}]", alias)
        if self.approved_artifact_refs is not None:
            for index, ref in enumerate(self.approved_artifact_refs):
                _ensure_non_empty_str(f"news_input.approved_artifact_refs[{index}]", ref)


@dataclass(frozen=True)
class NewsTargetProfile:
    ticker: str
    company_name: str
    approved_aliases: list[str]
    industry: str | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("news_target_profile.ticker", self.ticker)
        _ensure_non_empty_str("news_target_profile.company_name", self.company_name)
        _ensure_optional_str("news_target_profile.industry", self.industry)
        for index, alias in enumerate(self.approved_aliases):
            _ensure_non_empty_str(f"news_target_profile.approved_aliases[{index}]", alias)


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    title: str
    summary: str | None
    source: str
    publish_time: str | None
    url: str | None
    bucket: Literal["company_news", "industry_macro", "announcement", "rejected"]
    match_type: NewsMatchType
    match_evidence_span: str
    provider: str
    endpoint: str
    raw_payload_ref: str
    payload_hash: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("news_item.news_id", self.news_id)
        _ensure_non_empty_str("news_item.title", self.title)
        _ensure_optional_str("news_item.summary", self.summary)
        _ensure_non_empty_str("news_item.source", self.source)
        _ensure_optional_str("news_item.publish_time", self.publish_time)
        _ensure_optional_non_empty_str("news_item.url", self.url)
        _ensure_non_empty_str("news_item.match_evidence_span", self.match_evidence_span)
        _ensure_non_empty_str("news_item.provider", self.provider)
        _ensure_non_empty_str("news_item.endpoint", self.endpoint)
        _ensure_non_empty_str("news_item.raw_payload_ref", self.raw_payload_ref)
        _ensure_sha256("news_item.payload_hash", self.payload_hash)


@dataclass(frozen=True)
class NewsDomainData:
    schema_version: Literal["cn_a_news_pack.v1"]
    company_news: list[NewsItem]
    industry_macro_news: list[NewsItem]
    announcements: list[NewsItem]
    rejected_count: int

    def __post_init__(self) -> None:
        if self.schema_version != "cn_a_news_pack.v1":
            _schema_error("news domain schema_version 必须为 cn_a_news_pack.v1")
        _ensure_int_min("news_domain_data.rejected_count", self.rejected_count, 0)


@dataclass(frozen=True)
class SocialToolInput:
    ticker: str
    market: Literal["CN_A"]
    company_name: str | None
    industry: str | None
    start_date: str | None
    end_date: str | None
    aliases: list[str] | None
    approved_aliases: list[str] | None = None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("social_input.ticker", self.ticker)
        _ensure_cn_a_market("social_input.market", self.market)
        _ensure_optional_str("social_input.company_name", self.company_name)
        _ensure_optional_str("social_input.industry", self.industry)
        _ensure_optional_date("social_input.start_date", self.start_date)
        _ensure_optional_date("social_input.end_date", self.end_date)
        if self.aliases is not None:
            for index, alias in enumerate(self.aliases):
                _ensure_non_empty_str(f"social_input.aliases[{index}]", alias)
        if self.approved_aliases is not None:
            for index, alias in enumerate(self.approved_aliases):
                _ensure_non_empty_str(f"social_input.approved_aliases[{index}]", alias)


@dataclass(frozen=True)
class SocialSignal:
    signal_id: str
    signal_type: Literal["attention", "topic_keyword", "related_symbol", "narrative"]
    source_platform: SocialSourcePlatform
    observed_at: str
    target_ticker: str
    matched_target: bool
    match_evidence_span: str
    rank: int | None
    heat_value: float | None
    keyword: str | None
    related_ticker: str | None
    text_excerpt: str | None
    provider: str
    endpoint: str
    raw_payload_ref: str
    payload_hash: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("social_signal.signal_id", self.signal_id)
        _ensure_non_empty_str("social_signal.observed_at", self.observed_at)
        _ensure_non_empty_str("social_signal.target_ticker", self.target_ticker)
        _ensure_non_empty_str("social_signal.match_evidence_span", self.match_evidence_span)
        if self.rank is not None:
            _ensure_int_min("social_signal.rank", self.rank, 1)
        if self.heat_value is not None:
            _ensure_non_negative("social_signal.heat_value", self.heat_value)
        _ensure_optional_str("social_signal.keyword", self.keyword)
        _ensure_optional_str("social_signal.related_ticker", self.related_ticker)
        _ensure_optional_str("social_signal.text_excerpt", self.text_excerpt)
        _ensure_non_empty_str("social_signal.provider", self.provider)
        _ensure_non_empty_str("social_signal.endpoint", self.endpoint)
        _ensure_non_empty_str("social_signal.raw_payload_ref", self.raw_payload_ref)
        _ensure_sha256("social_signal.payload_hash", self.payload_hash)


@dataclass(frozen=True)
class SocialDomainData:
    schema_version: Literal["cn_a_social_pack.v1"]
    attention_signals: list[SocialSignal]
    topic_keyword_signals: list[SocialSignal]
    related_symbol_signals: list[SocialSignal]
    narrative_signals: list[SocialSignal]
    rejected_count: int
    social_judgment_allowed: bool

    def __post_init__(self) -> None:
        if self.schema_version != "cn_a_social_pack.v1":
            _schema_error("social domain schema_version 必须为 cn_a_social_pack.v1")
        _ensure_int_min("social_domain_data.rejected_count", self.rejected_count, 0)


@dataclass(frozen=True)
class FundamentalToolInput:
    ticker: str
    market: Literal["CN_A"]
    company_name: str | None
    start_date: str | None
    end_date: str | None

    def __post_init__(self) -> None:
        _ensure_non_empty_str("fundamental_input.ticker", self.ticker)
        _ensure_cn_a_market("fundamental_input.market", self.market)
        _ensure_optional_str("fundamental_input.company_name", self.company_name)
        _ensure_optional_date("fundamental_input.start_date", self.start_date)
        _ensure_optional_date("fundamental_input.end_date", self.end_date)


@dataclass(frozen=True)
class FundamentalField:
    field_name: FundamentalFieldName
    value: str | float | None
    unit: FundamentalUnit | None
    report_period: str | None
    source_time: str | None
    provider: str
    endpoint: str
    raw_payload_ref: str
    payload_hash: str
    conflict_group: str | None

    def __post_init__(self) -> None:
        _ensure_optional_str("fundamental_field.report_period", self.report_period)
        _ensure_optional_str("fundamental_field.source_time", self.source_time)
        _ensure_non_empty_str("fundamental_field.provider", self.provider)
        _ensure_non_empty_str("fundamental_field.endpoint", self.endpoint)
        _ensure_non_empty_str("fundamental_field.raw_payload_ref", self.raw_payload_ref)
        _ensure_sha256("fundamental_field.payload_hash", self.payload_hash)
        _ensure_optional_str("fundamental_field.conflict_group", self.conflict_group)


@dataclass(frozen=True)
class FundamentalDomainData:
    schema_version: Literal["cn_a_fundamental_pack.v1"]
    company_profile: dict[str, FundamentalField]
    valuation_fields: dict[str, FundamentalField]
    financial_fields: dict[str, FundamentalField]
    cash_flow_fields: dict[str, FundamentalField]
    missing_core_fields: list[str]
    conflict_diagnostics: list[str]

    def __post_init__(self) -> None:
        if self.schema_version != "cn_a_fundamental_pack.v1":
            _schema_error("fundamental domain schema_version 必须为 cn_a_fundamental_pack.v1")
        for field_group in (
            self.company_profile,
            self.valuation_fields,
            self.financial_fields,
            self.cash_flow_fields,
        ):
            for key, field_value in field_group.items():
                _ensure_non_empty_str("fundamental_domain_data field key", key)
                if not isinstance(field_value, FundamentalField):
                    _schema_error("fundamental domain 字段值必须是 FundamentalField")
        for index, item in enumerate(self.missing_core_fields):
            _ensure_non_empty_str(f"fundamental_domain_data.missing_core_fields[{index}]", item)
        for index, item in enumerate(self.conflict_diagnostics):
            _ensure_non_empty_str(f"fundamental_domain_data.conflict_diagnostics[{index}]", item)


@dataclass(frozen=True)
class BriefInput:
    domain: Domain
    input: PackInput
    quality: Quality
    provider_attempts: list[ProviderAttempt]
    accepted_counts: dict[str, int]
    missing_items: list[str]
    conflict_diagnostics: list[str]
    evidence_summary: list[str]

    def __post_init__(self) -> None:
        if not self.provider_attempts:
            _schema_error("brief_input.provider_attempts 不能为空")
        for key, value in self.accepted_counts.items():
            _ensure_non_empty_str("brief_input.accepted_counts key", key)
            _ensure_int_min(f"brief_input.accepted_counts[{key}]", value, 0)
        for index, item in enumerate(self.missing_items):
            _ensure_non_empty_str(f"brief_input.missing_items[{index}]", item)
        for index, item in enumerate(self.conflict_diagnostics):
            _ensure_non_empty_str(f"brief_input.conflict_diagnostics[{index}]", item)
        for index, item in enumerate(self.evidence_summary):
            _ensure_non_empty_str(f"brief_input.evidence_summary[{index}]", item)
            if _URI_RE.search(item):
                _schema_error("brief_input.evidence_summary 不允许包含 URI 主体")


@dataclass(frozen=True)
class GateInput:
    run_id: str
    worker_id: str
    call_id: str
    domain: Domain
    quality_status: QualityStatus
    provider_attempt_count: int
    l2_verified_count: int
    missing_core_fields: list[str]
    unsupported_claim_risk_fields: list[str]
    diagnostic_flags: list[str]

    def __post_init__(self) -> None:
        _ensure_non_empty_str("gate_input.run_id", self.run_id)
        _ensure_non_empty_str("gate_input.worker_id", self.worker_id)
        _ensure_non_empty_str("gate_input.call_id", self.call_id)
        _ensure_int_min("gate_input.provider_attempt_count", self.provider_attempt_count, 0)
        _ensure_int_min("gate_input.l2_verified_count", self.l2_verified_count, 0)
        for index, item in enumerate(self.missing_core_fields):
            _ensure_non_empty_str(f"gate_input.missing_core_fields[{index}]", item)
        for index, item in enumerate(self.unsupported_claim_risk_fields):
            _ensure_non_empty_str(f"gate_input.unsupported_claim_risk_fields[{index}]", item)
        for index, item in enumerate(self.diagnostic_flags):
            _ensure_non_empty_str(f"gate_input.diagnostic_flags[{index}]", item)


@dataclass(frozen=True)
class TechlabInputFrame:
    ticker: str
    rows: list[MarketPriceRow]
    output_dir: str

    def __post_init__(self) -> None:
        _ensure_non_empty_str("techlab_input.ticker", self.ticker)
        _ensure_non_empty_str("techlab_input.output_dir", self.output_dir)


@dataclass(frozen=True)
class TechlabResult:
    indicators: MarketIndicators | None
    chart_paths: list[str]
    chart_refs: list[ChartRef]
    diagnostics: list[str]
    failed: bool
    chart_evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        for index, path in enumerate(self.chart_paths):
            _ensure_non_empty_str(f"techlab_result.chart_paths[{index}]", path)
        for index, diagnostic in enumerate(self.diagnostics):
            _ensure_non_empty_str(f"techlab_result.diagnostics[{index}]", diagnostic)


def _schema_error(message: str) -> None:
    raise FrontlineValidationError(PACK_SCHEMA_INVALID, message)


def _ensure_non_empty_str(name: str, value: object) -> None:
    if not isinstance(value, str) or value.strip() == "":
        _schema_error(f"{name} 必须是非空字符串")


def _ensure_optional_non_empty_str(name: str, value: object) -> None:
    if value is None:
        return
    _ensure_non_empty_str(name, value)


def _ensure_optional_str(name: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        _schema_error(f"{name} 必须是字符串或 None")


def _ensure_cn_a_market(name: str, value: object) -> None:
    if value != "CN_A":
        _schema_error(f"{name} 必须是 CN_A")


def _ensure_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _schema_error(f"{name} 必须是 sha256:<64位小写hex>")


def _ensure_optional_sha256(name: str, value: object) -> None:
    if value is None:
        return
    _ensure_sha256(name, value)


def _ensure_int_min(name: str, value: object, minimum: int) -> None:
    if not isinstance(value, int) or value < minimum:
        _schema_error(f"{name} 必须是 >= {minimum} 的整数")


def _ensure_int_range(name: str, value: object, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or value < minimum or value > maximum:
        _schema_error(f"{name} 必须在 [{minimum}, {maximum}] 范围")


def _ensure_non_negative(name: str, value: object) -> None:
    if not isinstance(value, (int, float)) or value < 0:
        _schema_error(f"{name} 必须是 >= 0 的数值")


def _ensure_positive(name: str, value: object) -> None:
    if not isinstance(value, (int, float)) or value <= 0:
        _schema_error(f"{name} 必须是 > 0 的数值")


def _ensure_date(name: str, value: object) -> None:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        _schema_error(f"{name} 必须是 YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        _schema_error(f"{name} 不是合法日期: {exc}")


def _ensure_optional_date(name: str, value: object) -> None:
    if value is None:
        return
    _ensure_date(name, value)


def _validate_relative_path(path_text: str) -> None:
    _ensure_non_empty_str("l2_target.relative_path", path_text)
    if path_text.startswith("/") or "\\" in path_text:
        _schema_error("l2_target.relative_path 必须是相对 POSIX 路径")
    path = PurePosixPath(path_text)
    if any(part in {"", ".", ".."} for part in path.parts):
        _schema_error("l2_target.relative_path 不允许 . 或 ..")


__all__ = [
    "AttemptStatus",
    "AtrIndicators",
    "BriefInput",
    "BollIndicators",
    "CacheInspection",
    "ChartRef",
    "Domain",
    "EvidenceRef",
    "FieldSource",
    "FreshnessStatus",
    "FundamentalDomainData",
    "FundamentalField",
    "FundamentalFieldName",
    "FundamentalToolInput",
    "FundamentalUnit",
    "GateInput",
    "KdjIndicators",
    "L2WriteReceipt",
    "L2WriteRequest",
    "L2WriteTarget",
    "MacdIndicators",
    "MarketDateRange",
    "MarketDomainData",
    "MarketIndicators",
    "MarketPriceHistory",
    "MarketPriceRow",
    "MarketSupportResistance",
    "MarketToolInput",
    "MarketVolumeProfile",
    "MarketVolumeProfileBucket",
    "MovingAverageIndicators",
    "NewsDomainData",
    "NewsItem",
    "NewsMatchType",
    "NewsTargetProfile",
    "NewsToolInput",
    "PACK_SCHEMA_VERSION",
    "PackEnvelope",
    "PackInput",
    "ProviderAttempt",
    "ProviderCacheDocument",
    "ProviderCacheKey",
    "ProviderMode",
    "ProviderParamSource",
    "ProviderPriority",
    "ProviderQuery",
    "ProviderQueryParameter",
    "ProviderResult",
    "ProviderSpec",
    "Quality",
    "QualityStatus",
    "RsiIndicators",
    "SocialDomainData",
    "SocialSignal",
    "SocialSourcePlatform",
    "SocialToolInput",
    "SupportResistanceLevel",
    "TechlabInputFrame",
    "TechlabResult",
    "replace_attempt_evidence",
    "stable_json_bytes",
    "stable_json_dumps",
    "to_jsonable",
]
