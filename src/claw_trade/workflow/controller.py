from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed, should_early_stop
from claw_trade.workflow.models import (
    BatchScope,
    Decision,
    DecisionKind,
    ExportResult,
    FailureRecord,
    RunStatus,
    Stage,
    StageBatch,
    StagePlan,
    StopPoint,
    WorkerResult,
    WorkerStatus,
    WorkflowState,
    export_result_allows_workflow_completion,
    is_terminal_status,
)
from claw_trade.workflow.workers import (
    is_ready_status,
    is_running_status,
    stage_for_ready_status,
    stage_for_running_status,
    stage_plan,
    worker_by_id_or_none,
)


@dataclass(frozen=True)
class ControllerInput:
    state: WorkflowState
    worker_results: tuple[WorkerResult, ...]
    manifest: ApprovedManifest
    export_result: ExportResult | None
    now: str


@dataclass(frozen=True)
class TargetWorkerResult:
    ok: bool
    worker_id: str | None
    stage: Stage | None
    failure: FailureRecord | None

    @classmethod
    def failed(cls, state: WorkflowState, category: str, reason: str) -> TargetWorkerResult:
        return cls(
            ok=False,
            worker_id=None,
            stage=None,
            failure=FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=state.request.target_worker_id,
                stage=state.request.target_stage,
                category=category,
                reason=reason,
                evidence_paths=(state.run_dir / "request.json",),
                early_stop=True,
                human_action_required=None,
            ),
        )


def decide_next(input: ControllerInput) -> Decision:
    state = input.state
    # 状态机权威边界：controller 只根据当前状态与已批准材料决定下一步，不让 LLM 决定调度。
    if is_terminal_status(state.status):
        return Decision(DecisionKind.WAIT, reason="运行已经终止")

    if state.status == RunStatus.CREATED:
        if state.request.stop_point in (StopPoint.FIRST_RESPONSE, StopPoint.SINGLE_WORKER_COMPLETE):
            return decide_wake_single_worker(state=state, manifest=input.manifest)
        return decide_wake_stage(state=state, stage=Stage.FRONTLINE, manifest=input.manifest)

    if is_running_status(state.status):
        return decide_running_stage(
            state=state,
            stage=stage_for_running_status(state.status),
            results=input.worker_results,
            manifest=input.manifest,
        )

    if is_ready_status(state.status):
        return decide_ready_stage(
            state=state,
            stage=stage_for_ready_status(state.status),
            manifest=input.manifest,
        )

    if state.status == RunStatus.REPORT_EXPORTING:
        return decide_report_exporting(state=state, export_result=input.export_result)

    return Decision(
        kind=DecisionKind.FAIL,
        next_status=RunStatus.FAILED,
        failure=FailureRecord(
            run_id=state.run_id,
            call_id=None,
            worker_id=None,
            stage=None,
            category="workflow_state",
            reason=f"未知状态: {state.status}",
            evidence_paths=(state.run_dir / "state.json",),
            early_stop=True,
            human_action_required=None,
        ),
    )


def require_upstream_ready(plan: StagePlan, manifest: ApprovedManifest) -> GuardResult:
    if plan.required_upstream_stage is None:
        return guard_passed("ok")
    required_workers = stage_plan(plan.required_upstream_stage).workers
    missing = tuple(
        worker_id
        for worker_id in required_workers
        if not manifest.has_worker(worker_id=worker_id, stage=plan.required_upstream_stage)
    )
    if missing:
        missing_text = ",".join(missing)
        return guard_failed(
            category="artifact_flow",
            reason=f"上游 approved material 缺失: {missing_text}",
            paths=(),
            early_stop=True,
        )
    return guard_passed("ok")


