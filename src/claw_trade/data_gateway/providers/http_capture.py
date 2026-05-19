from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import re
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import urllib.request

_HEADER_ALLOWLIST = frozenset(
    {
        "age",
        "cache-control",
        "cf-cache-status",
        "content-encoding",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "server",
        "vary",
        "via",
        "x-cache",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-request-id",
    }
)

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


@dataclass(frozen=True)
class CapturedHttpExchange:
    method: str
    url: str | None
    response_status_code: int | None
    response_headers_summary: Mapping[str, str]
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class HttpCapture:
    exchanges: list[CapturedHttpExchange] = field(default_factory=list)


@contextmanager
def capture_provider_http() -> Iterator[HttpCapture]:
    capture = HttpCapture()
    patches = _install_patches(capture)
    try:
        yield capture
    finally:
        for owner, name, original in reversed(patches):
            setattr(owner, name, original)


def _install_patches(capture: HttpCapture) -> list[tuple[Any, str, Any]]:
    patches: list[tuple[Any, str, Any]] = []

    try:
        import requests  # type: ignore

        original = requests.sessions.Session.request

        def request_wrapper(
            session: Any,
            method: str,
            url: str,
            *args: Any,
            _original: Any = original,
            **kwargs: Any,
        ) -> Any:
            try:
                response = _original(session, method, url, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                response = getattr(exc, "response", None)
                capture.exchanges.append(
                    CapturedHttpExchange(
                        method=str(method or "GET").upper(),
                        url=_response_url(response) or _sanitize_captured_url(str(url)),
                        response_status_code=_response_status(response),
                        response_headers_summary=_headers_summary(_response_headers(response)),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                raise
            capture.exchanges.append(
                CapturedHttpExchange(
                    method=str(method or "GET").upper(),
                    url=_response_url(response) or _sanitize_captured_url(str(url)),
                    response_status_code=_response_status(response),
                    response_headers_summary=_headers_summary(_response_headers(response)),
                )
            )
            return response

        requests.sessions.Session.request = request_wrapper
        patches.append((requests.sessions.Session, "request", original))
    except Exception:  # noqa: BLE001
        pass

    try:
        import curl_cffi.requests as curl_requests  # type: ignore

        original = curl_requests.Session.request

        def curl_request_wrapper(
            session: Any,
            method: str,
            url: str,
            *args: Any,
            _original: Any = original,
            **kwargs: Any,
        ) -> Any:
            try:
                response = _original(session, method, url, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                response = getattr(exc, "response", None)
                capture.exchanges.append(
                    CapturedHttpExchange(
                        method=str(method or "GET").upper(),
                        url=_response_url(response) or _sanitize_captured_url(str(url)),
                        response_status_code=_response_status(response),
                        response_headers_summary=_headers_summary(_response_headers(response)),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                raise
            capture.exchanges.append(
                CapturedHttpExchange(
                    method=str(method or "GET").upper(),
                    url=_response_url(response) or _sanitize_captured_url(str(url)),
                    response_status_code=_response_status(response),
                    response_headers_summary=_headers_summary(_response_headers(response)),
                )
            )
            return response

        curl_requests.Session.request = curl_request_wrapper
        patches.append((curl_requests.Session, "request", original))
    except Exception:  # noqa: BLE001
        pass

    try:
        import aiohttp  # type: ignore

        original = aiohttp.ClientSession._request

        async def aiohttp_request_wrapper(
            session: Any,
            method: str,
            url: Any,
            *args: Any,
            _original: Any = original,
            **kwargs: Any,
        ) -> Any:
            try:
                response = await _original(session, method, url, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                response = getattr(exc, "response", None)
                capture.exchanges.append(
                    CapturedHttpExchange(
                        method=str(method or "GET").upper(),
                        url=_response_url(response) or _sanitize_captured_url(str(url)),
                        response_status_code=_response_status(response),
                        response_headers_summary=_headers_summary(_response_headers(response)),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                raise
            capture.exchanges.append(
                CapturedHttpExchange(
                    method=str(method or "GET").upper(),
                    url=_response_url(response) or _sanitize_captured_url(str(url)),
                    response_status_code=_response_status(response),
                    response_headers_summary=_headers_summary(_response_headers(response)),
                )
            )
            return response

        aiohttp.ClientSession._request = aiohttp_request_wrapper
        patches.append((aiohttp.ClientSession, "_request", original))
    except Exception:  # noqa: BLE001
        pass

    try:
        from aiohttp_client_cache.session import CachedSession  # type: ignore

        original = CachedSession._request

        async def cached_aiohttp_request_wrapper(
            session: Any,
            method: str,
            url: Any,
            *args: Any,
            _original: Any = original,
            **kwargs: Any,
        ) -> Any:
            try:
                response = await _original(session, method, url, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                response = getattr(exc, "response", None)
                capture.exchanges.append(
                    CapturedHttpExchange(
                        method=str(method or "GET").upper(),
                        url=_response_url(response) or _sanitize_captured_url(str(url)),
                        response_status_code=_response_status(response),
                        response_headers_summary=_headers_summary(_response_headers(response)),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                raise
            capture.exchanges.append(
                CapturedHttpExchange(
                    method=str(method or "GET").upper(),
                    url=_response_url(response) or _sanitize_captured_url(str(url)),
                    response_status_code=_response_status(response),
                    response_headers_summary=_headers_summary(_response_headers(response)),
                )
            )
            return response

        CachedSession._request = cached_aiohttp_request_wrapper
        patches.append((CachedSession, "_request", original))
    except Exception:  # noqa: BLE001
        pass

    original_urlopen = urllib.request.urlopen

    def urlopen_wrapper(url: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            response = original_urlopen(url, *args, **kwargs)
        except HTTPError as exc:
            capture.exchanges.append(
                CapturedHttpExchange(
                    method=_request_method(url),
                    url=_sanitize_captured_url(getattr(exc, "url", None)) or _request_url(url),
                    response_status_code=int(exc.code),
                    response_headers_summary=_headers_summary(getattr(exc, "headers", None)),
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001
            capture.exchanges.append(
                CapturedHttpExchange(
                    method=_request_method(url),
                    url=_request_url(url),
                    response_status_code=None,
                    response_headers_summary={},
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            raise
        capture.exchanges.append(
            CapturedHttpExchange(
                method=_request_method(url),
                url=_response_url(response) or _request_url(url),
                response_status_code=_response_status(response),
                response_headers_summary=_headers_summary(_response_headers(response)),
            )
        )
        return response

    urllib.request.urlopen = urlopen_wrapper
    patches.append((urllib.request, "urlopen", original_urlopen))

    # fundamental.py imports urlopen directly, so patch that module-level binding
    # when the module has already been imported by adapter construction.
    try:
        import claw_trade.data_gateway.providers.fundamental as fundamental_provider

        original = fundamental_provider.urlopen
        fundamental_provider.urlopen = urlopen_wrapper
        patches.append((fundamental_provider, "urlopen", original))
    except Exception:  # noqa: BLE001
        pass

    return patches


def _headers_summary(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    items: list[tuple[str, str]] = []
    if hasattr(headers, "items"):
        items = [(str(key), str(value)) for key, value in headers.items()]
    elif hasattr(headers, "getheaders"):
        items = [(str(key), str(value)) for key, value in headers.getheaders()]
    summary: dict[str, str] = {}
    for key, value in items:
        normalized = key.lower()
        if normalized in _HEADER_ALLOWLIST:
            summary[normalized] = value[:200]
    return summary


def _response_status(response: Any) -> int | None:
    value = getattr(response, "status_code", None)
    if value is None:
        value = getattr(response, "status", None)
    if value is None and hasattr(response, "getcode"):
        value = response.getcode()
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _response_headers(response: Any) -> Any:
    return getattr(response, "headers", None)


def _response_url(response: Any) -> str | None:
    value = getattr(response, "url", None)
    if value:
        return _sanitize_captured_url(str(value))
    if hasattr(response, "geturl"):
        try:
            return _sanitize_captured_url(str(response.geturl()))
        except Exception:  # noqa: BLE001
            return None
    return None


def _request_url(request: Any) -> str | None:
    if isinstance(request, str):
        return _sanitize_captured_url(request)
    value = getattr(request, "full_url", None)
    if value:
        return _sanitize_captured_url(str(value))
    value = getattr(request, "url", None)
    return _sanitize_captured_url(str(value)) if value else None


def _request_method(request: Any) -> str:
    if hasattr(request, "get_method"):
        try:
            return str(request.get_method()).upper()
        except Exception:  # noqa: BLE001
            return "GET"
    return "GET"


def _sanitize_captured_url(url: str | None) -> str | None:
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
            if _is_sensitive_query_key(key):
                redacted_pairs.append((key, "[REDACTED]"))
            else:
                redacted_pairs.append((key, value))
        redacted_query = urlencode(redacted_pairs, doseq=True) if redacted_pairs else split.query
        return urlunsplit((split.scheme, netloc, split.path, redacted_query, split.fragment))
    except Exception:  # noqa: BLE001
        return url


def _is_sensitive_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if not normalized:
        return False
    if normalized in _SENSITIVE_QUERY_KEYS_NORMALIZED:
        return True
    if normalized.endswith("token") or normalized.endswith("apikey"):
        return True
    if "secret" in normalized:
        return True
    return False
