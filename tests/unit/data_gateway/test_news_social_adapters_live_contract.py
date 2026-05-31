from __future__ import annotations

import sys
from types import SimpleNamespace

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


def test_news_cn_a_eastmoney_company_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.NEWS, ticker="600519.SH", company_name="贵州茅台")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.eastmoney_company.cn_a")
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    class _Frame:
        def to_dict(self, *, orient: str) -> list[dict[str, str]]:
            assert orient == "records"
            return [
                {
                    "新闻标题": "贵州茅台发布经营动态",
                    "新闻链接": "https://finance.eastmoney.com/a/maotai.html",
                    "发布时间": "2026-05-17 09:00:00",
                    "文章来源": "东方财富",
                }
            ]

    def _fake_stock_news_em(*, symbol: str) -> _Frame:
        seen["symbol"] = symbol
        return _Frame()

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_news_em=_fake_stock_news_em))

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "600519"}
    assert fetch.source_url == "https://quote.eastmoney.com"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.news.company.v1"
    assert normalized.rows[0]["title"] == "贵州茅台发布经营动态"


def test_news_cn_a_akshare_company_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.NEWS, ticker="600519.SH", company_name="贵州茅台")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.akshare_company.cn_a")
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    class _Frame:
        def to_dict(self, *, orient: str) -> list[dict[str, str]]:
            assert orient == "records"
            return [
                {
                    "新闻标题": "AkShare 新闻",
                    "新闻链接": "https://finance.eastmoney.com/a/akshare.html",
                    "发布时间": "2026-05-17 09:00:00",
                }
            ]

    def _fake_stock_news_em(*, symbol: str) -> _Frame:
        seen["symbol"] = symbol
        return _Frame()

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_news_em=_fake_stock_news_em))

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"symbol": "600519"}
    assert fetch.source_url == "https://akshare.akfamily.xyz/data/stock/stock.html"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.rows[0]["title"] == "AkShare 新闻"


def test_news_cn_a_akshare_cninfo_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.NEWS, ticker="600519.SH", company_name="贵州茅台")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.akshare_cninfo.cn_a")
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    class _Frame:
        def to_dict(self, *, orient: str) -> list[dict[str, str]]:
            assert orient == "records"
            return [
                {
                    "公告标题": "贵州茅台：年度报告",
                    "公告链接": "https://www.cninfo.com.cn/new/disclosure/detail",
                    "公告时间": "2026-05-17 00:00:00",
                    "简称": "贵州茅台",
                }
            ]

    def _fake_disclosure(**kwargs: str) -> _Frame:
        seen.update(kwargs)
        return _Frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_disclosure_report_cninfo=_fake_disclosure),
    )

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen["symbol"] == "600519"
    assert seen["market"] == "沪深京"
    assert seen["start_date"] == "20260501"
    assert seen["end_date"] == "20260517"
    assert fetch.source_url == "https://akshare.akfamily.xyz/data/stock/stock.html"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.rows[0]["title"] == "贵州茅台：年度报告"


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


def test_news_us_hk_yahoo_finance_search_is_discovery_only() -> None:
    caps = [
        cap
        for cap in news_capabilities()
        if cap.adapter_id in {"news.yahoo_finance.us", "news.yahoo_finance.hk"}
    ]

    assert {cap.market for cap in caps} == {Market.US, Market.HK}
    assert all(cap.source_role == SourceRole.SEARCH_DISCOVERY for cap in caps)
    assert all(cap.provider == "yahoo_finance" for cap in caps)


def test_news_yahoo_finance_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.US, domain=PackDomain.NEWS, ticker="AAPL", company_name="Apple Inc.")
    adapter = next(
        item
        for item in build_default_news_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "news.yahoo_finance.us"
    )
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, str] = {}

    def _fake_yahoo(*, query: str):
        seen["query"] = query
        return (
            (
                {
                    "title": "Apple supplier news",
                    "url": "https://finance.yahoo.com/news/apple",
                    "published_at": "2026-05-17",
                    "summary": "Yahoo Finance",
                },
            ),
            "https://query2.finance.yahoo.com/v1/finance/search",
        )

    monkeypatch.setattr(news_providers, "_fetch_yahoo_finance_news", _fake_yahoo)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen == {"query": "AAPL Apple Inc."}
    assert fetch.source_url and "query2.finance.yahoo.com" in fetch.source_url
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "us.news.discovery.v1"
    assert normalized.rows[0]["url"] == "https://finance.yahoo.com/news/apple"


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


def test_social_lunarcrush_uses_crypto_base_symbol_for_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.SOCIAL, ticker="AR/USDT", company_name="Arweave")
    adapter = next(
        item
        for item in build_default_social_adapters(provider_config_version="cfg", env={"LUNARCRUSH_API_KEY": "key"})
        if item.adapter_id == "social.lunarcrush.crypto"
    )
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, str] = {}

    def _fake_lunar(*, symbol: str, api_key: str):
        captured["symbol"] = symbol
        captured["api_key"] = api_key
        return (), "https://lunarcrush.com/api4/public/coins/list/v2"

    monkeypatch.setattr(social_providers, "_fetch_lunarcrush_metrics", _fake_lunar)

    adapter.fetch(spec, request)

    assert captured == {"symbol": "AR", "api_key": "key"}


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


