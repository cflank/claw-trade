from __future__ import annotations

import os
from typing import Any, Mapping

from .crypto_pack_common import (
    DEFAULT_TIMEOUT_SECONDS,
    FetchJson,
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
    search_terms,
    str_or_none,
    utc_now,
    write_pack_files,
)
from .crypto_news_data_pack import (
    _normalize_polymarket_events,
    _normalize_search_results,
    _polymarket_payload_schema_valid,
    _search_payload_schema_valid,
)
from .crypto_provider_cache import (
    commit_crypto_provider_cache,
    fetch_json_with_crypto_provider_cache,
    insert_crypto_provider_attempts,
    provider_attempt_gap_messages,
    resolve_crypto_provider_cache_collections,
)


_SCHEMA_VERSION = "crypto_social_sentiment_pack.v1"
_TOOL_NAME = "crypto_social_sentiment_pack"
_WORKER_ID = "social_analyst"


def run_crypto_social_sentiment_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    return build_crypto_social_sentiment_pack(tool_input, runtime_context)


def build_crypto_social_sentiment_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    fetch_json: FetchJson | None = None,
    provider_cache_collection: Any | None = None,
    provider_attempts_collection: Any | None = None,
    provider_rate_limit_collection: Any | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    fetch_json = requests_json if fetch_json is None else fetch_json
    context = normalize_frontline_context(runtime_context, tool_name=_TOOL_NAME, worker_id=_WORKER_ID)
    request = normalize_crypto_input(tool_input, context)
    if provider_cache_collection is None and provider_attempts_collection is None and provider_rate_limit_collection is None:
        collections = resolve_crypto_provider_cache_collections(env)
        provider_cache_collection = collections.cache
        provider_attempts_collection = collections.attempts
        provider_rate_limit_collection = collections.rate_limits

    attempts: list[dict[str, Any]] = []
    raw_payload: dict[str, Any] = {}

    alternative = _load_alternative_me(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["alternative_me"] = alternative["raw"]

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

    discussion = _load_public_discussion_search(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["public_discussion_search"] = discussion["raw"]

    lunarcrush = _load_lunarcrush_metrics(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["lunarcrush"] = lunarcrush["raw"]

    x_posts = _load_x_recent_search(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["x"] = x_posts["raw"]

    reddit_posts = _load_reddit_search(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["reddit"] = reddit_posts["raw"]

    telegram_posts = _load_telegram_updates(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["telegram"] = telegram_posts["raw"]

    discord_posts = _load_discord_messages(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    raw_payload["discord"] = discord_posts["raw"]

    data = {
        "market_level_sentiment": alternative["market_level_sentiment"],
        "polymarket_event_expectations": polymarket["events"],
        "public_discussion_discoveries": discussion["discoveries"],
        "social_platform_metrics": {
            "lunarcrush": lunarcrush["items"],
        },
        "social_platform_posts": {
            "x": x_posts["items"],
            "reddit": reddit_posts["items"],
            "telegram": telegram_posts["items"],
            "discord": discord_posts["items"],
        },
        "true_social_platform_coverage": {
            "x": _coverage_status(x_posts),
            "reddit": _coverage_status(reddit_posts),
            "telegram": _coverage_status(telegram_posts),
            "discord": _coverage_status(discord_posts),
            "lunarcrush": _coverage_status(lunarcrush),
        },
    }
    sources = _build_sources(alternative, polymarket, discussion, lunarcrush, x_posts, reddit_posts, telegram_posts, discord_posts)
    data_gaps = _build_data_gaps(alternative, polymarket, discussion, lunarcrush, x_posts, reddit_posts, telegram_posts, discord_posts, attempts)
    data_gaps.extend(provider_attempt_gap_messages(attempts))
    readiness = _build_readiness(data)
    quality = _build_quality(readiness, data_gaps)
    pack = {
        "ok": readiness["status"] != "insufficient",
        "schema_version": _SCHEMA_VERSION,
        "tool_name": _TOOL_NAME,
        "domain": "social_sentiment",
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
        domain="social_sentiment",
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


def _load_alternative_me(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    payload, fetch_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="Alternative.me",
        endpoint="/fng/",
        role="market_level_sentiment_indicator",
        source_role="market_level_sentiment_not_social_platform",
        url="https://api.alternative.me/fng/",
        params={"limit": 7, "format": "json"},
        headers={},
        auth_mode="public_no_key",
    )
    values = _normalize_alternative_me(payload)
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=fetch_attempts,
        payload=payload,
        accepted_count=len(values),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_alternative_me_payload_schema_valid(payload),
    )
    return {"market_level_sentiment": values, "raw": payload}


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
    query = " ".join([*search_terms(request), "crypto sentiment community narrative"])
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
        source_role="event_expectation_not_social_consensus",
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


def _load_public_discussion_search(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    query = f"{' '.join(search_terms(request))} crypto community sentiment reddit x telegram discord narrative"
    discoveries: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}

    provider_specs = (
        _brave_spec(env, query),
        _bocha_spec(env, query),
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
                    role="public_discussion_search_discovery",
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
            role="public_discussion_search_discovery",
            source_role="search_discovery_only_not_social_consensus",
            url=spec["url"],
            params=spec["params"],
            headers=spec["headers"],
            auth_mode=spec["auth_mode"],
            method=spec["method"],
            json_body=spec["json_body"],
        )
        normalized = _normalize_search_results(spec["provider"], payload)
        filtered = [item for item in normalized if _discovery_matches(item, request)]
        raw[spec["provider"].lower()] = payload
        discoveries.extend(filtered)
        _finish_cached_attempts(
            attempts=attempts,
            fetch_attempts=fetch_attempts,
            payload=payload,
            accepted_count=len(filtered),
            provider_cache_collection=provider_cache_collection,
            schema_valid=_search_payload_schema_valid(spec["provider"], payload),
        )
    return {"discoveries": discoveries[:25], "raw": raw}


def _load_lunarcrush_metrics(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    key = env_text(env, "LUNARCRUSH_API_KEY")
    if not key:
        attempts.append(
            blocked_attempt(
                provider="LunarCrush",
                endpoint="LUNARCRUSH_API_KEY",
                role="social_platform_metric",
                auth_mode="credential_missing",
                error_code="LUNARCRUSH_API_KEY_MISSING",
                error_message="LunarCrush social metrics require LUNARCRUSH_API_KEY.",
            )
        )
        return {"items": [], "raw": {}, "configured": False}
    template = env_text(env, "LUNARCRUSH_COIN_ENDPOINT_TEMPLATE") or "https://lunarcrush.com/api4/public/coins/list/v2"
    url = template.format(symbol=str(request["ticker"]).lower(), ticker=str(request["ticker"]).upper())
    payload, fetch_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="LunarCrush",
        endpoint=url,
        role="social_platform_metric",
        source_role="social_platform_metric",
        url=url,
        params={"symbol": request["ticker"], "limit": 50} if "{" not in template else {},
        headers={"Authorization": f"Bearer {key}"},
        auth_mode="bearer_header",
    )
    items = _normalize_lunarcrush(payload, request)
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=fetch_attempts,
        payload=payload,
        accepted_count=len(items),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_lunarcrush_payload_schema_valid(payload),
    )
    return {"items": items, "raw": payload, "configured": True}


def _load_x_recent_search(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    key, key_name = first_env(env, ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"))
    if not key:
        attempts.append(
            blocked_attempt(
                provider="X",
                endpoint="/2/tweets/search/recent",
                role="social_platform_posts",
                auth_mode="credential_missing",
                error_code="X_BEARER_TOKEN_MISSING",
                error_message="X recent search requires X_BEARER_TOKEN or TWITTER_BEARER_TOKEN.",
            )
        )
        return {"items": [], "raw": {}, "configured": False}
    query = f"({' OR '.join(search_terms(request))}) crypto -is:retweet lang:en"
    payload, fetch_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="X",
        endpoint="/2/tweets/search/recent",
        role="social_platform_posts",
        source_role="social_platform_posts",
        url="https://api.x.com/2/tweets/search/recent",
        params={
            "query": query,
            "max_results": 10,
            "tweet.fields": "created_at,author_id,public_metrics,lang",
        },
        headers={"Authorization": f"Bearer {key}"},
        auth_mode=f"{key_name}_bearer",
    )
    items = _normalize_x_posts(payload, request)
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=fetch_attempts,
        payload=payload,
        accepted_count=len(items),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_x_payload_schema_valid(payload),
    )
    return {"items": items, "raw": payload, "configured": True}


def _load_reddit_search(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    token = env_text(env, "REDDIT_BEARER_TOKEN")
    if not token:
        attempts.append(
            blocked_attempt(
                provider="Reddit",
                endpoint="/search",
                role="social_platform_posts",
                auth_mode="credential_missing",
                error_code="REDDIT_BEARER_TOKEN_MISSING",
                error_message="Reddit search requires REDDIT_BEARER_TOKEN generated by Reddit OAuth.",
            )
        )
        return {"items": [], "raw": {}, "configured": False}
    payload, fetch_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="Reddit",
        endpoint="/search",
        role="social_platform_posts",
        source_role="social_platform_posts",
        url="https://oauth.reddit.com/search",
        params={"q": " ".join(search_terms(request)), "sort": "new", "t": "week", "limit": 10, "restrict_sr": "false"},
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": env_text(env, "REDDIT_USER_AGENT") or "claw-trade/crypto-social-pack",
        },
        auth_mode="bearer_header",
    )
    items = _normalize_reddit_posts(payload, request)
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=fetch_attempts,
        payload=payload,
        accepted_count=len(items),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_reddit_payload_schema_valid(payload),
    )
    return {"items": items, "raw": payload, "configured": True}


def _load_telegram_updates(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    token = env_text(env, "TELEGRAM_BOT_TOKEN")
    if not token:
        attempts.append(
            blocked_attempt(
                provider="Telegram",
                endpoint="/bot{token}/getUpdates",
                role="social_platform_posts",
                auth_mode="credential_missing",
                error_code="TELEGRAM_BOT_TOKEN_MISSING",
                error_message="Telegram Bot API requires TELEGRAM_BOT_TOKEN and only returns bot-accessible updates.",
            )
        )
        return {"items": [], "raw": {}, "configured": False}
    payload, fetch_attempts = _cached_json_fetch(
        request=request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
        provider="Telegram",
        endpoint="/bot{token}/getUpdates",
        role="social_platform_posts",
        source_role="social_platform_posts",
        url=f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100, "timeout": 0},
        headers={},
        auth_mode="bot_token_path",
    )
    items = _normalize_telegram_updates(payload, request)
    _finish_cached_attempts(
        attempts=attempts,
        fetch_attempts=fetch_attempts,
        payload=payload,
        accepted_count=len(items),
        provider_cache_collection=provider_cache_collection,
        schema_valid=_telegram_payload_schema_valid(payload),
    )
    return {"items": items, "raw": payload, "configured": True}


def _load_discord_messages(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    token = env_text(env, "DISCORD_BOT_TOKEN")
    channels = configured_entries(env, "CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON", request)
    if not token or not channels:
        attempts.append(
            blocked_attempt(
                provider="Discord",
                endpoint="/channels/{channel.id}/messages",
                role="social_platform_posts",
                auth_mode="credential_or_config_missing",
                error_code="DISCORD_CONFIG_MISSING",
                error_message="Discord channel reads require DISCORD_BOT_TOKEN and CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON.",
            )
        )
        return {"items": [], "raw": {}, "configured": False}
    items: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for entry in channels:
        channel_id = str_or_none(entry.get("id") or entry.get("channel_id") or entry.get("url"))
        if not channel_id:
            attempts.append(
                blocked_attempt(
                    provider="Discord",
                    endpoint="/channels/{channel.id}/messages",
                    role="social_platform_posts",
                    auth_mode="config_invalid",
                    error_code="DISCORD_CHANNEL_ID_MISSING",
                    error_message="CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON entry is missing id/channel_id/url.",
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
            provider="Discord",
            endpoint="/channels/{channel.id}/messages",
            role="social_platform_posts",
            source_role="social_platform_posts",
            url=f"https://discord.com/api/v10/channels/{channel_id}/messages",
            params={"limit": 50},
            headers={"Authorization": f"Bot {token}"},
            auth_mode="bot_token_header",
        )
        normalized = _normalize_discord_messages(payload, request, channel_id=channel_id)
        raw[channel_id] = payload
        items.extend(normalized)
        _finish_cached_attempts(
            attempts=attempts,
            fetch_attempts=fetch_attempts,
            payload=payload,
            accepted_count=len(normalized),
            provider_cache_collection=provider_cache_collection,
            schema_valid=_discord_payload_schema_valid(payload),
        )
    return {"items": items[:25], "raw": raw, "configured": True}


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


def _serpapi_spec(env: Mapping[str, str], query: str) -> dict[str, Any]:
    key = env_text(env, "SERPAPI_API_KEY")
    params = {"engine": "google", "q": query, "gl": "us", "hl": "en"}
    if key:
        params["api_key"] = key
    return {
        "provider": "SerpAPI",
        "endpoint": "/search?engine=google",
        "url": "https://serpapi.com/search",
        "params": params,
        "headers": {},
        "method": "GET",
        "json_body": None,
        "auth_mode": "api_key_query" if key else "credential_missing",
        "key_missing": not key,
        "missing_code": "SERPAPI_API_KEY_MISSING",
        "missing_message": "SerpAPI requires SERPAPI_API_KEY.",
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
        "json_body": {"query": query, "search_depth": "basic", "topic": "general", "max_results": 8},
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


def _normalize_alternative_me(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, Mapping) else []
    if not isinstance(rows, list):
        return []
    values: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        values.append(
            {
                "value": number_or_none(item.get("value")),
                "classification": str_or_none(item.get("value_classification")),
                "timestamp": str_or_none(item.get("timestamp")),
                "time_until_update": str_or_none(item.get("time_until_update")),
                "provider_role": "market_level_sentiment_indicator_not_asset_social_sentiment",
            }
        )
    return values


def _alternative_me_payload_schema_valid(payload: Any) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get("data"), list)


def _discovery_matches(item: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    return matches_request({"title": item.get("title"), "snippet": item.get("snippet"), "url": item.get("url")}, request)


def _normalize_lunarcrush(payload: Any, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _payload_rows(payload)
    metrics: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping) or not matches_request(item, request):
            continue
        metrics.append(
            {
                "symbol": str_or_none(item.get("symbol")),
                "name": str_or_none(item.get("name")),
                "galaxy_score": number_or_none(item.get("galaxy_score")),
                "alt_rank": number_or_none(item.get("alt_rank")),
                "social_mentions": number_or_none(item.get("social_mentions")),
                "social_interactions": number_or_none(item.get("social_interactions") or item.get("interactions")),
                "social_contributors": number_or_none(item.get("social_contributors")),
                "social_dominance": number_or_none(item.get("social_dominance")),
                "provider_role": "social_platform_aggregate_metric",
            }
        )
        if len(metrics) >= 5:
            break
    return metrics


def _lunarcrush_payload_schema_valid(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    if isinstance(payload, Mapping):
        return any(isinstance(payload.get(key), list) for key in ("data", "coins", "results"))
    return False


def _normalize_x_posts(payload: Any, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, Mapping) else []
    if not isinstance(rows, list):
        return []
    posts: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping) or not matches_request(item, request):
            continue
        metrics = item.get("public_metrics") if isinstance(item.get("public_metrics"), Mapping) else {}
        posts.append(
            {
                "platform": "x",
                "id": str_or_none(item.get("id")),
                "author_id": str_or_none(item.get("author_id")),
                "created_at": str_or_none(item.get("created_at")),
                "text": str_or_none(item.get("text")),
                "lang": str_or_none(item.get("lang")),
                "public_metrics": {key: number_or_none(value) for key, value in metrics.items()},
                "provider_role": "social_platform_post_sample",
            }
        )
    return posts


def _x_payload_schema_valid(payload: Any) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get("data"), list)


def _normalize_reddit_posts(payload: Any, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    children = payload.get("data", {}).get("children", []) if isinstance(payload, Mapping) else []
    if not isinstance(children, list):
        return []
    posts: list[dict[str, Any]] = []
    for child in children:
        item = child.get("data") if isinstance(child, Mapping) else None
        if not isinstance(item, Mapping) or not matches_request(item, request):
            continue
        posts.append(
            {
                "platform": "reddit",
                "id": str_or_none(item.get("id")),
                "subreddit": str_or_none(item.get("subreddit")),
                "title": str_or_none(item.get("title")),
                "url": str_or_none(item.get("url")),
                "permalink": f"https://www.reddit.com{item.get('permalink')}" if item.get("permalink") else None,
                "score": number_or_none(item.get("score")),
                "num_comments": number_or_none(item.get("num_comments")),
                "created_utc": number_or_none(item.get("created_utc")),
                "provider_role": "social_platform_post_sample",
            }
        )
    return posts


def _reddit_payload_schema_valid(payload: Any) -> bool:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    return isinstance(data, Mapping) and isinstance(data.get("children"), list)


def _normalize_telegram_updates(payload: Any, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("result") if isinstance(payload, Mapping) else []
    if not isinstance(rows, list):
        return []
    posts: list[dict[str, Any]] = []
    for update in rows:
        if not isinstance(update, Mapping):
            continue
        message = update.get("channel_post") or update.get("message")
        if not isinstance(message, Mapping) or not matches_request(message, request):
            continue
        chat = message.get("chat") if isinstance(message.get("chat"), Mapping) else {}
        posts.append(
            {
                "platform": "telegram",
                "message_id": number_or_none(message.get("message_id")),
                "chat_id": str_or_none(chat.get("id")),
                "chat_title": str_or_none(chat.get("title") or chat.get("username")),
                "date": number_or_none(message.get("date")),
                "text": str_or_none(message.get("text") or message.get("caption")),
                "provider_role": "bot_accessible_message_sample",
            }
        )
    return posts


def _telegram_payload_schema_valid(payload: Any) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get("result"), list)


def _normalize_discord_messages(payload: Any, request: Mapping[str, Any], *, channel_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    posts: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping) or not matches_request(item, request):
            continue
        author = item.get("author") if isinstance(item.get("author"), Mapping) else {}
        posts.append(
            {
                "platform": "discord",
                "id": str_or_none(item.get("id")),
                "channel_id": channel_id,
                "author_id": str_or_none(author.get("id")),
                "timestamp": str_or_none(item.get("timestamp")),
                "content": str_or_none(item.get("content")),
                "provider_role": "bot_accessible_message_sample",
            }
        )
    return posts


def _discord_payload_schema_valid(payload: Any) -> bool:
    return isinstance(payload, list)


def _payload_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("data", "coins", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def _coverage_status(payload: Mapping[str, Any]) -> str:
    if payload.get("items"):
        return "covered_with_provider_payload"
    if payload.get("configured"):
        return "configured_but_empty_or_failed"
    return "missing_credentials_or_config"


def _build_sources(
    alternative: Mapping[str, Any],
    polymarket: Mapping[str, Any],
    discussion: Mapping[str, Any],
    lunarcrush: Mapping[str, Any],
    x_posts: Mapping[str, Any],
    reddit_posts: Mapping[str, Any],
    telegram_posts: Mapping[str, Any],
    discord_posts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    observed_at = utc_now()
    sources: list[dict[str, Any]] = []
    if alternative["market_level_sentiment"]:
        sources.append(
            {
                "provider": "Alternative.me",
                "endpoint": "/fng/",
                "observed_at": observed_at,
                "confidence": "market_level_sentiment_indicator_not_social_platform_coverage",
            }
        )
    if polymarket["events"]:
        sources.append(
            {
                "provider": "Polymarket",
                "endpoint": "/public-search",
                "observed_at": observed_at,
                "confidence": "event_expectation_market_not_social_consensus",
            }
        )
    search_providers = sorted({item["provider"] for item in discussion["discoveries"] if item.get("provider")})
    for provider in search_providers:
        sources.append(
            {
                "provider": provider,
                "endpoint": "commercial_search",
                "observed_at": observed_at,
                "confidence": "public_discussion_discovery_only_not_sentiment_measurement",
            }
        )
    for provider, payload, endpoint, confidence in (
        ("LunarCrush", lunarcrush, "configured_lunarcrush_endpoint", "social_platform_aggregate_metric"),
        ("X", x_posts, "/2/tweets/search/recent", "social_platform_post_sample"),
        ("Reddit", reddit_posts, "/search", "social_platform_post_sample"),
        ("Telegram", telegram_posts, "/bot{token}/getUpdates", "bot_accessible_message_sample"),
        ("Discord", discord_posts, "/channels/{channel.id}/messages", "bot_accessible_message_sample"),
    ):
        if payload["items"]:
            sources.append(
                {
                    "provider": provider,
                    "endpoint": endpoint,
                    "observed_at": observed_at,
                    "confidence": confidence,
                }
            )
    return sources


def _build_data_gaps(
    alternative: Mapping[str, Any],
    polymarket: Mapping[str, Any],
    discussion: Mapping[str, Any],
    lunarcrush: Mapping[str, Any],
    x_posts: Mapping[str, Any],
    reddit_posts: Mapping[str, Any],
    telegram_posts: Mapping[str, Any],
    discord_posts: Mapping[str, Any],
    attempts: list[Mapping[str, Any]],
) -> list[str]:
    gaps = [
        "Alternative.me 只代表市场级 Fear & Greed 指标，不代表单币种社交舆情。",
        "Polymarket 只代表事件预期盘口，不代表新闻事实或社交共识。",
        "商业搜索发现只提供公开讨论入口，不能直接写成 KOL 立场、社区共识或情绪结论。",
    ]
    for label, payload in (
        ("LunarCrush 聚合社交指标", lunarcrush),
        ("X 原帖样本", x_posts),
        ("Reddit 原帖样本", reddit_posts),
        ("Telegram bot 可访问消息样本", telegram_posts),
        ("Discord bot 可访问消息样本", discord_posts),
    ):
        if not payload["items"]:
            gaps.append(f"{label}未返回可匹配材料，或缺少对应接口凭证/配置；不能声称该平台已覆盖。")
    if not alternative["market_level_sentiment"]:
        gaps.append("Alternative.me 未返回市场级情绪指标。")
    if not polymarket["events"]:
        gaps.append("Polymarket 未返回可匹配事件预期盘口。")
    if not discussion["discoveries"]:
        gaps.append("商业搜索未返回公开讨论线索，或搜索来源密钥缺失/失败；不得解释为无舆情。")
    missing = sorted(
        {
            str(attempt["provider"])
            for attempt in attempts
            if attempt.get("role") == "public_discussion_search_discovery" and attempt.get("status") == "config_blocked"
        }
    )
    if missing:
        gaps.append("缺少公开讨论搜索来源密钥：" + "、".join(missing) + "；讨论线索覆盖不完整。")
    return gaps


def _build_readiness(data: Mapping[str, Any]) -> dict[str, str]:
    platform_count = (
        len(data["social_platform_metrics"]["lunarcrush"])
        + sum(len(rows) for rows in data["social_platform_posts"].values())
    )
    signal_count = (
        len(data["market_level_sentiment"])
        + len(data["polymarket_event_expectations"])
        + len(data["public_discussion_discoveries"])
        + platform_count
    )
    if signal_count == 0:
        return {"status": "insufficient", "reason": "舆情资料包没有任何市场级情绪、事件预期或公开讨论发现。"}
    if platform_count >= 2:
        return {"status": "ready", "reason": "已有多个真实社交平台或聚合社交指标样本，并保留辅助情绪/预期线索。"}
    if platform_count == 1:
        return {"status": "partial", "reason": "已有部分真实社交平台或聚合社交指标样本，但覆盖仍不完整。"}
    return {"status": "partial", "reason": "只有辅助情绪/预期/搜索线索，缺少真实社交平台原始舆情覆盖。"}


def _build_quality(readiness: Mapping[str, str], data_gaps: list[str]) -> dict[str, Any]:
    if readiness["status"] == "ready":
        coverage = 0.75
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
        f"{pack['asset']} 的 CRYPTO 舆情资料包已返回，资料就绪度为 {_social_status_zh(readiness['status'])}：{readiness['reason']}",
        "本摘要只说明资料事实与缺口；Alternative.me、Polymarket 和搜索发现都不能冒充真实社交平台舆情覆盖。",
    ]
    if data["market_level_sentiment"]:
        latest = data["market_level_sentiment"][0]
        lines.append(
            "Alternative.me 市场级情绪："
            f"数值 {latest.get('value')}，分类 {latest.get('classification')}。"
        )
    if data["polymarket_event_expectations"]:
        titles = compact_str_list([item.get("title") for item in data["polymarket_event_expectations"]], limit=3)
        lines.append("Polymarket 事件预期：" + "；".join(titles) + "。")
    if data["public_discussion_discoveries"]:
        titles = compact_str_list([item.get("title") for item in data["public_discussion_discoveries"]], limit=4)
        lines.append("公开讨论搜索发现待核验入口：" + "；".join(titles) + "。")
    if data["social_platform_metrics"]["lunarcrush"]:
        latest = data["social_platform_metrics"]["lunarcrush"][0]
        lines.append(
            "LunarCrush 聚合指标："
            f"银河评分 {latest.get('galaxy_score')}，综合排名 {latest.get('alt_rank')}，"
            f"社交提及量 {latest.get('social_mentions')}。"
        )
    for platform, rows in data["social_platform_posts"].items():
        if rows:
            titles = compact_str_list(
                [item.get("text") or item.get("title") or item.get("content") for item in rows],
                limit=3,
            )
            lines.append(f"{platform} 样本：" + "；".join(titles) + "。")
    if pack.get("data_gaps"):
        lines.append("数据缺口：" + "；".join(_social_reader_gap(item) for item in pack["data_gaps"][:6]) + "。")
    return "\n".join(lines)


def _social_status_zh(value: Any) -> str:
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


def _social_reader_gap(value: Any) -> str:
    text = str(value).strip()
    replacements = {
        "readiness": "资料就绪度",
        "data_gaps": "资料缺口",
        "data_gap": "资料缺口",
        "provider_attempts": "来源尝试记录",
        "provider": "来源",
        "credential/config": "接口凭证/配置",
        "credential": "接口凭证",
        "config": "配置",
        "partial": "部分覆盖",
        "insufficient": "不足",
        "ready": "就绪",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