def decide_wake_stage(state: WorkflowState, stage: Stage, manifest: ApprovedManifest) -> Decision:
    plan = stage_plan(stage)
    upstream_check = require_upstream_ready(plan=plan, manifest=manifest)
    if not upstream_check.ok:
        return _blocked_failure(
            state=state,
            stage=stage,
            worker_id=None,
            category="artifact_flow",
            reason=upstream_check.reason or "上游 approved material 缺失",
        )

    if stage == Stage.INVESTMENT_DEBATE and state.request.stop_point == StopPoint.NONE:
        next_worker = next_investment_debate_worker(manifest=manifest)
        if next_worker is None:
            return Decision(
                kind=DecisionKind.ADVANCE,
                stage=stage,
                next_status=plan.ready_status,
            )
        return wake_investment_debate_worker(state=state, worker_id=next_worker)
    if stage == Stage.RISK_DEBATE and state.request.stop_point == StopPoint.NONE:
        next_worker = next_risk_debate_worker(manifest=manifest)
        if next_worker is None:
            return Decision(
                kind=DecisionKind.ADVANCE,
                stage=stage,
                next_status=plan.ready_status,
            )
        return wake_risk_debate_worker(state=state, worker_id=next_worker)

    # controller 只决定下一批 worker id，不负责构造运行时调用参数。
    return Decision(
        kind=DecisionKind.WAKE_STAGE,
        stage=stage,
        batch=StageBatch(
            run_id=state.run_id,
            stage=stage,
            worker_ids=plan.workers,
            scope=BatchScope.FULL_STAGE,
            collect_first=plan.collect_first,
            stop_point=state.request.stop_point,
        ),
        next_status=plan.running_status,
    )


def resolve_single_worker_target(state: WorkflowState) -> TargetWorkerResult:
    request = state.request
    if request.target_worker_id is None:
        return TargetWorkerResult.failed(
            state=state,
            category="single_worker_target",
            reason="单 worker 运行必须提供 target_worker_id",
        )
    worker = worker_by_id_or_none(request.target_worker_id)
    if worker is None:
        return TargetWorkerResult.failed(
            state=state,
            category="single_worker_target",
            reason=f"未知 target_worker_id: {request.target_worker_id}",
        )
    if request.target_stage is not None and request.target_stage != worker.stage:
        return TargetWorkerResult.failed(
            state=state,
            category="single_worker_target",
            reason="target_stage 与 target_worker_id 所属阶段不一致",
        )
    return TargetWorkerResult(ok=True, worker_id=worker.id, stage=worker.stage, failure=None)


def decide_wake_single_worker(state: WorkflowState, manifest: ApprovedManifest) -> Decision:
    target = resolve_single_worker_target(state)
    if not target.ok:
        return Decision(
            kind=DecisionKind.BLOCKED,
            next_status=RunStatus.FAILED,
            failure=target.failure,
        )
    assert target.worker_id is not None
    assert target.stage is not None
    plan = stage_plan(target.stage)
    upstream_check = require_upstream_ready(plan=plan, manifest=manifest)
    if not upstream_check.ok:
        return _blocked_failure(
            state=state,
            stage=target.stage,
            worker_id=target.worker_id,
            category="artifact_flow",
            reason=upstream_check.reason or "上游 approved material 缺失",
        )
    return Decision(
        kind=DecisionKind.WAKE_STAGE,
        stage=target.stage,
        batch=StageBatch(
            run_id=state.run_id,
            stage=target.stage,
            worker_ids=(target.worker_id,),
            scope=BatchScope.SINGLE_WORKER,
            collect_first=False,
            stop_point=state.request.stop_point,
        ),
        next_status=plan.running_status,
    )


