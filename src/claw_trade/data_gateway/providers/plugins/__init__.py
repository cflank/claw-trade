"""Provider plugin namespaces grouped by market profile."""

from __future__ import annotations

from claw_trade.data_gateway.providers.plugins.cn_a import (
    build_cn_a_provider_plugin,
    build_cn_a_provider_plugins,
)
from claw_trade.data_gateway.providers.plugins.crypto import (
    build_crypto_provider_plugin,
    build_crypto_provider_plugins,
)
from claw_trade.data_gateway.providers.plugins.hk import (
    build_hk_provider_plugin,
    build_hk_provider_plugins,
)
from claw_trade.data_gateway.providers.plugins.official_api import build_official_api_provider_plugins
from claw_trade.data_gateway.providers.plugins.us import (
    build_us_provider_plugin,
    build_us_provider_plugins,
)


def iter_minimal_market_plugins() -> tuple[object, ...]:
    return (
        *build_cn_a_provider_plugins(),
        *build_us_provider_plugins(),
        *build_hk_provider_plugins(),
        *build_crypto_provider_plugins(),
        *build_official_api_provider_plugins(),
    )


__all__ = [
    "build_cn_a_provider_plugin",
    "build_cn_a_provider_plugins",
    "build_us_provider_plugin",
    "build_us_provider_plugins",
    "build_hk_provider_plugin",
    "build_hk_provider_plugins",
    "build_crypto_provider_plugin",
    "build_crypto_provider_plugins",
    "build_official_api_provider_plugins",
    "iter_minimal_market_plugins",
]
