from __future__ import annotations

import json
from pathlib import Path

from claw_trade.artifacts.refs import make_material_target
from claw_trade.guards.visible_tools import validate_visible_tools
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_visible_tools_guard_passes_when_same_source_and_same_names(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=("market_data", "openviking_write_material"))
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    _write_json(
        provider_request_path,
        {
            "source": "provider_request_capture",
            "payload": {
                "tools": [
                    {"type": "function", "function": {"name": "market_data"}},
                    {"name": "openviking_write_material"},
                ]
            },
        },
    )
    _write_json(
        visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(provider_request_path),
            "tools": ["openviking_write_material", "market_data", "market_data"],
        },
    )
    guard = validate_visible_tools(call, evidence)
    assert guard.ok


def test_visible_tools_guard_fails_when_source_is_not_provider_request(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=("market_data",))
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    _write_json(provider_request_path, {"source": "provider_request_capture", "payload": {"tools": ["market_data"]}})
    _write_json(
        visible_tools_path,
        {
            "source": "allowlist_copy",
            "provider_request_path": str(provider_request_path),
            "tools": ["market_data"],
        },
    )
    guard = validate_visible_tools(call, evidence)
    assert not guard.ok
    assert guard.category == "visible_tools"
    assert guard.reason is not None and "source 不可信" in guard.reason


def test_visible_tools_guard_fails_when_provider_request_path_mismatch(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=("market_data",))
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    other_provider_path = tmp_path / "runs" / "run-1" / "calls" / "call-other" / "provider-request.json"
    _write_json(provider_request_path, {"source": "provider_request_capture", "payload": {"tools": ["market_data"]}})
    _write_json(
        visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(other_provider_path),
            "tools": ["market_data"],
        },
    )
    guard = validate_visible_tools(call, evidence)
    assert not guard.ok
    assert guard.reason is not None and "provider_request_path" in guard.reason


def test_visible_tools_guard_fails_when_tools_empty(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=("market_data",))
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    _write_json(provider_request_path, {"source": "provider_request_capture", "payload": {"tools": []}})
    _write_json(
        visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(provider_request_path),
            "tools": [],
        },
    )
    guard = validate_visible_tools(call, evidence)
    assert not guard.ok
    assert guard.reason is not None and "不能为空" in guard.reason


def test_visible_tools_guard_passes_when_pure_prompt_worker_has_no_tools(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=())
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    _write_json(provider_request_path, {"source": "provider_request_capture", "payload": {"tools": []}})
    _write_json(
        visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(provider_request_path),
            "tools": [],
        },
    )

    guard = validate_visible_tools(call, evidence)

    assert guard.ok


def test_visible_tools_guard_fails_when_visible_tools_not_equal_allowed_tools(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=("market_data", "openviking_write_material"))
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    _write_json(
        provider_request_path,
        {
            "source": "provider_request_capture",
            "payload": {"tools": ["market_data", "macro_news"]},
        },
    )
    _write_json(
        visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(provider_request_path),
            "tools": ["market_data", "macro_news"],
        },
    )
    guard = validate_visible_tools(call, evidence)
    assert not guard.ok
    assert guard.reason is not None and "call.allowed_tools 不一致" in guard.reason


def test_visible_tools_guard_fails_when_provider_and_visible_different_names(tmp_path: Path) -> None:
    call = _sample_call(tmp_path, allowed_tools=("market_data",))
    evidence, provider_request_path, visible_tools_path = _sample_evidence_paths(tmp_path)
    _write_json(
        provider_request_path,
        {
            "source": "provider_request_capture",
            "payload": {"tools": ["market_data"]},
        },
    )
    _write_json(
        visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(provider_request_path),
            "tools": ["macro_news"],
        },
    )
    guard = validate_visible_tools(call, evidence)
    assert not guard.ok
    assert guard.reason is not None and "来源不可验证" in guard.reason


def _sample_call(tmp_path: Path, *, allowed_tools: tuple[str, ...]) -> WorkerCall:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
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
        allowed_tools=allowed_tools,
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )


def _sample_evidence_paths(tmp_path: Path) -> tuple[ProviderEvidence, Path, Path]:
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    provider_request_path = evidence_dir / "provider-request.json"
    visible_tools_path = evidence_dir / "visible-tools.json"
    workspace_path = evidence_dir / "workspace.json"
    first_response_path = evidence_dir / "first-response.json"
    tool_calls_path = evidence_dir / "tool-calls.json"
    raw_output_path = evidence_dir / "raw-output.md"
    receipt_path = evidence_dir / "openviking-receipt.json"
    _write_json(workspace_path, {"source": "openclaw_workspace_loader"})
    _write_json(first_response_path, {"source": "openclaw_first_model_event"})
    _write_json(tool_calls_path, {"source": "model_tool_events", "calls": []})
    raw_output_path.write_text("", encoding="utf-8")
    _write_json(receipt_path, {"receipt_id": "r-1"})
    evidence = ProviderEvidence(
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=workspace_path,
        provider_request_path=provider_request_path,
        visible_tools_path=visible_tools_path,
        first_response_path=first_response_path,
        tool_calls_status="recorded",
        tool_calls_path=tool_calls_path,
        raw_output_path=raw_output_path,
        openviking_receipt_path=receipt_path,
    )
    return evidence, provider_request_path, visible_tools_path


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
