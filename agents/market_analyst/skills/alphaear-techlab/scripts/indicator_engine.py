from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

try:
    import pandas_ta as ta
except ModuleNotFoundError:
    ta = None


class IndicatorRuntimeUnavailableError(RuntimeError):
    pass


@dataclass
class AnalysisBundle:
    summary: dict[str, object]
    indicators: dict[str, object]
    warnings: list[dict[str, object]]
    chart_frame: pd.DataFrame


def _require_indicator_backend() -> None:
    if ta is None:
        raise IndicatorRuntimeUnavailableError("indicator_backend_unavailable:pandas_ta")


def _sma(series: pd.Series, *, length: int) -> pd.Series:
    return ta.sma(series, length=length)


def _macd(series: pd.Series, *, fast: int, slow: int, signal: int) -> pd.DataFrame | None:
    macd = ta.macd(series, fast=fast, slow=slow, signal=signal)
    if macd is None:
        return None
    macd_col = next(column for column in macd.columns if column.startswith("MACD_"))
    hist_col = next(column for column in macd.columns if column.startswith("MACDh_"))
    signal_col = next(column for column in macd.columns if column.startswith("MACDs_"))
    return pd.DataFrame({"macd": macd[macd_col], "signal": macd[signal_col], "hist": macd[hist_col]})


def _rsi(series: pd.Series, *, length: int) -> pd.Series:
    return ta.rsi(series, length=length)


def _bbands(series: pd.Series, *, length: int, std: float) -> pd.DataFrame | None:
    boll = ta.bbands(series, length=length, std=std)
    if boll is None:
        return None
    upper_col = next(column for column in boll.columns if column.startswith("BBU_"))
    mid_col = next(column for column in boll.columns if column.startswith("BBM_"))
    lower_col = next(column for column in boll.columns if column.startswith("BBL_"))
    return pd.DataFrame({"upper": boll[upper_col], "mid": boll[mid_col], "lower": boll[lower_col]})


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, *, length: int) -> pd.Series | None:
    return ta.atr(high, low, close, length=length)


def _stoch(high: pd.Series, low: pd.Series, close: pd.Series, *, k: int, d: int, smooth_k: int) -> pd.DataFrame | None:
    stoch = ta.stoch(high, low, close, k=k, d=d, smooth_k=smooth_k)
    if stoch is None:
        return None
    k_col = next(column for column in stoch.columns if column.startswith("STOCHk_"))
    d_col = next(column for column in stoch.columns if column.startswith("STOCHd_"))
    return pd.DataFrame({"k": stoch[k_col], "d": stoch[d_col]})


