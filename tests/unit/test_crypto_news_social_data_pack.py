from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.crypto_news_data_pack import build_crypto_news_data_pack  # noqa: E402
from frontline_data_pack.crypto_social_sentiment_pack import build_crypto_social_sentiment_pack  # noqa: E402


def test_crypto_news_pack_returns_real_provider_attempts_without_turning_search_into_facts(tmp_path: Path) -> None:
    pack = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env=_all_news_keys_and_sources(),
        fetch_json=_fake_fetch_news_success,
        fetch_text=_fake_fetch_text_success,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "ready"
    assert pack["data"]["official_announcements"][0]["source_role"] == "official_announcement_original_source"
    assert pack["data"]["github_releases"][0]["source_role"] == "github_release_original_source"
    assert pack["data"]["exchange_announcements"][0]["source_role"] == "exchange_announcement_original_source"
    assert pack["data"]["regulatory_sources"][0]["source_role"] == "regulatory_original_source"
    assert pack["data"]["defillama_event_background"]["security_incidents"][0]["provider_role"] == "background_event_not_news_fact"
    assert pack["data"]["polymarket_event_expectations"][0]["provider_role"] == "event_expectation_not_news_fact"
    assert pack["data"]["commercial_search_discoveries"][0]["discovery_role"] == "search_discovery_not_verified_fact"
    assert any("商业搜索发现只提供待核验入口" in gap for gap in pack["data_gaps"])
    assert all(attempt["status"] == "success" for attempt in pack["provider_attempts"])
    assert "Exa" in {attempt["provider"] for attempt in pack["provider_attempts"]}
    _assert_reader_brief_has_no_internal_news_field_names(pack["reader_brief"])
    assert (tmp_path / "crypto_news_data_pack.json").is_file()
    assert (tmp_path / "provider_raw" / "crypto_news_data_pack" / "raw_payload.json").is_file()


def test_crypto_news_pack_reuses_json_and_text_provider_cache(tmp_path: Path) -> None:
    cache = _FakeCollection()
    env = _all_news_keys_and_sources()

    first = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path / "first", worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env=env,
        fetch_json=_fake_fetch_news_success,
        fetch_text=_fake_fetch_text_success,
        provider_cache_collection=cache,
    )
    assert first["ok"] is True

    second = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path / "second", worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env=env,
        fetch_json=lambda *_args, **_kwargs: pytest.fail("JSON provider must not be called on fresh cache hit"),
        fetch_text=lambda *_args, **_kwargs: pytest.fail("text provider must not be called on fresh cache hit"),
        provider_cache_collection=cache,
    )

    assert second["ok"] is True
    assert {attempt["status"] for attempt in second["provider_attempts"]} == {"cache_hit"}
    assert {attempt["source_role"] for attempt in second["provider_attempts"]}.issuperset(
        {
            "official_announcement_original_source",
            "github_release_original_source",
            "exchange_announcement_original_source",
            "regulatory_original_source",
            "event_background_not_primary_news_source",
            "event_expectation_not_news_fact",
            "search_discovery_only_not_news_fact",
        }
    )


def test_crypto_news_pack_records_missing_provider_keys_and_does_not_report_no_news(tmp_path: Path) -> None:
    pack = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env={},
        fetch_json=_fake_fetch_empty_public,
    )

    assert pack["ok"] is False
    assert pack["readiness"]["status"] == "insufficient"
    blocked = [attempt for attempt in pack["provider_attempts"] if attempt["status"] == "config_blocked"]
    assert {attempt["provider"] for attempt in blocked}.issuperset({
        "OfficialSource",
        "GitHub",
        "ExchangeAnnouncement",
        "RegulatorySource",
        "DefiLlama",
        "Brave Search",
        "Bocha",
        "NewsAPI",
        "SerpAPI",
        "Tavily",
        "Exa",
    })
    assert any("不得解释为无新闻" in gap for gap in pack["data_gaps"])
    _assert_reader_brief_has_no_internal_news_field_names(pack["reader_brief"])


def test_crypto_news_pack_prefers_runtime_dates_over_tool_dates(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack")
    context["start_date"] = "2026-04-01"
    context["end_date"] = "2026-05-16"
    tool_input = _tool_input("AAVE")
    tool_input["start_date"] = "2025-01-01"
    tool_input["end_date"] = "2025-12-31"

    pack = build_crypto_news_data_pack(
        tool_input,
        context,
        env={},
        fetch_json=_fake_fetch_empty_public,
    )

    assert pack["input"]["start_date"] == "2026-04-01"
    assert pack["input"]["end_date"] == "2026-05-16"


def test_crypto_news_pack_records_invalid_regulatory_config_entry(tmp_path: Path) -> None:
    pack = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env={"CRYPTO_NEWS_REGULATORY_SOURCES_JSON": '[{"name":"Bad Regulatory"}]'},
        fetch_json=_fake_fetch_empty_public,
    )

    attempt = next(
        item
        for item in pack["provider_attempts"]
        if item["provider"] == "Bad Regulatory" and item["role"] == "regulatory_source"
    )
    assert attempt["status"] == "config_blocked"
    assert attempt["error_code"] == "SOURCE_URL_MISSING"


