from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import PROVIDER_HTTP_CLIENT_ERROR, PROVIDER_HTTP_SERVER_ERROR  # noqa: E402
from frontline_data_pack.models import ProviderQuery, ProviderQueryParameter, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import PROVIDER_SCHEMA_INVALID, execute_provider_attempt  # noqa: E402
from frontline_data_pack.providers_eastmoney import call_eastmoney_push2his_kline  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("d" * 64)


def test_t_pvd_005_builds_eastmoney_direct_request_with_required_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_http_get(url: str, *, params: dict[str, str], timeout: float) -> object:
        captured["url"] = url
        captured["params"] = dict(params)
        captured["timeout"] = timeout
        return _FakeResponse(
            status_code=200,
            payload={
                "data": {
                    "klines": [
                        "2026-05-07,1600.10,1610.20,1620.30,1590.40,1234567,7890000.50",
                    ]
                }
            },
        )

    monkeypatch.setattr("frontline_data_pack.providers_eastmoney._http_get", _fake_http_get)

    call_eastmoney_push2his_kline(
        _spec(provider="eastmoney_direct", endpoint="push2his_kline"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=4321),
    )

    assert captured["url"] == "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    assert captured["params"]["secid"] == "1.600519"
    assert captured["params"]["klt"] == "101"
    assert captured["params"]["fqt"] == "1"
    assert captured["params"]["beg"] == "20260401"
    assert captured["params"]["end"] == "20260508"
    assert captured["params"]["fields1"] != ""
    assert captured["params"]["fields2"] != ""
    assert math.isclose(float(captured["timeout"]), 4.321, rel_tol=0.0, abs_tol=1e-9)


def test_t_pvd_005_kline_rows_are_parsed_and_accepted_count_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get(url: str, *, params: dict[str, str], timeout: float) -> object:
        _ = url, params, timeout
        return _FakeResponse(
            status_code=200,
            payload={
                "data": {
                    "klines": [
                        "2026-05-07,1600.10,1610.20,1620.30,1590.40,1234567,7890000.50",
                        "2026-05-08,1611.00,1622.00,1633.00,1600.00,2000000,9000000.00",
                    ]
                }
            },
        )

    monkeypatch.setattr("frontline_data_pack.providers_eastmoney._http_get", _fake_http_get)

    spec = _spec(provider="eastmoney_direct", endpoint="push2his_kline")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_eastmoney_push2his_kline},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 2
    assert result.normalized_rows[0]["trade_date"] == "2026-05-07"
    assert result.normalized_rows[0]["open"] == 1600.1
    assert result.normalized_rows[0]["close"] == 1610.2
    assert result.normalized_rows[0]["adjust"] == "qfq"


def test_t_pvd_005_http_500_maps_to_error_with_server_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get(url: str, *, params: dict[str, str], timeout: float) -> object:
        _ = url, params, timeout
        return _FakeResponse(status_code=500, payload={"rc": 500})

    monkeypatch.setattr("frontline_data_pack.providers_eastmoney._http_get", _fake_http_get)

    spec = _spec(provider="eastmoney_direct", endpoint="push2his_kline")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_eastmoney_push2his_kline},
    )

    assert result.attempt.status == "error"
    assert result.attempt.error_code == PROVIDER_HTTP_SERVER_ERROR
    assert result.attempt.error_message_redacted is not None
    assert "push2his.eastmoney.com" not in result.attempt.error_message_redacted


def test_t_pvd_005_http_404_maps_to_error_with_client_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get(url: str, *, params: dict[str, str], timeout: float) -> object:
        _ = url, params, timeout
        return _FakeResponse(status_code=404, payload={"rc": 404})

    monkeypatch.setattr("frontline_data_pack.providers_eastmoney._http_get", _fake_http_get)

    spec = _spec(provider="eastmoney_direct", endpoint="push2his_kline")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_eastmoney_push2his_kline},
    )

    assert result.attempt.status == "error"
    assert result.attempt.error_code == PROVIDER_HTTP_CLIENT_ERROR
    assert result.attempt.error_message_redacted is not None
    assert "push2his.eastmoney.com" not in result.attempt.error_message_redacted


def test_t_pvd_005_missing_data_klines_maps_to_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get(url: str, *, params: dict[str, str], timeout: float) -> object:
        _ = url, params, timeout
        return _FakeResponse(status_code=200, payload={"data": {"kline": []}})

    monkeypatch.setattr("frontline_data_pack.providers_eastmoney._http_get", _fake_http_get)

    spec = _spec(provider="eastmoney_direct", endpoint="push2his_kline")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_eastmoney_push2his_kline},
    )

    assert result.attempt.status == "schema_invalid"
    assert result.attempt.error_code == PROVIDER_SCHEMA_INVALID


def test_t_pvd_005_empty_klines_maps_to_empty_with_zero_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get(url: str, *, params: dict[str, str], timeout: float) -> object:
        _ = url, params, timeout
        return _FakeResponse(status_code=200, payload={"data": {"klines": []}})

    monkeypatch.setattr("frontline_data_pack.providers_eastmoney._http_get", _fake_http_get)

    spec = _spec(provider="eastmoney_direct", endpoint="push2his_kline")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_eastmoney_push2his_kline},
    )

    assert result.attempt.status == "empty"
    assert result.attempt.accepted_count == 0
    assert result.normalized_rows == []


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FixedCallContext:
    def __init__(self, timeout_ms: int) -> None:
        self._timeout_ms = timeout_ms
        self.cancel_checks = 0

    def remaining_timeout_ms(self) -> int:
        return self._timeout_ms

    def raise_if_cancelled(self) -> None:
        self.cancel_checks += 1


def _fixed_call_context(*, timeout_ms: int) -> _FixedCallContext:
    return _FixedCallContext(timeout_ms)


def _spec(
    *,
    provider: str,
    endpoint: str,
    timeout_ms: int = 1000,
    mode: str = "remote",
) -> ProviderSpec:
    return ProviderSpec(
        domain="market",
        priority="P0",
        provider=provider,
        endpoint=endpoint,
        role="p0_price_history_backup",
        enabled=True,
        mode=mode,  # type: ignore[arg-type]
        timeout_ms=timeout_ms,
        required_for_complete=False,
        query_parameters=[
            ProviderQueryParameter(
                name="secid",
                source="ticker_secid",
                required=True,
                fixed_value=None,
            )
        ],
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-04-01",
        end_date="2026-05-08",
        adjust="qfq",
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-pvd-005",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-pvd-005",
        dispatch_id="dispatch-pvd-005",
        tool_name="market_market_data_pack",
        evidence_root="viking://resources/workflow/run-pvd-005/frontline/market_analyst/call-pvd-005/evidence",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )
