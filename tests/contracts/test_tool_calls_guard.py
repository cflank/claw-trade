from __future__ import annotations

import json
from pathlib import Path

from claw_trade.artifacts.refs import make_material_target
from claw_trade.guards.tool_calls import validate_tool_calls
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_tool_calls_accepts_none_status() -> None:
    call = _worker_call()
    evidence = _provider_evidence(call, tool_calls_status="none")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "none",
            "run_id": call.run_id,
            "call_id": call.call_id,
            "worker_id": call.worker_id,
            "stage": call.stage.value,
            "openclaw_run_id": evidence.openclaw_run_id,
            "calls": [],
        },
    )
    guard = validate_tool_calls(call, evidence)
    assert guard.ok


def test_tool_calls_rejects_non_model_source() -> None:
    call = _worker_call()
    evidence = _provider_evidence(call, tool_calls_status="none")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "worker_output",
            "status": "none",
            "run_id": call.run_id,
            "call_id": call.call_id,
            "worker_id": call.worker_id,
            "stage": call.stage.value,
            "openclaw_run_id": evidence.openclaw_run_id,
            "calls": [],
        },
    )
    guard = validate_tool_calls(call, evidence)
    assert not guard.ok


def test_tool_calls_rejects_invalid_status() -> None:
    call = _worker_call()
    evidence = _provider_evidence(call, tool_calls_status="present")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "present",
            "run_id": call.run_id,
            "call_id": call.call_id,
            "worker_id": call.worker_id,
            "stage": call.stage.value,
            "openclaw_run_id": evidence.openclaw_run_id,
            "calls": [],
        },
    )
    guard = validate_tool_calls(call, evidence)
    assert not guard.ok


def test_tool_calls_rejects_recorded_call_missing_required_fields() -> None:
    call = _worker_call()
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "run_id": call.run_id,
            "call_id": call.call_id,
            "worker_id": call.worker_id,
            "stage": call.stage.value,
            "openclaw_run_id": evidence.openclaw_run_id,
            "calls": [
                {
                    "tool_name": "openviking_write_material",
                    "action": "write",
                    "status": "success",
                }
            ],
        },
    )
    guard = validate_tool_calls(call, evidence)
    assert not guard.ok


def test_tool_calls_rejects_recorded_call_invalid_status() -> None:
    call = _worker_call()
    evidence = _provider_evidence(call, tool_calls_status="recorded")
    _write_tool_calls(
        evidence.tool_calls_path,
        {
            "source": "model_tool_events",
            "status": "recorded",
            "run_id": call.run_id,
            "call_id": call.call_id,
            "worker_id": call.worker_id,
            "stage": call.stage.value,
            "openclaw_run_id": evidence.openclaw_run_id,
            "calls": [
                {
                    "tool_name": "openviking_write_material",
                    "action": "write",
                    "status": "succeeded",
                    "result_sha256": "w" * 64,
                }
            ],
        },
    )
    guard = validate_tool_calls(call, evidence)
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
    path = Path("runs/run-1/calls/call-8/evidence/tool-calls.json")
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
        tool_calls_path=path,
        raw_output_path=Path("runs/run-1/calls/call-8/evidence/raw-output.json"),
        openviking_receipt_path=Path("runs/run-1/calls/call-8/evidence/openviking-receipt.json"),
    )


def _write_tool_calls(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
