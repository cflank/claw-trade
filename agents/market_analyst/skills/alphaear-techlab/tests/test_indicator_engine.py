from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from indicator_engine import AnalysisBundle, analyze_market_frame

INDICATOR_ENGINE_PATH = SCRIPTS_DIR / "indicator_engine.py"


def _load_indicator_engine_without_pandas_ta(monkeypatch: pytest.MonkeyPatch):
    module_name = "_test_indicator_engine_without_pandas_ta"
    monkeypatch.setitem(sys.modules, "pandas_ta", None)
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, INDICATOR_ENGINE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_ohlcv_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, date_value in enumerate(pd.date_range("2026-02-01", periods=40, freq="D")):
        close = 20.0 + day * 0.6
        rows.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "open": close - 0.3,
                "high": close + 0.5,
                "low": close - 0.7,
                "close": close,
                "volume": 1_000_000 + day * 25_000,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def short_ohlcv_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, date_value in enumerate(pd.date_range("2026-03-01", periods=8, freq="D")):
        close = 10.0 + day * 0.2
        rows.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.3,
                "close": close,
                "volume": 200_000 + day * 5_000,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def macd_boundary_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, date_value in enumerate(pd.date_range("2026-01-01", periods=30, freq="D")):
        close = 15.0 + day * 0.35
        rows.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "open": close - 0.15,
                "high": close + 0.4,
                "low": close - 0.45,
                "close": close,
                "volume": 500_000 + day * 12_500,
            }
        )
    return pd.DataFrame(rows)


def test_indicator_engine_returns_expected_indicator_groups(sample_ohlcv_frame: pd.DataFrame) -> None:
    result = analyze_market_frame(sample_ohlcv_frame, ticker="600015")

    assert isinstance(result, AnalysisBundle)
    assert set(result.indicators) == {"ma", "macd", "rsi", "boll", "atr", "kdj"}
    assert result.summary.get("indicator_backend") == "pandas_ta"
    assert result.summary["trend"] == "uptrend"
    assert result.summary["volume_state"] == "expanding"
    assert all(item.get("type") != "indicator_backend_degraded" for item in result.warnings)
    assert {"open", "high", "low", "close", "volume", "ma5", "ma10", "ma20", "macd", "signal", "hist", "rsi14"} <= set(
        result.chart_frame.columns
    )
    assert result.indicators["ma"]["ma5"] > result.indicators["ma"]["ma10"] > result.indicators["ma"]["ma20"]


def test_indicator_engine_uses_pandas_ta_backend_when_available(sample_ohlcv_frame: pd.DataFrame) -> None:
    pytest.importorskip("pandas_ta")

    result = analyze_market_frame(sample_ohlcv_frame, ticker="600015")

    assert result.summary.get("indicator_backend") == "pandas_ta"
    assert all(item.get("type") != "indicator_backend_degraded" for item in result.warnings)


def test_indicator_engine_handles_short_series_without_crashing(short_ohlcv_frame: pd.DataFrame) -> None:
    result = analyze_market_frame(short_ohlcv_frame, ticker="600015")

    assert isinstance(result, AnalysisBundle)
    assert set(result.indicators) == {"ma", "macd", "rsi", "boll", "atr", "kdj"}
    assert any(item["type"] == "insufficient_history" for item in result.warnings)
    assert result.summary["trend"] == "uptrend"


def test_indicator_engine_warns_when_latest_emitted_indicator_set_is_incomplete(macd_boundary_frame: pd.DataFrame) -> None:
    result = analyze_market_frame(macd_boundary_frame, ticker="600015")

    assert any(item["type"] == "insufficient_history" for item in result.warnings)
    assert any(value is None for value in result.indicators["macd"].values())


def test_indicator_engine_requires_pandas_ta_when_missing(
    monkeypatch: pytest.MonkeyPatch, sample_ohlcv_frame: pd.DataFrame
) -> None:
    module = _load_indicator_engine_without_pandas_ta(monkeypatch)

    with pytest.raises(RuntimeError, match="indicator_backend_unavailable:pandas_ta"):
        module.analyze_market_frame(sample_ohlcv_frame, ticker="600015")
