from __future__ import annotations

from email.utils import parsedate_to_datetime
import html
import json
import os
import re
from typing import Any, Mapping
from urllib.parse import quote
import xml.etree.ElementTree as ET

from .crypto_pack_common import (
    DEFAULT_TIMEOUT_SECONDS,
    FetchJson,
    FetchText,
    blocked_attempt,
    compact_str_list,
    configured_entries,
    env_text,
    finish_attempt,
    first_env,
    matches_request,
    normalize_crypto_input,
    normalize_frontline_context,
    number_or_none,
    requests_json,
    requests_text,
    search_terms,
    str_or_none,
    utc_now,
    write_pack_files,
)
from .crypto_provider_cache import (
    commit_crypto_provider_cache,
    fetch_json_with_crypto_provider_cache,
    fetch_text_with_crypto_provider_cache,
    insert_crypto_provider_attempts,
    provider_attempt_gap_messages,
    resolve_crypto_provider_cache_collections,
)


_SCHEMA_VERSION = "crypto_news_data_pack.v1"
_TOOL_NAME = "crypto_news_data_pack"
_WORKER_ID = "news_analyst"


def run_crypto_news_data_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    return build_crypto_news_data_pack(tool_input, runtime_context)


def build_crypto_news_data_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    fetch_json: FetchJson | None = None,
    fetch_text: FetchText | None = None,
    provider_cache_collection: Any | None = None,
    provider_attempts_collection: Any | None = None,
    provider_rate_limit_collection: Any | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    fetch_json = requests_json if fetch_json is None else fetch_json
    fetch_text = requests_text if fetch_text is None else fetch_text
    context = normalize_frontline_context(runtime_context, tool_name=_TOOL_NAME, worker_id=_WORKER_ID)
    request = normalize_crypto_input(tool_input, context)
    if provider_cache_collection is None and provider_attempts_collection is None and provider_rate_limit_collection is None:
        collections = resolve_crypto_provider_cache_collections(env)
        provider_cache_collection = collections.cache
        provider_attempts_collection = collections.attempts
        provider_rate_limit_collection = collections.rate_limits

    attempts: list[dict[str, Any]] = []
    raw_payload: dict[str, Any] = {}

    official = _load_configured_text_sources(
        request,
        context=context,
        env=env,
        fetch_text=fetch_text,
        attempts=attempts,
        env_field="CRYPTO_NEWS_OFFICIAL_SOURCES_JSON",
        provider="OfficialSource",
        role="official_announcement",
        source_role="official_announcement_original_source",
        missing_code="CRYPTO_NEWS_OFFICIAL_SOURCES_MISSING",
        missing_message="Configure CRYPTO_NEWS_OFFICIAL_SOURCES_JSON with official announcement/blog/RSS URLs per ticker.",
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["official_sources"] = official["raw"]

    github = _load_github_releases(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["github"] = github["raw"]

    exchange = _load_configured_text_sources(
        request,
        context=context,
        env=env,
        fetch_text=fetch_text,
        attempts=attempts,
        env_field="CRYPTO_NEWS_EXCHANGE_SOURCES_JSON",
        provider="ExchangeAnnouncement",
        role="exchange_announcement",
        source_role="exchange_announcement_original_source",
        missing_code="CRYPTO_NEWS_EXCHANGE_SOURCES_MISSING",
        missing_message="Configure CRYPTO_NEWS_EXCHANGE_SOURCES_JSON with exchange announcement URLs per ticker.",
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["exchange_sources"] = exchange["raw"]

    regulatory = _load_regulatory_sources(
        request,
        context=context,
        env=env,
        fetch_text=fetch_text,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["regulatory_sources"] = regulatory["raw"]

    defillama = _load_defillama_background(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["defillama"] = defillama["raw"]

    polymarket = _load_polymarket_expectations(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["polymarket"] = polymarket["raw"]

    search = _load_commercial_search_discoveries(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["commercial_search"] = search["raw"]

    data = {
        "official_announcements": official["items"],
        "github_releases": github["items"],
        "exchange_announcements": exchange["items"],
        "regulatory_sources": regulatory["items"],
        "defillama_event_background": {
            "security_incidents": defillama["security_incidents"],
            "funding_events": defillama["funding_events"],
        },
        "polymarket_event_expectations": polymarket["events"],
        "commercial_search_discoveries": search["discoveries"],
    }
    sources = _build_sources(official, github, exchange, regulatory, defillama, polymarket, search)
    data_gaps = _build_data_gaps(official, github, exchange, regulatory, defillama, polymarket, search, attempts)
    data_gaps.extend(provider_attempt_gap_messages(attempts))
    readiness = _build_readiness(data)
    quality = _build_quality(readiness, data_gaps)
    pack = {
        "ok": readiness["status"] != "insufficient",
        "schema_version": _SCHEMA_VERSION,
        "tool_name": _TOOL_NAME,
        "domain": "news",
        "asset": request["ticker"],
        "market": "CRYPTO",
        "as_of": utc_now(),
        "input": request,
        "data": data,
        "sources": sources,
        "provider_attempts": attempts,
        "data_gaps": data_gaps,
        "conflicts": [],
        "readiness": readiness,
        "quality": quality,
    }
    pack["reader_brief"] = _build_reader_brief(pack)
    write_pack_files(context, tool_name=_TOOL_NAME, pack=pack, raw_payload=raw_payload)
    insert_crypto_provider_attempts(attempts, collection=provider_attempts_collection)
    return pack


def _cached_json_fetch(
    *,
    request: Mapping[str, Any],
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
    provider: str,
    endpoint: str,
    role: str,
    source_role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    auth_mode: str,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="news",
        provider=provider,
        endpoint=endpoint,
        role=role,
        source_role=source_role,
        url=url,
        params=params,
        headers=headers,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_json=fetch_json,
        env=env,
        method=method,
        json_body=json_body,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    return result.payload, result.attempts


def _cached_text_fetch(
    *,
    request: Mapping[str, Any],
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_text: FetchText,
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
    provider: str,
    endpoint: str,
    role: str,
    source_role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    auth_mode: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    result = fetch_text_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="news",
        provider=provider,
        endpoint=endpoint,
        role=role,
        source_role=source_role,
        url=url,
        params=params,
        headers=headers,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_text=fetch_text,
        env=env,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    return result.payload, result.attempts


def _finish_cached_attempts(
    *,
    attempts: list[dict[str, Any]],
    fetch_attempts: list[dict[str, Any]],
    payload: Any,
    accepted_count: int,
    provider_cache_collection: Any | None,
    schema_valid: bool = True,
) -> None:
    if fetch_attempts:
        finish_attempt(fetch_attempts[-1], payload, accepted_count=accepted_count, schema_valid=schema_valid)
        commit_crypto_provider_cache(attempt=fetch_attempts[-1], payload=payload, collection=provider_cache_collection)
    attempts.extend(fetch_attempts)


def _load_configured_text_sources(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_text: FetchText,
    attempts: list[dict[str, Any]],
    env_field: str,
    provider: str,
    role: str,
    source_role: str,
    missing_code: str,
    missing_message: str,
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    entries = configured_entries(env, env_field, request)
    if not entries:
        attempts.append(
            blocked_attempt(
                provider=provider,
                endpoint=env_field,
                role=role,
                auth_mode="config_missing",
                error_code=missing_code,
                error_message=missing_message,
            )
        )
        return {"items": [], "raw": {}}

    items: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for index, entry in enumerate(entries, start=1):
        url = str_or_none(entry.get("url"))
        if not url:
            attempts.append(
                blocked_attempt(
                    provider=provider,
                    endpoint=env_field,
                    role=role,
                    auth_mode="config_invalid",
                    error_code="SOURCE_URL_MISSING",
                    error_message=f"{env_field} entry {index} is missing url.",
                )
            )
            continue
        source_name = str_or_none(entry.get("name")) or provider
        payload, fetch_attempts = _cached_text_fetch(
            request=request,
            context=context,
            env=env,
            fetch_text=fetch_text,
            provider_cache_collection=provider_cache_collection,
            provider_rate_limit_collection=provider_rate_limit_collection,
            provider=source_name,
            endpoint=url,
            role=role,
            source_role=source_role,
            url=url,
            params={},
            headers=_source_headers(env),
            auth_mode="public_or_configured_url",
        )
        normalized = _normalize_text_source(source_name, url, payload, request, role=role)
        raw[source_name] = _raw_text_payload(payload)
        items.extend(normalized)
        _finish_cached_attempts(
            attempts=attempts,
            fetch_attempts=fetch_attempts,
            payload=payload,
            accepted_count=len(normalized),
            provider_cache_collection=provider_cache_collection,
        )
    return {"items": items[:25], "raw": raw}


def _load_github_releases(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    repos = configured_entries(env, "CRYPTO_NEWS_GITHUB_REPOS_JSON", request)
    if not repos:
        attempts.append(
            blocked_attempt(
                provider="GitHub",
                endpoint="CRYPTO_NEWS_GITHUB_REPOS_JSON",
                role="github_release",
                auth_mode="config_missing",
                error_code="CRYPTO_NEWS_GITHUB_REPOS_MISSING",
                error_message="Configure CRYPTO_NEWS_GITHUB_REPOS_JSON with owner/repo entries per ticker.",
            )
        )
        return {"items": [], "raw": {}}
    token, token_name = first_env(env, ("GITHUB_TOKEN", "GH_TOKEN"))
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for entry in repos:
        repo = str_or_none(entry.get("repo") or entry.get("url"))
        if repo and repo.startswith("https://github.com/"):
            repo = repo.removeprefix("https://github.com/").strip("/")
        if not repo or "/" not in repo:
            attempts.append(
                blocked_attempt(
                    provider="GitHub",
                    endpoint="CRYPTO_NEWS_GITHUB_REPOS_JSON",
                    role="github_release",
                    auth_mode="config_invalid",
                    error_code="GITHUB_REPO_INVALID",
                    error_message="GitHub repo entries must be owner/repo or https://github.com/owner/repo.",
                )
            )
            continue
        url = f"https://api.github.com/repos/{quote(repo, safe='/')}/releases"
        payload, fetch_attempts = _cached_json_fetch(
            request=request,
            context=context,
            env=env,
            fetch_json=fetch_json,
            provider_cache_collection=provider_cache_collection,
            provider_rate_limit_collection=provider_rate_limit_collection,
            provider="GitHub",
            endpoint="/repos/{owner}/{repo}/releases",
            role="github_release",
            source_role="github_release_original_source",
            url=url,
            params={"per_page": 10},
            headers=headers,
            auth_mode=f"{token_name}_bearer" if token_name else "public_no_key",
        )
        normalized = _normalize_github_releases(repo, payload, request)
        raw[repo] = payload
        items.extend(normalized)
        _finish_cached_attempts(
            attempts=attempts,
            fetch_attempts=fetch_attempts,
            payload=payload,
            accepted_count=len(normalized),
            provider_cache_collection=provider_cache_collection,
            schema_valid=isinstance(payload, list),
        )
    return {"items": items[:25], "raw": raw}


def _load_regulatory_sources(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_text: FetchText,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    entries = configured_entries(env, "CRYPTO_NEWS_REGULATORY_SOURCES_JSON", request)
    if not entries and (env_text(env, "CRYPTO_NEWS_ENABLE_DEFAULT_REGULATORY_SOURCES") or "").lower() == "true":
        entries = [
            {"name": "SEC Press Releases", "url": "https://www.sec.gov/news/pressreleases.rss"},
            {"name": "CFTC Press Releases", "url": "https://www.cftc.gov/PressRoom/PressReleases/rss.xml"},
            {"name": "Federal Reserve Press Releases", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
        ]
    if not entries:
        attempts.append(
            blocked_attempt(
                provider="RegulatorySource",
                endpoint="CRYPTO_NEWS_REGULATORY_SOURCES_JSON",
                role="regulatory_source",
                auth_mode="config_missing",
                error_code="CRYPTO_NEWS_REGULATORY_SOURCES_MISSING",
                error_message="Configure CRYPTO_NEWS_REGULATORY_SOURCES_JSON or set CRYPTO_NEWS_ENABLE_DEFAULT_REGULATORY_SOURCES=true.",
            )
        )
        return {"items": [], "raw": {}}
    items: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for entry in entries:
        url = str_or_none(entry.get("url"))
        source_name = str_or_none(entry.get("name")) or "RegulatorySource"
        if not url:
            attempts.append(
                blocked_attempt(
                    provider=source_name,
                    endpoint="CRYPTO_NEWS_REGULATORY_SOURCES_JSON",
                    role="regulatory_source",
                    auth_mode="config_invalid",
                    error_code="SOURCE_URL_MISSING",
                    error_message="CRYPTO_NEWS_REGULATORY_SOURCES_JSON entry is missing url.",
                )
            )
            continue
        payload, fetch_attempts = _cached_text_fetch(
            request=request,
            context=context,
            env=env,
            fetch_text=fetch_text,
            provider_cache_collection=provider_cache_collection,
            provider_rate_limit_collection=provider_rate_limit_collection,
            provider=source_name,
            endpoint=url,
            role="regulatory_source",
            source_role="regulatory_original_source",
            url=url,
            params={},
            headers=_source_headers(env),
            auth_mode="public_or_configured_url",
        )
        normalized = _normalize_text_source(source_name, url, payload, request, role="regulatory_source")
        raw[source_name] = _raw_text_payload(payload)
        items.extend(normalized)
        _finish_cached_attempts(
            attempts=attempts,
            fetch_attempts=fetch_attempts,
            payload=payload,
            accepted_count=len(normalized),
            provider_cache_collection=provider_cache_collection,
        )
    return {"items": items[:25], "raw": raw}


def _load_defillama_background(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    api_key = env_text(env, "DEFILLAMA_API_KEY")
    if not api_key:
        attempts.append(
            blocked_attempt(
                provider="DefiLlama",
                endpoint="/api/hacks",
                role="security_event_background",
                auth_mode="credential_missing",
                error_code="DEFILLAMA_API_KEY_MISSING",
                error_message="DefiLlama hacks endpoint is treated as Pro API in this pack and requires DEFILLAMA_API_KEY.",
            )
        )
        attempts.append(
            blocked_attempt(
                provider="DefiLlama",
                endpoint="/api/raises",
                role="funding_event_background",
                auth_mode="credential_missing",
                error_code="DEFILLAMA_API_KEY_MISSING",
                error_message="DefiLlama raises endpoint is treated as Pro API in this pack and requires DEFILLAMA_API_KEY.",
            )
        )
        return {"security_incidents": [], "funding_events": [], "raw": {}}

    base_url = f"https://pro-api.llama.fi/{quote(api_key, safe='')}"
    hacks_payload, hacks_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="DefiLlama",
        endpoint="/api/hacks",
        role="security_event_background",
        source_role="event_background_not_primary_news_source",
        url=f"{base_url}/api/hacks",
        params={},
        headers={},
        auth_mode="pro_path_key",
    )
    security_incidents = _normalize_defillama_events(hacks_payload, request, event_type="security_incident")
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=hacks_attempts,
        payload=hacks_payload,
        accepted_count=len(security_incidents),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_defillama_events_payload_schema_valid(hacks_payload),
    )

    raises_payload, raises_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="DefiLlama",
        endpoint="/api/raises",
        role="funding_event_background",
        source_role="event_background_not_primary_news_source",
        url=f"{base_url}/api/raises",
        params={},
        headers={},
        auth_mode="pro_path_key",
    )
    funding_events = _normalize_defillama_events(raises_payload, request, event_type="funding_event")
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=raises_attempts,
        payload=raises_payload,
        accepted_count=len(funding_events),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_defillama_events_payload_schema_valid(raises_payload),
    )
    return {
        "security_incidents": security_incidents,
        "funding_events": funding_events,
        "raw": {"hacks": hacks_payload, "raises": raises_payload},
    }


def _load_polymarket_expectations(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    query = " ".join([*search_terms(request), "crypto"])
    payload, fetch_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="Polymarket",
        endpoint="/public-search",
        role="event_expectation",
        source_role="event_expectation_not_news_fact",
        url="https://gamma-api.polymarket.com/public-search",
        params={
            "q": query,
            "limit_per_type": 10,
            "events_status": "active",
            "search_tags": "true",
            "search_profiles": "false",
        },
        headers={},
        auth_mode="public_no_key",
    )
    events = _normalize_polymarket_events(payload, request)
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=fetch_attempts,
        payload=payload,
        accepted_count=len(events),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_polymarket_payload_schema_valid(payload),
    )
    return {"events": events, "raw": payload}


def _load_commercial_search_discoveries(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    query = (
        f"{' '.join(search_terms(request))} crypto official announcement exchange listing "
        "regulation SEC CFTC hack funding upgrade"
    )
    discoveries: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}

    provider_specs = (
        _brave_spec(env, query),
        _bocha_spec(env, query),
        _newsapi_spec(env, query, request),
        _serpapi_spec(env, query),
        _tavily_spec(env, query),
        _exa_spec(env, query),
    )
    for spec in provider_specs:
        if spec["key_missing"]:
            attempts.append(
                blocked_attempt(
                    provider=spec["provider"],
                    endpoint=spec["endpoint"],
                    role="commercial_search_discovery",
                    auth_mode="credential_missing",
                    error_code=spec["missing_code"],
                    error_message=spec["missing_message"],
                )
            )
            continue
        payload, fetch_attempts = _cached_json_fetch(
            request=request,
            context=context,
            env=env,
            fetch_json=fetch_json,
            provider_cache_collection=provider_cache_collection,
            provider_rate_limit_collection=provider_rate_limit_collection,
            provider=spec["provider"],
            endpoint=spec["endpoint"],
            role="commercial_search_discovery",
            source_role="search_discovery_only_not_news_fact",
            url=spec["url"],
            params=spec["params"],
            headers=spec["headers"],
            auth_mode=spec["auth_mode"],
            method=spec["method"],
            json_body=spec["json_body"],
        )
        normalized = [item for item in _normalize_search_results(spec["provider"], payload) if _discovery_matches(item, request)]
        raw[spec["provider"].lower()] = payload
        discoveries.extend(normalized)
        _finish_cached_attempts(
            attempts=attempts,
            fetch_attempts=fetch_attempts,
            payload=payload,
            accepted_count=len(normalized),
            provider_cache_collection=provider_cache_collection,
            schema_valid=_search_payload_schema_valid(spec["provider"], payload),
        )
    return {"discoveries": discoveries[:25], "raw": raw}


def _brave_spec(env: Mapping[str, str], query: str) -> dict[str, Any]:
    key, key_name = first_env(env, ("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY"))
    return {
        "provider": "Brave Search",
        "endpoint": "/res/v1/web/search",
        "url": "https://api.search.brave.com/res/v1/web/search",
        "params": {"q": query, "count": 8, "country": "us", "search_lang": "en"},
        "headers": {"X-Subscription-Token": key} if key else {},
        "method": "GET",
        "json_body": None,
        "auth_mode": f"{key_name}_header" if key_name else "credential_missing",
        "key_missing": not key,
        "missing_code": "BRAVE_SEARCH_API_KEY_MISSING",
        "missing_message": "Brave Search requires BRAVE_SEARCH_API_KEY or BRAVE_API_KEY.",
    }


def _bocha_spec(env: Mapping[str, str], query: str) -> dict[str, Any]:
    key = env_text(env, "BOCHA_API_KEY")
    return {
        "provider": "Bocha",
        "endpoint": "/v1/web-search",
        "url": "https://api.bochaai.com/v1/web-search",
        "params": {},
        "headers": {"Authorization": f"Bearer {key}", "Content-Type": "application/json"} if key else {},
        "method": "POST",
        "json_body": {"query": query, "freshness": "oneMonth", "summary": True, "count": 8},
        "auth_mode": "bearer_header" if key else "credential_missing",
        "key_missing": not key,
        "missing_code": "BOCHA_API_KEY_MISSING",
        "missing_message": "Bocha search requires BOCHA_API_KEY.",
    }


def _newsapi_spec(env: Mapping[str, str], query: str, request: Mapping[str, Any]) -> dict[str, Any]:
    key, key_name = first_env(env, ("NEWSAPI_API_KEY", "NEWS_API_KEY"))
    params: dict[str, Any] = {
        "q": query,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 10,
    }
    if request.get("start_date"):
        params["from"] = request["start_date"]
    if request.get("end_date"):
        params["to"] = request["end_date"]
    if key:
        params["apiKey"] = key
    return {
        "provider": "NewsAPI",
        "endpoint": "/v2/everything",
        "url": "https://newsapi.org/v2/everything",
        "params": params,
        "headers": {},
        "method": "GET",
        "json_body": None,
        "auth_mode": f"{key_name}_query" if key_name else "credential_missing",
        "key_missing": not key,
        "missing_code": "NEWSAPI_API_KEY_MISSING",
        "missing_message": "NewsAPI requires NEWSAPI_API_KEY or NEWS_API_KEY; Developer tier is not a production real-time source.",
    }


def _serpapi_spec(env: Mapping[str, str], query: str) -> dict[str, Any]:
    key = env_text(env, "SERPAPI_API_KEY")
    params = {"engine": "google_news", "q": query, "gl": "us", "hl": "en", "so": "1"}
    if key:
        params["api_key"] = key
    return {
        "provider": "SerpAPI",
        "endpoint": "/search?engine=google_news",
        "url": "https://serpapi.com/search",
        "params": params,
        "headers": {},
        "method": "GET",
        "json_body": None,
        "auth_mode": "api_key_query" if key else "credential_missing",
        "key_missing": not key,
        "missing_code": "SERPAPI_API_KEY_MISSING",
        "missing_message": "SerpAPI Google News requires SERPAPI_API_KEY.",
    }


def _tavily_spec(env: Mapping[str, str], query: str) -> dict[str, Any]:
    key = env_text(env, "TAVILY_API_KEY")
    return {
        "provider": "Tavily",
        "endpoint": "/search",
        "url": "https://api.tavily.com/search",
        "params": {},
        "headers": {"Authorization": f"Bearer {key}", "Content-Type": "application/json"} if key else {},
        "method": "POST",
        "json_body": {"query": query, "search_depth": "basic", "topic": "news", "max_results": 8},
        "auth_mode": "bearer_header" if key else "credential_missing",
        "key_missing": not key,
        "missing_code": "TAVILY_API_KEY_MISSING",
        "missing_message": "Tavily search requires TAVILY_API_KEY.",
    }


def _exa_spec(env: Mapping[str, str], query: str) -> dict[str, Any]:
    key = env_text(env, "EXA_API_KEY")
    return {
        "provider": "Exa",
        "endpoint": "/search",
        "url": "https://api.exa.ai/search",
        "params": {},
        "headers": {"x-api-key": key, "Content-Type": "application/json"} if key else {},
        "method": "POST",
        "json_body": {"query": query, "type": "auto", "numResults": 8, "text": False},
        "auth_mode": "x_api_key_header" if key else "credential_missing",
        "key_missing": not key,
        "missing_code": "EXA_API_KEY_MISSING",
        "missing_message": "Exa search requires EXA_API_KEY.",
    }


def _normalize_defillama_events(payload: Any, request: Mapping[str, Any], *, event_type: str) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, Mapping) else []
    if not isinstance(rows, list):
        return []
    events: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping) or not matches_request(item, request):
            continue
        events.append(
            {
                "event_type": event_type,
                "name": str_or_none(item.get("name") or item.get("project") or item.get("protocol")),
                "date": str_or_none(item.get("date") or item.get("timestamp")),
                "amount_usd": number_or_none(item.get("amount") or item.get("fundsLost") or item.get("raisedAmount")),
                "chain": str_or_none(item.get("chain")),
                "category": str_or_none(item.get("category") or item.get("classification")),
                "source_url": str_or_none(item.get("source") or item.get("sourceUrl") or item.get("url")),
                "provider_role": "background_event_not_news_fact",
            }
        )
        if len(events) >= 8:
            break
    return events


def _defillama_events_payload_schema_valid(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    if isinstance(payload, Mapping):
        return isinstance(payload.get("data"), list)
    return False


def _normalize_text_source(
    provider: str,
    url: str,
    payload: str | None,
    request: Mapping[str, Any],
    *,
    role: str,
) -> list[dict[str, Any]]:
    if not payload:
        return []
    entries = _parse_feed_entries(payload)
    if not entries:
        entries = [_parse_page_entry(payload, url)]

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not _entry_matches(entry, request, include_crypto_terms=role == "regulatory_source"):
            continue
        normalized.append(
            {
                "provider": provider,
                "title": entry.get("title"),
                "url": entry.get("url") or url,
                "published_at": entry.get("published_at"),
                "summary": entry.get("summary"),
                "source_role": _source_role(role),
                "verification_status": "source_original_or_configured",
            }
        )
        if len(normalized) >= 10:
            break
    return normalized


def _normalize_github_releases(repo: str, payload: Any, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    releases: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        if not _entry_matches(item, request):
            continue
        releases.append(
            {
                "provider": "GitHub",
                "repo": repo,
                "tag_name": str_or_none(item.get("tag_name")),
                "name": str_or_none(item.get("name")),
                "url": str_or_none(item.get("html_url")),
                "published_at": str_or_none(item.get("published_at")),
                "prerelease": item.get("prerelease") if isinstance(item.get("prerelease"), bool) else None,
                "draft": item.get("draft") if isinstance(item.get("draft"), bool) else None,
                "source_role": "github_release_original_source",
                "verification_status": "github_api_release",
            }
        )
        if len(releases) >= 10:
            break
    return releases


def _parse_feed_entries(payload: str) -> list[dict[str, str | None]]:
    try:
        root = ET.fromstring(payload.encode("utf-8"))
    except ET.ParseError:
        return []
    entries: list[dict[str, str | None]] = []
    for item in root.findall(".//item"):
        entries.append(
            {
                "title": _xml_text(item, "title"),
                "url": _xml_text(item, "link"),
                "published_at": _normalize_date(_xml_text(item, "pubDate")),
                "summary": _clean_text(_xml_text(item, "description")),
            }
        )
    if entries:
        return entries
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//atom:entry", ns):
        link = item.find("atom:link", ns)
        entries.append(
            {
                "title": _xml_text(item, "atom:title", ns),
                "url": link.get("href") if link is not None else None,
                "published_at": _normalize_date(_xml_text(item, "atom:updated", ns) or _xml_text(item, "atom:published", ns)),
                "summary": _clean_text(_xml_text(item, "atom:summary", ns) or _xml_text(item, "atom:content", ns)),
            }
        )
    return entries


def _parse_page_entry(payload: str, url: str) -> dict[str, str | None]:
    title_match = re.search(r"<title[^>]*>(?P<title>.*?)</title>", payload, flags=re.IGNORECASE | re.DOTALL)
    description_match = re.search(
        r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](?P<content>.*?)[\"']",
        payload,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return {
        "title": _clean_text(title_match.group("title")) if title_match else url,
        "url": url,
        "published_at": None,
        "summary": _clean_text(description_match.group("content")) if description_match else None,
    }


def _xml_text(item: ET.Element, name: str, ns: Mapping[str, str] | None = None) -> str | None:
    child = item.find(name, ns or {})
    if child is None or child.text is None:
        return None
    return _clean_text(child.text)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] if text else None


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return value


def _entry_matches(entry: Mapping[str, Any], request: Mapping[str, Any], *, include_crypto_terms: bool = False) -> bool:
    if matches_request(entry, request):
        return True
    if include_crypto_terms:
        text = json.dumps(entry, ensure_ascii=False).lower()
        return any(term in text for term in ("bitcoin", "ether", "ethereum", "crypto", "digital asset", "stablecoin"))
    return False


def _source_role(role: str) -> str:
    return {
        "official_announcement": "official_announcement_original_source",
        "exchange_announcement": "exchange_announcement_original_source",
        "regulatory_source": "regulatory_original_source",
    }.get(role, role)


def _raw_text_payload(payload: str | None) -> dict[str, Any]:
    if payload is None:
        return {}
    return {"text_sample": payload[:4000], "length": len(payload)}


def _source_headers(env: Mapping[str, str]) -> dict[str, str]:
    user_agent = env_text(env, "CRYPTO_NEWS_USER_AGENT") or "claw-trade/crypto-news-data-pack"
    return {"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, text/html, */*"}


def _normalize_polymarket_events(payload: Any, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("events") if isinstance(payload, Mapping) else []
    if not isinstance(rows, list):
        return []
    events: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping) or not matches_request(item, request):
            continue
        events.append(
            {
                "title": str_or_none(item.get("title")),
                "slug": str_or_none(item.get("slug")),
                "url": f"https://polymarket.com/event/{item.get('slug')}" if item.get("slug") else None,
                "active": item.get("active") if isinstance(item.get("active"), bool) else None,
                "closed": item.get("closed") if isinstance(item.get("closed"), bool) else None,
                "liquidity": number_or_none(item.get("liquidity")),
                "volume": number_or_none(item.get("volume")),
                "open_interest": number_or_none(item.get("openInterest")),
                "end_date": str_or_none(item.get("endDate")),
                "provider_role": "event_expectation_not_news_fact",
            }
        )
        if len(events) >= 10:
            break
    return events


def _polymarket_payload_schema_valid(payload: Any) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get("events"), list)


def _normalize_search_results(provider: str, payload: Any) -> list[dict[str, Any]]:
    if provider == "Brave Search":
        rows = payload.get("web", {}).get("results", []) if isinstance(payload, Mapping) else []
    elif provider == "Bocha":
        rows = payload.get("webPages", {}).get("value", []) if isinstance(payload, Mapping) else []
    elif provider == "NewsAPI":
        rows = payload.get("articles", []) if isinstance(payload, Mapping) else []
    elif provider == "SerpAPI":
        if isinstance(payload, Mapping):
            rows = payload.get("news_results") or payload.get("organic_results", [])
        else:
            rows = []
    elif provider == "Tavily":
        rows = payload.get("results", []) if isinstance(payload, Mapping) else []
    elif provider == "Exa":
        rows = payload.get("results", []) if isinstance(payload, Mapping) else []
    else:
        rows = []
    if not isinstance(rows, list):
        return []

    discoveries: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        source = item.get("source")
        discoveries.append(
            {
                "provider": provider,
                "title": str_or_none(item.get("title") or item.get("name")),
                "url": str_or_none(item.get("url") or item.get("link")),
                "source_name": _source_name(source),
                "published_at": str_or_none(
                    item.get("publishedAt") or item.get("publishedDate") or item.get("iso_date") or item.get("date")
                ),
                "snippet": str_or_none(item.get("description") or item.get("content") or item.get("snippet") or item.get("text")),
                "discovery_role": "search_discovery_not_verified_fact",
            }
        )
        if len(discoveries) >= 8:
            break
    return discoveries


def _search_payload_schema_valid(provider: str, payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if provider == "Brave Search":
        web = payload.get("web")
        return isinstance(web, Mapping) and isinstance(web.get("results"), list)
    if provider == "Bocha":
        pages = payload.get("webPages")
        return isinstance(pages, Mapping) and isinstance(pages.get("value"), list)
    if provider == "NewsAPI":
        return isinstance(payload.get("articles"), list)
    if provider == "SerpAPI":
        return isinstance(payload.get("news_results"), list) or isinstance(payload.get("organic_results"), list)
    if provider in {"Tavily", "Exa"}:
        return isinstance(payload.get("results"), list)
    return False


def _discovery_matches(item: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    return matches_request({"title": item.get("title"), "snippet": item.get("snippet"), "url": item.get("url")}, request)


def _source_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return str_or_none(value.get("name") or value.get("title"))
    return str_or_none(value)


def _build_sources(
    official: Mapping[str, Any],
    github: Mapping[str, Any],
    exchange: Mapping[str, Any],
    regulatory: Mapping[str, Any],
    defillama: Mapping[str, Any],
    polymarket: Mapping[str, Any],
    search: Mapping[str, Any],
) -> list[dict[str, Any]]:
    observed_at = utc_now()
    sources: list[dict[str, Any]] = []
    for label, payload, endpoint, confidence in (
        ("OfficialSource", official, "configured_official_sources", "official_announcement_original_source"),
        ("GitHub", github, "/repos/{owner}/{repo}/releases", "github_release_original_source"),
        ("ExchangeAnnouncement", exchange, "configured_exchange_sources", "exchange_announcement_original_source"),
        ("RegulatorySource", regulatory, "configured_or_default_regulatory_sources", "regulatory_original_source"),
    ):
        if payload["items"]:
            sources.append(
                {
                    "provider": label,
                    "endpoint": endpoint,
                    "observed_at": observed_at,
                    "confidence": confidence,
                }
            )
    if defillama["security_incidents"]:
        sources.append(
            {
                "provider": "DefiLlama",
                "endpoint": "/api/hacks",
                "observed_at": observed_at,
                "confidence": "security_background_not_primary_news_source",
            }
        )
    if defillama["funding_events"]:
        sources.append(
            {
                "provider": "DefiLlama",
                "endpoint": "/api/raises",
                "observed_at": observed_at,
                "confidence": "funding_background_not_primary_news_source",
            }
        )
    if polymarket["events"]:
        sources.append(
            {
                "provider": "Polymarket",
                "endpoint": "/public-search",
                "observed_at": observed_at,
                "confidence": "event_expectation_market_not_news_source",
            }
        )
    search_providers = sorted({item["provider"] for item in search["discoveries"] if item.get("provider")})
    for provider in search_providers:
        sources.append(
            {
                "provider": provider,
                "endpoint": "commercial_search",
                "observed_at": observed_at,
                "confidence": "search_discovery_only_requires_source_verification",
            }
        )
    return sources


def _build_data_gaps(
    official: Mapping[str, Any],
    github: Mapping[str, Any],
    exchange: Mapping[str, Any],
    regulatory: Mapping[str, Any],
    defillama: Mapping[str, Any],
    polymarket: Mapping[str, Any],
    search: Mapping[str, Any],
    attempts: list[Mapping[str, Any]],
) -> list[str]:
    gaps: list[str] = []
    if not official["items"]:
        gaps.append("官方公告/博客/RSS 未返回可匹配原始材料，或未配置 CRYPTO_NEWS_OFFICIAL_SOURCES_JSON；不能由搜索摘要替代。")
    if not github["items"]:
        gaps.append("GitHub 发布未返回可匹配发布材料，或未配置 GitHub 仓库来源；不能由搜索摘要替代。")
    if not exchange["items"]:
        gaps.append("交易所公告未返回可匹配原始材料，或未配置 CRYPTO_NEWS_EXCHANGE_SOURCES_JSON；不能由搜索摘要替代。")
    if not regulatory["items"]:
        gaps.append("监管原始来源未返回可匹配材料，或未配置监管来源；不能由搜索摘要替代。")
    if not defillama["security_incidents"]:
        gaps.append("DefiLlama 未返回可匹配安全事件背景，或缺少 DEFILLAMA_API_KEY。")
    if not defillama["funding_events"]:
        gaps.append("DefiLlama 未返回可匹配融资事件背景，或缺少 DEFILLAMA_API_KEY。")
    if not polymarket["events"]:
        gaps.append("Polymarket 未返回可匹配事件预期盘口；这不代表没有相关新闻。")
    if not search["discoveries"]:
        gaps.append("商业搜索未返回可用发现，或搜索 provider key 缺失/失败；不得解释为无新闻。")
    missing = sorted(
        {
            str(attempt["provider"])
            for attempt in attempts
            if attempt.get("role") == "commercial_search_discovery" and attempt.get("status") == "config_blocked"
        }
    )
    if missing:
        gaps.append("缺少搜索 provider key：" + "、".join(missing) + "；搜索覆盖不完整。")
    gaps.append("商业搜索发现只提供待核验入口，不能直接写成新闻事实、公告事实或监管事实。")
    return gaps


def _build_readiness(data: Mapping[str, Any]) -> dict[str, str]:
    original_count = (
        len(data["official_announcements"])
        + len(data["github_releases"])
        + len(data["exchange_announcements"])
        + len(data["regulatory_sources"])
    )
    direct_count = (
        len(data["defillama_event_background"]["security_incidents"])
        + len(data["defillama_event_background"]["funding_events"])
        + len(data["polymarket_event_expectations"])
    )
    discovery_count = len(data["commercial_search_discoveries"])
    if original_count == 0 and direct_count == 0 and discovery_count == 0:
        return {"status": "insufficient", "reason": "新闻资料包没有任何可用原始源、事件背景、事件预期或搜索发现。"}
    if original_count >= 2:
        return {"status": "ready", "reason": "已有多个原始新闻/公告/监管源，并保留辅助事件背景或发现线索。"}
    if original_count >= 1:
        return {"status": "partial", "reason": "已有部分原始新闻/公告/监管源，但覆盖仍不完整。"}
    return {"status": "partial", "reason": "只有辅助事件背景/事件预期/搜索发现，缺少原始新闻事实源。"}


def _build_quality(readiness: Mapping[str, str], data_gaps: list[str]) -> dict[str, Any]:
    if readiness["status"] == "ready":
        coverage = 0.8
    elif readiness["status"] == "partial":
        coverage = 0.45
    else:
        coverage = 0.0
    return {
        "status": "complete" if readiness["status"] == "ready" else ("failed" if coverage == 0 else "partial"),
        "coverage_score": coverage,
        "freshness_status": "provider_reported_or_unknown",
        "warnings": data_gaps[:8],
    }


def _build_reader_brief(pack: Mapping[str, Any]) -> str:
    data = pack["data"]
    readiness = pack["readiness"]
    lines = [
        f"{pack['asset']} 的 CRYPTO 新闻资料包已返回，资料就绪度为 {_news_status_zh(readiness['status'])}：{readiness['reason']}",
        "本摘要只说明资料事实与缺口；搜索摘要、Polymarket 盘口和 DefiLlama 背景不能直接写成新闻事实。",
    ]
    for field, label in (
        ("official_announcements", "官方公告/博客/RSS"),
        ("github_releases", "GitHub 发布"),
        ("exchange_announcements", "交易所公告"),
        ("regulatory_sources", "监管原始源"),
    ):
        rows = data.get(field) if isinstance(data.get(field), list) else []
        if rows:
            titles = compact_str_list([item.get("title") or item.get("name") or item.get("tag_name") for item in rows], limit=3)
            lines.append(f"{label}：" + "；".join(titles) + "。")
    background = data["defillama_event_background"]
    if background["security_incidents"]:
        names = compact_str_list([item.get("name") for item in background["security_incidents"]], limit=3)
        lines.append("DefiLlama 安全事件背景：" + "、".join(names) + "。")
    if background["funding_events"]:
        names = compact_str_list([item.get("name") for item in background["funding_events"]], limit=3)
        lines.append("DefiLlama 融资事件背景：" + "、".join(names) + "。")
    if data["polymarket_event_expectations"]:
        titles = compact_str_list([item.get("title") for item in data["polymarket_event_expectations"]], limit=3)
        lines.append("Polymarket 事件预期：" + "；".join(titles) + "。")
    if data["commercial_search_discoveries"]:
        titles = compact_str_list([item.get("title") for item in data["commercial_search_discoveries"]], limit=4)
        lines.append("商业搜索发现待核验入口：" + "；".join(titles) + "。")
    if pack.get("data_gaps"):
        lines.append("数据缺口：" + "；".join(_news_reader_gap(item) for item in pack["data_gaps"][:6]) + "。")
    return "\n".join(lines)


def _news_status_zh(value: Any) -> str:
    labels = {
        "ready": "就绪",
        "partial": "部分覆盖",
        "insufficient": "不足",
        "failed": "失败",
        "success": "成功",
        "config_blocked": "配置阻断",
        "empty": "空返回",
    }
    text = str(value or "unknown").strip()
    return labels.get(text.lower(), text.replace("_", " "))


def _news_reader_gap(value: Any) -> str:
    text = str(value).strip()
    replacements = {
        "readiness": "资料就绪度",
        "data_gaps": "资料缺口",
        "data_gap": "资料缺口",
        "provider_attempts": "来源尝试记录",
        "provider": "来源",
        "feed": "来源",
        "partial": "部分覆盖",
        "insufficient": "不足",
        "ready": "就绪",
        "CRYPTO_NEWS_OFFICIAL_SOURCES_JSON": "官方来源配置",
        "CRYPTO_NEWS_GITHUB_REPOS_JSON": "GitHub 仓库来源配置",
        "CRYPTO_NEWS_EXCHANGE_SOURCES_JSON": "交易所公告来源配置",
        "CRYPTO_NEWS_REGULATORY_SOURCES_JSON": "监管来源配置",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
