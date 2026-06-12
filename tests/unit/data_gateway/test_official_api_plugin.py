from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from claw_trade.data_gateway.execution.fetch_engine import FetchTask
from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec, ManagedHttp
from claw_trade.data_gateway.needs import ProviderCallSpec
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.provider_official_catalog import iter_official_provider_catalog
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.providers.plugins.official_api import (
    OFFICIAL_API_DATA_TYPE,
    OFFICIAL_API_SOURCE_SPECS,
    OfficialApiProviderPlugin,
    _path_from_official_path,
)


@dataclass
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str
    content: bytes | None = None


class _RecordingHttpClient:
    def __init__(self) -> None:
        self.requests: list[HttpRequestSpec] = []

    def send(self, request: HttpRequestSpec) -> _HttpResponse:
        self.requests.append(request)
        return _HttpResponse(status_code=200, headers={}, text='{"ok":true}')


class _Resolver:
    def __init__(self, source_type: str, *, endpoint_url: str | None = None, header_name: str | None = None) -> None:
        self.source_type = source_type
        self.endpoint_url = endpoint_url
        self.header_name = header_name

    def get_credential(self, name: str) -> str | None:
        return f"{self.source_type}-token" if name == f"data_source:{self.source_type}" else None

    def get_endpoint_url(self, name: str) -> str | None:
        if self.endpoint_url and name == f"data_source:{self.source_type}":
            return self.endpoint_url
        return None

    def get_header_name(self, name: str) -> str | None:
        if self.header_name and name == f"data_source:{self.source_type}":
            return self.header_name
        return None


def _task(provider_id: str, source_type: str, params: dict[str, object], *, market: str = "CRYPTO") -> FetchTask:
    symbol = "600519.SH" if source_type == "tushare" else "BTCUSDT"
    endpoint_id = _endpoint_id(params)
    return FetchTask(
        batch_id=f"batch:{source_type}",
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        market=market,
        data_type=OFFICIAL_API_DATA_TYPE,
        granularity="event",
        symbol_ids=(symbol,),
        date_range_start=None,
        date_range_end=None,
        fields=("raw_payload",),
        provider_config_version="test",
        params=params,
    )


def _endpoint_id(params: dict[str, object]) -> str:
    candidate = params.get("provider_call_spec") or params.get("call_spec")
    if isinstance(candidate, ProviderCallSpec):
        return candidate.catalog_endpoint_id
    if isinstance(candidate, dict):
        value = candidate.get("catalog_endpoint_id")
        return str(value) if value is not None else "unknown_endpoint"
    value = params.get("catalog_endpoint_id")
    return str(value) if value is not None else "unknown_endpoint"


def _call_spec(source_type: str, endpoint_id: str, params: dict[str, object]) -> dict[str, object]:
    endpoint = next(item for item in iter_official_catalog_endpoints() if item.source_type == source_type and item.endpoint_id == endpoint_id)
    return ProviderCallSpec(
        call_id=f"call:{endpoint_id}",
        method=endpoint.method,
        provider_id=endpoint.provider_id,
        catalog_endpoint_id=endpoint.endpoint_id,
        official_path_or_api_name=endpoint.official_path_or_api_name,
        params=params,
        auth_scope=endpoint.auth,
        rate_limit_bucket=endpoint.rate_limit_bucket,
        http_visibility=endpoint.http_visibility.value,
        parser_status=endpoint.parser_status,
        batch_key=f"batch:{endpoint_id}",
        official_doc_ref=endpoint.official_doc_ref,
        deadline_at=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        need_ids=("need-1",),
    ).model_dump()


def _plugin(source_type: str) -> OfficialApiProviderPlugin:
    spec = next(item for item in OFFICIAL_API_SOURCE_SPECS if item.source_type == source_type)
    return OfficialApiProviderPlugin(spec)


def test_official_api_plugins_are_registered_for_only_kept_keyed_sources() -> None:
    registry = build_minimal_provider_registry()
    official_caps = [cap for cap in registry.list_all_capabilities() if cap.data_type == OFFICIAL_API_DATA_TYPE]
    source_types = {
        entry.source_type
        for provider_id in {cap.provider_id for cap in official_caps}
        for entry in OFFICIAL_API_SOURCE_SPECS
        if entry.provider_id == provider_id
    }

    assert source_types == {"tushare", "finnhub", "fred", "coingecko_pro", "coinglass", "glassnode"}
    assert {cap.market for cap in official_caps} == {"CN_A", "US", "HK", "CRYPTO"}
    assert all(cap.endpoint_id != "official_api_call" for cap in official_caps)
    assert {"tushare.daily", "finnhub.quote", "coinglass.futures_open_interest"} <= {cap.endpoint_id for cap in official_caps}
    assert all(cap.can_be_formal_fact_source is False for cap in official_caps)


def test_official_provider_catalog_keeps_docs_for_only_kept_keyed_sources() -> None:
    catalog = iter_official_provider_catalog()
    assert {entry.source_type for entry in catalog} == {"tushare", "finnhub", "fred", "coingecko_pro", "coinglass", "glassnode"}
    assert all(entry.docs_url.startswith("https://") for entry in catalog)
    assert all(entry.documented_groups for entry in catalog)