def decide_running_stage(
    state: WorkflowState,
    stage: Stage,
    results: tuple[WorkerResult, ...],
    manifest: ApprovedManifest,
) -> Decision:
    plan = stage_plan(stage)
    stage_results = tuple(result for result in results if result.stage == stage)
    expected_workers = expected_workers_for_state(state=state, plan=plan)
    if state.request.stop_point in (StopPoint.FIRST_RESPONSE, StopPoint.SINGLE_WORKER_COMPLETE) and not expected_workers:
        failed = resolve_single_worker_target(state)
        return Decision(
            kind=DecisionKind.BLOCKED,
            next_status=RunStatus.FAILED,
            failure=failed.failure,
        )

    failures = terminal_failures(stage_results)
    if failures:
        return Decision(
            kind=DecisionKind.FAIL,
            next_status=RunStatus.FAILED,
            failure=merge_stage_failures(run_id=state.run_id, stage=stage, failures=failures),
        )

    if stage == Stage.INVESTMENT_DEBATE and state.request.stop_point == StopPoint.NONE:
        return decide_running_investment_debate(
            state=state,
            results=stage_results,
            manifest=manifest,
        )
    if stage == Stage.RISK_DEBATE and state.request.stop_point == StopPoint.NONE:
        return decide_running_risk_debate(
            state=state,
            results=stage_results,
            manifest=manifest,
        )

    if state.request.stop_point == StopPoint.FIRST_RESPONSE:
        if first_response_ready(results=stage_results, workers=expected_workers):
            return Decision(DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED)
        return Decision(DecisionKind.WAIT, reason=f"等待 {stage.value} first response")

    if state.request.stop_point == StopPoint.SINGLE_WORKER_COMPLETE:
        if all_workers_have_result(results=stage_results, workers=expected_workers) and all_workers_approved(
            workers=expected_workers,
            stage=stage,
            manifest=manifest,
        ):
            return Decision(DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED)
        return Decision(DecisionKind.WAIT, reason=f"等待 {stage.value} 单 worker 完整材料批准")

    if not all_workers_have_result(results=stage_results, workers=expected_workers):
        return Decision(DecisionKind.WAIT, reason=f"等待 {stage.value} worker result")

    if not all_workers_approved(workers=expected_workers, stage=stage, manifest=manifest):
        return Decision(DecisionKind.WAIT, reason=f"等待 {stage.value} approved material")

    return Decision(
        kind=DecisionKind.ADVANCE,
        stage=stage,
        next_status=plan.ready_status,
    )


def terminal_failures(results: tuple[WorkerResult, ...]) -> tuple[FailureRecord, ...]:
    failures: list[FailureRecord] = []
    for result in results:
        if result.status not in (WorkerStatus.FAILED, WorkerStatus.BLOCKED):
            continue
        if result.failure is not None:
            failures.append(result.failure)
    return tuple(failures)


def all_workers_have_result(results: tuple[WorkerResult, ...], workers: tuple[str, ...]) -> bool:
    observed = {result.worker_id for result in results}
    return set(workers).issubset(observed)


def all_workers_approved(workers: tuple[str, ...], stage: Stage, manifest: ApprovedManifest) -> bool:
    return all(manifest.has_worker(worker_id=worker_id, stage=stage) for worker_id in workers)


def first_response_ready(results: tuple[WorkerResult, ...], workers: tuple[str, ...]) -> bool:
    succeeded = {result.worker_id for result in results if result.status == WorkerStatus.SUCCEEDED}
    return set(workers).issubset(succeeded)


def next_investment_debate_worker(manifest: ApprovedManifest) -> str | None:
    if not manifest.has_worker(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE):
        return "bull_researcher"
    if not manifest.has_worker(worker_id="bear_researcher", stage=Stage.INVESTMENT_DEBATE):
        return "bear_researcher"
    return None


def wake_investment_debate_worker(state: WorkflowState, worker_id: str) -> Decision:
    plan = stage_plan(Stage.INVESTMENT_DEBATE)
    return Decision(
        kind=DecisionKind.WAKE_STAGE,
        stage=Stage.INVESTMENT_DEBATE,
        batch=StageBatch(
            run_id=state.run_id,
            stage=Stage.INVESTMENT_DEBATE,
            worker_ids=(worker_id,),
            scope=BatchScope.FULL_STAGE,
            collect_first=False,
            stop_point=state.request.stop_point,
        ),
        next_status=plan.running_status,
    )


def next_risk_debate_worker(manifest: ApprovedManifest) -> str | None:
    if not manifest.has_worker(worker_id="risk_challenger", stage=Stage.RISK_DEBATE):
        return "risk_challenger"
    if not manifest.has_worker(worker_id="risk_guardian", stage=Stage.RISK_DEBATE):
        return "risk_guardian"
    if not manifest.has_worker(worker_id="risk_moderator", stage=Stage.RISK_DEBATE):
        return "risk_moderator"
    return None


def wake_risk_debate_worker(state: WorkflowState, worker_id: str) -> Decision:
    plan = stage_plan(Stage.RISK_DEBATE)
    return Decision(
        kind=DecisionKind.WAKE_STAGE,
        stage=Stage.RISK_DEBATE,
        batch=StageBatch(
            run_id=state.run_id,
            stage=Stage.RISK_DEBATE,
            worker_ids=(worker_id,),
            scope=BatchScope.FULL_STAGE,
            collect_first=False,
            stop_point=state.request.stop_point,
        ),
        next_status=plan.running_status,
    )


