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
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={})
        if item.market == Market.CRYPTO and item.adapter_id == "project.crypto.market"
    )
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

    assert spec.params["requested_start_date"] == "2026-05-01"
    assert spec.params["technical_lookback_reason"] == "vegas_purple_band_ema676_daily"
    assert captured == {"symbol": "BTCUSD", "start_date": "2024-06-11", "end_date": "2026-05-17", "provider": "yfinance"}
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "crypto.market.ohlcv.v1"
    assert normalized.rows[0]["date"] == "2026-05-16"
    assert normalized.rows[1]["date"] == "2026-05-17"
    assert normalized.rows[0]["timezone"] == "UTC"


def test_crypto_capability_plan_is_not_yfinance_only() -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapters = tuple(item for item in build_default_market_adapters(provider_config_version="cfg", env={}) if item.market == Market.CRYPTO)
    all_specs = tuple(spec for adapter in adapters for spec in adapter.build_call_specs(request))
    endpoints = {spec.endpoint for spec in all_specs}
    assert endpoints == {
        "crypto_price_historical",
        "futures_oi_funding",
        "liquidation_heatmap",
        "onchain_signals",
        "macro_regime",
        "catalyst_events",
        "ahr999_index",
    }
    assert all(spec.provider_config_version == "cfg" for spec in all_specs)
    assert all(spec.coverage_group for spec in all_specs)
    assert all(spec.coverage_quorum == 1 for spec in all_specs)
    assert all(spec.raw_export_policy == "metadata_only" for spec in all_specs)

    missing = {
        adapter.adapter_id: adapter.validate_credentials().status.value
        for adapter in adapters
        if adapter.adapter_id != "project.crypto.market"
    }
    assert missing == {
        "project.crypto.derivatives": "missing",
        "project.crypto.liquidation_map": "missing",
        "project.crypto.onchain": "missing",
        "project.crypto.macro": "missing",
        "project.crypto.events": "missing",
        "project.crypto.ahr999": "missing",
    }


def test_crypto_derivatives_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"COINGLASS_API_KEY": "k"})
        if item.adapter_id == "project.crypto.derivatives"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: list[tuple[str, dict[str, str]]] = []

    def _fake_coinglass_get(path: str, *, params=None, version="v4", env=None):
        del env
        seen.append((path, dict(params or {})))
        if path == "/futures/open-interest/exchange-list":
            return {"code": "0", "data": [{"time": 1710000000000, "open_interest_usd": "12345"}]}
        if path == "/futures/funding-rate/exchange-list":
            return {"code": "0", "data": [{"time": 1710000000000, "stablecoin_margin_list": [{"exchange": "Binance"}, {"exchange": "OKX", "funding_rate": "0.0008"}]}]}
        if path == "/futures/global-long-short-account-ratio/history":
            return {"code": "0", "data": [{"timestamp": 1710000000000, "global_account_long_short_ratio": "1.2"}]}
        if path == "/futures/v2/taker-buy-sell-volume/history":
            return {"code": "0", "data": [{"taker_buy_volume_usd": "2500", "taker_sell_volume_usd": "1000"}]}
        if path == "/futures/liquidation/history":
            return {"code": "0", "data": [{"long_liq": 100, "short_liq": 80}]}
        raise AssertionError(path)

    monkeypatch.setattr(market_adapters, "_coinglass_get", _fake_coinglass_get)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert [item[0] for item in seen] == [
        "/futures/open-interest/exchange-list",
        "/futures/funding-rate/exchange-list",
        "/futures/global-long-short-account-ratio/history",
        "/futures/v2/taker-buy-sell-volume/history",
        "/futures/liquidation/history",
    ]
    assert seen[0][1]["symbol"] == "BTC"
    assert seen[2][1]["symbol"] == "BTCUSDT"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "crypto.market.derivatives.v1"
    row = normalized.rows[0]
    assert row["asset"] == "BTC"
    assert row["open_interest"]["value"] == 12345.0
    assert row["funding_rates"]["value"] == 0.0008
    assert row["long_short_ratio"]["value"] == 1.2
    assert row["cvd_proxy"]["cumulative_delta"] == 1500.0
    assert row["cvd_proxy"]["bias"] == "buy_pressure"


