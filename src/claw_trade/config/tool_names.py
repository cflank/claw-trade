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
    registry = ToolRegistry(
        intent_to_tools={
            # market_data 必须映射到真实 provider-visible 工具名；不能把 intent 当成可调用工具名。
            # no-sidecar 路径：禁止使用 openvikingArtifact__* 触发 1944 MCP sidecar。
            "market_data": (
                "market_market_data_pack",
            ),
            # frontline 资料包入口：worker 只看少量职责清晰的 pack，不再拼多段 generic 工具结果。
            "fundamentals_data_pack": ("fundamental_fundamentals_data_pack",),
            "news_data_pack": ("news_news_data_pack",),
            "social_sentiment_pack": ("social_social_sentiment_pack",),
            # 这里是 intent 到 provider-visible 工具名的边界：stage policy 保留 intent，
            # 但最终发给模型可见的工具名必须对齐 OpenViking 设计合同。
            "openviking_read": ("openviking_read_with_capability",),
            "openviking_write": ("openviking_write_material",),
        }
    )
    return ToolRegistryResult(ok=True, registry=registry, reason=None)


def resolve_tools(policy: StagePolicy, registry: ToolRegistry) -> tuple[str, ...]:
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

    if policy.worker_id == "news_analyst":
        require_global_news = require_global_news_capability_for_news(registry)
        if not require_global_news.ok:
            raise ConfigError(require_global_news.reason or "news capability missing")
        if "news_news_data_pack" not in tools:
            raise ConfigError("news_analyst must include news_news_data_pack")

    if not tools and policy.openviking_access != "none":
        raise ConfigError(
            f"resolved tool set is empty but openviking_access={policy.openviking_access}: "
            f"{policy.worker_id}/{policy.profile}"
        )
    return tuple(tools)


def require_global_news_capability_for_news(registry: ToolRegistry) -> GuardResult:
    has_news_pack = "news_data_pack" in registry.intent_to_tools
    if has_news_pack:
        return guard_passed("news_capability")
    return guard_failed(
        category="config_blocked",
        reason="news tool capability missing: news_data_pack",
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
