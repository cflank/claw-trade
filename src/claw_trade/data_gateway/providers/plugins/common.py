from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from claw_trade.data_gateway.models import FetchResult


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
    granularity: tuple[str, ...]
    fields: tuple[str, ...]
    freshness_supported: tuple[str, ...]
    http_visibility: str
    batch_policy: BatchPolicy
    priority_rank: int | None = None
    rate_limit_policy: object | None = None
    license_policy: LicensePolicy | None = None
    can_be_formal_fact_source: bool | None = None

    @property
    def supported_granularities(self) -> tuple[str, ...]:
        return self.granularity

    @property
    def coverage_fields(self) -> tuple[str, ...]:
        return self.fields


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    plugin_version: str
    endpoints: tuple[EndpointCapability, ...]
    credential_policy: CredentialPolicy
    license_policy: LicensePolicy
    default_rate_limit_policy: object
    default_priority_rank: int = 100

    @property
    def credentials(self) -> CredentialPolicy:
        return self.credential_policy


class MinimalProviderPlugin:
    def __init__(
        self,
        *,
        provider_id: str,
        plugin_version: str,
        market: str,
        endpoint_id: str,
        data_type: str,
        source_role: str,
        granularity: tuple[str, ...],
        fields: tuple[str, ...],
        credential_names: tuple[str, ...],
        credential_required: bool | None = None,
        credential_scope: str = "provider_token",
        batch_by: str = "symbol",
        http_visibility: str = "managed_http",
        priority_rank: int = 20,
    ) -> None:
        self.plugin_id = provider_id
        self.version = plugin_version
        self._capabilities = ProviderCapabilities(
            provider_id=provider_id,
            plugin_version=plugin_version,
            endpoints=(
                EndpointCapability(
                    endpoint_id=endpoint_id,
                    market=market,
                    data_type=data_type,
                    source_role=source_role,
                    granularity=granularity,
                    fields=fields,
                    freshness_supported=("trading_day",),
                    http_visibility=http_visibility,
                    priority_rank=priority_rank,
                    batch_policy=BatchPolicy(
                        supports_batch=True,
                        batch_by=batch_by,
                        max_symbols_per_call=50,
                        mergeable_fields=fields,
                    ),
                ),
            ),
            credential_policy=CredentialPolicy(
                credential_required=bool(credential_names) if credential_required is None else credential_required,
                credential_names=credential_names,
                credential_scope=credential_scope,
                missing_behavior="credential_missing",
            ),
            license_policy=LicensePolicy(
                raw_storage_mode="metadata_only",
                normalized_storage_allowed=True,
                redistribution_allowed=False,
                retention_days=30,
            ),
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=priority_rank,
        )
        self._endpoint_index = {
            (endpoint.endpoint_id, endpoint.market, endpoint.data_type): endpoint
            for endpoint in self._capabilities.endpoints
        }

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (
            SimpleNamespace(
                provider_id=getattr(batch, "provider_id", self.plugin_id),
                endpoint_id=getattr(batch, "endpoint_id", self._capabilities.endpoints[0].endpoint_id),
                market=getattr(batch, "market", self._capabilities.endpoints[0].market),
                data_type=getattr(batch, "data_type", self._capabilities.endpoints[0].data_type),
                params=dict(getattr(batch, "params_redacted", {})),
            ),
        )

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        endpoint = self._endpoint_index.get((task.endpoint_id, task.market, task.data_type))
        if endpoint is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("not_applicable"))

        missing = [
            name
            for name in self._capabilities.credential_policy.credential_names
            if self._capabilities.credential_policy.credential_required and not _credential_value(ctx, name)
        ]
        if missing:
            joined = ",".join(missing)
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{joined}"))

        if endpoint.http_visibility == "sdk_internal_unknown":
            return FetchResult.from_error(task, status="sdk_http_unknown", error=RuntimeError("sdk_http_unknown"))

        return FetchResult.from_error(task, status="error", error=RuntimeError("provider_error:no_auditable_http_observation"))


def _credential_value(ctx: Any, name: str) -> str | None:
    resolver = getattr(ctx, "credential_resolver", None)
    if resolver is not None:
        getter = getattr(resolver, "get_credential", None)
        if callable(getter):
            value = getter(name)
            return _non_empty(value)
        getter = getattr(resolver, "get", None)
        if callable(getter):
            value = getter(name)
            return _non_empty(value)

    credentials = getattr(ctx, "credentials", None)
    if isinstance(credentials, dict):
        return _non_empty(credentials.get(name))
    return None


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
