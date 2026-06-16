from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from claw_trade.data_gateway.models import FetchResult

from .managed_http import HttpObservation, HttpRequestSpec, HttpResponseCapture, ManagedHttp, RequestsHttpClient, _redact_headers
from .rate_limiter import RateLimiter


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
    deadline_at: datetime | None = None

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
            deadline_at=_deadline_at(batch),
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
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._registry = registry
        self._managed_http = managed_http or ManagedHttp(RequestsHttpClient())
        self._credential_resolver = credential_resolver
        self._rate_limiter = rate_limiter

    def fetch(self, batch: Any) -> FetchResult:
        plugin = self._registry.get(batch.provider_id)
        task = FetchTask.from_batch(batch)
        try:
            return plugin.fetch(
                task,
                FetchContext(
                    managed_http=self._managed_http_for_batch(batch),
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

    def _managed_http_for_batch(self, batch: Any) -> ManagedHttp:
        if self._rate_limiter is None or not _rate_limit_at_http(batch):
            return self._managed_http
        rate_limit_key = str(getattr(batch, "rate_limit_key", "") or "")
        rate_limit_policy = getattr(batch, "rate_limit_policy", None)
        if not rate_limit_key or rate_limit_policy is None:
            return self._managed_http
        return _RateLimitedManagedHttp(
            inner=self._managed_http,
            rate_limiter=self._rate_limiter,
            rate_limit_key=rate_limit_key,
            rate_limit_policy=rate_limit_policy,
            deadline_at=_deadline_at(batch),
            pre_reserved=_rate_limit_pre_reserved(batch),
        )


def _as_string(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


class _RateLimitedManagedHttp:
    def __init__(
        self,
        *,
        inner: ManagedHttp,
        rate_limiter: RateLimiter,
        rate_limit_key: str,
        rate_limit_policy: Any,
        deadline_at: datetime | None,
        pre_reserved: bool,
    ) -> None:
        self._inner = inner
        self._rate_limiter = rate_limiter
        self._rate_limit_key = rate_limit_key
        self._rate_limit_policy = rate_limit_policy
        self._deadline_at = deadline_at
        self._pre_reserved = pre_reserved

    def stable_key(self, request: HttpRequestSpec) -> str:
        return self._inner.stable_key(request)

    def send(self, request: HttpRequestSpec) -> HttpObservation:
        return self.send_capture(request).observation

    def send_capture(self, request: HttpRequestSpec) -> HttpResponseCapture:
        if self._pre_reserved:
            self._pre_reserved = False
            capture = self._inner.send_capture(request)
            if not _should_retry_managed_http_request(request, capture):
                return capture
            return self._retry_after_reserve(request, fallback=capture)
        capture = self._send_after_reserve(request)
        if not _should_retry_managed_http_request(request, capture):
            return capture
        return self._retry_after_reserve(request, fallback=capture)

    def _send_after_reserve(self, request: HttpRequestSpec) -> HttpResponseCapture:
        decision = self._rate_limiter.reserve(
            self._rate_limit_key,
            self._rate_limit_policy,
            deadline_at=self._deadline_at,
        )
        if not decision.allowed:
            quota_signal = decision.reason or "local_rate_limited"
            return HttpResponseCapture(
                observation=HttpObservation(
                    request_key=self._inner.stable_key(request),
                    method=request.method.upper(),
                    host=request.host,
                    path=request.path,
                    request_headers_redacted=_redact_headers(request.headers),
                    status_code=None,
                    response_headers_redacted={},
                    elapsed_ms=0,
                    quota_signal=quota_signal,
                )
            )
        return self._inner.send_capture(request)

    def _retry_after_reserve(self, request: HttpRequestSpec, *, fallback: HttpResponseCapture) -> HttpResponseCapture:
        retry_capture = self._send_after_reserve(request)
        if retry_capture.observation.quota_signal:
            return fallback
        return retry_capture


def _rate_limit_at_http(batch: Any) -> bool:
    return _as_string(getattr(batch, "http_visibility", "") or "") == "managed_http"


def _deadline_at(batch: Any) -> datetime | None:
    value = getattr(batch, "deadline_at", None)
    return value if isinstance(value, datetime) else None


def _rate_limit_pre_reserved(batch: Any) -> bool:
    return isinstance(getattr(batch, "rate_limit_reserved_at", None), datetime)


def _should_retry_managed_http_request(request: HttpRequestSpec, capture: HttpResponseCapture) -> bool:
    if request.method.upper() not in {"GET", "POST"}:
        return False
    return capture.observation.error_code in {"timeout", "connection_error", "connection_closed", "connection_reset"}
