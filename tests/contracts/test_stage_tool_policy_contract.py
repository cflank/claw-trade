import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from claw_trade.config.profiles import ConfigError
from claw_trade.config.stage_policy import StagePolicy, load_stage_policy
from claw_trade.config.tool_names import ToolRegistry, load_tool_registry, resolve_tools
from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import all_worker_ids, worker_by_id

SOCIAL_POLICY_PATH = Path("agents/social_analyst/skills/cn-a-social-data/scripts/policy.py")
FORMAL_CRYPTO_WORKERS: tuple[str, ...] = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
    "bull_researcher",
    "bear_researcher",
    "research_manager",
    "trader",
    "risk_challenger",
    "risk_guardian",
    "risk_moderator",
    "portfolio_manager",
    "report_polisher",
)


@pytest.fixture
def agents_root() -> Path:
    return Path("agents")


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_stage_tool_policy_allows_empty_tools_only_for_pure_prompt_workers(
    agents_root: Path,
    profile: str,
) -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    for worker_id in all_worker_ids():
        policy_result = load_stage_policy(agents_root, worker_id, profile)
        if not _profile_is_approved(worker_id, profile):
            assert policy_result.ok is False
            continue
        assert policy_result.ok is True and policy_result.policy is not None
        tools = resolve_tools(policy_result.policy, registry)
        if policy_result.policy.openviking_access == "none" and policy_result.policy.stage != Stage.FRONTLINE:
            assert tools == (), f"{worker_id}/{profile} pure prompt worker 不应暴露工具"
        else:
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
    assert tools == ("claw_get_market_pack",)
    for unexpected_tool in ("market_market_data_pack", "us_market_data_pack", "get_stock_data", "get_indicators"):
        assert unexpected_tool not in tools
    assert "market.stock_price" not in tools
    assert "market.techlab_analyze" not in tools
    assert "openviking_write_material" not in tools


def test_crypto_market_data_intent_resolves_to_single_compact_market_pack(agents_root: Path) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy_result = load_stage_policy(agents_root, "market_analyst", "CRYPTO")
    assert policy_result.ok is True and policy_result.policy is not None
    assert policy_result.policy.tool_intents == ("crypto_market_data",)
    assert resolve_tools(policy_result.policy, registry) == ("claw_get_market_pack",)


def test_crypto_formal_workers_are_approved(agents_root: Path) -> None:
    for worker_id in FORMAL_CRYPTO_WORKERS:
        policy_result = load_stage_policy(agents_root, worker_id, "CRYPTO")
        assert policy_result.ok is True, worker_id
        assert policy_result.policy is not None


def test_crypto_frontline_tool_policy_matches_current_real_tool_boundary(agents_root: Path) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    expected = {
        "market_analyst": (("crypto_market_data",), ("claw_get_market_pack",)),
        "fundamental_analyst": (("crypto_fundamentals_data",), ("claw_get_fundamental_pack",)),
        "news_analyst": (("crypto_news_data",), ("claw_get_news_pack",)),
        "social_analyst": (("crypto_social_sentiment",), ("claw_get_social_pack",)),
    }
    for worker_id, (expected_intents, expected_tools) in expected.items():
        policy_result = load_stage_policy(agents_root, worker_id, "CRYPTO")
        assert policy_result.ok is True and policy_result.policy is not None
        policy = policy_result.policy
        assert policy.tool_intents == expected_intents
        assert resolve_tools(policy, registry) == expected_tools


def test_hk_frontline_tool_policy_matches_current_real_tool_boundary(agents_root: Path) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    expected = {
        "market_analyst": (("hk_market_data",), ("claw_get_market_pack",)),
        "fundamental_analyst": (("hk_fundamentals_data",), ("claw_get_fundamental_pack",)),
        "news_analyst": (("hk_news_data",), ("claw_get_news_pack",)),
        "social_analyst": (("hk_social_sentiment",), ("claw_get_social_pack",)),
    }
    for worker_id, (expected_intents, expected_tools) in expected.items():
        policy_result = load_stage_policy(agents_root, worker_id, "HK")
        assert policy_result.ok is True and policy_result.policy is not None
        policy = policy_result.policy
        assert policy.tool_intents == expected_intents
        assert resolve_tools(policy, registry) == expected_tools


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_openviking_read_write_are_stage_scoped(agents_root: Path, profile: str) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    for worker_id in all_worker_ids():
        policy_result = load_stage_policy(agents_root, worker_id, profile)
        if not _profile_is_approved(worker_id, profile):
            assert policy_result.ok is False
            continue
        assert policy_result.ok is True and policy_result.policy is not None
        policy = policy_result.policy
        tools = resolve_tools(policy, registry)

        assert "openviking_read_with_capability" not in tools
        assert "openviking_write_material" not in tools


@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_news_analyst_must_include_company_and_macro_news(agents_root: Path, profile: str) -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy_result = load_stage_policy(agents_root, "news_analyst", profile)
    assert policy_result.ok is True and policy_result.policy is not None
    tools = resolve_tools(policy_result.policy, registry)
    assert tools == ("claw_get_news_pack",)
    for unexpected_tool in ("news_news_data_pack", "us_news_data_pack", "get_news", "get_global_news"):
        assert unexpected_tool not in tools


def test_unknown_openviking_access_fails() -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("cn_a_market_data",),
        openviking_access="unknown",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    with pytest.raises(ConfigError):
        resolve_tools(policy, registry)


def test_empty_intents_with_read_access_resolve_to_openviking_read_tool() -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy = StagePolicy(
        worker_id="market_analyst",
        stage=worker_by_id("market_analyst").stage,
        profile="US",
        tool_intents=(),
        openviking_access="read",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    assert resolve_tools(policy, registry) == ("openviking_read_with_capability",)


def test_empty_intents_are_allowed_for_pure_prompt_workers() -> None:
    registry = load_tool_registry().registry
    assert registry is not None

    policy = StagePolicy(
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        profile="CN_A",
        tool_intents=(),
        openviking_access="none",
        source_path=Path("agents/research_manager/STAGES.yaml"),
    )

    assert resolve_tools(policy, registry) == ()


def test_missing_openviking_tool_mapping_fails() -> None:
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


def test_social_visible_tools_validator_passes_for_exact_approved_set() -> None:
    result = SOCIAL_POLICY_MODULE.validate_social_visible_tools(
        ["claw_get_social_pack"]
    )
    assert result.ok is True
    assert result.code is None


def test_social_visible_tools_validator_fails_for_unapproved_extra_tool() -> None:
    result = SOCIAL_POLICY_MODULE.validate_social_visible_tools(
        [
            "claw_get_social_pack",
            "openviking_write_material",
            "stock_hot_keyword_em",
        ]
    )
    assert result.ok is False
    assert result.code == SOCIAL_POLICY_MODULE.SOCIAL_VISIBLE_TOOL_SET_INVALID


def _profile_is_approved(worker_id: str, profile: str) -> bool:
    parsed = yaml.safe_load((Path("agents") / worker_id / "STAGES.yaml").read_text(encoding="utf-8"))
    profile_config = parsed.get("profiles", {}).get(profile)
    return isinstance(profile_config, dict) and profile_config.get("approved") is True


def _load_social_policy_module():
    module_name = "cn_a_social_policy_contract"
    spec = importlib.util.spec_from_file_location(module_name, SOCIAL_POLICY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 social policy 模块: {SOCIAL_POLICY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


SOCIAL_POLICY_MODULE = _load_social_policy_module()
