from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingAccessRecord
from claw_trade.artifacts.refs import (
    L1Claim,
    L2Entry,
    L2Index,
    MaterialReceipt,
    OpenVikingReadCapability,
    make_material_target,
)
from claw_trade.guards.common import GuardResult
from claw_trade.runtime.evidence_reader import OpenClawResult
from claw_trade.workflow.models import (
    BatchScope,
    Decision,
    DecisionKind,
    ExportResult,
    FailureRecord,
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
)
from claw_trade.workflow.store import WorkflowStore


@dataclass(frozen=True)
class SampleExportClaimMapping:
    export_id: str
    stage: Stage
    source_paths: tuple[Path, ...]


def test_create_run_and_state_roundtrip(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    state = store.create_run(_request())

    run_dir = store.run_dir(state.run_id)
    assert run_dir == state.run_dir
    assert (run_dir / "calls").exists()
    assert (run_dir / "reports").exists()

    for filename in (
        "approved-manifest.json",
        "receipts.json",
        "access-audit.json",
        "l1-index.json",
        "l2-index.json",
        "l1-l2-index.json",
    ):
        payload = _read_json(run_dir / "openviking" / filename)
        assert payload["audit_only"] is True
        assert "不是正式材料权威" in payload["note"]

    loaded = store.load_state(state.run_id)
    assert loaded.run_id == state.run_id
    assert loaded.status == RunStatus.CREATED
    assert loaded.request.stop_point == StopPoint.NONE
    assert loaded.request.entry_point == WorkflowEntryPoint.GENERIC
    assert loaded.request.max_debate_rounds == 2
    assert loaded.request.max_risk_discuss_rounds == 3
    assert loaded.request.frontline_execution_mode == "parallel"

    updated = loaded.__class__(
        **{
            **loaded.__dict__,
            "status": RunStatus.FRONTLINE_RUNNING,
            "active_stage": Stage.FRONTLINE,
            "completed_workers": ("market_analyst",),
        }
    )
    store.save_state(updated)
    loaded_updated = store.load_state(state.run_id)
    assert loaded_updated.status == RunStatus.FRONTLINE_RUNNING
    assert loaded_updated.active_stage == Stage.FRONTLINE
    assert loaded_updated.completed_workers == ("market_analyst",)


def test_t50_main_persistence_methods(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    state = store.create_run(_request())
    call = _worker_call(state.run_id)

    assert store.call_dir(call) == store.run_dir(state.run_id) / "calls" / call.call_id
    assert store.evidence_dir(call) == store.call_dir(call)

    decision_path = store.save_decision(
        state.run_id,
        Decision(
            kind=DecisionKind.WAKE_STAGE,
            stage=Stage.FRONTLINE,
            batch=StageBatch(
                run_id=state.run_id,
                stage=Stage.FRONTLINE,
                worker_ids=("market_analyst",),
                scope=BatchScope.SINGLE_WORKER,
                collect_first=False,
                stop_point=StopPoint.SINGLE_WORKER_COMPLETE,
                turn_index=2,
                round_index=2,
                role_turn_index=2,
            ),
            next_status=RunStatus.FRONTLINE_RUNNING,
            reason=None,
            failure=None,
        ),
    )
    assert decision_path.exists()
    decision_payload = _read_json(decision_path)
    assert decision_payload["kind"] == "wake_stage"
    assert decision_payload["next_status"] == "frontline_running"
    assert decision_payload["stage"] == "frontline"
    assert decision_payload["batch"]["worker_ids"] == ["market_analyst"]
    assert decision_payload["batch"]["turn_index"] == 2
    assert decision_payload["batch"]["round_index"] == 2

    call_path = store.save_call(call)
    assert call_path.exists()
    assert call_path.parent == store.call_dir(call)
    call_payload = _read_json(call_path)
    assert call_payload["run_id"] == state.run_id
    assert call_payload["openviking_read_capabilities"][0]["allowed_l2_index_sha256"] == "sha-l2-index"
    for runtime_evidence_name in (
        "workspace-evidence.json",
        "provider-request.json",
        "visible-tools.json",
        "first-response.json",
        "tool-calls.json",
        "raw-output.md",
        "openviking-receipt.json",
    ):
        assert not (call_path.parent / runtime_evidence_name).exists()

    openclaw_result_path = store.save_openclaw_result(call, _openclaw_result(call))
    assert openclaw_result_path.exists()
    openclaw_result_payload = _read_json(openclaw_result_path)
    assert openclaw_result_payload["status"] == "succeeded"
    assert openclaw_result_payload["provider_request_id_status"] == "returned"
    assert openclaw_result_payload["provider_request_path"].endswith("/provider-request.json")
    assert openclaw_result_payload["raw_output_path"].endswith("/raw-output.md")

    worker_result = WorkerResult(
        run_id=state.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        status=WorkerStatus.SUCCEEDED,
        openclaw_result_path=openclaw_result_path,
        approved_material_id="mat-1",
        failure=None,
        turn_index=call.turn_index,
        round_index=call.round_index,
        role_turn_index=call.role_turn_index,
    )
    worker_result_path = store.save_worker_result(worker_result)
    assert worker_result_path.exists()

    results = store.list_worker_results(state.run_id)
    assert len(results) == 1
    assert results[0].status == WorkerStatus.SUCCEEDED
    assert results[0].stage == Stage.FRONTLINE
    assert results[0].turn_index == call.turn_index

    failure = FailureRecord(
        run_id=state.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        category="provider_request",
        reason="missing",
        evidence_paths=(store.call_dir(call) / "provider-request.json",),
        early_stop=False,
        human_action_required=None,
        turn_index=call.turn_index,
        round_index=call.round_index,
        role_turn_index=call.role_turn_index,
    )
    failure_path = store.save_failure(failure)
    assert failure_path.exists()

    batch_result = StageBatchResult(
        run_id=state.run_id,
        stage=Stage.FRONTLINE,
        worker_results=(worker_result,),
        failures=(failure,),
        early_stop_used=False,
        collect_first_report_path=store.run_dir(state.run_id) / "reports" / "collect-first.md",
    )
    stage_batch_path = store.save_stage_batch_result(batch_result)
    assert stage_batch_path.exists()

    export_result = ExportResult(
        run_id=state.run_id,
        status="passed",
        final_report_path=store.run_dir(state.run_id) / "reports" / "final-report.md",
        export_guard_result_path=store.run_dir(state.run_id) / "reports" / "export-guard-results.json",
        unsupported_claims=(),
        failure=None,
    )
    export_result_path = store.save_export_result(export_result)
    assert export_result_path.exists()
    loaded_export = store.load_export_result(state.run_id)
    assert loaded_export is not None
    assert loaded_export.status == "passed"


def test_t50a_audit_persistence_methods(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    state = store.create_run(_request())
    call = _worker_call(state.run_id)
    store.save_call(call)

    guard = GuardResult.failed("claims", "unsupported", paths=(store.call_dir(call) / "raw-output.md",))
    guard_path = store.save_guard_result(call, guard)
    assert guard_path.exists()

    export_guard_path = store.save_export_guard_result(state.run_id, GuardResult.passed("ok"))
    assert export_guard_path.exists()

    receipt = MaterialReceipt(
        uri=call.material_target.l1_uri,
        run_id=state.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        target_name="report",
        sha256="a" * 64,
        size_bytes=12,
        written_at="2026-05-04T12:00:00Z",
        receipt_id="receipt-1",
    )
    receipts_path = store.append_receipt_audit(state.run_id, receipt, guard)
    receipts_payload = _read_json(receipts_path)
    assert len(receipts_payload["receipts"]) == 1
    assert receipts_payload["receipts"][0]["receipt"]["uri"] == call.material_target.l1_uri

    access_path = store.append_access_audit(
        call,
        (
            OpenVikingAccessRecord(
                action="read",
                uri=call.material_target.l1_uri,
                ok=True,
                checked_at="2026-05-04T12:00:00Z",
                capability_id="cap-1",
                sha256="b" * 64,
                size_bytes=99,
                error_category=None,
                error_message=None,
            ),
        ),
    )
    access_payload = _read_json(access_path)
    assert len(access_payload["events"]) == 1
    assert access_payload["events"][0]["call_id"] == call.call_id

    index = L2Index(
        entries=(
            L2Entry(
                evidence_id="ev-1",
                uri=f"{call.material_target.l2_prefix}tool-output.json",
                kind="tool_output",
                source="openclaw",
                sha256="c" * 64,
                size_bytes=64,
            ),
        ),
        empty_reason=None,
        index_uri=f"{call.material_target.l2_prefix}index.json",
        index_sha256="d" * 64,
        index_size_bytes=111,
    )
    l1_l2_path = store.save_l1_l2_index(call, index)
    l1_l2_payload = _read_json(l1_l2_path)
    assert len(l1_l2_payload["items"]) == 1
    assert l1_l2_payload["items"][0]["stage"] == Stage.FRONTLINE.value

    mapping_path = store.save_export_claim_mapping(
        state.run_id,
        SampleExportClaimMapping(
            export_id="export-1",
            stage=Stage.PORTFOLIO_DECISION,
            source_paths=(Path("runs/a.json"), Path("runs/b.json")),
        ),
    )
    mapping_payload = _read_json(mapping_path)
    assert mapping_payload["stage"] == Stage.PORTFOLIO_DECISION.value
    assert mapping_payload["source_paths"] == ["runs/a.json", "runs/b.json"]


def test_run_dir_blocks_traversal(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    try:
        store.run_dir("../evil")
    except ValueError as exc:
        assert "run_id" in str(exc) or "路径越界" in str(exc)
    else:
        raise AssertionError("expected ValueError")


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
        max_debate_rounds=2,
        max_risk_discuss_rounds=3,
        frontline_execution_mode="parallel",
    )


def _worker_call(run_id: str) -> WorkerCall:
    call_id = f"{run_id}-frontline-market_analyst-1"
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
        openviking_read_capabilities=(
            OpenVikingReadCapability(
                capability_id="cap-frontline-market",
                material_id="mat-frontline-market",
                allowed_l1_uri=f"viking://resources/workflow/{run_id}/frontline/market_analyst/call-upstream/report.md",
                allowed_l1_sha256="sha-l1",
                allowed_l2_prefix=(
                    f"viking://resources/workflow/{run_id}/frontline/market_analyst/call-upstream/evidence/"
                ),
                manifest_entry_sha256="sha-manifest",
                allowed_l2_index_sha256="sha-l2-index",
            ),
        ),
        material_target=make_material_target(
            run_id=run_id,
            stage=Stage.FRONTLINE,
            worker_id="market_analyst",
            call_id=call_id,
        ),
        read_policy=ReadPolicy(),
        evidence_dir=Path("runs") / run_id / "calls" / call_id,
        stop_after_first_response=False,
    )


def _openclaw_result(call: WorkerCall) -> OpenClawResult:
    call_dir = Path("runs") / call.run_id / "calls" / call.call_id
    return OpenClawResult(
        status="succeeded",
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=call_dir / "workspace-evidence.json",
        provider_request_path=call_dir / "provider-request.json",
        visible_tools_path=call_dir / "visible-tools.json",
        first_response_path=call_dir / "first-response.json",
        tool_calls_status="recorded",
        tool_calls_path=call_dir / "tool-calls.json",
        raw_output_path=call_dir / "raw-output.md",
        openviking_receipt_path=call_dir / "openviking-receipt.json",
        failure_reason=None,
    )


def _read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)