def test_tushare_official_api_uses_api_name_body_token() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH", "fields": ["ts_code", "close"]})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.method == "POST"
    assert request.host == "https://api.tushare.pro"
    assert request.path == "/"
    body = json.loads(str(request.body))
    assert body == {
        "api_name": "daily",
        "token": "tushare-token",
        "params": {"ts_code": "600519.SH"},
        "fields": "ts_code,close",
    }


@pytest.mark.parametrize(
    ("source_type", "params", "expected_path", "expected_query", "expected_header"),
    [
        (
            "finnhub",
            {"provider_call_spec": _call_spec("finnhub", "finnhub.quote", {"symbol": "AAPL"})},
            "/api/v1/quote",
            {"symbol": "AAPL", "token": "finnhub-token"},
            None,
        ),
        (
            "fred",
            {"provider_call_spec": _call_spec("fred", "fred.series_observations", {"series_id": "GDP"})},
            "/fred/series/observations",
            {"series_id": "GDP", "api_key": "fred-token", "file_type": "json"},
            None,
        ),
        (
            "coingecko_pro",
            {"provider_call_spec": _call_spec("coingecko_pro", "coingecko_pro.coins_markets", {"vs_currency": "usd"})},
            "/api/v3/coins/markets",
            {"vs_currency": "usd"},
            "x-cg-pro-api-key",
        ),
        (
            "glassnode",
            {"provider_call_spec": _call_spec("glassnode", "glassnode.addresses_active_count", {"a": "BTC"})},
            "/v1/metrics/addresses/active_count",
            {"a": "BTC", "api_key": "glassnode-token"},
            None,
        ),
    ],
)
def test_rest_official_api_plugins_inject_auth_without_endpoint_whitelist(
    source_type: str,
    params: dict[str, object],
    expected_path: str,
    expected_query: dict[str, str],
    expected_header: str | None,
) -> None:
    client = _RecordingHttpClient()
    plugin = _plugin(source_type)
    task = _task(plugin.plugin_id, source_type, params)

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver(source_type)))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == expected_path
    assert request.query == expected_query
    if expected_header is not None:
        assert request.headers is not None
        assert request.headers[expected_header] == f"{source_type}-token"


def test_coinglass_official_api_reuses_configured_proxy_and_header_name() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_open_interest", {"symbol": "BTC"})},
    )

    result = plugin.fetch(
        task,
        SimpleNamespace(
            managed_http=ManagedHttp(client),
            credential_resolver=_Resolver(
                "coinglass",
                endpoint_url="https://proxy.keystore.com.cn/api/v1/proxy/coinglass",
                header_name="X-Api-Key",
            ),
        ),
    )

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.host == "https://proxy.keystore.com.cn"
    assert request.path == "/api/v1/proxy/coinglass/v4/api/futures/open-interest/exchange-list"
    assert request.headers is not None
    assert request.headers["X-Api-Key"] == "coinglass-token"


def test_official_api_rejects_nested_query_auth_injection_without_http() -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))
    call_spec = _call_spec("finnhub", "finnhub.quote", {"query": {"symbol": "AAPL", "token": "evil-token"}})

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {"provider_call_spec": call_spec}), ctx)

    assert result.status.value == "not_applicable"
    assert "official_api_call_params_forbidden:token" in str(result.error_message)
    assert client.requests == []


def test_official_api_replaces_catalog_path_template_params() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("coingecko_pro")
    task = _task(
        plugin.plugin_id,
        "coingecko_pro",
        {"provider_call_spec": _call_spec("coingecko_pro", "coingecko_pro.coins_id", {"id": "bitcoin"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coingecko_pro")))

    assert result.status.value == "success"
    request = client.requests[0]
    assert request.path == "/api/v3/coins/bitcoin"
    assert request.query == {}
    assert request.headers is not None
    assert request.headers["x-cg-pro-api-key"] == "coingecko_pro-token"


@pytest.mark.parametrize("freeform_params", [{"path": "/quote"}, {"api_name": "quote"}, {"url": "https://evil.test"}, {"header": "X-Key"}, {"token": "secret"}])
def test_official_api_rejects_freeform_call_spec_params_without_http(freeform_params: dict[str, object]) -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", freeform_params), ctx)

    assert result.status.value == "not_applicable"
    assert client.requests == []


def test_official_api_rejects_missing_call_spec_without_http() -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {}), ctx)

    assert result.status.value == "not_applicable"
    assert client.requests == []


@pytest.mark.parametrize("official_path", ["https://evil.test/quote", "//evil.test/quote", "quote", "/../quote"])
def test_official_api_rejects_absolute_or_escaping_catalog_paths(official_path: str) -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))
    call_spec = _call_spec("finnhub", "finnhub.quote", {"symbol": "AAPL"})
    call_spec["official_path_or_api_name"] = official_path
    plugin._catalog_by_endpoint_id["finnhub.quote"] = plugin._catalog_by_endpoint_id["finnhub.quote"].model_copy(
        update={"official_path_or_api_name": official_path}
    )

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {"provider_call_spec": call_spec}), ctx)

    assert result.status.value == "not_applicable"
    assert "official_api_path" in str(result.error_message)
    assert client.requests == []


@pytest.mark.parametrize("official_path", ["https://evil.test/quote", "//evil.test/quote", "quote", "/../quote"])
def test_official_path_validator_rejects_absolute_or_escaping_paths(official_path: str) -> None:
    result = _path_from_official_path(official_path)

    assert isinstance(result, RuntimeError)
