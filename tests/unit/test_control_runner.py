from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.artifacts.openviking_client import OpenVikingReadResult
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Index, make_material_target
from claw_trade.guards.common import ApprovalResult, BootResult, GuardResult
from claw_trade.runtime.evidence_reader import EvidenceReadResult, OpenClawResult, ProviderEvidence
from claw_trade.workflow.models import (
    BatchScope,
    Decision,
    DecisionKind,
    ExportResult,
    ReadPolicy,
    RunRequest,
    RunStatus,
    Stage,
    StageBatch,
    StageBatchResult,
    StopPoint,
    WorkerCall,
    WorkerResult,
    WorkerStatus,
    WorkflowEntryPoint,
    WorkflowState,
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
        self.texts: dict[str, bytes] = {}
        self.read_material_ids: list[str] = []

    def probe_read_stat_receipt(self) -> _Probe:
        self.probe_read_stat_receipt_calls += 1
        return self.probe_result

    def probe_namespace_stat(self) -> _Probe:
        self.probe_namespace_stat_calls += 1
        return self.namespace_probe_result

    def ensure_namespace(self, namespace: str) -> None:
        self.ensure_namespace_calls.append(namespace)

    def read_approved_l1(self, material: ApprovedMaterial) -> OpenVikingReadResult:
        self.read_material_ids.append(material.material_id)
        content = self.texts[material.material_id]
        return OpenVikingReadResult(
            uri=material.l1_uri,
            ok=True,
            content=content,
            sha256=sha256(content).hexdigest(),
            size_bytes=len(content),
            error_category=None,
            error_message=None,
        )


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


class _PassedExporter:
    def export(self, state: WorkflowState, manifest) -> ExportResult:  # type: ignore[no-untyped-def]
        _ = manifest
        reports_dir = state.run_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "final-report.md"
        guard_path = reports_dir / "export-guard-results.json"
        report_path.write_text("# report\n", encoding="utf-8")
        guard_path.write_text("{}", encoding="utf-8")
        return ExportResult.passed(state=state, final_report_path=report_path, guard_path=guard_path)


class _LineageWriter:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    def link_after_export(self, *, state: WorkflowState, manifest, export_result: ExportResult):  # type: ignore[no-untyped-def]
        self.calls += 1
        _ = (state, manifest, export_result)
        if self.ok:
            return _LineageWriteResult(ok=True, category=None, reason=None, paths=(state.run_dir / "openviking" / "lineage.json",))
        return _LineageWriteResult(
            ok=False,
            category="openviking_lineage",
            reason="relations API unavailable",
            paths=(state.run_dir / "openviking" / "approved-manifest.json",),
        )


@dataclass(frozen=True)
class _LineageWriteResult:
    ok: bool
    category: str | None
    reason: str | None
    paths: tuple[Path, ...]


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


class _DataPrefetcher:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[WorkflowState] = []

    def prefetch_report(self, state: WorkflowState):
        self.calls.append(state)
        return self.result


def test_report_command_prefetches_data_before_controller_dispatch(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    prefetcher = _DataPrefetcher({"ok": True, "evidence_paths": (str(tmp_path / "prefetch.json"),)})
    harness.runner.data_prefetcher = prefetcher

    def _wait_after_prefetch(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.WAIT)

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _wait_after_prefetch)

    state = harness.runner.run(replace(_request(), entry_point=WorkflowEntryPoint.REPORT_COMMAND))

    assert state.status == RunStatus.CREATED
    assert len(prefetcher.calls) == 1
    assert prefetcher.calls[0].run_id == state.run_id
    assert harness.openviking.ensure_namespace_calls == [state.openviking_namespace]


def test_report_command_prefetch_failure_fails_before_worker_dispatch(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    prefetch_path = tmp_path / "prefetch-failed.json"
    prefetcher = _DataPrefetcher(
        {
            "ok": False,
            "category": "data_prefetch",
            "reason": "rate_limited",
            "evidence_paths": (str(prefetch_path),),
        }
    )
    harness.runner.data_prefetcher = prefetcher
    controller_calls = 0

    def _controller_should_not_run(input) -> Decision:  # type: ignore[no-untyped-def]
        nonlocal controller_calls
        controller_calls += 1
        del input
        return Decision(kind=DecisionKind.WAIT)

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _controller_should_not_run)

    state = harness.runner.run(replace(_request(), entry_point=WorkflowEntryPoint.REPORT_COMMAND))

    assert state.status == RunStatus.FAILED
    assert "data_prefetch: rate_limited" in (state.failure_reason or "")
    assert len(prefetcher.calls) == 1
    assert controller_calls == 0
    failure_files = sorted((state.run_dir / "failures").glob("*.json"))
    assert len(failure_files) == 1
    failure_payload = json.loads(failure_files[0].read_text(encoding="utf-8"))
    assert failure_payload["evidence_paths"] == [str(prefetch_path)]


def test_crypto_market_worker_reaches_openclaw_with_compact_market_pack(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    request = replace(
        _request(profile="CRYPTO"),
        ticker="BTC",
        company_name="Bitcoin",
        market="CRYPTO",
        currency="USDT",
        currency_symbol="USDT",
        stop_point=StopPoint.SINGLE_WORKER_COMPLETE,
        target_worker_id="market_analyst",
        target_stage=Stage.FRONTLINE,
    )

    state = harness.runner.run(request)

    assert state.status == RunStatus.FAILED
    call_results = list((state.run_dir / "calls").glob("*/openclaw-result.json"))
    assert len(call_results) == 1
    call_payload = json.loads((call_results[0].parent / "call.json").read_text(encoding="utf-8"))
    assert call_payload["profile"] == "CRYPTO"
    assert call_payload["allowed_tools"] == ["claw_get_market_pack"]
    assert harness.openviking.ensure_namespace_calls == [state.openviking_namespace]


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


def test_passed_export_calls_openviking_lineage_writer(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    lineage = _LineageWriter(ok=True)
    harness.runner.exporter = _PassedExporter()
    harness.runner.lineage_writer = lineage
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
    state = harness.runner.run(_request())

    assert state.status == RunStatus.COMPLETED
    assert lineage.calls == 1


def test_openviking_lineage_failure_blocks_completion(monkeypatch, tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    lineage = _LineageWriter(ok=False)
    harness.runner.exporter = _PassedExporter()
    harness.runner.lineage_writer = lineage

    def _decide_once(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.EXPORT_REPORT, next_status=RunStatus.REPORT_EXPORTING)

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _decide_once)
    state = harness.runner.run(_request())

    assert state.status == RunStatus.FAILED
    assert lineage.calls == 1
    assert "openviking_lineage" in (state.failure_reason or "")


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


def test_frontline_stage_batch_runs_workers_concurrently(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(_request())
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"),
        scope=BatchScope.FULL_STAGE,
        collect_first=True,
        stop_point=StopPoint.NONE,
    )
    barrier = threading.Barrier(len(batch.worker_ids))
    lock = threading.Lock()
    active = 0
    max_active = 0
    started: list[str] = []

    def _run(call: WorkerCall) -> WorkerResult:
        nonlocal active, max_active
        with lock:
            started.append(call.worker_id)
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait(timeout=2)
        finally:
            with lock:
                active -= 1
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id=f"mat-{call.worker_id}",
            failure=None,
        )

    harness.runner.run_single_worker = _run  # type: ignore[method-assign]

    result = harness.runner.run_stage_batch(state, batch)

    assert [item.worker_id for item in result.worker_results] == list(batch.worker_ids)
    assert set(started) == set(batch.worker_ids)
    assert max_active == len(batch.worker_ids)
    assert result.failures == ()


def test_report_frontline_worker_calls_receive_prefetch_manifest_path(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(replace(_request(), entry_point=WorkflowEntryPoint.REPORT_COMMAND))
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"),
        scope=BatchScope.FULL_STAGE,
        collect_first=True,
        stop_point=StopPoint.NONE,
    )
    calls_seen: list[WorkerCall] = []

    def _run(call: WorkerCall) -> WorkerResult:
        calls_seen.append(call)
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id=f"mat-{call.worker_id}",
            failure=None,
        )

    harness.runner.run_single_worker = _run  # type: ignore[method-assign]

    result = harness.runner.run_stage_batch(state, batch)

    assert result.failures == ()
    expected_path = str(state.run_dir / "data-layer" / "report-prefetch.json")
    assert {call.worker_id for call in calls_seen} == set(batch.worker_ids)
    assert all(call.prompt_runtime_vars["report_prefetch_manifest_path"] == expected_path for call in calls_seen)


def test_generic_frontline_worker_calls_do_not_receive_prefetch_manifest_path(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(_request())
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("market_analyst",),
        scope=BatchScope.SINGLE_WORKER,
        collect_first=False,
        stop_point=StopPoint.NONE,
    )
    calls_seen: list[WorkerCall] = []

    def _run(call: WorkerCall) -> WorkerResult:
        calls_seen.append(call)
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id="mat-market",
            failure=None,
        )

    harness.runner.run_single_worker = _run  # type: ignore[method-assign]

    result = harness.runner.run_stage_batch(state, batch)

    assert result.failures == ()
    assert len(calls_seen) == 1
    assert "report_prefetch_manifest_path" not in calls_seen[0].prompt_runtime_vars


