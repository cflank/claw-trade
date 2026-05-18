from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Lock
from typing import Callable, Protocol

from claw_trade.artifacts.approval import approve_worker_material
from claw_trade.artifacts.manifest import ApprovedManifest, ManifestStore
from claw_trade.artifacts.openviking_client import OpenVikingReadResult
from claw_trade.artifacts.refs import ApprovedMaterial
from claw_trade.config.profiles import require_profile
from claw_trade.config.tool_names import load_tool_registry
from claw_trade.data_gateway.openviking import LineageWriteResult
from claw_trade.data_gateway.models import RunProviderPlan
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner, build_report_run_plan
from claw_trade.guards.common import (
    ApprovalResult,
    BootResult,
    GuardResult,
    combine_guard_results,
    should_early_stop,
)
from claw_trade.guards.artifact_flow import validate_artifact_flow
from claw_trade.guards.openviking_access import validate_openviking_runtime_reads
from claw_trade.guards.provider_request import validate_provider_request
from claw_trade.guards.tool_calls import validate_tool_calls
from claw_trade.guards.visible_tools import validate_visible_tools
from claw_trade.guards.workspace_evidence import validate_workspace_evidence
from claw_trade.runtime.evidence_reader import EvidenceReadResult, EvidenceReader, OpenClawResult, ProviderEvidence
from claw_trade.runtime.openclaw_client import build_openclaw_command
from claw_trade.runtime.request_builder import RequestBuildResult, build_worker_call
from claw_trade.workflow.controller import ControllerInput, decide_next
from claw_trade.workflow.models import (
    Decision,
    DecisionKind,
    ExportResult,
    FailureRecord,
    RunRequest,
    RunStatus,
    Stage,
    StageBatch,
    StageBatchResult,
    StopPoint,
    WorkerCall,
    WorkerResult,
    WorkerStatus,
    WorkflowState,
    WorkflowEntryPoint,
    export_result_allows_workflow_completion,
)
from claw_trade.workflow.store import WorkflowStore


class OpenClawClientLike(Protocol):
    def probe(self) -> object: ...

    def run_worker(self, command: object) -> OpenClawResult: ...


class OpenVikingClientLike(Protocol):
    def probe_read_stat_receipt(self) -> object: ...

    def probe_namespace_stat(self) -> object: ...

    def ensure_namespace(self, namespace: str) -> None: ...

    def read_approved_l1(self, material: ApprovedMaterial) -> OpenVikingReadResult: ...


class RequestBuilderLike(Protocol):
    def build_worker_call(
        self,
        state: WorkflowState,
        worker_id: str,
        stage: Stage,
        manifest: ApprovedManifest,
        turn_index: int = 0,
        round_index: int = 1,
        role_turn_index: int = 1,
    ) -> RequestBuildResult: ...


class ToolRegistryProbeLike(Protocol):
    def probe(self) -> BootResult: ...


class ExporterLike(Protocol):
    def export(self, state: WorkflowState, manifest: ApprovedManifest) -> ExportResult: ...


class LineageWriterLike(Protocol):
    def link_after_export(
        self,
        *,
        state: WorkflowState,
        manifest: ApprovedManifest,
        export_result: ExportResult,
    ) -> LineageWriteResult: ...


class RunProviderPlanStoreLike(Protocol):
    def write(self, plan: RunProviderPlan) -> str: ...


@dataclass(frozen=True)
class PromptMaterialResult:
    ok: bool
    call: WorkerCall | None
    failure: FailureRecord | None


@dataclass(frozen=True)
class PromptMaterialText:
    worker_id: str
    stage: Stage
    text: str
    turn_index: int = 0
    round_index: int = 1
    role_turn_index: int = 1


class _DefaultRequestBuilder:
    def build_worker_call(
        self,
        state: WorkflowState,
        worker_id: str,
        stage: Stage,
        manifest: ApprovedManifest,
        turn_index: int = 0,
        round_index: int = 1,
        role_turn_index: int = 1,
    ) -> RequestBuildResult:
        return build_worker_call(
            state=state,
            worker_id=worker_id,
            stage=stage,
            manifest=manifest,
            turn_index=turn_index,
            round_index=round_index,
            role_turn_index=role_turn_index,
        )


class _DefaultToolRegistryProbe:
    def probe(self) -> BootResult:
        probe = load_tool_registry()
        if not probe.ok or probe.registry is None:
            return BootResult.blocked("tool_registry", probe.reason or "tool registry 加载失败")
        return BootResult.ok_result()


