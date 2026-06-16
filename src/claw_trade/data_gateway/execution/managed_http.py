from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass
from hashlib import sha256
from http.client import RemoteDisconnected
from time import monotonic
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import requests

SECRET_KEYS = frozenset(
    {
        "authorization",
        "token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "x-api-key",
        "cg-api-key",
        "x-cg-pro-api-key",
        "x-cg-demo-api-key",
        "x-cmc_pro_api_key",
    }
)


@dataclass(frozen=True)
class HttpRequestSpec:
    method: str
    host: str
    path: str
    query: Mapping[str, Any] | None = None
    body: Mapping[str, Any] | str | bytes | None = None
    headers: Mapping[str, str] | None = None
    provider_config_version: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class HttpObservation:
    request_key: str
    method: str
    host: str
    path: str
    request_headers_redacted: dict[str, str]
    sent_at: datetime | None = None
    status_code: int | None = None
    response_headers_redacted: dict[str, str] | None = None
    response_body_hash: str | None = None
    elapsed_ms: int | None = None
    error_code: str | None = None
    quota_signal: str | None = None
    sdk_internal_unknown: bool = False


@dataclass(frozen=True)
class HttpResponseCapture:
    observation: HttpObservation
    body_text: str | None = None
    json_payload: Any | None = None


class HttpClient(Protocol):
    def send(self, request: HttpRequestSpec) -> Any: ...


@dataclass(frozen=True)
class _UrllibResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class UrllibHttpClient:
    def send(self, request: HttpRequestSpec) -> _UrllibResponse:
        method = request.method.upper()
        target = _url_from_request(request)
        body: bytes | None = None
        if request.body is not None:
            if isinstance(request.body, bytes):
                body = request.body
            elif isinstance(request.body, str):
                body = request.body.encode("utf-8")
            else:
                body = urlencode(dict(request.body)).encode("utf-8")
        req = Request(target, data=body, headers=dict(request.headers or {}), method=method)
        try:
            with urlopen(req, timeout=request.timeout_seconds) as response:  # noqa: S310
                return _UrllibResponse(
                    status_code=int(response.status),
                    headers=dict(response.headers.items()),
                    content=response.read(),
                )
        except HTTPError as exc:
            return _UrllibResponse(
                status_code=int(exc.code),
                headers=dict(exc.headers.items()),
                content=exc.read(),
            )


class RequestsHttpClient:
    def send(self, request: HttpRequestSpec) -> _UrllibResponse:
        method = request.method.upper()
        target = _url_from_request(request)
        body: bytes | str | None = None
        if request.body is not None:
            if isinstance(request.body, (bytes, str)):
                body = request.body
            else:
                body = urlencode(dict(request.body))
        response = requests.request(
            method,
            target,
            data=body,
            headers=dict(request.headers or {}),
            timeout=request.timeout_seconds,
        )
        return _UrllibResponse(
            status_code=int(response.status_code),
            headers=dict(response.headers.items()),
            content=response.content,
        )


