from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class Market(str, Enum):
    CN_A = "CN_A"
    US = "US"
    HK = "HK"
    CRYPTO = "CRYPTO"


class DataResultStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    ERROR = "error"


class GapSeverity(str, Enum):
    BLOCKER = "blocker"
    WARN = "warn"
    INFO = "info"


class GapReason(str, Enum):
    CREDENTIAL_MISSING = "credential_missing"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    RATE_LIMITED_BY_TOOL_BUDGET = "rate_limited_by_tool_budget"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_EMPTY = "provider_empty"
    PARSER_MISSING = "parser_missing"
    EMPTY_RESULT = "empty_result"
    WAREHOUSE_MISSING = "warehouse_missing"
    WAREHOUSE_STALE = "warehouse_stale"
    FIELD_MISSING = "field_missing"
    DATE_RANGE_MISSING = "date_range_missing"
    DATA_INTEGRITY_FAILED = "data_integrity_failed"
    GRANULARITY_MISMATCH = "granularity_mismatch"
    LICENSE_BLOCKED = "license_blocked"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    CACHE_HIT = "cache_hit"
    SHARED_RESULT = "shared_result"
    CACHED_EMPTY = "cached_empty"
    COOLDOWN_SKIPPED = "cooldown_skipped"
    RESOLVER_MAPPING_MISSING = "resolver_mapping_missing"
    NOT_APPLICABLE = "not_applicable"
    INVALID_REQUEST = "invalid_request"
    SDK_HTTP_UNKNOWN = "sdk_http_unknown"


class FetchStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    CREDENTIAL_MISSING = "credential_missing"
    NOT_APPLICABLE = "not_applicable"
    SDK_HTTP_UNKNOWN = "sdk_http_unknown"


class SourceRole(str, Enum):
    OFFICIAL = "official"
    PAID_DATA = "paid_data"
    BUILT_IN_PUBLIC = "built_in_public"
    DISCOVERY = "discovery"
    SENTIMENT = "sentiment"
    EVENT_EXPECTATION = "event_expectation"


