from __future__ import annotations

import pytest

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, ProviderStatus, SourceRole
from claw_trade.data_gateway.providers import news as news_providers
from claw_trade.data_gateway.providers import polymarket as polymarket_providers
from claw_trade.data_gateway.providers import social as social_providers
from claw_trade.data_gateway.providers.news import build_default_news_adapters, news_capabilities
from claw_trade.data_gateway.providers.social import build_default_social_adapters, social_capabilities


def _request(*, market: Market, domain: PackDomain, ticker: str, company_name: str = "Test Co") -> PackRequest:
    return PackRequest(
        run_id=f"run-{market.value.lower()}-{domain.value}",
        call_id=f"call-{market.value.lower()}-{domain.value}",
        worker_id=f"{domain.value}_analyst",
        market=market,
        domain=domain,
        ticker=ticker,
        company_name=company_name,
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def test_news_cn_a_official_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.NEWS, ticker="000001.SZ", company_name="平安银行")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.cninfo.cn_a")
    spec = adapter.build_call_specs(request)[0]

    def _fake_cninfo(*, ticker: str, start_date: str, end_date: str):
        assert ticker == "000001.SZ"
        assert start_date == "2026-05-01"
        assert end_date == "2026-05-17"
        return (
            (
                {
                    "title": "平安银行：年度报告披露",
                    "url": "https://static.cninfo.com.cn/report1.pdf",
                    "published_at": "2026-05-16",
                    "summary": "年度报告",
                },
            ),
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        )

    monkeypatch.setattr(news_providers, "_fetch_cninfo_announcements", _fake_cninfo)
    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert fetch.provider_request_id and fetch.provider_request_id.startswith("req_")
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.news.official.v1"
    assert normalized.row_count == 1
    assert "平安银行" in normalized.rows[0]["title"]


def test_news_crypto_official_unknown_ticker_is_explicit_error() -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.NEWS, ticker="UNKNOWN")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.project_official.crypto")
    spec = adapter.build_call_specs(request)[0]
    with pytest.raises(RuntimeError, match="not configured"):
        adapter.fetch(spec, request)


def test_news_search_capability_is_discovery_only() -> None:
    caps = [cap for cap in news_capabilities() if cap.adapter_id.startswith("news.search.")]
    if not caps:
        caps = [cap for cap in news_capabilities() if cap.adapter_id.startswith("news.google_news.")]
    assert caps
    assert all(cap.source_role == SourceRole.SEARCH_DISCOVERY for cap in caps)
    assert {cap.provider for cap in caps} == {"google_news"}


def test_social_crypto_x_and_lunar_missing_keys_report_missing() -> None:
    adapters = build_default_social_adapters(provider_config_version="cfg", env={})
    x_adapter = next(item for item in adapters if item.adapter_id == "social.x.crypto")
    lunar_adapter = next(item for item in adapters if item.adapter_id == "social.lunarcrush.crypto")

    x_credential = x_adapter.validate_credentials()
    lunar_credential = lunar_adapter.validate_credentials()

    assert x_credential.missing is True
    assert x_credential.status.value == "missing"
    assert x_credential.missing_keys == ("X_BEARER_TOKEN",)
    assert lunar_credential.missing is True
    assert lunar_credential.missing_keys == ("LUNARCRUSH_API_KEY",)


def test_social_alternative_and_polymarket_source_roles_locked() -> None:
    caps = social_capabilities()
    alternative = [cap for cap in caps if "alternative_me" in cap.adapter_id]
    polymarket = [cap for cap in caps if "polymarket" in cap.adapter_id]
    assert alternative
    assert polymarket
    assert all(cap.source_role == SourceRole.SOCIAL_AGGREGATE_METRIC for cap in alternative)
    assert all(cap.source_role == SourceRole.EVENT_EXPECTATION for cap in polymarket)


def test_news_crypto_polymarket_event_expectation_source_role_locked() -> None:
    caps = news_capabilities()
    polymarket = [cap for cap in caps if cap.adapter_id == "news.polymarket.crypto"]

    assert polymarket
    assert all(cap.source_role == SourceRole.EVENT_EXPECTATION for cap in polymarket)


