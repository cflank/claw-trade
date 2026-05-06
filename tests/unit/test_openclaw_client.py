from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from claw_trade.artifacts.refs import MaterialReadRef, OpenVikingReadCapability, make_material_target
from claw_trade.runtime.evidence_reader import OpenClawResult
from claw_trade.runtime.openclaw_client import (
    OpenClawClient,
    ProbeResult,
    build_openclaw_command,
    parse_openclaw_result,
    validate_openclaw_result_shape,
)
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_build_openclaw_command_rejects_non_string_worker_id() -> None:
    call = _valid_call()
    bad_call = replace(call, worker_id=123)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="agent 必须是 str"):
        build_openclaw_command(bad_call)


def test_build_openclaw_command_requires_stage_enum() -> None:
    call = _valid_call()
    wrong_stage = _FakeStage()
    bad_call = replace(call, stage=wrong_stage)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="stage 必须是 Stage"):
        build_openclaw_command(bad_call)


def test_build_openclaw_command_keeps_upstream_and_capability_sha_fields() -> None:
    command = build_openclaw_command(_valid_call())
    upstream = command.upstream_materials[0]
    capability = command.openviking_read_capabilities[0]

    assert upstream["material_id"] == "mat-frontline-market"
    assert upstream["capability_id"] == "cap-frontline-market"
    assert upstream["l1_uri"].startswith("viking://resources/workflow/run-1/frontline/")
    assert upstream["l1_sha256"] == "sha-l1"
    assert capability["material_id"] == "mat-frontline-market"
    assert capability["capability_id"] == "cap-frontline-market"
    assert capability["allowed_l1_sha256"] == "sha-l1"
    assert capability["allowed_l2_index_sha256"] == "sha-l2-index"


def test_parse_openclaw_result_converts_paths() -> None:
    payload = {
        "status": "succeeded",
        "openclaw_run_id": "openclaw-1",
        "provider_request_id": "req-1",
        "provider_request_id_status": "returned",
        "workspace_evidence_path": "runs/run-1/calls/call-1/workspace-evidence.json",
        "provider_request_path": "runs/run-1/calls/call-1/provider-request.json",
        "visible_tools_path": "runs/run-1/calls/call-1/visible-tools.json",
        "first_response_path": "runs/run-1/calls/call-1/first-response.json",
        "tool_calls_status": "recorded",
        "tool_calls_path": "runs/run-1/calls/call-1/tool-calls.json",
        "raw_output_path": "runs/run-1/calls/call-1/raw-output.md",
        "openviking_receipt_path": "runs/run-1/calls/call-1/openviking-receipt.json",
        "failure_reason": None,
    }
    result = parse_openclaw_result(payload)

    assert isinstance(result.workspace_evidence_path, Path)
    assert result.workspace_evidence_path == Path("runs/run-1/calls/call-1/workspace-evidence.json")


def test_validate_shape_first_response_does_not_require_raw_output_or_receipt(tmp_path: Path) -> None:
    call = _valid_call(
        evidence_dir=tmp_path / "evidence",
        stop_after_first_response=True,
    )
    paths = _prepare_required_evidence_files(call.evidence_dir)
    result = OpenClawResult(
        status="succeeded",
        openclaw_run_id="openclaw-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=paths["workspace_evidence_path"],
        provider_request_path=paths["provider_request_path"],
        visible_tools_path=paths["visible_tools_path"],
        first_response_path=paths["first_response_path"],
        tool_calls_status="recorded",
        tool_calls_path=paths["tool_calls_path"],
        raw_output_path=None,
        openviking_receipt_path=None,
        failure_reason=None,
    )

    guard = validate_openclaw_result_shape(result, call)
    assert guard.ok is True


