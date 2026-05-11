from __future__ import annotations

import json
from pathlib import Path

from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Index, make_material_target
from claw_trade.guards.common import ApprovalResult, BootResult, GuardResult
from claw_trade.runtime.evidence_reader import EvidenceReadResult, OpenClawResult, ProviderEvidence
from claw_trade.workflow.models import (
    BatchScope,
    FailureRecord,
    ReadPolicy,
    RunRequest,
    Stage,
    StageBatch,
    StopPoint,
    WorkerCall,
    WorkerResult,
    WorkerStatus,
)
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore


class _Probe:
    def __init__(self, ok: bool = True, reason: str | None = None) -> None:
        self.ok = ok
        self.reason = reason


class _OpenClaw:
    def __init__(self) -> None:
        self.probe_result = _Probe(True)
        self.run_result = OpenClawResult(
            status="succeeded",
            openclaw_run_id="oc-run-1",
            provider_request_id="req-1",
            provider_request_id_status="returned",
            workspace_evidence_path=None,
            provider_request_path=None,
            visible_tools_path=None,
            first_response_path=None,
            tool_calls_status=None,
            tool_calls_path=None,
            raw_output_path=None,
            openviking_receipt_path=None,
            failure_reason=None,
        )

    def probe(self) -> _Probe:
        return self.probe_result

    def run_worker(self, command: object) -> OpenClawResult:
        _ = command
        return self.run_result


class _OpenViking:
    def probe_read_stat_receipt(self) -> _Probe:
        return _Probe(True)

    def ensure_namespace(self, namespace: str) -> None:
        _ = namespace


class _ToolRegistryProbe:
    def probe(self) -> BootResult:
        return BootResult.ok_result()


class _RequestBuilder:
    def build_worker_call(  # type: ignore[no-untyped-def]
        self,
        state,
        worker_id,
        stage,
        manifest,
    ):
        _ = manifest
        call_id = f"{state.run_id}-{stage.value}-{worker_id}-1"
        target = make_material_target(state.run_id, stage, worker_id, call_id)
        return type(
            "R",
            (),
            {
                "ok": True,
                "call": WorkerCall(
                    call_id=call_id,
                    run_id=state.run_id,
                    worker_id=worker_id,
                    stage=stage,
                    profile="US",
                    ticker="AAPL",
                    company_name="Apple",
                    market="US",
                    currency="USD",
                    currency_symbol="$",
                    current_date="2026-05-04",
                    start_date="2026-04-04",
                    end_date="2026-05-04",
                    allowed_tools=("market_data", "openviking_write_material"),
                    upstream_materials=(),
                    openviking_read_capabilities=(),
                    material_target=target,
                    read_policy=ReadPolicy(),
                    evidence_dir=state.run_dir / "calls" / call_id,
                    stop_after_first_response=False,
                ),
                "failure": None,
            },
        )()


class _RecordingStore(WorkflowStore):
    def __init__(self, root: Path, timeline: list[str]) -> None:
        super().__init__(root)
        self.events: list[str] = []
        self.timeline = timeline

    def save_call(self, call: WorkerCall) -> Path:
        self.events.append("save_call")
        self.timeline.append("save_call")
        return super().save_call(call)

    def save_openclaw_result(self, call: WorkerCall, result: OpenClawResult) -> Path:
        self.events.append("save_openclaw_result")
        self.timeline.append("save_openclaw_result")
        return super().save_openclaw_result(call, result)

    def save_guard_result(self, call: WorkerCall, guard: GuardResult) -> Path:
        self.events.append("save_guard_result")
        self.timeline.append("save_guard_result")
        return super().save_guard_result(call, guard)

    def save_worker_result(self, result: WorkerResult) -> Path:
        self.events.append("save_worker_result")
        self.timeline.append("save_worker_result")
        return super().save_worker_result(result)


class _RecordingManifestStore(ManifestStore):
    def __init__(self, root_dir: Path, timeline: list[str]) -> None:
        super().__init__(root_dir=root_dir)
        self.events: list[str] = []
        self.timeline = timeline

    def add(self, run_id: str, material: ApprovedMaterial) -> None:
        self.events.append("manifest_add")
        self.timeline.append("manifest_add")
        super().add(run_id, material)


def test_collect_first_collects_multiple_non_early_stop_failures(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "runs")
    state = store.create_run(_request())
    runner = ControlRunner(
        store=store,
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        request_builder=_RequestBuilder(),
        tool_registry_probe=_ToolRegistryProbe(),
    )
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("market_analyst", "fundamental_analyst", "news_analyst"),
        scope=BatchScope.FULL_STAGE,
        collect_first=True,
        stop_point=StopPoint.NONE,
    )

    def _result_by_worker(call: WorkerCall) -> WorkerResult:
        if call.worker_id in {"market_analyst", "fundamental_analyst"}:
            return WorkerResult(
                run_id=call.run_id,
                call_id=call.call_id,
                worker_id=call.worker_id,
                stage=call.stage,
                status=WorkerStatus.FAILED,
                openclaw_result_path=None,
                approved_material_id=None,
                failure=_failure(call, "provider_evidence", "missing provider evidence"),
            )
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id=None,
            failure=None,
        )

    runner.run_single_worker = _result_by_worker  # type: ignore[method-assign]
    result = runner.run_stage_batch(state, batch)

    assert len(result.worker_results) == 3
    assert len(result.failures) == 2
    assert result.early_stop_used is False

    payload = json.loads(result.collect_first_report_path.read_text(encoding="utf-8"))
    compliance = payload["collect_first_compliance"]
    assert compliance["early_stop_exception_used"] is False
    assert len(compliance["failures_collected"]) == 2
    assert compliance["batch_fix_grouping"][0]["category"] == "provider_evidence"


