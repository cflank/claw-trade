from __future__ import annotations

from pathlib import Path

import pandas as pd


class ChartRuntimeUnavailableError(RuntimeError):
    def __init__(self, missing_dependencies: list[str]):
        self.missing_dependencies = sorted(set(missing_dependencies))
        super().__init__(f"chart_runtime_unavailable: {','.join(self.missing_dependencies)}")


class ChartInputInsufficientHistoryError(RuntimeError):
    def __init__(self, missing_series: list[str]):
        self.missing_series = sorted(set(missing_series))
        super().__init__(f"chart_input_insufficient_history: {','.join(self.missing_series)}")


_INDICATOR_CHART_COLUMNS = (
    "ma5",
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
)


def _load_plotting_dependencies():
    missing: list[str] = []
    matplotlib = None
    mpf = None

    try:
        import matplotlib as matplotlib_module

        matplotlib = matplotlib_module
    except ModuleNotFoundError:
        missing.append("matplotlib")

    try:
        import mplfinance as mplfinance_module

        mpf = mplfinance_module
    except ModuleNotFoundError:
        missing.append("mplfinance")

    if missing:
        raise ChartRuntimeUnavailableError(missing)

    assert matplotlib is not None
    assert mpf is not None
    matplotlib.use("Agg")
    import matplotlib.pyplot as pyplot

    return mpf, pyplot


def _prepare_chart_frame(chart_frame: pd.DataFrame) -> pd.DataFrame:
    prepared = chart_frame.copy()
    prepared.columns = [str(column).lower() for column in prepared.columns]

    required = {
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ma5",
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
    }
    missing = sorted(required - set(prepared.columns))
    if missing:
        raise ValueError(f"chart frame missing required columns: {missing}")

    prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce")
    prepared = prepared.dropna(subset=["date"]).sort_values("date").copy()
    for column in ("open", "high", "low", "close", "volume", *_INDICATOR_CHART_COLUMNS):
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if prepared.empty:
        raise ValueError("chart frame has no valid OHLCV rows")
    missing_indicator_series = [column for column in _INDICATOR_CHART_COLUMNS if not prepared[column].notna().any()]
    if missing_indicator_series:
        raise ChartInputInsufficientHistoryError(missing_indicator_series)
    prepared = prepared.set_index("date")
    return prepared


def _render_market_structure_chart(prepared: pd.DataFrame, *, ticker: str, output_dir: Path, mpf) -> Path:
    chart_path = output_dir / f"{ticker}_market_structure.png"
    addplots = [
        mpf.make_addplot(prepared["ma5"], panel=0, color="#1f77b4", width=1.0),
        mpf.make_addplot(prepared["ma10"], panel=0, color="#ff7f0e", width=1.0),
        mpf.make_addplot(prepared["ma20"], panel=0, color="#2ca02c", width=1.0),
        mpf.make_addplot(prepared["boll_upper"], panel=0, color="#8b5cf6", width=0.9, linestyle="dashdot"),
        mpf.make_addplot(prepared["boll_mid"], panel=0, color="#64748b", width=0.9, linestyle="dashed"),
        mpf.make_addplot(prepared["boll_lower"], panel=0, color="#8b5cf6", width=0.9, linestyle="dashdot"),
        mpf.make_addplot(prepared["macd"], panel=1, color="#1f77b4", ylabel="MACD"),
        mpf.make_addplot(prepared["signal"], panel=1, color="#ff7f0e"),
        mpf.make_addplot(prepared["hist"], panel=1, type="bar", color="#6baed6", alpha=0.6),
        mpf.make_addplot(prepared["rsi14"], panel=2, color="#dc2626", ylabel="RSI"),
    ]
    mpf.plot(
        prepared,
        type="candle",
        style="yahoo",
        addplot=addplots,
        volume=True,
        volume_panel=3,
        num_panels=4,
        panel_ratios=(5, 2, 2, 2),
        figscale=1.2,
        figratio=(16, 10),
        tight_layout=True,
        title=f"{ticker} Market Structure",
        ylabel="Price",
        ylabel_lower="Volume",
        savefig={"fname": str(chart_path), "dpi": 144, "bbox_inches": "tight"},
    )
    return chart_path


def _render_indicator_panels_chart(prepared: pd.DataFrame, *, ticker: str, output_dir: Path, pyplot) -> Path:
    chart_path = output_dir / f"{ticker}_indicator_panels.png"
    figure, axes = pyplot.subplots(3, 1, figsize=(16, 10), sharex=True)
    boll_axis, atr_axis, kdj_axis = axes

    boll_axis.plot(prepared.index, prepared["close"], color="#111827", linewidth=1.2, label="Close")
    boll_axis.plot(prepared.index, prepared["boll_upper"], color="#8b5cf6", linewidth=1.0, label="BOLL Upper")
    boll_axis.plot(prepared.index, prepared["boll_mid"], color="#64748b", linewidth=1.0, label="BOLL Mid")
    boll_axis.plot(prepared.index, prepared["boll_lower"], color="#8b5cf6", linewidth=1.0, label="BOLL Lower")
    boll_axis.set_title(f"{ticker} Indicator Panels")
    boll_axis.set_ylabel("Price / BOLL")
    boll_axis.legend(loc="upper left", ncol=4, fontsize=8)
    boll_axis.grid(alpha=0.25)

    atr_axis.plot(prepared.index, prepared["atr14"], color="#ea580c", linewidth=1.2, label="ATR14")
    atr_axis.set_ylabel("ATR")
    atr_axis.legend(loc="upper left", fontsize=8)
    atr_axis.grid(alpha=0.25)

    kdj_axis.plot(prepared.index, prepared["k"], color="#2563eb", linewidth=1.1, label="K")
    kdj_axis.plot(prepared.index, prepared["d"], color="#16a34a", linewidth=1.1, label="D")
    kdj_axis.plot(prepared.index, prepared["j"], color="#dc2626", linewidth=1.1, label="J")
    kdj_axis.axhline(80, color="#9ca3af", linewidth=0.8, linestyle="--")
    kdj_axis.axhline(20, color="#9ca3af", linewidth=0.8, linestyle="--")
    kdj_axis.set_ylabel("KDJ")
    kdj_axis.legend(loc="upper left", ncol=3, fontsize=8)
    kdj_axis.grid(alpha=0.25)

    figure.tight_layout()
    figure.savefig(chart_path, dpi=144, bbox_inches="tight")
    pyplot.close(figure)
    return chart_path


def render_market_charts(chart_frame: pd.DataFrame, *, ticker: str, output_dir: Path) -> tuple[Path, Path]:
    mpf, pyplot = _load_plotting_dependencies()
    prepared = _prepare_chart_frame(chart_frame)
    output_dir.mkdir(parents=True, exist_ok=True)
    market_structure = _render_market_structure_chart(prepared, ticker=ticker, output_dir=output_dir, mpf=mpf)
    indicator_panels = _render_indicator_panels_chart(prepared, ticker=ticker, output_dir=output_dir, pyplot=pyplot)
    return (market_structure, indicator_panels)
