from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:
    from claw_trade.data_gateway.providers.base import ProviderAdapter


class Market(StrEnum):
    CN_A = "CN_A"
    HK = "HK"
    US = "US"
    CRYPTO = "CRYPTO"


class PackDomain(StrEnum):
    MARKET = "market"
    FUNDAMENTAL = "fundamental"
    NEWS = "news"
    SOCIAL = "social"
    POLICY = "policy"
    HOT_MONEY = "hot_money"
    LOCKUP = "lockup"
    SELECT_FEATURE = "select_feature"


class SourceRole(StrEnum):
    OFFICIAL_ORIGINAL = "official_original"
    MARKET_DATA = "market_data"
    DERIVATIVE_MARKET_DATA = "derivative_market_data"
    FUNDAMENTAL_DATA = "fundamental_data"
    SOCIAL_ORIGINAL_SAMPLE = "social_original_sample"
    SOCIAL_AGGREGATE_METRIC = "social_aggregate_metric"
    SEARCH_DISCOVERY = "search_discovery"
    EVENT_EXPECTATION = "event_expectation"
    MACRO_DATA = "macro_data"
    OFFICIAL = "official"
    PAID_DATA = "paid_data"
    BUILT_IN_PUBLIC = "built_in_public"
    DISCOVERY = "discovery"
    SENTIMENT = "sentiment"


class ProviderKind(StrEnum):
    OPENBB_NATIVE = "openbb_native"
    PROJECT_EXTENSION = "project_extension"
    USER_DECLARATIVE = "user_declarative"


class ProviderAdmissionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    VALIDATED = "validated"
    ENABLED_CANDIDATE = "enabled_candidate"
    DISABLED = "disabled"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class PrioritySource(StrEnum):
    SYSTEM_DEFAULT = "system_default"
    USER_PREFERRED = "user_preferred"


class ProviderStatus(StrEnum):
    REMOTE_SUCCESS = "remote_success"
    REMOTE_ERROR = "remote_error"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    CREDENTIAL_MISSING = "credential_missing"
    LICENSE_BLOCKED = "license_blocked"
    RATE_LIMITED = "rate_limited"
    EMPTY = "empty"
    FIELD_MISSING = "field_missing"
    SCHEMA_INVALID = "schema_invalid"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CACHE_STALE = "cache_stale"
    CACHE_ERROR = "cache_error"
    CACHED_EMPTY = "cached_empty"
    WAREHOUSE_HIT = "warehouse_hit"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    SHARED_RESULT = "shared_result"
    COOLDOWN_SKIPPED = "cooldown_skipped"
    NOT_APPLICABLE = "not_applicable"
    SDK_HTTP_UNKNOWN = "sdk_http_unknown"


class DataGapReason(StrEnum):
    MONGO_MISSING = "mongo_missing"
    MONGO_STALE = "mongo_stale"
    REMOTE_ERROR = "remote_error"
    CREDENTIAL_MISSING = "credential_missing"
    RATE_LIMITED = "rate_limited"
    COOLDOWN_SKIPPED = "cooldown_skipped"
    EMPTY = "empty"
    FIELD_MISSING = "field_missing"
    SCHEMA_INVALID = "schema_invalid"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SOURCE_NOT_CONFIGURED = "source_not_configured"
    CACHE_ERROR = "cache_error"
    CACHED_EMPTY = "cached_empty"
    SDK_HTTP_UNKNOWN = "sdk_http_unknown"
    NOT_APPLICABLE = "not_applicable"
    STALE_CACHE_UNUSABLE = "stale_cache_unusable"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    LICENSE_BLOCKED = "license_blocked"


class FreshnessStatus(StrEnum):
    FRESH_REMOTE = "fresh_remote"
    FRESH_CACHE = "fresh_cache"
    FRESH_WAREHOUSE = "fresh_warehouse"
    STALE_CACHE = "stale_cache"
    CACHE_UNUSABLE = "cache_unusable"
    NOT_FETCHED = "not_fetched"


class ReadinessStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    BLOCKED = "blocked"


class AdmissionCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"
    BLOCKED = "blocked"


class GapSeverity(StrEnum):
    BLOCKER = "blocker"
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"


