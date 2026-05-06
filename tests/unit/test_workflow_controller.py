from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.workflow.controller import ControllerInput, decide_next
from claw_trade.workflow.models import (
    DecisionKind,
    ExportResult,
    FailureRecord,
    RunRequest,
    RunStatus,
    Stage,
    StopPoint,
    WorkerResult,
    WorkerStatus,
    WorkflowState,
)
from claw_trade.workflow.runner import (
    first_human_action,
    group_failures_by_category,
    merge_stage_failures,
)
from claw_trade.workflow.workers import stage_plan


class ManifestView:
    def __init__(self, approved: set[tuple[str, Stage]] | None = None) -> None:
        self._approved = approved or set()

    def has_worker(self, worker_id: str, stage: Stage, run_id: str | None = None) -> bool:
        _ = run_id
        return (worker_id, stage) in self._approved


def test_created_wakes_frontline_full_stage(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.CREATED)
    decision = decide_next(make_input(state=state, manifest=ManifestView()))

    assert decision.kind == DecisionKind.WAKE_STAGE
    assert decision.next_status == RunStatus.FRONTLINE_RUNNING
    assert decision.batch is not None
    assert decision.batch.worker_ids == stage_plan(Stage.FRONTLINE).workers


def test_created_first_response_missing_target_is_blocked(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.CREATED,
        stop_point=StopPoint.FIRST_RESPONSE,
        target_worker_id=None,
    )

    decision = decide_next(make_input(state=state, manifest=ManifestView()))

    assert decision.kind == DecisionKind.BLOCKED
    assert decision.failure is not None
    assert decision.failure.category == "single_worker_target"


def test_created_single_worker_stage_mismatch_is_blocked(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.CREATED,
        stop_point=StopPoint.SINGLE_WORKER_COMPLETE,
        target_worker_id="trader",
        target_stage=Stage.FRONTLINE,
    )

    decision = decide_next(make_input(state=state, manifest=ManifestView()))

    assert decision.kind == DecisionKind.BLOCKED
    assert decision.failure is not None
    assert decision.failure.category == "single_worker_target"


def test_created_single_worker_only_wakes_target_worker(tmp_path: Path):
    approved = {
        ("bull_researcher", Stage.INVESTMENT_DEBATE),
        ("bear_researcher", Stage.INVESTMENT_DEBATE),
    }
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.CREATED,
        stop_point=StopPoint.SINGLE_WORKER_COMPLETE,
        target_worker_id="research_manager",
        target_stage=Stage.INVESTMENT_DECISION,
    )

    decision = decide_next(make_input(state=state, manifest=ManifestView(approved=approved)))

    assert decision.kind == DecisionKind.WAKE_STAGE
    assert decision.batch is not None
    assert decision.batch.worker_ids == ("research_manager",)
    assert decision.batch.stage == Stage.INVESTMENT_DECISION


def test_ready_stage_blocks_on_missing_upstream_material(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.FRONTLINE_READY)
    decision = decide_next(make_input(state=state, manifest=ManifestView()))

    assert decision.kind == DecisionKind.BLOCKED
    assert decision.failure is not None
    assert decision.failure.category == "artifact_flow"


def test_running_stage_waits_until_all_results_and_approvals(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.INVESTMENT_DEBATE_RUNNING)
    partial = (succeeded_result(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE),)

    wait_result = decide_next(make_input(state=state, manifest=ManifestView(), worker_results=partial))
    assert wait_result.kind == DecisionKind.WAIT

    finished = (
        succeeded_result(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE),
        succeeded_result(worker_id="bear_researcher", stage=Stage.INVESTMENT_DEBATE),
    )
    approved = {
        ("bull_researcher", Stage.INVESTMENT_DEBATE),
        ("bear_researcher", Stage.INVESTMENT_DEBATE),
    }
    advance = decide_next(make_input(state=state, manifest=ManifestView(approved=approved), worker_results=finished))

    assert advance.kind == DecisionKind.ADVANCE
    assert advance.next_status == RunStatus.INVESTMENT_DEBATE_READY