class ManagedHttp:
    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def stable_key(self, request: HttpRequestSpec) -> str:
        query_hash = _hash_text(urlencode(sorted(_redact_mapping(request.query).items()))) if request.query else None
        body_hash = _hash_body(request.body)
        payload = {
            "method": request.method.upper(),
            "host": request.host.lower(),
            "path": request.path,
            "query_hash": query_hash,
            "body_hash": body_hash,
            "provider_config_version": request.provider_config_version,
        }
        return f"http:{_hash_text(_stable_json(payload))}"

    def send(self, request: HttpRequestSpec) -> HttpObservation:
        return self.send_capture(request).observation

    def send_capture(self, request: HttpRequestSpec) -> HttpResponseCapture:
        request_key = self.stable_key(request)
        started = monotonic()
        sent_at = datetime.now(tz=UTC)
        headers = _redact_headers(request.headers)
        try:
            response = self._client.send(request)
        except TimeoutError:
            return HttpResponseCapture(
                observation=HttpObservation(
                    request_key=request_key,
                    method=request.method.upper(),
                    host=request.host,
                    path=request.path,
                    request_headers_redacted=headers,
                    sent_at=sent_at,
                    elapsed_ms=_elapsed_ms(started),
                    error_code="timeout",
                )
            )
        except Exception as exc:
            return HttpResponseCapture(
                observation=HttpObservation(
                    request_key=request_key,
                    method=request.method.upper(),
                    host=request.host,
                    path=request.path,
                    request_headers_redacted=headers,
                    sent_at=sent_at,
                    elapsed_ms=_elapsed_ms(started),
                    error_code=_transport_error_code(exc),
                )
            )

        status_code = int(getattr(response, "status_code", 0) or 0)
        response_headers = {str(k).lower(): str(v) for k, v in dict(getattr(response, "headers", {}) or {}).items()}
        body_bytes = _response_body_bytes(response)
        quota_signal = _quota_signal(status_code=status_code, headers=response_headers)
        body_text = _decode_body(body_bytes, response_headers) if body_bytes else None
        return HttpResponseCapture(
            observation=HttpObservation(
                request_key=request_key,
                method=request.method.upper(),
                host=request.host,
                path=request.path,
                request_headers_redacted=headers,
                sent_at=sent_at,
                status_code=status_code,
                response_headers_redacted=response_headers,
                response_body_hash=_hash_text(body_text) if body_text else None,
                elapsed_ms=_elapsed_ms(started),
                quota_signal=quota_signal,
            ),
            body_text=body_text,
            json_payload=_json_or_none(body_text),
        )


def _url_from_request(request: HttpRequestSpec) -> str:
    host = request.host.strip()
    base = host if host.startswith(("http://", "https://")) else f"https://{host}"
    target = f"{base}{request.path}"
    if request.query:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{urlencode(request.query)}"
    return target


def _json_or_none(text: str | None) -> Any | None:
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _response_body_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.encode("utf-8")
    return b""


def _decode_body(body: bytes, headers: Mapping[str, str]) -> str:
    body = _decompress_body(body, str(headers.get("content-encoding", "") or ""))
    content_type = headers.get("content-type", "")
    charset = _charset_from_content_type(content_type)
    if charset:
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("gb18030", errors="replace")


def _decompress_body(body: bytes, content_encoding: str) -> bytes:
    encoding = content_encoding.strip().lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(body)
        if "deflate" in encoding:
            return zlib.decompress(body)
    except (OSError, zlib.error):
        return body
    return body


def _transport_error_code(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, RemoteDisconnected):
        return "connection_closed"
    if isinstance(exc, ConnectionResetError):
        return "connection_reset"
    return f"request_error:{type(exc).__name__}"


def _charset_from_content_type(content_type: str) -> str | None:
    for part in content_type.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.lower() == "charset":
            return value.strip().strip('"') or None
    return None


def _redact_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    output: dict[str, str] = {}
    for key, value in headers.items():
        output[key.lower()] = "<redacted>" if key.lower() in SECRET_KEYS else value
    return output


def _redact_mapping(payload: Mapping[str, Any] | None) -> dict[str, str]:
    if not payload:
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        value_text = "<redacted>" if str(key).lower() in SECRET_KEYS else str(value)
        result[str(key)] = value_text
    return result


def _hash_body(body: Mapping[str, Any] | str | bytes | None) -> str | None:
    if body is None:
        return None
    if isinstance(body, Mapping):
        return _hash_text(_stable_json(_redact_mapping(body)))
    if isinstance(body, bytes):
        return f"sha256:{sha256(body).hexdigest()}"
    return _hash_text(body)


def _stable_json(payload: Mapping[str, Any]) -> str:
    items = [f"{key}={payload[key]}" for key in sorted(payload)]
    return "&".join(items)


def _hash_text(text: str) -> str:
    return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"


def _elapsed_ms(started: float) -> int:
    return int((monotonic() - started) * 1000)


def _quota_signal(*, status_code: int, headers: Mapping[str, str]) -> str | None:
    if status_code == 429:
        return "http_429"
    if "retry-after" in headers:
        return "retry_after"
    if "x-ratelimit-remaining" in headers and headers["x-ratelimit-remaining"] == "0":
        return "quota_exhausted"
    return None