def test_crypto_news_pack_marks_malformed_search_payload_schema_invalid(tmp_path: Path) -> None:
    pack = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env={"BRAVE_SEARCH_API_KEY": "brave-key"},
        fetch_json=_fake_fetch_news_malformed_search,
    )

    attempt = next(item for item in pack["provider_attempts"] if item["provider"] == "Brave Search")
    assert attempt["status"] == "schema_invalid"
    assert attempt["error_code"] == "provider_response_schema_invalid"
    assert pack["data"]["commercial_search_discoveries"] == []


def test_crypto_news_pack_filters_irrelevant_search_discoveries(tmp_path: Path) -> None:
    pack = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env={"BRAVE_SEARCH_API_KEY": "brave-key"},
        fetch_json=_fake_fetch_news_irrelevant_search,
    )

    attempt = next(item for item in pack["provider_attempts"] if item["provider"] == "Brave Search")
    assert attempt["status"] == "empty"
    assert attempt["accepted_count"] == 0
    assert pack["data"]["commercial_search_discoveries"] == []


def test_crypto_news_pack_marks_malformed_polymarket_payload_schema_invalid(tmp_path: Path) -> None:
    pack = build_crypto_news_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_news_data_pack"),
        env={},
        fetch_json=_fake_fetch_malformed_polymarket,
    )

    attempt = next(item for item in pack["provider_attempts"] if item["provider"] == "Polymarket")
    assert attempt["status"] == "schema_invalid"
    assert attempt["error_code"] == "provider_response_schema_invalid"
    assert pack["data"]["polymarket_event_expectations"] == []


def test_crypto_social_pack_returns_market_sentiment_and_explicit_social_platform_gaps(tmp_path: Path) -> None:
    pack = build_crypto_social_sentiment_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="social_analyst", tool_name="crypto_social_sentiment_pack"),
        env=_all_social_keys(),
        fetch_json=_fake_fetch_social_success,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "ready"
    assert pack["data"]["market_level_sentiment"][0]["provider_role"] == (
        "market_level_sentiment_indicator_not_asset_social_sentiment"
    )
    assert pack["data"]["true_social_platform_coverage"]["x"] == "covered_with_provider_payload"
    assert pack["data"]["true_social_platform_coverage"]["reddit"] == "covered_with_provider_payload"
    assert pack["data"]["true_social_platform_coverage"]["telegram"] == "covered_with_provider_payload"
    assert pack["data"]["true_social_platform_coverage"]["discord"] == "covered_with_provider_payload"
    assert pack["data"]["true_social_platform_coverage"]["lunarcrush"] == "covered_with_provider_payload"
    assert pack["data"]["polymarket_event_expectations"][0]["provider_role"] == "event_expectation_not_news_fact"
    assert pack["data"]["public_discussion_discoveries"][0]["discovery_role"] == "search_discovery_not_verified_fact"
    assert "Exa" in {attempt["provider"] for attempt in pack["provider_attempts"]}
    assert any("Alternative.me" in source["provider"] for source in pack["sources"])
    _assert_reader_brief_has_no_internal_social_field_names(pack["reader_brief"])
    assert (tmp_path / "crypto_social_sentiment_pack.json").is_file()
    assert (tmp_path / "provider_raw" / "crypto_social_sentiment_pack" / "raw_payload.json").is_file()


def test_crypto_social_pack_reuses_cache_without_changing_source_boundaries(tmp_path: Path) -> None:
    cache = _FakeCollection()
    env = _all_social_keys()

    first = build_crypto_social_sentiment_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path / "first", worker_id="social_analyst", tool_name="crypto_social_sentiment_pack"),
        env=env,
        fetch_json=_fake_fetch_social_success,
        provider_cache_collection=cache,
    )
    assert first["ok"] is True

    second = build_crypto_social_sentiment_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path / "second", worker_id="social_analyst", tool_name="crypto_social_sentiment_pack"),
        env=env,
        fetch_json=lambda *_args, **_kwargs: pytest.fail("provider must not be called on fresh cache hit"),
        provider_cache_collection=cache,
    )

    assert second["ok"] is True
    assert {attempt["status"] for attempt in second["provider_attempts"]} == {"cache_hit"}
    assert {attempt["source_role"] for attempt in second["provider_attempts"]}.issuperset(
        {
            "market_level_sentiment_not_social_platform",
            "event_expectation_not_social_consensus",
            "search_discovery_only_not_social_consensus",
            "social_platform_metric",
            "social_platform_posts",
        }
    )


