from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, TypeAlias

from pydantic import BaseModel, Field, model_validator

from .models import GapReason, Market


NeedKind: TypeAlias = str


class NeedPriority(str, Enum):
    REQUIRED = "required"
    NORMAL = "normal"
    OPTIONAL = "optional"
    EXPENSIVE = "expensive"


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
    need_kind: NeedKind
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
        for field_name in ("need_id", "need_kind", "instrument", "requested_by_worker", "purpose"):
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
        _require_timezone("deadline_at", self.deadline_at)
        return self


class ScheduledCall(BaseModel):
    call_id: str
    need_ids: tuple[str, ...]
    provider_id: str
    catalog_endpoint_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    batch_key: str
    rate_limit_bucket: str
    earliest_start_at: datetime
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
        _require_timezone("earliest_start_at", self.earliest_start_at)
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


class NeedPlan(BaseModel):
    plan_id: str
    needs: tuple[DataNeed, ...] = ()
    planned_calls: tuple[ProviderCallSpec, ...] = ()
    scheduled_calls: tuple[ScheduledCall, ...] = ()
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


__all__ = [
    "DataNeed",
    "DataNeedGap",
    "MergeEvidence",
    "NeedInstrument",
    "NeedKind",
    "NeedPlan",
    "NeedPriority",
    "ProviderCallSpec",
    "RateLimitEvidence",
    "ScheduledCall",
]