def decide_running_investment_debate(
    state: WorkflowState,
    results: tuple[WorkerResult, ...],
    manifest: ApprovedManifest,
) -> Decision:
    if not all_workers_have_result(results=results, workers=("bull_researcher",)):
        return Decision(DecisionKind.WAIT, reason="等待 investment_debate bull_researcher worker result")
    if not manifest.has_worker(worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE):
        return Decision(DecisionKind.WAIT, reason="等待 investment_debate bull_researcher approved material")

    if not all_workers_have_result(results=results, workers=("bear_researcher",)):
        return wake_investment_debate_worker(state=state, worker_id="bear_researcher")
    if not manifest.has_worker(worker_id="bear_researcher", stage=Stage.INVESTMENT_DEBATE):
        return Decision(DecisionKind.WAIT, reason="等待 investment_debate bear_researcher approved material")

    return Decision(
        kind=DecisionKind.ADVANCE,
        stage=Stage.INVESTMENT_DEBATE,
        next_status=RunStatus.INVESTMENT_DEBATE_READY,
    )


def decide_running_risk_debate(
    state: WorkflowState,
    results: tuple[WorkerResult, ...],
    manifest: ApprovedManifest,
) -> Decision:
    if not all_workers_have_result(results=results, workers=("risk_challenger",)):
        return Decision(DecisionKind.WAIT, reason="等待 risk_debate risk_challenger worker result")
    if not manifest.has_worker(worker_id="risk_challenger", stage=Stage.RISK_DEBATE):
        return Decision(DecisionKind.WAIT, reason="等待 risk_debate risk_challenger approved material")

    if not all_workers_have_result(results=results, workers=("risk_guardian",)):
        return wake_risk_debate_worker(state=state, worker_id="risk_guardian")
    if not manifest.has_worker(worker_id="risk_guardian", stage=Stage.RISK_DEBATE):
        return Decision(DecisionKind.WAIT, reason="等待 risk_debate risk_guardian approved material")

    if not all_workers_have_result(results=results, workers=("risk_moderator",)):
        return wake_risk_debate_worker(state=state, worker_id="risk_moderator")
    if not manifest.has_worker(worker_id="risk_moderator", stage=Stage.RISK_DEBATE):
        return Decision(DecisionKind.WAIT, reason="等待 risk_debate risk_moderator approved material")

    return Decision(
        kind=DecisionKind.ADVANCE,
        stage=Stage.RISK_DEBATE,
        next_status=RunStatus.RISK_DEBATE_READY,
    )


def expected_workers_for_state(state: WorkflowState, plan: StagePlan) -> tuple[str, ...]:
    if state.request.stop_point in (StopPoint.FIRST_RESPONSE, StopPoint.SINGLE_WORKER_COMPLETE):
        target = resolve_single_worker_target(state)
        if not target.ok or target.worker_id is None:
            return ()
        return (target.worker_id,)
    return plan.workers


def decide_ready_stage(state: WorkflowState, stage: Stage, manifest: ApprovedManifest) -> Decision:
    if stage == Stage.FRONTLINE and state.request.stop_point == StopPoint.FRONTLINE_READY:
        return Decision(kind=DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED)
    if stage == Stage.INVESTMENT_DEBATE and state.request.stop_point == StopPoint.INVESTMENT_DEBATE_READY:
        return Decision(kind=DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED)

    if stage == Stage.PORTFOLIO_DECISION:
        if not manifest.has_worker("portfolio_manager", Stage.PORTFOLIO_DECISION):
            return Decision(
                kind=DecisionKind.FAIL,
                next_status=RunStatus.FAILED,
                failure=FailureRecord(
                    run_id=state.run_id,
                    call_id=None,
                    worker_id="portfolio_manager",
                    stage=Stage.PORTFOLIO_DECISION,
                    category="pm_owner",
                    reason="portfolio_manager approved material 缺失",
                    evidence_paths=(state.run_dir / "openviking" / "approved-manifest.json",),
                    early_stop=True,
                    human_action_required=None,
                ),
            )

    if stage == Stage.FINAL_REPORT:
        if not manifest.has_worker("report_polisher", Stage.FINAL_REPORT):
            return Decision(
                kind=DecisionKind.FAIL,
                next_status=RunStatus.FAILED,
                failure=FailureRecord(
                    run_id=state.run_id,
                    call_id=None,
                    worker_id="report_polisher",
                    stage=Stage.FINAL_REPORT,
                    category="final_report",
                    reason="report_polisher approved material 缺失",
                    evidence_paths=(state.run_dir / "openviking" / "approved-manifest.json",),
                    early_stop=True,
                    human_action_required=None,
                ),
            )
        return Decision(kind=DecisionKind.EXPORT_REPORT, next_status=RunStatus.REPORT_EXPORTING)

    next_stage = stage_plan(stage).next_stage
    if next_stage is None:
        return Decision(
            kind=DecisionKind.FAIL,
            next_status=RunStatus.FAILED,
            failure=FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=stage,
                category="workflow_state",
                reason="缺少下一阶段",
                evidence_paths=(state.run_dir / "state.json",),
                early_stop=True,
                human_action_required=None,
            ),
        )
    return decide_wake_stage(state=state, stage=next_stage, manifest=manifest)


