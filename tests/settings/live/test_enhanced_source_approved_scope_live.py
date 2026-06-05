from __future__ import annotations

from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.data_gateway.providers.registry import ProviderRegistry

_EXPECTED_MARKETS = {"CN_A", "HK", "US", "CRYPTO"}
_NON_FORMAL_ROLES = {"discovery", "event_expectation"}


def _scope_key(capability: object) -> tuple[str, str, str, str]:
    return (
        str(getattr(capability, "market")),
        str(getattr(capability, "provider_id")),
        str(getattr(capability, "endpoint_id")),
        str(getattr(capability, "data_type")),
    )


def test_live_enhanced_source_attempt_scope_is_only_from_registered_provider_capabilities() -> None:
    registry = ProviderRegistry()
    plugins = iter_minimal_market_plugins()
    for plugin in plugins:
        registry.register(plugin)

    provider_ids = tuple(str(getattr(plugin, "plugin_id")) for plugin in plugins)
    capabilities = registry.read_capabilities(provider_ids).list()
    scopes = {_scope_key(capability) for capability in capabilities}

    assert capabilities, "provider capability catalog is empty"
    assert {capability.market for capability in capabilities} == _EXPECTED_MARKETS
    assert len(scopes) == len(capabilities)
    for capability in capabilities:
        assert capability.supported_granularities
        assert capability.coverage_fields
        assert capability.http_visibility in {"managed_http", "sdk_internal_unknown", "no_http"}
        if capability.credential_required:
            assert capability.credential_names
        if capability.source_role in _NON_FORMAL_ROLES:
            assert capability.can_be_formal_fact_source is False
