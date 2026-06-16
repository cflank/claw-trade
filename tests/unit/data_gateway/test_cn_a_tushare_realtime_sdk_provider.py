from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from claw_trade.data_gateway.execution.fetch_engine import FetchTask
from claw_trade.data_gateway.providers.plugins.cn_a.provider_matrix import TushareRealtimeSDKPlugin


class _Resolver:
    def get_credential(self, name: str) -> str | None:
        return "ts-token" if name == "data_source:tushare" else None


class _FakeTushare:
    def __init__(self) -> None:
        self.token: str | None = None
        self.calls: list[dict[str, object]] = []

    def set_token(self, token: str) -> None:
        self.token = token

    def realtime_quote(self, *, ts_code: str, src: str = "sina") -> list[dict[str, object]]:
        self.calls.append({"ts_code": ts_code, "src": src})
        return [
            {
                "TS_CODE": "600519.SH",
                "NAME": "贵州茅台",
                "OPEN": "1271.18",
                "PRE_CLOSE": "1279",
                "PRICE": "1291.91",
                "HIGH": "1295",
                "LOW": "1265.01",
                "BID": "1291.90",
                "ASK": "1291.91",
                "VOLUME": "50495",
                "AMOUNT": "6477910214",
                "B1_V": "10",
                "B1_P": "1291.90",
                "B2_V": "20",
                "B2_P": "1291.80",
                "A1_V": "11",
                "A1_P": "1291.91",
                "A2_V": "21",
                "A2_P": "1292.00",
                "DATE": "20260612",
                "TIME": "15:00:00",
            }
        ]


def _task(endpoint_id: str) -> FetchTask:
    return FetchTask(
        batch_id=f"batch:{endpoint_id}",
        provider_id="cn_a_tushare_realtime",
        endpoint_id=endpoint_id,
        market="CN_A",
        data_type="order_book_snapshot" if endpoint_id == "realtime_order_book" else "quote_snapshot",
        granularity="realtime",
        symbol_ids=("600519.SH",),
        date_range_start=None,
        date_range_end=None,
        fields=("price", "bid_price", "ask_price", "timestamp", "symbol_id"),
        provider_config_version="test",
        params={"src": "sina"},
        deadline_at=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
    )


def test_tushare_realtime_sdk_quote_parses_quote_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeTushare()
    monkeypatch.setitem(sys.modules, "tushare", fake)

    result = TushareRealtimeSDKPlugin().fetch(_task("realtime_quote"), SimpleNamespace(credential_resolver=_Resolver()))

    assert result.status.value == "success"
    assert fake.token == "ts-token"
    assert fake.calls == [{"ts_code": "600519.SH", "src": "sina"}]
    row = result.payload["rows"][0]
    assert row["dataset"] == "quote_snapshot"
    assert row["symbol_id"] == "600519.SH"
    assert row["price"] == 1291.91
    assert row["bid_price"] == 1291.9
    assert row["ask_price"] == 1291.91
    assert row["source_roles"] == ("paid_data",)
    assert row["timestamp"] == datetime(2026, 6, 12, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_tushare_realtime_sdk_quote_parses_order_book_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeTushare()
    monkeypatch.setitem(sys.modules, "tushare", fake)

    result = TushareRealtimeSDKPlugin().fetch(_task("realtime_order_book"), SimpleNamespace(credential_resolver=_Resolver()))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "order_book_snapshot"
    assert row["bid_price"] == 1291.9
    assert row["bid_size"] == 10.0
    assert row["ask_price"] == 1291.91
    assert row["ask_size"] == 11.0
    assert row["bid_price_2"] == 1291.8
    assert row["ask_size_2"] == 21.0
