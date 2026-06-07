from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chart_engine import ChartInputInsufficientHistoryError, render_market_charts

CHART_ENGINE_PATH = SCRIPTS_DIR / "chart_engine.py"


def _load_chart_engine_with_blocked_modules(monkeypatch: pytest.MonkeyPatch, *, blocked: list[str]):
    module_name = "_test_chart_engine_with_blocked_modules"
    for module in blocked:
        monkeypatch.setitem(sys.modules, module, None)
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, CHART_ENGINE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_chart_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, date_value in enumerate(pd.date_range("2026-02-01", periods=40, freq="D")):
        close = 20.0 + day * 0.6
        macd = 0.1 + day * 0.03
        signal = 0.08 + day * 0.025
        rows.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "open": close - 0.3,
                "high": close + 0.5,
                "low": close - 0.7,
                "close": close,
                "volume": 1_000_000 + day * 25_000,
                "ma5": close - 0.5,
                "ma10": close - 1.0,
                "ma20": close - 2.0,
                "macd": macd,
                "signal": signal,
                "hist": macd - signal,
                "rsi14": 45.0 + day * 0.8,
                "boll_upper": close + 1.2,
                "boll_mid": close,
                "boll_lower": close - 1.2,
                "atr14": 0.4 + day * 0.01,
                "k": 55.0 + day * 0.6,
                "d": 50.0 + day * 0.5,
                "j": 65.0 + day * 0.8,
            }
        )
    return pd.DataFrame(rows)


def test_chart_engine_writes_market_and_indicator_pngs(sample_chart_frame: pd.DataFrame, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("mplfinance")

    chart_paths = render_market_charts(sample_chart_frame, ticker="600015", output_dir=tmp_path)

    assert chart_paths == (
        tmp_path / "600015_market_structure.png",
        tmp_path / "600015_indicator_panels.png",
    )
    for chart_path in chart_paths:
        assert chart_path.exists()
        assert chart_path.suffix == ".png"
        assert chart_path.stat().st_size > 0


def test_chart_engine_module_import_does_not_crash_when_plotting_dependencies_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_chart_engine_with_blocked_modules(monkeypatch, blocked=["matplotlib", "mplfinance"])
    assert callable(module.render_market_charts)


def test_chart_engine_missing_dependencies_emit_machine_readable_unavailable_reason(
    monkeypatch: pytest.MonkeyPatch, sample_chart_frame: pd.DataFrame, tmp_path: Path
) -> None:
    module = _load_chart_engine_with_blocked_modules(monkeypatch, blocked=["matplotlib", "mplfinance"])

    with pytest.raises(RuntimeError, match=r"^chart_runtime_unavailable: matplotlib,mplfinance$"):
        module.render_market_charts(sample_chart_frame, ticker="600015", output_dir=tmp_path)


def test_chart_engine_rejects_all_nan_indicator_series_before_backend_plot(sample_chart_frame: pd.DataFrame, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("mplfinance")
    short_frame = sample_chart_frame.head(8).copy()
    for column in (
        "ma10",
        "ma20",
        "macd",
        "signal",
        "hist",
        "rsi14",
        "boll_upper",
        "boll_mid",
        "boll_lower",
        "atr14",
        "k",
        "d",
        "j",
    ):
        short_frame[column] = pd.NA

    with pytest.raises(ChartInputInsufficientHistoryError, match="chart_input_insufficient_history"):
        render_market_charts(short_frame, ticker="600015", output_dir=tmp_path)
