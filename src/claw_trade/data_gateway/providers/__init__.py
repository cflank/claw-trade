"""Provider plugin layer boundary package."""

from __future__ import annotations

from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.data_gateway.providers.registry import ProviderRegistry


def build_minimal_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    for plugin in iter_minimal_market_plugins():
        registry.register(plugin)
    return registry


__all__ = ["ProviderRegistry", "build_minimal_provider_registry"]