def test_running_stage_failure_merges_collect_first_failures(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.FRONTLINE_RUNNING)
    failed_one = failed_result(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        category="provider_request",
        reason="missing provider request",
        early_stop=False,
    )
    failed_two = failed_result(
        worker_id="news_analyst",
        stage=Stage.FRONTLINE,
        category="provider_request",
        reason="missing provider request again",
        early_stop=True,
    )

    decision = decide_next(
        make_input(
            state=state,
            manifest=ManifestView(),
            worker_results=(failed_one, failed_two),
        )
    )

    assert decision.kind == DecisionKind.FAIL
    assert decision.failure is not None
    assert decision.failure.category == "stage_batch"
    assert "provider_request" in decision.failure.reason
    assert decision.failure.early_stop is True


def test_first_response_running_only_waits_for_target_worker(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.FRONTLINE_RUNNING,
        stop_point=StopPoint.FIRST_RESPONSE,
        target_worker_id="news_analyst",
        target_stage=Stage.FRONTLINE,
    )
    other_worker_only = (
        succeeded_result(worker_id="market_analyst", stage=Stage.FRONTLINE),
    )

    waiting = decide_next(
        make_input(state=state, manifest=ManifestView(), worker_results=other_worker_only)
    )
    assert waiting.kind == DecisionKind.WAIT

    target_ready = (
        succeeded_result(worker_id="news_analyst", stage=Stage.FRONTLINE),
    )
    done = decide_next(
        make_input(state=state, manifest=ManifestView(), worker_results=target_ready)
    )
    assert done.kind == DecisionKind.COMPLETE
    assert done.next_status == RunStatus.COMPLETED


def test_single_worker_complete_requires_result_and_approved_material(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.TRADE_DECISION_RUNNING,
        stop_point=StopPoint.SINGLE_WORKER_COMPLETE,
        target_worker_id="trader",
        target_stage=Stage.TRADE_DECISION,
    )
    results = (succeeded_result(worker_id="trader", stage=Stage.TRADE_DECISION),)

    waiting = decide_next(make_input(state=state, manifest=ManifestView(), worker_results=results))
    assert waiting.kind == DecisionKind.WAIT

    approved = ManifestView(approved={("trader", Stage.TRADE_DECISION)})
    done = decide_next(make_input(state=state, manifest=approved, worker_results=results))

    assert done.kind == DecisionKind.COMPLETE
    assert done.next_status == RunStatus.COMPLETED


def test_portfolio_ready_exports_and_report_exporting_decisions(tmp_path: Path):
    state_ready = make_state(tmp_path=tmp_path, status=RunStatus.PORTFOLIO_DECISION_READY)
    approved = ManifestView(approved={("portfolio_manager", Stage.PORTFOLIO_DECISION)})
    export_decision = decide_next(make_input(state=state_ready, manifest=approved))

    assert export_decision.kind == DecisionKind.EXPORT_REPORT
    assert export_decision.next_status == RunStatus.REPORT_EXPORTING

    state_exporting = make_state(tmp_path=tmp_path, status=RunStatus.REPORT_EXPORTING)
    wait_decision = decide_next(make_input(state=state_exporting, manifest=approved, export_result=None))
    assert wait_decision.kind == DecisionKind.WAIT

    passed = ExportResult(
        run_id=state_exporting.run_id,
        status="passed",
        final_report_path=state_exporting.run_dir / "reports" / "final-report.md",
        export_guard_result_path=state_exporting.run_dir / "guards" / "export-guard.json",
        pm_owner_guard_result_path=None,
        unsupported_claims=(),
        failure=None,
    )
    complete = decide_next(make_input(state=state_exporting, manifest=approved, export_result=passed))
    assert complete.kind == DecisionKind.COMPLETE

    failed = ExportResult(
        run_id=state_exporting.run_id,
        status="failed",
        final_report_path=None,
        export_guard_result_path=None,
        pm_owner_guard_result_path=None,
        unsupported_claims=(),
        failure=FailureRecord(
            run_id=state_exporting.run_id,
            call_id=None,
            worker_id=None,
            stage=Stage.PORTFOLIO_DECISION,
            category="export_truthfulness",
            reason="unsupported claim",
            evidence_paths=(state_exporting.run_dir / "reports" / "export-result.json",),
            early_stop=True,
            human_action_required=None,
        ),
    )
    fail_decision = decide_next(make_input(state=state_exporting, manifest=approved, export_result=failed))
    assert fail_decision.kind == DecisionKind.FAIL
    assert fail_decision.failure is not None


