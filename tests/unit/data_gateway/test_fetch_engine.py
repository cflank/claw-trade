from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from claw_trade.data_gateway.execution.fetch_engine import FetchEngine
from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec, ManagedHttp
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.models import FetchResult, FetchStatus, HttpVisibility


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


@dataclass(frozen=True)
class _Response:
    status_code: int
    headers: dict[str, str]
    text: str

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class _HttpClient:
    def __init__(self) -> None:
        self.requests: list[HttpRequestSpec] = []

    def send(self, request: HttpRequestSpec) -> _Response:
        self.requests.append(request)
        return _Response(status_code=200, headers={}, text='{"ok": true}')


class _Registry:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def get(self, provider_id: str) -> Any:
        assert provider_id == "provider"
        return self.plugin


class _ThreeHttpCallsPlugin:
    def __init__(self) -> None:
        self.managed_http_type_names: list[str] = []

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        self.managed_http_type_names.append(type(ctx.managed_http).__name__)
        observations = []
        for index in range(3):
            capture = ctx.managed_http.send_capture(
                HttpRequestSpec(
                    method="GET",
                    host="example.com",
                    path=f"/items/{index}",
                    provider_config_version=getattr(task, "provider_config_version", None),
                )
            )
            observations.append(capture.observation)
            if capture.observation.quota_signal or capture.observation.status_code == 429:
                return FetchResult.from_error(
                    task,
                    status="rate_limited",
                    error=RuntimeError(capture.observation.quota_signal or "http_429"),
                    http_observations=tuple(observations),
                )
        return FetchResult.from_success(task, payload={"rows": []}, row_count=0, http_observations=tuple(observations))


@pytest.mark.parametrize("http_visibility", ("managed_http", HttpVisibility.MANAGED_HTTP))
def test_fetch_engine_rate_limits_each_managed_http_send_and_waits(http_visibility: object) -> None:
    clock = _Clock(datetime(2026, 6, 11, 12, 0, tzinfo=UTC))
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.tick(seconds)

    client = _HttpClient()
    limiter = RateLimiter(now_fn=clock, sleep_fn=_sleep)
    batch = SimpleNamespace(
        batch_id="batch:1",
        provider_id="provider",
        endpoint_id="endpoint",
        market="CRYPTO",
        data_type="derivative_metric",
        granularity="1h",
        symbol_ids=("BTCUSDT",),
        date_range_start=None,
        date_range_end=None,
        fields_union=("cvd",),
        provider_config_version="test",
        params={},
        http_visibility=http_visibility,
        rate_limit_key="ratelimit:test",
        rate_limit_policy=RateLimitPolicy(window_seconds=60, max_requests=2, overflow="wait", wait_timeout_seconds=120),
    )
    plugin = _ThreeHttpCallsPlugin()
    engine = FetchEngine(
        _Registry(plugin),
        managed_http=ManagedHttp(client),
        rate_limiter=limiter,
    )

    result = engine.fetch(batch)

    assert result.status == FetchStatus.SUCCESS
    assert plugin.managed_http_type_names == ["_RateLimitedManagedHttp"]
    assert len(client.requests) == 3
    assert sleep_calls == [30.0, 30.0]
