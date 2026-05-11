from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
WORKSPACE_ROOT = SKILL_ROOT.parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from indicator_engine import AnalysisBundle
import market_data_provider
import techlab_entrypoint


def test_techlab_entrypoint_analyze_returns_indicator_payload(monkeypatch, capsys, tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-04-10", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3, "volume": 1200},
            {"date": "2026-04-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 1800},
        ]
    )
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_load_price_frame(*, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        calls.append(("provider", {"ticker": ticker, "start_date": start_date, "end_date": end_date}))
        return frame

    def fake_analyze_market_frame(input_frame: pd.DataFrame, *, ticker: str) -> AnalysisBundle:
        calls.append(("engine", {"ticker": ticker}))
        assert input_frame.equals(frame)
        return AnalysisBundle(
            summary={"trend": "uptrend", "volume_state": "expanding", "indicator_backend": "pandas_ta"},
            indicators={
                "ma": {"ma5": 10.5, "ma10": 10.1, "ma20": 9.8},
                "macd": {"macd": 0.2, "signal": 0.1, "hist": 0.1},
                "rsi": {"rsi14": 63.4},
                "boll": {"upper": 10.9, "mid": 10.2, "lower": 9.5},
                "atr": {"atr14": 0.4},
                "kdj": {"k": 70.0, "d": 66.0, "j": 78.0},
            },
            warnings=[],
            chart_frame=input_frame,
        )

    def fake_render_market_charts(chart_frame: pd.DataFrame, *, ticker: str, output_dir: Path) -> tuple[Path, Path]:
        calls.append(("chart", {"ticker": ticker, "output_dir": str(output_dir)}))
        assert chart_frame.equals(frame)
        return (
            output_dir / f"{ticker}_market_structure.png",
            output_dir / f"{ticker}_indicator_panels.png",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", fake_load_price_frame)
    monkeypatch.setattr(techlab_entrypoint, "analyze_market_frame", fake_analyze_market_frame)
    monkeypatch.setattr(techlab_entrypoint, "render_market_charts", fake_render_market_charts)

    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert calls == [
        ("provider", {"ticker": "600010", "start_date": "2026-01-01", "end_date": "2026-04-13"}),
        ("engine", {"ticker": "600010"}),
        ("chart", {"ticker": "600010", "output_dir": str(WORKSPACE_ROOT / "techlab-out")}),
    ]
    assert payload == {
        "ok": True,
        "ticker": "600010",
        "summary": {"trend": "uptrend", "volume_state": "expanding", "indicator_backend": "pandas_ta"},
        "indicators": {
            "ma": {"ma5": 10.5, "ma10": 10.1, "ma20": 9.8},
            "macd": {"macd": 0.2, "signal": 0.1, "hist": 0.1},
            "rsi": {"rsi14": 63.4},
            "boll": {"upper": 10.9, "mid": 10.2, "lower": 9.5},
            "atr": {"atr14": 0.4},
            "kdj": {"k": 70.0, "d": 66.0, "j": 78.0},
        },
        "chart_files": [
            "techlab-out/600010_market_structure.png",
            "techlab-out/600010_indicator_panels.png",
        ],
        "warnings": [],
    }


def test_techlab_entrypoint_market_pack_returns_price_rows_and_technical_payload(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-04-10",
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.3,
                "volume": 1200,
                "change_pct": 0.0,
            },
            {
                "date": "2026-04-11",
                "open": 10.2,
                "high": 10.8,
                "low": 10.1,
                "close": 10.7,
                "volume": 1800,
                "change_pct": 3.8835,
            },
        ]
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", lambda **_: frame)
    monkeypatch.setattr(
        techlab_entrypoint,
        "analyze_market_frame",
        lambda input_frame, *, ticker: AnalysisBundle(
            summary={"trend": "uptrend", "volume_state": "expanding", "indicator_backend": "pandas_ta"},
            indicators={
                "ma": {"ma5": 10.5, "ma10": 10.1, "ma20": 9.8},
                "macd": {"macd": 0.2, "signal": 0.1, "hist": 0.1},
                "rsi": {"rsi14": 63.4},
                "boll": {"upper": 10.9, "mid": 10.2, "lower": 9.5},
                "atr": {"atr14": 0.4},
                "kdj": {"k": 70.0, "d": 66.0, "j": 78.0},
            },
            warnings=[],
            chart_frame=input_frame,
        ),
    )
    monkeypatch.setattr(
        techlab_entrypoint,
        "render_market_charts",
        lambda chart_frame, *, ticker, output_dir: (
            output_dir / f"{ticker}_market_structure.png",
            output_dir / f"{ticker}_indicator_panels.png",
        ),
    )

    exit_code = techlab_entrypoint.main(
        [
            "market-pack",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["ticker"] == "600010"
    assert payload["market_data"] == {
        "ticker": "600010",
        "price_row_count": 2,
        "requested_price_row_count": 2,
        "recent_price_rows": frame.to_dict(orient="records"),
        "recent_price_row_count": 2,
        "price_range": {"start_date": "2026-04-10", "end_date": "2026-04-11"},
        "requested_price_range": {"start_date": "2026-01-01", "end_date": "2026-04-13"},
        "recent_price_range": {"start_date": "2026-04-10", "end_date": "2026-04-11"},
        "indicator_warmup": {
            "enabled": True,
            "reason": "technical indicators and chart panels need enough prior price history",
        },
    }
    assert payload["indicators"]["macd"] == {"macd": 0.2, "signal": 0.1, "hist": 0.1}
    assert payload["chart_files"] == [
        "techlab-out/600010_market_structure.png",
        "techlab-out/600010_indicator_panels.png",
    ]


def test_entrypoint_fails_when_chart_runtime_dependencies_missing(monkeypatch, capsys) -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-04-10", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3, "volume": 1200},
            {"date": "2026-04-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 1800},
        ]
    )

    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", lambda **_: frame)
    monkeypatch.setattr(
        techlab_entrypoint,
        "analyze_market_frame",
        lambda input_frame, *, ticker: AnalysisBundle(
            summary={"trend": "uptrend", "volume_state": "expanding", "indicator_backend": "pandas_ta"},
            indicators={
                "ma": {"ma5": 10.5, "ma10": 10.1, "ma20": 9.8},
                "macd": {"macd": 0.2, "signal": 0.1, "hist": 0.1},
                "rsi": {"rsi14": 63.4},
                "boll": {"upper": 10.9, "mid": 10.2, "lower": 9.5},
                "atr": {"atr14": 0.4},
                "kdj": {"k": 70.0, "d": 66.0, "j": 78.0},
            },
            warnings=[],
            chart_frame=input_frame,
        ),
    )

    def raise_render_error(chart_frame: pd.DataFrame, *, ticker: str, output_dir: Path) -> tuple[Path, Path]:
        assert chart_frame.equals(frame)
        assert ticker == "600010"
        assert output_dir == WORKSPACE_ROOT / "techlab-out"
        raise techlab_entrypoint.ChartRuntimeUnavailableError(["matplotlib", "mplfinance"])

    monkeypatch.setattr(techlab_entrypoint, "render_market_charts", raise_render_error)

    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload == {
        "ok": False,
        "error": {
            "type": "chart_runtime_unavailable",
            "message": "chart_runtime_unavailable: matplotlib,mplfinance",
        },
    }


def test_entrypoint_fails_when_chart_render_fails(monkeypatch, capsys) -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-04-10", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3, "volume": 1200},
            {"date": "2026-04-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 1800},
        ]
    )

    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", lambda **_: frame)
    monkeypatch.setattr(
        techlab_entrypoint,
        "analyze_market_frame",
        lambda input_frame, *, ticker: AnalysisBundle(
            summary={"trend": "uptrend", "volume_state": "expanding", "indicator_backend": "pandas_ta"},
            indicators={
                "ma": {"ma5": 10.5, "ma10": 10.1, "ma20": 9.8},
                "macd": {"macd": 0.2, "signal": 0.1, "hist": 0.1},
                "rsi": {"rsi14": 63.4},
                "boll": {"upper": 10.9, "mid": 10.2, "lower": 9.5},
                "atr": {"atr14": 0.4},
                "kdj": {"k": 70.0, "d": 66.0, "j": 78.0},
            },
            warnings=[],
            chart_frame=input_frame,
        ),
    )

    def raise_render_error(chart_frame: pd.DataFrame, *, ticker: str, output_dir: Path) -> tuple[Path, Path]:
        assert chart_frame.equals(frame)
        assert ticker == "600010"
        assert output_dir == WORKSPACE_ROOT / "techlab-out"
        raise ValueError("plot backend crashed")

    monkeypatch.setattr(techlab_entrypoint, "render_market_charts", raise_render_error)

    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload == {
        "ok": False,
        "error": {
            "type": "chart_render_failed",
            "message": "plot backend crashed",
        },
    }


def test_entrypoint_reports_insufficient_chart_history_without_backend_crash(monkeypatch, capsys) -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-04-10", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3, "volume": 1200},
            {"date": "2026-04-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 1800},
        ]
    )

    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", lambda **_: frame)
    monkeypatch.setattr(
        techlab_entrypoint,
        "analyze_market_frame",
        lambda input_frame, *, ticker: AnalysisBundle(
            summary={"trend": "uptrend", "volume_state": "expanding", "indicator_backend": "pandas_ta"},
            indicators={
                "ma": {"ma5": 10.5, "ma10": None, "ma20": None},
                "macd": {"macd": None, "signal": None, "hist": None},
                "rsi": {"rsi14": None},
                "boll": {"upper": None, "mid": None, "lower": None},
                "atr": {"atr14": None},
                "kdj": {"k": None, "d": None, "j": None},
            },
            warnings=[{"type": "insufficient_history", "message": "not enough rows"}],
            chart_frame=input_frame,
        ),
    )

    def raise_render_error(chart_frame: pd.DataFrame, *, ticker: str, output_dir: Path) -> tuple[Path, Path]:
        assert chart_frame.equals(frame)
        assert ticker == "600010"
        assert output_dir == WORKSPACE_ROOT / "techlab-out"
        raise techlab_entrypoint.ChartInputInsufficientHistoryError(["macd", "boll_upper", "atr14", "k"])

    monkeypatch.setattr(techlab_entrypoint, "render_market_charts", raise_render_error)

    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-04-01",
            "--end-date",
            "2026-04-19",
            "--output-dir",
            "techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload == {
        "ok": False,
        "error": {
            "type": "chart_input_insufficient_history",
            "message": "chart_input_insufficient_history: atr14,boll_upper,k,macd",
        },
    }


