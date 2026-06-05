from __future__ import annotations

from types import SimpleNamespace

from claw_trade.data_gateway.providers.plugins.http_daily_bar import TushareDailyBarPlugin


def test_tushare_daily_bar_rows_scale_amount_from_thousand_cny_to_cny() -> None:
    plugin = TushareDailyBarPlugin()
    rows = plugin._rows_from_payload(
        {
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
                "items": [["000001.SZ", "20260605", 10.0, 11.0, 9.9, 10.8, 1000000.0, 1102446.143]],
            },
        },
        task=SimpleNamespace(),
        symbol="000001.SZ",
    )

    assert rows is not None
    assert rows[0]["amount"] == 1102446143.0
    assert rows[0]["amount_unit"] == "CNY"