class RequiredLevel(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    EXPENSIVE = "expensive"
    NOT_APPLICABLE = "not_applicable"


class ConsumerType(StrEnum):
    REPORT_WORKER = "report_worker"
    SELECT_STRATEGY = "select_strategy"
    UI_PROBE = "ui_probe"
    PRICE_ALERT = "price_alert"


class RequestKind(StrEnum):
    REPORT = "report"
    SELECT = "select"
    UI_PROBE = "ui_probe"
    PRICE_ALERT = "price_alert"


class HttpVisibility(StrEnum):
    MANAGED_HTTP = "managed_http"
    SDK_INTERNAL_UNKNOWN = "sdk_internal_unknown"
    NO_HTTP = "no_http"


class FreshnessDecisionStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    PARTIAL = "partial"
    INVALID_QUALITY = "invalid_quality"


class RateLimitDecisionStatus(StrEnum):
    RESERVED = "reserved"
    BLOCKED = "blocked"
    COOLDOWN_ACTIVE = "cooldown_active"
    BUDGET_EXHAUSTED = "budget_exhausted"


class CooldownReason(StrEnum):
    HTTP_429 = "http_429"
    QUOTA_EXCEEDED = "quota_exceeded"
    PROVIDER_ERROR_BURST = "provider_error_burst"
    MANUAL = "manual"


class SingleFlightStatus(StrEnum):
    ACQUIRED = "acquired"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class CacheScope(StrEnum):
    PROVIDER_CALL = "provider_call"
    MANAGED_HTTP = "managed_http"


class PayloadKind(StrEnum):
    JSON = "json"
    CSV = "csv"
    HTML = "html"
    BINARY = "binary"
    METADATA_ONLY = "metadata_only"


class PayloadLicenseMode(StrEnum):
    STORE_FULL = "store_full"
    METADATA_ONLY = "metadata_only"
    NO_STORE = "no_store"


class DomainPackApprovalStatus(StrEnum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    WARN = "warn"


class SelectSupportStatus(StrEnum):
    SUPPORTED = "supported"
    TARGET_DESIGN = "target_design"
    UNSUPPORTED = "unsupported"


class ProviderDisplayStatus(StrEnum):
    SHOW = "show"
    HIDE = "hide"
    HIDE_UNTIL_INTEGRATED = "hide_until_integrated"
    HIDE_BUILTIN = "hide_builtin"


class DomainReadiness(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    MISSING = "missing"
    ERROR = "error"
    STALE = "stale"
    LICENSE_BLOCKED = "license_blocked"


CRYPTO_DOMAIN_KEYS = (
    "market",
    "ohlcv",
    "derivatives",
    "liquidation_map",
    "onchain",
    "macro",
    "events",
    "ahr999",
)

CRYPTO_INPUT_POLLUTION_KEYS = frozenset(
    {
        "provider_raw_payload",
        "raw_payload",
        "debug_envelope",
        "openclaw_provider_payload",
        "openviking_protocol",
        "prompt_material",
        "prompt_material_body",
        "runtime_target",
        "report_submission",
        "material_id",
        "capability",
    }
)


PRIMARY_FACT_SOURCE_ROLES = frozenset(
    {
        SourceRole.OFFICIAL_ORIGINAL,
        SourceRole.OFFICIAL,
        SourceRole.MARKET_DATA,
        SourceRole.DERIVATIVE_MARKET_DATA,
        SourceRole.FUNDAMENTAL_DATA,
        SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        SourceRole.PAID_DATA,
        SourceRole.BUILT_IN_PUBLIC,
        SourceRole.SENTIMENT,
        SourceRole.MACRO_DATA,
    }
)

DISCOVERY_ONLY_SOURCE_ROLES = frozenset(
    {
        SourceRole.SEARCH_DISCOVERY,
        SourceRole.DISCOVERY,
        SourceRole.EVENT_EXPECTATION,
    }
)

CACHE_DECISION_STATUSES = frozenset(
    {
        ProviderStatus.CACHE_HIT,
        ProviderStatus.CACHE_MISS,
        ProviderStatus.CACHE_STALE,
        ProviderStatus.CACHE_ERROR,
        ProviderStatus.CACHED_EMPTY,
    }
)

CACHE_NON_REMOTE_STATUSES = frozenset(
    {
        ProviderStatus.CACHE_HIT,
        ProviderStatus.CACHE_STALE,
        ProviderStatus.CACHE_ERROR,
        ProviderStatus.CACHED_EMPTY,
    }
)

PROVIDER_CALL_STATUSES = frozenset(
    {
        ProviderStatus.REMOTE_SUCCESS,
        ProviderStatus.REMOTE_ERROR,
        ProviderStatus.EMPTY,
        ProviderStatus.FIELD_MISSING,
        ProviderStatus.CREDENTIAL_MISSING,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.CACHE_HIT,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.CACHE_STALE,
        ProviderStatus.WAREHOUSE_HIT,
        ProviderStatus.SHARED_RESULT,
        ProviderStatus.NOT_APPLICABLE,
        ProviderStatus.EVIDENCE_WRITE_FAILED,
        ProviderStatus.SDK_HTTP_UNKNOWN,
    }
)

PROVIDER_NON_REMOTE_SUCCESS_STATUSES = PROVIDER_CALL_STATUSES - {ProviderStatus.REMOTE_SUCCESS}
GAP_REQUIRED_PROVIDER_STATUSES = frozenset(
    {
        ProviderStatus.REMOTE_ERROR,
        ProviderStatus.EMPTY,
        ProviderStatus.FIELD_MISSING,
        ProviderStatus.CREDENTIAL_MISSING,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.NOT_APPLICABLE,
        ProviderStatus.EVIDENCE_WRITE_FAILED,
        ProviderStatus.SDK_HTTP_UNKNOWN,
    }
)

RAW_EXPORT_POLICIES = frozenset({"metadata_only", "redacted", "full"})
CN_A_ONLY_DOMAINS = frozenset(
    {
        PackDomain.POLICY,
        PackDomain.HOT_MONEY,
        PackDomain.LOCKUP,
    }
)


def _require_enum(name: str, value: object, enum_type: type[StrEnum]) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{name} must be {enum_type.__name__}")


def _require_all_enum(name: str, values: tuple[object, ...], enum_type: type[StrEnum]) -> None:
    for value in values:
        _require_enum(name, value, enum_type)


def provider_status_is_remote_success(status: ProviderStatus) -> bool:
    _require_enum("status", status, ProviderStatus)
    return status == ProviderStatus.REMOTE_SUCCESS


def provider_status_requires_gap(status: ProviderStatus) -> bool:
    _require_enum("status", status, ProviderStatus)
    return status in GAP_REQUIRED_PROVIDER_STATUSES


def validate_market_domain(market: Market, domain: PackDomain) -> None:
    _require_enum("market", market, Market)
    _require_enum("domain", domain, PackDomain)
    if domain in CN_A_ONLY_DOMAINS and market != Market.CN_A:
        raise ValueError(f"{domain.value} is only approved for CN_A")


def source_role_can_be_primary_fact(role: SourceRole) -> bool:
    return role in PRIMARY_FACT_SOURCE_ROLES


def source_role_is_discovery_only(role: SourceRole) -> bool:
    return role in DISCOVERY_ONLY_SOURCE_ROLES


def _assert_no_crypto_input_pollution(value: object, path: str = "domains") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_str = str(key)
            if key_str in CRYPTO_INPUT_POLLUTION_KEYS:
                raise ValueError(f"crypto input contains forbidden field: {path}.{key_str}")
            _assert_no_crypto_input_pollution(nested, f"{path}.{key_str}")
        return
    if isinstance(value, tuple):
        for idx, nested in enumerate(value):
            _assert_no_crypto_input_pollution(nested, f"{path}[{idx}]")
        return
    if isinstance(value, list):
        for idx, nested in enumerate(value):
            _assert_no_crypto_input_pollution(nested, f"{path}[{idx}]")
        return


def _assert_natural_language_material(name: str, value: str) -> None:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    if stripped.startswith("{"):
        raise ValueError(f"{name} must be natural language, not json envelope")
    forbidden = (
        "debug_envelope",
        "provider_raw_payload",
        "raw_payload_ref",
        "normalized_ref",
        "http_evidence_refs",
        "openviking_protocol",
        "provider_attempt",
        "RuntimeTarget",
        "ReportSubmission",
    )
    if any(token in value for token in forbidden):
        raise ValueError(f"{name} must not expose internal refs/debug/protocol material")


@dataclass(frozen=True)
class FreshnessPolicy:
    max_age_seconds: int
    require_remote_for_domains: tuple[str, ...] = ()
    allow_stale_for_non_core: bool = False
    allow_cached_empty: bool = False

    def __post_init__(self) -> None:
        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be > 0")


@dataclass(frozen=True)
class DataRequirement:
    requirement_id: str
    market: Market
    data_type: str
    granularity: str
    ticker: str | None
    universe_ref: str | None
    date_range: tuple[date, date] | None
    lookback_window_days: int | None
    current_date: date
    freshness_policy: str
    required_level: RequiredLevel
    consumer_type: ConsumerType
    consumer_id: str
    domain: PackDomain
    source_role_required: SourceRole | None = None
    field_set: tuple[str, ...] = ()
    allow_search_discovery: bool = False

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        _require_enum("required_level", self.required_level, RequiredLevel)
        _require_enum("consumer_type", self.consumer_type, ConsumerType)
        _require_enum("domain", self.domain, PackDomain)
        if self.source_role_required is not None:
            _require_enum("source_role_required", self.source_role_required, SourceRole)
        validate_market_domain(self.market, self.domain)
        if not self.requirement_id.strip():
            raise ValueError("requirement_id must not be empty")
        if not self.data_type.strip():
            raise ValueError("data_type must not be empty")
        if not self.granularity.strip():
            raise ValueError("granularity must not be empty")
        if self.lookback_window_days is not None and self.lookback_window_days <= 0:
            raise ValueError("lookback_window_days must be > 0")
        if not self.consumer_id.strip():
            raise ValueError("consumer_id must not be empty")
        if not self.freshness_policy.strip():
            raise ValueError("freshness_policy must not be empty")
        if self.ticker is None and self.universe_ref is None:
            raise ValueError("DataRequirement requires ticker or universe_ref")


@dataclass(frozen=True)
class RequirementBatch:
    batch_id: str
    request_id: str
    request_kind: RequestKind
    original_requirements: tuple[DataRequirement, ...]
    merged_requirements: tuple[DataRequirement, ...]
    duplicate_map: Mapping[str, tuple[str, ...]]
    created_at: datetime
    profile: str | None = None

    def __post_init__(self) -> None:
        _require_enum("request_kind", self.request_kind, RequestKind)
        if not self.batch_id.strip():
            raise ValueError("batch_id must not be empty")
        if not self.request_id.strip():
            raise ValueError("request_id must not be empty")
        if not self.original_requirements:
            raise ValueError("RequirementBatch requires original_requirements")
        if not self.merged_requirements:
            raise ValueError("RequirementBatch requires merged_requirements")
        merged_ids = {requirement.requirement_id for requirement in self.merged_requirements}
        if set(self.duplicate_map) - merged_ids:
            raise ValueError("duplicate_map keys must be merged requirement ids")


@dataclass(frozen=True)
class ProviderPriorityRule:
    rule_id: str
    market: Market
    data_type: str
    ordered_provider_ids: tuple[str, ...]
    official_provider_ids: tuple[str, ...] = ()
    user_config_priority_scope: tuple[str, ...] = ()
    search_discovery_provider_ids: tuple[str, ...] = ()
    fallback_allowed_with_evidence: bool = True
    not_applicable_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        if not self.rule_id.strip():
            raise ValueError("rule_id must not be empty")
        if not self.data_type.strip():
            raise ValueError("data_type must not be empty")
        if not self.ordered_provider_ids:
            raise ValueError("ordered_provider_ids must not be empty")
        missing_official = set(self.official_provider_ids) - set(self.ordered_provider_ids)
        if missing_official:
            raise ValueError("official_provider_ids must be in ordered_provider_ids")


@dataclass(frozen=True)
class ProviderCallResult:
    result_id: str
    spec: "ProviderCallSpec"
    status: ProviderStatus
    rows: tuple[Mapping[str, Any], ...]
    raw_payload_ref: str | None
    normalized_ref: str | None
    attempt_ref: str | None
    http_evidence_refs: tuple[str, ...]
    cache_entry_ref: str | None
    shared_owner_attempt_ref: str | None
    data_gaps: tuple["DataGap", ...]
    remote_success: bool
    created_at: datetime

    def __post_init__(self) -> None:
        _require_enum("status", self.status, ProviderStatus)
        if self.status not in PROVIDER_CALL_STATUSES:
            raise ValueError(f"ProviderCallResult status not allowed for data layer call: {self.status}")
        if self.remote_success != provider_status_is_remote_success(self.status):
            raise ValueError("remote_success must be true only for status=remote_success")
        if not self.attempt_ref:
            raise ValueError(f"{self.status.value} requires attempt_ref")
        if self.status == ProviderStatus.REMOTE_SUCCESS:
            if not self.raw_payload_ref or not self.normalized_ref:
                raise ValueError("remote_success requires raw_payload_ref and normalized_ref")
        if self.status in PROVIDER_NON_REMOTE_SUCCESS_STATUSES and self.remote_success:
            raise ValueError(f"{self.status.value} cannot be converted to remote_success")
        if provider_status_requires_gap(self.status) and not self.data_gaps:
            raise ValueError(f"{self.status.value} requires data_gaps")
        if self.status == ProviderStatus.CACHE_HIT and not self.cache_entry_ref:
            raise ValueError("cache_hit requires cache_entry_ref")
        if self.status == ProviderStatus.SHARED_RESULT and not self.shared_owner_attempt_ref:
            raise ValueError("shared_result requires shared_owner_attempt_ref")
        if self.status == ProviderStatus.SDK_HTTP_UNKNOWN and self.http_evidence_refs:
            raise ValueError("sdk_http_unknown must not fabricate http_evidence_refs")


@dataclass(frozen=True)
class HttpEvidence:
    http_evidence_id: str
    provider_id: str
    endpoint_id: str
    method: str
    host: str
    path: str
    query_hash: str
    body_hash: str | None
    request_headers_redacted: Mapping[str, str]
    response_status: int | None
    response_headers_redacted: Mapping[str, str]
    response_body_hash: str | None
    elapsed_ms: int | None
    quota_signal: str | None
    cache_key: str
    rate_limit_or_cooldown_recorded: bool
    sdk_internal_unknown: bool = False

    def __post_init__(self) -> None:
        if self.sdk_internal_unknown:
            raise ValueError("SDK internal unknown must be represented on attempt/result, not fake HttpEvidence")
        if self.elapsed_ms is not None and self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be >= 0")


@dataclass(frozen=True)
class RawPayloadRecord:
    raw_payload_id: str
    provider_id: str
    endpoint_id: str
    requirement_id: str
    attempt_id: str
    payload_kind: PayloadKind
    payload_ref: str | None
    payload_hash: str
    row_count: int | None
    captured_at: datetime
    license_mode: PayloadLicenseMode
    manifest_hash: str | None = None

    def __post_init__(self) -> None:
        _require_enum("payload_kind", self.payload_kind, PayloadKind)
        _require_enum("license_mode", self.license_mode, PayloadLicenseMode)
        if not self.payload_hash.strip():
            raise ValueError("payload_hash must not be empty")
        if self.row_count is not None and self.row_count < 0:
            raise ValueError("row_count must be >= 0")


@dataclass(frozen=True)
class NormalizedRecord:
    normalized_id: str
    market: Market
    data_type: str
    ticker: str | None
    universe_ref: str | None
    period_start: date | datetime | None
    period_end: date | datetime | None
    rows_ref: str
    row_count: int
    schema_version: str
    source_provider_id: str
    source_attempt_id: str
    raw_payload_id: str | None
    quality_flags: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        if not self.rows_ref.strip():
            raise ValueError("rows_ref must not be empty")
        if self.row_count < 0:
            raise ValueError("row_count must be >= 0")
        if self.ticker is None and self.universe_ref is None:
            raise ValueError("NormalizedRecord requires ticker or universe_ref")


@dataclass(frozen=True)
class CacheEntry:
    cache_entry_id: str
    cache_scope: CacheScope
    cache_key: str
    status: ProviderStatus
    created_at: datetime
    expires_at: datetime
    result_ref: str | None
    raw_payload_ref: str | None
    normalized_ref: str | None
    http_evidence_ref: str | None
    empty_reason: str | None
    stale_after: datetime

    def __post_init__(self) -> None:
        _require_enum("cache_scope", self.cache_scope, CacheScope)
        _require_enum("status", self.status, ProviderStatus)
        allowed = {
            ProviderStatus.REMOTE_SUCCESS,
            ProviderStatus.EMPTY,
            ProviderStatus.REMOTE_ERROR,
            ProviderStatus.CACHED_EMPTY,
        }
        if self.status not in allowed:
            raise ValueError("CacheEntry status must be provider-call cache result status")
        if self.status == ProviderStatus.REMOTE_SUCCESS and not (self.raw_payload_ref and self.normalized_ref):
            raise ValueError("remote_success cache entry requires raw_payload_ref and normalized_ref")


@dataclass(frozen=True)
class FreshnessDecision:
    decision_id: str
    requirement_id: str
    status: FreshnessDecisionStatus
    mongo_query_ref: str
    matched_normalized_refs: tuple[str, ...]
    missing_fields: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    latest_data_time: datetime | date | None
    required_data_time: datetime | date | None
    should_call_provider: bool
    data_gaps: tuple["DataGap", ...]

    def __post_init__(self) -> None:
        _require_enum("status", self.status, FreshnessDecisionStatus)
        if self.status == FreshnessDecisionStatus.FRESH and self.should_call_provider:
            raise ValueError("fresh decision must not call provider")
        if self.status != FreshnessDecisionStatus.FRESH and not self.should_call_provider and not self.data_gaps:
            raise ValueError("non-fresh decision without provider call requires data_gaps")


@dataclass(frozen=True)
class RateLimitDecision:
    decision_id: str
    spec_id: str
    rate_limit_key: str
    status: RateLimitDecisionStatus
    reserve_token: str | None
    retry_after: datetime | None
    reason: str
    remote_allowed: bool

    def __post_init__(self) -> None:
        _require_enum("status", self.status, RateLimitDecisionStatus)
        if self.remote_allowed != (self.status == RateLimitDecisionStatus.RESERVED):
            raise ValueError("remote_allowed is true only when reserved")


@dataclass(frozen=True)
class CooldownState:
    cooldown_key: str
    provider_id: str
    endpoint_id: str | None
    market: Market | None
    reason: CooldownReason
    active_until: datetime
    source_attempt_id: str | None
    source_http_evidence_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.market is not None:
            _require_enum("market", self.market, Market)
        _require_enum("reason", self.reason, CooldownReason)
        if not self.source_attempt_id and not self.source_http_evidence_id:
            raise ValueError("CooldownState requires source attempt or HTTP evidence ref")


@dataclass(frozen=True)
class SingleFlightLease:
    lease_id: str
    single_flight_key: str
    owner_request_id: str
    owner_attempt_id: str | None
    consumer_request_ids: tuple[str, ...]
    status: SingleFlightStatus
    acquired_at: datetime
    expires_at: datetime
    result_ref: str | None

    def __post_init__(self) -> None:
        _require_enum("status", self.status, SingleFlightStatus)
        if self.status in {SingleFlightStatus.COMPLETED, SingleFlightStatus.FAILED} and not self.result_ref:
            raise ValueError("completed/failed single-flight lease requires result_ref")


@dataclass(frozen=True)
class PackRequest:
    run_id: str
    call_id: str
    worker_id: str
    market: Market
    domain: PackDomain
    ticker: str
    company_name: str
    start_date: str
    end_date: str
    current_date: str
    currency: str
    profile: str
    freshness_policy: FreshnessPolicy

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        _require_enum("domain", self.domain, PackDomain)
        validate_market_domain(self.market, self.domain)


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    adapter_id: str
    provider_kind: ProviderKind
    market: Market
    domain: PackDomain
    endpoint: str
    source_role: SourceRole
    expected_schema_id: str
    license_policy_id: str
    credential_requirements: tuple[str, ...]
    rate_limit_policy_id: str
    cache_ttl_seconds: int
    required: bool
    attempt_required: bool
    coverage_group: str | None
    coverage_quorum: int | None
    priority: int
    priority_source: PrioritySource = PrioritySource.SYSTEM_DEFAULT
    raw_export_policy: str = "metadata_only"
    data_type: str | None = None
    coverage_fields: tuple[str, ...] = ()
    coverage_symbols: str | None = None
    freshness_supported: tuple[str, ...] = ()
    user_config_key: str | None = None
    can_be_formal_fact_source: bool | None = None
    can_enter_worker_pack: bool | None = None
    http_visibility: HttpVisibility | None = None
    live_fresh_required_for_ui: bool = False

    def __post_init__(self) -> None:
        _require_enum("provider_kind", self.provider_kind, ProviderKind)
        _require_enum("market", self.market, Market)
        _require_enum("domain", self.domain, PackDomain)
        validate_market_domain(self.market, self.domain)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("priority_source", self.priority_source, PrioritySource)
        if self.raw_export_policy not in RAW_EXPORT_POLICIES:
            raise ValueError(f"raw_export_policy must be one of {sorted(RAW_EXPORT_POLICIES)}")
        if self.coverage_quorum is not None and self.coverage_quorum <= 0:
            raise ValueError("coverage_quorum must be > 0")
        if self.http_visibility is not None:
            _require_enum("http_visibility", self.http_visibility, HttpVisibility)
        if self.can_be_formal_fact_source and source_role_is_discovery_only(self.source_role):
            raise ValueError("discovery-only provider cannot be a formal fact source")

    @property
    def provider_id(self) -> str:
        return self.provider


@dataclass(frozen=True)
class ProviderCallSpec:
    call_key: str
    provider: str
    adapter_id: str
    provider_kind: ProviderKind
    provider_config_version: str
    endpoint: str
    source_role: SourceRole
    market: Market
    domain: PackDomain
    required: bool
    attempt_required: bool
    coverage_group: str | None
    coverage_quorum: int | None
    params: Mapping[str, Any]
    cache_ttl_seconds: int
    license_policy_id: str
    expected_schema_id: str
    priority: int
    priority_source: PrioritySource
    user_preferred: bool
    raw_export_policy: str = "metadata_only"
    spec_id: str | None = None
    requirement_id: str | None = None
    data_type: str | None = None
    priority_rank: int | None = None
    rate_limit_key: str | None = None
    cooldown_key: str | None = None
    single_flight_key: str | None = None
    freshness_policy_id: str | None = None
    managed_http_required: bool = False

    def __post_init__(self) -> None:
        _require_enum("provider_kind", self.provider_kind, ProviderKind)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("market", self.market, Market)
        _require_enum("domain", self.domain, PackDomain)
        validate_market_domain(self.market, self.domain)
        _require_enum("priority_source", self.priority_source, PrioritySource)
        if self.raw_export_policy not in RAW_EXPORT_POLICIES:
            raise ValueError(f"raw_export_policy must be one of {sorted(RAW_EXPORT_POLICIES)}")
        if self.cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        if self.priority_rank is not None and self.priority_rank < 0:
            raise ValueError("priority_rank must be >= 0")

    @property
    def provider_id(self) -> str:
        return self.provider

    @property
    def endpoint_id(self) -> str:
        return self.endpoint


@dataclass(frozen=True)
class DeclarativeProviderManifest:
    provider_id: str
    adapter_id: str
    display_name: str
    version: str
    config_version: str
    markets: tuple[Market, ...]
    domains: tuple[PackDomain, ...]
    endpoints: tuple[str, ...]
    source_role: SourceRole
    expected_schema_id: str
    base_url: str
    request_template: Mapping[str, Any]
    response_mapping: Mapping[str, str]
    credential_requirements: tuple[str, ...]
    rate_limit_policy_id: str
    cache_ttl_seconds: int
    license_policy_id: str
    raw_export_policy: str
    healthcheck: Mapping[str, Any]
    enabled: bool
    admission_status: ProviderAdmissionStatus
    priority: int
    priority_source: PrioritySource
    coverage_group: str | None
    coverage_quorum: int | None

    def __post_init__(self) -> None:
        for market in self.markets:
            _require_enum("markets", market, Market)
        for domain in self.domains:
            _require_enum("domains", domain, PackDomain)
        for market in self.markets:
            for domain in self.domains:
                validate_market_domain(market, domain)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("admission_status", self.admission_status, ProviderAdmissionStatus)
        _require_enum("priority_source", self.priority_source, PrioritySource)


@dataclass(frozen=True)
class GatewaySettings:
    openbb_runtime_url: str
    openbb_home: str
    mongo_uri: str
    provider_config_version: str
    provider_catalog_path: str
    provider_catalog: Any
    provider_settings: Mapping[str, Mapping[str, Any]]
    secret_store_uri: str
    object_store_uri: str
    single_flight_lease_seconds: int
    raw_payload_inline_max_bytes: int
    allowed_declarative_provider_domains: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.single_flight_lease_seconds <= 0:
            raise ValueError("single_flight_lease_seconds must be > 0")
        if self.raw_payload_inline_max_bytes <= 0:
            raise ValueError("raw_payload_inline_max_bytes must be > 0")


@dataclass(frozen=True)
class CredentialStatus:
    status: AdmissionCheckStatus
    provider: str
    adapter_id: str
    missing_keys: tuple[str, ...] = ()
    invalid_keys: tuple[str, ...] = ()
    root_cause: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == AdmissionCheckStatus.PASS

    @property
    def missing(self) -> bool:
        return self.status == AdmissionCheckStatus.MISSING

    def to_gap(self, request: PackRequest, adapter: ProviderAdapter) -> DataGap:
        root_cause = self.root_cause or "credential missing"
        return DataGap(
            gap_id=f"{request.run_id}:{request.call_id}:{adapter.adapter_id}:credential",
            domain=request.domain,
            severity=GapSeverity.FAIL,
            reason=DataGapReason.CREDENTIAL_MISSING,
            field_path="credentials",
            provider_candidates=(adapter.provider_id,),
            attempt_ids=(),
            root_cause=root_cause,
            next_action="configure required credential keys",
        )


@dataclass(frozen=True)
class LicenseCheckResult:
    status: AdmissionCheckStatus
    license_policy_id: str
    cost_tier: str
    raw_export_policy: str
    commercial_use_allowed: bool
    note: str


@dataclass(frozen=True)
class ProviderValidationReceipt:
    provider_id: str
    adapter_id: str
    config_version: str
    status: ProviderAdmissionStatus
    credential_status: AdmissionCheckStatus
    healthcheck_status: AdmissionCheckStatus
    schema_status: AdmissionCheckStatus
    license_status: AdmissionCheckStatus
    secret_status: AdmissionCheckStatus
    credential_detail: CredentialStatus | None
    license_detail: LicenseCheckResult | None
    sample_raw_ref: str | None
    sample_normalized_ref: str | None
    transition_actor: str
    previous_status: ProviderAdmissionStatus | None
    transition_reason: str
    errors: tuple[str, ...]
    validated_at: str


@dataclass(frozen=True)
class RateLimitPlanItem:
    provider: str
    endpoint: str
    call_key: str
    window_seconds: int
    estimated_cost: int
    hard_reserved: bool = False


@dataclass(frozen=True)
class RunProviderPlan:
    run_id: str
    provider_config_version: str
    market: Market
    ticker: str
    domains: tuple[PackDomain, ...]
    call_specs: tuple[ProviderCallSpec, ...]
    shared_call_keys: tuple[str, ...]
    cache_keys: tuple[str, ...]
    rate_limit_plan: tuple[RateLimitPlanItem, ...]
    initial_gaps: tuple["DataGap", ...]
    generated_at: str
    remote_prefetch_allowed: bool = False

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        for domain in self.domains:
            _require_enum("domains", domain, PackDomain)
            validate_market_domain(self.market, domain)
        if self.remote_prefetch_allowed:
            raise ValueError("RunProviderPlan.remote_prefetch_allowed must be false")


@dataclass(frozen=True)
class CacheReceipt:
    cache_key: str
    provider: str
    endpoint: str
    status: ProviderStatus
    hit: bool
    stale: bool
    cached_empty: bool
    created_at: str | None
    expires_at: str | None
    ttl_seconds: int
    evidence_hash: str | None
    raw_ref: str | None
    normalized_ref: str | None

    def __post_init__(self) -> None:
        _require_enum("status", self.status, ProviderStatus)
        if self.status not in CACHE_DECISION_STATUSES:
            raise ValueError(f"cache receipt status must be cache status, got {self.status}")


@dataclass(frozen=True)
class CacheDecision:
    status: ProviderStatus
    receipt: CacheReceipt
    usable_raw_ref: str | None
    usable_normalized_ref: str | None
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_enum("status", self.status, ProviderStatus)
        if self.status not in CACHE_DECISION_STATUSES:
            raise ValueError(f"cache decision status must be cache status, got {self.status}")
        if self.status != self.receipt.status:
            raise ValueError("cache decision status must match receipt status")


@dataclass(frozen=True)
class ProviderFetch:
    payload: bytes | str | Mapping[str, Any]
    content_type: str
    source_url: str | None
    is_empty: bool
    row_count: int | None
    provider_request_id: str | None
    http_method: str = "GET"
    response_status_code: int | None = None
    response_headers_summary: Mapping[str, str] | None = None


@dataclass(frozen=True)
class NormalizedResult:
    status: ProviderStatus
    schema_id: str
    rows: tuple[Mapping[str, Any], ...]
    compact_facts: Mapping[str, Any]
    row_count: int
    field_units: Mapping[str, str]
    currency: str | None
    timezone: str | None
    source_raw_ref: str | None
    missing_fields: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class NormalizedBundle:
    request: PackRequest
    results: tuple[NormalizedResult, ...]
    rows_by_schema: Mapping[str, tuple[Mapping[str, Any], ...]]
    compact_facts: Mapping[str, Any]
    field_units: Mapping[str, str]
    currencies: tuple[str, ...]
    timezones: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    bundle_ref: str | None


@dataclass(frozen=True)
class ProviderAttempt:
    attempt_id: str
    run_id: str
    call_id: str
    worker_id: str
    pack: str
    provider: str
    adapter_id: str
    adapter_kind: str
    provider_kind: ProviderKind
    provider_config_version: str
    endpoint: str
    source_role: SourceRole
    started_at: str
    finished_at: str
    status: ProviderStatus
    required: bool
    attempt_required: bool
    coverage_group: str | None
    coverage_quorum: int | None
    priority_source: PrioritySource
    user_preferred: bool
    from_cache: bool
    cache_status: ProviderStatus | None
    single_flight_role: Literal["none", "owner", "consumer"]
    shared_from_attempt_id: str | None
    latency_ms: int
    row_count: int | None
    raw_ref: str | None
    normalized_ref: str | None
    error_code: str | None
    error_message: str | None
    schema_id: str
    license_note: str
    source_metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_enum("provider_kind", self.provider_kind, ProviderKind)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("status", self.status, ProviderStatus)
        _require_enum("priority_source", self.priority_source, PrioritySource)
        if self.cache_status is not None and self.cache_status not in CACHE_DECISION_STATUSES:
            raise ValueError(f"cache_status must be cache status, got {self.cache_status}")
        if self.status == ProviderStatus.REMOTE_SUCCESS and self.cache_status in CACHE_NON_REMOTE_STATUSES:
            raise ValueError(
                "cache_hit/cache_stale/cache_error/cached_empty cannot be converted to remote_success"
            )
        if self.status == ProviderStatus.REMOTE_SUCCESS and (not self.raw_ref or not self.normalized_ref):
            raise ValueError("remote_success requires raw_ref and normalized_ref")
        if self.status in PROVIDER_NON_REMOTE_SUCCESS_STATUSES and self.cache_status in CACHE_NON_REMOTE_STATUSES:
            if self.status == ProviderStatus.REMOTE_SUCCESS:
                raise ValueError("non-remote cache status cannot be remote_success")
        if self.status == ProviderStatus.SHARED_RESULT:
            if self.single_flight_role != "consumer":
                raise ValueError("shared_result requires single_flight_role=consumer")
            if not self.shared_from_attempt_id:
                raise ValueError("shared_result requires shared_from_attempt_id")
            if not (self.raw_ref or self.normalized_ref):
                raise ValueError("shared_result requires evidence refs")
        if self.status == ProviderStatus.LICENSE_BLOCKED and not self.license_note.strip():
            raise ValueError("license_blocked requires license_note")

    @property
    def remote_success(self) -> bool:
        return self.status == ProviderStatus.REMOTE_SUCCESS

    @property
    def remote_attempted(self) -> bool:
        return self.status not in {
            ProviderStatus.CACHE_HIT,
            ProviderStatus.CACHED_EMPTY,
            ProviderStatus.CACHE_STALE,
            ProviderStatus.WAREHOUSE_HIT,
            ProviderStatus.SHARED_RESULT,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.COOLDOWN_SKIPPED,
            ProviderStatus.NOT_APPLICABLE,
            ProviderStatus.SKIPPED_NOT_CONFIGURED,
        }


@dataclass(frozen=True)
class OpenBBProviderHttpEvidence:
    evidence_id: str
    run_id: str
    call_id: str
    worker_id: str
    pack: str
    provider: str
    adapter_id: str
    provider_kind: ProviderKind
    provider_config_version: str
    endpoint: str
    source_role: SourceRole
    requested_at: str
    finished_at: str
    http_method: str
    source_url: str | None
    response_status_code: int | None
    response_headers_summary: Mapping[str, str]
    provider_request_id: str | None
    latency_ms: int
    status: ProviderStatus
    error_code: str | None
    error_message: str | None
    raw_ref: str | None

    def __post_init__(self) -> None:
        _require_enum("provider_kind", self.provider_kind, ProviderKind)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("status", self.status, ProviderStatus)
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")


@dataclass(frozen=True)
class ProviderResult:
    spec: ProviderCallSpec
    status: ProviderStatus
    request_id: str | None
    requested_at: str
    latency_ms: int
    source_role: SourceRole
    freshness: FreshnessStatus
    license_note: str
    raw_ref: str | None
    normalized_ref: str | None
    rows: tuple[Mapping[str, Any], ...]
    row_count: int
    cache_receipt: CacheReceipt | None
    attempt: ProviderAttempt
    missing_fields: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _require_enum("status", self.status, ProviderStatus)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("freshness", self.freshness, FreshnessStatus)
        if self.status == ProviderStatus.REMOTE_SUCCESS and self.cache_receipt is not None:
            if self.cache_receipt.status in CACHE_NON_REMOTE_STATUSES:
                raise ValueError(
                    "cache_hit/cache_stale/cache_error/cached_empty cannot be converted to remote_success"
                )
        if self.status == ProviderStatus.REMOTE_SUCCESS and (not self.raw_ref or not self.normalized_ref):
            raise ValueError("remote_success requires raw_ref and normalized_ref")
        if self.status == ProviderStatus.SHARED_RESULT and self.attempt.status != ProviderStatus.SHARED_RESULT:
            raise ValueError("shared_result provider result must carry shared_result attempt")
        if self.status in PROVIDER_NON_REMOTE_SUCCESS_STATUSES and self.attempt.remote_success:
            raise ValueError(f"{self.status.value} cannot carry remote_success attempt")
        if self.status == ProviderStatus.LICENSE_BLOCKED and not self.license_note.strip():
            raise ValueError("license_blocked requires license_note")


@dataclass(frozen=True)
class DataGap:
    gap_id: str
    domain: PackDomain
    severity: GapSeverity
    reason: DataGapReason
    field_path: str
    provider_candidates: tuple[str, ...]
    attempt_ids: tuple[str, ...]
    root_cause: str
    next_action: str
    requirement_id: str | None = None
    market: Market | None = None
    data_type: str | None = None
    ticker: str | None = None
    official_source_required: bool = False
    search_discovery_only: bool = False
    evidence_refs: tuple[str, ...] = ()
    human_readable: str | None = None

    def __post_init__(self) -> None:
        _require_enum("domain", self.domain, PackDomain)
        _require_enum("severity", self.severity, GapSeverity)
        _require_enum("reason", self.reason, DataGapReason)
        if self.market is not None:
            _require_enum("market", self.market, Market)
        if self.human_readable is not None:
            _assert_natural_language_material("human_readable", self.human_readable)

    @property
    def worker_visible_text(self) -> str:
        return self.human_readable or self.root_cause


@dataclass(frozen=True)
class DomainPack:
    pack_id: str
    run_id: str
    domain: PackDomain
    market: Market
    ticker: str | None
    normalized_refs: tuple[str, ...]
    raw_payload_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    http_evidence_refs: tuple[str, ...]
    data_gaps: tuple[DataGap, ...]
    readable_markdown: str
    source_summary: str
    approval_status: DomainPackApprovalStatus
    material_ref: str | None = None

    def __post_init__(self) -> None:
        _require_enum("domain", self.domain, PackDomain)
        _require_enum("market", self.market, Market)
        validate_market_domain(self.market, self.domain)
        _require_enum("approval_status", self.approval_status, DomainPackApprovalStatus)
        _assert_natural_language_material("readable_markdown", self.readable_markdown)
        _assert_natural_language_material("source_summary", self.source_summary)
        if self.approval_status == DomainPackApprovalStatus.APPROVED and not self.normalized_refs:
            raise ValueError("approved DomainPack requires normalized_refs")
        if self.approval_status == DomainPackApprovalStatus.APPROVED and any(
            gap.reason == DataGapReason.EVIDENCE_WRITE_FAILED for gap in self.data_gaps
        ):
            raise ValueError("evidence_write_failed blocks approved DomainPack material")

    @property
    def worker_primary_material_md(self) -> str:
        return self.readable_markdown

    def worker_visible_material(self) -> tuple[str, ...]:
        gap_texts = tuple(gap.worker_visible_text for gap in self.data_gaps)
        return (self.readable_markdown, self.source_summary, *gap_texts)


@dataclass(frozen=True)
class ReportDataPlan:
    plan_id: str
    report_run_id: str
    market: Market
    ticker: str
    company_name: str | None
    date_range: tuple[date, date]
    current_date: date
    profile: str
    workers: tuple[str, ...]
    requirement_batch: RequirementBatch
    provider_call_specs: tuple[ProviderCallSpec, ...]
    domain_pack_ids: tuple[str, ...]
    data_gap_ids: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        if self.requirement_batch.request_kind != RequestKind.REPORT:
            raise ValueError("ReportDataPlan requires report RequirementBatch")
        if not self.workers:
            raise ValueError("ReportDataPlan requires workers")


@dataclass(frozen=True)
class SelectDataPlan:
    plan_id: str
    select_run_id: str
    market: Market
    universe_ref: str
    strategy_refs: tuple[str, ...]
    current_date: date
    lookback_window_days: int
    requirement_batch: RequirementBatch
    warehouse_checks: tuple[FreshnessDecision, ...]
    provider_call_specs: tuple[ProviderCallSpec, ...]
    feature_refs: tuple[str, ...]
    data_gap_ids: tuple[str, ...]
    support_status: SelectSupportStatus

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        _require_enum("support_status", self.support_status, SelectSupportStatus)
        if self.requirement_batch.request_kind != RequestKind.SELECT:
            raise ValueError("SelectDataPlan requires select RequirementBatch")
        if self.lookback_window_days <= 0:
            raise ValueError("lookback_window_days must be > 0")


@dataclass(frozen=True)
class ProviderDisplayDecision:
    provider_id: str
    market: Market
    display_status: ProviderDisplayStatus
    reason: str
    requires_user_credential: bool
    changes_report_or_select_result: bool
    writes_mongo_and_evidence: bool
    enters_domain_pack: bool
    consumed_by_worker_or_strategy: bool
    live_fresh_evidence_ref: str | None
    probe_only: bool

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        _require_enum("display_status", self.display_status, ProviderDisplayStatus)
        if self.display_status == ProviderDisplayStatus.SHOW:
            required = (
                self.changes_report_or_select_result,
                self.writes_mongo_and_evidence,
                self.enters_domain_pack,
                self.consumed_by_worker_or_strategy,
                bool(self.live_fresh_evidence_ref),
            )
            if self.probe_only or not all(required):
                raise ValueError("provider display requires real report/select chain evidence")


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    field_path: str
    values: tuple[str, ...]
    provider_refs: tuple[str, ...]
    resolution: str
    confidence: str


@dataclass(frozen=True)
class ProviderSourceRef:
    ref_id: str
    provider: str
    adapter_id: str
    endpoint: str
    source_role: SourceRole
    status: ProviderStatus
    normalized_ref: str | None = None

    def __post_init__(self) -> None:
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("status", self.status, ProviderStatus)


@dataclass(frozen=True)
class CryptoDomainBundle:
    market: Mapping[str, Any] | None
    ohlcv: Mapping[str, Any] | None
    derivatives: Mapping[str, Any] | None
    liquidation_map: Mapping[str, Any] | None
    onchain: Mapping[str, Any] | None
    macro: Mapping[str, Any] | None
    events: Mapping[str, Any] | None
    ahr999: Mapping[str, Any] | None

    def to_mapping(self) -> Mapping[str, Mapping[str, Any] | None]:
        return {
            "market": self.market,
            "ohlcv": self.ohlcv,
            "derivatives": self.derivatives,
            "liquidation_map": self.liquidation_map,
            "onchain": self.onchain,
            "macro": self.macro,
            "events": self.events,
            "ahr999": self.ahr999,
        }


@dataclass(frozen=True)
class NormalizedCryptoMarketBundle:
    run_id: str
    call_id: str
    ticker: str
    market: Market
    quote: str
    as_of: str
    start_date: str
    end_date: str
    freshness: FreshnessStatus
    domains: CryptoDomainBundle
    domain_status: Mapping[str, DomainReadiness]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    source_refs: tuple[ProviderSourceRef, ...]
    attempt_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_enum("market", self.market, Market)
        _require_enum("freshness", self.freshness, FreshnessStatus)
        if self.market != Market.CRYPTO:
            raise ValueError("NormalizedCryptoMarketBundle.market must be CRYPTO")
        expected_keys = set(CRYPTO_DOMAIN_KEYS)
        if set(self.domain_status) != expected_keys:
            raise ValueError("domain_status must include all crypto domains")
        domain_payloads = self.domains.to_mapping()
        for domain_key in CRYPTO_DOMAIN_KEYS:
            status = self.domain_status[domain_key]
            _require_enum(f"domain_status[{domain_key}]", status, DomainReadiness)
            payload = domain_payloads[domain_key]
            if payload is None and status in (
                DomainReadiness.READY,
                DomainReadiness.PARTIAL,
                DomainReadiness.INSUFFICIENT,
                DomainReadiness.STALE,
            ):
                raise ValueError(f"{domain_key} payload missing for readiness={status}")
            if payload is not None:
                _assert_no_crypto_input_pollution(payload, f"domains.{domain_key}")

    def to_worker_material_contract(self) -> Mapping[str, Any]:
        return {
            "run_id": self.run_id,
            "call_id": self.call_id,
            "ticker": self.ticker,
            "market": self.market.value,
            "quote": self.quote,
            "as_of": self.as_of,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "freshness": self.freshness.value,
            "domain_status": {k: v.value for k, v in self.domain_status.items()},
            "data_gaps": tuple(gap.gap_id for gap in self.data_gaps),
            "conflicts": tuple(conflict.conflict_id for conflict in self.conflicts),
            "source_refs": tuple(ref.ref_id for ref in self.source_refs),
            "attempt_refs": self.attempt_refs,
            "normalized_refs": self.normalized_refs,
        }


@dataclass(frozen=True)
class Readiness:
    status: ReadinessStatus
    coverage: Mapping[str, str]
    required_domains: tuple[str, ...]
    missing_domains: tuple[str, ...]
    blocking_gap_ids: tuple[str, ...]
    non_blocking_gap_ids: tuple[str, ...]
    root_cause: str | None

    def __post_init__(self) -> None:
        _require_enum("status", self.status, ReadinessStatus)


@dataclass(frozen=True)
class ChartAsset:
    chart_id: str
    title: str
    kind: str
    image_ref: str | None
    data_ref: str | None
    status: ReadinessStatus
    root_cause: str | None

    def __post_init__(self) -> None:
        _require_enum("status", self.status, ReadinessStatus)


@dataclass(frozen=True)
class PackPlan:
    request: PackRequest
    call_specs: tuple[ProviderCallSpec, ...]
    initial_gaps: tuple[DataGap, ...]


@dataclass(frozen=True)
class PackAuditPayload:
    request: PackRequest
    openbb_runtime_marker: str
    openbb_extension_version: str
    run_provider_plan_id: str
    call_specs: tuple[ProviderCallSpec, ...]
    attempts: tuple[ProviderAttempt, ...]
    cache_receipts: tuple[CacheReceipt, ...]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    readiness: Readiness
    chart_assets: tuple[ChartAsset, ...]
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    normalized_bundle_ref: str | None
    payload_hash: str
    generated_at: str
    analysis_evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPackResult:
    request: PackRequest
    reader_brief_md: str
    compact_facts: Mapping[str, Any]
    attempts: tuple[ProviderAttempt, ...]
    cache_receipts: tuple[CacheReceipt, ...]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    readiness: Readiness
    chart_assets: tuple[ChartAsset, ...]
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    normalized_bundle_ref: str | None
    audit_ref: str
    audit_payload_hash: str
    audit_payload: PackAuditPayload

    def __post_init__(self) -> None:
        if not self.reader_brief_md.strip():
            raise ValueError("reader_brief_md must not be empty")
        if self.reader_brief_md.lstrip().startswith("{"):
            raise ValueError("reader_brief_md must be natural language, not json envelope")

    @property
    def worker_primary_material_md(self) -> str:
        return self.reader_brief_md

    def to_audit_payload_ref(self) -> dict[str, str]:
        return {
            "audit_ref": self.audit_ref,
            "audit_payload_hash": self.audit_payload_hash,
            "generated_at": self.audit_payload.generated_at,
        }


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()
