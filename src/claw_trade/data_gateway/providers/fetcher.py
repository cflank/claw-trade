from __future__ import annotations

from claw_trade.data_gateway.models import CredentialStatus, ProviderCallSpec
from claw_trade.data_gateway.providers.base import ProviderAdapter


class ProviderCallSpecBindingError(RuntimeError):
    pass


def ensure_call_spec_bound_to_adapter(*, adapter: ProviderAdapter, spec: ProviderCallSpec) -> None:
    if spec.adapter_id != adapter.adapter_id:
        raise ProviderCallSpecBindingError(
            f"provider_call_spec_adapter_mismatch: spec.adapter_id={spec.adapter_id}, adapter.adapter_id={adapter.adapter_id}"
        )

    capabilities = tuple(adapter.capabilities())
    if not capabilities:
        raise ProviderCallSpecBindingError(
            "provider_call_spec_capability_missing: "
            f"adapter_id={spec.adapter_id}, provider={spec.provider}, endpoint={spec.endpoint}, "
            f"market={spec.market.value}, domain={spec.domain.value}"
        )

    capability_matches = tuple(
        capability
        for capability in capabilities
        if capability.adapter_id == spec.adapter_id
        and capability.provider == spec.provider
        and capability.endpoint == spec.endpoint
        and capability.market == spec.market
        and capability.domain == spec.domain
    )
    if capability_matches:
        return

    raise ProviderCallSpecBindingError(
        "provider_call_spec_capability_mismatch: "
        f"adapter_id={spec.adapter_id}, provider={spec.provider}, endpoint={spec.endpoint}, "
        f"market={spec.market.value}, domain={spec.domain.value}"
    )


def adapter_credential_status(*, adapter: ProviderAdapter) -> CredentialStatus:
    return adapter.validate_credentials()
