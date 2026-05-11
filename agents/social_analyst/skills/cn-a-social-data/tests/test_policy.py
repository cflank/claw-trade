from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from policy import (  # noqa: E402
    SOCIAL_TOOL_POLICY_LEAK,
    SOCIAL_TOOL_POLICY_MISSING_PACK,
    SOCIAL_TOOL_POLICY_PROFILE_MISMATCH,
    SOCIAL_TOOL_POLICY_WORKER_MISMATCH,
    SocialToolPolicyError,
    resolve_social_visible_tools,
)


def test_resolve_social_visible_tools_returns_pack_policy_for_real_cn_a_stage_config() -> None:
    policy = resolve_social_visible_tools("social_analyst", "CN_A")

    assert policy.worker_id == "social_analyst"
    assert policy.market_profile == "CN_A"
    assert set(policy.tool_names) == {"social_social_sentiment_pack"}
    assert policy.openviking_access == "none"


def test_resolve_social_visible_tools_raises_worker_mismatch_for_non_social_worker() -> None:
    with pytest.raises(SocialToolPolicyError) as exc_info:
        resolve_social_visible_tools("market_analyst", "CN_A")

    assert exc_info.value.code == SOCIAL_TOOL_POLICY_WORKER_MISMATCH


def test_resolve_social_visible_tools_raises_profile_mismatch_for_non_cn_a_profile() -> None:
    with pytest.raises(SocialToolPolicyError) as exc_info:
        resolve_social_visible_tools("social_analyst", "US")

    assert exc_info.value.code == SOCIAL_TOOL_POLICY_PROFILE_MISMATCH


def test_resolve_social_visible_tools_raises_leak_when_disallowed_provider_tool_visible() -> None:
    with pytest.raises(SocialToolPolicyError) as exc_info:
        resolve_social_visible_tools(
            "social_analyst",
            "CN_A",
            visible_tools_override=(
                "social_social_sentiment_pack",
                "stock_hot_rank_em",
            ),
        )

    assert exc_info.value.code == SOCIAL_TOOL_POLICY_LEAK


def test_resolve_social_visible_tools_raises_missing_pack_when_pack_tool_missing() -> None:
    with pytest.raises(SocialToolPolicyError) as exc_info:
        resolve_social_visible_tools(
            "social_analyst",
            "CN_A",
            visible_tools_override=(),
        )

    assert exc_info.value.code == SOCIAL_TOOL_POLICY_MISSING_PACK
