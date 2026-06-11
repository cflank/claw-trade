from __future__ import annotations

from dataclasses import dataclass

import pytest
from claw_trade.data_gateway.providers.base import CapabilityError
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.data_gateway.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class BatchPolicy:
    supports_batch: bool
    batch_by: str
    max_symbols_per_call: int | None = None
    max_days_per_call: int | None = None
    mergeable_fields: tuple[str, ...] = ()
    pagination_policy: object = "none"
    split_policy: object = "strict"


@dataclass(frozen=True)
class CredentialPolicy:
    credential_required: bool
    credential_names: tuple[str, ...]
    credential_scope: str | None
    missing_behavior: str


@dataclass(frozen=True)
class LicensePolicy:
    raw_storage_mode: str
    normalized_storage_allowed: bool
    redistribution_allowed: bool
    retention_days: int | None


@dataclass(frozen=True)
class EndpointCapability:
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    supported_granularities: tuple[str, ...]
    coverage_fields: tuple[str, ...]
    freshness_supported: tuple[str, ...]
    http_visibility: str
    batch_policy: BatchPolicy
    priority_rank: int | None = None
    rate_limit_policy: object | None = None
    license_policy: LicensePolicy | None = None
    can_be_formal_fact_source: bool | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    plugin_version: str
    endpoints: tuple[EndpointCapability, ...]
    credentials: CredentialPolicy
    license_policy: LicensePolicy
    default_rate_limit_policy: object
    default_priority_rank: int = 100


class FakePlugin:
    def __init__(self, capabilities: ProviderCapabilities) -> None:
        self._capabilities = capabilities
        self.plugin_id = capabilities.provider_id
        self.version = capabilities.plugin_version

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: object) -> tuple[object, ...]:
        del batch
        return ()

    def fetch(self, task: object, ctx: object) -> object:
        del task
        del ctx
        raise NotImplementedError


def _plugin(*, provider_id: str = "official_feed", endpoint_id: str = "daily", market: str = "US", data_type: str = "daily_bar", priority_rank: int | None = None, credentials_required: bool = False, credential_names: tuple[str, ...] = ()) -> FakePlugin:
    caps = ProviderCapabilities(
        provider_id=provider_id,
        plugin_version="1.0.0",
        endpoints=(
            EndpointCapability(
                endpoint_id=endpoint_id,
                market=market,
                data_type=data_type,
                source_role="official",
                supported_granularities=("daily",),
                coverage_fields=("close", "volume"),
                freshness_supported=("trading_day",),
                http_visibility="managed_http",
                priority_rank=priority_rank,
                batch_policy=BatchPolicy(
                    supports_batch=True,
                    batch_by="symbol",
                    max_symbols_per_call=50,
                    mergeable_fields=("close", "volume"),
                ),
            ),
        ),
        credentials=CredentialPolicy(
            credential_required=credentials_required,
            credential_names=credential_names,
            credential_scope="user" if credentials_required else None,
            missing_behavior="credential_missing",
        ),
        license_policy=LicensePolicy(
            raw_storage_mode="metadata_only",
            normalized_storage_allowed=True,
            redistribution_allowed=False,
            retention_days=30,
        ),
        default_rate_limit_policy={"window_seconds": 60, "max_calls": 10},
    )
    return FakePlugin(caps)


def test_registry_register_and_index_capabilities() -> None:
    registry = ProviderRegistry()
    registry.register(_plugin(provider_id="official_feed", priority_rank=3))
    registry.register(_plugin(provider_id="backup_feed", priority_rank=7))

    listed = registry.list_capabilities("US", "daily_bar")
    assert [cap.provider_id for cap in listed] == ["official_feed", "backup_feed"]
    snapshot = registry.read_capabilities(["official_feed"])
    cap = snapshot.get("official_feed", "daily", market="US", data_type="daily_bar")
    assert cap.priority_rank == 3
    assert registry.get("official_feed").plugin_id == "official_feed"


def test_registry_rejects_duplicate_provider_and_endpoint_key() -> None:
    registry = ProviderRegistry()
    plugin = _plugin(provider_id="official_feed")
    registry.register(plugin)
    with pytest.raises(CapabilityError, match="duplicate_provider:official_feed"):
        registry.register(plugin)


def test_registry_rejects_invalid_credential_policy() -> None:
    registry = ProviderRegistry()
    plugin = _plugin(
        provider_id="needs_key",
        credentials_required=True,
        credential_names=(),
    )
    with pytest.raises(CapabilityError, match="credential_required=True 时必须声明 credential_names"):
        registry.register(plugin)


def test_registry_registers_minimal_market_plugins_for_all_markets() -> None:
    registry = ProviderRegistry()
    for plugin in iter_minimal_market_plugins():
        registry.register(plugin)

    listed = registry.read_capabilities(("cn_a_primary", "us_yahoo_finance", "hk_sina_public", "crypto_primary")).list()
    assert {cap.market for cap in listed} == {"CN_A", "US", "HK", "CRYPTO"}
    assert {cap.provider_id for cap in listed} == {"cn_a_primary", "us_yahoo_finance", "hk_sina_public", "crypto_primary"}
