from __future__ import annotations

import pytest

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, ProviderStatus
from claw_trade.data_gateway.providers import market_adapters
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters


def test_df_to_rows_preserves_named_index_date() -> None:
    pandas = pytest.importorskip("pandas")
    frame = pandas.DataFrame(
        {
            "open": [200.0],
            "high": [201.0],
            "low": [199.0],
            "close": [200.5],
            "volume": [1000],
        },
        index=pandas.to_datetime(["2026-05-17"]),
    )
    frame.index.name = "date"

    rows = market_adapters._df_to_rows(frame)

    assert str(rows[0]["date"])[:10] == "2026-05-17"


def _request(market: Market, ticker: str, currency: str) -> PackRequest:
    return PackRequest(
        run_id=f"run-{market.value.lower()}",
        call_id=f"call-{market.value.lower()}",
        worker_id="market_analyst",
        market=market,
        domain=PackDomain.MARKET,
        ticker=ticker,
        company_name=ticker,
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency=currency,
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def test_cn_a_akshare_fetch_and_normalize_ohlcv(monkeypatch) -> None:
    request = _request(Market.CN_A, "600519.SH", "CNY")
    adapter = next(item for item in build_default_market_adapters(provider_config_version="cfg", env={}) if item.market == Market.CN_A)
    spec = next(item for item in adapter.build_call_specs(request) if item.endpoint == "stock_zh_a_hist")

    def _fake_call(*, symbol: str, start_date: str, end_date: str, adjust: str):
        assert symbol == "600519"
        assert start_date == "2026-05-01"
        assert end_date == "2026-05-17"
        assert adjust == "qfq"
        return (
            {"日期": "2026-05-03", "开盘": 101.0, "最高": 103.0, "最低": 99.0, "收盘": 102.0, "成交量": 12345, "成交额": 88888},
            {"日期": "2026-05-01", "开盘": 98.0, "最高": 100.0, "最低": 97.0, "收盘": 99.0, "成交量": 10000, "成交额": 66666},
        )

    monkeypatch.setattr(market_adapters, "_call_akshare_stock_zh_a_hist", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert fetch.source_url and "akshare" in fetch.source_url
    assert fetch.provider_request_id and fetch.provider_request_id.startswith("req_")
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.market.ohlcv.v1"
    assert normalized.row_count == 2
    assert normalized.rows[0]["date"] == "2026-05-01"
    assert normalized.rows[1]["date"] == "2026-05-03"
    assert normalized.rows[0]["currency"] == "CNY"
    assert normalized.rows[0]["timezone"] == "Asia/Shanghai"


def test_cn_a_tushare_daily_missing_token_is_explicit_error() -> None:
    request = _request(Market.CN_A, "600519", "CNY")
    adapter = next(item for item in build_default_market_adapters(provider_config_version="cfg", env={}) if item.market == Market.CN_A)
    spec = next(item for item in adapter.build_call_specs(request) if item.endpoint == "daily")

    try:
        adapter.fetch(spec, request)
    except RuntimeError as exc:
        assert "token missing" in str(exc)
    else:
        raise AssertionError("expected explicit token error for cn_a daily")


def test_cn_a_tushare_daily_uses_proxy_pro_bar_initializer(monkeypatch) -> None:
    seen: dict[str, object] = {}
    pro = object()

    class _Frame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {
                    "trade_date": "20260515",
                    "open": 11.05,
                    "high": 11.11,
                    "low": 10.96,
                    "close": 10.99,
                    "vol": 974741.76,
                    "amount": 1074648.533,
                }
            ]

    def _fake_create(*, token: str):
        seen["token"] = token
        return pro

    def _fake_pro_bar(*, api, ts_code: str, start_date: str, end_date: str, adj: str):
        seen.update({"api": api, "ts_code": ts_code, "start_date": start_date, "end_date": end_date, "adj": adj})
        return _Frame()

    monkeypatch.setattr(market_adapters, "create_tushare_pro", _fake_create)
    monkeypatch.setattr(market_adapters, "call_tushare_pro_bar", _fake_pro_bar)

    rows = market_adapters._call_tushare_daily(
        token="secret-token",
        ts_code="000001.SZ",
        start_date="2026-05-01",
        end_date="2026-05-17",
    )

    assert seen == {
        "token": "secret-token",
        "api": pro,
        "ts_code": "000001.SZ",
        "start_date": "20260501",
        "end_date": "20260517",
        "adj": "qfq",
    }
    assert rows[0]["trade_date"] == "20260515"


def test_hk_stock_hk_daily_keeps_00700_with_qfq_and_normalizes(monkeypatch) -> None:
    request = _request(Market.HK, "00700.HK", "HKD")
    adapter = next(item for item in build_default_market_adapters(provider_config_version="cfg", env={}) if item.market == Market.HK)
    spec = next(item for item in adapter.build_call_specs(request) if item.endpoint == "stock_hk_daily")

    seen: dict[str, str] = {}

    def _fake_call(*, symbol: str, adjust: str, start_date: str, end_date: str):
        seen["symbol"] = symbol
        seen["adjust"] = adjust
        seen["start_date"] = start_date
        seen["end_date"] = end_date
        return (
            {"日期": "2026-05-17", "开盘": 510.0, "最高": 515.0, "最低": 505.0, "收盘": 512.0, "成交量": 1000, "成交额": 500000},
            {"日期": "2026-05-16", "开盘": 500.0, "最高": 511.0, "最低": 499.0, "收盘": 510.0, "成交量": 1200, "成交额": 600000},
        )

    monkeypatch.setattr(market_adapters, "_call_akshare_stock_hk_daily", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "00700", "adjust": "qfq", "start_date": "2026-05-01", "end_date": "2026-05-17"}
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "hk.market.ohlcv.v1"
    assert normalized.rows[0]["date"] == "2026-05-16"
    assert normalized.rows[1]["date"] == "2026-05-17"
    assert normalized.rows[0]["currency"] == "HKD"
    assert normalized.rows[0]["timezone"] == "Asia/Hong_Kong"


def test_us_openbb_yfinance_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.US, "AAPL", "USD")
    adapter = next(item for item in build_default_market_adapters(provider_config_version="cfg", env={}) if item.market == Market.US)
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, str] = {}

    def _fake_call(*, symbol: str, start_date: str, end_date: str, provider: str):
        captured["symbol"] = symbol
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["provider"] = provider
        return (
            {"Date": "2026-05-15", "Open": 200.0, "High": 203.0, "Low": 199.0, "Close": 202.0, "Volume": 1000000},
            {"Date": "2026-05-16", "Open": 202.0, "High": 205.0, "Low": 201.0, "Close": 204.0, "Volume": 1200000},
        )

    monkeypatch.setattr(market_adapters, "_call_openbb_equity_price_historical", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert captured == {"symbol": "AAPL", "start_date": "2026-05-01", "end_date": "2026-05-17", "provider": "yfinance"}
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "us.market.ohlcv.v1"
    assert normalized.row_count == 2
    assert normalized.rows[-1]["close"] == 204.0
    assert normalized.rows[-1]["timezone"] == "America/New_York"


def test_crypto_openbb_yfinance_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(item for item in build_default_market_adapters(provider_config_version="cfg", env={}) if item.market == Market.CRYPTO)
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, str] = {}

    def _fake_call(*, symbol: str, start_date: str, end_date: str, provider: str):
        captured["symbol"] = symbol
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["provider"] = provider
        return (
            {"date": "20260517", "open": 103000.0, "high": 104000.0, "low": 102500.0, "close": 103500.0, "volume": 1234.5},
            {"date": "20260516", "open": 102000.0, "high": 103000.0, "low": 101500.0, "close": 102700.0, "volume": 1200.5},
        )

    monkeypatch.setattr(market_adapters, "_call_openbb_crypto_price_historical", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert captured == {"symbol": "BTCUSD", "start_date": "2026-05-01", "end_date": "2026-05-17", "provider": "yfinance"}
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "crypto.market.ohlcv.v1"
    assert normalized.rows[0]["date"] == "2026-05-16"
    assert normalized.rows[1]["date"] == "2026-05-17"
    assert normalized.rows[0]["timezone"] == "UTC"