class ControlRunner:
    def __init__(
        self,
        *,
        store: WorkflowStore,
        manifest_store: ManifestStore,
        openclaw: OpenClawClientLike,
        openviking: OpenVikingClientLike,
        request_builder: RequestBuilderLike | None = None,
        evidence_reader: EvidenceReader | None = None,
        tool_registry_probe: ToolRegistryProbeLike | None = None,
        exporter: ExporterLike | None = None,
        now_text: Callable[[], str] | None = None,
        agents_root: Path | None = None,
        run_provider_planner: RunProviderPlanner | None = None,
        run_provider_plan_store: RunProviderPlanStoreLike | None = None,
        run_provider_registry: ProviderRegistry | None = None,
        provider_config_version_resolver: Callable[[], str] | None = None,
        lineage_writer: LineageWriterLike | None = None,
    ) -> None:
        self.store = store
        self.manifest_store = manifest_store
        self.openclaw = openclaw
        self.openviking = openviking
        self.request_builder = request_builder or _DefaultRequestBuilder()
        self.evidence_reader = evidence_reader or EvidenceReader()
        self.tool_registry_probe = tool_registry_probe or _DefaultToolRegistryProbe()
        self.exporter = exporter
        self.now_text = now_text or _utc_now_iso_text
        self.agents_root = agents_root or (Path(__file__).resolve().parents[3] / "agents")
        self.run_provider_planner = run_provider_planner
        self.run_provider_plan_store = run_provider_plan_store
        self.run_provider_registry = run_provider_registry
        self.provider_config_version_resolver = provider_config_version_resolver
        self.lineage_writer = lineage_writer
        self._manifest_write_lock = Lock()

    def boot(self, request: RunRequest) -> BootResult:
        profile = require_profile(request.profile)
        if not profile.ok:
            return BootResult.blocked("profile", profile.reason or f"profile 校验失败: {request.profile}")

        openclaw_probe = self.openclaw.probe()
        if not _probe_ok(openclaw_probe):
            return BootResult.blocked("openclaw", _probe_reason(openclaw_probe, "openclaw probe 失败"))

        if request.stop_point == StopPoint.FIRST_RESPONSE:
            openviking_probe = self.openviking.probe_namespace_stat()
        else:
            openviking_probe = self.openviking.probe_read_stat_receipt()
        if not _probe_ok(openviking_probe):
            return BootResult.blocked("openviking", _probe_reason(openviking_probe, "openviking probe 失败"))

        registry_probe = self.tool_registry_probe.probe()
        if not registry_probe.ok:
            return BootResult.blocked(
                registry_probe.category or "tool_registry",
                registry_probe.reason or "tool registry probe 失败",
            )
        return BootResult.ok_result()

    def fail_before_run(self, request: RunRequest, boot: BootResult) -> WorkflowState:
        # 启动前失败也必须落盘成可审计 FAILED state；这里只记录失败，不写任何 runtime 证据文件。
        state = self.store.create_run(request)
        failure = FailureRecord(
            run_id=state.run_id,
            call_id=None,
            worker_id=None,
            stage=None,
            category=boot.category or "boot_blocked",
            reason=boot.reason or "boot 失败",
            evidence_paths=(state.run_dir / "request.json",),
            early_stop=True,
            human_action_required=None,
        )
        self.store.save_failure(failure)
        failed = replace(
            state,
            status=RunStatus.FAILED,
            active_stage=None,
            updated_at=self.now_text(),
            failure_reason=f"{failure.category}: {failure.reason}",
            last_decision_path=None,
        )
        self.store.save_state(failed)
        return failed

    def fail_run(self, state: WorkflowState, failure: FailureRecord, decision_path: Path) -> WorkflowState:
        self.store.save_failure(failure)
        failed = replace(
            state,
            status=RunStatus.FAILED,
            active_stage=None,
            updated_at=self.now_text(),
            failure_reason=f"{failure.category}: {failure.reason}",
            last_decision_path=decision_path,
        )
        self.store.save_state(failed)
        return failed

    def run(self, request: RunRequest) -> WorkflowState:
        boot = self.boot(request)
        if not boot.ok:
            return self.fail_before_run(request, boot)

        state = self.store.create_run(request)
        run_plan_failure = self._initialize_report_run_plan(state)
        if run_plan_failure is not None:
            return self.fail_run(
                state,
                run_plan_failure,
                decision_path=state.run_dir / "decisions" / "bootstrap-failed.json",
            )
        try:
            self.openviking.ensure_namespace(state.openviking_namespace)
        except Exception as exc:
            failure = FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=None,
                category="openviking",
                reason=f"openviking namespace 初始化失败: {exc}",
                evidence_paths=(state.run_dir / "state.json",),
                early_stop=True,
                human_action_required=None,
            )
            return self.fail_run(state, failure, decision_path=state.run_dir / "decisions" / "bootstrap-failed.json")

        while True:
            state = self.store.load_state(state.run_id)
            # 调度权边界：下一步 worker/stage 由 controller 决定，runner 只执行决定。
            decision = decide_next(
                ControllerInput(
                    state=state,
                    worker_results=self.store.list_worker_results(state.run_id),
                    manifest=self.manifest_store.load(state.run_id),
                    export_result=self.store.load_export_result(state.run_id),
                    now=self.now_text(),
                )
            )
            decision_path = self.store.save_decision(state.run_id, decision)
            state = self.apply_decision(state, decision, decision_path)
            if decision.kind in (DecisionKind.WAIT, DecisionKind.FAIL, DecisionKind.BLOCKED, DecisionKind.COMPLETE):
                return state
            if state.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
                return state

    def _initialize_report_run_plan(self, state: WorkflowState) -> FailureRecord | None:
        if state.request.entry_point != WorkflowEntryPoint.REPORT_COMMAND:
            return None
        if self.run_provider_planner is None or self.run_provider_plan_store is None or self.run_provider_registry is None:
            return FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=None,
                category="run_provider_plan",
                reason="report_command 缺少 run provider plan 依赖",
                evidence_paths=(state.run_dir / "request.json",),
                early_stop=True,
                human_action_required=None,
            )
        if self.provider_config_version_resolver is None:
            return FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=None,
                category="run_provider_plan",
                reason="report_command 缺少 provider_config_version snapshot resolver",
                evidence_paths=(state.run_dir / "request.json",),
                early_stop=True,
                human_action_required=None,
            )

        try:
            provider_config_version = self.provider_config_version_resolver()
            plan = build_report_run_plan(
                request=state.request,
                run_id=state.run_id,
                provider_config_version=provider_config_version,
                planner=self.run_provider_planner,
                registry=self.run_provider_registry,
            )
            if plan is None:
                return FailureRecord(
                    run_id=state.run_id,
                    call_id=None,
                    worker_id=None,
                    stage=None,
                    category="run_provider_plan",
                    reason="report_command run plan 生成失败",
                    evidence_paths=(state.run_dir / "request.json",),
                    early_stop=True,
                    human_action_required=None,
                )
            self.run_provider_plan_store.write(plan)
        except Exception as exc:
            return FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=None,
                category="run_provider_plan",
                reason=f"report_command run plan 写入失败: {exc}",
                evidence_paths=(state.run_dir / "request.json",),
                early_stop=True,
                human_action_required=None,
            )
        return None

    def apply_decision(self, state: WorkflowState, decision: Decision, decision_path: Path) -> WorkflowState:
        if decision.kind == DecisionKind.WAIT:
            waiting = replace(state, updated_at=self.now_text(), last_decision_path=decision_path)
            self.store.save_state(waiting)
            return waiting

        if decision.kind in (DecisionKind.FAIL, DecisionKind.BLOCKED):
            failure = decision.failure or FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=None,
                stage=decision.stage,
                category="workflow_state",
                reason="controller 返回失败但 failure 为空",
                evidence_paths=(state.run_dir / "state.json",),
                early_stop=True,
                human_action_required=None,
            )
            return self.fail_run(state, failure, decision_path)

        if decision.kind == DecisionKind.ADVANCE:
            advanced = replace(
                state,
                status=decision.next_status or state.status,
                active_stage=None,
                updated_at=self.now_text(),
                last_decision_path=decision_path,
            )
            self.store.save_state(advanced)
            return advanced

        if decision.kind == DecisionKind.WAKE_STAGE:
            if decision.batch is None or decision.stage is None:
                failure = FailureRecord(
                    run_id=state.run_id,
                    call_id=None,
                    worker_id=None,
                    stage=None,
                    category="workflow_state",
                    reason="controller 返回 WAKE_STAGE 但缺少 stage/batch",
                    evidence_paths=(state.run_dir / "state.json",),
                    early_stop=True,
                    human_action_required=None,
                )
                return self.fail_run(state, failure, decision_path)
            running = replace(
                state,
                status=decision.next_status or state.status,
                active_stage=decision.stage,
                updated_at=self.now_text(),
                last_decision_path=decision_path,
            )
            self.store.save_state(running)
            batch_result = self.run_stage_batch(running, decision.batch)
            self.store.save_stage_batch_result(batch_result)
            if batch_result.early_stop_used and batch_result.failures:
                failure = merge_stage_failures(running.run_id, decision.stage, batch_result.failures)
                return self.fail_run(running, failure, decision_path)
            return running

        if decision.kind == DecisionKind.EXPORT_REPORT:
            exporting = replace(
                state,
                status=RunStatus.REPORT_EXPORTING,
                active_stage=None,
                updated_at=self.now_text(),
                last_decision_path=decision_path,
            )
            self.store.save_state(exporting)
            manifest = self.manifest_store.load(exporting.run_id)
            exported = self.export_final_report(exporting, manifest)
            self.store.save_export_result(exported)
            if exported.status == "passed" and self.lineage_writer is not None:
                lineage = self.lineage_writer.link_after_export(
                    state=exporting,
                    manifest=manifest,
                    export_result=exported,
                )
                if not lineage.ok:
                    failure = FailureRecord(
                        run_id=exporting.run_id,
                        call_id=None,
                        worker_id=None,
                        stage=Stage.FINAL_REPORT,
                        category=lineage.category or "openviking_lineage",
                        reason=lineage.reason or "OpenViking lineage 写入失败",
                        evidence_paths=lineage.paths or (
                            exporting.run_dir / "openviking" / "approved-manifest.json",
                        ),
                        early_stop=True,
                        human_action_required=None,
                    )
                    return self.fail_run(exporting, failure, decision_path)
            if not export_result_allows_workflow_completion(exporting.request.profile, exported):
                failure = exported.failure or FailureRecord(
                    run_id=exporting.run_id,
                    call_id=None,
                    worker_id=None,
                    stage=Stage.FINAL_REPORT,
                    category="export_truthfulness",
                    reason=f"export status={exported.status}",
                    evidence_paths=(exporting.run_dir / "reports" / "export-result.json",),
                    early_stop=True,
                    human_action_required=None,
                )
                return self.fail_run(exporting, failure, decision_path)
            return exporting

        if decision.kind == DecisionKind.COMPLETE:
            try:
                self.assert_export_result_before_completed(state)
            except ValueError as exc:
                failure = FailureRecord(
                    run_id=state.run_id,
                    call_id=None,
                    worker_id=None,
                    stage=Stage.FINAL_REPORT,
                    category="export_truthfulness",
                    reason=str(exc),
                    evidence_paths=(state.run_dir / "reports" / "export-result.json",),
                    early_stop=True,
                    human_action_required=None,
                )
                return self.fail_run(state, failure, decision_path)

            completed = replace(
                state,
                status=RunStatus.COMPLETED,
                active_stage=None,
                updated_at=self.now_text(),
                last_decision_path=decision_path,
            )
            self.store.save_state(completed)
            return completed

        failure = FailureRecord(
            run_id=state.run_id,
            call_id=None,
            worker_id=None,
            stage=None,
            category="workflow_state",
            reason=f"未知 decision.kind: {decision.kind}",
            evidence_paths=(state.run_dir / "state.json",),
            early_stop=True,
            human_action_required=None,
        )
        return self.fail_run(state, failure, decision_path)

    def export_final_report(self, state: WorkflowState, manifest: ApprovedManifest) -> ExportResult:
        # B11 前 exporter 不可用时必须明确失败，禁止把导出阶段标记成成功。
        if self.exporter is None:
            return ExportResult.failed(
                state=state,
                category="export_blocked",
                reason="exporter 未提供，当前批次不允许导出",
                paths=(state.run_dir / "reports",),
            )
        try:
            result = self.exporter.export(state, manifest)
        except Exception as exc:
            return ExportResult.failed(
                state=state,
                category="export_blocked",
                reason=f"exporter 执行失败: {exc}",
                paths=(state.run_dir / "reports",),
            )
        if not isinstance(result, ExportResult):
            return ExportResult.failed(
                state=state,
                category="export_blocked",
                reason="exporter 返回对象类型错误",
                paths=(state.run_dir / "reports",),
            )
        return result

    def run_stage_batch(self, state: WorkflowState, batch: StageBatch) -> StageBatchResult:
        if self._should_run_stage_batch_concurrently(state, batch):
            return self._run_stage_batch_concurrent(state, batch)
        return self._run_stage_batch_serial(state, batch)

    def _should_run_stage_batch_concurrently(self, state: WorkflowState, batch: StageBatch) -> bool:
        return (
            state.request.frontline_execution_mode == "parallel"
            and batch.stage == Stage.FRONTLINE
            and batch.collect_first
            and len(batch.worker_ids) > 1
        )

    def _run_stage_batch_serial(self, state: WorkflowState, batch: StageBatch) -> StageBatchResult:
        worker_results: list[WorkerResult] = []
        failures: list[FailureRecord] = []
        early_stop_used = False
        early_stop_failures: list[FailureRecord] = []

        # collect-first 语义在这里执行：同阶段可恢复失败继续收集，命中早停类再中止。
        for worker_id in batch.worker_ids:
            manifest = self.manifest_store.load(batch.run_id)
            call_result = self.request_builder.build_worker_call(
                state=state,
                worker_id=worker_id,
                stage=batch.stage,
                manifest=manifest,
                turn_index=batch.turn_index,
                round_index=batch.round_index,
                role_turn_index=batch.role_turn_index,
            )
            if not call_result.ok or call_result.call is None:
                failure = self._normalize_build_failure(batch, worker_id, call_result)
                result = WorkerResult(
                    run_id=batch.run_id,
                    call_id=failure.call_id or _synthetic_blocked_call_id(batch.run_id, batch.stage, worker_id),
                    worker_id=worker_id,
                    stage=batch.stage,
                    status=WorkerStatus.BLOCKED,
                    openclaw_result_path=None,
                    approved_material_id=None,
                    failure=failure,
                    turn_index=batch.turn_index,
                    round_index=batch.round_index,
                    role_turn_index=batch.role_turn_index,
                )
                self.store.save_worker_result(result)
                worker_results.append(result)
                failures.append(failure)
                if should_early_stop(failure):
                    early_stop_used = True
                    early_stop_failures.append(failure)
                    break
                if not batch.collect_first:
                    early_stop_used = True
                    break
                continue

            prompt_result = self.attach_prompt_materials(call=call_result.call, manifest=manifest)
            if not prompt_result.ok or prompt_result.call is None:
                failure = prompt_result.failure or FailureRecord(
                    run_id=batch.run_id,
                    call_id=call_result.call.call_id,
                    worker_id=worker_id,
                    stage=batch.stage,
                    category="prompt_materials",
                    reason="prompt 材料注入失败",
                    evidence_paths=(state.run_dir / "openviking" / "approved-manifest.json",),
                    early_stop=True,
                    human_action_required=None,
                )
                result = WorkerResult(
                    run_id=batch.run_id,
                    call_id=failure.call_id or call_result.call.call_id,
                    worker_id=worker_id,
                    stage=batch.stage,
                    status=WorkerStatus.BLOCKED,
                    openclaw_result_path=None,
                    approved_material_id=None,
                    failure=failure,
                    turn_index=batch.turn_index,
                    round_index=batch.round_index,
                    role_turn_index=batch.role_turn_index,
                )
                self.store.save_worker_result(result)
                worker_results.append(result)
                failures.append(failure)
                early_stop_used = True
                early_stop_failures.append(failure)
                break

            call = prompt_result.call
            result = self.run_single_worker(call)
            self.store.save_worker_result(result)
            worker_results.append(result)
            if result.failure is None:
                continue

            failures.append(result.failure)
            if should_early_stop(result.failure):
                early_stop_used = True
                early_stop_failures.append(result.failure)
                break
            if not batch.collect_first:
                early_stop_used = True
                break

        report_path = self.write_collect_first_report(
            batch=batch,
            results=worker_results,
            failures=failures,
            early_stop_used=early_stop_used,
            early_stop_failures=tuple(early_stop_failures),
        )
        return StageBatchResult(
            run_id=batch.run_id,
            stage=batch.stage,
            worker_results=tuple(worker_results),
            failures=tuple(failures),
            early_stop_used=early_stop_used,
            collect_first_report_path=report_path,
        )

    def _run_stage_batch_concurrent(self, state: WorkflowState, batch: StageBatch) -> StageBatchResult:
        worker_results_by_id: dict[str, WorkerResult] = {}
        prepared_calls: list[WorkerCall] = []

        for worker_id in batch.worker_ids:
            manifest = self.manifest_store.load(batch.run_id)
            call_result = self.request_builder.build_worker_call(
                state=state,
                worker_id=worker_id,
                stage=batch.stage,
                manifest=manifest,
                turn_index=batch.turn_index,
                round_index=batch.round_index,
                role_turn_index=batch.role_turn_index,
            )
            if not call_result.ok or call_result.call is None:
                failure = self._normalize_build_failure(batch, worker_id, call_result)
                result = WorkerResult(
                    run_id=batch.run_id,
                    call_id=failure.call_id or _synthetic_blocked_call_id(batch.run_id, batch.stage, worker_id),
                    worker_id=worker_id,
                    stage=batch.stage,
                    status=WorkerStatus.BLOCKED,
                    openclaw_result_path=None,
                    approved_material_id=None,
                    failure=failure,
                    turn_index=batch.turn_index,
                    round_index=batch.round_index,
                    role_turn_index=batch.role_turn_index,
                )
                self.store.save_worker_result(result)
                worker_results_by_id[worker_id] = result
                if should_early_stop(failure):
                    break
                continue

            prompt_result = self.attach_prompt_materials(call=call_result.call, manifest=manifest)
            if not prompt_result.ok or prompt_result.call is None:
                failure = prompt_result.failure or FailureRecord(
                    run_id=batch.run_id,
                    call_id=call_result.call.call_id,
                    worker_id=worker_id,
                    stage=batch.stage,
                    category="prompt_materials",
                    reason="prompt 材料注入失败",
                    evidence_paths=(state.run_dir / "openviking" / "approved-manifest.json",),
                    early_stop=True,
                    human_action_required=None,
                )
                result = WorkerResult(
                    run_id=batch.run_id,
                    call_id=failure.call_id or call_result.call.call_id,
                    worker_id=worker_id,
                    stage=batch.stage,
                    status=WorkerStatus.BLOCKED,
                    openclaw_result_path=None,
                    approved_material_id=None,
                    failure=failure,
                    turn_index=batch.turn_index,
                    round_index=batch.round_index,
                    role_turn_index=batch.role_turn_index,
                )
                self.store.save_worker_result(result)
                worker_results_by_id[worker_id] = result
                break

            prepared_calls.append(prompt_result.call)

        if prepared_calls:
            with ThreadPoolExecutor(max_workers=len(prepared_calls)) as executor:
                future_to_call = {executor.submit(self.run_single_worker, call): call for call in prepared_calls}
                for future in as_completed(future_to_call):
                    call = future_to_call[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = _failed_worker_result(
                            call=call,
                            category="worker_exception",
                            reason=f"worker 执行异常: {exc}",
                            paths=(call.evidence_dir,),
                            openclaw_result_path=None,
                            early_stop=True,
                        )
                    self.store.save_worker_result(result)
                    worker_results_by_id[call.worker_id] = result

        worker_results = [
            worker_results_by_id[worker_id]
            for worker_id in batch.worker_ids
            if worker_id in worker_results_by_id
        ]
        failures = [result.failure for result in worker_results if result.failure is not None]
        early_stop_failures = [failure for failure in failures if should_early_stop(failure)]
        early_stop_used = bool(early_stop_failures) or (bool(failures) and not batch.collect_first)
        report_path = self.write_collect_first_report(
            batch=batch,
            results=worker_results,
            failures=failures,
            early_stop_used=early_stop_used,
            early_stop_failures=tuple(early_stop_failures),
        )
        return StageBatchResult(
            run_id=batch.run_id,
            stage=batch.stage,
            worker_results=tuple(worker_results),
            failures=tuple(failures),
            early_stop_used=early_stop_used,
            collect_first_report_path=report_path,
        )

    def attach_prompt_materials(self, call: WorkerCall, manifest: ApprovedManifest) -> PromptMaterialResult:
        if call.profile not in {"CN_A", "US", "HK", "CRYPTO"} or call.stage == Stage.FRONTLINE:
            return PromptMaterialResult(ok=True, call=call, failure=None)

        material_texts: dict[tuple[str, Stage], str] = {}
        ordered_material_texts: list[PromptMaterialText] = []
        material_by_id = {material.material_id: material for material in manifest.all_for_run(call.run_id)}
        for ref in call.upstream_materials:
            material = material_by_id.get(ref.material_id)
            if material is None:
                return PromptMaterialResult(
                    ok=False,
                    call=None,
                    failure=self._prompt_material_failure(
                        call=call,
                        reason=(
                            f"approved material 缺失: material_id={ref.material_id} "
                            f"worker={ref.worker_id} stage={ref.stage.value} turn={ref.turn_index}"
                        ),
                        paths=(call.evidence_dir / "call.json",),
                    ),
                )
            read_result = self.openviking.read_approved_l1(material)
            if not read_result.ok or read_result.content is None:
                return PromptMaterialResult(
                    ok=False,
                    call=None,
                    failure=self._prompt_material_failure(
                        call=call,
                        reason=(
                            f"读取 approved material L1 失败: worker={material.worker_id} "
                            f"category={read_result.error_category} reason={read_result.error_message}"
                        ),
                        paths=(call.evidence_dir / "call.json", material.hard_gate_result_path),
                    ),
                )
            if read_result.sha256 != material.l1_sha256 or read_result.size_bytes != material.l1_size_bytes:
                return PromptMaterialResult(
                    ok=False,
                    call=None,
                    failure=self._prompt_material_failure(
                        call=call,
                        reason=(
                            f"approved material L1 指纹不匹配: worker={material.worker_id} "
                            f"sha={read_result.sha256}/{material.l1_sha256} "
                            f"size={read_result.size_bytes}/{material.l1_size_bytes}"
                        ),
                        paths=(call.evidence_dir / "call.json", material.hard_gate_result_path),
                    ),
                )
            try:
                text = read_result.content.decode("utf-8")
            except UnicodeDecodeError as exc:
                return PromptMaterialResult(
                    ok=False,
                    call=None,
                    failure=self._prompt_material_failure(
                        call=call,
                        reason=f"approved material L1 不是 UTF-8 文本: worker={material.worker_id} ({exc})",
                        paths=(call.evidence_dir / "call.json", material.hard_gate_result_path),
                    ),
                )
            material_texts[(material.worker_id, material.stage)] = text
            ordered_material_texts.append(
                PromptMaterialText(
                    worker_id=material.worker_id,
                    stage=material.stage,
                    text=text,
                    turn_index=material.turn_index,
                    round_index=material.round_index,
                    role_turn_index=material.role_turn_index,
                )
            )

        prompt_vars = build_profile_prompt_vars(
            call=call,
            material_texts=material_texts,
            ordered_material_texts=tuple(ordered_material_texts),
        )
        return PromptMaterialResult(
            ok=True,
            call=replace(call, prompt_runtime_vars=prompt_vars),
            failure=None,
        )

    def run_single_worker(self, call: WorkerCall) -> WorkerResult:
        self.store.save_call(call)
        command = build_openclaw_command(call)
        openclaw_result = self.openclaw.run_worker(command)
        openclaw_result_path = self.store.save_openclaw_result(call, openclaw_result)

        if openclaw_result.status != "succeeded":
            reason = openclaw_result.failure_reason or f"openclaw status={openclaw_result.status}"
            return _failed_worker_result(
                call=call,
                category="openclaw_runtime",
                reason=reason,
                paths=(openclaw_result_path,),
                openclaw_result_path=openclaw_result_path,
            )

        evidence_result = self.read_worker_evidence(call, openclaw_result)
        if not evidence_result.ok or evidence_result.evidence is None:
            return _failed_worker_result(
                call=call,
                category="provider_evidence",
                reason=evidence_result.reason or "provider evidence 校验失败",
                paths=evidence_result.paths or (openclaw_result_path,),
                openclaw_result_path=openclaw_result_path,
            )
        evidence = evidence_result.evidence

        guard_result, guard_path = self.run_runtime_guards(call, evidence)
        if not guard_result.ok:
            guard_paths = _dedupe_paths((guard_path, *guard_result.paths))
            return _failed_worker_result(
                call=call,
                category=guard_result.category,
                reason=guard_result.reason or "runtime guards 失败",
                paths=guard_paths,
                openclaw_result_path=openclaw_result_path,
                early_stop=guard_result.early_stop,
            )

        if call.stop_after_first_response:
            return WorkerResult(
                run_id=call.run_id,
                call_id=call.call_id,
                worker_id=call.worker_id,
                stage=call.stage,
                status=WorkerStatus.SUCCEEDED,
                openclaw_result_path=openclaw_result_path,
                approved_material_id=None,
                failure=None,
                turn_index=call.turn_index,
                round_index=call.round_index,
                role_turn_index=call.role_turn_index,
            )

        approval_result = self.run_material_approval(call, evidence)
        if not approval_result.ok or approval_result.material is None:
            return _failed_worker_result(
                call=call,
                category=approval_result.category or "approval",
                reason=approval_result.reason or "approval 失败",
                paths=approval_result.paths or (guard_path,),
                openclaw_result_path=openclaw_result_path,
            )

        with self._manifest_write_lock:
            try:
                self.assert_manifest_not_written_before_guards(call.call_id, call.run_id)
            except ValueError as exc:
                return _failed_worker_result(
                    call=call,
                    category="artifact_flow_overreach",
                    reason=str(exc),
                    paths=(guard_path,),
                    openclaw_result_path=openclaw_result_path,
                    early_stop=True,
                )

            persist_approved_material_after_hard_gate(call=call, approval=approval_result, manifest_store=self.manifest_store)
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=openclaw_result_path,
            approved_material_id=approval_result.material.material_id,
            failure=None,
            turn_index=call.turn_index,
            round_index=call.round_index,
            role_turn_index=call.role_turn_index,
        )

    def read_worker_evidence(self, call: WorkerCall, result: OpenClawResult) -> EvidenceReadResult:
        if result.status != "succeeded":
            return EvidenceReadResult.failed(
                reason=f"openclaw_result.status 不是 succeeded: {result.status}",
                paths=(call.evidence_dir,),
            )
        return self.evidence_reader.require_provider_evidence(
            call=call,
            result=result,
            full_run=not call.stop_after_first_response,
        )

    def run_runtime_guards(self, call: WorkerCall, evidence: ProviderEvidence) -> tuple[GuardResult, Path]:
        with self._manifest_write_lock:
            manifest = self.manifest_store.load(call.run_id)
            checks = (
                validate_workspace_evidence(call, evidence, self.agents_root),
                validate_provider_request(call, evidence),
                validate_visible_tools(call, evidence),
                validate_tool_calls(call, evidence),
                self._validate_artifact_flow(call, manifest),
                validate_openviking_runtime_reads(call, evidence, manifest),
            )
        combined = combine_guard_results(checks)
        guard_path = self.store.save_guard_result(call, combined)
        return combined, guard_path

    def run_material_approval(self, call: WorkerCall, evidence: ProviderEvidence) -> ApprovalResult:
        # 材料批准权在 claw-trade；OpenClaw 成功不等于材料可进入 approved manifest。
        return approve_worker_material(call=call, evidence=evidence, openviking=self.openviking)

    def write_collect_first_report(
        self,
        batch: StageBatch,
        results: list[WorkerResult],
        failures: list[FailureRecord],
        early_stop_used: bool,
        early_stop_failures: tuple[FailureRecord, ...],
    ) -> Path:
        report_path = self.store.run_dir(batch.run_id) / "reports" / (
            f"collect-first-{batch.stage.value}-t{batch.turn_index:02d}.json"
        )
        completed_items: list[dict[str, object]] = []
        for result in results:
            if result.status != WorkerStatus.SUCCEEDED:
                continue
            completed_items.append(
                {
                    "worker_id": result.worker_id,
                    "call_id": result.call_id,
                    "status": result.status.value,
                    "approved_material_id": result.approved_material_id,
                    "turn_index": result.turn_index,
                    "round_index": result.round_index,
                    "role_turn_index": result.role_turn_index,
                }
            )
        failure_items = [
            {
                "worker_id": failure.worker_id,
                "call_id": failure.call_id,
                "category": failure.category,
                "reason": failure.reason,
                "evidence_paths": [str(path) for path in failure.evidence_paths],
                "turn_index": failure.turn_index,
                "round_index": failure.round_index,
                "role_turn_index": failure.role_turn_index,
            }
            for failure in failures
        ]
        exception_evidence = [
            {
                "early_stop_category": failure.category,
                "reason": failure.reason,
                "evidence_paths": [str(path) for path in failure.evidence_paths],
                "why_continue_is_blocked": "继续执行会污染证据链或扩大越权范围",
            }
            for failure in early_stop_failures
        ]
        payload = {
            "collect_first_compliance": {
                "batch_scope": {
                    "run_id": batch.run_id,
                    "stage": batch.stage.value,
                    "scope": batch.scope.value,
                    "worker_ids": list(batch.worker_ids),
                },
                "completed_items": completed_items,
                "failures_collected": failure_items,
                "early_stop_exception_used": bool(exception_evidence) and early_stop_used,
                "exception_evidence": exception_evidence,
                "batch_fix_grouping": self.build_batch_fix_grouping(failures),
            }
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return report_path

    def build_batch_fix_grouping(self, failures: list[FailureRecord]) -> list[dict[str, object]]:
        grouped: dict[str, list[FailureRecord]] = {}
        for failure in failures:
            grouped.setdefault(failure.category, []).append(failure)

        rows: list[dict[str, object]] = []
        for category, items in grouped.items():
            workers: list[str] = []
            for item in items:
                if item.worker_id and item.worker_id not in workers:
                    workers.append(item.worker_id)
            rows.append(
                {
                    "category": category,
                    "workers": workers,
                    "root_component_guess": _root_component_guess(category),
                }
            )
        return rows

    def assert_manifest_not_written_before_guards(self, call_id: str, run_id: str) -> None:
        call_dir = self.store.run_dir(run_id) / "calls" / call_id
        guard_path = call_dir / "guard-results.json"
        if not guard_path.exists():
            raise ValueError(f"guard-results.json 缺失，禁止写 approved manifest: {call_id}")
        manifest_path = self.store.run_dir(run_id) / "openviking" / "approved-manifest.json"
        if not manifest_path.exists():
            return
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        materials = payload.get("materials")
        if not isinstance(materials, list):
            return
        for item in materials:
            if isinstance(item, dict) and item.get("call_id") == call_id:
                raise ValueError(f"approved manifest 已存在 call_id={call_id}，顺序或去重异常")

    def assert_export_result_before_completed(self, state: WorkflowState) -> None:
        if state.status != RunStatus.REPORT_EXPORTING:
            return
        exported = self.store.load_export_result(state.run_id)
        if exported is None:
            raise ValueError("REPORT_EXPORTING 缺少 export-result.json，禁止 completed")
        if not export_result_allows_workflow_completion(state.request.profile, exported):
            raise ValueError(f"export-result.status={exported.status}，禁止 completed")

    def _validate_artifact_flow(self, call: WorkerCall, manifest: ApprovedManifest) -> GuardResult:
        return validate_artifact_flow(call, manifest)

    def _prompt_material_failure(
        self,
        call: WorkerCall,
        reason: str,
        paths: tuple[Path, ...],
    ) -> FailureRecord:
        return FailureRecord(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            category="prompt_materials",
            reason=reason,
            evidence_paths=paths,
            early_stop=True,
            human_action_required=None,
            turn_index=call.turn_index,
            round_index=call.round_index,
            role_turn_index=call.role_turn_index,
        )

    def _normalize_build_failure(
        self,
        batch: StageBatch,
        worker_id: str,
        build_result: RequestBuildResult,
    ) -> FailureRecord:
        blocked_call_id = _synthetic_blocked_call_id(batch.run_id, batch.stage, worker_id)
        if build_result.failure is None:
            return FailureRecord(
                run_id=batch.run_id,
                call_id=blocked_call_id,
                worker_id=worker_id,
                stage=batch.stage,
                category="blocked",
                reason="request_builder 返回失败但 failure 为空",
                evidence_paths=(self.store.run_dir(batch.run_id) / "request.json",),
                early_stop=True,
                human_action_required=None,
            )
        failure = build_result.failure
        if failure.call_id and failure.call_id.strip():
            return failure
        return FailureRecord(
            run_id=failure.run_id,
            call_id=blocked_call_id,
            worker_id=failure.worker_id or worker_id,
            stage=failure.stage or batch.stage,
            category=failure.category,
            reason=failure.reason,
            evidence_paths=failure.evidence_paths,
            early_stop=failure.early_stop,
            human_action_required=failure.human_action_required,
            turn_index=batch.turn_index,
            round_index=batch.round_index,
            role_turn_index=batch.role_turn_index,
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


def build_profile_prompt_vars(
    *,
    call: WorkerCall,
    material_texts: dict[tuple[str, Stage], str],
    ordered_material_texts: tuple[PromptMaterialText, ...] = (),
) -> dict[str, str]:
    # Profile-specific on purpose: US follows original TradingAgents prompt
    # boundaries, while CN_A follows TradingAgents-CN prompt boundaries.
    # Audit refs may include more materials than the model-visible variables.
    frontline_vars = {
        "market_research_report": material_texts.get(("market_analyst", Stage.FRONTLINE), ""),
        "sentiment_report": material_texts.get(("social_analyst", Stage.FRONTLINE), ""),
        "news_report": material_texts.get(("news_analyst", Stage.FRONTLINE), ""),
        "fundamentals_report": material_texts.get(("fundamental_analyst", Stage.FRONTLINE), ""),
    }
    debate_labels = {
        "bull_researcher": "Bull Analyst",
        "bear_researcher": "Bear Analyst",
    }
    risk_labels = {
        "risk_challenger": "Risky Analyst",
        "risk_guardian": "Safe Analyst",
        "risk_moderator": "Neutral Analyst",
    }
    debate_arguments = _ordered_role_arguments(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        stage=Stage.INVESTMENT_DEBATE,
        role_labels=debate_labels,
    )
    risk_arguments = _ordered_role_arguments(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        stage=Stage.RISK_DEBATE,
        role_labels=risk_labels,
    )
    bull_argument = _latest_role_argument(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        worker_id="bull_researcher",
        stage=Stage.INVESTMENT_DEBATE,
        role_label="Bull Analyst",
    )
    bear_argument = _latest_role_argument(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        worker_id="bear_researcher",
        stage=Stage.INVESTMENT_DEBATE,
        role_label="Bear Analyst",
    )
    risky_argument = _latest_role_argument(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        worker_id="risk_challenger",
        stage=Stage.RISK_DEBATE,
        role_label="Risky Analyst",
    )
    safe_argument = _latest_role_argument(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        worker_id="risk_guardian",
        stage=Stage.RISK_DEBATE,
        role_label="Safe Analyst",
    )
    neutral_argument = _latest_role_argument(
        material_texts=material_texts,
        ordered_material_texts=ordered_material_texts,
        worker_id="risk_moderator",
        stage=Stage.RISK_DEBATE,
        role_label="Neutral Analyst",
    )

    if call.worker_id == "bull_researcher":
        return {
            **frontline_vars,
            "history": _conversation_history(*debate_arguments),
            "current_response": debate_arguments[-1] if debate_arguments else "",
            "past_memory_str": _default_memory_for_worker(call.worker_id),
        }
    if call.worker_id == "bear_researcher":
        return {
            **frontline_vars,
            "history": _conversation_history(*debate_arguments),
            "current_response": debate_arguments[-1] if debate_arguments else bull_argument,
            "past_memory_str": _default_memory_for_worker(call.worker_id),
        }
    if call.worker_id == "research_manager":
        if call.profile == "US":
            return {
                "history": _conversation_history(*debate_arguments),
                "past_memory_str": _default_memory_for_worker(call.worker_id),
            }
        return {
            **frontline_vars,
            "history": _conversation_history(*debate_arguments),
            "past_memory_str": _default_memory_for_worker(call.worker_id),
        }
    if call.worker_id == "trader":
        return {
            "investment_plan": material_texts.get(("research_manager", Stage.INVESTMENT_DECISION), ""),
            "past_memory_str": _default_memory_for_worker(call.worker_id),
        }
    if call.worker_id == "risk_challenger":
        return {
            **frontline_vars,
            "trader_decision": material_texts.get(("trader", Stage.TRADE_DECISION), ""),
            "history": _conversation_history(*risk_arguments),
            "current_safe_response": "",
            "current_neutral_response": neutral_argument,
        }
    if call.worker_id == "risk_guardian":
        return {
            **frontline_vars,
            "trader_decision": material_texts.get(("trader", Stage.TRADE_DECISION), ""),
            "history": _conversation_history(*risk_arguments),
            "current_risky_response": risky_argument,
            "current_neutral_response": neutral_argument,
        }
    if call.worker_id == "risk_moderator":
        return {
            **frontline_vars,
            "trader_decision": material_texts.get(("trader", Stage.TRADE_DECISION), ""),
            "history": _conversation_history(*risk_arguments),
            "current_risky_response": risky_argument,
            "current_safe_response": safe_argument,
        }
    if call.worker_id == "portfolio_manager":
        return {
            "ticker": call.ticker,
            "currency": call.currency,
            "currency_symbol": call.currency_symbol,
            "research_plan": material_texts.get(("research_manager", Stage.INVESTMENT_DECISION), ""),
            "trader_plan": material_texts.get(("research_manager", Stage.INVESTMENT_DECISION), ""),
            "trader_decision": material_texts.get(("trader", Stage.TRADE_DECISION), ""),
            "history": _conversation_history(*risk_arguments),
            "past_memory_str": _default_memory_for_worker(call.worker_id),
        }
    if call.worker_id == "report_polisher":
        if call.profile == "US":
            supporting_sources = (
                ("bull_researcher", Stage.INVESTMENT_DEBATE, "Bull Researcher"),
                ("bear_researcher", Stage.INVESTMENT_DEBATE, "Bear Researcher"),
                ("research_manager", Stage.INVESTMENT_DECISION, "Research Manager"),
                ("risk_challenger", Stage.RISK_DEBATE, "Aggressive Risk Analyst"),
                ("risk_guardian", Stage.RISK_DEBATE, "Conservative Risk Analyst"),
                ("risk_moderator", Stage.RISK_DEBATE, "Neutral Risk Analyst"),
            )
            chart_assets_note = (
                "If the market analysis report generated verified technical charts, the final export will place "
                "those verified charts in the technical market analysis section; do not invent image paths or chart conclusions."
            )
        elif call.profile == "CRYPTO":
            supporting_sources = (
                ("bull_researcher", Stage.INVESTMENT_DEBATE, "看涨研究员"),
                ("bear_researcher", Stage.INVESTMENT_DEBATE, "看跌研究员"),
                ("research_manager", Stage.INVESTMENT_DECISION, "研究经理"),
                ("risk_challenger", Stage.RISK_DEBATE, "风险挑战方"),
                ("risk_guardian", Stage.RISK_DEBATE, "风险防守方"),
                ("risk_moderator", Stage.RISK_DEBATE, "风险整合方"),
            )
            chart_assets_note = "如市场分析报告已生成技术图表，最终导出会把已验证图表放入市场结构与技术指标分析段；不要编造图片路径或图表结论。"
        else:
            supporting_sources = (
                ("bull_researcher", Stage.INVESTMENT_DEBATE, "多头研究员"),
                ("bear_researcher", Stage.INVESTMENT_DEBATE, "空头研究员"),
                ("research_manager", Stage.INVESTMENT_DECISION, "研究经理"),
                ("risk_challenger", Stage.RISK_DEBATE, "风险挑战方"),
                ("risk_guardian", Stage.RISK_DEBATE, "风险防守方"),
                ("risk_moderator", Stage.RISK_DEBATE, "风险整合方"),
            )
            chart_assets_note = "如市场分析报告已生成技术图表，最终导出会把已验证图表放入技术指标分析段；不要编造图片路径或图表结论。"
        supporting_reports = _report_bundle(
            material_texts,
            supporting_sources,
            ordered_material_texts=ordered_material_texts,
        )
        return {
            "ticker": call.ticker,
            "company_name": call.company_name,
            "market": call.market,
            "currency": call.currency,
            "current_date": call.current_date,
            "start_date": call.start_date,
            "end_date": call.end_date,
            "portfolio_manager_report": material_texts.get(("portfolio_manager", Stage.PORTFOLIO_DECISION), ""),
            "market_analyst_report": material_texts.get(("market_analyst", Stage.FRONTLINE), ""),
            "fundamental_analyst_report": material_texts.get(("fundamental_analyst", Stage.FRONTLINE), ""),
            "news_analyst_report": material_texts.get(("news_analyst", Stage.FRONTLINE), ""),
            "social_analyst_report": material_texts.get(("social_analyst", Stage.FRONTLINE), ""),
            "trader_report": material_texts.get(("trader", Stage.TRADE_DECISION), ""),
            "supporting_worker_reports": supporting_reports,
            "chart_assets_note": chart_assets_note,
        }
    return {}


def _role_argument(
    material_texts: dict[tuple[str, Stage], str],
    worker_id: str,
    stage: Stage,
    role_label: str,
) -> str:
    text = material_texts.get((worker_id, stage), "")
    return f"{role_label}: {text}" if text else ""


def _latest_role_argument(
    *,
    material_texts: dict[tuple[str, Stage], str],
    ordered_material_texts: tuple[PromptMaterialText, ...],
    worker_id: str,
    stage: Stage,
    role_label: str,
) -> str:
    for item in reversed(ordered_material_texts):
        if item.worker_id == worker_id and item.stage == stage:
            return f"{role_label}: {item.text}" if item.text else ""
    return _role_argument(material_texts, worker_id, stage, role_label)


def _ordered_role_arguments(
    *,
    material_texts: dict[tuple[str, Stage], str],
    ordered_material_texts: tuple[PromptMaterialText, ...],
    stage: Stage,
    role_labels: dict[str, str],
) -> tuple[str, ...]:
    if ordered_material_texts:
        arguments: list[str] = []
        for item in ordered_material_texts:
            if item.stage != stage:
                continue
            role_label = role_labels.get(item.worker_id)
            if role_label is None or not item.text:
                continue
            arguments.append(f"{role_label}: {item.text}")
        return tuple(arguments)
    return tuple(
        argument
        for worker_id, role_label in role_labels.items()
        if (argument := _role_argument(material_texts, worker_id, stage, role_label))
    )


def _conversation_history(*arguments: str) -> str:
    return "".join(f"\n{argument}" for argument in arguments if argument)


def _report_bundle(
    material_texts: dict[tuple[str, Stage], str],
    sources: tuple[tuple[str, Stage, str], ...],
    *,
    ordered_material_texts: tuple[PromptMaterialText, ...] = (),
) -> str:
    if ordered_material_texts:
        source_titles = {(worker_id, stage): title for worker_id, stage, title in sources}
        source_counts: dict[tuple[str, Stage], int] = {}
        for item in ordered_material_texts:
            source = (item.worker_id, item.stage)
            if source in source_titles:
                source_counts[source] = source_counts.get(source, 0) + 1
        sections: list[str] = []
        for item in ordered_material_texts:
            source = (item.worker_id, item.stage)
            title = source_titles.get(source)
            if title is None:
                continue
            text = item.text.strip()
            if not text:
                continue
            if source_counts.get(source, 0) > 1 or item.round_index > 1:
                title = f"{title}（第{item.round_index}轮）"
            sections.append(f"### {title}\n{text}")
        return "\n\n".join(sections)

    sections: list[str] = []
    for worker_id, stage, title in sources:
        text = material_texts.get((worker_id, stage), "").strip()
        if not text:
            continue
        sections.append(f"### {title}\n{text}")
    return "\n\n".join(sections)


def _default_memory_for_worker(worker_id: str) -> str:
    _ = worker_id
    return ""


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
    # collect-first 即使继续收集，其间触发的早停条件也必须被保留下来，避免误判为可继续推进。
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


def persist_approved_material_after_hard_gate(
    call: WorkerCall,
    approval: ApprovalResult,
    manifest_store: ManifestStore,
) -> Path | None:
    if not approval.ok or approval.material is None:
        return None
    material = approval.material
    if material.run_id != call.run_id or material.call_id != call.call_id:
        raise ValueError("approval.material 与当前 call 不一致")
    hard_gate_path = material.hard_gate_result_path
    hard_gate_path.parent.mkdir(parents=True, exist_ok=True)
    hard_gate_payload = {
        "ok": True,
        "status": "passed",
        "category": "combined_hard_gate",
        "run_id": call.run_id,
        "call_id": call.call_id,
        "worker_id": call.worker_id,
        "stage": call.stage.value,
        "written_at": _utc_now_iso_text(),
        "paths": [str(path) for path in approval.paths],
    }
    hard_gate_path.write_text(
        json.dumps(hard_gate_payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    # manifest 只能在 hard gate 证据落盘之后写入，防止出现“先入 manifest 后补证据”的伪成功顺序。
    manifest_store.add(call.run_id, material)
    return hard_gate_path


def _failed_worker_result(
    *,
    call: WorkerCall,
    category: str,
    reason: str,
    paths: tuple[Path, ...],
    openclaw_result_path: Path | None,
    early_stop: bool = False,
) -> WorkerResult:
    return WorkerResult(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        status=WorkerStatus.BLOCKED if category == "blocked" else WorkerStatus.FAILED,
        openclaw_result_path=openclaw_result_path,
        approved_material_id=None,
        failure=FailureRecord(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            category=category,
            reason=reason,
            evidence_paths=paths,
            early_stop=early_stop,
            human_action_required=None,
            turn_index=call.turn_index,
            round_index=call.round_index,
            role_turn_index=call.role_turn_index,
        ),
        turn_index=call.turn_index,
        round_index=call.round_index,
        role_turn_index=call.role_turn_index,
    )


def _dedupe_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    out: list[Path] = []
    for path in paths:
        if path not in out:
            out.append(path)
    return tuple(out)


def _probe_ok(probe: object) -> bool:
    if isinstance(probe, bool):
        return probe
    value = getattr(probe, "ok", None)
    return bool(value is True)


def _probe_reason(probe: object, default_reason: str) -> str:
    if isinstance(probe, dict):
        reason = probe.get("reason")
        return str(reason) if reason is not None else default_reason
    reason = getattr(probe, "reason", None)
    return str(reason) if reason is not None else default_reason


def _synthetic_blocked_call_id(run_id: str, stage: Stage, worker_id: str) -> str:
    return f"{run_id}-{stage.value}-{worker_id}-blocked"


def _root_component_guess(category: str) -> str:
    mapping = {
        "provider_evidence": "OpenClaw evidence capture",
        "provider_request": "OpenClaw provider request capture",
        "visible_tools": "OpenClaw visible tools capture",
        "tool_calls": "OpenClaw tool calls capture",
        "workspace_evidence": "OpenClaw workspace evidence capture",
        "openviking_runtime_reads": "OpenViking capability runtime guard",
        "openviking_receipt": "OpenViking receipt verification",
        "openviking_integrity": "OpenViking read/stat integrity",
        "artifact_flow_overreach": "approved manifest artifact flow",
        "claim": "L1/L2 claim hard gate",
        "openclaw_runtime": "OpenClaw worker runtime",
    }
    return mapping.get(category, "runner runtime pipeline")


def _utc_now_iso_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
