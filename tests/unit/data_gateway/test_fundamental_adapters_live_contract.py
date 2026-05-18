from __future__ import annotations

from claw_trade.data_gateway.models import AdmissionCheckStatus, FreshnessPolicy, Market, PackDomain, PackRequest, ProviderStatus
from claw_trade.data_gateway.providers import fundamental
from claw_trade.data_gateway.providers.fundamental import build_default_fundamental_adapters


def _request(market: Market, ticker: str, currency: str) -> PackRequest:
    return PackRequest(
        run_id=f"run-{market.value.lower()}",
        call_id=f"call-{market.value.lower()}",
        worker_id="fundamental_analyst",
        market=market,
        domain=PackDomain.FUNDAMENTAL,
        ticker=ticker,
        company_name=ticker,
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency=currency,
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def test_cn_a_akshare_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CN_A, "600519.SH", "CNY")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.akshare.cn_a")
    spec = adapter.build_call_specs(request)[0]

    def _fake_call(*, symbol: str):
        assert symbol == "600519"
        return ({"pe_ttm": 31.1, "pb": 8.8, "roe": 0.34},)

    monkeypatch.setattr(fundamental, "_call_akshare_cn_a_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert fetch.source_url and "akshare" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.fundamental.supplement.v1"
    assert normalized.rows[0]["valuation.pe"] == 31.1
    assert normalized.rows[0]["valuation.pb"] == 8.8
    assert normalized.rows[0]["financial_indicators.roe"] == 0.34


def test_cn_a_tushare_missing_token_is_explicit_credential_missing() -> None:
    request = _request(Market.CN_A, "600519.SH", "CNY")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.tushare.cn_a")
    spec = adapter.build_call_specs(request)[0]

    credential = adapter.validate_credentials()

    assert credential.status == AdmissionCheckStatus.MISSING
    assert credential.missing_keys == ("TUSHARE_TOKEN",)
    try:
        adapter.fetch(spec, request)
    except RuntimeError as exc:
        assert "token missing" in str(exc)
    else:
        raise AssertionError("fetch should not be called after credential validation fails")


def test_hk_akshare_indicator_is_independent_candidate(monkeypatch) -> None:
    request = _request(Market.HK, "00700.HK", "HKD")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.akshare.hk")
    spec = adapter.build_call_specs(request)[0]

    def _fake_hk_call(*, symbol: str):
        assert symbol == "00700"
        return ({"市盈率": 19.8, "市净率": 4.2, "净资产收益率": 0.21},)

    monkeypatch.setattr(fundamental, "_call_akshare_hk_fundamental", _fake_hk_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "hk.fundamental.akshare.indicator.v1"
    assert normalized.rows[0]["valuation.pe"] == 19.8
    assert normalized.rows[0]["valuation.pb"] == 4.2
    assert normalized.rows[0]["financial_indicators.roe"] == 0.21


def test_hk_tushare_uses_hk_fina_indicator_when_token_present(monkeypatch) -> None:
    request = _request(Market.HK, "00700.HK", "HKD")
    adapter = next(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={"TUSHARE_TOKEN": "token"})
        if item.adapter_id == "fundamental.tushare.hk"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_call(*, token: str, ts_code: str, start_date: str, end_date: str):
        seen.update({"token": token, "ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        return ({"pe_ttm": 18.9, "pb": 4.1, "roe_avg": 0.22},)

    monkeypatch.setattr(fundamental, "_call_tushare_hk_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"token": "token", "ts_code": "00700.HK", "start_date": "2026-05-01", "end_date": "2026-05-17"}
    assert fetch.source_url == "https://api.tushare.pro"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "hk.fundamental.tushare.v1"
    assert normalized.rows[0]["valuation.pe"] == 18.9
    assert normalized.rows[0]["valuation.pb"] == 4.1
    assert normalized.rows[0]["financial_indicators.roe"] == 0.22


def test_hk_yfinance_supplement_uses_openbb_symbol_and_core_fields(monkeypatch) -> None:
    request = _request(Market.HK, "00700.HK", "HKD")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.yfinance.hk")
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_call(*, symbol: str):
        seen["symbol"] = symbol
        return ({"pe_ratio": 18.7, "price_to_book": 3.9, "return_on_equity": 0.214},)

    monkeypatch.setattr(fundamental, "_call_openbb_hk_fundamental_yfinance", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "0700.HK"}
    assert fetch.source_url and "yahoo" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "hk.fundamental.yfinance.v1"
    assert normalized.rows[0]["valuation.pe"] == 18.7
    assert normalized.rows[0]["valuation.pb"] == 3.9
    assert normalized.rows[0]["financial_indicators.roe"] == 0.214


def test_us_yfinance_fetch_and_normalize_without_fmp_key(monkeypatch) -> None:
    request = _request(Market.US, "AAPL", "USD")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.yfinance.us")
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_call(*, symbol: str):
        seen["symbol"] = symbol
        return ({"pe_ratio": 28.3, "price_to_book": 39.5, "return_on_equity": 0.44},)

    monkeypatch.setattr(fundamental, "_call_openbb_us_fundamental_yfinance", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "AAPL"}
    assert fetch.source_url and "yahoo" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "us.fundamental.core.v1"
    assert normalized.rows[0]["valuation.pe"] == 28.3
    assert normalized.rows[0]["valuation.pb"] == 39.5
    assert normalized.rows[0]["financial_indicators.roe"] == 0.44


def test_crypto_missing_fields_are_explicit_field_missing(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USD")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.coingecko.crypto")
    spec = adapter.build_call_specs(request)[0]

    def _fake_call(*, coin_id: str, demo_api_key: str | None):
        assert coin_id == "bitcoin"
        assert demo_api_key is None
        return {"unexpected": "shape"}

    monkeypatch.setattr(fundamental, "_call_coingecko_coin_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert normalized.status == ProviderStatus.FIELD_MISSING
    assert normalized.schema_id == "crypto.fundamental.coingecko.v1"
    assert "valuation.market_cap_usd" in normalized.missing_fields
