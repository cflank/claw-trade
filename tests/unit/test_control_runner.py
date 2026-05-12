from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Index, make_material_target
from claw_trade.guards.common import ApprovalResult, BootResult, GuardResult
from claw_trade.runtime.evidence_reader import EvidenceReadResult, OpenClawResult, ProviderEvidence
from claw_trade.workflow.models import (
    Decision,
    DecisionKind,
    ExportResult,
    RunRequest,
    RunStatus,
    Stage,
    StopPoint,
    WorkerCall,
    WorkerStatus,
    WorkflowState,
    ReadPolicy,
)
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore


class _Probe:
    def __init__(self, ok: bool, reason: str | None = None) -> None:
        self.ok = ok
        self.reason = reason


class _OpenClaw:
    def __init__(self) -> None:
        self.probe_result = _Probe(True)
        self.next_result = OpenClawResult(
            status="failed",
            openclaw_run_id=None,
            provider_request_id=None,
            provider_request_id_status=None,
            workspace_evidence_path=None,
            provider_request_path=None,
            visible_tools_path=None,
            first_response_path=None,
            tool_calls_status=None,
            tool_calls_path=None,
            raw_output_path=None,
            openviking_receipt_path=None,
            failure_reason="runtime error",
        )

    def probe(self) -> _Probe:
        return self.probe_result

    def run_worker(self, command: object) -> OpenClawResult:
        _ = command
        return self.next_result


class _OpenViking:
    def __init__(self) -> None:
        self.probe_result = _Probe(True)
        self.namespace_probe_result = _Probe(True)
        self.probe_read_stat_receipt_calls = 0
        self.probe_namespace_stat_calls = 0
        self.ensure_namespace_calls: list[str] = []

    def probe_read_stat_receipt(self) -> _Probe:
        self.probe_read_stat_receipt_calls += 1
        return self.probe_result

    def probe_namespace_stat(self) -> _Probe:
        self.probe_namespace_stat_calls += 1
        return self.namespace_probe_result

    def ensure_namespace(self, namespace: str) -> None:
        self.ensure_namespace_calls.append(namespace)


class _ToolRegistryProbe:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def probe(self) -> BootResult:
        if self.ok:
            return BootResult.ok_result()
        return BootResult.blocked("tool_registry", "tool registry down")


class _Exporter:
    def __init__(self) -> None:
        self.decision_files_seen = False

    def export(self, state: WorkflowState, manifest) -> ExportResult:  # type: ignore[no-untyped-def]
        _ = manifest
        decision_dir = state.run_dir / "decisions"
        self.decision_files_seen = decision_dir.exists() and any(decision_dir.glob("*.json"))
        return ExportResult.failed(
            state=state,
            category="export_blocked",
            reason="b11 未实现",
            paths=(state.run_dir / "reports",),
        )


class _AssetFailExporter:
    def export(self, state: WorkflowState, manifest) -> ExportResult:  # type: ignore[no-untyped-def]
        _ = manifest
        return ExportResult.failed(
            state=state,
            category="export_report_assets",
            reason="报告导出失败：未找到可复制的图表资产",
            paths=(state.run_dir / "calls",),
        )


class _RunnerHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "runs"
        self.store = WorkflowStore(self.root)
        self.manifest_store = ManifestStore(self.root)
        self.openclaw = _OpenClaw()
        self.openviking = _OpenViking()
        self.tool_registry = _ToolRegistryProbe(ok=True)
        self.runner = ControlRunner(
            store=self.store,
            manifest_store=self.manifest_store,
            openclaw=self.openclaw,
            openviking=self.openviking,
            tool_registry_probe=self.tool_registry,
            now_text=lambda: "2026-05-04T12:00:00Z",
        )


