from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.models import ProviderQuery, ProviderQueryParameter, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import (  # noqa: E402
    PROVIDER_SCHEMA_INVALID,
    execute_provider_attempt,
)
from frontline_data_pack.providers_akshare_market import (  # noqa: E402
    call_akshare_sina_daily,
    call_akshare_stock_zh_a_hist,
    call_akshare_tencent_hist_tx,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("e" * 64)


def test_t_pvd_004_stock_zh_a_hist_success_rows_are_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _stock_zh_a_hist(**kwargs: object) -> pd.DataFrame:
        captured.update(kwargs)
        return pd.DataFrame(
            [
                {
                    "日期": "2026-05-07",
                    "开盘": 1600.1,
                    "收盘": 1610.2,
                    "最高": 1620.3,
                    "最低": 1590.4,
                    "成交量": 1234567,
                    "成交额": 7890000.5,
                }
            ]
        )

    fake_akshare = SimpleNamespace(stock_zh_a_hist=_stock_zh_a_hist)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_market._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _spec(provider="akshare", endpoint="stock_zh_a_hist")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_zh_a_hist},
    )

    assert result.attempt.status == "success"
    assert result.attempt.raw_count > 0
    assert result.attempt.accepted_count > 0
    assert result.normalized_rows[0]["trade_date"] == "2026-05-07"
    assert result.normalized_rows[0]["open"] == 1600.1
    assert result.normalized_rows[0]["close"] == 1610.2
    assert result.normalized_rows[0]["high"] == 1620.3
    assert result.normalized_rows[0]["low"] == 1590.4
    assert result.normalized_rows[0]["volume"] == 1234567
    assert result.normalized_rows[0]["amount"] == 7890000.5
    assert result.normalized_rows[0]["adjust"] == "qfq"
    assert captured["symbol"] == "600519"
    assert captured["start_date"] == "20260401"
    assert captured["end_date"] == "20260508"
    assert captured["period"] == "daily"
    assert captured["adjust"] == "qfq"
    assert isinstance(captured["timeout"], float)
    assert captured["timeout"] > 0.0


def test_t_pvd_004_missing_close_column_returns_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_akshare = SimpleNamespace(
        stock_zh_a_hist=lambda **kwargs: pd.DataFrame(
            [
                {
                    "日期": "2026-05-07",
                    "开盘": 1600.1,
                    "最高": 1620.3,
                    "最低": 1590.4,
                    "成交量": 1234567,
                    "成交额": 7890000.5,
                }
            ]
        )
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_market._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _spec(provider="akshare", endpoint="stock_zh_a_hist")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_zh_a_hist},
    )

    assert result.attempt.status == "schema_invalid"
    assert result.attempt.error_code == PROVIDER_SCHEMA_INVALID
    assert result.attempt.raw_count == 1
    assert result.normalized_rows == []


def test_t_pvd_004_empty_dataframe_returns_empty_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_akshare = SimpleNamespace(
        stock_zh_a_hist=lambda **kwargs: pd.DataFrame(
            columns=["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]
        )
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_market._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _spec(provider="akshare", endpoint="stock_zh_a_hist")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_zh_a_hist},
    )

    assert result.attempt.status == "empty"
    assert result.attempt.accepted_count == 0
    assert result.normalized_rows == []


def test_t_pvd_004_akshare_exception_returns_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs: object) -> pd.DataFrame:
        _ = kwargs
        raise RuntimeError("token=secrettokenvalue1234567890 https://user:pass@example.com/path?token=abc123")

    fake_akshare = SimpleNamespace(stock_zh_a_hist=_raise)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_market._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _spec(provider="akshare", endpoint="stock_zh_a_hist")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_zh_a_hist},
    )

    assert result.attempt.status == "error"
    assert result.attempt.error_message_redacted is not None
    assert "***" in result.attempt.error_message_redacted
    assert "secrettokenvalue1234567890" not in result.attempt.error_message_redacted
    assert "user:pass" not in result.attempt.error_message_redacted


def test_t_pvd_004_hist_and_hist_tx_pass_remaining_timeout_to_akshare(monkeypatch: pytest.MonkeyPatch) -> None:
    hist_calls: list[dict[str, object]] = []
    tx_calls: list[dict[str, object]] = []

    def _hist(**kwargs: object) -> pd.DataFrame:
        hist_calls.append(dict(kwargs))
        return _rows_dataframe()

    def _tx(**kwargs: object) -> pd.DataFrame:
        tx_calls.append(dict(kwargs))
        return _rows_dataframe()

    fake_akshare = SimpleNamespace(
        stock_zh_a_hist=_hist,
        stock_zh_a_hist_tx=_tx,
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_market._load_akshare_module",
        lambda: fake_akshare,
    )

    call_context = _fixed_call_context(timeout_ms=4321)
    call_akshare_stock_zh_a_hist(_spec(provider="akshare", endpoint="stock_zh_a_hist"), _query(), _context(), call_context)
    call_akshare_tencent_hist_tx(
        _spec(provider="tencent", endpoint="stock_zh_a_hist_tx"),
        _query(),
        _context(),
        call_context,
    )

    assert math.isclose(float(hist_calls[0]["timeout"]), 4.321, rel_tol=0.0, abs_tol=1e-9)
    assert math.isclose(float(tx_calls[0]["timeout"]), 4.321, rel_tol=0.0, abs_tol=1e-9)
    assert hist_calls[0]["symbol"] == "600519"
    assert tx_calls[0]["symbol"] == "sh600519"


def test_t_pvd_004_sina_daily_uses_remaining_timeout_for_underlying_http(monkeypatch: pytest.MonkeyPatch) -> None:
    request_timeouts: list[float] = []
    captured_call: dict[str, str] = {}

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> object:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return SimpleNamespace(text="")

    def _fake_stock_zh_a_daily(
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str,
    ) -> pd.DataFrame:
        captured_call.update(
            {
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "adjust": adjust,
            }
        )
        requests.get("https://example.com/a")
        requests.get("https://example.com/b", timeout=99.0)
        return _rows_dataframe()

    _fake_stock_zh_a_daily.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(stock_zh_a_daily=_fake_stock_zh_a_daily)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_market._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_sina_daily(
        _spec(provider="sina", endpoint="stock_zh_a_daily"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert captured_call["symbol"] == "sh600519"
    assert captured_call["start_date"] == "20260401"
    assert captured_call["end_date"] == "20260508"
    assert captured_call["adjust"] == "qfq"
    assert request_timeouts
    assert all(math.isclose(value, 2.5, rel_tol=0.0, abs_tol=1e-9) for value in request_timeouts)


def _rows_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-05-07",
                "open": 1600.1,
                "close": 1610.2,
                "high": 1620.3,
                "low": 1590.4,
                "volume": 1234567,
                "amount": 7890000.5,
            }
        ]
    )


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
) -> ProviderSpec:
    return ProviderSpec(
        domain="market",
        priority="P0",
        provider=provider,
        endpoint=endpoint,
        role="p0_role",
        enabled=True,
        mode="remote",
        timeout_ms=timeout_ms,
        required_for_complete=True,
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="ticker_code_6",
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
        run_id="run-pvd-004",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-pvd-004",
        dispatch_id="dispatch-pvd-004",
        tool_name="market_market_data_pack",
        evidence_root="viking://resources/workflow/run-pvd-004/frontline/market_analyst/call-pvd-004/evidence",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )
