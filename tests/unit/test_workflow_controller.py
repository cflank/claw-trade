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
    def __init__(
        self,
        approved: set[tuple[str, Stage]] | None = None,
        stage_turn_counts: dict[Stage, int] | None = None,
    ) -> None:
        self._approved = approved or set()
        self._stage_turn_counts = stage_turn_counts or {}

    def has_worker(self, worker_id: str, stage: Stage, run_id: str | None = None) -> bool:
        _ = run_id
        return (worker_id, stage) in self._approved

    def stage_turn_count(self, stage: Stage, run_id: str | None = None) -> int:
        _ = run_id
        if stage in self._stage_turn_counts:
            return self._stage_turn_counts[stage]
        return sum(1 for worker_id, approved_stage in self._approved if approved_stage == stage)


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


def test_frontline_ready_wakes_bull_first_without_extra_gate(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.FRONTLINE_READY)
    approved = ManifestView(
        approved={
            ("market_analyst", Stage.FRONTLINE),
            ("fundamental_analyst", Stage.FRONTLINE),
            ("news_analyst", Stage.FRONTLINE),
            ("social_analyst", Stage.FRONTLINE),
        }
    )

    decision = decide_next(make_input(state=state, manifest=approved))

    assert decision.kind == DecisionKind.WAKE_STAGE
    assert decision.next_status == RunStatus.INVESTMENT_DEBATE_RUNNING
    assert decision.stage == Stage.INVESTMENT_DEBATE
    assert decision.batch is not None
    assert decision.batch.worker_ids == ("bull_researcher",)
    assert decision.batch.collect_first is False


def test_investment_debate_ready_wakes_research_manager_after_bull_and_bear(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.INVESTMENT_DEBATE_READY)
    approved = ManifestView(
        approved={
            ("bull_researcher", Stage.INVESTMENT_DEBATE),
            ("bear_researcher", Stage.INVESTMENT_DEBATE),
        }
    )

    decision = decide_next(make_input(state=state, manifest=approved))

    assert decision.kind == DecisionKind.WAKE_STAGE
    assert decision.next_status == RunStatus.INVESTMENT_DECISION_RUNNING
    assert decision.stage == Stage.INVESTMENT_DECISION
    assert decision.batch is not None
    assert decision.batch.worker_ids == ("research_manager",)
    assert decision.batch.collect_first is False


def test_investment_debate_ready_stop_point_completes_without_research_manager(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.INVESTMENT_DEBATE_READY,
        stop_point=StopPoint.INVESTMENT_DEBATE_READY,
    )
    approved = ManifestView(
        approved={
            ("bull_researcher", Stage.INVESTMENT_DEBATE),
            ("bear_researcher", Stage.INVESTMENT_DEBATE),
        }
    )

    decision = decide_next(make_input(state=state, manifest=approved))

    assert decision.kind == DecisionKind.COMPLETE
    assert decision.next_status == RunStatus.COMPLETED


def test_trade_decision_ready_wakes_risk_challenger_first_without_collect_first(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.TRADE_DECISION_READY)
    approved = ManifestView(
        approved={
            ("trader", Stage.TRADE_DECISION),
        }
    )

    decision = decide_next(make_input(state=state, manifest=approved))

    assert decision.kind == DecisionKind.WAKE_STAGE
    assert decision.next_status == RunStatus.RISK_DEBATE_RUNNING
    assert decision.stage == Stage.RISK_DEBATE
    assert decision.batch is not None
    assert decision.batch.worker_ids == ("risk_challenger",)
    assert decision.batch.collect_first is False


def test_running_stage_waits_until_all_results_and_approvals(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.INVESTMENT_DEBATE_RUNNING)
    partial = (succeeded_result(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE),)

    wait_result = decide_next(make_input(state=state, manifest=ManifestView(), worker_results=partial))
    assert wait_result.kind == DecisionKind.WAIT

    bull_approved = ManifestView(approved={("bull_researcher", Stage.INVESTMENT_DEBATE)})
    wake_bear = decide_next(make_input(state=state, manifest=bull_approved, worker_results=partial))
    assert wake_bear.kind == DecisionKind.WAKE_STAGE
    assert wake_bear.batch is not None
    assert wake_bear.batch.worker_ids == ("bear_researcher",)

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


def test_investment_debate_runs_second_round_when_configured(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.INVESTMENT_DEBATE_RUNNING,
        max_debate_rounds=2,
    )
    first_round_results = (
        succeeded_result(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE, turn_index=0),
        succeeded_result(worker_id="bear_researcher", stage=Stage.INVESTMENT_DEBATE, turn_index=1),
    )
    first_round_approved = ManifestView(
        approved={
            ("bull_researcher", Stage.INVESTMENT_DEBATE),
            ("bear_researcher", Stage.INVESTMENT_DEBATE),
        },
        stage_turn_counts={Stage.INVESTMENT_DEBATE: 2},
    )

    wake_bull_round_2 = decide_next(
        make_input(state=state, manifest=first_round_approved, worker_results=first_round_results)
    )

    assert wake_bull_round_2.kind == DecisionKind.WAKE_STAGE
    assert wake_bull_round_2.batch is not None
    assert wake_bull_round_2.batch.worker_ids == ("bull_researcher",)
    assert wake_bull_round_2.batch.turn_index == 2
    assert wake_bull_round_2.batch.round_index == 2

    all_results = first_round_results + (
        succeeded_result(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE, turn_index=2),
        succeeded_result(worker_id="bear_researcher", stage=Stage.INVESTMENT_DEBATE, turn_index=3),
    )
    all_approved = ManifestView(
        approved={
            ("bull_researcher", Stage.INVESTMENT_DEBATE),
            ("bear_researcher", Stage.INVESTMENT_DEBATE),
        },
        stage_turn_counts={Stage.INVESTMENT_DEBATE: 4},
    )

    advance = decide_next(make_input(state=state, manifest=all_approved, worker_results=all_results))

    assert advance.kind == DecisionKind.ADVANCE
    assert advance.next_status == RunStatus.INVESTMENT_DEBATE_READY


def test_running_risk_debate_enforces_cn_serial_handoff(tmp_path: Path):
    state = make_state(tmp_path=tmp_path, status=RunStatus.RISK_DEBATE_RUNNING)
    risky_only = (succeeded_result(worker_id="risk_challenger", stage=Stage.RISK_DEBATE),)

    wait_risky_approved = decide_next(make_input(state=state, manifest=ManifestView(), worker_results=risky_only))
    assert wait_risky_approved.kind == DecisionKind.WAIT

    risky_approved = ManifestView(approved={("risk_challenger", Stage.RISK_DEBATE)})
    wake_safe = decide_next(make_input(state=state, manifest=risky_approved, worker_results=risky_only))
    assert wake_safe.kind == DecisionKind.WAKE_STAGE
    assert wake_safe.batch is not None
    assert wake_safe.batch.worker_ids == ("risk_guardian",)
    assert wake_safe.batch.collect_first is False

    risky_safe = (
        succeeded_result(worker_id="risk_challenger", stage=Stage.RISK_DEBATE),
        succeeded_result(worker_id="risk_guardian", stage=Stage.RISK_DEBATE),
    )
    risky_safe_approved = ManifestView(
        approved={
            ("risk_challenger", Stage.RISK_DEBATE),
            ("risk_guardian", Stage.RISK_DEBATE),
        }
    )
    wake_neutral = decide_next(make_input(state=state, manifest=risky_safe_approved, worker_results=risky_safe))
    assert wake_neutral.kind == DecisionKind.WAKE_STAGE
    assert wake_neutral.batch is not None
    assert wake_neutral.batch.worker_ids == ("risk_moderator",)
    assert wake_neutral.batch.collect_first is False

    all_three = (
        succeeded_result(worker_id="risk_challenger", stage=Stage.RISK_DEBATE),
        succeeded_result(worker_id="risk_guardian", stage=Stage.RISK_DEBATE),
        succeeded_result(worker_id="risk_moderator", stage=Stage.RISK_DEBATE),
    )
    all_approved = ManifestView(
        approved={
            ("risk_challenger", Stage.RISK_DEBATE),
            ("risk_guardian", Stage.RISK_DEBATE),
            ("risk_moderator", Stage.RISK_DEBATE),
        }
    )
    advance = decide_next(make_input(state=state, manifest=all_approved, worker_results=all_three))
    assert advance.kind == DecisionKind.ADVANCE
    assert advance.next_status == RunStatus.RISK_DEBATE_READY


