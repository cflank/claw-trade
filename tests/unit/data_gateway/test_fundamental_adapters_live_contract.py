from __future__ import annotations

import pytest

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, PrioritySource, ProviderStatus
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


def test_cn_a_sina_financials_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.CN_A, "600519.SH", "CNY")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.cn_a.sina_financials")
    spec = adapter.build_call_specs(request)[0]

    def _fake_call(*, symbol: str):
        assert symbol == "600519"
        return ({"pe_ttm": 31.1, "pb": 8.8, "roe": 0.34},)

    monkeypatch.setattr(fundamental, "_call_akshare_cn_a_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert fetch.source_url and "sina" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.fundamental.financials.v1"
    assert normalized.rows[0]["valuation.pe"] == 31.1
    assert normalized.rows[0]["valuation.pb"] == 8.8
    assert normalized.rows[0]["financial_indicators.roe"] == 0.34


def test_cn_a_fundamental_catalog_includes_paid_tushare_candidate_but_requires_token() -> None:
    adapters = tuple(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={})
        if item.market == Market.CN_A
    )
    adapter = next(item for item in adapters if item.adapter_id == "fundamental.tushare.cn_a")
    spec = adapter.build_call_specs(_request(Market.CN_A, "600519.SH", "CNY"))[0]

    assert adapter.capability.provider == "tushare"
    assert adapter.capability.credential_requirements == ("TUSHARE_TOKEN",)
    assert spec.priority_source == PrioritySource.SYSTEM_DEFAULT
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        adapter.fetch(spec, _request(Market.CN_A, "600519.SH", "CNY"))