def test_crypto_social_pack_records_invalid_discord_channel_config(tmp_path: Path) -> None:
    pack = build_crypto_social_sentiment_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="social_analyst", tool_name="crypto_social_sentiment_pack"),
        env={"DISCORD_BOT_TOKEN": "discord-token", "CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON": '{"AAVE":[{"name":"bad"}]}'},
        fetch_json=_fake_fetch_empty_public,
    )

    attempt = next(item for item in pack["provider_attempts"] if item["provider"] == "Discord")
    assert attempt["status"] == "config_blocked"
    assert attempt["error_code"] == "DISCORD_CHANNEL_ID_MISSING"
    assert pack["data"]["true_social_platform_coverage"]["discord"] == "configured_but_empty_or_failed"


def test_crypto_social_pack_prefers_runtime_dates_over_tool_dates(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path, worker_id="social_analyst", tool_name="crypto_social_sentiment_pack")
    context["start_date"] = "2026-04-01"
    context["end_date"] = "2026-05-16"
    tool_input = _tool_input("AAVE")
    tool_input["start_date"] = "2025-01-01"
    tool_input["end_date"] = "2025-12-31"

    pack = build_crypto_social_sentiment_pack(
        tool_input,
        context,
        env={},
        fetch_json=_fake_fetch_empty_public,
    )

    assert pack["input"]["start_date"] == "2026-04-01"
    assert pack["input"]["end_date"] == "2026-05-16"


def test_crypto_social_pack_marks_malformed_market_sentiment_schema_invalid(tmp_path: Path) -> None:
    pack = build_crypto_social_sentiment_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="social_analyst", tool_name="crypto_social_sentiment_pack"),
        env={},
        fetch_json=_fake_fetch_social_malformed_alternative,
    )

    attempt = next(item for item in pack["provider_attempts"] if item["provider"] == "Alternative.me")
    assert attempt["status"] == "schema_invalid"
    assert attempt["error_code"] == "provider_response_schema_invalid"
    assert pack["data"]["market_level_sentiment"] == []


def test_crypto_social_pack_marks_malformed_polymarket_payload_schema_invalid(tmp_path: Path) -> None:
    pack = build_crypto_social_sentiment_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path, worker_id="social_analyst", tool_name="crypto_social_sentiment_pack"),
        env={},
        fetch_json=_fake_fetch_malformed_polymarket,
    )

    attempt = next(item for item in pack["provider_attempts"] if item["provider"] == "Polymarket")
    assert attempt["status"] == "schema_invalid"
    assert attempt["error_code"] == "provider_response_schema_invalid"
    assert pack["data"]["polymarket_event_expectations"] == []


def test_crypto_social_pack_rejects_wrong_worker_context(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="worker_id=social_analyst"):
        build_crypto_social_sentiment_pack(
            _tool_input("AAVE"),
            _runtime_context(tmp_path, worker_id="news_analyst", tool_name="crypto_social_sentiment_pack"),
            env={},
            fetch_json=_fake_fetch_empty_public,
        )


def _tool_input(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "market": "CRYPTO",
        "company_name": ticker,
        "start_date": "2026-05-01",
        "end_date": "2026-05-15",
        "aliases": ["Aave"] if ticker == "AAVE" else [],
    }


def _runtime_context(tmp_path: Path, *, worker_id: str, tool_name: str) -> dict[str, str]:
    return {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": worker_id,
        "call_id": "call-1",
        "tool_name": tool_name,
        "evidence_root": str(tmp_path),
        "current_date": "2026-05-15",
    }


def _all_search_keys() -> dict[str, str]:
    return {
        "DEFILLAMA_API_KEY": "llama-key",
        "BRAVE_SEARCH_API_KEY": "brave-key",
        "BOCHA_API_KEY": "bocha-key",
        "NEWSAPI_API_KEY": "newsapi-key",
        "SERPAPI_API_KEY": "serpapi-key",
        "TAVILY_API_KEY": "tavily-key",
        "EXA_API_KEY": "exa-key",
    }


