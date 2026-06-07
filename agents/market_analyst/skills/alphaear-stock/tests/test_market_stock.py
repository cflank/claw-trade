from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import claw_trade.data_gateway.agent_tools as agent_tools
from scripts.stock_tools import StockTools


def test_stock_tools_delegates_price_to_data_layer(monkeypatch) -> None:
    calls: dict[str, object] = {}
    frame = pd.DataFrame(
        [
            {
                "date": "2026-04-21",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000,
                "change_pct": 1.0,
            }
        ]
    )

    def _load_price_frame(**kwargs):
        calls.update(kwargs)
        return frame

    monkeypatch.setattr(agent_tools, "load_price_frame", _load_price_frame)

    tools = StockTools(auto_update=False)
    result = tools.get_stock_price("600515", start_date="2026-04-01", end_date="2026-04-21")

    assert result.equals(frame)
    assert calls == {"ticker": "600515", "start_date": "2026-04-01", "end_date": "2026-04-21"}


def test_stock_tools_delegates_fundamentals_to_data_layer(monkeypatch) -> None:
    monkeypatch.setattr(agent_tools, "load_fundamentals", lambda ticker: {"code": ticker, "pe": 12.3})

    assert StockTools(auto_update=False).get_stock_fundamentals("600515") == {"code": "600515", "pe": 12.3}
