from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from claw_trade.data_gateway.models import (
    CacheScope,
    ConsumerType,
    DataGap,
    DataGapReason,
    DataRequirement,
    DomainPack,
    DomainPackApprovalStatus,
    FreshnessDecision,
    FreshnessDecisionStatus,
    GapSeverity,
    HttpEvidence,
    HttpVisibility,
    Market,
    PackDomain,
    PayloadKind,
    PayloadLicenseMode,
    ProviderCallResult,
    ProviderCallSpec,
    ProviderDisplayDecision,
    ProviderDisplayStatus,
    ProviderKind,
    ProviderPriorityRule,
    ProviderStatus,
    RawPayloadRecord,
    NormalizedRecord,
    RequirementBatch,
    RequestKind,
    RequiredLevel,
    SelectDataPlan,
    SelectSupportStatus,
    SourceRole,
    PrioritySource,
    ReportDataPlan,
    CacheEntry,
    provider_status_is_remote_success,
)


NOW = datetime(2026, 5, 29, 10, 0, tzinfo=UTC)


def _requirement(*, request_kind: RequestKind = RequestKind.REPORT) -> DataRequirement:
    consumer_type = ConsumerType.REPORT_WORKER
    consumer_id = "market_analyst"
    if request_kind == RequestKind.SELECT:
        consumer_type = ConsumerType.SELECT_STRATEGY
        consumer_id = "sequoia_x"
    return DataRequirement(
        requirement_id=f"req-{request_kind.value}",
        market=Market.CN_A,
        data_type="qfq_daily_bar",
        granularity="daily",
        ticker="000001.SZ" if request_kind == RequestKind.REPORT else None,
        universe_ref=None if request_kind == RequestKind.REPORT else "universe://cn-a/all",
        date_range=(date(2026, 5, 1), date(2026, 5, 29)),
        lookback_window_days=29,
        current_date=date(2026, 5, 29),
        freshness_policy="trading_day",
        required_level=RequiredLevel.REQUIRED,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        domain=PackDomain.MARKET if request_kind == RequestKind.REPORT else PackDomain.SELECT_FEATURE,
        field_set=("close", "volume"),
    )


def _batch(request_kind: RequestKind) -> RequirementBatch:
    req = _requirement(request_kind=request_kind)
    return RequirementBatch(
        batch_id=f"batch-{request_kind.value}",
        request_id=f"request-{request_kind.value}",
        request_kind=request_kind,
        original_requirements=(req,),
        merged_requirements=(req,),
        duplicate_map={req.requirement_id: (req.requirement_id,)},
        created_at=NOW,
        profile="CN_A",
    )


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
        requirement_id="req-report",
        data_type="qfq_daily_bar",
        priority_rank=0,
        rate_limit_key="rl:tushare:daily",
        cooldown_key="cd:tushare:daily",
        single_flight_key="sf:tushare:daily:000001.SZ",
        freshness_policy_id="trading_day",
        managed_http_required=True,
    )


def _gap(reason: DataGapReason) -> DataGap:
    return DataGap(
        gap_id=f"gap-{reason.value}",
        domain=PackDomain.MARKET,
        severity=GapSeverity.BLOCKER,
        reason=reason,
        field_path="market.close",
        provider_candidates=("tushare",),
        attempt_ids=("attempt-1",),
        root_cause=f"{reason.value} means required market data is unavailable",
        next_action="record the gap and try the next approved provider",
        requirement_id="req-report",
        market=Market.CN_A,
        data_type="qfq_daily_bar",
        ticker="000001.SZ",
        evidence_refs=("attempt://attempt-1",),
        human_readable=f"{reason.value}：必需行情数据不可用。",
    )