def test_polymarket_public_search_finds_bitcoin_when_ticker_is_btc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_get_json(url: str, *, params=None):
        calls.append((url, dict(params or {})))
        if params and params.get("q") == "Bitcoin":
            return {
                "events": [
                    {
                        "id": "event-1",
                        "slug": "when-will-bitcoin-hit-150k",
                        "title": "When will Bitcoin hit $150k?",
                        "active": True,
                        "closed": False,
                        "endDate": "2026-12-31T00:00:00Z",
                        "markets": [
                            {
                                "question": "Will Bitcoin hit $150k by December 31?",
                                "active": True,
                                "closed": False,
                                "outcomes": '["Yes", "No"]',
                                "outcomePrices": '["0.42", "0.58"]',
                            }
                        ],
                    }
                ]
            }
        return {"events": []}

    monkeypatch.setattr(polymarket_providers, "_http_get_json", _fake_get_json)

    rows, source_url = polymarket_providers.fetch_polymarket_events(
        params={"ticker": "BTC", "company_name": "Bitcoin"},
    )

    assert source_url == "https://gamma-api.polymarket.com/public-search"
    assert calls[0][1]["q"] == "Bitcoin"
    assert len(rows) == 1
    assert "When will Bitcoin hit $150k?" in rows[0]["title"]
    assert "Yes=42.0%" in rows[0]["title"]


def test_social_polymarket_adapter_returns_public_search_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.SOCIAL, ticker="BTC", company_name="Bitcoin")
    adapter = next(item for item in build_default_social_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "social.polymarket.crypto")
    spec = adapter.build_call_specs(request)[0]

    def _fake_polymarket(*, params, limit=10):
        assert params["ticker"] == "BTC"
        return (
            (
                {
                    "title": "When will Bitcoin hit $150k?；Yes=42.0% / No=58.0%",
                    "url": "https://polymarket.com/event/when-will-bitcoin-hit-150k",
                    "published_at": "2026-12-31T00:00:00Z",
                    "summary": "polymarket_event_expectation",
                },
            ),
            "https://gamma-api.polymarket.com/public-search",
        )

    monkeypatch.setattr(social_providers, "fetch_polymarket_events", _fake_polymarket)
    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "crypto.social.event_expectation.v1"
    assert normalized.row_count == 1
    assert "Yes=42.0%" in normalized.rows[0]["title"]


def test_news_polymarket_adapter_returns_event_expectation_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.NEWS, ticker="BTC", company_name="Bitcoin")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.polymarket.crypto")
    spec = adapter.build_call_specs(request)[0]

    def _fake_polymarket(*, params, limit=10):
        assert params["company_name"] == "Bitcoin"
        return (
            (
                {
                    "title": "When will Bitcoin hit $150k?；Yes=42.0% / No=58.0%",
                    "url": "https://polymarket.com/event/when-will-bitcoin-hit-150k",
                    "published_at": "2026-12-31T00:00:00Z",
                    "summary": "polymarket_event_expectation",
                },
            ),
            "https://gamma-api.polymarket.com/public-search",
        )

    monkeypatch.setattr(news_providers, "fetch_polymarket_events", _fake_polymarket)
    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "crypto.news.event_expectation.v1"
    assert normalized.row_count == 1
    assert "When will Bitcoin hit $150k?" in normalized.rows[0]["title"]


def test_social_alternative_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.SOCIAL, ticker="BTC")
    adapter = next(item for item in build_default_social_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "social.alternative_me.crypto")
    spec = adapter.build_call_specs(request)[0]

    def _fake_alternative():
        return (
            (
                {
                    "title": "Fear & Greed 68 (Greed)",
                    "url": "https://alternative.me/crypto/fear-and-greed-index/",
                    "published_at": "1715904000",
                    "summary": "alternative_me_market_sentiment",
                },
            ),
            "https://api.alternative.me/fng/",
        )

    monkeypatch.setattr(social_providers, "_fetch_alternative_me_sentiment", _fake_alternative)
    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "crypto.social.market_sentiment.v1"
    assert normalized.row_count == 1
    assert "Fear & Greed" in normalized.rows[0]["title"]


def test_social_unknown_provider_is_explicit_remote_error_not_search_substitution() -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.SOCIAL, ticker="600519.SH")
    adapter = next(item for item in build_default_social_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "social.xueqiu.cn_a")
    spec = adapter.build_call_specs(request)[0]

    with pytest.raises(RuntimeError, match="not configured for live fetch"):
        adapter.fetch(spec, request)
