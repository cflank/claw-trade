from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from claw_trade.data_gateway.models import FetchResult

from .managed_http import ManagedHttp, UrllibHttpClient


class CredentialMissingError(Exception):
    pass


class ProviderRateLimitedError(Exception):
    pass


class ProviderEmptyError(Exception):
    pass


@dataclass(frozen=True)
class FetchTask:
    batch_id: str
    provider_id: str
    endpoint_id: str
    market: str
    data_type: str
    granularity: str
    symbol_ids: tuple[str, ...]
    date_range_start: date | datetime | None
    date_range_end: date | datetime | None
    fields: tuple[str, ...]
    provider_config_version: str | None
    params: dict[str, Any]

    @classmethod
    def from_batch(cls, batch: Any) -> "FetchTask":
        return cls(
            batch_id=str(getattr(batch, "batch_id", "batch:unknown")),
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=_as_string(batch.market),
            data_type=batch.data_type,
            granularity=str(getattr(batch, "granularity", "")),
            symbol_ids=tuple(str(item) for item in tuple(getattr(batch, "symbol_ids", ()) or ())),
            date_range_start=getattr(batch, "date_range_start", None),
            date_range_end=getattr(batch, "date_range_end", None),
            fields=tuple(str(item) for item in tuple(getattr(batch, "fields_union", ()) or ())),
            provider_config_version=getattr(batch, "provider_config_version", None),
            params=dict(getattr(batch, "params", {}) or getattr(batch, "params_redacted", {}) or {}),
        )


@dataclass(frozen=True)
class FetchContext:
    managed_http: ManagedHttp
    credential_resolver: Any | None = None


class ProviderPlugin(Protocol):
    def fetch(self, task: FetchTask, context: FetchContext) -> FetchResult: ...


class ProviderRegistry(Protocol):
    def get(self, provider_id: str) -> ProviderPlugin: ...


class FetchEngine:
    def __init__(
        self,
        registry: ProviderRegistry,
        managed_http: ManagedHttp | None = None,
        credential_resolver: Any | None = None,
    ) -> None:
        self._registry = registry
        self._managed_http = managed_http or ManagedHttp(UrllibHttpClient())
        self._credential_resolver = credential_resolver

    def fetch(self, batch: Any) -> FetchResult:
        plugin = self._registry.get(batch.provider_id)
        task = FetchTask.from_batch(batch)
        try:
            return plugin.fetch(
                task,
                FetchContext(
                    managed_http=self._managed_http,
                    credential_resolver=self._credential_resolver,
                ),
            )
        except CredentialMissingError as exc:
            return FetchResult.from_error(batch, status="credential_missing", error=exc)
        except ProviderRateLimitedError as exc:
            return FetchResult.from_error(batch, status="rate_limited", error=exc)
        except ProviderEmptyError as exc:
            return FetchResult.from_empty(batch, error=exc)
        except Exception as exc:
            return FetchResult.from_error(batch, status="error", error=exc)


def _as_string(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)
