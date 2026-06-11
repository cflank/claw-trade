from __future__ import annotations

from dataclasses import dataclass

from claw_trade.data_gateway.execution import NON_REMOTE_GATE_KINDS, GateDecision, ResultRefs
from claw_trade.data_gateway.execution.fetch_engine import FetchResult
from claw_trade.data_gateway.ingest import NON_REMOTE_ATTEMPT_STATUSES, DataGap
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.pipeline import gaps_from_fetch_result


@dataclass(frozen=True)
class _Batch:
    provider_id: str = "provider"
    endpoint_id: str = "endpoint"
    market: str = "CN_A"
    data_type: str = "daily_bar"
    granularity: str = "daily"
    request_ids: tuple[str, ...] = ("req-failure",)
    symbol_ids: tuple[str, ...] = ("600519.SH",)


def test_failure_reason_contract_is_explicit_and_complete() -> None:
    reasons = {
        "credential_missing",
        "rate_limited",
        "provider_error",
        "empty_result",
        "warehouse_missing",
        "warehouse_stale",
        "field_missing",
        "date_range_missing",
        "data_integrity_failed",
        "granularity_mismatch",
        "license_blocked",
        "evidence_write_failed",
        "cache_hit",
        "shared_result",
        "cached_empty",
        "cooldown_skipped",
        "not_applicable",
        "invalid_request",
        "sdk_http_unknown",
    }
    assert reasons == {
        DataGap.by_reason("credential_missing").reason,
        DataGap.by_reason("rate_limited", evidence_refs=("rate_limit:contract",)).reason,
        DataGap.by_reason("provider_error").reason,
        DataGap.by_reason("empty_result").reason,
        DataGap.by_reason("warehouse_missing").reason,
        DataGap.by_reason("warehouse_stale").reason,
        DataGap.by_reason("field_missing").reason,
        DataGap.by_reason("date_range_missing").reason,
        DataGap.by_reason("data_integrity_failed").reason,
        DataGap.by_reason("granularity_mismatch").reason,
        DataGap.by_reason("license_blocked").reason,
        DataGap.by_reason("evidence_write_failed").reason,
        DataGap.by_reason("cache_hit").reason,
        DataGap.by_reason("shared_result").reason,
        DataGap.by_reason("cached_empty").reason,
        DataGap.by_reason("cooldown_skipped", evidence_refs=("rate_limit:contract",)).reason,
        DataGap.by_reason("not_applicable").reason,
        DataGap.by_reason("invalid_request").reason,
        DataGap.by_reason("sdk_http_unknown").reason,
    }


def test_non_remote_statuses_are_never_remote_success() -> None:
    assert NON_REMOTE_GATE_KINDS == {"cache_hit", "shared_result", "rate_limited", "cached_empty", "cooldown_skipped"}
    assert NON_REMOTE_ATTEMPT_STATUSES == {
        "cache_hit",
        "shared_result",
        "rate_limited",
        "cached_empty",
        "cooldown_skipped",
        "credential_missing",
        "sdk_http_unknown",
    }

    attempt_log = AttemptLog()
    batch = _Batch()
    for status in sorted(NON_REMOTE_ATTEMPT_STATUSES):
        if status in NON_REMOTE_GATE_KINDS:
            gate = GateDecision(kind=status, refs=ResultRefs())
            try:
                attempt_log.record(batch=batch, gate=gate, remote_success=True)
                raise AssertionError(f"{status} accepted remote_success=True")
            except ValueError:
                pass


def test_fetch_failure_mapping_contains_required_reasons() -> None:
    batch = _Batch()
    assert [g.reason for g in gaps_from_fetch_result(FetchResult.from_error(batch, status="credential_missing"), batch)] == [
        "credential_missing"
    ]
    assert [g.reason for g in gaps_from_fetch_result(FetchResult.from_error(batch, status="rate_limited"), batch)] == [
        "rate_limited"
    ]
    assert [g.reason for g in gaps_from_fetch_result(FetchResult.from_empty(batch), batch)] == ["empty_result"]
    assert [g.reason for g in gaps_from_fetch_result(FetchResult.from_error(batch, status="not_applicable"), batch)] == [
        "not_applicable"
    ]
    assert [g.reason for g in gaps_from_fetch_result(FetchResult.from_error(batch, status="sdk_http_unknown"), batch)] == [
        "sdk_http_unknown",
        "provider_error",
    ]
    assert [g.reason for g in gaps_from_fetch_result(FetchResult.from_error(batch, status="error"), batch)] == ["provider_error"]


def test_fetch_failure_gaps_keep_request_context() -> None:
    batch = _Batch()
    gaps = gaps_from_fetch_result(FetchResult.from_error(batch, status="rate_limited"), batch)

    assert gaps[0].request_id == "req-failure"
    assert gaps[0].data_type == "daily_bar"
    assert gaps[0].granularity == "daily"
    assert gaps[0].symbol_id == "600519.SH"


def test_provider_error_gap_preserves_specific_error_message() -> None:
    batch = _Batch()
    gaps = gaps_from_fetch_result(
        FetchResult.from_error(batch, status="error", error=RuntimeError("connection_closed")),
        batch,
    )

    assert gaps[0].reason == "provider_error"
    assert gaps[0].human_readable == "connection_closed"
