import sys
import os
import unittest
from unittest.mock import patch

import pandas as pd

# Add skill root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from scripts import stock_tools as stock_tools_module
    from scripts.stock_tools import StockTools
    from scripts.database_manager import DatabaseManager
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

class TestStock(unittest.TestCase):
    def test_init(self):
        print("Testing StockTools Iteration...")
        db = DatabaseManager(":memory:")
        tools = StockTools(db, auto_update=False)
        self.assertIsNotNone(tools)
        print("StockTools Initialized.")

    def test_longer_historical_window_triggers_refetch_when_cache_starts_too_late(self):
        db = DatabaseManager(":memory:")
        tools = StockTools(db, auto_update=False)

        short_dates = pd.bdate_range("2026-03-23", "2026-04-21")
        long_dates = pd.bdate_range("2026-01-01", "2026-04-21")

        def _frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "日期": dates.strftime("%Y-%m-%d"),
                    "开盘": [3.5 + idx * 0.01 for idx, _ in enumerate(dates)],
                    "收盘": [3.55 + idx * 0.01 for idx, _ in enumerate(dates)],
                    "最高": [3.6 + idx * 0.01 for idx, _ in enumerate(dates)],
                    "最低": [3.45 + idx * 0.01 for idx, _ in enumerate(dates)],
                    "成交量": [1000000 + idx for idx, _ in enumerate(dates)],
                    "涨跌幅": [0.1 for _ in dates],
                }
            )

        calls: list[tuple[str, str]] = []

        def _fake_hist(*, symbol: str, period: str, start_date: str, end_date: str, adjust: str):
            calls.append((start_date, end_date))
            self.assertEqual(symbol, "600515")
            self.assertEqual(period, "daily")
            self.assertEqual(adjust, "qfq")
            if start_date == "20260321":
                return _frame(short_dates)
            if start_date == "20251021":
                return _frame(long_dates)
            raise AssertionError(f"unexpected start_date: {start_date}")

        with patch.object(stock_tools_module.ak, "stock_zh_a_hist", side_effect=_fake_hist):
            short = tools.get_stock_price("600515", start_date="2026-03-21", end_date="2026-04-21")
            self.assertEqual(len(short), len(short_dates))

            longer = tools.get_stock_price("600515", start_date="2025-10-21", end_date="2026-04-21")

        self.assertEqual(calls, [("20260321", "20260421"), ("20251021", "20260421")])
        self.assertEqual(longer["date"].min(), "2026-01-01")
        self.assertEqual(len(longer), len(long_dates))

if __name__ == '__main__':
    unittest.main()
