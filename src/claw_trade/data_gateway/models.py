from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    SHARED_RESULT = "shared_result"


class DataGapReason(StrEnum):
    CREDENTIAL_MISSING = "credential_missing"
    RATE_LIMITED = "rate_limited"
    EMPTY = "empty"
    FIELD_MISSING = "field_missing"
    SCHEMA_INVALID = "schema_invalid"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SOURCE_NOT_CONFIGURED = "source_not_configured"
    CACHE_ERROR = "cache_error"
    CACHED_EMPTY = "cached_empty"
    STALE_CACHE_UNUSABLE = "stale_cache_unusable"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    LICENSE_BLOCKED = "license_blocked"


class FreshnessStatus(StrEnum):
    FRESH_REMOTE = "fresh_remote"
    FRESH_CACHE = "fresh_cache"
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
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"


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
        SourceRole.MARKET_DATA,
        SourceRole.DERIVATIVE_MARKET_DATA,
        SourceRole.FUNDAMENTAL_DATA,
        SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        SourceRole.MACRO_DATA,
    }
)

DISCOVERY_ONLY_SOURCE_ROLES = frozenset(
    {
        SourceRole.SEARCH_DISCOVERY,
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

RAW_EXPORT_POLICIES = frozenset({"metadata_only", "redacted", "full"})


def _require_enum(name: str, value: object, enum_type: type[StrEnum]) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{name} must be {enum_type.__name__}")


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

    def __post_init__(self) -> None:
        _require_enum("provider_kind", self.provider_kind, ProviderKind)
        _require_enum("market", self.market, Market)
        _require_enum("domain", self.domain, PackDomain)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("priority_source", self.priority_source, PrioritySource)
        if self.raw_export_policy not in RAW_EXPORT_POLICIES:
            raise ValueError(f"raw_export_policy must be one of {sorted(RAW_EXPORT_POLICIES)}")
        if self.coverage_quorum is not None and self.coverage_quorum <= 0:
            raise ValueError("coverage_quorum must be > 0")


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

    def __post_init__(self) -> None:
        _require_enum("provider_kind", self.provider_kind, ProviderKind)
        _require_enum("source_role", self.source_role, SourceRole)
        _require_enum("market", self.market, Market)
        _require_enum("domain", self.domain, PackDomain)
        _require_enum("priority_source", self.priority_source, PrioritySource)
        if self.raw_export_policy not in RAW_EXPORT_POLICIES:
            raise ValueError(f"raw_export_policy must be one of {sorted(RAW_EXPORT_POLICIES)}")
        if self.cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")


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
        if self.status == ProviderStatus.SHARED_RESULT:
            if self.single_flight_role != "consumer":
                raise ValueError("shared_result requires single_flight_role=consumer")
            if not self.shared_from_attempt_id:
                raise ValueError("shared_result requires shared_from_attempt_id")
            if not (self.raw_ref or self.normalized_ref):
                raise ValueError("shared_result requires evidence refs")
        if self.status == ProviderStatus.LICENSE_BLOCKED and not self.license_note.strip():
            raise ValueError("license_blocked requires license_note")


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
        if self.status == ProviderStatus.SHARED_RESULT and self.attempt.status != ProviderStatus.SHARED_RESULT:
            raise ValueError("shared_result provider result must carry shared_result attempt")
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

    def __post_init__(self) -> None:
        _require_enum("domain", self.domain, PackDomain)
        _require_enum("severity", self.severity, GapSeverity)
        _require_enum("reason", self.reason, DataGapReason)


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
