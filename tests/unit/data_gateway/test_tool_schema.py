from __future__ import annotations

from pathlib import Path

from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools
from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import all_worker_ids


_FRONTLINE_ALLOWED_PACK_TOOLS = {
    "claw_get_market_pack",
    "claw_get_fundamental_pack",
    "claw_get_news_pack",
    "claw_get_social_pack",
}

_FORBIDDEN_US_ATOMICS = {
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
}

_FORBIDDEN_OPENBB_PATTERNS = (
    "provider.",
    "admin.",
    "discovery.",
    "activate_tools",
    "execute_prompt",
    "list_providers",
    "cache",
    "raw",
    "debug",
)


def test_frontline_tool_schema_is_canonical_pack_only_under_openbb_flag(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED", "true")
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry
    agents_root = Path("agents")

    expected_frontline = {
        "market_analyst": ("claw_get_market_pack",),
        "fundamental_analyst": ("claw_get_fundamental_pack",),
        "news_analyst": ("claw_get_news_pack",),
        "social_analyst": ("claw_get_social_pack",),
    }

    for profile in ("US", "CN_A", "HK", "CRYPTO"):
        for worker_id in ("market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"):
            policy_result = load_stage_policy(agents_root, worker_id, profile)
            assert policy_result.ok is True and policy_result.policy is not None
            tools = resolve_tools(policy_result.policy, registry)
            assert tools
            assert set(tools).issubset(_FRONTLINE_ALLOWED_PACK_TOOLS)
            assert set(tools).isdisjoint(_FORBIDDEN_US_ATOMICS)
            for tool_name in tools:
                assert not any(pattern in tool_name for pattern in _FORBIDDEN_OPENBB_PATTERNS)
            assert tools == expected_frontline[worker_id]


def test_downstream_workers_have_empty_visible_tools_under_openbb_flag(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED", "true")
    registry = load_tool_registry().registry
    assert registry is not None
    agents_root = Path("agents")

    for profile in ("US", "CN_A", "HK", "CRYPTO"):
        for worker_id in all_worker_ids():
            policy_result = load_stage_policy(agents_root, worker_id, profile)
            if not policy_result.ok or policy_result.policy is None:
                continue
            policy = policy_result.policy
            if policy.stage == Stage.FRONTLINE:
                continue
            assert resolve_tools(policy, registry) == ()
