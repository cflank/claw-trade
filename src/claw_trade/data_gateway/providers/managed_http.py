from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from claw_trade.data_gateway.models import ProviderStatus

_SENSITIVE_QUERY_KEYS_NORMALIZED = frozenset(
    {
        "xapikey",
        "apikey",
        "key",
        "token",
        "accesstoken",
        "secret",
        "authorization",
        "auth",
    }
)

_SENSITIVE_HEADER_KEYS_NORMALIZED = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "x-api-key",
        "api-key",
        "apikey",
    }
)

_QUOTA_HEADER_KEYS = frozenset(
    {
        "x-ratelimit-remaining",
        "x-rate-limit-remaining",
        "ratelimit-remaining",
        "retry-after",
    }
)


@dataclass(frozen=True)
class ManagedHttpRequest:
    provider_id: str
    provider_config_version: str
    method: str
    url: str
    headers: Mapping[str, str] | None = None
    body: bytes | str | None = None


@dataclass(frozen=True)
class ManagedHttpResponse:
    status_code: int | None
    headers: Mapping[str, str] | None
    empty_result: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ManagedHttpRunResult:
    cache_key: str
    status: ProviderStatus
    quota_signal: str | None
    response: ManagedHttpResponse


def run_managed_http(
    *,
    request: ManagedHttpRequest,
    fetcher: Callable[[ManagedHttpRequest], ManagedHttpResponse],
) -> ManagedHttpRunResult:
    cache_key = stable_http_cache_key(request=request)
    response = fetcher(request)
    status, quota_signal = classify_http_response(
        response_status_code=response.status_code,
        response_headers=response.headers,
        empty_result=response.empty_result,
    )
    if response.error_code:
        status = ProviderStatus.REMOTE_ERROR
    return ManagedHttpRunResult(
        cache_key=cache_key,
        status=status,
        quota_signal=quota_signal,
        response=response,
    )


def stable_http_cache_key(*, request: ManagedHttpRequest) -> str:
    split = urlsplit(request.url)
    canonical_query = canonical_query_without_secrets(split.query)
    query_hash = _sha256(canonical_query.encode("utf-8"))
    body_hash = _body_hash(request.body)
    host = split.hostname or ""
    method = request.method.upper() if request.method else "GET"
    return (
        "http:"
        f"{request.provider_id}:"
        f"{method}:"
        f"{host}:"
        f"{split.path}:"
        f"{query_hash}:"
        f"{body_hash or ''}:"
        f"{request.provider_config_version}"
    )


def classify_http_response(
    *,
    response_status_code: int | None,
    response_headers: Mapping[str, str] | None,
    empty_result: bool,
) -> tuple[ProviderStatus, str | None]:
    quota_signal = detect_quota_signal(response_status_code=response_status_code, response_headers=response_headers)
    if quota_signal is not None:
        return ProviderStatus.RATE_LIMITED, quota_signal
    if response_status_code is not None and response_status_code >= 500:
        return ProviderStatus.REMOTE_ERROR, None
    if empty_result:
        return ProviderStatus.EMPTY, None
    return ProviderStatus.REMOTE_SUCCESS, None


def detect_quota_signal(
    *,
    response_status_code: int | None,
    response_headers: Mapping[str, str] | None,
) -> str | None:
    if response_status_code == 429:
        return "http_429"
    headers = normalize_headers(response_headers or {})
    for key in _QUOTA_HEADER_KEYS:
        value = headers.get(key)
        if value is None:
            continue
        compact = value.strip().lower()
        if compact in {"0", "true", "yes"}:
            return f"header:{key}"
        if key == "retry-after" and compact:
            return f"header:{key}"
    return None


def normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        normalized[str(key).lower()] = str(value)
    return normalized


def redact_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        normalized = _normalize_secret_key(key)
        if normalized in _SENSITIVE_HEADER_KEYS_NORMALIZED or _looks_secret_key(normalized):
            redacted[str(key)] = "[REDACTED]"
            continue
        redacted[str(key)] = str(value)
    return redacted


def redact_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        split = urlsplit(url)
        host_part = split.netloc.rsplit("@", maxsplit=1)[-1]
        if split.username is not None or split.password is not None or "@" in split.netloc:
            netloc = f"[REDACTED]@{host_part}"
        else:
            netloc = split.netloc
        query_pairs = parse_qsl(split.query, keep_blank_values=True)
        redacted_pairs: list[tuple[str, str]] = []
        for key, value in query_pairs:
            if _looks_secret_key(_normalize_secret_key(key)):
                redacted_pairs.append((key, "[REDACTED]"))
            else:
                redacted_pairs.append((key, value))
        redacted_query = urlencode(redacted_pairs, doseq=True) if redacted_pairs else split.query
        return urlunsplit((split.scheme, netloc, split.path, redacted_query, split.fragment))
    except Exception:  # noqa: BLE001
        return url


def canonical_query_without_secrets(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    normalized: list[tuple[str, str]] = []
    for key, value in pairs:
        if _looks_secret_key(_normalize_secret_key(key)):
            normalized.append((key, "[REDACTED]"))
        else:
            normalized.append((key, value))
    normalized.sort(key=lambda item: (item[0], item[1]))
    return urlencode(normalized, doseq=True)


def has_visible_http_exchange(
    *,
    source_url: str | None,
    response_status_code: int | None,
    response_headers: Mapping[str, str] | None,
) -> bool:
    del source_url
    if response_status_code is None:
        return False
    if response_headers is None:
        return False
    return bool(dict(response_headers))


def _body_hash(body: bytes | str | None) -> str | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        payload = body
    else:
        payload = body.encode("utf-8")
    return _sha256(payload)


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _normalize_secret_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _looks_secret_key(normalized_key: str) -> bool:
    if not normalized_key:
        return False
    if normalized_key in _SENSITIVE_QUERY_KEYS_NORMALIZED:
        return True
    if normalized_key.endswith("token") or normalized_key.endswith("apikey"):
        return True
    if "secret" in normalized_key:
        return True
    return False
