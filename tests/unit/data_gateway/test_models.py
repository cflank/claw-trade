from __future__ import annotations

import pytest

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CacheReceipt,
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessPolicy,
    FreshnessStatus,
    GapSeverity,
    Market,
    NormalizedBundle,
    NormalizedResult,
    PackPlan,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    Readiness,
    ReadinessStatus,
    RunProviderPlan,
    SourceRole,
)


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="000001.SZ",
        company_name="平安银行",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key="market:tushare:daily",
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
    )


def _attempt(*, status: ProviderStatus, cache_status: ProviderStatus | None) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id="attempt-1",
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        pack="market",
        provider="tushare",
        adapter_id="project.tushare",
        adapter_kind="project_extension",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        started_at="2026-05-17T10:00:00+00:00",
        finished_at="2026-05-17T10:00:01+00:00",
        status=status,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=cache_status == ProviderStatus.CACHE_HIT,
        cache_status=cache_status,
        single_flight_role="none",
        shared_from_attempt_id=None,
        latency_ms=80,
        row_count=1,
        raw_ref="raw://attempt-1",
        normalized_ref="norm://attempt-1",
        error_code=None,
        error_message=None,
        schema_id="market.daily.v1",
        license_note="approved",
    )


def test_cache_status_cannot_be_remote_success() -> None:
    forbidden = (
        ProviderStatus.CACHE_HIT,
        ProviderStatus.CACHE_STALE,
        ProviderStatus.CACHE_ERROR,
        ProviderStatus.CACHED_EMPTY,
    )
    for cache_status in forbidden:
        with pytest.raises(ValueError):
            _attempt(status=ProviderStatus.REMOTE_SUCCESS, cache_status=cache_status)


def test_runtime_enum_values_must_use_contract_enums() -> None:
    with pytest.raises(ValueError):
        PackRequest(
            **{
                **_request().__dict__,
                "market": "CN_A",
            }
        )

    with pytest.raises(ValueError):
        CacheReceipt(
            cache_key="cache-1",
            provider="tushare",
            endpoint="daily",
            status="cache_hit",
            hit=True,
            stale=False,
            cached_empty=False,
            created_at="2026-05-17T10:00:00+00:00",
            expires_at="2026-05-17T10:05:00+00:00",
            ttl_seconds=300,
            evidence_hash="sha256:abc",
            raw_ref="raw://1",
            normalized_ref="norm://1",
        )

    with pytest.raises(ValueError):
        DataGap(
            gap_id="gap-1",
            domain=PackDomain.MARKET,
            severity=GapSeverity.FAIL,
            reason="cache_error",
            field_path="market.close",
            provider_candidates=("tushare",),
            attempt_ids=("attempt-1",),
            root_cause="cache error",
            next_action="retry remote provider",
        )


def test_run_provider_plan_cannot_enable_remote_prefetch() -> None:
    with pytest.raises(ValueError):
        RunProviderPlan(
            run_id="run-1",
            provider_config_version="cfg-v1",
            market=Market.CN_A,
            ticker="000001.SZ",
            domains=(PackDomain.MARKET,),
            call_specs=(_spec(),),
            shared_call_keys=(),
            cache_keys=("cache-1",),
            rate_limit_plan=(),
            initial_gaps=(),
            generated_at="2026-05-17T10:00:00+00:00",
            remote_prefetch_allowed=True,
        )


def test_normalized_bundle_and_pack_plan_contracts_exist() -> None:
    req = _request()
    spec = _spec()
    normalized = NormalizedResult(
        status=ProviderStatus.REMOTE_SUCCESS,
        schema_id="market.daily.v1",
        rows=({"close": 10.2},),
        compact_facts={"close": 10.2},
        row_count=1,
        field_units={"close": "CNY"},
        currency="CNY",
        timezone="Asia/Shanghai",
        source_raw_ref="raw://attempt-1",
    )
    bundle = NormalizedBundle(
        request=req,
        results=(normalized,),
        rows_by_schema={"market.daily.v1": normalized.rows},
        compact_facts=normalized.compact_facts,
        field_units=normalized.field_units,
        currencies=("CNY",),
        timezones=("Asia/Shanghai",),
        normalized_refs=("norm://attempt-1",),
        bundle_ref="bundle://1",
    )
    plan = PackPlan(request=req, call_specs=(spec,), initial_gaps=())

    assert bundle.rows_by_schema["market.daily.v1"][0]["close"] == 10.2
    assert plan.call_specs == (spec,)


