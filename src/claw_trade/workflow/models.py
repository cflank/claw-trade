from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claw_trade.artifacts.refs import MaterialReadRef, MaterialTarget, OpenVikingReadCapability


class StopPoint(str, Enum):
    NONE = "none"
    FIRST_RESPONSE = "first_response"
    SINGLE_WORKER_COMPLETE = "single_worker_complete"
    FRONTLINE_READY = "frontline_ready"
    INVESTMENT_DEBATE_READY = "investment_debate_ready"
    COMPLETED = "completed"


class WorkflowEntryPoint(str, Enum):
    GENERIC = "generic"
    REPORT_COMMAND = "report_command"


class RunStatus(str, Enum):
    CREATED = "created"
    FRONTLINE_RUNNING = "frontline_running"
    FRONTLINE_READY = "frontline_ready"
    INVESTMENT_DEBATE_RUNNING = "investment_debate_running"
    INVESTMENT_DEBATE_READY = "investment_debate_ready"
    INVESTMENT_DECISION_RUNNING = "investment_decision_running"
    INVESTMENT_DECISION_READY = "investment_decision_ready"
    TRADE_DECISION_RUNNING = "trade_decision_running"
    TRADE_DECISION_READY = "trade_decision_ready"
    RISK_DEBATE_RUNNING = "risk_debate_running"
    RISK_DEBATE_READY = "risk_debate_ready"
    PORTFOLIO_DECISION_RUNNING = "portfolio_decision_running"
    PORTFOLIO_DECISION_READY = "portfolio_decision_ready"
    REPORT_EXPORTING = "report_exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Stage(str, Enum):
    FRONTLINE = "frontline"
    INVESTMENT_DEBATE = "investment_debate"
    INVESTMENT_DECISION = "investment_decision"
    TRADE_DECISION = "trade_decision"
    RISK_DEBATE = "risk_debate"
    PORTFOLIO_DECISION = "portfolio_decision"


@dataclass(frozen=True)
class RunRequest:
    ticker: str
    company_name: str
    market: str
    profile: str
    currency: str
    currency_symbol: str
    current_date: str
    start_date: str
    end_date: str
    stop_point: StopPoint = StopPoint.NONE
    target_worker_id: str | None = None
    target_stage: Stage | None = None
    entry_point: WorkflowEntryPoint = WorkflowEntryPoint.GENERIC


@dataclass(frozen=True)
class WorkflowState:
    run_id: str
    request: RunRequest
    status: RunStatus
    run_dir: Path
    openviking_namespace: str
    created_at: str
    updated_at: str
    active_stage: Stage | None = None
    completed_workers: tuple[str, ...] = ()
    failed_workers: tuple[str, ...] = ()
    failure_reason: str | None = None
    last_decision_path: Path | None = None


@dataclass(frozen=True)
class WorkerSpec:
    id: str
    stage: Stage


@dataclass(frozen=True)
class StagePlan:
    stage: Stage
    workers: tuple[str, ...]
    running_status: RunStatus
    ready_status: RunStatus
    next_stage: Stage | None
    required_upstream_stage: Stage | None
    collect_first: bool


class BatchScope(str, Enum):
    FULL_STAGE = "full_stage"
    SINGLE_WORKER = "single_worker"


class DecisionKind(str, Enum):
    WAKE_STAGE = "wake_stage"
    WAIT = "wait"
    ADVANCE = "advance"
    EXPORT_REPORT = "export_report"
    COMPLETE = "complete"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class StageBatch:
    run_id: str
    stage: Stage
    worker_ids: tuple[str, ...]
    scope: BatchScope
    collect_first: bool
    stop_point: StopPoint


@dataclass(frozen=True)
class FailureRecord:
    run_id: str
    call_id: str | None
    worker_id: str | None
    stage: Stage | None
    category: str
    reason: str
    evidence_paths: tuple[Path, ...]
    early_stop: bool
    human_action_required: str | None


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    stage: Stage | None = None
    batch: StageBatch | None = None
    next_status: RunStatus | None = None
    reason: str | None = None
    failure: FailureRecord | None = None


class WorkerStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ReadPolicy:
    default_layer: str = "L1"
    allow_l2_when: tuple[str, ...] = (
        "specific_number_required",
        "chart_required",
        "source_text_required",
        "conflict_resolution_required",
        "hard_gate_field_required",
    )
    forbid_compact_as_writing_source: bool = True


