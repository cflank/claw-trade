from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

import frontline_data_pack.us_data_pack as us_data_pack  # noqa: E402


def test_us_market_pack_exposes_original_tradingagents_indicator_set(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(us_data_pack, "_yfinance", lambda: _FakeYFinance())
    monkeypatch.setattr(us_data_pack, "_load_module", _fake_market_module_loader)

    pack = us_data_pack.run_us_market_data_pack(
        {
            "ticker": "AAPL",
            "market": "US",
            "company_name": "Apple Inc.",
            "start_date": "2026-04-01",
            "end_date": "2026-05-13",
        },
        _runtime_context("market_analyst", "us_market_data_pack", tmp_path),
    )

    original = pack["domain_data"]["original_tradingagents_indicators"]
    latest = original["latest"]
    assert latest["close_50_sma"] is not None
    assert latest["close_200_sma"] is not None
    assert latest["close_10_ema"] is not None
    assert latest["vwma"] is not None
    assert latest["macd"] is not None
    assert latest["rsi"] is not None
    assert latest["boll_ub"] is not None
    assert original["missing_latest"] == []

    brief = pack["reader_brief"]
    assert "Original TradingAgents yfinance indicator latest values" in brief
    assert "## close_50_sma values" in brief
    assert "## close_200_sma values" in brief
    assert "## vwma values" in brief


def test_us_original_market_tools_return_separate_tradingagents_text(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(us_data_pack, "_yfinance", lambda: _FakeYFinance())
    monkeypatch.setattr(us_data_pack, "_load_module", _fake_market_module_loader)

    stock_pack = us_data_pack.run_us_get_stock_data(
        {
            "symbol": "AAPL",
            "market": "US",
            "start_date": "2026-04-01",
            "end_date": "2026-05-13",
        },
        _runtime_context("market_analyst", "get_stock_data", tmp_path),
    )
    assert stock_pack["tool_name"] == "get_stock_data"
    assert stock_pack["reader_brief"].startswith("# Stock data for AAPL from 2026-04-01 to 2026-05-13")
    assert "# Total records:" in stock_pack["reader_brief"]
    assert "Open,High,Low,Close,Volume" in stock_pack["reader_brief"]
    assert stock_pack["domain_data"]["chart_files"] == [str(tmp_path / "techlab" / "charts-local" / "chart.png")]
    assert (tmp_path / "techlab" / "charts-local" / "chart.png").exists()

    indicator_pack = us_data_pack.run_us_get_indicators(
        {
            "symbol": "AAPL",
            "market": "US",
            "indicator": "close_50_sma",
            "curr_date": "2026-05-13",
            "look_back_days": 30,
        },
        _runtime_context("market_analyst", "get_indicators", tmp_path),
    )
    assert indicator_pack["tool_name"] == "get_indicators"
    assert "## close_50_sma values from 2026-04-13 to 2026-05-13" in indicator_pack["reader_brief"]
    assert "50 SMA: A medium-term trend indicator." in indicator_pack["reader_brief"]


def test_us_fundamental_pack_exposes_original_ttm_fields_and_quarterly_statements(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(us_data_pack, "_yfinance", lambda: _FakeYFinance())

    pack = us_data_pack.run_us_fundamentals_data_pack(
        {
            "ticker": "AAPL",
            "market": "US",
            "company_name": "Apple Inc.",
            "start_date": "2025-05-13",
            "end_date": "2026-05-13",
        },
        _runtime_context("fundamental_analyst", "us_fundamentals_data_pack", tmp_path),
    )

    assert pack["quality"]["status"] == "complete"
    assert "quarterly_income_statement" in pack["domain_data"]
    assert "quarterly_balance_sheet" in pack["domain_data"]
    assert "quarterly_cash_flow" in pack["domain_data"]
    assert "income_statement_snapshot" not in pack["domain_data"]

    brief = pack["reader_brief"]
    assert "PE Ratio (TTM): 35.48" in brief
    assert "EPS (TTM): 8.25" in brief
    assert "Revenue (TTM): 451442016256" in brief
    assert "Free Cash Flow: 101090746368" in brief
    assert "# Income Statement data for AAPL (quarterly)" in brief
    assert "# Balance Sheet data for AAPL (quarterly)" in brief
    assert "# Cash Flow data for AAPL (quarterly)" in brief
    assert "2026-03-31" in brief

    endpoints = {attempt["endpoint"] for attempt in pack["provider_attempts"]}
    assert "Ticker.quarterly_income_stmt" in endpoints
    assert "Ticker.quarterly_balance_sheet" in endpoints
    assert "Ticker.quarterly_cashflow" in endpoints


def test_us_original_fundamental_tools_return_separate_ttm_and_quarterly_text(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(us_data_pack, "_yfinance", lambda: _FakeYFinance())

    fundamentals_pack = us_data_pack.run_us_get_fundamentals(
        {"ticker": "AAPL", "market": "US", "curr_date": "2026-05-13"},
        _runtime_context("fundamental_analyst", "get_fundamentals", tmp_path),
    )
    assert fundamentals_pack["tool_name"] == "get_fundamentals"
    assert "# Company Fundamentals for AAPL" in fundamentals_pack["reader_brief"]
    assert "PE Ratio (TTM): 35.48" in fundamentals_pack["reader_brief"]
    assert "EPS (TTM): 8.25" in fundamentals_pack["reader_brief"]
    assert "Revenue (TTM): 451442016256" in fundamentals_pack["reader_brief"]

    balance_sheet_pack = us_data_pack.run_us_get_balance_sheet(
        {"ticker": "AAPL", "market": "US", "freq": "quarterly", "curr_date": "2026-05-13"},
        _runtime_context("fundamental_analyst", "get_balance_sheet", tmp_path),
    )
    assert balance_sheet_pack["tool_name"] == "get_balance_sheet"
    assert "# Balance Sheet data for AAPL (quarterly)" in balance_sheet_pack["reader_brief"]
    assert "2026-03-31" in balance_sheet_pack["reader_brief"]


def test_us_original_news_tools_return_separate_news_text(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(us_data_pack, "_yfinance", lambda: _FakeYFinance())

    news_pack = us_data_pack.run_us_get_news(
        {"ticker": "AAPL", "market": "US", "start_date": "2026-05-05", "end_date": "2026-05-13"},
        _runtime_context("news_analyst", "get_news", tmp_path),
    )
    assert news_pack["tool_name"] == "get_news"
    assert "## AAPL News, from 2026-05-05 to 2026-05-13" in news_pack["reader_brief"]
    assert "Apple AI services momentum" in news_pack["reader_brief"]
    assert "Link: https://example.com/aapl-ai" in news_pack["reader_brief"]

    global_pack = us_data_pack.run_us_get_global_news(
        {"market": "US", "curr_date": "2026-05-13", "look_back_days": 10, "limit": 5},
        _runtime_context("news_analyst", "get_global_news", tmp_path),
    )
    assert global_pack["tool_name"] == "get_global_news"
    assert "## Global Market News, from 2026-05-03 to 2026-05-13" in global_pack["reader_brief"]
    assert "Nasdaq futures rise ahead of CPI" in global_pack["reader_brief"]


def _runtime_context(worker_id: str, tool_name: str, tmp_path: Path) -> dict[str, str]:
    return {
        "run_id": "run-us-parity",
        "stage": "frontline",
        "worker_id": worker_id,
        "call_id": f"call-{tool_name}",
        "dispatch_id": f"dispatch-{tool_name}",
        "tool_name": tool_name,
        "evidence_root": str(tmp_path),
    }


def _fake_market_module_loader(path: Path, module_name: str) -> Any:
    if "indicator_engine" in str(path):
        return SimpleNamespace(
            analyze_market_frame=lambda frame, *, ticker: SimpleNamespace(
                indicators={"ma": {"ma5": 1, "ma10": 2, "ma20": 3}},
                summary={"trend": "uptrend", "volume_state": "stable", "support_levels": [], "resistance_levels": []},
                chart_frame=frame,
            )
        )
    return SimpleNamespace(render_market_charts=_fake_render_market_charts)


def _fake_render_market_charts(chart_frame: pd.DataFrame, *, ticker: str, output_dir: Path) -> list[Path]:
    chart_path = Path(output_dir) / "chart.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_bytes(b"\x89PNG\r\n\x1a\nus-market-chart")
    return [chart_path]


class _FakeYFinance:
    def download(self, *_args: Any, **_kwargs: Any) -> pd.DataFrame:
        dates = pd.date_range(end="2026-05-13", periods=260, freq="B")
        close = pd.Series(range(100, 100 + len(dates)), index=dates, dtype="float64")
        frame = pd.DataFrame(
            {
                "Open": close - 0.5,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": pd.Series(range(1_000_000, 1_000_000 + len(dates)), index=dates, dtype="float64"),
            },
            index=dates,
        )
        frame.index.name = "Date"
        return frame

    def Ticker(self, ticker: str) -> "_FakeTicker":
        return _FakeTicker(ticker)


class _FakeTicker:
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.info = {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "marketCap": 4_298_695_245_824,
            "trailingPE": 35.48,
            "forwardPE": 30.62,
            "pegRatio": 2.57,
            "priceToBook": 40.31,
            "trailingEps": 8.25,
            "forwardEps": 9.56,
            "dividendYield": 0.0037,
            "beta": 1.065,
            "fiftyTwoWeekHigh": 294.76,
            "fiftyTwoWeekLow": 193.46,
            "fiftyDayAverage": 263.37,
            "twoHundredDayAverage": 257.41,
            "totalRevenue": 451_442_016_256,
            "grossProfits": 216_070_995_968,
            "ebitda": 159_975_997_440,
            "netIncomeToCommon": 122_575_003_648,
            "profitMargins": 0.2715,
            "operatingMargins": 0.3227,
            "returnOnEquity": 1.4147,
            "returnOnAssets": 0.2622,
            "debtToEquity": 79.548,
            "currentRatio": 1.07,
            "bookValue": 7.26,
            "freeCashflow": 101_090_746_368,
        }
        self.news = _news_items(ticker)
        self._history_frame = _history_frame()
        columns = pd.to_datetime(["2026-03-31", "2025-12-31", "2025-09-30"])
        self.quarterly_income_stmt = pd.DataFrame(
            [[95_359_000_000, 124_300_000_000, 102_466_000_000], [24_780_000_000, 36_330_000_000, 27_466_000_000]],
            index=["Total Revenue", "Net Income"],
            columns=columns,
        )
        self.quarterly_balance_sheet = pd.DataFrame(
            [[371_082_000_000, 379_297_000_000, 359_241_000_000], [84_711_000_000, 90_509_000_000, 98_657_000_000]],
            index=["Total Assets", "Total Debt"],
            columns=columns,
        )
        self.quarterly_cashflow = pd.DataFrame(
            [[24_935_000_000, 30_491_000_000, 23_434_000_000], [-26_000_000_000, -30_000_000_000, -34_000_000_000]],
            index=["Free Cash Flow", "Repurchase Of Capital Stock"],
            columns=columns,
        )

    def history(self, *_args: Any, **_kwargs: Any) -> pd.DataFrame:
        return self._history_frame.copy()


def _history_frame() -> pd.DataFrame:
    dates = pd.date_range(end="2026-05-13", periods=260, freq="B")
    close = pd.Series(range(100, 100 + len(dates)), index=dates, dtype="float64")
    frame = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": pd.Series(range(1_000_000, 1_000_000 + len(dates)), index=dates, dtype="float64"),
        },
        index=dates,
    )
    frame.index.name = "Date"
    return frame


def _news_items(ticker: str) -> list[dict[str, Any]]:
    if ticker.upper() == "AAPL":
        return [
            {
                "content": {
                    "title": "Apple AI services momentum attracts analyst attention",
                    "summary": "Analysts highlighted Apple services and AI optionality.",
                    "provider": {"displayName": "Example Finance"},
                    "pubDate": "2026-05-12T14:00:00Z",
                    "canonicalUrl": {"url": "https://example.com/aapl-ai"},
                }
            }
        ]
    return [
        {
            "content": {
                "title": "Nasdaq futures rise ahead of CPI",
                "summary": "Index futures moved higher before inflation data.",
                "provider": {"displayName": "Example Markets"},
                "pubDate": "2026-05-12T12:00:00Z",
                "canonicalUrl": {"url": "https://example.com/global-cpi"},
            }
        }
    ]