def test_crypto_liquidation_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"COINGLASS_API_KEY": "k"})
        if item.adapter_id == "project.crypto.liquidation_map"
    )
    spec = adapter.build_call_specs(request)[0]

    def _fake_coinglass_get(path: str, *, params=None, version="v4", env=None):
        del env
        assert path == "/futures/liquidation/aggregated-heatmap/model1"
        assert params == {"symbol": "BTC", "range": "3d"}
        return {
            "code": "0",
            "data": {
                "current_price": 65000,
                "y_axis": [64000, 65000, 66000],
                "liquidation_leverage_data": [[0, 2, 1000000], [0, 0, 500000]],
            },
        }

    monkeypatch.setattr(market_adapters, "_coinglass_get", _fake_coinglass_get)

    normalized = adapter.normalize(spec, adapter.fetch(spec, request))

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    row = normalized.rows[0]
    assert row["current_price"] == 65000.0
    assert row["largest_clusters"][0]["liquidation_value"] == 1000000.0


def test_crypto_onchain_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"COINGLASS_API_KEY": "k"})
        if item.adapter_id == "project.crypto.onchain"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: list[tuple[str, dict[str, str]]] = []

    def _fake_coinglass_get(path: str, *, params=None, version="v4", env=None):
        del version
        del env
        seen.append((path, dict(params or {})))
        if path == "/exchange/balance/list":
            return {"code": "0", "data": [{"exchange": "Binance", "balance": "1234"}]}
        if path == "/exchange/balance/chart":
            return {
                "code": "0",
                "data": [
                    {"time": 1710000000000, "balance": "1000"},
                    {"time": 1710003600000, "balance": "1234"},
                ],
            }
        if path == "/chain/v2/whale-transfer":
            return {"code": "0", "data": [{"amount_usd": "2500000"}, {"value_usd": "1500000"}]}
        if path == "/spot/coin/netflow":
            return {"code": "0", "data": [{"netflow_usd": "200"}, {"netflow_usd": "-50"}]}
        if path == "/index/bitcoin-sth-sopr":
            return {"code": "0", "data": [{"time": 1710000000000, "sth_sopr": "0.98"}]}
        if path == "/index/bitcoin-lth-sopr":
            return {"code": "0", "data": [{"time": 1710000000000, "lth_sopr": "1.04"}]}
        if path == "/index/bitcoin-net-unrealized-profit-loss":
            return {"code": "0", "data": [{"time": 1710000000000, "net_unpnl": "0.43"}]}
        if path == "/index/bitcoin-active-addresses":
            return {"code": "0", "data": [{"time": 1710000000000, "active_address_count": "800000"}]}
        raise AssertionError(path)

    monkeypatch.setattr(market_adapters, "_coinglass_get", _fake_coinglass_get)

    normalized = adapter.normalize(spec, adapter.fetch(spec, request))

    assert [item[0] for item in seen] == [
        "/exchange/balance/list",
        "/exchange/balance/chart",
        "/chain/v2/whale-transfer",
        "/spot/coin/netflow",
        "/spot/coin/netflow",
        "/index/bitcoin-sth-sopr",
        "/index/bitcoin-lth-sopr",
        "/index/bitcoin-net-unrealized-profit-loss",
        "/index/bitcoin-active-addresses",
    ]
    assert seen[0][1] == {"symbol": "BTC"}
    assert seen[2][1]["symbol"] == "BTC"
    assert seen[3][1]["symbol"] == "USDT"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    row = normalized.rows[0]
    assert row["asset"] == "BTC"
    assert row["source_type"] == "coinglass-onchain"
    assert row["exchange_balance_trend"]["latest"] == 1234.0
    assert row["whale_activity"]["large_tx_count"] == 2
    assert row["whale_activity"]["large_tx_volume"] == 4000000.0
    assert row["stablecoin_flows"]["aggregate_netflow"] == 300.0
    assert row["btc_indicators"]["active_addresses"]["value"] == 800000.0


