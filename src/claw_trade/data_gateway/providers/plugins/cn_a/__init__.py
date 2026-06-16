from __future__ import annotations

from claw_trade.data_gateway.providers.plugins.cn_a.provider_matrix import (
    AkShareSocialNewsPlugin,
    AStockSignalSocialPlugin,
    BaostockCNProviderPlugin,
    CNADefaultProviderPlugin,
    CNInfoEventsPlugin,
    EastMoneyCNEventsPlugin,
    EastMoneyCNMarketDataPlugin,
    GoogleNewsDiscoveryPlugin,
    MootdxCNProviderPlugin,
    TushareFundamentalPlugin,
    TushareRealtimeSDKPlugin,
    build_cn_a_provider_plugin,
    build_cn_a_provider_plugins,
)

__all__ = [
    "AStockSignalSocialPlugin",
    "AkShareSocialNewsPlugin",
    "BaostockCNProviderPlugin",
    "CNADefaultProviderPlugin",
    "CNInfoEventsPlugin",
    "EastMoneyCNEventsPlugin",
    "EastMoneyCNMarketDataPlugin",
    "GoogleNewsDiscoveryPlugin",
    "MootdxCNProviderPlugin",
    "TushareFundamentalPlugin",
    "TushareRealtimeSDKPlugin",
    "build_cn_a_provider_plugin",
    "build_cn_a_provider_plugins",
]