def test_entrypoint_fails_when_indicator_backend_is_unavailable(monkeypatch, capsys) -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-04-10", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3, "volume": 1200},
            {"date": "2026-04-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 1800},
        ]
    )

    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", lambda **_: frame)

    def raise_indicator_error(input_frame: pd.DataFrame, *, ticker: str):
        assert input_frame.equals(frame)
        assert ticker == "600010"
        raise techlab_entrypoint.IndicatorRuntimeUnavailableError("indicator_backend_unavailable:pandas_ta")

    monkeypatch.setattr(techlab_entrypoint, "analyze_market_frame", raise_indicator_error)

    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload == {
        "ok": False,
        "error": {
            "type": "indicator_backend_unavailable",
            "message": "indicator_backend_unavailable:pandas_ta",
        },
    }


def test_entrypoint_returns_no_market_data_error_when_provider_returns_empty(monkeypatch, capsys) -> None:
    monkeypatch.setattr(techlab_entrypoint, "load_price_frame", lambda **_: pd.DataFrame())

    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600015",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-11",
            "--output-dir",
            "data/techlab-output",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"] == {
        "type": "no_market_data",
        "message": "no price rows available for 600015",
    }


def test_techlab_entrypoint_rejects_absolute_output_dir(capsys) -> None:
    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "/tmp/techlab-out",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "invalid_output_dir",
        "message": "output-dir must be relative",
    }


