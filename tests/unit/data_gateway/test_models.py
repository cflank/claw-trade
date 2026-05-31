from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CacheReceipt,
    ConsumerType,
    DataRequirement,
    DataGap,
    DataGapReason,
    DomainPack,
    DomainPackApprovalStatus,
    DomainPackResult,
    FreshnessPolicy,
    FreshnessStatus,
    GapSeverity,
    HttpEvidence,
    HttpVisibility,
    Market,
    NormalizedBundle,
    NormalizedResult,
    PackPlan,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderCallResult,
    ProviderAttempt,
    ProviderCapability,
    ProviderCallSpec,
    ProviderDisplayDecision,
    ProviderDisplayStatus,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    RequiredLevel,
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


def _requirement() -> DataRequirement:
    return DataRequirement(
        requirement_id="req-market-1",
        market=Market.CN_A,
        data_type="qfq_daily_bar",
        granularity="daily",
        ticker="000001.SZ",
        universe_ref=None,
        date_range=(date(2026, 5, 1), date(2026, 5, 17)),
        lookback_window_days=17,
        current_date=date(2026, 5, 17),
        freshness_policy="trading_day",
        required_level=RequiredLevel.REQUIRED,
        consumer_type=ConsumerType.REPORT_WORKER,
        consumer_id="market_analyst",
        domain=PackDomain.MARKET,
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("close", "volume"),
        allow_search_discovery=False,
    )


def _gap(reason: DataGapReason = DataGapReason.RATE_LIMITED) -> DataGap:
    return DataGap(
        gap_id=f"gap-{reason.value}",
        domain=PackDomain.MARKET,
        severity=GapSeverity.BLOCKER,
        reason=reason,
        field_path="market.close",
        provider_candidates=("tushare",),
        attempt_ids=("attempt-1",),
        root_cause=f"{reason.value} blocked required market data",
        next_action="try next approved provider",
        requirement_id="req-market-1",
        market=Market.CN_A,
        data_type="qfq_daily_bar",
        ticker="000001.SZ",
        evidence_refs=("attempt://attempt-1",),
        human_readable=f"{reason.value}：未取得必需行情数据。",
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


def test_provider_call_result_failure_statuses_do_not_become_remote_success() -> None:
    gap_by_status = {
        ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
        ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
        ProviderStatus.COOLDOWN_SKIPPED: DataGapReason.COOLDOWN_SKIPPED,
        ProviderStatus.SDK_HTTP_UNKNOWN: DataGapReason.SDK_HTTP_UNKNOWN,
        ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
    }
    valid_extra = {
        ProviderStatus.CACHE_HIT: {"cache_entry_ref": "cache://hit-1"},
        ProviderStatus.SHARED_RESULT: {"shared_owner_attempt_ref": "attempt://owner-1"},
    }

    statuses = (
        ProviderStatus.CACHE_HIT,
        ProviderStatus.SHARED_RESULT,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.SDK_HTTP_UNKNOWN,
        ProviderStatus.EVIDENCE_WRITE_FAILED,
    )
    for status in statuses:
        data_gaps = ()
        if status in gap_by_status:
            data_gaps = (_gap(gap_by_status[status]),)
        kwargs = {
            "result_id": f"result-{status.value}",
            "spec": _spec(),
            "status": status,
            "rows": ({"close": 10.2},) if status in {ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT} else (),
            "raw_payload_ref": "raw://cached" if status == ProviderStatus.CACHE_HIT else None,
            "normalized_ref": "norm://cached" if status in {ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT} else None,
            "attempt_ref": f"attempt://{status.value}",
            "http_evidence_refs": (),
            "cache_entry_ref": None,
            "shared_owner_attempt_ref": None,
            "data_gaps": data_gaps,
            "remote_success": False,
            "created_at": datetime(2026, 5, 17, 10, 0, tzinfo=UTC),
        }
        kwargs.update(valid_extra.get(status, {}))
        ok = ProviderCallResult(**kwargs)
        assert ok.remote_success is False

        with pytest.raises(ValueError, match="remote_success"):
            ProviderCallResult(
                **{
                    **ok.__dict__,
                    "remote_success": True,
                }
            )


def test_provider_call_result_remote_success_requires_raw_and_normalized_refs() -> None:
    base = {
        "result_id": "result-success",
        "spec": _spec(),
        "status": ProviderStatus.REMOTE_SUCCESS,
        "rows": ({"close": 10.2},),
        "attempt_ref": "attempt://success-1",
        "http_evidence_refs": ("http://1",),
        "cache_entry_ref": None,
        "shared_owner_attempt_ref": None,
        "data_gaps": (),
        "remote_success": True,
        "created_at": datetime(2026, 5, 17, 10, 0, tzinfo=UTC),
    }
    with pytest.raises(ValueError, match="raw_payload_ref and normalized_ref"):
        ProviderCallResult(**{**base, "raw_payload_ref": None, "normalized_ref": "norm://1"})
    with pytest.raises(ValueError, match="raw_payload_ref and normalized_ref"):
        ProviderCallResult(**{**base, "raw_payload_ref": "raw://1", "normalized_ref": None})

    ok = ProviderCallResult(**{**base, "raw_payload_ref": "raw://1", "normalized_ref": "norm://1"})
    assert ok.remote_success is True


def test_empty_field_missing_and_credential_missing_require_data_gap() -> None:
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
                created_at=datetime(2026, 5, 17, 10, 0, tzinfo=UTC),
            )

        result = ProviderCallResult(
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
            data_gaps=(_gap(reason),),
            remote_success=False,
            created_at=datetime(2026, 5, 17, 10, 0, tzinfo=UTC),
        )
        assert result.data_gaps[0].human_readable