def test_t1_model_symbols_and_failure_status_values_are_locked() -> None:
    expected = {
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
        "shared_result",
        "not_applicable",
        "evidence_write_failed",
        "sdk_http_unknown",
    }
    assert expected <= {status.value for status in ProviderStatus}
    assert DataRequirement
    assert RequirementBatch
    assert ProviderPriorityRule
    assert ProviderCallResult
    assert RawPayloadRecord
    assert NormalizedRecord
    assert FreshnessDecision
    assert DomainPack
    assert ReportDataPlan
    assert SelectDataPlan
    assert ProviderDisplayDecision


def test_data_requirement_requires_explicit_granularity() -> None:
    with pytest.raises(ValueError, match="granularity"):
        DataRequirement(
            requirement_id="req-missing-granularity",
            market=Market.CN_A,
            data_type="qfq_daily_bar",
            granularity="",
            ticker="000001.SZ",
            universe_ref=None,
            date_range=(date(2026, 5, 1), date(2026, 5, 29)),
            lookback_window_days=29,
            current_date=date(2026, 5, 29),
            freshness_policy="trading_day",
            required_level=RequiredLevel.REQUIRED,
            consumer_type=ConsumerType.REPORT_WORKER,
            consumer_id="market_analyst",
            domain=PackDomain.MARKET,
            field_set=("close", "volume"),
        )


def test_non_remote_call_statuses_cannot_be_marked_remote_success() -> None:
    gap_reasons = {
        ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
        ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
        ProviderStatus.COOLDOWN_SKIPPED: DataGapReason.COOLDOWN_SKIPPED,
        ProviderStatus.SDK_HTTP_UNKNOWN: DataGapReason.SDK_HTTP_UNKNOWN,
        ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
    }
    for status in (
        ProviderStatus.CACHE_HIT,
        ProviderStatus.SHARED_RESULT,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.SDK_HTTP_UNKNOWN,
        ProviderStatus.EVIDENCE_WRITE_FAILED,
    ):
        kwargs = {
            "result_id": f"result-{status.value}",
            "spec": _spec(),
            "status": status,
            "rows": (),
            "raw_payload_ref": None,
            "normalized_ref": None,
            "attempt_ref": f"attempt://{status.value}",
            "http_evidence_refs": (),
            "cache_entry_ref": "cache://1" if status == ProviderStatus.CACHE_HIT else None,
            "shared_owner_attempt_ref": "attempt://owner" if status == ProviderStatus.SHARED_RESULT else None,
            "data_gaps": (_gap(gap_reasons[status]),) if status in gap_reasons else (),
            "remote_success": False,
            "created_at": NOW,
        }
        result = ProviderCallResult(**kwargs)
        assert result.remote_success is False
        assert provider_status_is_remote_success(status) is False
        with pytest.raises(ValueError, match="remote_success"):
            ProviderCallResult(**{**kwargs, "remote_success": True})


def test_success_requires_raw_and_normalized_refs_and_failures_carry_gaps() -> None:
    success = ProviderCallResult(
        result_id="result-success",
        spec=_spec(),
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"close": 10.2},),
        raw_payload_ref="raw://attempt-1",
        normalized_ref="normalized://attempt-1",
        attempt_ref="attempt://attempt-1",
        http_evidence_refs=("http://evidence-1",),
        cache_entry_ref=None,
        shared_owner_attempt_ref=None,
        data_gaps=(),
        remote_success=True,
        created_at=NOW,
    )
    assert success.raw_payload_ref
    assert success.normalized_ref

    for status, reason in (
        (ProviderStatus.REMOTE_ERROR, DataGapReason.REMOTE_ERROR),
        (ProviderStatus.EMPTY, DataGapReason.EMPTY),
        (ProviderStatus.FIELD_MISSING, DataGapReason.FIELD_MISSING),
        (ProviderStatus.CREDENTIAL_MISSING, DataGapReason.CREDENTIAL_MISSING),
    ):
        with pytest.raises(ValueError, match="requires data_gaps"):
            ProviderCallResult(
                result_id=f"result-{status.value}",
                spec=_spec(),
                status=status,
                rows=(),
                raw_payload_ref=None,
                normalized_ref=None,
                attempt_ref=f"attempt://{status.value}",
                http_evidence_refs=(),
                cache_entry_ref=None,
                shared_owner_attempt_ref=None,
                data_gaps=(),
                remote_success=False,
                created_at=NOW,
        )
        assert _gap(reason).worker_visible_text