def test_cn_a_tushare_fundamental_uses_financial_endpoints_when_token_present(monkeypatch) -> None:
    request = _request(Market.CN_A, "600519.SH", "CNY")
    adapter = next(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={"TUSHARE_TOKEN": "token"})
        if item.adapter_id == "fundamental.tushare.cn_a"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_call(*, token: str, ts_code: str, start_date: str, end_date: str, env=None):
        seen.update({"token": token, "ts_code": ts_code, "start_date": start_date, "end_date": end_date, "env_token": env.get("TUSHARE_TOKEN") if env else None})
        return (
            {"pe_ttm": 26.1, "pb": 7.2, "tushare_endpoint": "daily_basic"},
            {"roe": 31.5, "tushare_endpoint": "fina_indicator"},
        )

    monkeypatch.setattr(fundamental, "_call_tushare_cn_a_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"token": "token", "ts_code": "600519.SH", "start_date": "2026-05-01", "end_date": "2026-05-17", "env_token": "token"}
    assert spec.priority_source == PrioritySource.USER_PREFERRED
    assert spec.user_preferred is True
    assert fetch.source_url == "https://api.tushare.pro"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.fundamental.financials.v1"
    assert normalized.rows[0]["valuation.pe"] == 26.1
    assert normalized.rows[0]["valuation.pb"] == 7.2
    assert normalized.rows[0]["financial_indicators.roe"] == 31.5


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

    def _fake_call(*, token: str, ts_code: str, start_date: str, end_date: str, env=None):
        seen.update({"token": token, "ts_code": ts_code, "start_date": start_date, "end_date": end_date, "env_token": env.get("TUSHARE_TOKEN") if env else None})
        return ({"pe_ttm": 18.9, "pb": 4.1, "roe_avg": 0.22},)

    monkeypatch.setattr(fundamental, "_call_tushare_hk_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"token": "token", "ts_code": "00700.HK", "start_date": "2026-05-01", "end_date": "2026-05-17", "env_token": "token"}
    assert fetch.source_url == "https://api.tushare.pro"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "hk.fundamental.tushare.v1"
    assert normalized.rows[0]["valuation.pe"] == 18.9
    assert normalized.rows[0]["valuation.pb"] == 4.1
    assert normalized.rows[0]["financial_indicators.roe"] == 0.22


def test_hk_openbb_yfinance_fundamental_is_not_default_candidate() -> None:
    adapters = tuple(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={})
        if item.market == Market.HK
    )

    assert "fundamental.yfinance.hk" not in {item.adapter_id for item in adapters}
    assert "openbb_yfinance_hk" not in {item.capability.provider for item in adapters}


def test_hk_global_yahoo_quote_summary_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.HK, "00700.HK", "HKD")
    adapter = next(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "fundamental.global_yahoo.hk"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_call(*, symbol: str):
        seen["symbol"] = symbol
        return ({"pe_ratio": 18.1, "price_to_book": 3.8, "return_on_equity": 0.2},)

    monkeypatch.setattr(fundamental, "_call_yahoo_quote_summary_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "0700.HK"}
    assert fetch.source_url and "quoteSummary" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "hk.fundamental.yahoo_quote_summary.v1"
    assert normalized.rows[0]["valuation.pe"] == 18.1
    assert normalized.rows[0]["valuation.pb"] == 3.8
    assert normalized.rows[0]["financial_indicators.roe"] == 0.2


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


def test_us_global_yahoo_quote_summary_fetch_and_normalize(monkeypatch) -> None:
    request = _request(Market.US, "AAPL", "USD")
    adapter = next(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "fundamental.global_yahoo.us"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_call(*, symbol: str):
        seen["symbol"] = symbol
        return ({"pe_ratio": 28.3, "price_to_book": 39.5, "return_on_equity": 0.44},)

    monkeypatch.setattr(fundamental, "_call_yahoo_quote_summary_fundamental", _fake_call)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "AAPL"}
    assert fetch.source_url and "quoteSummary" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "us.fundamental.yahoo_quote_summary.v1"
    assert normalized.rows[0]["valuation.pe"] == 28.3
    assert normalized.rows[0]["valuation.pb"] == 39.5
    assert normalized.rows[0]["financial_indicators.roe"] == 0.44


def test_crypto_missing_fields_are_explicit_field_missing(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "BTC", "USDT")
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


def test_crypto_pair_coingecko_uses_coin_id_alias(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "AR/USDT", "USDT")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.coingecko.crypto")
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, str] = {}

    def _fake_call(*, coin_id: str, demo_api_key: str | None):
        captured["coin_id"] = coin_id
        return {"unexpected": "shape"}

    monkeypatch.setattr(fundamental, "_call_coingecko_coin_fundamental", _fake_call)

    adapter.fetch(spec, request)

    assert captured["coin_id"] == "arweave"


def test_crypto_pair_defillama_uses_protocol_alias(monkeypatch) -> None:
    request = _request(Market.CRYPTO, "AR/USDT", "USDT")
    adapter = next(item for item in build_default_fundamental_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "fundamental.defillama.crypto")
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, str] = {}

    def _fake_call(*, protocol_slug: str):
        captured["protocol_slug"] = protocol_slug
        return {"name": "Arweave"}

    monkeypatch.setattr(fundamental, "_call_defillama_fundamental", _fake_call)

    adapter.fetch(spec, request)

    assert captured["protocol_slug"] == "arweave"


def test_hk_tushare_missing_token_is_explicit_credential_error() -> None:
    request = _request(Market.HK, "00700.HK", "HKD")
    adapter = next(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "fundamental.tushare.hk"
    )
    spec = adapter.build_call_specs(request)[0]

    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        adapter.fetch(spec, request)


def test_cn_a_sina_exception_is_not_swallowed_as_success(monkeypatch) -> None:
    request = _request(Market.CN_A, "600519.SH", "CNY")
    adapter = next(
        item
        for item in build_default_fundamental_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "fundamental.cn_a.sina_financials"
    )
    spec = adapter.build_call_specs(request)[0]

    def _raise(*, symbol: str):
        del symbol
        raise RuntimeError("upstream offline")

    monkeypatch.setattr(fundamental, "_call_akshare_cn_a_fundamental", _raise)

    with pytest.raises(RuntimeError, match="upstream offline"):
        adapter.fetch(spec, request)