def test_provider_capability_t1_fields_and_discovery_boundaries() -> None:
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


def test_sdk_http_unknown_rejects_fake_http_evidence() -> None:
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


def test_domain_pack_worker_material_excludes_internal_refs() -> None:
    pack = DomainPack(
        pack_id="pack-1",
        run_id="run-1",
        domain=PackDomain.MARKET,
        market=Market.CN_A,
        ticker="000001.SZ",
        normalized_refs=("norm://1",),
        raw_payload_refs=("raw://1",),
        attempt_refs=("attempt://1",),
        http_evidence_refs=("http://1",),
        data_gaps=(_gap(DataGapReason.CACHED_EMPTY),),
        readable_markdown="行情资料：收盘价和成交量已取得，部分缓存空结果已列为缺口。",
        source_summary="来源摘要：Tushare 远端证据和本地标准化记录已可追踪。",
        approval_status=DomainPackApprovalStatus.APPROVED,
        material_ref="ov://material/pack-1",
    )
    worker_material = "\n".join(pack.worker_visible_material())
    assert "norm://1" not in worker_material
    assert "attempt://1" not in worker_material
    assert "cached_empty" in worker_material

    with pytest.raises(ValueError, match="internal refs"):
        DomainPack(
            **{
                **pack.__dict__,
                "readable_markdown": "raw_payload_ref=raw://1",
            }
        )


def test_provider_display_show_requires_real_chain_evidence() -> None:
    with pytest.raises(ValueError, match="real report/select chain evidence"):
        ProviderDisplayDecision(
            provider_id="unused-paid-source",
            market=Market.CN_A,
            display_status=ProviderDisplayStatus.SHOW,
            reason="probe succeeded only",
            requires_user_credential=True,
            changes_report_or_select_result=False,
            writes_mongo_and_evidence=False,
            enters_domain_pack=False,
            consumed_by_worker_or_strategy=False,
            live_fresh_evidence_ref=None,
            probe_only=True,
        )

    decision = ProviderDisplayDecision(
        provider_id="tushare",
        market=Market.CN_A,
        display_status=ProviderDisplayStatus.SHOW,
        reason="configured source changes report data and has live evidence",
        requires_user_credential=True,
        changes_report_or_select_result=True,
        writes_mongo_and_evidence=True,
        enters_domain_pack=True,
        consumed_by_worker_or_strategy=True,
        live_fresh_evidence_ref="evidence://live/tushare-1",
        probe_only=False,
    )
    assert decision.display_status == ProviderDisplayStatus.SHOW


def test_cn_a_only_domains_reject_non_cn_a_market() -> None:
    with pytest.raises(ValueError, match="only approved for CN_A"):
        PackRequest(
            **{
                **_request().__dict__,
                "market": Market.US,
                "domain": PackDomain.POLICY,
            }
        )

    with pytest.raises(ValueError, match="only approved for CN_A"):
        ProviderCallSpec(
            **{
                **_spec().__dict__,
                "market": Market.HK,
                "domain": PackDomain.HOT_MONEY,
            }
        )

    with pytest.raises(ValueError, match="only approved for CN_A"):
        ProviderCapability(
            provider="akshare",
            adapter_id="project.akshare.policy",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.US,
            domain=PackDomain.POLICY,
            endpoint="policy_events",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            expected_schema_id="policy.events.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="default",
            cache_ttl_seconds=300,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_policy_official",
            coverage_quorum=1,
            priority=0,
            priority_source=PrioritySource.SYSTEM_DEFAULT,
        )

    with pytest.raises(ValueError, match="only approved for CN_A"):
        RunProviderPlan(
            run_id="run-1",
            provider_config_version="cfg-v1",
            market=Market.US,
            ticker="AAPL",
            domains=(PackDomain.LOCKUP,),
            call_specs=(),
            shared_call_keys=(),
            cache_keys=(),
            rate_limit_plan=(),
            initial_gaps=(),
            generated_at="2026-05-17T10:00:00+00:00",
            remote_prefetch_allowed=False,
        )


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

    with pytest.raises(ValueError):
        DataGap(
            gap_id="gap-2",
            domain=PackDomain.MARKET,
            severity=GapSeverity.FAIL,
            reason=ProviderStatus.CACHE_ERROR,
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

    official_original_attempt = ProviderAttempt(
        **{
            **_attempt(status=ProviderStatus.CACHE_MISS, cache_status=ProviderStatus.CACHE_MISS).__dict__,
            "source_role": SourceRole.OFFICIAL_ORIGINAL,
            "coverage_group": "cn_a_news_announcement",
            "required": True,
            "attempt_required": True,
        }
    )
    assert official_original_attempt.source_role == SourceRole.OFFICIAL_ORIGINAL


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

    with pytest.raises(ValueError):
        ProviderResult(
            **{
                **result.__dict__,
                "status": ProviderStatus.REMOTE_SUCCESS,
                "cache_receipt": CacheReceipt(
                    cache_key="cache-1",
                    provider="tushare",
                    endpoint="daily",
                    status=ProviderStatus.CACHE_HIT,
                    hit=True,
                    stale=False,
                    cached_empty=False,
                    created_at="2026-05-17T10:00:00+00:00",
                    expires_at="2026-05-17T10:05:00+00:00",
                    ttl_seconds=300,
                    evidence_hash="sha256:abc",
                    raw_ref="raw://attempt-1",
                    normalized_ref="norm://attempt-1",
                ),
            }
        )
