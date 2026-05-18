from __future__ import annotations

from typing import Protocol, runtime_checkable

from claw_trade.data_gateway.models import (
    CredentialStatus,
    NormalizedResult,
    PackRequest,
    ProviderCallSpec,
    ProviderCapability,
    ProviderFetch,
    ProviderKind,
)


@runtime_checkable
class ProviderAdapter(Protocol):
    adapter_id: str
    provider_id: str
    adapter_kind: str
    provider_kind: ProviderKind

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        """Return declared provider capabilities."""

    def validate_credentials(self) -> CredentialStatus:
        """Return credential status for this adapter."""

    def build_call_specs(self, request: PackRequest) -> tuple[ProviderCallSpec, ...]:
        """Build call specs for this request."""

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        """Execute provider request and return raw payload metadata."""

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        """Map payload into normalized schema."""
