from __future__ import annotations

from dataclasses import replace

from claw_trade.data_gateway.models import GatewaySettings


def with_provider_catalog(settings: GatewaySettings, provider_catalog: object) -> GatewaySettings:
    return replace(settings, provider_catalog=provider_catalog)


def validate_gateway_settings(settings: GatewaySettings) -> None:
    # 这里只做最小硬边界校验，详细 provider 准入在 admission 流程里完成。
    if not settings.openbb_runtime_url.strip():
        raise ValueError("openbb_runtime_url must not be empty")
    if not settings.mongo_uri.strip():
        raise ValueError("mongo_uri must not be empty")
    if not settings.provider_config_version.strip():
        raise ValueError("provider_config_version must not be empty")