def test_risk_debate_runs_second_round_when_configured(tmp_path: Path):
    state = make_state(
        tmp_path=tmp_path,
        status=RunStatus.RISK_DEBATE_RUNNING,
        max_risk_discuss_rounds=2,
    )
    first_round_results = (
        succeeded_result(worker_id="risk_challenger", stage=Stage.RISK_DEBATE, turn_index=0),
        succeeded_result(worker_id="risk_guardian", stage=Stage.RISK_DEBATE, turn_index=1),
        succeeded_result(worker_id="risk_moderator", stage=Stage.RISK_DEBATE, turn_index=2),
    )
    first_round_approved = ManifestView(
        approved={
            ("risk_challenger", Stage.RISK_DEBATE),
            ("risk_guardian", Stage.RISK_DEBATE),
            ("risk_moderator", Stage.RISK_DEBATE),
        },
        stage_turn_counts={Stage.RISK_DEBATE: 3},
    )

    wake_challenger_round_2 = decide_next(
        make_input(state=state, manifest=first_round_approved, worker_results=first_round_results)
    )

    assert wake_challenger_round_2.kind == DecisionKind.WAKE_STAGE
    assert wake_challenger_round_2.batch is not None
    assert wake_challenger_round_2.batch.worker_ids == ("risk_challenger",)
    assert wake_challenger_round_2.batch.turn_index == 3
    assert wake_challenger_round_2.batch.round_index == 2

    all_results = first_round_results + (
        succeeded_result(worker_id="risk_challenger", stage=Stage.RISK_DEBATE, turn_index=3),
        succeeded_result(worker_id="risk_guardian", stage=Stage.RISK_DEBATE, turn_index=4),
        succeeded_result(worker_id="risk_moderator", stage=Stage.RISK_DEBATE, turn_index=5),
    )
    all_approved = ManifestView(
        approved={
            ("risk_challenger", Stage.RISK_DEBATE),
            ("risk_guardian", Stage.RISK_DEBATE),
            ("risk_moderator", Stage.RISK_DEBATE),
        },
        stage_turn_counts={Stage.RISK_DEBATE: 6},
    )

    advance = decide_next(make_input(state=state, manifest=all_approved, worker_results=all_results))

    assert advance.kind == DecisionKind.ADVANCE
    assert advance.next_status == RunStatus.RISK_DEBATE_READY


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
    report_decision = decide_next(make_input(state=state_ready, manifest=approved))

    assert report_decision.kind == DecisionKind.WAKE_STAGE
    assert report_decision.next_status == RunStatus.FINAL_REPORT_RUNNING
    assert report_decision.stage == Stage.FINAL_REPORT
    assert report_decision.batch is not None
    assert report_decision.batch.worker_ids == ("report_polisher",)

    state_final_ready = make_state(tmp_path=tmp_path, status=RunStatus.FINAL_REPORT_READY)
    final_approved = ManifestView(approved={("report_polisher", Stage.FINAL_REPORT)})
    export_decision = decide_next(make_input(state=state_final_ready, manifest=final_approved))

    assert export_decision.kind == DecisionKind.EXPORT_REPORT
    assert export_decision.next_status == RunStatus.REPORT_EXPORTING

    state_exporting = make_state(tmp_path=tmp_path, status=RunStatus.REPORT_EXPORTING)
    wait_decision = decide_next(make_input(state=state_exporting, manifest=final_approved, export_result=None))
    assert wait_decision.kind == DecisionKind.WAIT

    passed = ExportResult(
        run_id=state_exporting.run_id,
        status="passed",
        final_report_path=state_exporting.run_dir / "reports" / "final-report.md",
        export_guard_result_path=state_exporting.run_dir / "guards" / "export-guard.json",
        unsupported_claims=(),
        failure=None,
    )
    complete = decide_next(make_input(state=state_exporting, manifest=final_approved, export_result=passed))
    assert complete.kind == DecisionKind.COMPLETE

    failed = ExportResult(
        run_id=state_exporting.run_id,
        status="failed",
        final_report_path=None,
        export_guard_result_path=None,
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
    fail_decision = decide_next(make_input(state=state_exporting, manifest=final_approved, export_result=failed))
    assert fail_decision.kind == DecisionKind.FAIL
    assert fail_decision.failure is not None

    state_exporting_cn = make_state(tmp_path=tmp_path, status=RunStatus.REPORT_EXPORTING, profile="CN_A")
    asset_failed = ExportResult(
        run_id=state_exporting_cn.run_id,
        status="failed",
        final_report_path=None,
        export_guard_result_path=None,
        unsupported_claims=(),
        failure=FailureRecord(
            run_id=state_exporting_cn.run_id,
            call_id=None,
            worker_id=None,
            stage=Stage.PORTFOLIO_DECISION,
            category="export_report_assets",
            reason="未找到可复制的图表资产",
            evidence_paths=(state_exporting_cn.run_dir / "reports" / "export-result.json",),
            early_stop=True,
            human_action_required=None,
        ),
    )
    complete_cn = decide_next(make_input(state=state_exporting_cn, manifest=final_approved, export_result=asset_failed))
    assert complete_cn.kind == DecisionKind.COMPLETE


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
    profile: str = "US",
    max_debate_rounds: int = 1,
    max_risk_discuss_rounds: int = 1,
) -> WorkflowState:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile=profile,
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-01-01",
        end_date="2026-05-04",
        stop_point=stop_point,
        target_worker_id=target_worker_id,
        target_stage=target_stage,
        max_debate_rounds=max_debate_rounds,
        max_risk_discuss_rounds=max_risk_discuss_rounds,
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


def succeeded_result(worker_id: str, stage: Stage, turn_index: int = 0) -> WorkerResult:
    return WorkerResult(
        run_id="run-1",
        call_id=f"call-{worker_id}-t{turn_index}",
        worker_id=worker_id,
        stage=stage,
        status=WorkerStatus.SUCCEEDED,
        openclaw_result_path=Path(f"runs/run-1/calls/call-{worker_id}-t{turn_index}/openclaw-result.json"),
        approved_material_id=None,
        failure=None,
        turn_index=turn_index,
        round_index=turn_index + 1,
        role_turn_index=turn_index + 1,
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