def decide_report_exporting(state: WorkflowState, export_result: ExportResult | None) -> Decision:
    if export_result is None:
        return Decision(kind=DecisionKind.WAIT, reason="等待 export result")
    # CN_A 下图表资产导出失败属于产品导出证据，不阻断 12-worker workflow 完成。
    if export_result_allows_workflow_completion(state.request.profile, export_result):
        return Decision(kind=DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED)
    return Decision(
        kind=DecisionKind.FAIL,
        next_status=RunStatus.FAILED,
        failure=export_result.failure
        or FailureRecord(
            run_id=state.run_id,
            call_id=None,
            worker_id="report_polisher",
            stage=Stage.FINAL_REPORT,
            category="export_truthfulness",
            reason=f"export status={export_result.status}",
            evidence_paths=(state.run_dir / "reports" / "export-result.json",),
            early_stop=True,
            human_action_required=None,
        ),
    )


def group_failures_by_category(failures: tuple[FailureRecord, ...]) -> dict[str, tuple[FailureRecord, ...]]:
    grouped: dict[str, list[FailureRecord]] = {}
    for failure in failures:
        grouped.setdefault(failure.category, []).append(failure)
    return {category: tuple(items) for category, items in grouped.items()}


def first_human_action(failures: tuple[FailureRecord, ...]) -> str | None:
    for failure in failures:
        if failure.human_action_required:
            return failure.human_action_required
    return None


def merge_stage_failures(run_id: str, stage: Stage, failures: tuple[FailureRecord, ...]) -> FailureRecord:
    grouped = group_failures_by_category(failures)
    reason_lines: list[str] = []
    evidence_paths: list[Path] = []
    for category, items in grouped.items():
        workers = [item.worker_id for item in items]
        reasons = [item.reason for item in items]
        reason_lines.append(f"{category}: workers={workers}; reasons={reasons}")
        for item in items:
            for evidence_path in item.evidence_paths:
                if evidence_path not in evidence_paths:
                    evidence_paths.append(evidence_path)
    # collect-first 会先收集同阶段多条失败；但只要任一失败命中共享早停规则，合并后仍必须标记早停，
    # 否则主流程会把“本应终止的越权/证据污染风险”误判为可继续推进。
    return FailureRecord(
        run_id=run_id,
        call_id=None,
        worker_id=None,
        stage=stage,
        category="stage_batch",
        reason="\n".join(reason_lines) if reason_lines else f"{stage.value} stage batch failed",
        evidence_paths=tuple(evidence_paths),
        early_stop=any(should_early_stop(item) for item in failures),
        human_action_required=first_human_action(failures),
    )


def _blocked_failure(
    state: WorkflowState,
    stage: Stage,
    worker_id: str | None,
    category: str,
    reason: str,
) -> Decision:
    return Decision(
        kind=DecisionKind.BLOCKED,
        next_status=RunStatus.FAILED,
        failure=FailureRecord(
            run_id=state.run_id,
            call_id=None,
            worker_id=worker_id,
            stage=stage,
            category=category,
            reason=reason,
            evidence_paths=(state.run_dir / "openviking" / "approved-manifest.json",),
            early_stop=True,
            human_action_required=None,
        ),
    )
