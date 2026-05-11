from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from boundary import (
    APPROVED_VISIBLE_TOOLS,
    FUNDAMENTAL_MARKET_PROFILE,
    FUNDAMENTAL_WORKER_ID,
    is_forbidden_provider_tool_name,
)
from claw_trade.config.profiles import ConfigError
from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools

TOOL_POLICY_WORKER_MISMATCH = "TOOL_POLICY_WORKER_MISMATCH"
TOOL_POLICY_PROFILE_MISMATCH = "TOOL_POLICY_PROFILE_MISMATCH"
TOOL_POLICY_VISIBLE_SET_INVALID = "TOOL_POLICY_VISIBLE_SET_INVALID"
TOOL_POLICY_PROVIDER_TOOL_LEAK = "TOOL_POLICY_PROVIDER_TOOL_LEAK"

_REPO_ROOT = Path(__file__).resolve().parents[5]


@dataclass(frozen=True)
class VisibleToolPolicy:
    worker_id: str
    market_profile: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class VisibleToolSnapshot:
    worker_id: str
    market_profile: str
    tools: tuple[str, ...]


class FundamentalToolPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def resolve_fundamental_visible_tools(
    worker_id: str,
    market_profile: str,
    *,
    visible_tools_override: tuple[str, ...] | None = None,
) -> VisibleToolPolicy:
    if worker_id != FUNDAMENTAL_WORKER_ID:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_WORKER_MISMATCH,
            f"worker_id 必须为 {FUNDAMENTAL_WORKER_ID}: {worker_id}",
        )
    if market_profile != FUNDAMENTAL_MARKET_PROFILE:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_PROFILE_MISMATCH,
            f"market_profile 必须为 {FUNDAMENTAL_MARKET_PROFILE}: {market_profile}",
        )

    resolved_tools = (
        visible_tools_override
        if visible_tools_override is not None
        else _resolve_tools_from_stage_policy(worker_id, market_profile)
    )
    _assert_visible_tools_exact(resolved_tools)
    return VisibleToolPolicy(
        worker_id=worker_id,
        market_profile=market_profile,
        tool_names=tuple(resolved_tools),
    )


def is_visible_tool_snapshot_invalid_v1(snapshot: VisibleToolSnapshot | Mapping[str, object]) -> bool:
    if isinstance(snapshot, Mapping):
        worker_id = str(snapshot.get("worker_id", "")).strip()
        market_profile = str(snapshot.get("market_profile", "")).strip()
        tools_obj = snapshot.get("tools")
        if not isinstance(tools_obj, (list, tuple)):
            return True
        tools = tuple(str(item).strip() for item in tools_obj)
    else:
        worker_id = snapshot.worker_id.strip()
        market_profile = snapshot.market_profile.strip()
        tools = tuple(tool.strip() for tool in snapshot.tools)

    if worker_id != FUNDAMENTAL_WORKER_ID:
        return True
    if market_profile != FUNDAMENTAL_MARKET_PROFILE:
        return True
    return set(tools) != APPROVED_VISIBLE_TOOLS


def _resolve_tools_from_stage_policy(worker_id: str, market_profile: str) -> tuple[str, ...]:
    agents_root = _REPO_ROOT / "agents"
    policy_result = load_stage_policy(agents_root, worker_id, market_profile)
    if not policy_result.ok or policy_result.policy is None:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_VISIBLE_SET_INVALID,
            f"stage policy 读取失败: {policy_result.reason or 'unknown'}",
        )
    registry_result = load_tool_registry()
    if not registry_result.ok or registry_result.registry is None:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_VISIBLE_SET_INVALID,
            f"tool registry 读取失败: {registry_result.reason or 'unknown'}",
        )
    try:
        return resolve_tools(policy_result.policy, registry_result.registry)
    except ConfigError as exc:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_VISIBLE_SET_INVALID,
            f"stage tool 解析失败: {exc}",
        ) from exc


def _assert_visible_tools_exact(tools: tuple[str, ...]) -> None:
    provider_leaks = [tool for tool in tools if is_forbidden_provider_tool_name(tool)]
    if provider_leaks:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_PROVIDER_TOOL_LEAK,
            f"检测到 provider 工具泄露: {sorted(provider_leaks)}",
        )
    if set(tools) != APPROVED_VISIBLE_TOOLS:
        raise FundamentalToolPolicyError(
            TOOL_POLICY_VISIBLE_SET_INVALID,
            f"visible tools 不匹配，expected={sorted(APPROVED_VISIBLE_TOOLS)} actual={sorted(set(tools))}",
        )
