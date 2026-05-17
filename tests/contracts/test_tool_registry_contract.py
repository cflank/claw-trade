import json
from pathlib import Path

import pytest
import yaml

from claw_trade.config.profiles import ConfigError
from claw_trade.config.stage_policy import StagePolicy
from claw_trade.config.tool_names import (
    ToolRegistry,
    load_tool_registry,
    require_global_news_capability_for_news,
    resolve_tools,
)
from claw_trade.workflow.models import Stage


def test_load_tool_registry_has_minimum_intents() -> None:
    result = load_tool_registry()
    assert result.ok is True
    assert result.registry is not None
    intents = result.registry.intent_to_tools
    assert "market_data" not in intents
    assert intents["cn_a_market_data"] == ("market_market_data_pack",)
    assert intents["us_market_data"] == ("get_stock_data", "get_indicators")
    assert intents["crypto_market_data"] == ("crypto_market_data_pack",)
    assert intents["cn_a_fundamentals_data"] == ("fundamental_fundamentals_data_pack",)
    assert intents["us_fundamentals_data"] == (
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
    )
    assert intents["crypto_fundamentals_data"] == ("crypto_fundamental_data_pack",)
    assert intents["cn_a_news_data"] == ("news_news_data_pack",)
    assert intents["us_news_data"] == ("get_news", "get_global_news")
    assert intents["crypto_news_data"] == ("crypto_news_data_pack",)
    assert intents["cn_a_social_sentiment"] == ("social_social_sentiment_pack",)
    assert intents["us_social_sentiment"] == ("get_news",)
    assert intents["crypto_social_sentiment"] == ("crypto_social_sentiment_pack",)
    assert "openviking_write" in intents
    assert intents["openviking_read"] == ("openviking_read_with_capability",)
    assert intents["openviking_write"] == ("openviking_write_material",)


def test_hk_frontline_stage_policy_declares_approved_pack_tools() -> None:
    expected_tools = {
        "market_analyst": ["cn_a_market_data"],
        "fundamental_analyst": ["cn_a_fundamentals_data"],
        "news_analyst": ["cn_a_news_data"],
        "social_analyst": ["cn_a_social_sentiment"],
    }
    for worker_id, tool_intents in expected_tools.items():
        stage_path = Path("agents") / worker_id / "STAGES.yaml"
        parsed = yaml.safe_load(stage_path.read_text(encoding="utf-8"))
        hk_profile = parsed["profiles"]["HK"]
        assert hk_profile["approved"] is True
        assert hk_profile["prompt"] == "prompts/HK.md"
        assert hk_profile["tools"] == tool_intents
        assert hk_profile["openviking_access"] == "none"
        assert "failure" not in hk_profile


def test_hk_frontline_reuses_existing_pack_tool_contracts_without_hk_specific_visible_tools() -> None:
    hk_specific_tools = {
        "hk_market_data",
        "hk_fundamental_data",
        "hk_news_data",
        "hk_social_sentiment",
    }
    root = Path(__file__).resolve().parents[2]
    plugin_root = root / "openclaw_plugins" / "claw-trade-frontline-tools"
    manifest = json.loads((plugin_root / "openclaw.plugin.json").read_text(encoding="utf-8"))
    registered_tools = set(manifest["contracts"]["tools"])
    registry = load_tool_registry().registry
    assert registry is not None

    assert {
        "market_market_data_pack",
        "fundamental_fundamentals_data_pack",
        "news_news_data_pack",
        "social_social_sentiment_pack",
    }.issubset(registered_tools)
    assert registered_tools.isdisjoint(hk_specific_tools)
    assert set(registry.intent_to_tools).isdisjoint(hk_specific_tools)


