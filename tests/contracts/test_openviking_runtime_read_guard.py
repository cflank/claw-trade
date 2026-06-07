from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.refs import (
    ApprovedMaterial,
    L1Claim,
    L2Entry,
    L2Index,
    make_material_target,
)
from claw_trade.guards.openviking_access import validate_openviking_runtime_reads
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_runtime_reads_pass_for_manifest_scoped_l1_read_and_local_write(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": cap.allowed_l1_uri,
                    "result_sha256": cap.allowed_l1_sha256,
                    "status": "success",
                },
                {
                    "tool_name": "openviking_write_material",
                    "action": "write",
                    "capability_id": None,
                    "material_id": None,
                    "uri": call.material_target.l1_uri,
                    "result_sha256": "w" * 64,
                    "status": "success",
                },
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert guard.ok


def test_runtime_reads_reject_missing_read_fields(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "material_id": cap.material_id,
                    "uri": cap.allowed_l1_uri,
                    "result_sha256": cap.allowed_l1_sha256,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_allow_error_read_without_capability_or_uri_when_error_is_present(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "material_id": "US",
                    "status": "error",
                    "error": "openviking_read_with_capability capability not found in command",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert guard.ok


def test_runtime_reads_reject_error_read_without_error_message(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "status": "error",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_reject_uri_outside_manifest_capability(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": "viking://resources/workflow/run-1/investment_decision/research_manager/call-7/evidence-other/a.json",
                    "result_sha256": "a" * 64,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_allow_l1_hash_mismatch_for_audit_only(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": cap.allowed_l1_uri,
                    "result_sha256": "x" * 64,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert guard.ok


def test_runtime_reads_pass_for_manifest_scoped_l2_read_with_index_sha(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    assert cap.allowed_l2_prefix is not None
    assert cap.allowed_l2_index_sha256 is not None
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": f"{cap.allowed_l2_prefix}e1.json",
                    "result_sha256": "l" * 64,
                    "l2_index_sha256": cap.allowed_l2_index_sha256,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert guard.ok


def test_runtime_reads_non_openviking_action_read_does_not_trigger(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "market_data.read",
                    "action": "read",
                    "uri": "https://example.invalid/data.json",
                    "result_sha256": "m" * 64,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert guard.ok


def test_runtime_reads_reject_openviking_tool_name_with_wrong_action(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "write",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": cap.allowed_l1_uri,
                    "result_sha256": cap.allowed_l1_sha256,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_reject_openviking_status_invalid(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": cap.allowed_l1_uri,
                    "result_sha256": cap.allowed_l1_sha256,
                    "status": "succeeded",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_reject_l2_index_sha_missing(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    assert cap.allowed_l2_prefix is not None
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": f"{cap.allowed_l2_prefix}e1.json",
                    "result_sha256": "l" * 64,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_allow_l2_index_sha_mismatch_for_audit_only(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    cap = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION, run_id=call.run_id)[0]
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    assert cap.allowed_l2_prefix is not None
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_read_with_capability",
                    "action": "read",
                    "capability_id": cap.capability_id,
                    "material_id": cap.material_id,
                    "uri": f"{cap.allowed_l2_prefix}e1.json",
                    "result_sha256": "l" * 64,
                    "l2_index_sha256": "wrong-index-sha",
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert guard.ok


def test_runtime_reads_reject_write_with_forged_capability_or_material(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_write_material",
                    "action": "write",
                    "capability_id": "cap-forged",
                    "material_id": "mat-forged",
                    "uri": call.material_target.l1_uri,
                    "result_sha256": "z" * 64,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def test_runtime_reads_reject_write_uri_not_material_target(tmp_path: Path) -> None:
    call = _worker_call()
    manifest = _manifest(tmp_path)
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "calls": [
                {
                    "tool_name": "openviking_write_material",
                    "action": "write",
                    "capability_id": None,
                    "material_id": None,
                    "uri": "viking://resources/workflow/run-1/trade_decision/trader/call-8/other.md",
                    "result_sha256": "y" * 64,
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_openviking_runtime_reads(call, evidence, manifest)
    assert not guard.ok


def _worker_call() -> WorkerCall:
    target = make_material_target("run-1", Stage.TRADE_DECISION, "trader", "call-8")
    return WorkerCall(
        call_id=target.call_id,
        run_id=target.run_id,
        worker_id=target.worker_id,
        stage=target.stage,
        profile="US",
        ticker="AAPL",
        company_name="Apple",
        market="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-01-01",
        end_date="2026-05-04",
        allowed_tools=("openviking_read_with_capability", "openviking_write_material"),
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=Path("runs/run-1/calls/call-8/evidence"),
        stop_after_first_response=False,
    )


def _provider_evidence(call: WorkerCall, *, tool_calls_status: str) -> ProviderEvidence:
    return ProviderEvidence(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=Path("runs/run-1/calls/call-8/evidence/workspace.json"),
        provider_request_path=Path("runs/run-1/calls/call-8/evidence/provider-request.json"),
        visible_tools_path=Path("runs/run-1/calls/call-8/evidence/visible-tools.json"),
        first_response_path=Path("runs/run-1/calls/call-8/evidence/first-response.json"),
        tool_calls_status=tool_calls_status,
        tool_calls_path=Path("runs/run-1/calls/call-8/evidence/tool-calls.json"),
        raw_output_path=Path("runs/run-1/calls/call-8/evidence/raw-output.json"),
        openviking_receipt_path=Path("runs/run-1/calls/call-8/evidence/openviking-receipt.json"),
    )


def _manifest(tmp_path: Path) -> ApprovedManifest:
    material = _approved_material(
        tmp_path,
        material_id="mat-investment-manager",
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        call_id="call-7",
    )
    return ApprovedManifest.empty().add(material)


def _approved_material(
    tmp_path: Path,
    material_id: str,
    worker_id: str,
    stage: Stage,
    call_id: str,
) -> ApprovedMaterial:
    l1_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/report.md"
    l2_index_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/index.json"
    evidence_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/e1.json"
    evidence_bytes = b'{"evidence_id":"e1"}'
    gate_result_path = tmp_path / "gate_result.json"
    gate_result_path.write_text('{"ok": true, "category": "runtime_guards"}', encoding="utf-8")
    return ApprovedMaterial(
        material_id=material_id,
        run_id="run-1",
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=l1_uri,
        l1_sha256="approved-sha",
        l1_size_bytes=128,
        l2_index_uri=l2_index_uri,
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="e1",
                    uri=evidence_uri,
                    kind="market_data",
                    source="provider",
                    sha256=hashlib.sha256(evidence_bytes).hexdigest(),
                    size_bytes=len(evidence_bytes),
                ),
            ),
            empty_reason=None,
            index_uri=l2_index_uri,
            index_sha256="index-sha",
            index_size_bytes=32,
        ),
        l1_claims=(
            L1Claim(
                claim_id="claim-1",
                kind="source_claim",
                text="据来源",
                value=None,
                required_evidence_kinds=("source",),
                evidence_ids=("e1",),
            ),
        ),
        approved_at="2026-05-03T16:10:00Z",
        hard_gate_result_path=gate_result_path,
    )


def _write_tool_calls(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
