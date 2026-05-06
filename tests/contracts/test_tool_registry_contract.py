from pathlib import Path

import pytest

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
    assert "market_data" in intents
    assert intents["market_data"] == (
        "market.stock_price",
        "market.techlab_analyze",
    )
    assert "openviking_write" in intents
    assert intents["openviking_read"] == ("openviking.read_with_capability",)
    assert intents["openviking_write"] == ("openviking.write_material",)


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


def test_unknown_openviking_access_fails() -> None:
    registry = load_tool_registry().registry
    assert registry is not None
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("market_data",),
        openviking_access="invalid",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_openviking_required_tool_missing_fails() -> None:
    registry = ToolRegistry({"market_data": ("market_data",)})
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("market_data",),
        openviking_access="write",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_news_guard_blocks_missing_macro_or_company_news() -> None:
    guard = require_global_news_capability_for_news(ToolRegistry({"company_news": ("company_news",)}))
    assert guard.ok is False
    assert guard.category == "config_blocked"
