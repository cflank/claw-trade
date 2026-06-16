from __future__ import annotations

from pathlib import Path

import pytest
from claw_trade.selection.models import (
    CandidateFactRow,
    CandidateCacheManifest,
    CandidateCacheReadbackStatus,
    CandidateCacheRef,
    DataGapRef,
    DataGapSeverity,
    DecisionTicker,
    ReportHandoffRequest,
    SelectionBatchScope,
    SelectionConfirmation,
    SelectionConfirmationStatus,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionDecision,
    SelectionMarket,
    SelectionProfile,
    SelectionDataNeedAudit,
    SelectionRunPlan,
    SelectionStage,
    SelectionSystemContextPolicy,
    SelectionTriggerSource,
    SelectionWorkerArtifact,
    SelectionWorkerDispatch,
    SelectionWorkerId,
    SelectionWorkflowRun,
    SelectionWorkflowStatus,
    SelectRequest,
)
from claw_trade.workflow.models import RunRequest, WorkflowEntryPoint


def _request() -> SelectRequest:
    return SelectRequest(
        request_id="req-1",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        user_id="user-1",
        created_at="2026-05-26T12:00:00+00:00",
    )


def _cache_ref(run_id: str = "sel-run-1") -> CandidateCacheRef:
    return CandidateCacheRef(
        selection_run_id=run_id,
        material_id="mat-1",
        l1_uri="ov://selection/candidate-cache/1",
        content_sha256="a" * 64,
        manifest_ref="manifest://candidate-cache-1",
        approved_at="2026-05-26T12:00:00+00:00",
        expires_at="2026-05-27T12:00:00+00:00",
        cache_summary_ref="summary://candidate-cache-1",
    )


def _decision(run_id: str = "wf-1") -> SelectionDecision:
    return SelectionDecision(
        select_workflow_run_id=run_id,
        enter_report=(DecisionTicker(ticker="600519.SH", company_name="贵州茅台", rationale_excerpt="资源优先"),),
        watch=(DecisionTicker(ticker="000858.SZ", company_name="五粮液", rationale_excerpt="继续跟踪"),),
        reject=(),
        report_questions={"600519.SH": ("盈利持续性",)},
        source_summary={"market": "交易所行情，2026-05-26"},
        approved_material_id="mat-pm-1",
    )


def _report_request(entry_point: WorkflowEntryPoint = WorkflowEntryPoint.REPORT_COMMAND) -> RunRequest:
    return RunRequest(
        ticker="600519.SH",
        company_name="贵州茅台",
        market="CN_A",
        profile="CN_A",
        currency="CNY",
        currency_symbol="¥",
        current_date="2026-05-26",
        start_date="2025-05-26",
        end_date="2026-05-26",
        entry_point=entry_point,
    )


def test_select_request_requires_select_command_and_cn_a_only() -> None:
    req = _request()
    assert req.entry_point == WorkflowEntryPoint.SELECT_COMMAND
    assert req.system_context_policy == SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL

    with pytest.raises(ValueError, match="entry_point must be select_command"):
        SelectRequest(
            **{
                **req.__dict__,
                "entry_point": WorkflowEntryPoint.REPORT_COMMAND,
            }
        )

    with pytest.raises(ValueError, match="market must be SelectionMarket"):
        SelectRequest(
            **{
                **req.__dict__,
                "market": "US",
            }
        )


def test_selection_run_plan_validates_rerun_supersede_and_trade_date() -> None:
    SelectionRunPlan(
        selection_run_id="sel-run-1",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        data_need_audit_ref="plan://1",
        approved_strategy_config_ref="config://approved",
        trigger_source=SelectionTriggerSource.MANUAL_RERUN,
        supersedes_run_id="sel-run-0",
    )

    with pytest.raises(ValueError, match="supersedes_run_id is only valid"):
        SelectionRunPlan(
            selection_run_id="sel-run-2",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            lookback_trading_days=260,
            universe_scope="all_a_shares",
            data_need_audit_ref="plan://1",
            approved_strategy_config_ref="config://approved",
            trigger_source=SelectionTriggerSource.SCHEDULED,
            supersedes_run_id="sel-run-1",
        )


