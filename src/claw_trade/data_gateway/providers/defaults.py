from __future__ import annotations

import hashlib
import importlib
import json
import os
from typing import Callable, Iterable, Mapping

from claw_trade.data_gateway.models import ProviderCapability
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.fundamental import build_default_fundamental_adapters
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters
from claw_trade.data_gateway.providers.news import build_default_news_adapters
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.social import build_default_social_adapters

CapabilityLoader = Callable[[], Iterable[ProviderCapability]]

DEFAULT_CAPABILITY_LOADERS: tuple[str, ...] = (
    "claw_trade.data_gateway.providers.market:market_capabilities",
    "claw_trade.data_gateway.providers.fundamental:fundamental_capabilities",
    "claw_trade.data_gateway.providers.news:news_capabilities",
    "claw_trade.data_gateway.providers.social:social_capabilities",
)


def load_default_system_capabilities(loaders: tuple[str, ...] = DEFAULT_CAPABILITY_LOADERS) -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for spec in loaders:
        loader = _load_capability_loader(spec)
        if loader is None:
            continue
        capabilities.extend(tuple(loader()))
    return tuple(capabilities)


def build_default_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(capabilities=load_default_system_capabilities())


def build_default_provider_adapters(
    *,
    provider_config_version: str,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderAdapter, ...]:
    adapters: list[ProviderAdapter] = []
    adapters.extend(build_default_market_adapters(provider_config_version=provider_config_version, env=env))
    adapters.extend(build_default_fundamental_adapters(provider_config_version=provider_config_version, env=env))
    adapters.extend(build_default_news_adapters(provider_config_version=provider_config_version, env=env))
    adapters.extend(build_default_social_adapters(provider_config_version=provider_config_version, env=env))
    return tuple(adapters)


def default_provider_config_version(capabilities: Iterable[ProviderCapability]) -> str:
    override = os.environ.get("DATA_GATEWAY_PROVIDER_CONFIG_VERSION", "").strip()
    if override:
        return override
    payload = [
        {
            "adapter_id": item.adapter_id,
            "provider": item.provider,
            "provider_kind": item.provider_kind.value,
            "market": item.market.value,
            "domain": item.domain.value,
            "endpoint": item.endpoint,
            "source_role": item.source_role.value,
            "expected_schema_id": item.expected_schema_id,
            "license_policy_id": item.license_policy_id,
            "credential_requirements": list(item.credential_requirements),
            "rate_limit_policy_id": item.rate_limit_policy_id,
            "cache_ttl_seconds": item.cache_ttl_seconds,
            "required": item.required,
            "attempt_required": item.attempt_required,
            "coverage_group": item.coverage_group,
            "coverage_quorum": item.coverage_quorum,
            "priority": item.priority,
            "priority_source": item.priority_source.value,
        }
        for item in sorted(capabilities, key=lambda cap: (cap.market.value, cap.domain.value, cap.adapter_id, cap.endpoint))
    ]
    rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(rendered).hexdigest()


def _load_capability_loader(spec: str) -> CapabilityLoader | None:
    module_name, separator, attr_name = spec.partition(":")
    if not module_name or separator != ":" or not attr_name:
        raise ValueError(f"invalid capability loader spec: {spec}")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    loader = getattr(module, attr_name, None)
    if loader is None:
        return None
    if not callable(loader):
        raise TypeError(f"capability loader is not callable: {spec}")
    return loader
