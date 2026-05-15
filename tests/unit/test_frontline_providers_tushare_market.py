from __future__ import annotations

import sys
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
SRC_ROOT = REPO_ROOT / "src"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claw_trade.providers.tushare_client import TushareClientConfigError  # noqa: E402
from frontline_data_pack.models import ProviderQuery, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import ProviderCallContext, execute_provider_attempt  # noqa: E402
from frontline_data_pack.providers_tushare_market import call_tushare_hk_daily_adj, call_tushare_pro_bar  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("d" * 64)


def test_tushare_pro_bar_maps_ohlcv_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pro = object()

    class _FakeTushareModule:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def pro_bar(self, **kwargs: object) -> pd.DataFrame:
            self.calls.append(dict(kwargs))
            return pd.DataFrame(
                [
                    {
                        "trade_date": "20260508",
                        "open": 100.0,
                        "close": 102.0,
                        "high": 103.0,
                        "low": 99.0,
                        "vol": 12345,
                        "amount": 67890.0,
                    }
                ]
            )

    fake_tushare = _FakeTushareModule()
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_market.create_tushare_pro",
        lambda: fake_pro,
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_market._load_tushare_module",
        lambda: fake_tushare,
    )

    spec = _spec()
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_tushare_pro_bar},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert fake_tushare.calls[0]["api"] is fake_pro
    assert fake_tushare.calls[0]["ts_code"] == "600519.SH"
    assert fake_tushare.calls[0]["start_date"] == "20260501"
    assert fake_tushare.calls[0]["end_date"] == "20260509"
    assert result.normalized_rows[0]["trade_date"] == "20260508"


def test_tushare_pro_bar_missing_token_is_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_market.create_tushare_pro",
        lambda: (_ for _ in ()).throw(TushareClientConfigError("TUSHARE_TOKEN missing")),
    )

    spec = _spec()
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_tushare_pro_bar},
    )
    assert result.attempt.status == "error"
    assert result.attempt.error_code == "PROVIDER_KEY_MISSING"


def test_tushare_hk_daily_adj_maps_adjusted_ohlcv_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePro:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def hk_daily_adj(self, **kwargs: object) -> pd.DataFrame:
            self.calls.append(dict(kwargs))
            return pd.DataFrame(
                [
                    {
                        "trade_date": "20260508",
                        "open": 100.0,
                        "close": 102.0,
                        "high": 103.0,
                        "low": 99.0,
                        "pre_close": 98.0,
                        "change": 4.0,
                        "pct_change": 4.08,
                        "vol": 12345,
                        "amount": 67890.0,
                        "adj_factor": 0.5,
                    }
                ]
            )

    fake_pro = _FakePro()
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_market.create_tushare_pro",
        lambda: fake_pro,
    )

    spec = _spec(endpoint="hk_daily_adj")
    result = execute_provider_attempt(
        spec,
        _hk_query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_tushare_hk_daily_adj},
    )

    assert result.attempt.status == "success"
    assert fake_pro.calls[0]["ts_code"] == "00700.HK"
    assert result.normalized_rows[0]["open"] == 50.0
    assert result.normalized_rows[0]["close"] == 51.0
    assert result.normalized_rows[0]["pre_close"] == 49.0
    assert result.normalized_rows[0]["change"] == 2.0


def test_tushare_pro_bar_stdout_pollution_is_isolated(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class _FakeTushareModule:
        def pro_bar(self, **kwargs: object) -> pd.DataFrame:
            _ = kwargs
            print("HTTPConnectionPool(host='api.tushare.pro', port=443): token=super_secret_token")
            return pd.DataFrame(
                [
                    {
                        "trade_date": "20260508",
                        "open": 100.0,
                        "close": 102.0,
                        "high": 103.0,
                        "low": 99.0,
                        "vol": 12345,
                        "amount": 67890.0,
                    }
                ]
            )

    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_market.create_tushare_pro",
        lambda: object(),
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_market._load_tushare_module",
        lambda: _FakeTushareModule(),
    )

    payload = call_tushare_pro_bar(
        _spec(),
        _query(),
        _context(),
        ProviderCallContext(
            deadline_monotonic=time.monotonic() + 1,
            cancel_event=threading.Event(),
        ),
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    rows = payload.get("rows")
    assert isinstance(rows, list)
    assert len(rows) == 1
    diagnostics = payload.get("diagnostics")
    assert isinstance(diagnostics, dict)
    stdout_pollution = diagnostics.get("stdout_pollution")
    assert isinstance(stdout_pollution, dict)
    summary = stdout_pollution.get("summary")
    assert isinstance(summary, str)
    assert "super_secret_token" not in summary


def _spec(endpoint: str = "pro_bar") -> ProviderSpec:
    return ProviderSpec(
        domain="market",
        priority="P0",
        provider="tushare",
        endpoint=endpoint,
        role="p0_price_history",
        enabled=True,
        mode="remote",
        timeout_ms=1000,
        required_for_complete=True,
        query_parameters=[],
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-09",
        adjust="qfq",
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _hk_query() -> ProviderQuery:
    return ProviderQuery(
        market="HK",
        ticker="00700.HK",
        company_name="腾讯控股",
        industry="互联网",
        start_date="2026-05-01",
        end_date="2026-05-09",
        adjust="qfq",
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-tushare-market",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-tushare-market",
        dispatch_id="dispatch-tushare-market",
        tool_name="market_market_data_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
        current_date="2026-05-09",
    )
