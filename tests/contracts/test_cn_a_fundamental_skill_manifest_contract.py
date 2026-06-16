from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")
POLICY_PATH = SCRIPTS_ROOT / "policy.py"


def test_fundamental_analyst_skill_manifest_mounts_cn_a_fundamental_data() -> None:
    manifest_text = Path("agents/fundamental_analyst/skills/manifest.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(manifest_text)
    assert isinstance(parsed, dict)
    skills = parsed.get("skills")
    assert isinstance(skills, list)
    for entry in skills:
        assert isinstance(entry, dict)
        if entry.get("path") != "cn-a-fundamental-data/SKILL.md":
            continue
        assert entry.get("workers") == ["fundamental_analyst"]
        assert entry.get("tool_exports") == ["claw_request_data"]
        return
    raise AssertionError("fundamental_analyst 未挂载 cn-a-fundamental-data skill manifest 条目")


def test_resolve_fundamental_visible_tools_is_exactly_data_request_tool_for_cn_a() -> None:
    policy = POLICY_MODULE.resolve_fundamental_visible_tools("fundamental_analyst", "CN_A")
    assert set(policy.tool_names) == {"claw_request_data"}
    assert all(not tool.startswith("tushare.") for tool in policy.tool_names)
    assert all(not tool.startswith("akshare.") for tool in policy.tool_names)
    assert all(not tool.startswith("baostock.") for tool in policy.tool_names)


def test_visible_tool_snapshot_invalid_when_provider_tool_leaks() -> None:
    invalid = POLICY_MODULE.is_visible_tool_snapshot_invalid_v1(
        {
            "worker_id": "fundamental_analyst",
            "market_profile": "CN_A",
            "tools": (
                "claw_request_data",
                "tushare.fina_indicator",
            ),
        }
    )
    assert invalid is True


def _load_policy_module():
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("cn_a_fundamental_policy_contract", POLICY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 fundamental policy 模块: {POLICY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cn_a_fundamental_policy_contract"] = module
    spec.loader.exec_module(module)
    return module


POLICY_MODULE = _load_policy_module()