def test_crypto_news_official_uses_arweave_repo_for_ar_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.NEWS, ticker="AR/USDT", company_name="Arweave")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.project_official.crypto")
    spec = adapter.build_call_specs(request)[0]
    captured: dict[str, str] = {}

    def _fake_http_get_json(url: str, params=None):
        captured["url"] = url
        return []

    monkeypatch.setattr(news_providers, "_http_get_json", _fake_http_get_json)

    fetch = adapter.fetch(spec, request)

    assert "ArweaveTeam/arweave" in captured["url"]
    assert fetch.source_url == captured["url"]


def test_polymarket_queries_for_ar_pair_use_arweave_not_ambiguous_state_code() -> None:
    queries = polymarket_providers._polymarket_queries(
        {"ticker": "AR/USDT", "company_name": "Arweave", "market": "CRYPTO"}
    )

    assert "Arweave" in queries
    assert "AR crypto" in queries
    assert "AR" not in queries


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


def test_social_cn_a_ths_concept_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.SOCIAL, ticker="600519.SH", company_name="贵州茅台")
    adapter = next(item for item in build_default_social_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "social.ths_concept_hot.cn_a")
    spec = adapter.build_call_specs(request)[0]

    class _Frame:
        def to_dict(self, *, orient: str) -> list[dict[str, str]]:
            assert orient == "records"
            return [{"概念名称": "白酒概念", "涨跌幅": "1.23", "网址": "https://q.10jqka.com.cn/gn/detail/"}]

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_board_concept_name_ths=lambda: _Frame()))

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert fetch.source_url == "https://q.10jqka.com.cn/gn/"
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.social.concept.v1"
    assert "白酒概念" in normalized.rows[0]["title"]


def test_social_cn_a_baidu_concept_fetch_and_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.SOCIAL, ticker="600519.SH", company_name="贵州茅台")
    adapter = next(item for item in build_default_social_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "social.baidu_concept.cn_a")
    spec = adapter.build_call_specs(request)[0]
    seen: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ResultCode": "0",
                "Result": [
                    {
                        "type": "概念板块",
                        "list": [
                            {"name": "白酒概念", "increase": "2.1", "desc": "相关概念"},
                        ],
                    }
                ],
            }

    def _fake_get(url: str, **kwargs: object) -> _Response:
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        return _Response()

    monkeypatch.setattr(social_providers.managed_requests, "get", _fake_get)

    fetch = adapter.fetch(spec, request)
    normalized = adapter.normalize(spec, fetch)

    assert seen["url"] == "https://finance.pae.baidu.com/api/getrelatedblock"
    assert seen["params"] == {"code": "600519", "market": "ab", "typeCode": "all", "finClientType": "pc"}
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS
    assert normalized.schema_id == "cn_a.social.concept.v1"
    assert "白酒概念" in normalized.rows[0]["title"]


def test_social_unwired_provider_is_explicit_remote_error_not_search_substitution() -> None:
    request = _request(market=Market.HK, domain=PackDomain.SOCIAL, ticker="00700.HK")
    adapters = build_default_social_adapters(provider_config_version="cfg", env={})
    assert "social.xueqiu.cn_a" not in {item.adapter_id for item in adapters}
    adapter = next(item for item in adapters if item.adapter_id == "social.xueqiu_hk.hk")
    spec = adapter.build_call_specs(request)[0]

    with pytest.raises(RuntimeError, match="not configured for live fetch"):
        adapter.fetch(spec, request)


def test_social_x_fetch_without_key_is_explicit_credential_error() -> None:
    request = _request(market=Market.CRYPTO, domain=PackDomain.SOCIAL, ticker="BTC", company_name="Bitcoin")
    adapter = next(
        item
        for item in build_default_social_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "social.x.crypto"
    )
    spec = adapter.build_call_specs(request)[0]

    with pytest.raises(RuntimeError, match="credential missing: X_BEARER_TOKEN"):
        adapter.fetch(spec, request)


def test_news_cninfo_exception_is_not_swallowed_as_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(market=Market.CN_A, domain=PackDomain.NEWS, ticker="000001.SZ", company_name="平安银行")
    adapter = next(item for item in build_default_news_adapters(provider_config_version="cfg", env={}) if item.adapter_id == "news.cninfo.cn_a")
    spec = adapter.build_call_specs(request)[0]

    def _raise(*, ticker: str, start_date: str, end_date: str):
        del ticker, start_date, end_date
        raise RuntimeError("cninfo temporary unavailable")

    monkeypatch.setattr(news_providers, "_fetch_cninfo_announcements", _raise)

    with pytest.raises(RuntimeError, match="cninfo temporary unavailable"):
        adapter.fetch(spec, request)
