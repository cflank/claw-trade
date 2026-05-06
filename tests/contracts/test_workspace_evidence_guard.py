from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw_trade.artifacts.refs import make_material_target
from claw_trade.guards.workspace_evidence import validate_workspace_evidence
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_workspace_evidence_guard_passes_with_required_evidence(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    evidence_path.write_text(
        json.dumps(workspace_payload(call), ensure_ascii=False),
        encoding="utf-8",
    )
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert guard.ok
    assert guard.category == "workspace_evidence"


def test_workspace_evidence_guard_fails_when_file_missing(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "读取失败" in guard.reason


def test_workspace_evidence_guard_fails_on_invalid_json(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    evidence_path.write_text("{bad json", encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "JSON 解析失败" in guard.reason


def test_workspace_evidence_guard_fails_on_source_mismatch(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    payload["source"] = "openclaw_workspace_runtime"
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "source 非法" in guard.reason


def test_workspace_evidence_guard_fails_on_identity_mismatch(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    payload["worker_id"] = "news_analyst"
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "worker_id 不匹配" in guard.reason


def test_workspace_evidence_guard_fails_when_text_asset_sha_missing(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    payload["text_assets"] = [{"path": "agents/market_analyst/prompts/US.md", "sha256": ""}]
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "text_assets[0].sha256 缺失或为空" in guard.reason


def test_workspace_evidence_guard_fails_when_stage_policy_selected_stage_mismatch(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    stage_policy = dict(payload["stage_policy"])  # type: ignore[arg-type]
    stage_policy["selected_stage"] = "risk_debate"
    payload["stage_policy"] = stage_policy
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "selected_stage 不匹配" in guard.reason


def test_workspace_evidence_guard_accepts_worker_relative_paths(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    payload["identity"]["path"] = f"{call.worker_id}/IDENTITY.md"  # type: ignore[index]
    payload["skills_manifest"]["path"] = f"{call.worker_id}/skills/manifest.yaml"  # type: ignore[index]
    payload["stage_policy"]["path"] = f"{call.worker_id}/STAGES.yaml"  # type: ignore[index]
    payload["text_assets"] = [
        {"path": f"{call.worker_id}/prompts/US.md", "sha256": "sha-text-us"},
        {"path": f"{call.worker_id}/SKILLS.md", "sha256": "sha-text-skills"},
    ]
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert guard.ok
    assert guard.category == "workspace_evidence"


def test_workspace_evidence_guard_accepts_openclaw_absolute_paths(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    agents_root = (tmp_path / "agents").resolve()
    worker_root = (agents_root / call.worker_id).resolve()
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    payload["identity"]["path"] = (worker_root / "IDENTITY.md").as_posix()  # type: ignore[index]
    payload["skills_manifest"]["path"] = (worker_root / "skills" / "manifest.yaml").as_posix()  # type: ignore[index]
    payload["stage_policy"]["path"] = (worker_root / "STAGES.yaml").as_posix()  # type: ignore[index]
    payload["text_assets"] = [
        {"path": (worker_root / "prompts" / "US.md").as_posix(), "sha256": "sha-text-us"},
        {"path": (worker_root / "SKILLS.md").as_posix(), "sha256": "sha-text-skills"},
    ]
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, agents_root)

    assert guard.ok
    assert guard.category == "workspace_evidence"


@pytest.mark.parametrize(
    ("field_name", "bad_path"),
    (
        ("identity", "agents/market_analyst/IDENTITY_BACKUP.md"),
        ("skills_manifest", "agents/market_analyst/skills/MANIFEST.yaml"),
        ("stage_policy", "agents/market_analyst/STAGES_BACKUP.yaml"),
    ),
)
def test_workspace_evidence_guard_rejects_wrong_fixed_path(
    tmp_path: Path,
    field_name: str,
    bad_path: str,
) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    block = dict(payload[field_name])  # type: ignore[arg-type]
    block["path"] = bad_path
    payload[field_name] = block
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "必须指向当前 worker" in guard.reason


@pytest.mark.parametrize(
    ("field_name", "bad_path"),
    (
        ("identity", "agents/market_analyst/../news_analyst/IDENTITY.md"),
        ("skills_manifest", "market_analyst/../news_analyst/skills/manifest.yaml"),
        ("stage_policy", "agents/market_analyst_backup/STAGES.yaml"),
        ("text_assets", "agents/market_analyst/../news_analyst/prompts/US.md"),
    ),
)
def test_workspace_evidence_guard_rejects_path_bypass(
    tmp_path: Path,
    field_name: str,
    bad_path: str,
) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    if field_name == "text_assets":
        payload["text_assets"] = [{"path": bad_path, "sha256": "sha-text-us"}]
    else:
        block = dict(payload[field_name])  # type: ignore[arg-type]
        block["path"] = bad_path
        payload[field_name] = block
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "不属于当前 worker" in guard.reason


def test_workspace_evidence_guard_rejects_windows_drive_path(tmp_path: Path) -> None:
    call = build_call(tmp_path)
    evidence_path = call.evidence_dir / "workspace-evidence.json"
    payload = workspace_payload(call)
    payload["identity"]["path"] = "C:/repo/agents/market_analyst/IDENTITY.md"  # type: ignore[index]
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence = build_provider_evidence(call, evidence_path)

    guard = validate_workspace_evidence(call, evidence, Path("agents"))

    assert not guard.ok
    assert guard.reason is not None
    assert "不属于当前 worker" in guard.reason


def build_call(tmp_path: Path) -> WorkerCall:
    call_id = "call-1"
    run_id = "run-1"
    worker_id = "market_analyst"
    stage = Stage.FRONTLINE
    target = make_material_target(run_id, stage, worker_id, call_id)
    evidence_dir = tmp_path / "runs" / run_id / "calls" / call_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return WorkerCall(
        call_id=call_id,
        run_id=run_id,
        worker_id=worker_id,
        stage=stage,
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
        stop_after_first_response=False,
    )


def build_provider_evidence(call: WorkerCall, workspace_evidence_path: Path) -> ProviderEvidence:
    return ProviderEvidence(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        openclaw_run_id="openclaw-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=workspace_evidence_path,
        provider_request_path=call.evidence_dir / "provider-request.json",
        visible_tools_path=call.evidence_dir / "visible-tools.json",
        first_response_path=call.evidence_dir / "first-response.json",
        tool_calls_status="recorded",
        tool_calls_path=call.evidence_dir / "tool-calls.json",
        raw_output_path=call.evidence_dir / "raw-output.md",
        openviking_receipt_path=call.evidence_dir / "openviking-receipt.json",
    )


def workspace_payload(call: WorkerCall) -> dict[str, object]:
    return {
        "source": "openclaw_workspace_loader",
        "run_id": call.run_id,
        "call_id": call.call_id,
        "worker_id": call.worker_id,
        "stage": call.stage.value,
        "profile": call.profile,
        "openclaw_run_id": "openclaw-run-1",
        "workspace_root": f"agents/{call.worker_id}",
        "identity": {
            "path": f"agents/{call.worker_id}/IDENTITY.md",
            "sha256": "sha-identity",
        },
        "skills_manifest": {
            "path": f"agents/{call.worker_id}/skills/manifest.yaml",
            "sha256": "sha-skills",
        },
        "stage_policy": {
            "path": f"agents/{call.worker_id}/STAGES.yaml",
            "sha256": "sha-stage",
            "selected_stage": call.stage.value,
        },
        "text_assets": [
            {
                "path": f"agents/{call.worker_id}/prompts/US.md",
                "sha256": "sha-text-us",
            },
            {
                "path": f"agents/{call.worker_id}/SKILLS.md",
                "sha256": "sha-text-skills",
            },
        ],
        "loaded_at": "2026-05-04T10:10:10Z",
    }
