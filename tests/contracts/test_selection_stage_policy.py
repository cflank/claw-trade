from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SELECTION_WORKERS = {
    "selection_strategist": {
        "stage": "selection_review",
        "tools": ["selection_candidate_cache"],
    },
    "selection_skeptic": {
        "stage": "selection_review",
        "tools": ["selection_candidate_cache"],
    },
    "selection_manager": {
        "stage": "selection_decision",
        "tools": [],
    },
    "selection_portfolio_manager": {
        "stage": "selection_portfolio_decision",
        "tools": [],
    },
}


@pytest.mark.parametrize("worker_id", tuple(SELECTION_WORKERS))
def test_selection_worker_stage_profile_and_tool_matrix(worker_id: str) -> None:
    worker_dir = Path("agents") / worker_id
    stages_path = worker_dir / "STAGES.yaml"
    assert stages_path.is_file(), f"{worker_id} 缺少 STAGES.yaml"

    parsed = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert parsed.get("worker") == worker_id
    assert parsed.get("runtime") == "openclaw"
    assert parsed.get("completion") == "openclaw_agent_turn"
    assert parsed.get("stage") == SELECTION_WORKERS[worker_id]["stage"]

    profiles = parsed.get("profiles")
    assert isinstance(profiles, dict)
    assert set(profiles.keys()) == {"CN_A", "CRYPTO"}

    for profile in ("CN_A", "CRYPTO"):
        profile_config = profiles[profile]
        assert profile_config.get("approved") is True
        assert profile_config.get("prompt") == f"prompts/{profile}.md"
        assert profile_config.get("tools") == SELECTION_WORKERS[worker_id]["tools"]
        assert profile_config.get("openviking_access") == "none"

    tool_policy = parsed.get("tool_policy")
    assert isinstance(tool_policy, dict)
    assert tool_policy.get("owner") == "stage_profile"
    assert tool_policy.get("python_calls_tools") is False