def test_collect_first_early_stop_writes_exception_evidence(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "runs")
    state = store.create_run(_request())
    runner = ControlRunner(
        store=store,
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        request_builder=_RequestBuilder(),
        tool_registry_probe=_ToolRegistryProbe(),
    )
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("market_analyst", "fundamental_analyst"),
        scope=BatchScope.FULL_STAGE,
        collect_first=True,
        stop_point=StopPoint.NONE,
    )

    def _always_early_stop(call: WorkerCall) -> WorkerResult:
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.FAILED,
            openclaw_result_path=None,
            approved_material_id=None,
            failure=_failure(call, "artifact_flow_overreach", "artifact flow mismatch"),
        )

    runner.run_single_worker = _always_early_stop  # type: ignore[method-assign]
    result = runner.run_stage_batch(state, batch)
    payload = json.loads(result.collect_first_report_path.read_text(encoding="utf-8"))
    compliance = payload["collect_first_compliance"]

    assert result.early_stop_used is True
    assert compliance["early_stop_exception_used"] is True
    assert len(compliance["exception_evidence"]) == 1
    assert compliance["exception_evidence"][0]["early_stop_category"] == "artifact_flow_overreach"


def test_full_worker_order_is_call_openclaw_guard_manifest_then_result(tmp_path: Path) -> None:
    timeline: list[str] = []
    store = _RecordingStore(tmp_path / "runs", timeline)
    manifest_store = _RecordingManifestStore(tmp_path / "runs", timeline)
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        request_builder=_RequestBuilder(),
        tool_registry_probe=_ToolRegistryProbe(),
    )
    state = store.create_run(_request())
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("market_analyst",),
        scope=BatchScope.SINGLE_WORKER,
        collect_first=False,
        stop_point=StopPoint.SINGLE_WORKER_COMPLETE,
    )

    def _pass_evidence(call: WorkerCall, result: OpenClawResult) -> EvidenceReadResult:
        _ = result
        evidence = ProviderEvidence(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            openclaw_run_id="oc-run-1",
            provider_request_id="req-1",
            provider_request_id_status="returned",
            workspace_evidence_path=call.evidence_dir / "workspace-evidence.json",
            provider_request_path=call.evidence_dir / "provider-request.json",
            visible_tools_path=call.evidence_dir / "visible-tools.json",
            first_response_path=call.evidence_dir / "first-response.json",
            tool_calls_status="recorded",
            tool_calls_path=call.evidence_dir / "tool-calls.json",
            raw_output_path=call.evidence_dir / "raw-output.md",
            openviking_receipt_path=call.evidence_dir / "openviking-receipt.json",
        )
        return EvidenceReadResult.passed(evidence)

    def _pass_guards(call: WorkerCall, evidence: ProviderEvidence) -> tuple[GuardResult, Path]:
        _ = evidence
        guard = GuardResult.passed("ok")
        return guard, store.save_guard_result(call, guard)

    def _pass_approval(call: WorkerCall, evidence: ProviderEvidence) -> ApprovalResult:
        _ = evidence
        material = ApprovedMaterial(
            material_id=f"mat-{call.call_id}",
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            target_name=call.material_target.target_name,
            l1_uri=call.material_target.l1_uri,
            l1_sha256="a" * 64,
            l1_size_bytes=1,
            l2_index_uri=f"{call.material_target.l2_prefix}index.json",
            l2_index=L2Index(
                entries=(),
                empty_reason="none",
                index_uri=f"{call.material_target.l2_prefix}index.json",
                index_sha256="b" * 64,
                index_size_bytes=2,
            ),
            l1_claims=(
                L1Claim(
                    claim_id="claim-1",
                    kind="source_claim",
                    text="ok",
                    value=None,
                    required_evidence_kinds=(),
                    evidence_ids=(),
                ),
            ),
            approved_at="2026-05-04T12:00:00Z",
            hard_gate_result_path=call.evidence_dir / "approval-hard-gate.json",
        )
        return ApprovalResult.ok_result(material)

    runner.read_worker_evidence = _pass_evidence  # type: ignore[method-assign]
    runner.run_runtime_guards = _pass_guards  # type: ignore[method-assign]
    runner.run_material_approval = _pass_approval  # type: ignore[method-assign]

    result = runner.run_stage_batch(state, batch)

    assert result.worker_results[0].status == WorkerStatus.SUCCEEDED
    assert store.events == [
        "save_call",
        "save_openclaw_result",
        "save_guard_result",
        "save_worker_result",
    ]
    assert manifest_store.events == ["manifest_add"]
    assert timeline == [
        "save_call",
        "save_openclaw_result",
        "save_guard_result",
        "manifest_add",
        "save_worker_result",
    ]


def _failure(call: WorkerCall, category: str, reason: str) -> FailureRecord:
    return FailureRecord(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        category=category,
        reason=reason,
        evidence_paths=(call.evidence_dir / "failure.json",),
        early_stop=False,
        human_action_required=None,
    )


def _request() -> RunRequest:
    return RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-04-04",
        end_date="2026-05-04",
        stop_point=StopPoint.NONE,
    )
