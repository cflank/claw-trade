from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PackStatus = Literal["ok", "partial", "failed"]
ProviderStatus = Literal["success", "empty", "error", "timeout", "skipped", "miss", "stale", "schema_invalid"]


@dataclass(frozen=True)
class DataPackRequest:
    ticker: str
    start_date: str | None
    end_date: str | None
    current_date: str
    run_id: str
    dispatch_id: str
    worker_id: Literal["fundamental_analyst"]
    market: Literal["CN_A"]


@dataclass(frozen=True)
class NormalizedInput:
    raw_ticker: str
    canonical_code: str
    tushare_code: str
    akshare_symbol: str
    exchange: Literal["SH", "SZ"]
    market: Literal["CN_A"]
    start_date: str | None
    end_date: str | None
    current_date: str
    run_id: str
    dispatch_id: str
    latest_report_period: str | None = None


@dataclass(frozen=True)
class ProviderAttempt:
    provider: Literal["mongodb", "tushare", "akshare"]
    role: Literal["cache", "primary", "supplement"]
    api_name: str | None
    attempt_seq: int | None
    status: ProviderStatus
    reason: str | None
    started_at: str
    ended_at: str
    duration_ms: int
    retry_count: int
    request_params_redacted: dict[str, str]
    response_row_count: int
    response_col_count: int
    field_coverage: list[str]
    report_period: str | None
    announce_date: str | None
    as_of: str | None
    fetched_at: str | None
    raw_payload_hash: str | None
    raw_payload_ref: str | None
    error_type: str | None
    error_message_redacted: str | None


@dataclass(frozen=True)
class ApiCallSpec:
    provider: Literal["tushare", "akshare"]
    api_name: str
    role: Literal["primary", "supplement"]
    required: bool
    field_family: str
    parameters: dict[str, str]
    timeout_ms: int
    retry_limit: int


@dataclass(frozen=True)
class ProviderResult:
    attempt: ProviderAttempt
    raw_payload_hash: str | None
    raw_payload_ref: str | None
    extracted_fields: list[tuple[str, Any, str]]
    schema_changed: bool