def _all_news_keys_and_sources() -> dict[str, str]:
    env = _all_search_keys()
    env.update(
        {
            "CRYPTO_NEWS_OFFICIAL_SOURCES_JSON": '{"AAVE":[{"name":"Aave Blog","url":"https://aave.example/feed.xml"}]}',
            "CRYPTO_NEWS_GITHUB_REPOS_JSON": '{"AAVE":["aave/aave-v3-core"]}',
            "CRYPTO_NEWS_EXCHANGE_SOURCES_JSON": '{"AAVE":[{"name":"Example Exchange","url":"https://exchange.example/aave.xml"}]}',
            "CRYPTO_NEWS_REGULATORY_SOURCES_JSON": '[{"name":"SEC Press Releases","url":"https://sec.example/rss.xml"}]',
            "GITHUB_TOKEN": "github-token",
        }
    )
    return env


def _all_social_keys() -> dict[str, str]:
    env = _all_search_keys()
    env.update(
        {
            "LUNARCRUSH_API_KEY": "lunar-key",
            "X_BEARER_TOKEN": "x-token",
            "REDDIT_BEARER_TOKEN": "reddit-token",
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "DISCORD_BOT_TOKEN": "discord-token",
            "CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON": '{"AAVE":["123456"]}',
        }
    )
    return env


def _assert_reader_brief_has_no_internal_news_field_names(text: str) -> None:
    forbidden = (
        "readiness=",
        "data_gap",
        "data_gaps",
        "provider_attempts",
        "crypto_news_data_pack",
        "partial",
        "insufficient",
        "CRYPTO_NEWS_",
        "feed",
    )
    for token in forbidden:
        assert token not in text


def _assert_reader_brief_has_no_internal_social_field_names(text: str) -> None:
    forbidden = (
        "readiness=",
        "data_gap",
        "data_gaps",
        "provider_attempts",
        "crypto_social_sentiment_pack",
        "partial",
        "insufficient",
        "credential/config",
        "credential",
        "provider key",
        "galaxy_score=",
        "alt_rank=",
        "social_mentions=",
        "value=",
        "classification=",
    )
    for token in forbidden:
        assert token not in text


def _fake_fetch_news_success(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if url.endswith("/api/hacks"):
        return [{"name": "Aave test incident", "date": "2026-05-01", "fundsLost": 1000000, "source": "https://example.com/hack"}]
    if url.endswith("/api/raises"):
        return [{"project": "Aave Labs", "date": "2026-05-02", "raisedAmount": 2000000, "sourceUrl": "https://example.com/raise"}]
    if "api.github.com/repos/aave/aave-v3-core/releases" in url:
        return [
            {
                "tag_name": "v3.1.0",
                "name": "Aave v3.1.0",
                "html_url": "https://github.com/aave/aave-v3-core/releases/tag/v3.1.0",
                "published_at": "2026-05-09T00:00:00Z",
                "prerelease": False,
                "draft": False,
            }
        ]
    if "gamma-api.polymarket.com/public-search" in url:
        return {"events": [_polymarket_event()]}
    if "api.search.brave.com" in url:
        return {"web": {"results": [_search_row("Aave official upgrade coverage")]}}
    if "api.bochaai.com" in url:
        return {"webPages": {"value": [_search_row("Aave exchange announcement clue")]}}
    if "newsapi.org" in url:
        return {"articles": [_search_row("Aave regulatory news clue")]}
    if "serpapi.com" in url:
        return {"news_results": [_search_row("Aave Google News clue")]}
    if "api.tavily.com" in url:
        return {"results": [_search_row("Aave Tavily discovery")]}
    if "api.exa.ai/search" in url:
        return {"results": [_search_row("Aave Exa discovery")]}
    raise AssertionError(url)


def _fake_fetch_social_success(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if "api.alternative.me/fng" in url:
        return {
            "name": "Fear and Greed Index",
            "data": [{"value": "40", "value_classification": "Fear", "timestamp": "1778803200"}],
        }
    if "gamma-api.polymarket.com/public-search" in url:
        return {"events": [_polymarket_event()]}
    if "api.search.brave.com" in url:
        return {"web": {"results": [_search_row("Aave community discussion clue")]}}
    if "api.bochaai.com" in url:
        return {"webPages": {"value": [_search_row("Aave public discussion clue")]}}
    if "serpapi.com" in url:
        return {"organic_results": [_search_row("Aave forum thread clue")]}
    if "api.tavily.com" in url:
        return {"results": [_search_row("Aave narrative discovery")]}
    if "api.exa.ai/search" in url:
        return {"results": [_search_row("Aave Exa discussion discovery")]}
    if "lunarcrush.com" in url:
        return {"data": [{"symbol": "AAVE", "name": "Aave", "galaxy_score": 70, "alt_rank": 20, "social_mentions": 1000}]}
    if "api.x.com/2/tweets/search/recent" in url:
        return {"data": [{"id": "1", "text": "Aave community is debating the new upgrade", "created_at": "2026-05-10T00:00:00Z"}]}
    if "oauth.reddit.com/search" in url:
        return {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "r1",
                            "title": "Aave upgrade discussion",
                            "subreddit": "Aave",
                            "url": "https://reddit.example/aave",
                            "permalink": "/r/Aave/comments/r1",
                            "score": 10,
                            "num_comments": 3,
                        }
                    }
                ]
            }
        }
    if "api.telegram.org/bot" in url:
        return {
            "ok": True,
            "result": [
                {
                    "channel_post": {
                        "message_id": 1,
                        "chat": {"id": "-100", "title": "Aave Channel"},
                        "date": 1778803200,
                        "text": "Aave governance discussion update",
                    }
                }
            ],
        }
    if "discord.com/api/v10/channels/123456/messages" in url:
        return [{"id": "d1", "content": "Aave Discord community discussing risk", "timestamp": "2026-05-10T00:00:00Z"}]
    raise AssertionError(url)