def test_crypto_onchain_collects_partial_coinglass_signals(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"COINGLASS_API_KEY": "k"})
        if item.adapter_id == "project.crypto.onchain"
    )
    spec = adapter.build_call_specs(request)[0]

    def _fake_coinglass_get(path: str, *, params=None, version="v4", env=None):
        del params
        del version
        del env
        if path == "/exchange/balance/list":
            return {"code": "0", "data": [{"exchange": "Binance", "balance": "1234"}]}
        if path == "/exchange/balance/chart":
            return {"code": "0", "data": [{"time": 1710000000000, "balance": "1234"}]}
        if path == "/chain/v2/whale-transfer":
            return {"code": "0", "data": [{"amount_usd": "2500000"}]}
        if path == "/spot/coin/netflow":
            return {"code": "0", "data": [{"netflow_usd": "200"}]}
        if path == "/index/bitcoin-sth-sopr":
            raise RuntimeError("429 Client Error")
        if path == "/index/bitcoin-lth-sopr":
            return {"code": "0", "data": [{"time": 1710000000000, "lth_sopr": "1.04"}]}
        if path == "/index/bitcoin-net-unrealized-profit-loss":
            return {"code": "0", "data": [{"time": 1710000000000, "net_unpnl": "0.43"}]}
        if path == "/index/bitcoin-active-addresses":
            return {"code": "0", "data": [{"time": 1710000000000, "active_address_count": "800000"}]}
        raise AssertionError(path)

    monkeypatch.setattr(market_adapters, "_coinglass_get", _fake_coinglass_get)

    normalized = adapter.normalize(spec, adapter.fetch(spec, request))

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    row = normalized.rows[0]
    assert row["exchange_balance_trend"]["latest"] == 1234.0
    assert row["whale_activity"]["large_tx_count"] == 1
    assert row["stablecoin_flows"]["aggregate_netflow"] == 400.0
    assert row["btc_indicators"]["active_addresses"]["value"] == 800000.0
    assert row["btc_indicators"]["lth_sopr"]["value"] == 1.04
    assert "sth_sopr" not in row["btc_indicators"]
    assert any("bitcoin-sth-sopr" in item for item in row["warnings"])


def test_crypto_macro_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"FRED_API_KEY": "k"})
        if item.adapter_id == "project.crypto.macro"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: list[str] = []

    def _fake_fred_latest(series_id: str, *, env=None):
        del env
        seen.append(series_id)
        return {"date": "2026-05-17", "value": 1.0}

    monkeypatch.setattr(market_adapters, "_fred_latest", _fake_fred_latest)

    normalized = adapter.normalize(spec, adapter.fetch(spec, request))

    assert "CPIAUCSL" in seen
    assert "FEDFUNDS" in seen
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.rows[0]["risk_window"] == "medium"


def test_crypto_events_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "SOL", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"TAVILY_API_KEY": "k"})
        if item.adapter_id == "project.crypto.events"
    )
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, object] = {}

    def _fake_tavily_search(*, query: str, max_results: int, env=None):
        del env
        captured["query"] = query
        captured["max_results"] = max_results
        return {"results": [{"title": "unlock", "url": "https://example.com/unlock", "published_date": "2026-05-17", "score": 0.9}]}

    monkeypatch.setattr(market_adapters, "_tavily_search", _fake_tavily_search)

    normalized = adapter.normalize(spec, adapter.fetch(spec, request))

    assert "SOL" in str(captured["query"])
    assert captured["max_results"] == 10
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.rows[0]["result_count"] == 1


def test_crypto_ahr999_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"COINGLASS_API_KEY": "k"})
        if item.adapter_id == "project.crypto.ahr999"
    )
    spec = adapter.build_call_specs(request)[0]

    def _fake_coinglass_get(path: str, *, params=None, version="v4", env=None):
        del env
        assert path == "/index/ahr999"
        return {"code": "0", "data": [{"time": 1710000000000, "ahr999": 1.11}]}

    monkeypatch.setattr(market_adapters, "_coinglass_get", _fake_coinglass_get)

    normalized = adapter.normalize(spec, adapter.fetch(spec, request))

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.rows[0]["ahr999"] == 1.11
    assert normalized.rows[0]["dca_zone"] is True


