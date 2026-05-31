from __future__ import annotations

from claw_trade.data_gateway.models import ProviderCapability
from claw_trade.data_gateway.providers.market_adapters import (
    build_cn_a_selection_batch_adapters,
    build_default_market_adapters,
)


def market_capabilities() -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for adapter in build_default_market_adapters(provider_config_version="default-catalog"):
        capabilities.extend(adapter.capabilities())
    return tuple(capabilities)


def cn_a_selection_batch_capabilities() -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for adapter in build_cn_a_selection_batch_adapters(provider_config_version="default-catalog"):
        capabilities.extend(adapter.capabilities())
    return tuple(capabilities)
