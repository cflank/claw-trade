from __future__ import annotations

from dataclasses import dataclass

from claw_trade.config.profiles import ConfigError
from claw_trade.config.stage_policy import StagePolicy
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed


@dataclass(frozen=True)
class ToolRegistry:
    intent_to_tools: dict[str, tuple[str, ...]]

    def resolve_intent(self, intent: str) -> tuple[str, ...]:
        tools = self.intent_to_tools.get(intent)
        if tools is None:
            raise ConfigError(f"unknown tool intent: {intent}")
        return tools


@dataclass(frozen=True)
class ToolRegistryResult:
    ok: bool
    registry: ToolRegistry | None
    reason: str | None


def load_tool_registry() -> ToolRegistryResult:
    data_tool = ("claw_request_data",)
    registry = ToolRegistry(
        intent_to_tools={
            # 市场 profile 必须显式选择对应工具；不能让 US/CN_A 共用一个含糊 intent。
            # no-sidecar 路径：禁止使用 openvikingArtifact__* 触发 1944 MCP sidecar。
            "cn_a_market_data": data_tool,
            "us_market_data": data_tool,
            "hk_market_data": data_tool,
            "crypto_market_data": data_tool,
            "cn_a_fundamentals_data": data_tool,
            "us_fundamentals_data": data_tool,
            "hk_fundamentals_data": data_tool,
            "crypto_fundamentals_data": data_tool,
            "cn_a_news_data": data_tool,
            "us_news_data": data_tool,
            "hk_news_data": data_tool,
            "crypto_news_data": data_tool,
            "cn_a_social_sentiment": data_tool,
            "us_social_sentiment": data_tool,
            "hk_social_sentiment": data_tool,
            "crypto_social_sentiment": data_tool,
            "cn_a_policy_data": data_tool,
            "cn_a_hot_money_data": data_tool,
            "cn_a_lockup_data": data_tool,
            "selection_candidate_cache": ("claw_get_selection_candidate_cache",),
            # 这里是 intent 到 provider-visible 工具名的边界：stage policy 保留 intent，
            # 但最终发给模型可见的工具名必须对齐 OpenViking 设计合同。
            "openviking_read": ("openviking_read_with_capability",),
            "openviking_write": ("openviking_write_material",),
        }
    )
    return ToolRegistryResult(ok=True, registry=registry, reason=None)


def resolve_tools(policy: StagePolicy, registry: ToolRegistry) -> tuple[str, ...]:
    stage_value = getattr(policy.stage, "value", policy.stage)
    if (
        not policy.tool_intents
        and stage_value == "frontline"
        and policy.openviking_access != "read"
    ):
        raise ConfigError(
            f"frontline policy must include profile-specific data tools: "
            f"{policy.worker_id}/{policy.profile}"
        )

    tools: list[str] = []
    for intent in policy.tool_intents:
        for tool_name in registry.resolve_intent(intent):
            if tool_name not in tools:
                tools.append(tool_name)

    for openviking_tool in _required_openviking_tools(policy.openviking_access):
        if registry.intent_to_tools.get(openviking_tool) is None:
            raise ConfigError(f"missing openviking tool mapping: {openviking_tool}")
        for mapped in registry.resolve_intent(openviking_tool):
            if mapped not in tools:
                tools.append(mapped)

    if policy.worker_id == "news_analyst" and policy.profile == "CRYPTO":
        if "claw_request_data" not in tools:
            raise ConfigError("news_analyst CRYPTO must include claw_request_data")

    if policy.worker_id == "news_analyst" and policy.profile != "CRYPTO":
        require_global_news = require_global_news_capability_for_news(registry)
        if not require_global_news.ok:
            raise ConfigError(require_global_news.reason or "news capability missing")
        if "claw_request_data" not in tools:
            raise ConfigError("news_analyst must include profile-specific data need tool")

    if policy.worker_id == "social_analyst" and policy.profile == "CRYPTO":
        if "claw_request_data" not in tools:
            raise ConfigError("social_analyst CRYPTO must include claw_request_data")

    if not tools and policy.openviking_access != "none":
        raise ConfigError(
            f"resolved tool set is empty but openviking_access={policy.openviking_access}: "
            f"{policy.worker_id}/{policy.profile}"
        )
    return tuple(tools)


def require_global_news_capability_for_news(registry: ToolRegistry) -> GuardResult:
    has_news_data_tool = bool(
        {"cn_a_news_data", "us_news_data", "hk_news_data"}.intersection(registry.intent_to_tools)
    )
    if has_news_data_tool:
        return guard_passed("news_capability")
    return guard_failed(
        category="config_blocked",
        reason="news tool capability missing: profile-specific news tools",
        paths=(),
        early_stop=True,
    )


def _required_openviking_tools(access: str) -> tuple[str, ...]:
    if access == "none":
        return ()
    if access == "read":
        return ("openviking_read",)
    if access == "write":
        return ("openviking_write",)
    if access == "read_write":
        return ("openviking_read", "openviking_write")
    raise ConfigError(f"unknown openviking_access: {access}")
