from __future__ import annotations

from urllib.parse import urlsplit

from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderCallSpec,
    ProviderResult,
    ProviderStatus,
    SourceRole,
    ProviderKind,
)
from claw_trade.data_gateway.packs.policy import normalize_policy_results
from claw_trade.data_gateway.providers.policy import DefaultPolicyAdapter, build_default_policy_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-policy-unit",
        call_id="call-policy-unit",
        worker_id="policy_analyst",
        market=Market.CN_A,
        domain=PackDomain.POLICY,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-22",
        current_date="2026-05-22",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _spec(source_role: SourceRole, coverage_group: str) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"policy:{coverage_group}",
        provider="provider",
        adapter_id=f"policy.{coverage_group}",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg",
        endpoint=coverage_group,
        source_role=source_role,
        market=Market.CN_A,
        domain=PackDomain.POLICY,
        required=False,
        attempt_required=True,
        coverage_group=coverage_group,
        coverage_quorum=1,
        params={},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.policy.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def test_policy_normalizer_keeps_discovery_as_clue_not_fact() -> None:
    request = _request()
    fact_result = ProviderResult(
        spec=_spec(SourceRole.OFFICIAL_ORIGINAL, "cn_a_policy_official"),
        status=ProviderStatus.REMOTE_SUCCESS,
        request_id="req-1",
        requested_at="2026-05-22T00:00:00+00:00",
        latency_ms=1,
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        freshness=FreshnessStatus.FRESH_REMOTE,
        license_note="ok",
        raw_ref="raw://1",
        normalized_ref="norm://1",
        rows=(
            {
                "title": "官方政策公告",
                "url": "https://cninfo.example/1",
                "published_at": "2026-05-20",
                "source": "cninfo",
                "policy_level": "regulatory",
            },
        ),
        row_count=1,
        cache_receipt=None,
        attempt=type("AttemptLike", (), {"attempt_id": "a1"})(),  # type: ignore[arg-type]
    )
    clue_result = ProviderResult(
        spec=_spec(SourceRole.SEARCH_DISCOVERY, "cn_a_policy_discovery"),
        status=ProviderStatus.REMOTE_SUCCESS,
        request_id="req-2",
        requested_at="2026-05-22T00:00:00+00:00",
        latency_ms=1,
        source_role=SourceRole.SEARCH_DISCOVERY,
        freshness=FreshnessStatus.FRESH_REMOTE,
        license_note="ok",
        raw_ref="raw://2",
        normalized_ref="norm://2",
        rows=(
            {
                "title": "搜索线索",
                "url": "https://google.example/1",
                "published_at": "2026-05-19",
                "source": "google_news",
                "policy_level": "discovery",
            },
        ),
        row_count=1,
        cache_receipt=None,
        attempt=type("AttemptLike", (), {"attempt_id": "a2"})(),  # type: ignore[arg-type]
    )

    normalized = normalize_policy_results([fact_result, clue_result])

    assert normalized["fact_event_count"] == 1
    assert normalized["clue_event_count"] == 1
    assert normalized["fact_events"][0]["source_role"] == SourceRole.OFFICIAL_ORIGINAL.value
    assert normalized["clue_events"][0]["source_role"] == SourceRole.SEARCH_DISCOVERY.value
    del request


def test_policy_adapter_requires_published_at_for_official_fact() -> None:
    adapter = DefaultPolicyAdapter(
        adapter_id="policy.cninfo.cn_a",
        provider_id="cninfo",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        endpoint="announcements",
        expected_schema_id="cn_a.policy.official.v1",
        provider_config_version="cfg",
        rate_limit_policy_id="cninfo.policy",
        cache_ttl_seconds=300,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_policy_official",
        coverage_quorum=1,
        priority=0,
    )
    spec = adapter.build_call_specs(_request())[0]
    result = adapter.normalize(
        spec,
        type(
            "FetchLike",
            (),
            {
                "payload": {
                    "rows": [
                        {
                            "title": "无日期官方公告",
                            "url": "https://cninfo.example/missing-date",
                            "source": "cninfo",
                        }
                    ]
                },
                "provider_request_id": "req-missing-date",
                "source_url": "https://cninfo.example/api",
                "row_count": 1,
            },
        )(),
    )
    assert result.status == ProviderStatus.FIELD_MISSING
    assert "published_at" in result.missing_fields