def _to_float(value: Any, *, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized.columns = [str(column).lower() for column in normalized.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(f"market frame missing required columns: {sorted(missing)}")

    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized = normalized.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if normalized.empty:
        raise ValueError("market frame has no valid OHLCV rows")
    return normalized


def _warning_list(indicators: dict[str, object], *, ticker: str, indicator_backend: str) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    if any(value is None for payload in indicators.values() for value in payload.values()):
        warnings.append(
            {
                "type": "insufficient_history",
                "message": f"{ticker} does not yet have enough history for a complete latest indicator set",
            }
        )
    return warnings


def _trend_state(latest: pd.Series, enriched: pd.DataFrame) -> str:
    ma5 = latest["ma5"]
    ma10 = latest["ma10"]
    ma20 = latest["ma20"]
    close = latest["close"]
    if not pd.isna(ma20):
        if close >= ma20 and ma5 >= ma10 >= ma20:
            return "uptrend"
        if close <= ma20 and ma5 <= ma10 <= ma20:
            return "downtrend"
    return "uptrend" if close >= enriched.iloc[0]["close"] else "downtrend"


def _volume_state(enriched: pd.DataFrame) -> str:
    latest_volume = float(enriched.iloc[-1]["volume"])
    reference = enriched["volume"].tail(min(5, len(enriched)))
    baseline = float(reference.iloc[:-1].mean()) if len(reference) > 1 else float(reference.iloc[0])
    if latest_volume > baseline:
        return "expanding"
    if latest_volume < baseline:
        return "contracting"
    return "stable"


def _levels(series: pd.Series, reducer: str) -> list[float]:
    windows = [min(10, len(series)), min(20, len(series))]
    values: list[float] = []
    for window in windows:
        if window <= 0:
            continue
        window_slice = series.tail(window)
        value = window_slice.min() if reducer == "min" else window_slice.max()
        rounded = round(float(value), 2)
        if rounded not in values:
            values.append(rounded)
    return values


def analyze_market_frame(frame: pd.DataFrame, *, ticker: str) -> AnalysisBundle:
    enriched = _normalize_frame(frame)
    _require_indicator_backend()
    indicator_backend = "pandas_ta"

    enriched["ma5"] = _sma(enriched["close"], length=5)
    enriched["ma10"] = _sma(enriched["close"], length=10)
    enriched["ma20"] = _sma(enriched["close"], length=20)

    macd = _macd(enriched["close"], fast=12, slow=26, signal=9)
    if macd is None:
        enriched["macd"] = pd.NA
        enriched["signal"] = pd.NA
        enriched["hist"] = pd.NA
    else:
        enriched["macd"] = macd["macd"]
        enriched["signal"] = macd["signal"]
        enriched["hist"] = macd["hist"]

    enriched["rsi14"] = _rsi(enriched["close"], length=14)

    boll = _bbands(enriched["close"], length=20, std=2)
    if boll is None:
        enriched["boll_upper"] = pd.NA
        enriched["boll_mid"] = pd.NA
        enriched["boll_lower"] = pd.NA
    else:
        enriched["boll_upper"] = boll["upper"]
        enriched["boll_mid"] = boll["mid"]
        enriched["boll_lower"] = boll["lower"]

    atr = _atr(enriched["high"], enriched["low"], enriched["close"], length=14)
    enriched["atr14"] = atr if atr is not None else pd.NA

    stoch = _stoch(enriched["high"], enriched["low"], enriched["close"], k=14, d=3, smooth_k=3)
    if stoch is None:
        enriched["k"] = pd.NA
        enriched["d"] = pd.NA
        enriched["j"] = pd.NA
    else:
        enriched["k"] = stoch["k"]
        enriched["d"] = stoch["d"]
        enriched["j"] = (3 * enriched["k"]) - (2 * enriched["d"])

    latest = enriched.iloc[-1]
    summary = {
        "indicator_backend": indicator_backend,
        "trend": _trend_state(latest, enriched),
        "volume_state": _volume_state(enriched),
        "latest_close": _to_float(latest["close"]),
        "support_levels": _levels(enriched["low"], "min"),
        "resistance_levels": _levels(enriched["high"], "max"),
    }
    indicators = {
        "ma": {
            "ma5": _to_float(latest["ma5"]),
            "ma10": _to_float(latest["ma10"]),
            "ma20": _to_float(latest["ma20"]),
        },
        "macd": {
            "macd": _to_float(latest["macd"]),
            "signal": _to_float(latest["signal"]),
            "hist": _to_float(latest["hist"]),
        },
        "rsi": {"rsi14": _to_float(latest["rsi14"])},
        "boll": {
            "upper": _to_float(latest["boll_upper"]),
            "mid": _to_float(latest["boll_mid"]),
            "lower": _to_float(latest["boll_lower"]),
        },
        "atr": {"atr14": _to_float(latest["atr14"])},
        "kdj": {
            "k": _to_float(latest["k"]),
            "d": _to_float(latest["d"]),
            "j": _to_float(latest["j"]),
        },
    }
    warnings = _warning_list(indicators, ticker=ticker, indicator_backend=indicator_backend)
    return AnalysisBundle(summary=summary, indicators=indicators, warnings=warnings, chart_frame=enriched)
