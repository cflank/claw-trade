from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw_trade.artifacts.refs import make_material_target
from claw_trade.guards.provider_request import validate_provider_request
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_validate_provider_request_passes_for_real_capture_payload(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    write_json(
        evidence.provider_request_path,
        provider_request_payload(call, evidence),
    )

    guard = validate_provider_request(call, evidence)

    assert guard.ok
    assert guard.category == "provider_request"


def test_validate_provider_request_fails_when_file_missing(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.category == "provider_request"
    assert guard.reason is not None and "读取失败" in guard.reason


def test_validate_provider_request_fails_on_invalid_json(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    evidence.provider_request_path.write_text("{invalid", encoding="utf-8")

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and "JSON 解析失败" in guard.reason


@pytest.mark.parametrize(
    "source",
    (
        "renderer_output",
        "export_report",
        "logs",
        "reconstructed_prompt",
        "reconstructed_provider_request",
    ),
)
def test_validate_provider_request_rejects_disallowed_sources(tmp_path: Path, source: str) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    payload = provider_request_payload(call, evidence)
    payload["source"] = source
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and "source 非法" in guard.reason


def test_validate_provider_request_fails_when_runtime_markers_missing(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    payload = provider_request_payload(call, evidence)
    payload.pop("runtime_markers")
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and "runtime_markers 缺失" in guard.reason


def test_validate_provider_request_rejects_legacy_runtime_marker_string(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    payload = provider_request_payload(call, evidence)
    payload.pop("runtime_markers")
    payload["runtime_marker"] = "openclaw_single_agent_turn"
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and "runtime_markers 缺失" in guard.reason


def test_validate_provider_request_fails_on_runtime_marker_mismatch(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    payload = provider_request_payload(call, evidence)
    runtime_markers = dict(payload["runtime_markers"])  # type: ignore[arg-type]
    runtime_markers["worker_id"] = "news_analyst"
    payload["runtime_markers"] = runtime_markers
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and "runtime_markers.worker_id 与当前调用不一致" in guard.reason


@pytest.mark.parametrize(
    "patcher, expected",
    (
        (lambda p: p["payload"].pop("messages"), "payload.messages 必须是非空列表"),
        (lambda p: p["payload"].__setitem__("messages", []), "payload.messages 必须是非空列表"),
        (lambda p: p["payload"].__setitem__("messages", ["hello"]), "payload.messages 结构不可信"),
        (lambda p: p["payload"].pop("tools"), "payload.tools 必须是非空列表"),
        (lambda p: p["payload"].__setitem__("tools", []), "payload.tools 必须是非空列表"),
        (lambda p: p["payload"].__setitem__("tools", ["market_data"]), "payload.tools 结构不可信"),
    ),
)
def test_validate_provider_request_fails_on_bad_payload_shape(
    tmp_path: Path,
    patcher,
    expected: str,
) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    payload = provider_request_payload(call, evidence)
    patcher(payload)
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and expected in guard.reason


def test_validate_provider_request_allows_no_tools_for_pure_prompt_worker(tmp_path: Path) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    call = _replace_call_tools(call, allowed_tools=())
    payload = provider_request_payload(call, evidence)
    payload_body = payload["payload"]
    assert isinstance(payload_body, dict)
    payload_body["tools"] = []
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert guard.ok


@pytest.mark.parametrize("profile", ("US", "CN_A", "HK", "CRYPTO"))
@pytest.mark.parametrize(
    "pollution",
    (
        "material_id=mat-1",
        "viking://run-1/materials/mat-1",
        "[ApprovedMaterials]",
        "OpenViking",
        "OpenClaw",
        "openviking_read_with_capability",
        "你正在执行当前分析师的一轮任务。",
    ),
)
def test_validate_provider_request_rejects_model_visible_protocol_pollution(
    tmp_path: Path,
    profile: str,
    pollution: str,
) -> None:
    call, evidence = sample_call_and_evidence(tmp_path)
    call = _replace_call_tools(call, profile=profile, allowed_tools=())
    payload = provider_request_payload(call, evidence)
    payload_body = payload["payload"]
    assert isinstance(payload_body, dict)
    payload_body["tools"] = []
    payload_body["messages"] = [{"role": "user", "content": f"正常报告正文\n{pollution}"}]
    runtime_markers = payload["runtime_markers"]
    assert isinstance(runtime_markers, dict)
    runtime_markers["profile"] = profile
    write_json(evidence.provider_request_path, payload)

    guard = validate_provider_request(call, evidence)

    assert not guard.ok
    assert guard.reason is not None and "模型可见 prompt 含机器协议" in guard.reason


def sample_call_and_evidence(tmp_path: Path) -> tuple[WorkerCall, ProviderEvidence]:
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    call = WorkerCall(
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
        allowed_tools=("market_data", "openviking_write_material"),
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )
    evidence = ProviderEvidence(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=evidence_dir / "workspace-evidence.json",
        provider_request_path=evidence_dir / "provider-request.json",
        visible_tools_path=evidence_dir / "visible-tools.json",
        first_response_path=evidence_dir / "first-response.json",
        tool_calls_status="recorded",
        tool_calls_path=evidence_dir / "tool-calls.json",
        raw_output_path=evidence_dir / "raw-output.md",
        openviking_receipt_path=evidence_dir / "openviking-receipt.json",
    )
    return call, evidence


def provider_request_payload(call: WorkerCall, evidence: ProviderEvidence) -> dict[str, object]:
    return {
        "source": "provider_request_capture",
        "provider": "openai",
        "request_id": evidence.provider_request_id,
        "payload": {
            "messages": [
                {"role": "system", "content": "你是 market_analyst"},
                {"role": "user", "content": "分析 AAPL"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "market_data", "description": "读取市场数据"},
                }
            ],
        },
        "runtime_markers": {
            "run_id": call.run_id,
            "call_id": call.call_id,
            "worker_id": call.worker_id,
            "stage": call.stage.value,
            "profile": call.profile,
            "openclaw_run_id": evidence.openclaw_run_id,
        },
    }


def _replace_call_tools(
    call: WorkerCall,
    *,
    allowed_tools: tuple[str, ...],
    profile: str | None = None,
) -> WorkerCall:
    from dataclasses import replace

    return replace(call, allowed_tools=allowed_tools, profile=profile or call.profile)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
