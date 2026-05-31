from __future__ import annotations

from dataclasses import dataclass

from claw_trade.data_gateway.models import CredentialStatus, PackRequest, ProviderCallSpec, ProviderFetch
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.fetcher import adapter_credential_status, ensure_call_spec_bound_to_adapter


@dataclass(frozen=True)
class ProviderExecutionWrapper:
    adapter: ProviderAdapter

    def credential_status(self) -> CredentialStatus:
        return adapter_credential_status(adapter=self.adapter)

    def fetch(self, *, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        ensure_call_spec_bound_to_adapter(adapter=self.adapter, spec=spec)
        return self.adapter.fetch(spec, request)

    def normalize(self, *, spec: ProviderCallSpec, fetch: ProviderFetch):
        ensure_call_spec_bound_to_adapter(adapter=self.adapter, spec=spec)
        return self.adapter.normalize(spec, fetch)
