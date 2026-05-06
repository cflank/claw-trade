from pathlib import Path

import pytest

from claw_trade.config.profiles import ConfigError
from claw_trade.config.stage_policy import StagePolicy, load_stage_policy
from claw_trade.config.tool_names import ToolRegistry, load_tool_registry, resolve_tools
from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import all_worker_ids, worker_by_id


@pytest.fixture
def agents_root() -> Path:
    return Path("agents")


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_stage_tool_policy_never_returns_empty_tools(agents_root: Path, profile: str) -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    for worker_id in all_worker_ids():
        policy_result = load_stage_policy(agents_root, worker_id, profile)
        assert policy_result.ok is True and policy_result.policy is not None
        tools = resolve_tools(policy_result.policy, registry)
        assert tools, f"{worker_id}/{profile} tools 为空"


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_market_data_intent_resolves_to_provider_visible_mcp_tools(
    agents_root: Path, profile: str
) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy_result = load_stage_policy(agents_root, "market_analyst", profile)
    assert policy_result.ok is True and policy_result.policy is not None
    tools = resolve_tools(policy_result.policy, registry)
    assert "market.stock_price" in tools
    assert "market.techlab_analyze" in tools
    assert "openviking.write_material" in tools


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_openviking_read_write_are_stage_scoped(agents_root: Path, profile: str) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    for worker_id in all_worker_ids():
        policy_result = load_stage_policy(agents_root, worker_id, profile)
        assert policy_result.ok is True and policy_result.policy is not None
        policy = policy_result.policy
        tools = resolve_tools(policy, registry)

        if policy.stage == Stage.FRONTLINE:
            assert "openviking.write_material" in tools
            assert "openviking.read_with_capability" not in tools
        else:
            assert "openviking.read_with_capability" in tools
            assert "openviking.write_material" in tools


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_news_analyst_must_include_company_and_macro_news(agents_root: Path, profile: str) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy_result = load_stage_policy(agents_root, "news_analyst", profile)
    assert policy_result.ok is True and policy_result.policy is not None
    tools = resolve_tools(policy_result.policy, registry)
    assert "company_news" in tools
    assert "macro_news" in tools


def test_unknown_openviking_access_fails() -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("market_data",),
        openviking_access="unknown",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_empty_intents_fail() -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy = StagePolicy(
        worker_id="market_analyst",
        stage=worker_by_id("market_analyst").stage,
        profile="US",
        tool_intents=(),
        openviking_access="write",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_missing_openviking_tool_mapping_fails() -> None:
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
