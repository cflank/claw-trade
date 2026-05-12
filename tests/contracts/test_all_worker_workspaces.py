from pathlib import Path

import pytest

from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools
from claw_trade.config.workspace import validate_worker_workspace_for_control
from claw_trade.workflow.workers import worker_by_id


REQUIRED_WORKERS: tuple[str, ...] = (
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
)

REQUIRED_WORKSPACE_FILES: tuple[str, ...] = (
    "IDENTITY.md",
    "USER.md",
    "prompts/US.md",
    "prompts/CN_A.md",
    "STAGES.yaml",
    "SKILLS.md",
    "skills/manifest.yaml",
    "prompt-review.yaml",
)


@pytest.fixture
def agents_root() -> Path:
    return Path("agents")


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
def test_all_worker_workspaces_exist_and_validate_for_us_cn_a(
    agents_root: Path,
    worker_id: str,
) -> None:
    worker_dir = agents_root / worker_id
    for relative in REQUIRED_WORKSPACE_FILES:
        assert (worker_dir / relative).is_file(), f"{worker_id} 缺少 {relative}"

    workspace_result = validate_worker_workspace_for_control(agents_root, worker_id)
    assert workspace_result.ok is True, workspace_result.reason


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_worker_prompts_are_non_empty(agents_root: Path, worker_id: str, profile: str) -> None:
    prompt_path = agents_root / worker_id / "prompts" / f"{profile}.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    assert prompt.strip(), f"{worker_id}/{profile} prompt 为空"


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
@pytest.mark.parametrize("profile", ("US", "CN_A"))
def test_all_worker_stage_policy_is_loadable_and_resolvable(
    agents_root: Path,
    worker_id: str,
    profile: str,
) -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    policy_result = load_stage_policy(agents_root, worker_id, profile)
    assert policy_result.ok is True and policy_result.policy is not None
    policy = policy_result.policy
    assert policy.stage == worker_by_id(worker_id).stage
    assert policy.openviking_access.strip()

    tools = resolve_tools(policy, registry)
    if policy.stage == worker_by_id(worker_id).stage and policy.openviking_access == "none":
        if policy.stage.value == "frontline":
            assert tools
        else:
            assert tools == ()
    else:
        assert tools
    _assert_openviking_access_tools(policy.openviking_access, tools)


def _assert_openviking_access_tools(access: str, tools: tuple[str, ...]) -> None:
    if access == "write":
        assert "openviking_write_material" in tools
        assert "openviking_read_with_capability" not in tools
        return
    if access == "read_write":
        assert "openviking_read_with_capability" in tools
        assert "openviking_write_material" in tools
        return
    if access == "read":
        assert "openviking_read_with_capability" in tools
        assert "openviking_write_material" not in tools
        return
    if access == "none":
        assert "openviking_read_with_capability" not in tools
        assert "openviking_write_material" not in tools
        return
    raise AssertionError(f"未知 openviking_access: {access}")