def _fake_fetch_empty_public(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if "gamma-api.polymarket.com/public-search" in url:
        return {"events": []}
    if "api.alternative.me/fng" in url:
        return {"data": []}
    raise AssertionError(url)


def _fake_fetch_news_malformed_search(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if "gamma-api.polymarket.com/public-search" in url:
        return {"events": []}
    if "api.search.brave.com" in url:
        return {"web": {"items": []}}
    raise AssertionError(url)


def _fake_fetch_news_irrelevant_search(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if "gamma-api.polymarket.com/public-search" in url:
        return {"events": []}
    if "api.search.brave.com" in url:
        return {
            "web": {
                "results": [
                    {
                        "title": "Ethereum validator release",
                        "url": "https://example.com/eth",
                        "description": "An unrelated protocol update.",
                    }
                ]
            }
        }
    raise AssertionError(url)


def _fake_fetch_social_malformed_alternative(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if "api.alternative.me/fng" in url:
        return {"unexpected": []}
    if "gamma-api.polymarket.com/public-search" in url:
        return {"events": []}
    raise AssertionError(url)


def _fake_fetch_malformed_polymarket(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if "api.alternative.me/fng" in url:
        return {"data": []}
    if "gamma-api.polymarket.com/public-search" in url:
        return {"markets": []}
    raise AssertionError(url)


def _fake_fetch_text_success(url: str, *, params: Mapping[str, Any], headers: Mapping[str, str], timeout: int) -> str:
    _ = params, headers, timeout
    if url in {"https://aave.example/feed.xml", "https://exchange.example/aave.xml", "https://sec.example/rss.xml"}:
        return """
        <rss><channel>
          <item>
            <title>Aave official upgrade and regulatory notice</title>
            <link>https://example.com/aave-source</link>
            <pubDate>Fri, 10 May 2026 00:00:00 GMT</pubDate>
            <description>Aave source material from an original configured source.</description>
          </item>
        </channel></rss>
        """
    raise AssertionError(url)


def _polymarket_event() -> dict[str, Any]:
    return {
        "title": "Will AAVE approve a major upgrade in 2026?",
        "slug": "will-aave-approve-major-upgrade-2026",
        "active": True,
        "closed": False,
        "liquidity": 10000,
        "volume": 25000,
        "openInterest": 5000,
        "endDate": "2026-12-31T00:00:00Z",
    }


def _search_row(title: str) -> dict[str, Any]:
    return {
        "title": title,
        "name": title,
        "url": "https://example.com/aave",
        "link": "https://example.com/aave",
        "description": "Aave related public search discovery.",
        "content": "Aave related public search discovery.",
        "source": {"name": "Example Source"},
        "publishedAt": "2026-05-10T00:00:00Z",
        "iso_date": "2026-05-10T00:00:00Z",
    }


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.inserted: list[dict[str, Any]] = []

    def find_one(self, query: Mapping[str, Any]) -> dict[str, Any] | None:
        if "_id" in query:
            return self.docs.get(str(query["_id"]))
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    def update_one(self, query: Mapping[str, Any], update: Mapping[str, Any], *, upsert: bool = False) -> None:
        key = str(query["_id"])
        doc = self.docs.get(key)
        if doc is None:
            if not upsert:
                return
            doc = {"_id": key}
            doc.update(update.get("$setOnInsert", {}))
            self.docs[key] = doc
        doc.update(update.get("$set", {}))

    def insert_one(self, document: Mapping[str, Any]) -> None:
        self.inserted.append(dict(document))