def test_provider_capability_t1_fields_are_semantic_not_presence_only() -> None:
    from claw_trade.data_gateway.models import ProviderCapability

    capability = ProviderCapability(
        provider="tushare",
        adapter_id="project.tushare",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        expected_schema_id="market.daily.v1",
        license_policy_id="personal_research",
        credential_requirements=("TUSHARE_TOKEN",),
        rate_limit_policy_id="default",
        cache_ttl_seconds=300,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        data_type="qfq_daily_bar",
        coverage_fields=("close", "volume"),
        coverage_symbols="configured_universe",
        freshness_supported=("trading_day",),
        user_config_key="TUSHARE_TOKEN",
        can_be_formal_fact_source=True,
        can_enter_worker_pack=True,
        http_visibility=HttpVisibility.MANAGED_HTTP,
        live_fresh_required_for_ui=True,
    )
    assert capability.provider_id == "tushare"
    assert capability.data_type == "qfq_daily_bar"
    assert capability.coverage_fields == ("close", "volume")
    assert capability.coverage_symbols == "configured_universe"
    assert capability.freshness_supported == ("trading_day",)
    assert capability.user_config_key == "TUSHARE_TOKEN"
    assert capability.can_be_formal_fact_source is True
    assert capability.can_enter_worker_pack is True
    assert capability.http_visibility == HttpVisibility.MANAGED_HTTP
    assert capability.live_fresh_required_for_ui is True

    with pytest.raises(ValueError, match="discovery-only provider"):
        ProviderCapability(
            **{
                **capability.__dict__,
                "provider": "google_news",
                "source_role": SourceRole.DISCOVERY,
                "can_be_formal_fact_source": True,
            }
        )

    with pytest.raises(ValueError, match="HttpVisibility"):
        ProviderCapability(
            **{
                **capability.__dict__,
                "http_visibility": "managed_http",
            }
        )


def test_sdk_http_unknown_cannot_create_http_evidence_record() -> None:
    with pytest.raises(ValueError, match="SDK internal unknown"):
        HttpEvidence(
            http_evidence_id="http-unknown",
            provider_id="sdk-provider",
            endpoint_id="sdk-call",
            method="GET",
            host="provider.example",
            path="/sdk/internal",
            query_hash="sha256:query",
            body_hash=None,
            request_headers_redacted={},
            response_status=None,
            response_headers_redacted={},
            response_body_hash=None,
            elapsed_ms=None,
            quota_signal=None,
            cache_key="http:sdk-provider",
            rate_limit_or_cooldown_recorded=False,
            sdk_internal_unknown=True,
        )


def test_evidence_records_keep_refs_hashes_and_worker_pack_keeps_natural_boundary() -> None:
    raw = RawPayloadRecord(
        raw_payload_id="raw-1",
        provider_id="tushare",
        endpoint_id="daily",
        requirement_id="req-report",
        attempt_id="attempt-1",
        payload_kind=PayloadKind.JSON,
        payload_ref="object://raw-1",
        payload_hash="sha256:abc",
        row_count=1,
        captured_at=NOW,
        license_mode=PayloadLicenseMode.METADATA_ONLY,
        manifest_hash="sha256:manifest",
    )
    normalized = NormalizedRecord(
        normalized_id="norm-1",
        market=Market.CN_A,
        data_type="qfq_daily_bar",
        ticker="000001.SZ",
        universe_ref=None,
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 29),
        rows_ref="openbb_normalized://norm-1",
        row_count=1,
        schema_version="market.daily.v1",
        source_provider_id="tushare",
        source_attempt_id="attempt-1",
        raw_payload_id=raw.raw_payload_id,
        quality_flags=(),
        created_at=NOW,
    )
    pack = DomainPack(
        pack_id="pack-1",
        run_id="run-1",
        domain=PackDomain.MARKET,
        market=Market.CN_A,
        ticker="000001.SZ",
        normalized_refs=(normalized.rows_ref,),
        raw_payload_refs=(raw.payload_ref,),
        attempt_refs=("attempt://attempt-1",),
        http_evidence_refs=("http://evidence-1",),
        data_gaps=(_gap(DataGapReason.CACHED_EMPTY),),
        readable_markdown="行情资料：远端数据已标准化，空缓存缺口单独列明。",
        source_summary="来源摘要：远端尝试、原始载荷和标准化记录都有审计引用。",
        approval_status=DomainPackApprovalStatus.APPROVED,
        material_ref="ov://material/pack-1",
    )
    worker_text = "\n".join(pack.worker_visible_material())
    assert "object://raw-1" not in worker_text
    assert "openbb_normalized://norm-1" not in worker_text
    assert "空缓存缺口" in worker_text


