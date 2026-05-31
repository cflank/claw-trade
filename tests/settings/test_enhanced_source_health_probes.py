from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from claw_trade.data_gateway import ui_runtime_checks
from claw_trade.ui_backend.data_source_settings import DataSourceSettingsService

PROBE_BACKED_SOURCE_TYPES: tuple[str, ...] = (
    "tushare",
    "alpha_vantage",
    "fmp",
    "polygon",
    "finnhub",
    "tiingo",
    "nasdaq_data_link",
    "coingecko_pro",
    "coinmarketcap",
)


class _FakeResponse:
    def __init__(self, *, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self) -> object:
        return self._payload


def test_data_source_health_tester_fixed_sources_do_not_hit_probe_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def _record(name: str):
        def _probe(**_: object) -> None:
            called.append(name)

        return _probe

    monkeypatch.setattr(ui_runtime_checks, "_probe_tushare", _record("tushare"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_alpha_vantage", _record("alpha_vantage"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_fmp", _record("fmp"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_polygon", _record("polygon"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_finnhub", _record("finnhub"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_tiingo", _record("tiingo"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_nasdaq_data_link", _record("nasdaq_data_link"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_coingecko_pro", _record("coingecko_pro"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_coinmarketcap", _record("coinmarketcap"))

    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    for source_type in PROBE_BACKED_SOURCE_TYPES:
        assert tester({"supportedType": source_type})["status"] == "validated"

    assert called == list(PROBE_BACKED_SOURCE_TYPES)


def test_successful_probe_does_not_expand_settings_beyond_factory_tushare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui_runtime_checks, "_probe_tushare", lambda **_: None)
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    result = tester({"supportedType": "tushare", "apiKeyReplacement": "ts-token"})
    assert result["status"] == "validated"
    assert result["requestKind"] == "ui_probe"
    assert result["consumerType"] == "ui_probe"
    assert result["dataRequirement"]["dataType"] == "provider_connection_probe"
    assert result["evidence"] == {
        "kind": "probe_only",
        "probeOnly": True,
        "mainChainEvidence": False,
        "remoteSuccess": False,
    }
    assert DataSourceSettingsService(health_tester=tester).list_data_sources()["supportedTypes"] == ("tushare",)


def test_data_source_health_tester_unknown_type_uses_probe_not_supported_fallback() -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    with pytest.raises(RuntimeError, match="probe_not_supported"):
        tester({"supportedType": "unknown_provider"})


def test_probe_akshare_uses_real_path_and_accepts_non_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = SimpleNamespace(tool_trade_date_hist_sina=lambda: ["2026-05-23"])
    monkeypatch.setattr(ui_runtime_checks.importlib, "import_module", lambda _name: fake_module)
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    result = tester({"supportedType": "akshare"})

    assert result["status"] == "validated"


def test_probe_akshare_rejects_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = SimpleNamespace(tool_trade_date_hist_sina=lambda: [])
    monkeypatch.setattr(ui_runtime_checks.importlib, "import_module", lambda _name: fake_module)
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    with pytest.raises(RuntimeError, match="empty_probe"):
        tester({"supportedType": "akshare"})


def test_probe_tushare_uses_real_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    def _fake_create_tushare_pro(*, token: str, env: dict[str, str]) -> object:
        called["token"] = token
        called["env"] = dict(env)
        return object()

    def _fake_call_tushare_pro_bar(**kwargs: object) -> object:
        called["kwargs"] = dict(kwargs)
        return {"rows": [{"close": 1.0}]}

    monkeypatch.setattr(ui_runtime_checks, "create_tushare_pro", _fake_create_tushare_pro)
    monkeypatch.setattr(ui_runtime_checks, "call_tushare_pro_bar", _fake_call_tushare_pro_bar)
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    result = tester({"supportedType": "tushare", "apiKeyReplacement": "ts-token"})

    assert result["status"] == "validated"
    assert called["token"] == "ts-token"
    assert called["env"]["TUSHARE_TOKEN"] == "ts-token"
    assert called["kwargs"]["ts_code"] == "000001.SZ"
    assert called["kwargs"]["env"]["TUSHARE_TOKEN"] == "ts-token"


def test_probe_tushare_passes_endpoint_url_to_pro_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    monkeypatch.setattr(ui_runtime_checks, "create_tushare_pro", lambda **_: object())

    def _fake_call_tushare_pro_bar(**kwargs: object) -> object:
        called["kwargs"] = dict(kwargs)
        return {"rows": [{"close": 1.0}]}

    monkeypatch.setattr(ui_runtime_checks, "call_tushare_pro_bar", _fake_call_tushare_pro_bar)
    tester = ui_runtime_checks.build_data_source_health_tester(env={"TUSHARE_HTTP_URL": "http://old.example"})

    result = tester(
        {
            "supportedType": "tushare",
            "apiKeyReplacement": "ts-token",
            "endpointUrl": "http://111.170.140.159:8020",
        }
    )

    assert result["status"] == "validated"
    assert called["kwargs"]["env"]["TUSHARE_HTTP_URL"] == "http://111.170.140.159:8020"


def test_probe_tushare_rejects_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui_runtime_checks, "create_tushare_pro", lambda **_: object())
    monkeypatch.setattr(ui_runtime_checks, "call_tushare_pro_bar", lambda **_: None)
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    with pytest.raises(RuntimeError, match="empty_probe"):
        tester({"supportedType": "tushare", "apiKeyReplacement": "ts-token"})


@pytest.mark.parametrize(
    ("source_type", "env", "payload"),
    (
        ("alpha_vantage", {"ALPHA_VANTAGE_API_KEY": "k"}, {"Global Quote": {"05. price": "1.0"}}),
        ("fmp", {"FMP_API_KEY": "k"}, [{"symbol": "AAPL"}]),
        ("polygon", {"POLYGON_API_KEY": "k"}, {"status": "OK", "results": {"ticker": "AAPL"}}),
        ("finnhub", {"FINNHUB_TOKEN": "k"}, {"c": 1.0}),
        ("tiingo", {"TIINGO_TOKEN": "k"}, {"ticker": "AAPL"}),
        ("nasdaq_data_link", {"NASDAQ_DATA_LINK_API_KEY": "k"}, {"datasets": [{"database_code": "WIKI"}]}),
        ("coingecko_pro", {"COINGECKO_PRO_API_KEY": "k"}, {"gecko_says": "(V3) To the Moon!"}),
        ("coinmarketcap", {"CMC_PRO_API_KEY": "k"}, {"status": {"error_code": 0}, "data": {"plan": "basic"}}),
    ),
)
def test_requests_based_probes_accept_success_payloads(
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    env: dict[str, str],
    payload: object,
) -> None:
    monkeypatch.setattr(
        ui_runtime_checks.requests,
        "get",
        lambda *_, **__: _FakeResponse(payload=payload),
    )
    tester = ui_runtime_checks.build_data_source_health_tester(env=env)

    result = tester({"supportedType": source_type})

    assert result["status"] == "validated"


@pytest.mark.parametrize(
    ("source_type", "env", "payload"),
    (
        ("alpha_vantage", {"ALPHA_VANTAGE_API_KEY": "k"}, {"Error Message": "bad key"}),
        ("fmp", {"FMP_API_KEY": "k"}, {"error": "invalid"}),
        ("polygon", {"POLYGON_API_KEY": "k"}, {"status": "ERROR", "error": "forbidden"}),
        ("finnhub", {"FINNHUB_TOKEN": "k"}, {"error": "invalid token"}),
        ("tiingo", {"TIINGO_TOKEN": "k"}, {"detail": "forbidden", "message": "forbidden"}),
        ("nasdaq_data_link", {"NASDAQ_DATA_LINK_API_KEY": "k"}, {"quandl_error": {"message": "invalid"}}),
        ("coingecko_pro", {"COINGECKO_PRO_API_KEY": "k"}, {"error": "throttled"}),
        ("coinmarketcap", {"CMC_PRO_API_KEY": "k"}, {"status": {"error_code": 1002, "error_message": "bad key"}}),
    ),
)
def test_requests_based_probes_reject_error_payloads(
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    env: dict[str, str],
    payload: object,
) -> None:
    monkeypatch.setattr(
        ui_runtime_checks.requests,
        "get",
        lambda *_, **__: _FakeResponse(payload=payload),
    )
    tester = ui_runtime_checks.build_data_source_health_tester(env=env)

    with pytest.raises(RuntimeError):
        tester({"supportedType": source_type})


def test_probe_binance_http_failure_is_not_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ui_runtime_checks.requests,
        "get",
        lambda *_, **__: _FakeResponse(payload={}, status_code=500),
    )
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    with pytest.raises(RuntimeError):
        tester({"supportedType": "binance"})


@pytest.mark.parametrize(
    "source_type",
    (
        "tushare",
        "alpha_vantage",
        "fmp",
        "polygon",
        "finnhub",
        "tiingo",
        "nasdaq_data_link",
        "coingecko_pro",
        "coinmarketcap",
    ),
)
def test_keyed_sources_require_credentials_without_fallback(source_type: str) -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    with pytest.raises(RuntimeError, match="credential_missing"):
        tester({"supportedType": source_type})


def test_probe_coingecko_uses_demo_key_header_without_pro_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_headers: dict[str, str] = {}

    def _fake_get(*_: object, **kwargs: object) -> _FakeResponse:
        headers = kwargs.get("headers")
        if isinstance(headers, dict):
            seen_headers.update(headers)
        return _FakeResponse(payload={"gecko_says": "ok"})

    monkeypatch.setattr(ui_runtime_checks.requests, "get", _fake_get)
    tester = ui_runtime_checks.build_data_source_health_tester(env={"COINGECKO_DEMO_API_KEY": "demo"})

    result = tester({"supportedType": "coingecko"})

    assert result["status"] == "validated"
    assert "x-cg-pro-api-key" not in {str(key).lower() for key in seen_headers}
    assert seen_headers["x-cg-demo-api-key"] == "demo"


def test_probe_coingecko_pro_uses_pro_api_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
        seen["url"] = url
        headers = kwargs.get("headers")
        if isinstance(headers, dict):
            seen["headers"] = dict(headers)
        return _FakeResponse(payload={"gecko_says": "ok"})

    monkeypatch.setattr(ui_runtime_checks.requests, "get", _fake_get)
    tester = ui_runtime_checks.build_data_source_health_tester(env={"COINGECKO_PRO_API_KEY": "pro-token"})

    result = tester({"supportedType": "coingecko_pro"})

    assert result["status"] == "validated"
    assert seen["url"] == "https://pro-api.coingecko.com/api/v3/ping"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["x-cg-pro-api-key"] == "pro-token"
