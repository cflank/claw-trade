from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping, TypeAlias

from pydantic import BaseModel, Field, model_validator

from .models import GapReason, Market


ApiId: TypeAlias = str
_FORBIDDEN_CALL_SPEC_PARAM_KEYS = frozenset(
    {
        "provider",
        "path",
        "api_name",
        "url",
        "header",
        "header_name",
        "headers",
        "token",
        "api_key",
        "secret",
        "api_id",
        "data_type",
        "fields",
        "worker",
        "worker_id",
        "requested_by_worker",
        "consumer",
        "domain",
        "report_section",
        "allowed_worker",
        "allowed_domain",
        "allowed_report_section",
        "provider_scope",
    }
)


class NeedPriority(str, Enum):
    REQUIRED = "required"
    NORMAL = "normal"
    OPTIONAL = "optional"
    EXPENSIVE = "expensive"


class ExecutionGroupKind(str, Enum):
    FALLBACK_CHAIN = "fallback_chain"
    COMPOSITION_GROUP = "composition_group"


class NeedInstrument(BaseModel):
    symbol: str
    exchange: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None

    @model_validator(mode="after")
    def validate_instrument(self) -> "NeedInstrument":
        _require_non_empty("symbol", self.symbol)
        for field_name in ("exchange", "base_asset", "quote_asset"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty(field_name, value)
        return self


class DataNeed(BaseModel):
    need_id: str
    api_id: ApiId
    market: Market
    instrument: str
    time_range_start: date | datetime | None = None
    time_range_end: date | datetime | None = None
    granularity: str | None = None
    priority: NeedPriority = NeedPriority.NORMAL
    requested_by_worker: str
    purpose: str
    freshness_policy: str = "trading_day"
    deadline_at: datetime
    consumer: str = "report"

    @model_validator(mode="after")
    def validate_need(self) -> "DataNeed":
        for field_name in ("need_id", "api_id", "instrument", "requested_by_worker", "purpose"):
            _require_non_empty(field_name, getattr(self, field_name))
        for field_name in ("granularity", "freshness_policy", "consumer"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty(field_name, value)
        _require_timezone("deadline_at", self.deadline_at)
        _require_ordered_range(self.time_range_start, self.time_range_end)
        return self


class ProviderCallSpec(BaseModel):
    call_id: str
    method: str = "GET"
    public_api_id: str | None = None
    implementation_id: str | None = None
    business_api_id: str | None = None
    execution_group_id: str | None = None
    execution_group_kind: ExecutionGroupKind | str | None = None
    source_group_id: str | None = None
    fallback_order: int = 0
    component_id: str | None = None
    satisfaction_contract_id: str | None = None
    standard_output_contract_id: str | None = None
    provider_id: str
    catalog_endpoint_id: str
    official_path_or_api_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    auth_scope: str
    rate_limit_bucket: str
    http_visibility: str
    parser_status: str
    batch_key: str
    official_doc_ref: str
    deadline_at: datetime
    need_ids: tuple[str, ...]
    priority: NeedPriority = NeedPriority.NORMAL

    @model_validator(mode="after")
    def validate_call_spec(self) -> "ProviderCallSpec":
        for field_name in (
            "call_id",
            "method",
            "provider_id",
            "catalog_endpoint_id",
            "official_path_or_api_name",
            "auth_scope",
            "rate_limit_bucket",
            "http_visibility",
            "parser_status",
            "batch_key",
            "official_doc_ref",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        if not self.need_ids:
            raise ValueError("need_ids 不能为空")
        for need_id in self.need_ids:
            _require_non_empty("need_ids", need_id)
        for field_name in (
            "business_api_id",
            "execution_group_id",
            "execution_group_kind",
            "source_group_id",
            "component_id",
            "satisfaction_contract_id",
            "standard_output_contract_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty(field_name, str(getattr(value, "value", value)))
        if self.fallback_order < 0:
            raise ValueError("fallback_order 必须 >= 0")
        _require_timezone("deadline_at", self.deadline_at)
        forbidden_params = _forbidden_call_spec_param_keys(self.params)
        if forbidden_params:
            raise ValueError(f"ProviderCallSpec.params 含内部执行或业务范围字段: {', '.join(sorted(forbidden_params))}")
        return self


class ScheduledCall(BaseModel):
    call_id: str
    need_ids: tuple[str, ...]
    execution_group_id: str | None = None
    execution_group_kind: ExecutionGroupKind | str | None = None
    source_group_id: str | None = None
    fallback_order: int = 0
    component_id: str | None = None
    provider_id: str
    catalog_endpoint_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    batch_key: str
    rate_limit_bucket: str
    earliest_start_at: datetime
    rate_limit_reserved_at: datetime | None = None
    deadline_at: datetime
    priority: NeedPriority = NeedPriority.NORMAL

    @model_validator(mode="after")
    def validate_scheduled_call(self) -> "ScheduledCall":
        for field_name in (
            "call_id",
            "provider_id",
            "catalog_endpoint_id",
            "batch_key",
            "rate_limit_bucket",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        if not self.need_ids:
            raise ValueError("need_ids 不能为空")
        for need_id in self.need_ids:
            _require_non_empty("need_ids", need_id)
        for field_name in ("execution_group_id", "execution_group_kind", "source_group_id", "component_id"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty(field_name, str(getattr(value, "value", value)))
        if self.fallback_order < 0:
            raise ValueError("fallback_order 必须 >= 0")
        _require_timezone("earliest_start_at", self.earliest_start_at)
        if self.rate_limit_reserved_at is not None:
            _require_timezone("rate_limit_reserved_at", self.rate_limit_reserved_at)
        _require_timezone("deadline_at", self.deadline_at)
        if self.earliest_start_at > self.deadline_at:
            raise ValueError("earliest_start_at 不能晚于 deadline_at")
        return self


class DataNeedGap(BaseModel):
    need_id: str
    reason: GapReason
    evidence_refs: tuple[str, ...] = ()
    human_readable: str | None = None
    provider_ids_tried: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_gap(self) -> "DataNeedGap":
        if self.reason == GapReason.RATE_LIMITED_BY_TOOL_BUDGET and not self.evidence_refs:
            raise ValueError("rate_limited_by_tool_budget 必须提供 evidence_refs")
        return self


class MergeEvidence(BaseModel):
    batch_key: str
    need_ids: tuple[str, ...]
    call_id: str | None = None
    merged: bool
    reason: str | None = None


class RateLimitEvidence(BaseModel):
    rate_limit_bucket: str
    provider_id: str
    call_id: str | None = None
    reserved_at: datetime | None = None
    next_available_at: datetime | None = None
    deadline_at: datetime | None = None
    outcome: str
    evidence_refs: tuple[str, ...] = ()


class ExecutionGroup(BaseModel):
    group_id: str
    kind: ExecutionGroupKind
    business_api_id: str
    source_group_id: str | None = None
    planned_call_ids: tuple[str, ...] = ()
    initial_call_ids: tuple[str, ...] = ()
    deferred_call_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_execution_group(self) -> "ExecutionGroup":
        for field_name in ("group_id", "business_api_id"):
            _require_non_empty(field_name, getattr(self, field_name))
        if self.source_group_id is not None:
            _require_non_empty("source_group_id", self.source_group_id)
        if not self.planned_call_ids:
            raise ValueError("planned_call_ids 不能为空")
        return self


class AttemptState(BaseModel):
    plan_id: str
    execution_group_id: str
    call_id: str
    attempt_outcome: str
    contract_satisfied: bool
    satisfied_component_ids: tuple[str, ...] = ()
    missing_component_ids: tuple[str, ...] = ()
    gap_reason: GapReason | None = None

    @model_validator(mode="after")
    def validate_attempt_state(self) -> "AttemptState":
        for field_name in ("plan_id", "execution_group_id", "call_id", "attempt_outcome"):
            _require_non_empty(field_name, str(getattr(self, field_name)))
        return self


class NeedPlan(BaseModel):
    plan_id: str
    needs: tuple[DataNeed, ...] = ()
    execution_groups: tuple[ExecutionGroup, ...] = ()
    planned_calls: tuple[ProviderCallSpec, ...] = ()
    initial_scheduled_calls: tuple[ProviderCallSpec, ...] = ()
    scheduled_calls: tuple[ScheduledCall, ...] = ()
    deferred_calls: tuple[ProviderCallSpec, ...] = ()
    skipped_needs: tuple[DataNeedGap, ...] = ()
    merge_evidence: tuple[MergeEvidence, ...] = ()
    rate_limit_evidence: tuple[RateLimitEvidence, ...] = ()
    created_at: datetime


def _require_non_empty(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} 不能为空")


def _require_timezone(field_name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} 必须有 timezone")


def _require_ordered_range(start: date | datetime | None, end: date | datetime | None) -> None:
    if start is None or end is None:
        return
    if isinstance(start, datetime) and isinstance(end, datetime):
        if start > end:
            raise ValueError("time_range_start 不能晚于 time_range_end")
        return
    normalized_start = start.date() if isinstance(start, datetime) else start
    normalized_end = end.date() if isinstance(end, datetime) else end
    if normalized_start > normalized_end:
        raise ValueError("time_range_start 不能晚于 time_range_end")


def _forbidden_call_spec_param_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_CALL_SPEC_PARAM_KEYS or key_text.startswith(("allowed_", "only_for_")):
                found.add(key_text)
            found.update(_forbidden_call_spec_param_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_forbidden_call_spec_param_keys(child))
    return found


__all__ = [
    "AttemptState",
    "DataNeed",
    "DataNeedGap",
    "ExecutionGroup",
    "ExecutionGroupKind",
    "MergeEvidence",
    "NeedInstrument",
    "ApiId",
    "NeedPlan",
    "NeedPriority",
    "ProviderCallSpec",
    "RateLimitEvidence",
    "ScheduledCall",
]