def test_report_select_and_ui_plan_models_preserve_control_boundaries() -> None:
    report_plan = ReportDataPlan(
        plan_id="report-plan-1",
        report_run_id="report-run-1",
        market=Market.CN_A,
        ticker="000001.SZ",
        company_name="平安银行",
        date_range=(date(2026, 5, 1), date(2026, 5, 29)),
        current_date=date(2026, 5, 29),
        profile="CN_A",
        workers=("market_analyst",),
        requirement_batch=_batch(RequestKind.REPORT),
        provider_call_specs=(_spec(),),
        domain_pack_ids=("pack-1",),
        data_gap_ids=(),
        created_at=NOW,
    )
    assert report_plan.requirement_batch.request_kind == RequestKind.REPORT

    freshness = FreshnessDecision(
        decision_id="freshness-1",
        requirement_id="req-select",
        status=FreshnessDecisionStatus.MISSING,
        mongo_query_ref="openbb_normalized://query/select-1",
        matched_normalized_refs=(),
        missing_fields=("close",),
        missing_symbols=("000001.SZ",),
        latest_data_time=None,
        required_data_time=date(2026, 5, 29),
        should_call_provider=True,
        data_gaps=(_gap(DataGapReason.MONGO_MISSING),),
    )
    select_plan = SelectDataPlan(
        plan_id="select-plan-1",
        select_run_id="select-run-1",
        market=Market.CN_A,
        universe_ref="universe://cn-a/all",
        strategy_refs=("strategy://sequoia-x",),
        current_date=date(2026, 5, 29),
        lookback_window_days=260,
        requirement_batch=_batch(RequestKind.SELECT),
        warehouse_checks=(freshness,),
        provider_call_specs=(_spec(),),
        feature_refs=("feature://select-run-1",),
        data_gap_ids=("gap-mongo_missing",),
        support_status=SelectSupportStatus.SUPPORTED,
    )
    assert select_plan.support_status == SelectSupportStatus.SUPPORTED

    cache = CacheEntry(
        cache_entry_id="cache-1",
        cache_scope=CacheScope.PROVIDER_CALL,
        cache_key="cache:tushare:daily",
        status=ProviderStatus.CACHED_EMPTY,
        created_at=NOW,
        expires_at=NOW,
        result_ref=None,
        raw_payload_ref=None,
        normalized_ref=None,
        http_evidence_ref="http://evidence-empty",
        empty_reason="provider returned no rows",
        stale_after=NOW,
    )
    assert cache.status == ProviderStatus.CACHED_EMPTY

    with pytest.raises(ValueError, match="real report/select chain evidence"):
        ProviderDisplayDecision(
            provider_id="probe-only",
            market=Market.CN_A,
            display_status=ProviderDisplayStatus.SHOW,
            reason="probe only",
            requires_user_credential=True,
            changes_report_or_select_result=False,
            writes_mongo_and_evidence=False,
            enters_domain_pack=False,
            consumed_by_worker_or_strategy=False,
            live_fresh_evidence_ref=None,
            probe_only=True,
        )