def test_candidate_cache_manifest_enforces_top20_and_verified_readback() -> None:
    CandidateCacheManifest(
        schema_version="v1",
        selection_run_id="sel-run-1",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        candidate_count=20,
        source_lineage_refs=("lineage://1",),
        cache_body_sha256="b" * 64,
        strategy_config_ref="config://approved",
        readback_status=CandidateCacheReadbackStatus.VERIFIED,
    )

    with pytest.raises(ValueError, match="candidate_count must be in \\[1, 20\\]"):
        CandidateCacheManifest(
            schema_version="v1",
            selection_run_id="sel-run-1",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            candidate_count=0,
            source_lineage_refs=("lineage://1",),
            cache_body_sha256="b" * 64,
            strategy_config_ref="config://approved",
            readback_status=CandidateCacheReadbackStatus.VERIFIED,
        )

    with pytest.raises(ValueError, match="candidate_count must be in \\[1, 20\\]"):
        CandidateCacheManifest(
            schema_version="v1",
            selection_run_id="sel-run-1",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            candidate_count=21,
            source_lineage_refs=("lineage://1",),
            cache_body_sha256="b" * 64,
            strategy_config_ref="config://approved",
            readback_status=CandidateCacheReadbackStatus.VERIFIED,
        )


def test_selection_data_need_audit_scope_is_selection_batch_only() -> None:
    SelectionDataNeedAudit(
        plan_id="plan-1",
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        coverage_groups=("market", "fundamental", "news"),
        ttl_policy_ref="ttl://policy-1",
        lineage_root_ref="lineage://root-1",
    )

    with pytest.raises(ValueError, match="scope must be SelectionBatchScope"):
        SelectionDataNeedAudit(
            plan_id="plan-2",
            scope="ticker_batch",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            lookback_trading_days=260,
            universe_scope="all_a_shares",
            coverage_groups=("market",),
            ttl_policy_ref="ttl://policy-1",
            lineage_root_ref="lineage://root-1",
        )


def test_selection_worker_dispatch_rejects_handoff_stage_and_missing_runtime_keys() -> None:
    with pytest.raises(ValueError, match="cannot be selection_report_handoff"):
        SelectionWorkerDispatch(
            dispatch_id="disp-1",
            select_workflow_run_id="wf-1",
            worker_id=SelectionWorkerId.STRATEGIST,
            stage=SelectionStage.SELECTION_REPORT_HANDOFF,
            allowed_tools=("claw_get_selection_candidate_cache",),
            prompt_runtime_vars={
                "market": "CN_A",
                "profile": "CN_A",
                "trade_date": "2026-05-26",
                "selection_run_id": "sel-run-1",
                "select_workflow_run_id": "wf-1",
            },
            model_visible_materials=("candidate_cache_summary",),
            evidence_dir=Path("runs/wf-1/calls/disp-1"),
        )

    with pytest.raises(ValueError, match="prompt_runtime_vars missing keys"):
        SelectionWorkerDispatch(
            dispatch_id="disp-2",
            select_workflow_run_id="wf-1",
            worker_id=SelectionWorkerId.STRATEGIST,
            stage=SelectionStage.SELECTION_REVIEW,
            allowed_tools=("claw_get_selection_candidate_cache",),
            prompt_runtime_vars={"market": "CN_A"},
            model_visible_materials=("candidate_cache_summary",),
            evidence_dir=Path("runs/wf-1/calls/disp-2"),
        )