def test_runner_failure_grouping_and_merge():
    f1 = FailureRecord(
        run_id="run-1",
        call_id="call-1",
        worker_id="news_analyst",
        stage=Stage.FRONTLINE,
        category="provider_request",
        reason="r1",
        evidence_paths=(Path("a.json"),),
        early_stop=False,
        human_action_required=None,
    )
    f2 = FailureRecord(
        run_id="run-1",
        call_id="call-2",
        worker_id="social_analyst",
        stage=Stage.FRONTLINE,
        category="provider_request",
        reason="r2",
        evidence_paths=(Path("b.json"),),
        early_stop=True,
        human_action_required="check provider capture",
    )
    grouped = group_failures_by_category((f1, f2))
    assert tuple(grouped) == ("provider_request",)
    assert first_human_action((f1, f2)) == "check provider capture"

    merged = merge_stage_failures("run-1", Stage.FRONTLINE, (f1, f2))
    assert merged.category == "stage_batch"
    assert merged.early_stop is True
    assert merged.human_action_required == "check provider capture"
    assert "provider_request" in merged.reason


@pytest.mark.parametrize(
    "status",
    (
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ),
)
def test_terminal_state_returns_wait(tmp_path: Path, status: RunStatus):
    decision = decide_next(make_input(state=make_state(tmp_path=tmp_path, status=status), manifest=ManifestView()))
    assert decision.kind == DecisionKind.WAIT


def make_input(
    state: WorkflowState,
    manifest: ManifestView,
    worker_results: tuple[WorkerResult, ...] = (),
    export_result: ExportResult | None = None,
) -> ControllerInput:
    return ControllerInput(
        state=state,
        worker_results=worker_results,
        manifest=manifest,  # type: ignore[arg-type]
        export_result=export_result,
        now="2026-05-04T10:00:00Z",
    )


def make_state(
    tmp_path: Path,
    status: RunStatus,
    stop_point: StopPoint = StopPoint.NONE,
    target_worker_id: str | None = None,
    target_stage: Stage | None = None,
) -> WorkflowState:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-01-01",
        end_date="2026-05-04",
        stop_point=stop_point,
        target_worker_id=target_worker_id,
        target_stage=target_stage,
    )
    return WorkflowState(
        run_id="run-1",
        request=request,
        status=status,
        run_dir=run_dir,
        openviking_namespace="workflow/run-1",
        created_at="2026-05-04T09:00:00Z",
        updated_at="2026-05-04T09:00:00Z",
    )


def succeeded_result(worker_id: str, stage: Stage) -> WorkerResult:
    return WorkerResult(
        run_id="run-1",
        call_id=f"call-{worker_id}",
        worker_id=worker_id,
        stage=stage,
        status=WorkerStatus.SUCCEEDED,
        openclaw_result_path=Path(f"runs/run-1/calls/call-{worker_id}/openclaw-result.json"),
        approved_material_id=None,
        failure=None,
    )


def failed_result(
    worker_id: str,
    stage: Stage,
    category: str,
    reason: str,
    early_stop: bool,
) -> WorkerResult:
    status = WorkerStatus.BLOCKED if category == "blocked" else WorkerStatus.FAILED
    return WorkerResult(
        run_id="run-1",
        call_id=f"call-{worker_id}",
        worker_id=worker_id,
        stage=stage,
        status=status,
        openclaw_result_path=None,
        approved_material_id=None,
        failure=FailureRecord(
            run_id="run-1",
            call_id=f"call-{worker_id}",
            worker_id=worker_id,
            stage=stage,
            category=category,
            reason=reason,
            evidence_paths=(Path(f"runs/run-1/calls/call-{worker_id}/failure.json"),),
            early_stop=early_stop,
            human_action_required=None,
        ),
    )