def test_crypto_ahr999_non_btc_is_skipped_at_call_spec_build() -> None:
    request = _request(Market.CRYPTO, "ETH", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg", env={"COINGLASS_API_KEY": "k"})
        if item.adapter_id == "project.crypto.ahr999"
    )
    specs = adapter.build_call_specs(request)
    assert specs == ()


def test_http_get_json_request_exception_redacts_secrets() -> None:
    import requests

    bad_url = (
        "https://user:very-secret@api.example.com/fail?symbol=BTC"
        "&api_key=top-secret&token=hidden-token"
        "&accessToken=camel-secret&x-api-key=x-secret&api-key=dash-secret"
    )

    def _raise_request_exception(*args, **kwargs):
        del args, kwargs
        raise requests.RequestException(f"403 Client Error: Forbidden for url: {bad_url}")

    original = requests.sessions.Session.request
    requests.sessions.Session.request = _raise_request_exception
    try:
        with pytest.raises(RuntimeError) as excinfo:
            market_adapters._http_get_json(bad_url)
    finally:
        requests.sessions.Session.request = original

    message = str(excinfo.value)
    assert "top-secret" not in message
    assert "hidden-token" not in message
    assert "camel-secret" not in message
    assert "x-secret" not in message
    assert "dash-secret" not in message
    assert "user:very-secret@" not in message
    assert "[REDACTED]@api.example.com" in message
    assert "symbol=BTC" in message
    assert "api_key=" in message
    assert "token=" in message
    assert "accessToken=" in message
    assert "x-api-key=" in message
    assert "api-key=" in message


def test_crypto_coinglass_fetch_reads_injected_env_not_global_os(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(
            provider_config_version="cfg",
            env={
                "COINGLASS_API_KEY": "adapter-key",
                "COINGLASS_API_BASE": "https://proxy.keystore.com.cn/open-api-v4.coinglass.com",
                "COINGLASS_API_HEADER_NAME": "X-Test-Key",
            },
        )
        if item.adapter_id == "project.crypto.ahr999"
    )
    spec = adapter.build_call_specs(request)[0]

    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    monkeypatch.delenv("COINGLASS_API_BASE", raising=False)
    monkeypatch.delenv("COINGLASS_API_HEADER_NAME", raising=False)
    captured: dict[str, object] = {}

    def _fake_http_get_json(url: str, *, params=None, headers=None):
        del params
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        return {"code": "0", "data": [{"time": 1710000000000, "ahr999": 1.5}]}

    monkeypatch.setattr(market_adapters, "_http_get_json", _fake_http_get_json)
    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert fetch.source_url == "https://proxy.keystore.com.cn/open-api-v4.coinglass.com"
    assert str(captured["url"]).startswith("https://proxy.keystore.com.cn/open-api-v4.coinglass.com/api/index/ahr999")
    assert captured["headers"] == {"X-Test-Key": "adapter-key", "accept": "application/json"}
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS


def test_crypto_coinglass_missing_key_is_explicit_and_skips_http_request(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(
        item
        for item in build_default_market_adapters(
            provider_config_version="cfg",
            env={"COINGLASS_API_BASE": "https://coinglass.example", "COINGLASS_API_HEADER_NAME": "X-Api-Key"},
        )
        if item.adapter_id == "project.crypto.ahr999"
    )
    spec = adapter.build_call_specs(request)[0]
    assert adapter.validate_credentials().status == market_adapters.AdmissionCheckStatus.MISSING

    called = {"count": 0}

    def _should_not_call_http_get_json(url: str, *, params=None, headers=None):
        del url, params, headers
        called["count"] += 1
        raise AssertionError("http request should not run when credential is missing")

    monkeypatch.setattr(market_adapters, "_http_get_json", _should_not_call_http_get_json)
    with pytest.raises(RuntimeError, match="missing credential keys: COINGLASS_API_KEY"):
        adapter.fetch(spec, request)
    assert called["count"] == 0