def test_cn_a_unprefetched_frontline_workers_do_not_receive_prefetch_manifest_path(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    request = replace(
        _request(profile="CN_A"),
        ticker="600519",
        company_name="贵州茅台",
        market="CN_A",
        currency="CNY",
        currency_symbol="¥",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )
    state = harness.store.create_run(request)
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_ids=("policy_analyst", "hot_money_tracker", "lockup_watcher"),
        scope=BatchScope.FULL_STAGE,
        collect_first=True,
        stop_point=StopPoint.NONE,
    )
    calls_seen: list[WorkerCall] = []

    def _run(call: WorkerCall) -> WorkerResult:
        calls_seen.append(call)
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id=f"mat-{call.worker_id}",
            failure=None,
        )

    harness.runner.run_single_worker = _run  # type: ignore[method-assign]

    result = harness.runner.run_stage_batch(state, batch)

    assert result.failures == ()
    assert {call.worker_id for call in calls_seen} == set(batch.worker_ids)
    assert all("report_prefetch_manifest_path" not in call.prompt_runtime_vars for call in calls_seen)


def test_final_report_batch_runs_report_polisher_dynamic_serial_section_turns(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
                "models": {
                    "providers": {
                        "deepseek": {
                            "models": [
                                {
                                    "id": "deepseek-chat",
                                    "maxTokens": 8192,
                                    "contextWindow": 131072,
                                }
                            ]
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config_path))
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(_request(profile="CN_A"))
    source_material_ids = _seed_final_report_upstream_manifest(harness, state)
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.FINAL_REPORT,
        worker_ids=("report_polisher",),
        scope=BatchScope.FULL_STAGE,
        collect_first=False,
        stop_point=StopPoint.NONE,
    )
    active = 0
    max_active = 0
    calls_seen: list[WorkerCall] = []
    lock = threading.Lock()

    def _run(call: WorkerCall) -> WorkerResult:
        nonlocal active, max_active
        with lock:
            calls_seen.append(call)
            active += 1
            max_active = max(max_active, active)
        with lock:
            active -= 1
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id=f"mat-final-report-{call.turn_index}",
            failure=None,
            turn_index=call.turn_index,
            round_index=call.round_index,
            role_turn_index=call.role_turn_index,
        )

    def _unexpected_concurrent(_state: WorkflowState, _batch: StageBatch) -> StageBatchResult:
        raise AssertionError("final_report 不应并发执行")

    harness.runner.run_single_worker = _run  # type: ignore[method-assign]
    harness.runner._run_stage_batch_concurrent = _unexpected_concurrent  # type: ignore[method-assign]

    result = harness.runner.run_stage_batch(state, batch)

    assert result.failures == ()
    assert [call.worker_id for call in calls_seen] == ["report_polisher"] * 5
    assert [call.stage for call in calls_seen] == [Stage.FINAL_REPORT] * 5
    assert [call.turn_index for call in calls_seen] == [0, 1, 2, 3, 4]
    section_instructions = [
        call.prompt_runtime_vars["final_report_section_instruction"]
        for call in calls_seen
    ]
    assert "`## 一、`、`## 二、`" in section_instructions[0]
    assert "`## 三、`" in section_instructions[1]
    assert "`## 四、`" in section_instructions[2]
    assert "`## 五、`" in section_instructions[3]
    assert "`## 六、`、`## 七、`、`## 八、`" in section_instructions[4]
    assert "严禁生成 H1 标题" not in section_instructions[0]
    assert all("严禁生成 H1 标题" in instruction for instruction in section_instructions[1:])
    assert max_active == 1
    assert len(harness.openviking.read_material_ids) == len(source_material_ids) * len(calls_seen)
    for offset in range(0, len(harness.openviking.read_material_ids), len(source_material_ids)):
        assert harness.openviking.read_material_ids[offset : offset + len(source_material_ids)] == source_material_ids


