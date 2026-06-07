from __future__ import annotations

from typing import Any

import claw_trade.data_gateway.agent_tools as agent_tools


class StockTools:
    def __init__(self, auto_update: bool = True):
        del auto_update

    def _check_and_update_stock_list(self, force: bool = False) -> None:
        del force

    def search_ticker(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        return agent_tools.search_ticker(query, limit=limit)

    def get_stock_price(
        self,
        ticker: str,
        start_date: str | None = None,
        end_date: str | None = None,
        force_sync: bool = False,
    ):
        del force_sync
        return agent_tools.load_price_frame(ticker=ticker, start_date=start_date or "", end_date=end_date or "")

    def get_stock_fundamentals(self, ticker: str) -> dict[str, Any]:
        return agent_tools.load_fundamentals(ticker)


def get_stock_analysis(ticker: str) -> str:
    tools = StockTools(auto_update=False)
    df = tools.get_stock_price(ticker)
    if df.empty:
        return f"未能获取 {ticker} 的股价数据。"
    latest = df.iloc[-1]
    change = ((latest["close"] - df.iloc[0]["close"]) / df.iloc[0]["close"]) * 100
    return "\n".join(
        [
            f"## {ticker} 分析报告",
            f"- 查询时段: {df.iloc[0]['date']} -> {latest['date']}",
            f"- 当前价: {latest['close']:.2f}",
            f"- 时段涨跌: {change:+.2f}%",
            f"- 最高/最低: {df['high'].max():.2f} / {df['low'].min():.2f}",
        ]
    )