def test_validate_shape_full_run_requires_raw_output_and_receipt(tmp_path: Path) -> None:
    call = _valid_call(
        evidence_dir=tmp_path / "evidence",
        stop_after_first_response=False,
    )
    paths = _prepare_required_evidence_files(call.evidence_dir)
    result = OpenClawResult(
        status="succeeded",
        openclaw_run_id="openclaw-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=paths["workspace_evidence_path"],
        provider_request_path=paths["provider_request_path"],
        visible_tools_path=paths["visible_tools_path"],
        first_response_path=paths["first_response_path"],
        tool_calls_status="recorded",
        tool_calls_path=paths["tool_calls_path"],
        raw_output_path=None,
        openviking_receipt_path=None,
        failure_reason=None,
    )

    guard = validate_openclaw_result_shape(result, call)
    assert guard.ok is False
    assert guard.category == "openclaw_result_shape"
    assert guard.reason is not None and "raw_output_path 缺失" in guard.reason


def test_validate_shape_resolves_relative_paths_against_evidence_dir_when_cwd_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-8" / "evidence"
    call = _valid_call(
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )
    _prepare_required_evidence_files(evidence_dir)
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(other_cwd)

    result = OpenClawResult(
        status="succeeded",
        openclaw_run_id="openclaw-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=Path("workspace-evidence.json"),
        provider_request_path=Path("provider-request.json"),
        visible_tools_path=Path("visible-tools.json"),
        first_response_path=Path("first-response.json"),
        tool_calls_status="recorded",
        tool_calls_path=Path("tool-calls.json"),
        raw_output_path=Path("raw-output.md"),
        openviking_receipt_path=Path("openviking-receipt.json"),
        failure_reason=None,
    )

    guard = validate_openclaw_result_shape(result, call)
    assert guard.ok is True


def test_validate_shape_rejects_relative_path_escape_from_evidence_dir(tmp_path: Path) -> None:
    call = _valid_call(
        evidence_dir=tmp_path / "evidence",
        stop_after_first_response=False,
    )
    paths = _prepare_required_evidence_files(call.evidence_dir)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "provider-request.json").write_text("{}", encoding="utf-8")
    result = OpenClawResult(
        status="succeeded",
        openclaw_run_id="openclaw-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=paths["workspace_evidence_path"],
        provider_request_path=Path("../outside/provider-request.json"),
        visible_tools_path=paths["visible_tools_path"],
        first_response_path=paths["first_response_path"],
        tool_calls_status="recorded",
        tool_calls_path=paths["tool_calls_path"],
        raw_output_path=paths["raw_output_path"],
        openviking_receipt_path=paths["openviking_receipt_path"],
        failure_reason=None,
    )

    guard = validate_openclaw_result_shape(result, call)
    assert guard.ok is False
    assert guard.reason is not None and "provider_request_path 不在 evidence_dir 内" in guard.reason


def test_run_worker_returns_failed_when_openclaw_payload_is_missing_required_paths(tmp_path: Path) -> None:
    call = _valid_call(evidence_dir=tmp_path / "evidence")
    command = build_openclaw_command(call)
    paths = _prepare_required_evidence_files(call.evidence_dir)
    runner = _FakeRunner(
        payload={
            "status": "succeeded",
            "openclaw_run_id": "openclaw-1",
            "provider_request_id": "req-1",
            "provider_request_id_status": "returned",
            "workspace_evidence_path": str(paths["workspace_evidence_path"]),
            "provider_request_path": None,
            "visible_tools_path": str(paths["visible_tools_path"]),
            "first_response_path": str(paths["first_response_path"]),
            "tool_calls_status": "recorded",
            "tool_calls_path": str(paths["tool_calls_path"]),
            "raw_output_path": str(paths["raw_output_path"]),
            "openviking_receipt_path": str(paths["openviking_receipt_path"]),
            "failure_reason": None,
        }
    )
    client = OpenClawClient(runner=runner)

    result = client.run_worker(command)

    assert result.status == "failed"
    assert result.failure_reason is not None and "provider_request_path 缺失" in result.failure_reason


