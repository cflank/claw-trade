from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    DomainPack,
    DomainPackApprovalStatus,
    GapSeverity,
    HttpEvidence,
    Market,
    PackDomain,
    PrioritySource,
    PROVIDER_CALL_STATUSES,
    ProviderCallResult,
    ProviderCallSpec,
    ProviderKind,
    ProviderStatus,
    SourceRole,
)
from claw_trade.guards.common import EARLY_STOP_CATEGORIES


NOW = datetime(2026, 5, 29, 10, 0, tzinfo=UTC)

SECTION_18_STATUS_VALUES = {
    "remote_success",
    "remote_error",
    "empty",
    "field_missing",
    "credential_missing",
    "rate_limited",
    "cooldown_skipped",
    "cache_hit",
    "cached_empty",
    "cache_stale",
    "warehouse_hit",
    "shared_result",
    "not_applicable",
    "evidence_write_failed",
    "sdk_http_unknown",
}

GAP_REASON_BY_STATUS = {
    ProviderStatus.REMOTE_ERROR: DataGapReason.REMOTE_ERROR,
    ProviderStatus.EMPTY: DataGapReason.EMPTY,
    ProviderStatus.FIELD_MISSING: DataGapReason.FIELD_MISSING,
    ProviderStatus.CREDENTIAL_MISSING: DataGapReason.CREDENTIAL_MISSING,
    ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
    ProviderStatus.COOLDOWN_SKIPPED: DataGapReason.COOLDOWN_SKIPPED,
    ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
    ProviderStatus.CACHE_STALE: DataGapReason.STALE_CACHE_UNUSABLE,
    ProviderStatus.NOT_APPLICABLE: DataGapReason.NOT_APPLICABLE,
    ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
    ProviderStatus.SDK_HTTP_UNKNOWN: DataGapReason.SDK_HTTP_UNKNOWN,
}


def _spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key="CN_A:qfq_daily_bar:tushare:daily:000001.SZ",
        provider="tushare",
        adapter_id="project.tushare",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={"ticker": "000001.SZ"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="market.daily.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        spec_id="spec-1",
        requirement_id="req-market",
        data_type="qfq_daily_bar",
        priority_rank=0,
        rate_limit_key="rl:tushare:daily",
        cooldown_key="cd:tushare:daily",
        single_flight_key="sf:tushare:daily:000001.SZ",
        freshness_policy_id="trading_day",
        managed_http_required=True,
    )


def _gap(
    reason: DataGapReason,
    *,
    attempt_id: str = "attempt-1",
    evidence_refs: tuple[str, ...] = ("attempt://attempt-1",),
) -> DataGap:
    return DataGap(
        gap_id=f"gap-{reason.value}",
        domain=PackDomain.MARKET,
        severity=GapSeverity.BLOCKER,
        reason=reason,
        field_path="market.close",
        provider_candidates=("tushare",),
        attempt_ids=(attempt_id,),
        root_cause=f"{reason.value} prevents required market data",
        next_action="record the gap and use the next approved provider if any",
        requirement_id="req-market",
        market=Market.CN_A,
        data_type="qfq_daily_bar",
        ticker="000001.SZ",
        evidence_refs=evidence_refs,
        human_readable=f"{reason.value} prevents required market data.",
    )


