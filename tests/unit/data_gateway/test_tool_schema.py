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
    "claw_get_policy_pack",
    "claw_get_hot_money_pack",
    "claw_get_lockup_pack",
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

_FORBIDDEN_LEGACY_ALIASES = {
    "market_market_data_pack",
    "us_market_data_pack",
    "cn_a_market_data_pack",
    "crypto_market_data_pack",
    "fundamental_fundamentals_data_pack",
    "us_fundamentals_data_pack",
    "cn_a_fundamentals_data_pack",
    "crypto_fundamental_data_pack",
    "news_news_data_pack",
    "us_news_data_pack",
    "cn_a_news_data_pack",
    "crypto_news_data_pack",
    "social_social_sentiment_pack",
    "us_social_sentiment_pack",
    "cn_a_social_sentiment_pack",
    "crypto_social_sentiment_pack",
    "bb_crypto_data",
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

    expected_frontline_by_profile = {
        "US": {
            "market_analyst": ("claw_get_market_pack",),
            "fundamental_analyst": ("claw_get_fundamental_pack",),
            "news_analyst": ("claw_get_news_pack",),
            "social_analyst": ("claw_get_social_pack",),
        },
        "CN_A": {
            "market_analyst": ("claw_get_market_pack",),
            "fundamental_analyst": ("claw_get_fundamental_pack",),
            "news_analyst": ("claw_get_news_pack",),
            "social_analyst": ("claw_get_social_pack",),
            "policy_analyst": ("claw_get_policy_pack",),
            "hot_money_tracker": ("claw_get_hot_money_pack",),
            "lockup_watcher": ("claw_get_lockup_pack",),
        },
        "HK": {
            "market_analyst": ("claw_get_market_pack",),
            "fundamental_analyst": ("claw_get_fundamental_pack",),
            "news_analyst": ("claw_get_news_pack",),
            "social_analyst": ("claw_get_social_pack",),
        },
        "CRYPTO": {
            "market_analyst": ("claw_get_market_pack",),
            "fundamental_analyst": ("claw_get_fundamental_pack",),
            "news_analyst": ("claw_get_news_pack",),
            "social_analyst": ("claw_get_social_pack",),
        },
    }

    for profile, expected_frontline in expected_frontline_by_profile.items():
        for worker_id in expected_frontline:
            policy_result = load_stage_policy(agents_root, worker_id, profile)
            assert policy_result.ok is True and policy_result.policy is not None
            tools = resolve_tools(policy_result.policy, registry)
            assert tools
            assert set(tools).issubset(_FRONTLINE_ALLOWED_PACK_TOOLS)
            assert set(tools).isdisjoint(_FORBIDDEN_US_ATOMICS)
            assert set(tools).isdisjoint(_FORBIDDEN_LEGACY_ALIASES)
            for tool_name in tools:
                assert tool_name.startswith("claw_get_")
                assert tool_name.endswith("_pack")
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
            tools = resolve_tools(policy, registry)
            assert tools == ()
            assert set(tools).isdisjoint(_FORBIDDEN_LEGACY_ALIASES)