def test_selection_data_run_requires_explicit_failed_and_completed_fields() -> None:
    SelectionDataRun(
        selection_run_id="sel-run-1",
        status=SelectionDataRunStatus.FAILED,
        failed_at="2026-05-26T13:00:00+00:00",
        failure_code="already_running",
        failure_reason="lease exists",
    )

    with pytest.raises(ValueError, match="failed status requires"):
        SelectionDataRun(
            selection_run_id="sel-run-2",
            status=SelectionDataRunStatus.FAILED,
            failed_at="2026-05-26T13:00:00+00:00",
            failure_code=None,
            failure_reason="missing code",
        )

    with pytest.raises(ValueError, match="candidate_cache_ref is required"):
        SelectionDataRun(
            selection_run_id="sel-run-3",
            status=SelectionDataRunStatus.COMPLETED,
            completed_at="2026-05-26T13:00:00+00:00",
        )

    SelectionDataRun(
        selection_run_id="sel-run-4",
        status=SelectionDataRunStatus.NO_CANDIDATE,
        completed_at="2026-05-26T13:00:00+00:00",
    )

    with pytest.raises(ValueError, match="no_candidate run cannot include candidate_cache_ref"):
        SelectionDataRun(
            selection_run_id="sel-run-5",
            status=SelectionDataRunStatus.NO_CANDIDATE,
            candidate_cache_ref=_cache_ref("sel-run-5"),
            completed_at="2026-05-26T13:00:00+00:00",
        )


def test_selection_decision_contract_limits_and_uniqueness() -> None:
    with pytest.raises(ValueError, match="0-3 tickers"):
        SelectionDecision(
            select_workflow_run_id="wf-1",
            enter_report=(
                DecisionTicker(ticker="000001.SZ", company_name="平安银行", rationale_excerpt="A"),
                DecisionTicker(ticker="000002.SZ", company_name="万科A", rationale_excerpt="B"),
                DecisionTicker(ticker="000333.SZ", company_name="美的集团", rationale_excerpt="C"),
                DecisionTicker(ticker="600519.SH", company_name="贵州茅台", rationale_excerpt="D"),
            ),
            watch=(),
            reject=(),
            report_questions=None,
            source_summary=None,
            approved_material_id="mat-pm",
        )

    with pytest.raises(ValueError, match="must be unique across enter/watch/reject"):
        SelectionDecision(
            select_workflow_run_id="wf-1",
            enter_report=(DecisionTicker(ticker="600519.SH", company_name="贵州茅台", rationale_excerpt="A"),),
            watch=(DecisionTicker(ticker="600519.SH", company_name="贵州茅台", rationale_excerpt="B"),),
            reject=(),
            report_questions=None,
            source_summary=None,
            approved_material_id="mat-pm",
        )


def test_selection_workflow_run_keeps_select_semantics_and_decision_requirement() -> None:
    request = _request()
    run = SelectionWorkflowRun(
        select_workflow_run_id="wf-1",
        selection_run_id="sel-run-1",
        status=SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION,
        request=request,
        candidate_cache_ref=_cache_ref(),
        decision=_decision(),
        created_at="2026-05-26T12:10:00+00:00",
        updated_at="2026-05-26T12:20:00+00:00",
    )
    assert run.request.entry_point == WorkflowEntryPoint.SELECT_COMMAND

    with pytest.raises(ValueError, match="decision is required"):
        SelectionWorkflowRun(
            select_workflow_run_id="wf-2",
            selection_run_id="sel-run-1",
            status=SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION,
            request=request,
            candidate_cache_ref=_cache_ref(),
            decision=None,
            created_at="2026-05-26T12:10:00+00:00",
            updated_at="2026-05-26T12:20:00+00:00",
        )

    with pytest.raises(ValueError, match="entry_point must be select_command"):
        SelectRequest(
            **{
                **request.__dict__,
                "entry_point": WorkflowEntryPoint.REPORT_COMMAND,
            }
        )


def test_selection_workflow_run_created_updated_are_required_and_non_empty() -> None:
    request = _request()
    with pytest.raises(TypeError, match="missing 2 required positional arguments"):
        SelectionWorkflowRun(
            select_workflow_run_id="wf-3",
            selection_run_id="sel-run-1",
            status=SelectionWorkflowStatus.RECEIVED,
            request=request,
            candidate_cache_ref=_cache_ref(),
        )

    with pytest.raises(ValueError, match="created_at must be non-empty"):
        SelectionWorkflowRun(
            select_workflow_run_id="wf-4",
            selection_run_id="sel-run-1",
            status=SelectionWorkflowStatus.RECEIVED,
            request=request,
            candidate_cache_ref=_cache_ref(),
            created_at="",
            updated_at="2026-05-26T12:20:00+00:00",
        )

    with pytest.raises(ValueError, match="updated_at must be non-empty"):
        SelectionWorkflowRun(
            select_workflow_run_id="wf-5",
            selection_run_id="sel-run-1",
            status=SelectionWorkflowStatus.RECEIVED,
            request=request,
            candidate_cache_ref=_cache_ref(),
            created_at="2026-05-26T12:10:00+00:00",
            updated_at="  ",
        )


