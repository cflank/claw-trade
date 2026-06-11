from __future__ import annotations

from dataclasses import dataclass
from http.client import RemoteDisconnected

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec, ManagedHttp


@dataclass
class _Response:
    status_code: int
    headers: dict[str, str]
    text: str


class _Client:
    def __init__(
        self,
        response: _Response | None = None,
        *,
        raise_timeout: bool = False,
        raise_error: Exception | None = None,
    ) -> None:
        self.response = response
        self.raise_timeout = raise_timeout
        self.raise_error = raise_error

    def send(self, request: HttpRequestSpec) -> _Response:
        if self.raise_timeout:
            raise TimeoutError("timeout")
        if self.raise_error is not None:
            raise self.raise_error
        assert request.method in {"GET", "POST"}
        assert request.host
        return self.response or _Response(status_code=200, headers={}, text="{}")


def test_stable_key_redacts_secrets() -> None:
    client = _Client()
    http = ManagedHttp(client)
    req1 = HttpRequestSpec(
        method="GET",
        host="api.example.com",
        path="/daily",
        query={"symbol": "000001.SZ", "token": "secret-1"},
        headers={"Authorization": "Bearer abc"},
    )
    req2 = HttpRequestSpec(
        method="GET",
        host="api.example.com",
        path="/daily",
        query={"symbol": "000001.SZ", "token": "secret-2"},
        headers={"Authorization": "Bearer xyz"},
    )
    assert http.stable_key(req1) == http.stable_key(req2)


def test_managed_http_maps_429_to_rate_limit_signal() -> None:
    http = ManagedHttp(_Client(response=_Response(status_code=429, headers={"Retry-After": "30"}, text="limited")))
    obs = http.send(HttpRequestSpec(method="GET", host="api.example.com", path="/daily"))
    assert obs.status_code == 429
    assert obs.quota_signal == "http_429"
    assert obs.error_code is None
    assert obs.sent_at is not None


def test_managed_http_timeout_returns_observation_error() -> None:
    http = ManagedHttp(_Client(raise_timeout=True))
    obs = http.send(HttpRequestSpec(method="GET", host="api.example.com", path="/daily"))
    assert obs.status_code is None
    assert obs.error_code == "timeout"
    assert obs.sent_at is not None


def test_managed_http_connection_closed_returns_specific_observation_error() -> None:
    http = ManagedHttp(_Client(raise_error=RemoteDisconnected("closed")))
    obs = http.send(HttpRequestSpec(method="GET", host="api.example.com", path="/daily"))
    assert obs.status_code is None
    assert obs.error_code == "connection_closed"


def test_managed_http_capture_exposes_json_body_with_observation() -> None:
    http = ManagedHttp(_Client(response=_Response(status_code=200, headers={}, text='{"ok": true}')))
    capture = http.send_capture(HttpRequestSpec(method="GET", host="api.example.com", path="/daily"))
    assert capture.observation.status_code == 200
    assert capture.observation.response_body_hash
    assert capture.json_payload == {"ok": True}
