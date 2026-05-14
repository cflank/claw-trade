from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools


EXPECTED_FRONTLINE_VISIBLE_TOOLS = {
    "market_analyst": ("market_market_data_pack",),
    "fundamental_analyst": ("fundamental_fundamentals_data_pack",),
    "news_analyst": ("news_news_data_pack",),
    "social_analyst": ("social_social_sentiment_pack",),
}
EXPECTED_US_FRONTLINE_VISIBLE_TOOLS = {
    "market_analyst": ("get_stock_data", "get_indicators"),
    "fundamental_analyst": ("get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"),
    "news_analyst": ("get_news", "get_global_news"),
    "social_analyst": ("get_news",),
}

PACK_TOOL_PARAM_FIELDS = {
    "ticker",
    "market",
    "company_name",
    "industry",
    "start_date",
    "end_date",
    "aliases",
    "approved_artifact_refs",
}

FORBIDDEN_PROVIDER_ATOMIC_TOOL_HINTS = (
    "akshare",
    "eastmoney",
    "mongodb",
    "l2",
    "raw",
)

EXPECTED_WORKER_PACK_EXPORTS = {
    "market_analyst": {"market_market_data_pack", "get_stock_data", "get_indicators"},
    "fundamental_analyst": {
        "fundamental_fundamentals_data_pack",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
    },
    "news_analyst": {"news_news_data_pack", "get_news", "get_global_news"},
    "social_analyst": {"social_social_sentiment_pack", "get_news"},
}


def test_cn_a_frontline_visible_tools_match_domain_pack_only() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    for worker_id, expected_tools in EXPECTED_FRONTLINE_VISIBLE_TOOLS.items():
        policy_result = load_stage_policy(Path("agents"), worker_id, "CN_A")
        assert policy_result.ok is True and policy_result.policy is not None
        actual_tools = resolve_tools(policy_result.policy, registry)
        assert actual_tools == expected_tools


def test_us_frontline_visible_tools_match_original_profile_tools() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    for worker_id, expected_tools in EXPECTED_US_FRONTLINE_VISIBLE_TOOLS.items():
        policy_result = load_stage_policy(Path("agents"), worker_id, "US")
        assert policy_result.ok is True and policy_result.policy is not None
        actual_tools = resolve_tools(policy_result.policy, registry)
        assert actual_tools == expected_tools


def test_cn_a_frontline_visible_tools_do_not_expose_provider_atomic_tools() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    for worker_id in EXPECTED_FRONTLINE_VISIBLE_TOOLS:
        policy_result = load_stage_policy(Path("agents"), worker_id, "CN_A")
        assert policy_result.ok is True and policy_result.policy is not None
        visible_tools = resolve_tools(policy_result.policy, registry)
        lowered = [name.lower() for name in visible_tools]
        for tool_name in lowered:
            assert not any(token in tool_name for token in FORBIDDEN_PROVIDER_ATOMIC_TOOL_HINTS), tool_name


def test_frontline_skill_manifest_only_exports_worker_profile_tools() -> None:
    for worker_id, expected_pack_tools in EXPECTED_WORKER_PACK_EXPORTS.items():
        manifest_path = Path("agents") / worker_id / "skills" / "manifest.yaml"
        parsed = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)
        skills = parsed.get("skills")
        assert isinstance(skills, list)

        export_entries = [entry for entry in skills if isinstance(entry, dict) and "tool_exports" in entry]
        exported_tools = set()
        for entry in export_entries:
            assert entry.get("workers") == [worker_id]
            tools = entry.get("tool_exports")
            assert isinstance(tools, list)
            exported_tools.update(str(tool) for tool in tools)
        assert exported_tools == expected_pack_tools


def test_openviking_write_material_schema_for_non_pm_workers_exposes_only_content() -> None:
    source_path = Path("third_party/openclaw/src/agents/pi-embedded-runner/run/openviking-tools.ts")
    source = source_path.read_text(encoding="utf-8")

    base_schema_match = re.search(
        r"const WRITE_MATERIAL_BASE_PARAMETERS = Type\.Object\(\s*\{(?P<body>.*?)\}\s*,\s*\{ additionalProperties: false \}\s*,\s*\);",
        source,
        flags=re.DOTALL,
    )
    assert base_schema_match is not None
    body = "".join(base_schema_match.group("body").split())
    assert body == "content:Type.Optional(Type.String()),"
    assert "WRITE_MATERIAL_PM_PARAMETERS" not in source
    assert "isPmDecisionScope" not in source
    assert "parameters: WRITE_MATERIAL_BASE_PARAMETERS" in source


def test_frontline_plugin_registers_profile_tools_with_expected_params_schema() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    plugin_path = repo_root / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js"

    script = f"""
import plugin from {json.dumps(str(plugin_path))};
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
console.log(JSON.stringify(registrations));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    registrations = json.loads(result.stdout)
    names = {item["name"] for item in registrations}
    assert names == {
        "market_market_data_pack",
        "get_stock_data",
        "get_indicators",
        "fundamental_fundamentals_data_pack",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
        "get_news",
        "get_global_news",
        "news_news_data_pack",
        "social_social_sentiment_pack",
    }
    expected_fields_by_name = {
        "get_stock_data": {"symbol", "start_date", "end_date"},
        "get_indicators": {"symbol", "indicator", "curr_date", "look_back_days"},
        "get_fundamentals": {"ticker", "curr_date"},
        "get_balance_sheet": {"ticker", "freq", "curr_date"},
        "get_cashflow": {"ticker", "freq", "curr_date"},
        "get_income_statement": {"ticker", "freq", "curr_date"},
        "get_news": {"ticker", "query", "start_date", "end_date"},
        "get_global_news": {"curr_date", "look_back_days", "limit"},
    }
    for item in registrations:
        assert item["schemaType"] == "object"
        assert item["additionalProperties"] is False
        assert set(item["fields"]) == expected_fields_by_name.get(item["name"], PACK_TOOL_PARAM_FIELDS)