def test_frontline_tools_are_registered_by_local_openclaw_plugin_not_old_core_files() -> None:
    root = Path(__file__).resolve().parents[2]
    plugin_root = root / "openclaw_plugins" / "claw-trade-frontline-tools"
    manifest = json.loads((plugin_root / "openclaw.plugin.json").read_text(encoding="utf-8"))
    assert set(manifest["contracts"]["tools"]) == {
        "market_market_data_pack",
        "crypto_market_data_pack",
        "get_stock_data",
        "get_indicators",
        "fundamental_fundamentals_data_pack",
        "crypto_fundamental_data_pack",
        "crypto_news_data_pack",
        "crypto_social_sentiment_pack",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
        "get_news",
        "get_global_news",
        "news_news_data_pack",
        "social_social_sentiment_pack",
    }

    plugin_entry = (plugin_root / "index.js").read_text(encoding="utf-8")
    assert "{ name, optional: true }" in plugin_entry
    assert not (root / "third_party/openclaw/src/agents/pi-embedded-runner/run/frontline-tools.ts").exists()
    assert not (root / "third_party/openclaw/src/agents/pi-embedded-runner/run/market-tools.ts").exists()


def test_unknown_stage_tool_intent_fails() -> None:
    registry = load_tool_registry().registry
    assert registry is not None
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("made_up_tool",),
        openviking_access="write",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_empty_stage_tool_intents_fail() -> None:
    registry = load_tool_registry().registry
    assert registry is not None
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=(),
        openviking_access="write",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


@pytest.mark.parametrize("worker_id", ("news_analyst", "social_analyst"))
def test_crypto_news_and_social_frontline_no_longer_allow_empty_tools(worker_id: str) -> None:
    registry = load_tool_registry().registry
    assert registry is not None
    policy = StagePolicy(
        worker_id=worker_id,
        stage=Stage.FRONTLINE,
        profile="CRYPTO",
        tool_intents=(),
        openviking_access="none",
        source_path=Path(f"agents/{worker_id}/STAGES.yaml"),
    )

    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_unknown_openviking_access_fails() -> None:
    registry = load_tool_registry().registry
    assert registry is not None
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("cn_a_market_data",),
        openviking_access="invalid",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_openviking_required_tool_missing_fails() -> None:
    registry = ToolRegistry({"cn_a_market_data": ("market_data",)})
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("cn_a_market_data",),
        openviking_access="write",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_news_guard_blocks_missing_macro_or_company_news() -> None:
    guard = require_global_news_capability_for_news(ToolRegistry({"cn_a_market_data": ("market_market_data_pack",)}))
    assert guard.ok is False
    assert guard.category == "config_blocked"


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_news_analyst_still_requires_profile_specific_news_tools(profile: str) -> None:
    registry = load_tool_registry().registry
    assert registry is not None
    policy = StagePolicy(
        worker_id="news_analyst",
        stage=Stage.FRONTLINE,
        profile=profile,
        tool_intents=(),
        openviking_access="none",
        source_path=Path("agents/news_analyst/STAGES.yaml"),
    )

    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_social_analyst_skill_manifest_must_mount_cn_a_social_data_skill() -> None:
    manifest_text = Path("agents/social_analyst/skills/manifest.yaml").read_text(encoding="utf-8")
    _assert_social_analyst_manifest_mounts_cn_a_social_data(manifest_text)


def test_social_analyst_skill_manifest_contract_fails_when_cn_a_social_skill_missing() -> None:
    broken_manifest = """
skills:
  - path: claw-trade-stage/SKILL.md
"""
    with pytest.raises(AssertionError):
        _assert_social_analyst_manifest_mounts_cn_a_social_data(broken_manifest)


def _assert_social_analyst_manifest_mounts_cn_a_social_data(manifest_text: str) -> None:
    parsed = yaml.safe_load(manifest_text)
    assert isinstance(parsed, dict)
    skills = parsed.get("skills")
    assert isinstance(skills, list)
    for entry in skills:
        assert isinstance(entry, dict)
        if entry.get("path") != "cn-a-social-data/SKILL.md":
            continue
        workers = entry.get("workers")
        assert workers == ["social_analyst"]
        return
    raise AssertionError("social_analyst 未挂载 cn-a-social-data skill manifest 条目")
