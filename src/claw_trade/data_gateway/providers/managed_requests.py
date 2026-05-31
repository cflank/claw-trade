from __future__ import annotations

import json
import urllib.request
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import Request

import requests as _requests
from claw_trade.data_gateway.providers.managed_http import (
    ManagedHttpRequest,
    ManagedHttpResponse,
    run_managed_http,
)

exceptions = _requests.exceptions
RequestException = _requests.exceptions.RequestException
Response = _requests.Response

_DEFAULT_PROVIDER_CONFIG_VERSION = "managed-http-v1"


def request(method: str, url: str, **kwargs: Any) -> _requests.Response:
    response: _requests.Response | None = None
    captured_error: BaseException | None = None

    managed_request = ManagedHttpRequest(
        provider_id=_provider_id_from_url(url),
        provider_config_version=_DEFAULT_PROVIDER_CONFIG_VERSION,
        method=method.upper(),
        url=_prepared_requests_url(method=method, url=url, params=kwargs.get("params")),
        headers=_string_mapping(kwargs.get("headers")),
        body=_body_for_cache_key(kwargs),
    )

    def fetcher(_request: ManagedHttpRequest) -> ManagedHttpResponse:
        nonlocal response, captured_error
        try:
            response = _requests.request(method, url, **kwargs)
        except BaseException as exc:
            captured_error = exc
            error_response = getattr(exc, "response", None)
            return ManagedHttpResponse(
                status_code=_response_status(error_response),
                headers=_response_headers(error_response),
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        return ManagedHttpResponse(
            status_code=_response_status(response),
            headers=_response_headers(response),
            empty_result=_response_empty(response),
        )

    result = run_managed_http(request=managed_request, fetcher=fetcher)
    if response is not None:
        setattr(response, "_claw_managed_http", result)
    if captured_error is not None:
        raise captured_error
    if response is None:
        raise RuntimeError("managed HTTP request did not produce a response")
    return response


def get(url: str, **kwargs: Any) -> _requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> _requests.Response:
    return request("POST", url, **kwargs)


class Session:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._session = _requests.Session(*args, **kwargs)

    @property
    def headers(self) -> Any:
        return self._session.headers

    def request(self, method: str, url: str, **kwargs: Any) -> _requests.Response:
        response: _requests.Response | None = None
        captured_error: BaseException | None = None
        headers = dict(self._session.headers)
        headers.update(_string_mapping(kwargs.get("headers")))
        managed_request = ManagedHttpRequest(
            provider_id=_provider_id_from_url(url),
            provider_config_version=_DEFAULT_PROVIDER_CONFIG_VERSION,
            method=method.upper(),
            url=_prepared_requests_url(method=method, url=url, params=kwargs.get("params")),
            headers=headers,
            body=_body_for_cache_key(kwargs),
        )

        def fetcher(_request: ManagedHttpRequest) -> ManagedHttpResponse:
            nonlocal response, captured_error
            try:
                response = self._session.request(method, url, **kwargs)
            except BaseException as exc:
                captured_error = exc
                error_response = getattr(exc, "response", None)
                return ManagedHttpResponse(
                    status_code=_response_status(error_response),
                    headers=_response_headers(error_response),
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            return ManagedHttpResponse(
                status_code=_response_status(response),
                headers=_response_headers(response),
                empty_result=_response_empty(response),
            )

        result = run_managed_http(request=managed_request, fetcher=fetcher)
        if response is not None:
            setattr(response, "_claw_managed_http", result)
        if captured_error is not None:
            raise captured_error
        if response is None:
            raise RuntimeError("managed HTTP request did not produce a response")
        return response

    def get(self, url: str, **kwargs: Any) -> _requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _requests.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._session.close()


def urlopen(url: str | Request, *args: Any, **kwargs: Any) -> Any:
    response: Any | None = None
    captured_error: BaseException | None = None
    request_url = _request_url(url)
    managed_request = ManagedHttpRequest(
        provider_id=_provider_id_from_url(request_url),
        provider_config_version=_DEFAULT_PROVIDER_CONFIG_VERSION,
        method=_request_method(url),
        url=request_url,
        headers=_request_headers(url),
        body=None,
    )

    def fetcher(_request: ManagedHttpRequest) -> ManagedHttpResponse:
        nonlocal response, captured_error
        try:
            response = urllib.request.urlopen(url, *args, **kwargs)
        except BaseException as exc:
            captured_error = exc
            return ManagedHttpResponse(
                status_code=_response_status(exc),
                headers=_response_headers(exc),
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        return ManagedHttpResponse(
            status_code=_response_status(response),
            headers=_response_headers(response),
        )

    run_managed_http(request=managed_request, fetcher=fetcher)
    if captured_error is not None:
        raise captured_error
    if response is None:
        raise RuntimeError("managed HTTP urlopen did not produce a response")
    return response


def _prepared_requests_url(*, method: str, url: str, params: Any) -> str:
    try:
        prepared = _requests.Request(method=method.upper(), url=url, params=params).prepare()
        return str(prepared.url or url)
    except Exception:
        return url


def _body_for_cache_key(kwargs: Mapping[str, Any]) -> bytes | str | None:
    if kwargs.get("data") is not None:
        data = kwargs["data"]
        if isinstance(data, (bytes, str)):
            return data
        return json.dumps(data, sort_keys=True, ensure_ascii=False)
    if kwargs.get("json") is not None:
        return json.dumps(kwargs["json"], sort_keys=True, ensure_ascii=False)
    return None


def _provider_id_from_url(url: str | None) -> str:
    if not url:
        return "unknown_http_provider"
    try:
        return str(urlsplit(url).hostname or "unknown_http_provider")
    except Exception:
        return "unknown_http_provider"


def _response_status(response: Any) -> int | None:
    value = getattr(response, "status_code", None)
    if value is None:
        value = getattr(response, "status", None)
    if value is None:
        value = getattr(response, "code", None)
    if value is None and hasattr(response, "getcode"):
        try:
            value = response.getcode()
        except Exception:
            value = None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _response_headers(response: Any) -> Mapping[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(response, "hdrs", None)
    if headers is None:
        return {}
    if hasattr(headers, "items"):
        return {str(key): str(value) for key, value in headers.items()}
    return {}


def _response_empty(response: Any) -> bool:
    if response is None:
        return False
    content = getattr(response, "content", None)
    if content is not None:
        return len(content) == 0
    text = getattr(response, "text", None)
    if text is not None:
        return str(text) == ""
    return False


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _request_url(request: str | Request) -> str:
    if isinstance(request, str):
        return request
    return str(getattr(request, "full_url", None) or getattr(request, "url", ""))


def _request_method(request: str | Request) -> str:
    if hasattr(request, "get_method"):
        try:
            return str(request.get_method()).upper()
        except Exception:
            return "GET"
    return "GET"


def _request_headers(request: str | Request) -> Mapping[str, str] | None:
    headers = getattr(request, "headers", None)
    return _string_mapping(headers) if isinstance(headers, Mapping) else None
