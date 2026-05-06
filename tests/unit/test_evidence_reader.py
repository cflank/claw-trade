from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw_trade.artifacts.refs import make_material_target
from claw_trade.runtime.evidence_reader import (
    EvidenceReader,
    OpenClawResult,
    ProviderEvidence,
    provider_evidence_to_dict,
)
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_provider_evidence_stage_uses_stage_enum() -> None:
    evidence = _provider_evidence()

    assert evidence.stage == Stage.FRONTLINE


def test_provider_evidence_to_dict_serializes_stage_value() -> None:
    evidence = _provider_evidence()

    payload = provider_evidence_to_dict(evidence)
    assert payload["stage"] == Stage.FRONTLINE.value


def test_provider_evidence_rejects_string_stage() -> None:
    with pytest.raises(TypeError, match=r"stage.*Stage"):
        _provider_evidence(stage="frontline")


def test_provider_evidence_rejects_non_stage_object() -> None:
    class FakeStage:
        value = "frontline"

    with pytest.raises(TypeError, match=r"stage.*Stage"):
        _provider_evidence(stage=FakeStage())


def test_require_provider_evidence_full_run_success(tmp_path: Path) -> None:
    call = _worker_call(tmp_path, stop_after_first_response=False)
    result = _openclaw_result(call.evidence_dir)
    reader = EvidenceReader()

    evidence_result = reader.require_provider_evidence(call=call, result=result, full_run=True)

    assert evidence_result.ok is True
    assert evidence_result.evidence is not None
    assert evidence_result.evidence.run_id == call.run_id
    assert evidence_result.evidence.call_id == call.call_id
    assert evidence_result.evidence.raw_output_path is not None
    assert evidence_result.evidence.openviking_receipt_path is not None


def test_require_provider_evidence_first_response_mode_allows_missing_raw_and_receipt(tmp_path: Path) -> None:
    call = _worker_call(tmp_path, stop_after_first_response=True)
    result = _openclaw_result(call.evidence_dir, raw_output=False, receipt=False)
    reader = EvidenceReader()

    evidence_result = reader.require_provider_evidence(call=call, result=result, full_run=False)

    assert evidence_result.ok is True
    assert evidence_result.evidence is not None
    assert evidence_result.evidence.raw_output_path is None
    assert evidence_result.evidence.openviking_receipt_path is None


@pytest.mark.parametrize(
    ("missing_raw_output", "missing_receipt", "expected_field"),
    (
        (True, False, "raw_output_path"),
        (False, True, "openviking_receipt_path"),
    ),
)
def test_require_provider_evidence_full_run_requires_raw_output_and_receipt(
    tmp_path: Path,
    missing_raw_output: bool,
    missing_receipt: bool,
    expected_field: str,
) -> None:
    call = _worker_call(tmp_path, stop_after_first_response=False)
    result = _openclaw_result(
        call.evidence_dir,
        raw_output=not missing_raw_output,
        receipt=not missing_receipt,
    )
    reader = EvidenceReader()

    evidence_result = reader.require_provider_evidence(call=call, result=result, full_run=True)

    assert evidence_result.ok is False
    assert evidence_result.reason is not None
    assert expected_field in evidence_result.reason


def test_require_provider_evidence_rejects_escape_path(tmp_path: Path) -> None:
    call = _worker_call(tmp_path, stop_after_first_response=False)
    result = _openclaw_result(call.evidence_dir)
    escape_path = tmp_path / "other-call" / "provider-request.json"
    escape_path.parent.mkdir(parents=True, exist_ok=True)
    escape_path.write_text("{}", encoding="utf-8")
    result = OpenClawResult(
        status=result.status,
        openclaw_run_id=result.openclaw_run_id,
        provider_request_id=result.provider_request_id,
        provider_request_id_status=result.provider_request_id_status,
        workspace_evidence_path=result.workspace_evidence_path,
        provider_request_path=escape_path,
        visible_tools_path=result.visible_tools_path,
        first_response_path=result.first_response_path,
        tool_calls_status=result.tool_calls_status,
        tool_calls_path=result.tool_calls_path,
        raw_output_path=result.raw_output_path,
        openviking_receipt_path=result.openviking_receipt_path,
        failure_reason=result.failure_reason,
    )
    reader = EvidenceReader()

    evidence_result = reader.require_provider_evidence(call=call, result=result, full_run=True)

    assert evidence_result.ok is False
    assert evidence_result.reason is not None
    assert "provider_request_path 不在本次 call evidence_dir 下" in evidence_result.reason