def test_license_blocked_and_shared_result_field_closure() -> None:
    with pytest.raises(ValueError):
        ProviderAttempt(
            **{
                **_attempt(status=ProviderStatus.CACHE_MISS, cache_status=ProviderStatus.CACHE_MISS).__dict__,
                "status": ProviderStatus.LICENSE_BLOCKED,
                "license_note": "   ",
            }
        )

    with pytest.raises(ValueError):
        ProviderAttempt(
            **{
                **_attempt(status=ProviderStatus.CACHE_MISS, cache_status=ProviderStatus.CACHE_MISS).__dict__,
                "status": ProviderStatus.SHARED_RESULT,
                "single_flight_role": "consumer",
                "shared_from_attempt_id": None,
            }
        )

    ok_shared = ProviderAttempt(
        **{
            **_attempt(status=ProviderStatus.CACHE_MISS, cache_status=ProviderStatus.CACHE_MISS).__dict__,
            "status": ProviderStatus.SHARED_RESULT,
            "single_flight_role": "consumer",
            "shared_from_attempt_id": "attempt-owner",
            "raw_ref": "raw://shared",
            "normalized_ref": "norm://shared",
        }
    )
    assert ok_shared.status == ProviderStatus.SHARED_RESULT

    blocked_gap = DataGap(
        gap_id="gap-license",
        domain=PackDomain.MARKET,
        severity=GapSeverity.FAIL,
        reason=DataGapReason.LICENSE_BLOCKED,
        field_path="license",
        provider_candidates=("tushare",),
        attempt_ids=("attempt-1",),
        root_cause="license blocked",
        next_action="review license",
    )
    assert blocked_gap.reason == DataGapReason.LICENSE_BLOCKED


def test_reader_brief_is_worker_primary_material() -> None:
    req = _request()
    spec = _spec()
    attempt = _attempt(status=ProviderStatus.CACHE_MISS, cache_status=ProviderStatus.CACHE_MISS)
    cache = CacheReceipt(
        cache_key="cache-1",
        provider="tushare",
        endpoint="daily",
        status=ProviderStatus.CACHE_MISS,
        hit=False,
        stale=False,
        cached_empty=False,
        created_at=None,
        expires_at=None,
        ttl_seconds=300,
        evidence_hash=None,
        raw_ref=None,
        normalized_ref=None,
    )
    result = ProviderResult(
        spec=spec,
        status=ProviderStatus.CACHE_MISS,
        request_id=None,
        requested_at="2026-05-17T10:00:01+00:00",
        latency_ms=0,
        source_role=SourceRole.MARKET_DATA,
        freshness=FreshnessStatus.CACHE_UNUSABLE,
        license_note="ok",
        raw_ref=None,
        normalized_ref=None,
        rows=(),
        row_count=0,
        cache_receipt=cache,
        attempt=attempt,
    )
    readiness = Readiness(
        status=ReadinessStatus.PARTIAL,
        coverage={"market": "partial"},
        required_domains=("market",),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=("gap-1",),
        root_cause=None,
    )
    audit = PackAuditPayload(
        request=req,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="ext-v1",
        run_provider_plan_id="plan-1",
        call_specs=(spec,),
        attempts=(attempt,),
        cache_receipts=(cache,),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=("raw://attempt-1",),
        normalized_refs=("norm://attempt-1",),
        normalized_bundle_ref="bundle://1",
        payload_hash="sha256:abc",
        generated_at="2026-05-17T10:00:02+00:00",
    )
    pack = DomainPackResult(
        request=req,
        reader_brief_md="市场材料正文：成交与均线仍可用，衍生品数据缺失。",
        compact_facts={"close": 10.2},
        attempts=(result.attempt,),
        cache_receipts=(cache,),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=("raw://attempt-1",),
        normalized_refs=("norm://attempt-1",),
        normalized_bundle_ref="bundle://1",
        audit_ref="audit://pack-1",
        audit_payload_hash="sha256:payload",
        audit_payload=audit,
    )
    assert pack.worker_primary_material_md == pack.reader_brief_md
    assert "openbb_runtime_marker" not in pack.worker_primary_material_md
