from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from claw_trade.config.profiles import ConfigError
from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools

SOCIAL_TOOL_POLICY_WORKER_MISMATCH = "SOCIAL_TOOL_POLICY_WORKER_MISMATCH"
SOCIAL_TOOL_POLICY_PROFILE_MISMATCH = "SOCIAL_TOOL_POLICY_PROFILE_MISMATCH"
SOCIAL_TOOL_POLICY_LEAK = "SOCIAL_TOOL_POLICY_LEAK"
SOCIAL_TOOL_POLICY_MISSING_PACK = "SOCIAL_TOOL_POLICY_MISSING_PACK"
SOCIAL_VISIBLE_TOOL_SET_INVALID = "SOCIAL_VISIBLE_TOOL_SET_INVALID"

_REQUIRED_WORKER_ID = "social_analyst"
_REQUIRED_MARKET_PROFILE = "CN_A"
_EXPECTED_VISIBLE_TOOLS = (
    "social_social_sentiment_pack",
)
_DISALLOWED_PROVIDER_TOOL_NAMES = frozenset(
    {
        "stock_hot_rank_latest_em",
        "stock_hot_keyword_em",
        "stock_hot_rank_relate_em",
        "stock_hot_rank_em",
        "stock_hot_follow_xq",
        "stock_hot_tweet_xq",
        "stock_hot_deal_xq",
    }
)
_REPO_ROOT = Path(__file__).resolve().parents[5]


@dataclass(frozen=True)
class VisibleToolPolicy:
    worker_id: str
    market_profile: str
    tool_names: tuple[str, ...]
    openviking_access: Literal["none"]


@dataclass(frozen=True)
class SocialVisibleToolsValidationResult:
    ok: bool
    code: str | None
    reason: str | None


class SocialToolPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def validate_social_visible_tools(visible_tools: tuple[str, ...] | list[str]) -> SocialVisibleToolsValidationResult:
    normalized: list[str] = []
    for tool_name in visible_tools:
        if not isinstance(tool_name, str) or not tool_name.strip():
            return SocialVisibleToolsValidationResult(
                ok=False,
                code=SOCIAL_VISIBLE_TOOL_SET_INVALID,
                reason="visible tools 包含空工具名或非字符串工具名",
            )
        normalized.append(tool_name.strip())

    expected = set(_EXPECTED_VISIBLE_TOOLS)
    actual = set(normalized)
    if len(normalized) != len(expected) or actual != expected:
        return SocialVisibleToolsValidationResult(
            ok=False,
            code=SOCIAL_VISIBLE_TOOL_SET_INVALID,
            reason=f"visible tools 不匹配，expected={sorted(expected)} actual={sorted(actual)}",
        )

    return SocialVisibleToolsValidationResult(ok=True, code=None, reason=None)


def resolve_social_visible_tools(
    worker_id: str,
    market_profile: str,
    *,
    visible_tools_override: tuple[str, ...] | None = None,
) -> VisibleToolPolicy:
    if worker_id != _REQUIRED_WORKER_ID:
        raise SocialToolPolicyError(
            SOCIAL_TOOL_POLICY_WORKER_MISMATCH,
            f"worker_id 必须为 {_REQUIRED_WORKER_ID}: {worker_id}",
        )
    if market_profile != _REQUIRED_MARKET_PROFILE:
        raise SocialToolPolicyError(
            SOCIAL_TOOL_POLICY_PROFILE_MISMATCH,
            f"market_profile 必须为 {_REQUIRED_MARKET_PROFILE}: {market_profile}",
        )

    tools = (
        tuple(visible_tools_override)
        if visible_tools_override is not None
        else _resolve_stage_visible_tools(worker_id, market_profile)
    )
    expected = set(_EXPECTED_VISIBLE_TOOLS)
    actual = set(tools)
    if actual != expected:
        if actual & _DISALLOWED_PROVIDER_TOOL_NAMES:
            raise SocialToolPolicyError(
                SOCIAL_TOOL_POLICY_LEAK,
                f"检测到 provider 工具泄露: {sorted(actual & _DISALLOWED_PROVIDER_TOOL_NAMES)}",
            )
        raise SocialToolPolicyError(
            SOCIAL_TOOL_POLICY_MISSING_PACK,
            f"visible tools 不匹配，expected={sorted(expected)} actual={sorted(actual)}",
        )

    return VisibleToolPolicy(
        worker_id=worker_id,
        market_profile=market_profile,
        tool_names=tools,
        openviking_access="none",
    )


def _resolve_stage_visible_tools(worker_id: str, market_profile: str) -> tuple[str, ...]:
    agents_root = _REPO_ROOT / "agents"
    policy_result = load_stage_policy(agents_root, worker_id, market_profile)
    if not policy_result.ok or policy_result.policy is None:
        raise SocialToolPolicyError(
            SOCIAL_TOOL_POLICY_MISSING_PACK,
            f"stage policy 读取失败: {policy_result.reason or 'unknown'}",
        )

    registry_result = load_tool_registry()
    if not registry_result.ok or registry_result.registry is None:
        raise SocialToolPolicyError(
            SOCIAL_TOOL_POLICY_MISSING_PACK,
            f"tool registry 读取失败: {registry_result.reason or 'unknown'}",
        )

    try:
        return resolve_tools(policy_result.policy, registry_result.registry)
    except ConfigError as exc:
        raise SocialToolPolicyError(
            SOCIAL_TOOL_POLICY_MISSING_PACK,
            f"tool 解析失败: {exc}",
        ) from exc
