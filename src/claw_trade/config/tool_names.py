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
                "market.stock_price",
                "market.techlab_analyze",
            ),
            "fundamentals_data": ("fundamentals_data",),
            "company_news": ("company_news",),
            "macro_news": ("macro_news",),
            "social_sentiment": ("social_sentiment",),
            # 这里是 intent 到 provider-visible 工具名的边界：stage policy 保留 intent，
            # 但最终发给模型可见的工具名必须对齐 OpenViking 设计合同。
            "openviking_read": ("openviking.read_with_capability",),
            "openviking_write": ("openviking.write_material",),
        }
    )
    return ToolRegistryResult(ok=True, registry=registry, reason=None)


def resolve_tools(policy: StagePolicy, registry: ToolRegistry) -> tuple[str, ...]:
    if not policy.tool_intents:
        raise ConfigError(f"stage tool intents cannot be empty: {policy.worker_id}/{policy.profile}")

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
        if "company_news" not in tools or "macro_news" not in tools:
            raise ConfigError("news_analyst must include both company_news and macro_news")

    # 这里是 OpenClaw 调用前硬边界：工具集合为空不能执行，避免出现策略空跑或隐式 fallback。
    if not tools:
        raise ConfigError(f"resolved tool set is empty: {policy.worker_id}/{policy.profile}")
    return tuple(tools)


def require_global_news_capability_for_news(registry: ToolRegistry) -> GuardResult:
    has_company = "company_news" in registry.intent_to_tools
    has_macro = "macro_news" in registry.intent_to_tools
    if has_company and has_macro:
        return guard_passed("news_capability")
    missing = []
    if not has_company:
        missing.append("company_news")
    if not has_macro:
        missing.append("macro_news")
    return guard_failed(
        category="config_blocked",
        reason=f"news tool capability missing: {', '.join(missing)}",
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