def test_confirmation_and_report_handoff_contracts() -> None:
    SelectionConfirmation(
        confirmation_id="cfm-1",
        idempotency_key="wf-1:600519.SH:cfm-1",
        report_handoff_dedupe_key="wf-1:600519.SH",
        select_workflow_run_id="wf-1",
        ticker="600519.SH",
        status=SelectionConfirmationStatus.PENDING,
    )

    with pytest.raises(ValueError, match="must be selection_report_handoff"):
        ReportHandoffRequest(
            confirmation_id="cfm-1",
            ticker="600519.SH",
            company_name="贵州茅台",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            current_date="2026-05-26",
            selection_context_ref="selection://decision/wf-1",
            selection_stage_marker=SelectionStage.SELECTION_PORTFOLIO_DECISION,
            report_request=_report_request(),
        )

    with pytest.raises(ValueError, match="entry_point must be report_command"):
        ReportHandoffRequest(
            confirmation_id="cfm-1",
            ticker="600519.SH",
            company_name="贵州茅台",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            current_date="2026-05-26",
            selection_context_ref="selection://decision/wf-1",
            selection_stage_marker=SelectionStage.SELECTION_REPORT_HANDOFF,
            report_request=_report_request(entry_point=WorkflowEntryPoint.SELECT_COMMAND),
        )

    with pytest.raises(ValueError, match="must be RunRequest"):
        ReportHandoffRequest(
            confirmation_id="cfm-1",
            ticker="600519.SH",
            company_name="贵州茅台",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            current_date="2026-05-26",
            selection_context_ref="selection://decision/wf-1",
            selection_stage_marker=SelectionStage.SELECTION_REPORT_HANDOFF,
            report_request="report_command",  # type: ignore[arg-type]
        )


def test_candidate_and_gap_models_are_fact_only_contract_shells() -> None:
    row = CandidateFactRow(
        rank=1,
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="饮料",
        feature_values={"close": 1500.5, "vol_ratio": 1.8},
        strategy_hits=("turtle_trade",),
        risk_flags=("turnover_drop",),
        data_quality="行情完整，财报缺一项字段",
        source_summary="交易所行情+公开财报，日期 2026-05-26",
    )
    assert row.rank == 1

    gap = DataGapRef(
        gap_id="gap-1",
        domain="fundamental",
        gap_code="field_missing",
        severity=DataGapSeverity.WARN,
        attempt_refs=("attempt://1",),
        reader_message="财报字段缺失，已在候选摘要标注",
    )
    assert gap.severity == DataGapSeverity.WARN


def test_selection_worker_artifact_must_be_approved() -> None:
    SelectionWorkerArtifact(
        artifact_id="art-1",
        worker_id=SelectionWorkerId.STRATEGIST,
        stage=SelectionStage.SELECTION_REVIEW,
        l1_text="只包含研究结论正文",
        material_id="mat-1",
        l1_uri="ov://selection/l1/1",
        l1_sha256="c" * 64,
        approval_status="approved",
        provider_payload_ref="payload://1",
    )

    with pytest.raises(ValueError, match="approval_status must be approved"):
        SelectionWorkerArtifact(
            artifact_id="art-2",
            worker_id=SelectionWorkerId.STRATEGIST,
            stage=SelectionStage.SELECTION_REVIEW,
            l1_text="未批准",
            material_id="mat-2",
            l1_uri="ov://selection/l1/2",
            l1_sha256="d" * 64,
            approval_status="rejected",
            provider_payload_ref="payload://2",
        )