class RequiredLevel(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    EXPENSIVE = "expensive"
    NOT_APPLICABLE = "not_applicable"


class HttpVisibility(str, Enum):
    MANAGED_HTTP = "managed_http"
    SDK_INTERNAL_UNKNOWN = "sdk_internal_unknown"
    NO_HTTP = "no_http"


class PaginationPolicy(BaseModel):
    mode: str = "none"
    page_size: int | None = None
    max_pages: int | None = None


class SplitPolicy(BaseModel):
    mode: str = "strict"


class EndpointBatchPolicy(BaseModel):
    supports_batch: bool
    batch_by: Literal["none", "symbol", "date", "symbol_date", "field"]
    max_symbols_per_call: int | None = None
    max_days_per_call: int | None = None
    mergeable_fields: tuple[str, ...] = ()
    pagination_policy: PaginationPolicy = Field(default_factory=PaginationPolicy)
    split_policy: SplitPolicy = Field(default_factory=SplitPolicy)

    @model_validator(mode="after")
    def validate_policy(self) -> "EndpointBatchPolicy":
        if not self.supports_batch:
            if self.batch_by != "none":
                raise ValueError("supports_batch=False 时 batch_by 必须为 none")
            if self.mergeable_fields:
                raise ValueError("supports_batch=False 时 mergeable_fields 必须为空")
        if self.supports_batch and self.batch_by == "none":
            raise ValueError("supports_batch=True 时 batch_by 不能为 none")
        return self


class RateLimitPolicy(BaseModel):
    max_requests: int | None = None
    window_seconds: int = 60
    safety_margin: int = 0
    overflow: Literal["wait", "fail_fast"] = "fail_fast"


class LicensePolicy(BaseModel):
    raw_storage_mode: Literal["store_full", "metadata_only", "no_store"] = "store_full"
    normalized_storage_allowed: bool = True
    redistribution_allowed: bool = False
    retention_days: int | None = None


class DataRequest(BaseModel):
    request_id: str
    market: Market
    symbol_id: str | None = None
    universe_ref: str | None = None
    exchange: str | None = None
    currency: str | None = None
    timezone: str
    calendar: str
    base_asset: str | None = None
    quote_asset: str | None = None
    data_type: str
    granularity: str
    fields: tuple[str, ...]
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    freshness_policy: str
    source_role_required: SourceRole | None = None
    consumer: Literal["report", "select", "ui_probe", "price_alert", "maintenance"]
    consumer_id: str
    as_of: datetime

    @model_validator(mode="after")
    def validate_contract(self) -> "DataRequest":
        if bool(self.symbol_id) == bool(self.universe_ref):
            raise ValueError("symbol_id 与 universe_ref 必须二选一")
        if not self.fields:
            raise ValueError("fields 不能为空")
        if not self.timezone or not self.timezone.strip():
            raise ValueError("timezone 不能为空")
        if not self.calendar or not self.calendar.strip():
            raise ValueError("calendar 不能为空")
        if self.date_range_start and self.date_range_end and self.date_range_start > self.date_range_end:
            raise ValueError("date_range_start 不能晚于 date_range_end")
        if self.market == Market.CRYPTO and (not self.base_asset or not self.quote_asset):
            raise ValueError("CRYPTO 必须提供 base_asset 和 quote_asset")
        if self.market != Market.CRYPTO and (self.base_asset or self.quote_asset):
            raise ValueError("非 CRYPTO 不允许 base_asset 或 quote_asset")
        return self


class DataGap(BaseModel):
    gap_id: str
    request_id: str
    severity: GapSeverity
    reason: GapReason
    market: Market
    symbol_id: str | None = None
    exchange: str | None = None
    currency: str | None = None
    timezone: str | None = None
    calendar: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    data_type: str
    granularity: str
    required_fields: tuple[str, ...] = ()
    provider_ids_tried: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    human_readable: str
    as_of: datetime

    @model_validator(mode="after")
    def validate_gap(self) -> "DataGap":
        if self.reason == GapReason.FIELD_MISSING and not self.required_fields:
            raise ValueError("field_missing 必须提供 required_fields")
        if self.reason in {GapReason.RATE_LIMITED, GapReason.COOLDOWN_SKIPPED} and not self.evidence_refs:
            raise ValueError("rate_limited/cooldown_skipped 必须提供 evidence_refs")
        return self

    @classmethod
    def by_reason(
        cls,
        reason: str | GapReason,
        *,
        request_id: str = "unknown",
        market: Market = Market.CN_A,
        data_type: str = "unknown",
        granularity: str = "unknown",
        severity: GapSeverity = GapSeverity.BLOCKER,
        evidence_refs: tuple[str, ...] = (),
        required_fields: tuple[str, ...] = (),
        message: str | None = None,
        symbol_id: str | None = None,
        as_of: datetime | None = None,
    ) -> "DataGap":
        reason_value = _coerce_gap_reason(reason)
        normalized_required_fields = required_fields if reason_value == GapReason.FIELD_MISSING else ()
        if reason_value == GapReason.FIELD_MISSING and not normalized_required_fields:
            normalized_required_fields = ("unknown_field",)
        return cls(
            gap_id=f"gap:{request_id}:{reason_value.value}:{uuid4().hex[:10]}",
            request_id=request_id,
            severity=severity,
            reason=reason_value,
            market=market,
            symbol_id=symbol_id,
            data_type=data_type,
            granularity=granularity,
            required_fields=normalized_required_fields,
            evidence_refs=evidence_refs,
            human_readable=message or reason_value.value,
            as_of=as_of or datetime.now(tz=UTC),
        )

    @classmethod
    def invalid_request(
        cls,
        *,
        request_id: str,
        message: str,
        market: Market = Market.CN_A,
        as_of: datetime | None = None,
    ) -> "DataGap":
        return cls(
            gap_id=f"gap:{request_id}:invalid_request",
            request_id=request_id,
            severity=GapSeverity.BLOCKER,
            reason=GapReason.INVALID_REQUEST,
            market=market,
            data_type="unknown",
            granularity="unknown",
            human_readable=message,
            as_of=as_of or datetime.now(tz=UTC),
        )


class DataResult(BaseModel):
    request_id: str
    status: DataResultStatus
    rows: tuple[dict[str, Any], ...] = ()
    dataset_refs: tuple[str, ...] = ()
    raw_refs: tuple[str, ...] = ()
    attempt_refs: tuple[str, ...] = ()
    gaps: tuple[DataGap, ...] = ()
    freshness: dict[str, Any] = Field(default_factory=dict)
    source_summary: str | None = None
    as_of: datetime

    @model_validator(mode="after")
    def validate_status(self) -> "DataResult":
        if self.status == DataResultStatus.READY and not self.dataset_refs:
            raise ValueError("ready 必须提供 dataset_refs")
        if self.status in {DataResultStatus.MISSING, DataResultStatus.ERROR} and not self.gaps:
            raise ValueError("missing/error 必须提供 gaps")
        return self


class WarehouseCheck(BaseModel):
    request_id: str
    market: Market
    symbol_id: str | None = None
    universe_ref: str | None = None
    data_type: str
    granularity: str
    fields: tuple[str, ...]
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    freshness_policy: str
    timezone: str
    calendar: str
    source_role_required: SourceRole | None = None
    as_of: datetime | None = None


class CoverageRequirement(BaseModel):
    request_ids: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    required_fields_by_request: dict[str, tuple[str, ...]]


class QueryPlan(BaseModel):
    normalized_requests: tuple[DataRequest, ...]
    warehouse_checks: tuple[WarehouseCheck, ...]
    required_coverage: CoverageRequirement
    expected_outputs: tuple[str, ...]

    def request_for_gap(self, gap: Any) -> DataRequest:
        gap_request_id = self._read_attr(gap, "request_id")
        if isinstance(gap_request_id, str):
            for request in self.normalized_requests:
                if request.request_id == gap_request_id:
                    return request
        if len(self.normalized_requests) == 1:
            return self.normalized_requests[0]

        gap_market = self._read_attr(gap, "market")
        gap_data_type = self._read_attr(gap, "data_type")
        gap_symbol_id = self._read_attr(gap, "symbol_id")
        for request in self.normalized_requests:
            if gap_market is not None and request.market != gap_market:
                continue
            if gap_data_type is not None and request.data_type != gap_data_type:
                continue
            if gap_symbol_id is not None and request.symbol_id != gap_symbol_id:
                continue
            return request
        raise ValueError(f"query_plan_cannot_resolve_request_for_gap:{gap_request_id}")

    @staticmethod
    def _read_attr(obj: Any, name: str) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(name)
        return getattr(obj, name, None)


class ProviderCapability(BaseModel):
    provider_id: str
    plugin_version: str
    endpoint_id: str
    market: Market
    data_type: str
    source_role: SourceRole
    supported_granularities: tuple[str, ...]
    coverage_fields: tuple[str, ...]
    priority_rank: int
    credential_required: bool
    credential_names: tuple[str, ...] = ()
    credential_scope: str | None = None
    http_visibility: HttpVisibility
    can_be_formal_fact_source: bool
    license_policy: LicensePolicy
    rate_limit_policy: RateLimitPolicy
    batch_policy: EndpointBatchPolicy

    @model_validator(mode="after")
    def validate_capability(self) -> "ProviderCapability":
        if self.priority_rank < 0:
            raise ValueError("priority_rank 必须 >= 0")
        if self.credential_required and not self.credential_names:
            raise ValueError("credential_required=True 时必须声明 credential_names")
        if (
            self.source_role in {SourceRole.DISCOVERY, SourceRole.EVENT_EXPECTATION}
            and self.can_be_formal_fact_source
        ):
            raise ValueError("discovery/event_expectation 不能作为正式事实源")
        return self


class ProviderCandidate(BaseModel):
    request_id: str
    provider_id: str
    endpoint_id: str
    market: Market
    data_type: str
    granularity: str
    source_role: SourceRole
    priority_rank: int
    symbol_id: str | None = None
    universe_ref: str | None = None
    exchange: str | None = None
    currency: str | None = None
    timezone: str | None = None
    calendar: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    fields: tuple[str, ...]
    required_level: RequiredLevel
    configured_paid_data: bool = False


class MergeItem(BaseModel):
    request_id: str
    symbol_ids: tuple[str, ...]
    universe_ref: str | None = None
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    fields: tuple[str, ...]
    required_level: RequiredLevel
    exchange: str | None = None
    currency: str | None = None
    timezone: str | None = None
    calendar: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None


class MergeGroup(BaseModel):
    provider_id: str
    endpoint_id: str
    market: Market
    data_type: str
    granularity: str
    source_role: SourceRole
    priority_rank: int
    request_ids: tuple[str, ...]
    symbol_ids: tuple[str, ...]
    universe_ref: str | None = None
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    exchange: str | None = None
    currency: str | None = None
    timezone: str | None = None
    calendar: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    fields_union: tuple[str, ...]
    required_level: RequiredLevel = RequiredLevel.REQUIRED
    items: tuple[MergeItem, ...]
    plan_id: str = "plan-unknown"


class ProviderBatchPlan(BaseModel):
    batch_id: str
    plan_id: str
    provider_id: str
    endpoint_id: str
    market: Market
    data_type: str
    granularity: str
    request_ids: tuple[str, ...]
    symbol_ids: tuple[str, ...]
    universe_ref: str | None = None
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    exchange: str | None = None
    currency: str | None = None
    timezone: str | None = None
    calendar: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    fields_union: tuple[str, ...]
    capability_fields: tuple[str, ...] = ()
    params_redacted: dict[str, Any]
    priority_rank: int
    required_level: RequiredLevel
    cache_key: str
    rate_limit_key: str
    cooldown_key: str | None = None
    rate_limit_policy: Any | None = None
    http_visibility: HttpVisibility = HttpVisibility.MANAGED_HTTP
    single_flight_key: str
    lease_ttl_seconds: int = 30
    wait_timeout_seconds: int = 1
    provider_config_version: str
    license_policy: Any | None = None
    as_of: datetime

    @model_validator(mode="after")
    def validate_batch(self) -> "ProviderBatchPlan":
        if not self.request_ids:
            raise ValueError("request_ids 不能为空")
        if self.required_level == RequiredLevel.NOT_APPLICABLE:
            raise ValueError("not_applicable 不能进入真实 batch 计划")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds 必须大于 0")
        if self.wait_timeout_seconds < 0:
            raise ValueError("wait_timeout_seconds 必须大于等于 0")
        return self


class HttpObservation(BaseModel):
    request_key: str
    sent_at: datetime | None = None
    status_code: int | None = None
    error_code: str | None = None
    elapsed_ms: int | None = None


class FetchResult(BaseModel):
    fetch_id: str
    batch_id: str
    provider_id: str
    endpoint_id: str
    market: Market
    symbol_ids: tuple[str, ...]
    status: FetchStatus
    payload: Any | None = None
    payload_hash: str | None = None
    content_type: str | None = None
    http_observations: tuple[HttpObservation, ...] = ()
    row_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider_request_id: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @model_validator(mode="after")
    def validate_fetch(self) -> "FetchResult":
        if self.status == FetchStatus.SUCCESS and self.payload is None and not self.payload_hash:
            raise ValueError("success 必须有 payload 或 payload_hash")
        if self.status == FetchStatus.EMPTY and self.row_count not in (None, 0):
            raise ValueError("empty 时 row_count 必须为 0 或 None")
        return self

    @classmethod
    def from_success(
        cls,
        batch: Any,
        payload: Any,
        *,
        row_count: int | None = None,
        http_observations: tuple[Any, ...] = (),
    ) -> "FetchResult":
        digest = sha256(repr(payload).encode("utf-8")).hexdigest() if payload is not None else None
        symbol_ids = tuple(getattr(batch, "symbol_ids", ()) or ())
        if not symbol_ids:
            symbol_id = getattr(batch, "symbol_id", None)
            symbol_ids = (symbol_id,) if symbol_id else ()
        market = _coerce_market(getattr(batch, "market", Market.CN_A))
        return cls(
            fetch_id=f"fetch:{uuid4().hex}",
            batch_id=str(getattr(batch, "batch_id", "batch:unknown")),
            provider_id=str(getattr(batch, "provider_id", "unknown_provider")),
            endpoint_id=str(getattr(batch, "endpoint_id", "unknown_endpoint")),
            market=market,
            symbol_ids=symbol_ids,
            status=FetchStatus.SUCCESS,
            payload=payload,
            payload_hash=f"sha256:{digest}" if digest else None,
            row_count=row_count,
            http_observations=_coerce_http_observations(http_observations),
        )

    @classmethod
    def from_empty(cls, batch: Any, error: Exception | None = None, http_observations: tuple[Any, ...] = ()) -> "FetchResult":
        symbol_ids = tuple(getattr(batch, "symbol_ids", ()) or ())
        if not symbol_ids:
            symbol_id = getattr(batch, "symbol_id", None)
            symbol_ids = (symbol_id,) if symbol_id else ()
        return cls(
            fetch_id=f"fetch:{uuid4().hex}",
            batch_id=str(getattr(batch, "batch_id", "batch:unknown")),
            provider_id=str(getattr(batch, "provider_id", "unknown_provider")),
            endpoint_id=str(getattr(batch, "endpoint_id", "unknown_endpoint")),
            market=_coerce_market(getattr(batch, "market", Market.CN_A)),
            symbol_ids=symbol_ids,
            status=FetchStatus.EMPTY,
            error_code="empty_result",
            error_message=str(error) if error else None,
            row_count=0,
            http_observations=_coerce_http_observations(http_observations),
        )

    @classmethod
    def from_error(
        cls,
        batch: Any,
        *,
        status: Literal["error", "rate_limited", "credential_missing", "sdk_http_unknown", "not_applicable"],
        error: Exception | None = None,
        http_observations: tuple[Any, ...] = (),
    ) -> "FetchResult":
        symbol_ids = tuple(getattr(batch, "symbol_ids", ()) or ())
        if not symbol_ids:
            symbol_id = getattr(batch, "symbol_id", None)
            symbol_ids = (symbol_id,) if symbol_id else ()
        return cls(
            fetch_id=f"fetch:{uuid4().hex}",
            batch_id=str(getattr(batch, "batch_id", "batch:unknown")),
            provider_id=str(getattr(batch, "provider_id", "unknown_provider")),
            endpoint_id=str(getattr(batch, "endpoint_id", "unknown_endpoint")),
            market=_coerce_market(getattr(batch, "market", Market.CN_A)),
            symbol_ids=symbol_ids,
            status=FetchStatus(status),
            error_code=type(error).__name__ if error else status,
            error_message=str(error) if error else None,
            http_observations=_coerce_http_observations(http_observations),
        )


class IngestResult(BaseModel):
    ingest_id: str = Field(default_factory=lambda: f"ingest:{uuid4().hex}")
    batch_id: str = "batch:unknown"
    status: Literal["ingested", "partial", "failed", "non_remote_recorded"]
    dataset_refs: tuple[str, ...] = ()
    raw_refs: tuple[str, ...] = ()
    attempt_refs: tuple[str, ...] = ()
    gaps: tuple[DataGap, ...] = ()
    remote_success: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    cache_key: str | None = None
    cache_fresh_until: datetime | None = None
    cache_stale_until: datetime | None = None

    @model_validator(mode="after")
    def validate_ingest(self) -> "IngestResult":
        if self.status == "ingested" and not self.attempt_refs:
            raise ValueError("ingested 必须包含 attempt_refs")
        if self.remote_success and not self.raw_refs:
            raise ValueError("remote_success=True 必须包含 raw_refs")
        return self

    @classmethod
    def from_refs(
        cls,
        *,
        batch_id: str = "batch:unknown",
        dataset_refs: tuple[str, ...],
        raw_refs: tuple[str, ...],
        attempt_refs: tuple[str, ...],
        gaps: tuple[DataGap, ...],
        remote_success: bool,
        cache_key: str | None = None,
        cache_fresh_until: datetime | None = None,
        cache_stale_until: datetime | None = None,
    ) -> "IngestResult":
        if remote_success and dataset_refs:
            status: Literal["ingested", "partial", "failed", "non_remote_recorded"] = "ingested"
        elif remote_success and gaps:
            status = "partial"
        elif remote_success:
            status = "failed"
        elif dataset_refs and gaps:
            status = "partial"
        elif dataset_refs:
            status = "ingested"
        elif gaps:
            status = "failed"
        else:
            status = "non_remote_recorded"
        return cls(
            batch_id=batch_id,
            status=status,
            dataset_refs=dataset_refs,
            raw_refs=raw_refs,
            attempt_refs=attempt_refs,
            gaps=gaps,
            remote_success=remote_success,
            cache_key=cache_key,
            cache_fresh_until=cache_fresh_until,
            cache_stale_until=cache_stale_until,
        )

    @classmethod
    def failed(
        cls,
        reason: str | GapReason,
        *,
        batch_id: str = "batch:unknown",
        attempt_refs: tuple[str, ...] = (),
    ) -> "IngestResult":
        return cls(
            batch_id=batch_id,
            status="failed",
            attempt_refs=attempt_refs,
            gaps=(DataGap.by_reason(reason),),
            remote_success=False,
        )


class DataPlan(BaseModel):
    plan_id: str
    request_ids: tuple[str, ...]
    query_plan: QueryPlan
    warehouse_decisions: tuple[dict[str, Any], ...] = ()
    gaps: tuple[DataGap, ...] = ()
    provider_candidates: tuple[dict[str, Any], ...] = ()
    merge_groups: tuple[dict[str, Any], ...] = ()
    provider_batch_plans: tuple[ProviderBatchPlan, ...] = ()
    created_at: datetime


class WarehouseResult(BaseModel):
    satisfied: bool
    rows: tuple[dict[str, Any], ...] = ()
    dataset_refs: tuple[str, ...] = ()
    attempt_refs: tuple[str, ...] = ()
    attempt_refs_by_dataset_ref: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    gaps: tuple[DataGap, ...] = ()
    freshness: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> "WarehouseResult":
        if self.satisfied and not self.dataset_refs:
            raise ValueError("satisfied=True 时必须有 dataset_refs")
        return self

    @property
    def status(self) -> str:
        if self.satisfied:
            return "ready"
        if self.rows or self.dataset_refs:
            return "partial"
        return "missing"


class ResultRefs(BaseModel):
    dataset_refs: tuple[str, ...] = ()
    raw_refs: tuple[str, ...] = ()
    attempt_refs: tuple[str, ...] = ()


class GateDecision(BaseModel):
    kind: Literal["cache_hit", "cached_empty", "rate_limited", "shared_result", "cooldown_skipped", "owner"]
    refs: ResultRefs = Field(default_factory=ResultRefs)
    owner_token: str | None = None
    retry_after: datetime | None = None
    reason: str | None = None
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_gate(self) -> "GateDecision":
        if self.kind == "owner" and not self.owner_token:
            raise ValueError("owner gate 必须提供 owner_token")
        if self.evidence_refs and not self.refs.attempt_refs:
            self.refs = ResultRefs(
                dataset_refs=self.refs.dataset_refs,
                raw_refs=self.refs.raw_refs,
                attempt_refs=self.evidence_refs,
            )
        return self

    @classmethod
    def owner(cls, owner_token: str) -> "GateDecision":
        return cls(kind="owner", owner_token=owner_token)

    @classmethod
    def cache_hit(cls, refs: ResultRefs) -> "GateDecision":
        return cls(kind="cache_hit", refs=refs, evidence_refs=refs.attempt_refs)

    @classmethod
    def shared_result(cls, refs: ResultRefs) -> "GateDecision":
        return cls(kind="shared_result", refs=refs, evidence_refs=refs.attempt_refs)

    @classmethod
    def rate_limited(cls, retry_after: datetime | None, reason: str) -> "GateDecision":
        return cls(kind="rate_limited", retry_after=retry_after, reason=reason)

    @classmethod
    def cooldown_skipped(cls, retry_after: datetime | None, reason: str) -> "GateDecision":
        return cls(kind="cooldown_skipped", retry_after=retry_after, reason=reason)

    @classmethod
    def cached_empty(cls, refs: ResultRefs) -> "GateDecision":
        return cls(kind="cached_empty", refs=refs, evidence_refs=refs.attempt_refs)


def _coerce_gap_reason(reason: str | GapReason) -> GapReason:
    if isinstance(reason, GapReason):
        return reason
    if reason == "empty":
        return GapReason.EMPTY_RESULT
    return GapReason(str(reason))


def _coerce_market(raw: Any) -> Market:
    if isinstance(raw, Market):
        return raw
    if isinstance(raw, str):
        try:
            return Market(raw)
        except ValueError:
            return Market.CN_A
    return Market.CN_A


def _coerce_http_observations(raw: tuple[Any, ...]) -> tuple[HttpObservation, ...]:
    observations: list[HttpObservation] = []
    for item in raw:
        if isinstance(item, HttpObservation):
            observations.append(item)
            continue
        if isinstance(item, dict):
            observations.append(HttpObservation.model_validate(item))
            continue
        observations.append(
            HttpObservation(
                request_key=str(getattr(item, "request_key", "")),
                sent_at=getattr(item, "sent_at", None),
                status_code=getattr(item, "status_code", None),
                error_code=getattr(item, "error_code", None),
                elapsed_ms=getattr(item, "elapsed_ms", None),
            )
        )
    return tuple(observations)
