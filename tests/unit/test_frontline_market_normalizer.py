from __future__ import annotations

from datetime import date, datetime, timedelta
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.normalizer_market import (  # noqa: E402
    build_market_quality_input,
    merge_market_rows,
    normalize_market_rows,
    validate_ohlcv_rows,
)
from frontline_data_pack.profile import (  # noqa: E402
    MIN_MARKET_TECHNICAL_WINDOW_DAYS,
    build_market_provider_query,
    normalize_market_input,
)


def test_t_mkt_001_normalize_market_input_defaults_to_technical_window() -> None:
    normalized = normalize_market_input(
        {
            "ticker": "SH600519",
            "market": "CN_A",
        },
        today=date(2026, 5, 8),
    )

    assert normalized.ticker == "600519.SH"
    assert normalized.end_date == "2026-05-08"
    expected = datetime.strptime("2026-05-08", "%Y-%m-%d").date() - timedelta(days=MIN_MARKET_TECHNICAL_WINDOW_DAYS)
    assert normalized.start_date == expected.isoformat()
    assert normalized.adjust == "qfq"


def test_t_mkt_001_normalize_market_input_preserves_hk_market() -> None:
    normalized = normalize_market_input(
        {
            "ticker": "00700.HK",
            "market": "HK",
        },
        today=date(2026, 5, 8),
    )

    assert normalized.ticker == "00700.HK"
    assert normalized.market == "HK"


def test_t_mkt_001_build_market_provider_query_preserves_hk_market() -> None:
    query = build_market_provider_query(
        {
            "ticker": "00700.HK",
            "market": "HK",
            "start_date": "2026-04-01",
            "end_date": "2026-05-08",
        },
        today=date(2026, 5, 8),
    )

    assert query.ticker == "00700.HK"
    assert query.market == "HK"


def test_t_mkt_001_explicit_short_start_date_expands_to_technical_window() -> None:
    normalized = normalize_market_input(
        {
            "ticker": "SH600519",
            "market": "CN_A",
            "start_date": "2026-04-11",
            "end_date": "2026-05-11",
        }
    )

    expected = datetime.strptime("2026-05-11", "%Y-%m-%d").date() - timedelta(days=MIN_MARKET_TECHNICAL_WINDOW_DAYS)
    assert normalized.start_date == expected.isoformat()
    assert normalized.end_date == "2026-05-11"


def test_t_mkt_001_explicit_earlier_start_date_is_not_shortened() -> None:
    normalized = normalize_market_input(
        {
            "ticker": "SH600519",
            "market": "CN_A",
            "start_date": "2025-12-01",
            "end_date": "2026-05-11",
        }
    )

    assert normalized.start_date == "2025-12-01"
    assert normalized.end_date == "2026-05-11"


def test_t_mkt_001_validate_ohlcv_rejects_high_below_close_and_records_diagnostic() -> None:
    accepted_rows, diagnostics = validate_ohlcv_rows(
        [
            {
                "trade_date": "2026-05-07",
                "open": 100.0,
                "close": 110.0,
                "high": 109.0,
                "low": 99.0,
                "volume": 1000.0,
                "amount": 10000.0,
                "adjust": "qfq",
                "provider": "akshare",
                "endpoint": "stock_zh_a_hist",
                "priority": "P0",
                "role": "p0_price_history",
            }
        ]
    )

    assert accepted_rows == []
    assert any("high_lt_open_close_low" in item for item in diagnostics)


def test_t_mkt_001_merge_market_rows_prefers_p0_for_same_trade_date() -> None:
    merged = merge_market_rows(
        [
            {
                "trade_date": "2026-05-07",
                "open": 100.0,
                "close": 101.0,
                "high": 102.0,
                "low": 99.0,
                "volume": 100.0,
                "amount": 1000.0,
                "adjust": "qfq",
                "provider": "sina",
                "endpoint": "stock_zh_a_daily",
                "priority": "P1",
                "role": "p1_price_history",
            },
            {
                "trade_date": "2026-05-07",
                "open": 200.0,
                "close": 201.0,
                "high": 202.0,
                "low": 199.0,
                "volume": 200.0,
                "amount": 2000.0,
                "adjust": "qfq",
                "provider": "akshare",
                "endpoint": "stock_zh_a_hist",
                "priority": "P0",
                "role": "p0_price_history",
            },
        ]
    )

    assert len(merged) == 1
    assert merged[0]["provider"] == "akshare"
    assert merged[0]["priority"] == "P0"


def test_t_mkt_001_market_quality_input_failed_when_no_rows() -> None:
    quality_input = build_market_quality_input([])
    assert quality_input["status_candidate"] == "failed"
    assert quality_input["accepted_row_count"] == 0


def test_t_mkt_001_normalize_market_rows_supports_akshare_and_eastmoney_shapes() -> None:
    normalized_rows, diagnostics = normalize_market_rows(
        [
            {
                "provider": "akshare",
                "endpoint": "stock_zh_a_hist",
                "priority": "P0",
                "role": "p0_price_history",
                "rows": [
                    {
                        "日期": "2026-05-07",
                        "开盘": "1600.1",
                        "收盘": "1610.2",
                        "最高": "1620.3",
                        "最低": "1590.4",
                        "成交量": "1234567",
                        "成交额": "7890000.5",
                        "adjust": "qfq",
                    }
                ],
            },
            {
                "provider": "eastmoney_direct",
                "endpoint": "push2his_kline",
                "priority": "P0",
                "role": "p0_price_history_backup",
                "rows": [
                    {
                        "trade_date": datetime(2026, 5, 8),
                        "open": 1610.1,
                        "close": 1611.2,
                        "high": 1620.3,
                        "low": 1600.4,
                        "volume": 2234567,
                        "amount": 8890000.5,
                        "adjust": "qfq",
                    }
                ],
            },
        ]
    )

    assert diagnostics == []
    assert len(normalized_rows) == 2
    assert normalized_rows[0]["trade_date"] == "2026-05-07"
    assert normalized_rows[0]["provider"] == "akshare"
    assert normalized_rows[1]["trade_date"] == "2026-05-08"
    assert normalized_rows[1]["provider"] == "eastmoney_direct"


def test_t_mkt_001_build_market_provider_query_uses_normalized_input() -> None:
    query = build_market_provider_query(
        {
            "ticker": "600519",
            "market": "CN_A",
            "start_date": "2026-05-01",
            "end_date": "2026-05-08",
        }
    )

    assert query.market == "CN_A"
    assert query.ticker == "600519.SH"
    assert query.adjust == "qfq"
    expected = datetime.strptime("2026-05-08", "%Y-%m-%d").date() - timedelta(days=MIN_MARKET_TECHNICAL_WINDOW_DAYS)
    normalized = normalize_market_input({"ticker": "600519", "market": "CN_A"}, today=date(2026, 5, 8))
    assert normalized.start_date == expected.isoformat()