def test_boot_profile_blocked_writes_failed_state_without_runtime_evidence(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    request = _request(profile="HK")

    state = harness.runner.run(request)

    assert state.status == RunStatus.FAILED
    call_results = list((state.run_dir / "calls").glob("*/openclaw-result.json"))
    assert call_results == []
    assert harness.openviking.ensure_namespace_calls == []


def test_run_saves_decision_before_export_action(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    exporter = _Exporter()
    harness.runner.exporter = exporter

    def _decide_once(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.EXPORT_REPORT, next_status=RunStatus.REPORT_EXPORTING)

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _decide_once)
    state = harness.runner.run(_request())

    assert state.status == RunStatus.FAILED
    assert exporter.decision_files_seen is True


def test_report_exporting_complete_requires_passed_export_result(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    decisions = iter(
        (
            Decision(kind=DecisionKind.ADVANCE, next_status=RunStatus.REPORT_EXPORTING),
            Decision(kind=DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED),
        )
    )

    def _decide_sequence(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return next(decisions)

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _decide_sequence)
    state = harness.runner.run(_request())

    assert state.status == RunStatus.FAILED
    assert "REPORT_EXPORTING 缺少 export-result.json" in (state.failure_reason or "")


def test_cn_a_export_asset_failure_does_not_fail_workflow(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    harness.runner.exporter = _AssetFailExporter()
    decisions = iter(
        (
            Decision(kind=DecisionKind.EXPORT_REPORT, next_status=RunStatus.REPORT_EXPORTING),
            Decision(kind=DecisionKind.COMPLETE, next_status=RunStatus.COMPLETED),
        )
    )

    def _decide_sequence(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return next(decisions)

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _decide_sequence)
    state = harness.runner.run(_request(profile="CN_A"))

    assert state.status == RunStatus.COMPLETED
    exported = harness.store.load_export_result(state.run_id)
    assert exported is not None
    assert exported.status == "failed"
    assert exported.failure is not None
    assert exported.failure.category == "export_report_assets"


def test_single_worker_openclaw_failed_does_not_read_evidence(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    call = _worker_call(tmp_path, run_id="run-1", call_id="call-1")

    def _boom_read(call: WorkerCall, result: OpenClawResult) -> EvidenceReadResult:
        del call, result
        raise AssertionError("openclaw failed 时不应读取 evidence")

    harness.runner.read_worker_evidence = _boom_read  # type: ignore[method-assign]
    result = harness.runner.run_single_worker(call)

    assert result.status == WorkerStatus.FAILED
    assert result.failure is not None
    assert result.failure.category == "openclaw_runtime"


def test_boot_first_response_uses_receipt_free_openviking_probe(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    harness.openviking.probe_result = _Probe(False, "receipt path missing")
    request = replace(_request(), stop_point=StopPoint.FIRST_RESPONSE)

    boot = harness.runner.boot(request)

    assert boot.ok is True
    assert harness.openviking.probe_namespace_stat_calls == 1
    assert harness.openviking.probe_read_stat_receipt_calls == 0


def test_first_response_success_does_not_write_manifest(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(_request())
    call = _worker_call(tmp_path, run_id=state.run_id, call_id="call-fr")
    call = replace(call, stop_after_first_response=True)

    harness.openclaw.next_result = OpenClawResult(
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
        raw_output_path=None,
        openviking_receipt_path=None,
    )
    harness.runner.read_worker_evidence = lambda c, r: EvidenceReadResult.passed(evidence)  # type: ignore[method-assign]
    harness.runner.run_runtime_guards = lambda c, e: (  # type: ignore[method-assign]
        GuardResult.passed("ok"),
        harness.store.save_guard_result(c, GuardResult.passed("ok")),
    )
    harness.runner.run_material_approval = lambda c, e: _approval_ok(c)  # type: ignore[method-assign]

    result = harness.runner.run_single_worker(call)

    assert result.status.name == "SUCCEEDED"
    assert result.approved_material_id is None
    manifest_payload = json.loads((harness.root / call.run_id / "openviking" / "approved-manifest.json").read_text("utf-8"))
    assert manifest_payload.get("audit_only") is True
    assert manifest_payload.get("materials") == []


def _request(profile: str = "US") -> RunRequest:
    return RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile=profile,
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-04-04",
        end_date="2026-05-04",
        stop_point=StopPoint.NONE,
        target_worker_id=None,
        target_stage=None,
    )


def _worker_call(tmp_path: Path, run_id: str, call_id: str) -> WorkerCall:
    target = make_material_target(run_id, Stage.FRONTLINE, "market_analyst", call_id)
    evidence_dir = tmp_path / "runs" / run_id / "calls" / call_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return WorkerCall(
        call_id=call_id,
        run_id=run_id,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
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
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )


def _approval_ok(call: WorkerCall) -> ApprovalResult:
    return ApprovalResult.ok_result(
        ApprovedMaterial(
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
    )
