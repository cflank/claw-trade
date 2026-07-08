from __future__ import annotations

import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

from claw_trade.config.tool_names import load_tool_registry
from claw_trade.selection.controller import _rebuild_candidate_cache_summary_from_json
from claw_trade.selection.models import CandidateCacheRef

REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTION_PLUGIN_PATH = REPO_ROOT / "openclaw_plugins" / "claw-trade-selection-tools" / "index.js"
SEL04_APPROVED_ARTIFACTS = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "sel-04-candidate-cache-artifacts-2026-05-26"
    / "sel04-approval-run"
    / "candidate-cache"
    / "approved"
)


def _run_plugin_tool(
    *,
    worker_id: str,
    runtime_vars: dict[str, object],
    params: dict[str, object] | None = None,
    evidence_dir: str = "/tmp/selection-tool-evidence",
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    script = f"""
import plugin from {json.dumps(str(SELECTION_PLUGIN_PATH))};
const ctx = JSON.parse(process.argv[1]);
const params = JSON.parse(process.argv[2]);
const tools = [];
const api = {{
  registerTool(factory) {{
    tools.push(factory(ctx));
  }},
}};
plugin.register(api);
const tool = tools.find((item) => item.name === "claw_get_selection_candidate_cache");
if (!tool) {{
  throw new Error("tool not found");
}}
const result = await tool.execute("tool-call-1", params);
process.stdout.write(JSON.stringify(result));
"""
    ctx = {
        "singleWorkerCommand": {
            "run_id": "sel-run-1",
            "call_id": "dispatch-call-1",
            "worker_id": worker_id,
            "stage": "selection_review",
            "evidence_dir": evidence_dir,
            "runtime_vars": runtime_vars,
        }
    }
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(ctx), json.dumps(params or {})],
        cwd=cwd,
        env=command_env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _prepare_candidate_cache_fixture(tmp_path: Path, *, raw_complete_body: bool = False) -> dict[str, object]:
    artifact_root = tmp_path / "selection-artifacts"
    target = artifact_root / "sel04-approval-run" / "candidate-cache" / "approved"
    shutil.copytree(SEL04_APPROVED_ARTIFACTS, target)

    if raw_complete_body:
        raw_body = "\n".join(
            [
                "# A股候选缓存",
                "",
                "## 本轮范围",
                "- 交易日：2026-05-26",
                "- 市场：CN_A",
                "- 候选数量：20",
                "- 策略配置版本：cn_a.selection_strategy.v1",
                "- 权重版本：cn_a.selection_weights.v1",
                "",
                "## 候选事实表",
                "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 |",
                "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
                '| 1 | 600000.SH | 样本股票1 | 样本行业 | 100.000000 | {"liquidity_tradability_score":15} | myhhub/stock | myhhub_volume_rise | {"hit_volume_breakout":1} | {"amount":3000000000,"close":1612} | 0.000000 | 0.000000 | {"amount":3000000000} |',
            ]
        ) + "\n"
        (target / "candidate-cache.md").write_text(raw_body, encoding="utf-8")
        (target / "candidate-cache-summary.md").write_text(raw_body, encoding="utf-8")

    manifest_path = target / "candidate-cache-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw_complete_body:
        _refresh_verified_artifact(target / "candidate-cache.md", manifest)
        _refresh_verified_artifact(target / "candidate-cache-summary.md", manifest)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _refresh_verified_artifact(manifest_path, manifest, expected_field=None)
    candidate_cache_ref = {
        "selection_run_id": manifest["selection_run_id"],
        "material_id": "selection-candidate-cache-sel04-contract-proof",
        "l1_uri": f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/candidate-cache.md",
        "content_sha256": manifest["cache_body_sha256"],
        "manifest_ref": f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/candidate-cache-manifest.json",
        "approved_at": "2026-05-26T09:00:00Z",
        "expires_at": "2026-06-26T09:00:00Z",
        "cache_summary_ref": f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/candidate-cache-summary.md",
    }
    runtime_vars = {
        "market": "CN_A",
        "profile": "CN_A",
        "trade_date": manifest["trade_date"],
        "selection_run_id": manifest["selection_run_id"],
        "select_workflow_run_id": "wf-sel-05-proof",
        "selection_artifact_root": str(artifact_root),
        "candidate_cache_ref": candidate_cache_ref,
    }
    body_text = (target / "candidate-cache.md").read_text(encoding="utf-8")
    return {
        "runtime_vars": runtime_vars,
        "expected_sha256": manifest["cache_body_sha256"],
        "expected_body": body_text,
        "manifest": manifest,
    }


