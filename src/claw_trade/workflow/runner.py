from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Callable, Protocol

from claw_trade.artifacts.approval import approve_worker_material
from claw_trade.artifacts.manifest import ApprovedManifest, ManifestStore
from claw_trade.config.profiles import require_profile
from claw_trade.config.tool_names import load_tool_registry
from claw_trade.guards.common import (
    ApprovalResult,
    BootResult,
    GuardResult,
    combine_guard_results,
    guard_failed,
    guard_passed,
    should_early_stop,
)
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
)
from claw_trade.workflow.store import WorkflowStore


class OpenClawClientLike(Protocol):
    def probe(self) -> object: ...

    def run_worker(self, command: object) -> OpenClawResult: ...


class OpenVikingClientLike(Protocol):
    def probe_read_stat_receipt(self) -> object: ...

    def probe_namespace_stat(self) -> object: ...

    def ensure_namespace(self, namespace: str) -> None: ...


class RequestBuilderLike(Protocol):
    def build_worker_call(
        self,
        state: WorkflowState,
        worker_id: str,
        stage: Stage,
        manifest: ApprovedManifest,
    ) -> RequestBuildResult: ...


class ToolRegistryProbeLike(Protocol):
    def probe(self) -> BootResult: ...


class ExporterLike(Protocol):
    def export(self, state: WorkflowState, manifest: ApprovedManifest) -> ExportResult: ...


class _DefaultRequestBuilder:
    def build_worker_call(
        self,
        state: WorkflowState,
        worker_id: str,
        stage: Stage,
        manifest: ApprovedManifest,
    ) -> RequestBuildResult:
        return build_worker_call(state=state, worker_id=worker_id, stage=stage, manifest=manifest)


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
            if exported.status != "passed":
                failure = exported.failure or FailureRecord(
                    run_id=exporting.run_id,
                    call_id=None,
                    worker_id=None,
                    stage=Stage.PORTFOLIO_DECISION,
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
                    stage=Stage.PORTFOLIO_DECISION,
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
        worker_results: list[WorkerResult] = []
        failures: list[FailureRecord] = []
        early_stop_used = False
        early_stop_failures: list[FailureRecord] = []

        # collect-first 语义在这里执行：同阶段可恢复失败继续收集，命中早停类再中止。
        for worker_id in batch.worker_ids:
            call_result = self.request_builder.build_worker_call(
                state=state,
                worker_id=worker_id,
                stage=batch.stage,
                manifest=self.manifest_store.load(batch.run_id),
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

            call = call_result.call
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
        report_path = self.store.run_dir(batch.run_id) / "reports" / f"collect-first-{batch.stage.value}.json"
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
                }
            )
        failure_items = [
            {
                "worker_id": failure.worker_id,
                "call_id": failure.call_id,
                "category": failure.category,
                "reason": failure.reason,
                "evidence_paths": [str(path) for path in failure.evidence_paths],
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
        if exported.status != "passed":
            raise ValueError(f"export-result.status={exported.status}，禁止 completed")

    def _validate_artifact_flow(self, call: WorkerCall, manifest: ApprovedManifest) -> GuardResult:
        # 边界防线：下游只允许读取 approved manifest 发放的材料引用和 capability。
        if call.stage == Stage.FRONTLINE:
            if call.upstream_materials or call.openviking_read_capabilities:
                return guard_failed(
                    category="artifact_flow_overreach",
                    reason="frontline 不允许携带 upstream_materials/openviking_read_capabilities",
                    paths=(call.evidence_dir / "call.json",),
                    early_stop=True,
                )
            return guard_passed(category="artifact_flow")
        try:
            expected_refs = manifest.for_downstream_stage(call.stage, run_id=call.run_id)
            expected_caps = manifest.capabilities_for_downstream_stage(call.stage, run_id=call.run_id)
        except Exception as exc:
            return guard_failed(
                category="artifact_flow_overreach",
                reason=f"manifest 下游引用加载失败: {exc}",
                paths=(call.evidence_dir / "call.json",),
                early_stop=True,
            )
        if tuple(call.upstream_materials) != tuple(expected_refs):
            return guard_failed(
                category="artifact_flow_overreach",
                reason="upstream_materials 与 approved manifest 不一致",
                paths=(call.evidence_dir / "call.json",),
                early_stop=True,
            )
        if tuple(call.openviking_read_capabilities) != tuple(expected_caps):
            return guard_failed(
                category="artifact_flow_overreach",
                reason="openviking_read_capabilities 与 approved manifest 不一致",
                paths=(call.evidence_dir / "call.json",),
                early_stop=True,
            )
        return guard_passed(category="artifact_flow")

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
        ),
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
        "pm_owner": "portfolio manager ownership guard",
        "openclaw_runtime": "OpenClaw worker runtime",
    }
    return mapping.get(category, "runner runtime pipeline")


def _utc_now_iso_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