def test_techlab_entrypoint_rejects_empty_output_dir(capsys) -> None:
    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "invalid_output_dir",
        "message": "output-dir is required",
    }


def test_techlab_entrypoint_rejects_output_dir_escape(capsys) -> None:
    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
            "--output-dir",
            "../escape",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_output_dir"


def test_techlab_entrypoint_rejects_missing_required_arguments(capsys) -> None:
    exit_code = techlab_entrypoint.main(
        [
            "analyze",
            "--ticker",
            "600010",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-04-13",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"


def test_market_data_provider_uses_normal_import_seam_and_normalizes_rows(monkeypatch) -> None:
    class FakeTools:
        def get_stock_price(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
            assert ticker == "600015"
            assert start_date == "2026-01-01"
            assert end_date == "2026-01-31"
            return pd.DataFrame(
                [
                    {"DATE": "2026-01-03", "OPEN": "10.5", "HIGH": "10.8", "LOW": "10.1", "CLOSE": "10.6", "VOLUME": "1500"},
                    {"DATE": "bad-date", "OPEN": "10.0", "HIGH": "10.3", "LOW": "9.8", "CLOSE": "10.1", "VOLUME": "1200"},
                    {"DATE": "2026-01-02", "OPEN": "10.1", "HIGH": "10.6", "LOW": "9.9", "CLOSE": "10.4", "VOLUME": "1400"},
                ]
            )

    monkeypatch.setattr(
        market_data_provider,
        "_stock_entrypoint_deps",
        lambda: (lambda: "/tmp/test-signal-flux.db", lambda db_path, auto_update=False: FakeTools()),
    )

    frame = market_data_provider.load_price_frame(
        ticker="600015",
        start_date="2026-01-01",
        end_date="2026-01-31",
    )

    assert frame.to_dict(orient="records") == [
        {
            "date": "2026-01-02",
            "open": 10.1,
            "high": 10.6,
            "low": 9.9,
            "close": 10.4,
            "volume": 1400,
            "change_pct": 0.0,
        },
        {
            "date": "2026-01-03",
            "open": 10.5,
            "high": 10.8,
            "low": 10.1,
            "close": 10.6,
            "volume": 1500,
            "change_pct": pytest.approx(1.9230769231),
        },
    ]


def test_market_data_provider_loads_stock_skill_when_scripts_namespace_is_occupied(
    monkeypatch,
    tmp_path,
) -> None:
    for module_name in (
        "stock_entrypoint",
        "stock_tools",
        "database_manager",
        "scripts.database_manager",
        "scripts.stock_tools",
        "scripts.stock_entrypoint",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    conflicting_scripts_package = type(sys)("scripts")
    conflicting_scripts_package.__path__ = [str(SCRIPTS_DIR)]
    monkeypatch.setitem(sys.modules, "scripts", conflicting_scripts_package)

    _default_db_path, get_stock_tools = market_data_provider._stock_entrypoint_deps()
    tools = get_stock_tools(str(tmp_path / "signal_flux.db"), auto_update=False)

    assert _default_db_path().endswith("agents/market_analyst/skills/alphaear-stock/data/signal_flux.db")
    assert tools.__class__.__name__ == "StockTools"