@dataclass(frozen=True)
class WorkerCall:
    call_id: str
    run_id: str
    worker_id: str
    stage: Stage
    profile: str
    ticker: str
    company_name: str
    market: str
    currency: str
    currency_symbol: str
    current_date: str
    start_date: str
    end_date: str
    allowed_tools: tuple[str, ...]
    upstream_materials: tuple[MaterialReadRef, ...]
    openviking_read_capabilities: tuple[OpenVikingReadCapability, ...]
    material_target: MaterialTarget
    read_policy: ReadPolicy
    evidence_dir: Path
    stop_after_first_response: bool
    system_context_policy: str = "openclaw_default"
    prompt_runtime_vars: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenClawCommand:
    agent: str
    worker_id: str
    profile: str
    stage: str
    run_id: str
    call_id: str
    runtime_vars: dict[str, str]
    allowed_tools: tuple[str, ...]
    upstream_materials: tuple[dict[str, str | None], ...]
    openviking_read_capabilities: tuple[dict[str, str | None], ...]
    material_target: dict[str, str]
    read_policy: dict[str, object]
    evidence_dir: Path
    stop_after_first_response: bool
    system_context_policy: str = "openclaw_default"


@dataclass(frozen=True)
class WorkerResult:
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    status: WorkerStatus
    openclaw_result_path: Path | None
    approved_material_id: str | None
    failure: FailureRecord | None


@dataclass(frozen=True)
class StageBatchResult:
    run_id: str
    stage: Stage
    worker_results: tuple[WorkerResult, ...]
    failures: tuple[FailureRecord, ...]
    early_stop_used: bool
    collect_first_report_path: Path


@dataclass(frozen=True)
class ExportResult:
    run_id: str
    status: str
    final_report_path: Path | None
    export_guard_result_path: Path | None
    pm_owner_guard_result_path: Path | None
    unsupported_claims: tuple[str, ...]
    failure: FailureRecord | None

    @classmethod
    def passed(cls, state: WorkflowState, final_report_path: Path, guard_path: Path) -> ExportResult:
        return cls(
            run_id=state.run_id,
            status="passed",
            final_report_path=final_report_path,
            export_guard_result_path=guard_path,
            pm_owner_guard_result_path=None,
            unsupported_claims=(),
            failure=None,
        )

    @classmethod
    def failed(
        cls,
        state: WorkflowState,
        category: str,
        reason: str,
        paths: tuple[Path, ...],
        unsupported_claims: tuple[str, ...] = (),
    ) -> ExportResult:
        return cls(
            run_id=state.run_id,
            status="failed",
            final_report_path=None,
            export_guard_result_path=None,
            pm_owner_guard_result_path=None,
            unsupported_claims=unsupported_claims,
            failure=FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=Stage.PORTFOLIO_DECISION,
                category=category,
                reason=reason,
                evidence_paths=paths,
                early_stop=True,
                human_action_required=None,
            ),
        )


_CN_NON_BLOCKING_EXPORT_FAILURE_CATEGORIES = frozenset({"export_report_assets"})


def export_result_allows_workflow_completion(profile: str, export_result: ExportResult) -> bool:
    if export_result.status == "passed":
        return True
    return _is_cn_non_blocking_export_failure(profile=profile, export_result=export_result)


def _is_cn_non_blocking_export_failure(profile: str, export_result: ExportResult) -> bool:
    if profile != "CN_A":
        return False
    if export_result.status != "failed" or export_result.failure is None:
        return False
    return export_result.failure.category in _CN_NON_BLOCKING_EXPORT_FAILURE_CATEGORIES


def normalize_stop_point(value: str | None) -> StopPoint:
    if value is None:
        return StopPoint.NONE
    return StopPoint(value.strip().lower())


def is_terminal_status(status: RunStatus) -> bool:
    return status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


def export_passed(state: WorkflowState, final_report_path: Path, guard_path: Path) -> ExportResult:
    return ExportResult.passed(state, final_report_path, guard_path)


def export_failed(state: WorkflowState, category: str, reason: str, paths: tuple[Path, ...]) -> ExportResult:
    return ExportResult.failed(state, category, reason, paths)


def failed_worker_result(call: WorkerCall, category: str, reason: str, paths: tuple[Path, ...]) -> WorkerResult:
    status = WorkerStatus.BLOCKED if category == "blocked" else WorkerStatus.FAILED
    return WorkerResult(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        status=status,
        openclaw_result_path=None,
        approved_material_id=None,
        failure=FailureRecord(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            category=category,
            reason=reason,
            evidence_paths=paths,
            early_stop=False,
            human_action_required=None,
        ),
    )


def blocked_worker_result(run_id: str, worker_id: str, stage: Stage, failure: FailureRecord) -> WorkerResult:
    if failure.call_id is None or not failure.call_id.strip():
        raise ValueError("blocked_worker_result 需要 failure.call_id，调用方错误")
    call_id = failure.call_id
    return WorkerResult(
        run_id=run_id,
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        status=WorkerStatus.BLOCKED,
        openclaw_result_path=None,
        approved_material_id=None,
        failure=failure,
    )