def _refresh_verified_artifact(path: Path, manifest: dict[str, object], *, expected_field: str | None = "cache_body_sha256") -> None:
    digest = sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    if expected_field is not None:
        manifest[expected_field] = digest
    verify_path = path.with_suffix(f"{path.suffix}.readback-verify.json")
    verify_payload = {
        "uri": f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/{path.name}",
        "path": str(path),
        "status": "verified",
        "expected_sha256": digest,
        "readback_sha256": digest,
        "size_bytes": path.stat().st_size,
        "verified_at": "2026-05-26T13:14:20.175678Z",
    }
    verify_path.write_text(json.dumps(verify_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_selection_tool_registry_maps_intent_to_unique_canonical_name() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    assert registry_result.registry.resolve_intent("selection_candidate_cache") == (
        "claw_get_selection_candidate_cache",
    )


def test_selection_plugin_registers_candidate_cache_tool_with_empty_params_schema() -> None:
    script = f"""
import plugin from {json.dumps(str(SELECTION_PLUGIN_PATH))};
const registrations = [];
const api = {{
  registerTool(factory) {{
    const tool = factory({{ singleWorkerCommand: {{}} }});
    registrations.push({{
      name: tool.name,
      schemaType: tool.parameters?.type,
      additionalProperties: tool.parameters?.additionalProperties,
      fields: Object.keys(tool.parameters?.properties ?? {{}}),
    }});
  }},
}};
plugin.register(api);
process.stdout.write(JSON.stringify(registrations));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    registrations = json.loads(completed.stdout)
    assert registrations == [
        {
            "name": "claw_get_selection_candidate_cache",
            "schemaType": "object",
            "additionalProperties": False,
            "fields": [],
        }
    ]


def test_selection_tool_reads_same_approved_cache_hash_for_strategist_and_skeptic(tmp_path: Path) -> None:
    fixture = _prepare_candidate_cache_fixture(tmp_path)
    manifest = fixture["manifest"]
    assert isinstance(manifest, dict)
    assert "cache_body_sha256" in manifest
    assert "source_lineage_refs" in manifest

    strategist = _run_plugin_tool(
        worker_id="selection_strategist",
        runtime_vars=fixture["runtime_vars"],
    )
    skeptic = _run_plugin_tool(
        worker_id="selection_skeptic",
        runtime_vars=fixture["runtime_vars"],
    )

    assert strategist.get("isError") is False
    assert skeptic.get("isError") is False

    strategist_details = strategist.get("details", {})
    skeptic_details = skeptic.get("details", {})
    assert strategist_details.get("ok") is True
    assert skeptic_details.get("ok") is True
    assert strategist_details.get("cache_body_sha256") == fixture["expected_sha256"]
    assert skeptic_details.get("cache_body_sha256") == fixture["expected_sha256"]
    assert strategist_details.get("cache_body_sha256") == skeptic_details.get("cache_body_sha256")
    assert strategist["content"][0]["text"] == skeptic["content"][0]["text"]
    assert strategist["content"][0]["text"] != fixture["expected_body"]
    for label in (
        "总分",
        "分项得分",
        "策略来源",
        "策略变体",
        "命中字段",
        "实际指标值",
        "风险扣分",
        "数据缺口扣分",
        "tie-break",
        "权重版本",
        "策略配置版本",
    ):
        assert label in strategist["content"][0]["text"]
    for snake_case in (
        "strategy_hit_coverage_score",
        "risk_penalty_score",
        "data_gap_penalty_score",
        "vol_ratio",
        "\"amount\":",
    ):
        assert snake_case not in strategist["content"][0]["text"]
    for forbidden in ("raw/debug", "provider envelope", "OpenViking", "viking://", "manifest", "lineage", "sha256"):
        assert forbidden.lower() not in strategist["content"][0]["text"].lower()


def test_selection_tool_rebuilds_complete_body_that_contains_raw_reader_field_names(tmp_path: Path) -> None:
    fixture = _prepare_candidate_cache_fixture(tmp_path, raw_complete_body=True)

    result = _run_plugin_tool(
        worker_id="selection_strategist",
        runtime_vars=fixture["runtime_vars"],
    )

    assert result.get("isError") is False
    text = result["content"][0]["text"]
    assert text != fixture["expected_body"]
    assert "成交额" in text
    for raw_field in (
        "liquidity_tradability_score",
        '"amount":',
        '"close":',
        "hit_volume_breakout",
    ):
        assert raw_field not in text


def test_selection_tool_accepts_candidate_cache_ref_json_string(tmp_path: Path) -> None:
    fixture = _prepare_candidate_cache_fixture(tmp_path)
    runtime_vars = dict(fixture["runtime_vars"])
    runtime_vars["candidate_cache_ref"] = json.dumps(runtime_vars["candidate_cache_ref"], ensure_ascii=False)
    result = _run_plugin_tool(
        worker_id="selection_strategist",
        runtime_vars=runtime_vars,
    )
    assert result.get("isError") is False
    details = result.get("details", {})
    assert details.get("ok") is True
    assert details.get("cache_body_sha256") == fixture["expected_sha256"]


def test_selection_tool_absolutizes_paths_before_python(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    python_stub = tmp_path / "capture-selection-python.py"
    python_stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, sys",
                "payload = json.load(sys.stdin)",
                "print(json.dumps({'ok': True, 'reader_brief_md': 'ok', 'runtime_context': payload['runtime_context']}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    python_stub.chmod(0o755)
    runtime_vars = {
        "market": "CN_A",
        "profile": "CN_A",
        "trade_date": "2026-05-26",
        "selection_run_id": "sel04-approval-run",
        "select_workflow_run_id": "wf-path-proof",
        "selection_artifact_root": "runs/selection/artifacts",
        "candidate_cache_ref": {
            "selection_run_id": "sel04-approval-run",
            "material_id": "selection-candidate-cache-path-proof",
            "l1_uri": "local://selection/sel04-approval-run/candidate-cache/approved/candidate-cache.md",
            "content_sha256": "a" * 64,
            "manifest_ref": "local://selection/sel04-approval-run/candidate-cache/approved/candidate-cache-manifest.json",
            "approved_at": "2026-05-26T09:00:00Z",
            "expires_at": "2026-06-26T09:00:00Z",
            "cache_summary_ref": "local://selection/sel04-approval-run/candidate-cache/approved/candidate-cache-summary.md",
        },
    }

    result = _run_plugin_tool(
        worker_id="selection_strategist",
        runtime_vars=runtime_vars,
        evidence_dir="runs/selection/workflows/wf-1/dispatches/dispatch-call-1",
        cwd=shared_root,
        env={"CLAW_TRADE_SELECTION_TOOL_PYTHON": str(python_stub)},
    )

    runtime_context = result["details"]["runtime_context"]
    assert runtime_context["evidence_root"] == str(
        shared_root
        / "runs"
        / "selection"
        / "workflows"
        / "wf-1"
        / "dispatches"
        / "dispatch-call-1"
        / "selection-candidate-cache-tool-evidence"
    )
    assert runtime_context["selection_artifact_root"] == str(
        shared_root / "runs" / "selection" / "artifacts"
    )


def test_selection_tool_uses_market_python_alias_when_specific_env_missing(tmp_path: Path) -> None:
    marker = tmp_path / "selection-python-called.txt"
    python_stub = tmp_path / "probe-selection-python.py"
    python_stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, sys",
                f"marker = {json.dumps(str(marker))}",
                "json.load(sys.stdin)",
                "open(marker, 'w', encoding='utf-8').write('called')",
                "print(json.dumps({'ok': True, 'reader_brief_md': 'ok'}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    python_stub.chmod(0o755)

    result = _run_plugin_tool(
        worker_id="selection_strategist",
        runtime_vars={
            "market": "CN_A",
            "profile": "CN_A",
            "trade_date": "2026-05-26",
            "selection_run_id": "sel04-approval-run",
            "select_workflow_run_id": "wf-market-python-alias-proof",
            "selection_artifact_root": str(tmp_path / "selection-artifacts"),
            "candidate_cache_ref": {
                "selection_run_id": "sel04-approval-run",
                "material_id": "selection-candidate-cache-python-alias-proof",
                "l1_uri": "local://selection/sel04-approval-run/candidate-cache/approved/candidate-cache.md",
                "content_sha256": "a" * 64,
                "manifest_ref": "local://selection/sel04-approval-run/candidate-cache/approved/candidate-cache-manifest.json",
                "approved_at": "2026-05-26T09:00:00Z",
                "expires_at": "2026-06-26T09:00:00Z",
                "cache_summary_ref": "local://selection/sel04-approval-run/candidate-cache/approved/candidate-cache-summary.md",
            },
        },
        env={
            "CLAW_TRADE_SELECTION_TOOL_PYTHON": "",
            "OPENCLAW_MARKET_TOOL_PYTHON": str(python_stub),
        },
    )

    assert result.get("isError") is False
    assert marker.read_text(encoding="utf-8") == "called"


def test_selection_tool_rejects_business_params(tmp_path: Path) -> None:
    fixture = _prepare_candidate_cache_fixture(tmp_path)
    result = _run_plugin_tool(
        worker_id="selection_strategist",
        runtime_vars=fixture["runtime_vars"],
        params={"ticker": "600000.SH"},
    )
    assert result.get("isError") is True
    error = result.get("details", {}).get("error", {})
    assert error.get("code") == "TOOL_PARAMS_INVALID"


def test_selection_tool_error_text_hides_internal_details_from_model(tmp_path: Path) -> None:
    fixture = _prepare_candidate_cache_fixture(tmp_path)
    result = _run_plugin_tool(
        worker_id="provider=official_api_tushare api_name=hk_mins token=secret",
        runtime_vars=fixture["runtime_vars"],
    )

    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "当前 worker 不能使用候选缓存工具" in text
    for forbidden in ("official_api_tushare", "api_name", "hk_mins", "token", "secret"):
        assert forbidden not in text


def test_selection_controller_rebuild_summary_from_json_hides_typical_machine_keys(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    artifact_root = tmp_path / "runs" / "selection" / "artifacts"
    target = artifact_root / "sel04-approval-run" / "candidate-cache" / "approved"
    shutil.copytree(SEL04_APPROVED_ARTIFACTS, target)
    manifest = json.loads((target / "candidate-cache-manifest.json").read_text(encoding="utf-8"))
    candidate_cache_ref = CandidateCacheRef(
        selection_run_id=manifest["selection_run_id"],
        material_id="selection-candidate-cache-sel04-contract-proof",
        l1_uri=f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/candidate-cache.md",
        content_sha256=manifest["cache_body_sha256"],
        manifest_ref=f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/candidate-cache-manifest.json",
        approved_at="2026-05-26T09:00:00Z",
        expires_at="2026-06-26T09:00:00Z",
        cache_summary_ref=f"local://selection/{manifest['selection_run_id']}/candidate-cache/approved/candidate-cache-summary.md",
    )
    monkeypatch.chdir(tmp_path)
    rebuilt = _rebuild_candidate_cache_summary_from_json(candidate_cache_ref)
    assert rebuilt is not None
    assert "成交额" in rebuilt
    assert "总分" in rebuilt
    for snake_case in (
        "strategy_hit_coverage_score",
        "risk_penalty_score",
        "data_gap_penalty_score",
        "vol_ratio",
        "\"amount\":",
    ):
        assert snake_case not in rebuilt