def _result(status: ProviderStatus, **overrides: object) -> ProviderCallResult:
    raw_payload_ref = None
    normalized_ref = None
    rows: tuple[dict[str, float], ...] = ()
    http_evidence_refs: tuple[str, ...] = ()
    cache_entry_ref = None
    shared_owner_attempt_ref = None
    data_gaps: tuple[DataGap, ...] = ()

    if status == ProviderStatus.REMOTE_SUCCESS:
        rows = ({"close": 10.2},)
        raw_payload_ref = "raw://remote-success"
        normalized_ref = "normalized://remote-success"
        http_evidence_refs = ("http://remote-success",)
    elif status == ProviderStatus.CACHE_HIT:
        rows = ({"close": 10.2},)
        raw_payload_ref = "raw://cache-hit"
        normalized_ref = "normalized://cache-hit"
        cache_entry_ref = "cache://cache-hit"
    elif status == ProviderStatus.WAREHOUSE_HIT:
        rows = ({"close": 10.2},)
        raw_payload_ref = "mongo://openbb_raw_payloads/raw-warehouse"
        normalized_ref = "mongo://openbb_normalized/norm-warehouse"
    elif status == ProviderStatus.SHARED_RESULT:
        rows = ({"close": 10.2},)
        raw_payload_ref = "raw://owner"
        normalized_ref = "normalized://owner"
        shared_owner_attempt_ref = "attempt://owner"
    elif status in GAP_REASON_BY_STATUS:
        data_gaps = (_gap(GAP_REASON_BY_STATUS[status]),)
        if status == ProviderStatus.CACHED_EMPTY:
            cache_entry_ref = "cache://empty"

    kwargs = {
        "result_id": f"result-{status.value}",
        "spec": _spec(),
        "status": status,
        "rows": rows,
        "raw_payload_ref": raw_payload_ref,
        "normalized_ref": normalized_ref,
        "attempt_ref": f"attempt://{status.value}",
        "http_evidence_refs": http_evidence_refs,
        "cache_entry_ref": cache_entry_ref,
        "shared_owner_attempt_ref": shared_owner_attempt_ref,
        "data_gaps": data_gaps,
        "remote_success": status == ProviderStatus.REMOTE_SUCCESS,
        "created_at": NOW,
    }
    kwargs.update(overrides)
    return ProviderCallResult(**kwargs)


def test_section_18_status_table_is_explicitly_covered_by_provider_call_contract() -> None:
    assert {status.value for status in PROVIDER_CALL_STATUSES} == SECTION_18_STATUS_VALUES
    assert {status.value for status in GAP_REASON_BY_STATUS} == {
        "remote_error",
        "empty",
        "field_missing",
        "credential_missing",
        "rate_limited",
        "cooldown_skipped",
        "cached_empty",
        "cache_stale",
        "not_applicable",
        "evidence_write_failed",
        "sdk_http_unknown",
    }


def test_only_remote_success_can_be_marked_remote_success_and_must_have_refs() -> None:
    for status in PROVIDER_CALL_STATUSES:
        result = _result(status)
        assert result.remote_success is (status == ProviderStatus.REMOTE_SUCCESS)
        assert result.attempt_ref

        if status == ProviderStatus.REMOTE_SUCCESS:
            with pytest.raises(ValueError, match="remote_success"):
                _result(status, remote_success=False)
            continue

        with pytest.raises(ValueError, match="remote_success"):
            _result(status, remote_success=True)

    with pytest.raises(ValueError, match="raw_payload_ref and normalized_ref"):
        _result(ProviderStatus.REMOTE_SUCCESS, raw_payload_ref=None)
    with pytest.raises(ValueError, match="raw_payload_ref and normalized_ref"):
        _result(ProviderStatus.REMOTE_SUCCESS, normalized_ref=None)


def test_failures_require_attempts_and_gap_or_evidence_refs_where_applicable() -> None:
    for status in GAP_REASON_BY_STATUS:
        result = _result(status)
        assert result.attempt_ref
        assert result.data_gaps
        for gap in result.data_gaps:
            assert gap.attempt_ids
            assert gap.evidence_refs

        with pytest.raises(ValueError, match="requires attempt_ref"):
            _result(status, attempt_ref=None)


def test_cache_shared_and_throttle_states_cannot_be_disguised_as_remote_success() -> None:
    for status in (
        ProviderStatus.CACHE_HIT,
        ProviderStatus.WAREHOUSE_HIT,
        ProviderStatus.SHARED_RESULT,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.SDK_HTTP_UNKNOWN,
    ):
        assert _result(status).remote_success is False
        with pytest.raises(ValueError, match="remote_success"):
            _result(status, remote_success=True)


