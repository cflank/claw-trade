from __future__ import annotations

import pandas as pd
from claw_trade.data_gateway.agent_tools import DataLayerAgentToolError
from claw_trade.data_gateway.agent_tools import load_price_frame as _load_data_layer_price_frame


class NoMarketDataError(RuntimeError):
    """Raised when the data layer reports that no rows are available."""


def _normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    if normalized.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "change_pct"])

    normalized.columns = [str(column).lower() for column in normalized.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(normalized.columns)
    if missing:
        raise RuntimeError(f"price frame missing required columns: {sorted(missing)}")

    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized = normalized.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if "change_pct" in normalized.columns:
        normalized["change_pct"] = pd.to_numeric(normalized["change_pct"], errors="coerce")
    else:
        normalized["change_pct"] = normalized["close"].pct_change().mul(100.0)
    normalized["change_pct"] = normalized["change_pct"].fillna(0.0)
    normalized["date"] = normalized["date"].dt.strftime("%Y-%m-%d")
    normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return normalized[["date", "open", "high", "low", "close", "volume", "change_pct"]]


def load_market_price_frame(*, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        frame = _load_data_layer_price_frame(ticker=ticker, start_date=start_date, end_date=end_date)
    except DataLayerAgentToolError as exc:
        raise NoMarketDataError(str(exc)) from exc
    if frame is None:
        raise NoMarketDataError(f"no price rows available for {ticker}")
    normalized = _normalize_price_frame(frame)
    if normalized.empty:
        raise NoMarketDataError(f"no price rows available for {ticker}")
    return normalized


load_price_frame = load_market_price_frame