def test_policy_provider_source_claim_matches_fetch_url_domain_and_discovery_stays_clue(monkeypatch) -> None:
    import claw_trade.data_gateway.providers.policy as policy_mod

    adapters = build_default_policy_adapters(provider_config_version="cfg-policy-source-claim")
    by_group = {adapter.coverage_group: adapter for adapter in adapters}
    request = _request()
    called_get: list[str] = []
    called_post: list[str] = []

    class _Response:
        def __init__(self, *, json_body=None, text: str = "") -> None:
            self._json_body = json_body
            self.text = text

        def raise_for_status(self) -> None:
            return None

        def json(self):
            if self._json_body is None:
                raise RuntimeError("json body is not set")
            return self._json_body

    def _fake_post(url, data=None, headers=None, timeout=None):  # noqa: ANN001
        del data, headers, timeout
        called_post.append(url)
        return _Response(
            json_body={
                "announcements": [
                    {
                        "announcementTitle": "政策公告",
                        "announcementTime": "2026-05-20",
                        "plate": "sz",
                        "orgId": "gssz0000001",
                        "announcementId": "12345",
                    }
                ]
            }
        )

    def _fake_get(url, headers=None, timeout=None):  # noqa: ANN001
        del headers, timeout
        called_get.append(url)
        if "newsapi.eastmoney.com" in url:
            return _Response(
                text=(
                    'var ajaxResult={"LivesList":[{"title":"政策新闻",'
                    '"url":"https://finance.eastmoney.com/a/20260520.html",'
                    '"showtime":"2026-05-20"}]};'
                )
            )
        if "news.google.com" in url:
            return _Response(
                text=(
                    "<rss><channel><item>"
                    "<title>搜索线索</title>"
                    "<link>https://example.org/policy-clue</link>"
                    "<pubDate>Tue, 20 May 2026 10:00:00 GMT</pubDate>"
                    "</item></channel></rss>"
                )
            )
        raise AssertionError(f"unexpected GET url: {url}")

    monkeypatch.setattr(policy_mod.requests, "post", _fake_post)
    monkeypatch.setattr(policy_mod.requests, "get", _fake_get)

    expected = {
        "cn_a_policy_official": ("cninfo", SourceRole.OFFICIAL_ORIGINAL, "www.cninfo.com.cn"),
        "cn_a_policy_news": ("eastmoney_news", SourceRole.MARKET_DATA, "newsapi.eastmoney.com"),
        "cn_a_policy_macro": ("eastmoney_macro", SourceRole.MACRO_DATA, "newsapi.eastmoney.com"),
        "cn_a_policy_discovery": ("google_news", SourceRole.SEARCH_DISCOVERY, "news.google.com"),
    }

    for group, (provider_id, source_role, expected_host) in expected.items():
        adapter = by_group[group]
        spec = adapter.build_call_specs(request)[0]
        fetch = adapter.fetch(spec, request)
        normalized = adapter.normalize(spec, fetch)

        assert adapter.provider_id == provider_id
        assert adapter.source_role == source_role
        assert urlsplit(fetch.source_url).netloc == expected_host
        assert normalized.status == ProviderStatus.REMOTE_SUCCESS

        if group == "cn_a_policy_discovery":
            assert normalized.rows
            assert all(row["source_role"] == SourceRole.SEARCH_DISCOVERY.value for row in normalized.rows)
            assert all(row["fact_tier"] == "clue" for row in normalized.rows)
        else:
            assert normalized.rows
            assert all(row["fact_tier"] == "fact" for row in normalized.rows)

    assert any("cninfo.com.cn" in url for url in called_post)
    assert any("newsapi.eastmoney.com" in url for url in called_get)
    assert any("news.google.com" in url for url in called_get)