def test_capture_only_and_literal_completed_rows_do_not_create_success() -> None:
    with pytest.raises(ValueError, match="requires attempt_ref"):
        _result(ProviderStatus.REMOTE_SUCCESS, attempt_ref=None)
    with pytest.raises(ValueError, match="raw_payload_ref and normalized_ref"):
        _result(
            ProviderStatus.REMOTE_SUCCESS,
            raw_payload_ref=None,
            normalized_ref=None,
            http_evidence_refs=("http://capture-only",),
            rows=({"close": 10.2},),
        )
    with pytest.raises(ValueError, match="remote_success"):
        _result(
            ProviderStatus.CACHE_HIT,
            remote_success=True,
            rows=({"close": 10.2},),
            raw_payload_ref="raw://cache",
            normalized_ref="normalized://cache",
        )


def test_sdk_http_unknown_cannot_carry_forged_http_evidence() -> None:
    with pytest.raises(ValueError, match="must not fabricate http_evidence_refs"):
        _result(ProviderStatus.SDK_HTTP_UNKNOWN, http_evidence_refs=("http://forged",))

    with pytest.raises(ValueError, match="SDK internal unknown"):
        HttpEvidence(
            http_evidence_id="http-sdk-unknown",
            provider_id="sdk-provider",
            endpoint_id="daily",
            method="GET",
            host="provider.example",
            path="/daily",
            query_hash="sha256:query",
            body_hash=None,
            request_headers_redacted={},
            response_status=None,
            response_headers_redacted={},
            response_body_hash=None,
            elapsed_ms=None,
            quota_signal=None,
            cache_key="http:sdk-provider:daily",
            rate_limit_or_cooldown_recorded=False,
            sdk_internal_unknown=True,
        )


def test_evidence_write_failed_blocks_material_availability() -> None:
    gap = _gap(DataGapReason.EVIDENCE_WRITE_FAILED)
    _result(ProviderStatus.EVIDENCE_WRITE_FAILED, data_gaps=(gap,))

    with pytest.raises(ValueError, match="evidence_write_failed blocks approved DomainPack material"):
        DomainPack(
            pack_id="pack-market",
            run_id="run-1",
            domain=PackDomain.MARKET,
            market=Market.CN_A,
            ticker="000001.SZ",
            normalized_refs=("normalized://existing",),
            raw_payload_refs=(),
            attempt_refs=("attempt://attempt-1",),
            http_evidence_refs=(),
            data_gaps=(gap,),
            readable_markdown="Market data is unavailable because evidence writing failed.",
            source_summary="Evidence writing failed; no approved material is available.",
            approval_status=DomainPackApprovalStatus.APPROVED,
        )

    blocked = DomainPack(
        pack_id="pack-market-blocked",
        run_id="run-1",
        domain=PackDomain.MARKET,
        market=Market.CN_A,
        ticker="000001.SZ",
        normalized_refs=(),
        raw_payload_refs=(),
        attempt_refs=("attempt://attempt-1",),
        http_evidence_refs=(),
        data_gaps=(gap,),
        readable_markdown="Market data is unavailable because evidence writing failed.",
        source_summary="Evidence writing failed; material is blocked.",
        approval_status=DomainPackApprovalStatus.BLOCKED,
    )
    assert blocked.approval_status == DomainPackApprovalStatus.BLOCKED


def test_t12_keeps_runtime_guard_freeze_outside_data_layer_failure_states() -> None:
    guard_contract = Path("tests/contracts/test_guard_change_requires_approval.py").read_text(encoding="utf-8")
    assert "test_runtime_guard_file_set_is_frozen_without_human_approval" in guard_contract
    assert "test_early_stop_category_set_is_frozen_without_human_approval" in guard_contract
    assert "APPROVED_RUNTIME_GUARD_FILES" in guard_contract
    assert "APPROVED_EARLY_STOP_CATEGORIES" in guard_contract
    assert "fake_success_path" in EARLY_STOP_CATEGORIES
    assert SECTION_18_STATUS_VALUES.isdisjoint(EARLY_STOP_CATEGORIES)
    assert not tuple(Path("src/claw_trade/guards").glob("*data_layer*"))