def test_run_worker_sends_command_payload_to_runner(tmp_path: Path) -> None:
    call = _valid_call(evidence_dir=tmp_path / "evidence")
    command = build_openclaw_command(call)
    paths = _prepare_required_evidence_files(call.evidence_dir)
    runner = _FakeRunner(
        payload={
            "status": "succeeded",
            "openclaw_run_id": "openclaw-1",
            "provider_request_id": "req-1",
            "provider_request_id_status": "returned",
            "workspace_evidence_path": str(paths["workspace_evidence_path"]),
            "provider_request_path": str(paths["provider_request_path"]),
            "visible_tools_path": str(paths["visible_tools_path"]),
            "first_response_path": str(paths["first_response_path"]),
            "tool_calls_status": "recorded",
            "tool_calls_path": str(paths["tool_calls_path"]),
            "raw_output_path": str(paths["raw_output_path"]),
            "openviking_receipt_path": str(paths["openviking_receipt_path"]),
            "failure_reason": None,
        }
    )
    client = OpenClawClient(runner=runner)

    result = client.run_worker(command)

    assert result.status == "succeeded"
    sent = runner.last_payload
    assert sent is not None
    assert sent["material_target"]["l1_uri"] == command.material_target["l1_uri"]  # type: ignore[index]
    first_cap = sent["openviking_read_capabilities"][0]  # type: ignore[index]
    assert first_cap["allowed_l2_index_sha256"] == "sha-l2-index"


def test_probe_delegates_to_runner() -> None:
    runner = _FakeRunner(payload={"status": "failed", "failure_reason": "x"}, probe_result=ProbeResult.passed())
    client = OpenClawClient(runner=runner)
    probe = client.probe()
    assert probe.ok is True
    assert probe.reason is None


def _prepare_required_evidence_files(evidence_dir: Path) -> dict[str, Path]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    names = (
        "workspace-evidence.json",
        "provider-request.json",
        "visible-tools.json",
        "first-response.json",
        "tool-calls.json",
        "raw-output.md",
        "openviking-receipt.json",
    )
    out: dict[str, Path] = {}
    for name in names:
        path = evidence_dir / name
        path.write_text("{}", encoding="utf-8")
        key = name.replace("-", "_").replace(".", "_")
        if key.endswith("_json") or key.endswith("_md"):
            key = key.rsplit("_", 1)[0]
        out[f"{key}_path"] = path
    return out


def _valid_call(
    *,
    evidence_dir: Path | None = None,
    stop_after_first_response: bool = False,
) -> WorkerCall:
    target = make_material_target("run-1", Stage.INVESTMENT_DEBATE, "bull_researcher", "call-8")
    upstream = MaterialReadRef(
        material_id="mat-frontline-market",
        capability_id="cap-frontline-market",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        l1_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md",
        l1_sha256="sha-l1",
        l2_index_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/index.json",
        l2_allowed_prefix="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/",
        call_id="call-1",
    )
    capability = OpenVikingReadCapability(
        capability_id="cap-frontline-market",
        material_id="mat-frontline-market",
        allowed_l1_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md",
        allowed_l1_sha256="sha-l1",
        allowed_l2_prefix="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/",
        manifest_entry_sha256="sha-manifest",
        allowed_l2_index_sha256="sha-l2-index",
    )
    return WorkerCall(
        call_id="call-8",
        run_id="run-1",
        worker_id="bull_researcher",
        stage=Stage.INVESTMENT_DEBATE,
        profile="US",
        ticker="AAPL",
        company_name="Apple",
        market="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-03",
        start_date="2026-01-01",
        end_date="2026-05-03",
        allowed_tools=("market_data", "openviking.read_with_capability"),
        upstream_materials=(upstream,),
        openviking_read_capabilities=(capability,),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir or Path("runs/run-1/calls/call-8/evidence"),
        stop_after_first_response=stop_after_first_response,
    )


class _FakeRunner:
    def __init__(self, payload: dict[str, object], probe_result: ProbeResult | None = None) -> None:
        self._payload = payload
        self._probe = probe_result or ProbeResult.passed()
        self.last_payload: dict[str, object] | None = None

    def probe(self) -> ProbeResult:
        return self._probe

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        self.last_payload = payload
        return self._payload


class _FakeStage:
    value = "frontline"