def test_require_provider_evidence_first_response_still_rejects_optional_escape_paths(tmp_path: Path) -> None:
    call = _worker_call(tmp_path, stop_after_first_response=True)
    result = _openclaw_result(call.evidence_dir, raw_output=False, receipt=False)
    escape_receipt = tmp_path / "other-call" / "openviking-receipt.json"
    escape_receipt.parent.mkdir(parents=True, exist_ok=True)
    escape_receipt.write_text("{}", encoding="utf-8")
    result = OpenClawResult(
        status=result.status,
        openclaw_run_id=result.openclaw_run_id,
        provider_request_id=result.provider_request_id,
        provider_request_id_status=result.provider_request_id_status,
        workspace_evidence_path=result.workspace_evidence_path,
        provider_request_path=result.provider_request_path,
        visible_tools_path=result.visible_tools_path,
        first_response_path=result.first_response_path,
        tool_calls_status=result.tool_calls_status,
        tool_calls_path=result.tool_calls_path,
        raw_output_path=None,
        openviking_receipt_path=escape_receipt,
        failure_reason=result.failure_reason,
    )
    reader = EvidenceReader()

    evidence_result = reader.require_provider_evidence(call=call, result=result, full_run=False)

    assert evidence_result.ok is False
    assert evidence_result.reason is not None
    assert "openviking_receipt_path 不在本次 call evidence_dir 下" in evidence_result.reason


def test_require_provider_evidence_returns_failed_result_on_json_decode_error(tmp_path: Path) -> None:
    call = _worker_call(tmp_path, stop_after_first_response=False)
    result = _openclaw_result(call.evidence_dir)
    visible_tools_path = call.evidence_dir / "visible-tools.json"
    visible_tools_path.write_text("{invalid", encoding="utf-8")
    reader = EvidenceReader()

    evidence_result = reader.require_provider_evidence(call=call, result=result, full_run=True)

    assert evidence_result.ok is False
    assert evidence_result.reason is not None
    assert "visible tools JSON 解析失败" in evidence_result.reason
    assert evidence_result.paths == (visible_tools_path.resolve(),)


def _provider_evidence(*, stage: Stage | object = Stage.FRONTLINE) -> ProviderEvidence:
    return ProviderEvidence(
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        stage=stage,  # type: ignore[arg-type]
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="present",
        workspace_evidence_path=Path("runs/run-1/calls/call-1/evidence/workspace.json"),
        provider_request_path=Path("runs/run-1/calls/call-1/evidence/provider-request.json"),
        visible_tools_path=Path("runs/run-1/calls/call-1/evidence/visible-tools.json"),
        first_response_path=Path("runs/run-1/calls/call-1/evidence/first-response.json"),
        tool_calls_status="present",
        tool_calls_path=Path("runs/run-1/calls/call-1/evidence/tool-calls.json"),
        raw_output_path=Path("runs/run-1/calls/call-1/evidence/raw-output.json"),
        openviking_receipt_path=Path("runs/run-1/calls/call-1/evidence/openviking-receipt.json"),
    )


def _worker_call(tmp_path: Path, *, stop_after_first_response: bool) -> WorkerCall:
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    return WorkerCall(
        call_id="call-1",
        run_id="run-1",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        ticker="AAPL",
        company_name="Apple",
        market="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-01-01",
        end_date="2026-05-04",
        allowed_tools=("market_data",),
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=stop_after_first_response,
    )


def _openclaw_result(
    evidence_dir: Path,
    *,
    raw_output: bool = True,
    receipt: bool = True,
) -> OpenClawResult:
    _write_json(evidence_dir / "workspace.json", {"source": "openclaw"})
    _write_json(evidence_dir / "provider-request.json", {"messages": [], "tools": []})
    _write_json(evidence_dir / "visible-tools.json", {"tools": []})
    _write_json(evidence_dir / "first-response.json", {"type": "message"})
    _write_json(evidence_dir / "tool-calls.json", {"events": []})
    raw_output_path: Path | None = None
    receipt_path: Path | None = None
    if raw_output:
        raw_output_path = evidence_dir / "raw-output.json"
        raw_output_path.write_text("{}", encoding="utf-8")
    if receipt:
        receipt_path = evidence_dir / "openviking-receipt.json"
        receipt_path.write_text("{}", encoding="utf-8")

    return OpenClawResult(
        status="succeeded",
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=evidence_dir / "workspace.json",
        provider_request_path=evidence_dir / "provider-request.json",
        visible_tools_path=evidence_dir / "visible-tools.json",
        first_response_path=evidence_dir / "first-response.json",
        tool_calls_status="recorded",
        tool_calls_path=evidence_dir / "tool-calls.json",
        raw_output_path=raw_output_path,
        openviking_receipt_path=receipt_path,
        failure_reason=None,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