def test_non_final_report_batch_keeps_single_worker_turn(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(_request())
    _seed_material(harness, state, "research_manager", Stage.INVESTMENT_DECISION, "# plan\nbody")
    batch = StageBatch(
        run_id=state.run_id,
        stage=Stage.TRADE_DECISION,
        worker_ids=("trader",),
        scope=BatchScope.FULL_STAGE,
        collect_first=False,
        stop_point=StopPoint.NONE,
    )
    calls_seen: list[WorkerCall] = []

    def _run(call: WorkerCall) -> WorkerResult:
        calls_seen.append(call)
        return WorkerResult(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            status=WorkerStatus.SUCCEEDED,
            openclaw_result_path=call.evidence_dir / "openclaw-result.json",
            approved_material_id="mat-trader",
            failure=None,
            turn_index=call.turn_index,
            round_index=call.round_index,
            role_turn_index=call.role_turn_index,
        )

    harness.runner.run_single_worker = _run  # type: ignore[method-assign]

    result = harness.runner.run_stage_batch(state, batch)

    assert result.failures == ()
    assert [(call.worker_id, call.stage, call.turn_index) for call in calls_seen] == [
        ("trader", Stage.TRADE_DECISION, 0)
    ]
    assert "final_report_section_instruction" not in calls_seen[0].prompt_runtime_vars


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


def test_report_polisher_segment_structure_diagnostic_allows_material_approval(tmp_path: Path) -> None:
    harness = _RunnerHarness(tmp_path)
    state = harness.store.create_run(_request(profile="CN_A"))
    call = _worker_call(tmp_path, run_id=state.run_id, call_id="call-report-polisher-t01")
    target = make_material_target(state.run_id, Stage.FINAL_REPORT, "report_polisher", call.call_id)
    call = replace(
        call,
        worker_id="report_polisher",
        stage=Stage.FINAL_REPORT,
        allowed_tools=(),
        material_target=target,
        prompt_runtime_vars={
            "final_report_section_instruction": (
                "本次只撰写终稿的第三节；第一行必须以 `## 三、` 开头；"
                "本段必须完整包含以下二级标题：`## 三、`；严禁生成 H1 标题；"
                "不要生成指定范围以外的其他编号章节正文。"
            )
        },
    )
    raw_output_path = call.evidence_dir / "raw-output.md"
    raw_output_path.write_text("# 三、基本面分析\n缺少二级标题", encoding="utf-8")
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
        raw_output_path=raw_output_path,
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
        raw_output_path=raw_output_path,
        openviking_receipt_path=call.evidence_dir / "openviking-receipt.json",
    )
    harness.runner.read_worker_evidence = lambda c, r: EvidenceReadResult.passed(evidence)  # type: ignore[method-assign]
    harness.runner.run_runtime_guards = lambda c, e: (  # type: ignore[method-assign]
        GuardResult.passed("ok"),
        harness.store.save_guard_result(c, GuardResult.passed("ok")),
    )
    harness.runner.run_material_approval = lambda c, e: _approval_ok(c)  # type: ignore[method-assign]

    result = harness.runner.run_single_worker(call)

    assert result.status == WorkerStatus.SUCCEEDED
    assert result.failure is None
    assert result.approved_material_id == f"mat-{call.call_id}"
    structure_path = call.evidence_dir / "final-report-segment-structure.json"
    assert structure_path.exists()
    payload = json.loads(structure_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "非首段禁止 H1 标题" in payload["reason"]
    assert payload["hard_fail"] is False
    assert "guard_source" not in payload


def _seed_final_report_upstream_manifest(harness: _RunnerHarness, state: WorkflowState) -> list[str]:
    sources = (
        ("market_analyst", Stage.FRONTLINE),
        ("fundamental_analyst", Stage.FRONTLINE),
        ("news_analyst", Stage.FRONTLINE),
        ("social_analyst", Stage.FRONTLINE),
        ("bull_researcher", Stage.INVESTMENT_DEBATE),
        ("bear_researcher", Stage.INVESTMENT_DEBATE),
        ("research_manager", Stage.INVESTMENT_DECISION),
        ("trader", Stage.TRADE_DECISION),
        ("risk_challenger", Stage.RISK_DEBATE),
        ("risk_guardian", Stage.RISK_DEBATE),
        ("risk_moderator", Stage.RISK_DEBATE),
        ("portfolio_manager", Stage.PORTFOLIO_DECISION),
    )
    return [
        _seed_material(harness, state, worker_id, stage, f"# {worker_id}\napproved L1")
        for worker_id, stage in sources
    ]


def _seed_material(
    harness: _RunnerHarness,
    state: WorkflowState,
    worker_id: str,
    stage: Stage,
    text: str,
) -> str:
    call_id = f"call-{stage.value}-{worker_id}"
    target = make_material_target(state.run_id, stage, worker_id, call_id)
    content = text.encode("utf-8")
    hard_gate_path = state.run_dir / "hard-gates" / f"{stage.value}-{worker_id}.json"
    hard_gate_path.parent.mkdir(parents=True, exist_ok=True)
    hard_gate_path.write_text(
        json.dumps({"ok": True, "status": "passed", "category": "combined_hard_gate"}),
        encoding="utf-8",
    )
    material = ApprovedMaterial(
        material_id=f"mat-{stage.value}-{worker_id}",
        run_id=state.run_id,
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name=target.target_name,
        l1_uri=target.l1_uri,
        l1_sha256=sha256(content).hexdigest(),
        l1_size_bytes=len(content),
        l2_index_uri=f"{target.l2_prefix}index.json",
        l2_index=L2Index(
            entries=(),
            empty_reason="none",
            index_uri=f"{target.l2_prefix}index.json",
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
        hard_gate_result_path=hard_gate_path,
    )
    harness.openviking.texts[material.material_id] = content
    harness.manifest_store.add(state.run_id, material)
    return material.material_id


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
