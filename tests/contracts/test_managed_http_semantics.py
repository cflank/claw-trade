from __future__ import annotations

from claw_trade.data_gateway.models import ProviderStatus
from claw_trade.data_gateway.providers import managed_requests
from claw_trade.data_gateway.providers.managed_http import (
    ManagedHttpRequest,
    ManagedHttpResponse,
    ManagedHttpRunResult,
    classify_http_response,
    redact_url,
    run_managed_http,
    stable_http_cache_key,
)


def test_stable_http_key_excludes_secret_values_and_is_order_stable() -> None:
    request_a = ManagedHttpRequest(
        provider_id="market_openbb",
        provider_config_version="cfg-v1",
        method="GET",
        url=(
            "https://user:pwd@api.example.com/v1/quotes?"
            "symbol=AAPL&api_key=token-1&auth=secret-a&limit=10"
        ),
    )
    request_b = ManagedHttpRequest(
        provider_id="market_openbb",
        provider_config_version="cfg-v1",
        method="GET",
        url=(
            "https://user:other@api.example.com/v1/quotes?"
            "limit=10&auth=secret-b&symbol=AAPL&api_key=token-2"
        ),
    )

    key_a = stable_http_cache_key(request=request_a)
    key_b = stable_http_cache_key(request=request_b)

    assert key_a == key_b
    assert "token-1" not in key_a
    assert "token-2" not in key_a
    assert "secret-a" not in key_a
    assert "secret-b" not in key_a
    redacted_url = redact_url(request_a.url)
    assert redacted_url is not None
    assert "[REDACTED]@api.example.com" in redacted_url
    assert "api_key=%5BREDACTED%5D" in redacted_url
    assert "auth=%5BREDACTED%5D" in redacted_url


def test_http_429_and_quota_headers_map_to_rate_limited() -> None:
    status_429, quota_429 = classify_http_response(
        response_status_code=429,
        response_headers={"content-type": "application/json"},
        empty_result=False,
    )
    assert status_429 == ProviderStatus.RATE_LIMITED
    assert quota_429 == "http_429"

    status_quota, quota_header = classify_http_response(
        response_status_code=200,
        response_headers={"x-ratelimit-remaining": "0"},
        empty_result=False,
    )
    assert status_quota == ProviderStatus.RATE_LIMITED
    assert quota_header == "header:x-ratelimit-remaining"


def test_http_empty_and_remote_error_classification() -> None:
    empty_result = run_managed_http(
        request=ManagedHttpRequest(
            provider_id="news_google",
            provider_config_version="cfg-v1",
            method="GET",
            url="https://api.example.com/v1/news?symbol=AAPL",
        ),
        fetcher=lambda _: ManagedHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            empty_result=True,
        ),
    )
    assert empty_result.status == ProviderStatus.EMPTY

    error_result = run_managed_http(
        request=ManagedHttpRequest(
            provider_id="news_google",
            provider_config_version="cfg-v1",
            method="GET",
            url="https://api.example.com/v1/news?symbol=AAPL",
        ),
        fetcher=lambda _: ManagedHttpResponse(
            status_code=None,
            headers={},
            error_code="timeout",
            error_message="network timeout",
        ),
    )
    assert error_result.status == ProviderStatus.REMOTE_ERROR


def test_managed_requests_routes_request_through_managed_http(monkeypatch) -> None:
    seen: list[ManagedHttpRequest] = []

    class _Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"ok": true}'

    def _fake_request(method: str, url: str, **kwargs):
        assert method == "GET"
        assert kwargs["params"] == {"symbol": "AAPL", "api_key": "secret"}
        return _Response()

    def _fake_run_managed_http(*, request: ManagedHttpRequest, fetcher):
        seen.append(request)
        response = fetcher(request)
        return ManagedHttpRunResult(
            cache_key="cache://managed",
            status=ProviderStatus.REMOTE_SUCCESS,
            quota_signal=None,
            response=response,
        )

    monkeypatch.setattr(managed_requests._requests, "request", _fake_request)  # noqa: SLF001
    monkeypatch.setattr(managed_requests, "run_managed_http", _fake_run_managed_http)

    response = managed_requests.get(
        "https://api.example.com/v1/quotes",
        params={"symbol": "AAPL", "api_key": "secret"},
    )

    assert response.status_code == 200
    assert seen
    assert seen[0].method == "GET"
    assert "api_key=secret" in seen[0].url
    assert getattr(response, "_claw_managed_http").cache_key == "cache://managed"
