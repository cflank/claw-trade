from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from urllib.parse import quote

import requests

from .crypto_provider_cache import (
    commit_crypto_provider_cache,
    fetch_json_with_crypto_provider_cache,
    insert_crypto_provider_attempts,
    provider_attempt_gap_messages,
    resolve_crypto_provider_cache_collections,
)


_SCHEMA_VERSION = "crypto_fundamental_pack.v1"
_TOOL_NAME = "crypto_fundamental_data_pack"
_WORKER_ID = "fundamental_analyst"
_MARKET = "CRYPTO"
_DEFAULT_TIMEOUT_SECONDS = 15
_MAX_DESCRIPTION_CHARS = 1200
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

FetchJson = Callable[..., Any]


def run_crypto_fundamental_data_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    return build_crypto_fundamental_data_pack(tool_input, runtime_context)


def build_crypto_fundamental_data_pack(
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
    fetch_json = _requests_get_json if fetch_json is None else fetch_json
    context = _normalize_context(runtime_context)
    request = _normalize_input(tool_input, context)
    if provider_cache_collection is None and provider_attempts_collection is None and provider_rate_limit_collection is None:
        collections = resolve_crypto_provider_cache_collections(env)
        provider_cache_collection = collections.cache
        provider_attempts_collection = collections.attempts
        provider_rate_limit_collection = collections.rate_limits

    attempts: list[dict[str, Any]] = []
    raw_payload: dict[str, Any] = {}

    coingecko = _load_coingecko_asset(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    if coingecko["raw"] is not None:
        raw_payload["coingecko"] = coingecko["raw"]

    defillama = _load_defillama_protocol(
        request,
        coingecko_id=coingecko["data"].get("id") if coingecko["data"] else None,
        context=context,
        env=env,
        fetch_json=fetch_json,
        attempts=attempts,
        provider_cache_collection=provider_cache_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    if defillama["raw"]:
        raw_payload["defillama"] = defillama["raw"]

    asset_data = _build_asset_data(coingecko["data"], defillama["data"])
    sources = _build_sources(coingecko["data"], defillama["data"])
    conflicts = _build_conflicts(coingecko["data"], defillama["data"])
    data_gaps = _build_data_gaps(coingecko["data"], defillama["data"])
    data_gaps.extend(provider_attempt_gap_messages(attempts))
    readiness = _build_readiness(coingecko["data"], defillama["data"], data_gaps)
    quality = _build_quality(readiness, data_gaps, conflicts)
    as_of = _utc_now()

    pack = {
        "ok": readiness["status"] != "insufficient",
        "schema_version": _SCHEMA_VERSION,
        "tool_name": _TOOL_NAME,
        "domain": "fundamental",
        "asset": request["ticker"],
        "market": _MARKET,
        "as_of": as_of,
        "input": request,
        "data": asset_data,
        "sources": sources,
        "provider_attempts": attempts,
        "data_gaps": data_gaps,
        "conflicts": conflicts,
        "readiness": readiness,
        "quality": quality,
    }
    pack["reader_brief"] = _build_reader_brief(pack)
    _write_pack_files(context, pack, raw_payload)
    insert_crypto_provider_attempts(attempts, collection=provider_attempts_collection)
    return pack


def _normalize_context(runtime_context: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(runtime_context, Mapping):
        raise ValueError("runtime_context must be a mapping")
    required = ("run_id", "stage", "worker_id", "call_id", "tool_name", "evidence_root")
    normalized: dict[str, str] = {}
    for field in required:
        value = runtime_context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"runtime_context.{field} is required")
        normalized[field] = value.strip()
    if normalized["stage"] != "frontline":
        raise ValueError(f"{_TOOL_NAME} requires stage=frontline")
    if normalized["worker_id"] != _WORKER_ID:
        raise ValueError(f"{_TOOL_NAME} requires worker_id={_WORKER_ID}")
    if normalized["tool_name"] != _TOOL_NAME:
        raise ValueError(f"runtime_context.tool_name must be {_TOOL_NAME}")
    for optional in ("current_date", "start_date", "end_date"):
        value = runtime_context.get(optional)
        if isinstance(value, str) and value.strip():
            normalized[optional] = value.strip()
    return normalized


def _normalize_input(tool_input: Mapping[str, Any], context: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(tool_input, Mapping):
        raise ValueError("tool_input must be a mapping")
    ticker = _required_text(tool_input, "ticker").upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError("tool_input.ticker must be a single crypto symbol")
    market = _required_text(tool_input, "market").upper()
    if market != _MARKET:
        raise ValueError(f"tool_input.market must be {_MARKET}")
    current_date = context.get("current_date")
    start_date = context.get("start_date") or _optional_text(tool_input, "start_date")
    end_date = context.get("end_date") or _optional_text(tool_input, "end_date") or current_date
    company_name = _optional_text(tool_input, "company_name") or ticker
    aliases = tool_input.get("aliases")
    if aliases is not None:
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ValueError("tool_input.aliases must be a string list")
        aliases = [item.strip() for item in aliases if item.strip()]
    else:
        aliases = []
    return {
        "ticker": ticker,
        "market": market,
        "company_name": company_name,
        "start_date": start_date,
        "end_date": end_date,
        "aliases": aliases,
    }


def _load_coingecko_asset(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    base_url, headers, auth_mode = _coingecko_auth(env)
    if base_url is None:
        attempts.append(
            _blocked_attempt(
                provider="CoinGecko",
                endpoint="/search",
                role="reference",
                auth_mode=auth_mode,
                error_code="COINGECKO_API_KEY_MISSING",
                error_message="CoinGecko requires COINGECKO_DEMO_API_KEY or COINGECKO_PRO_API_KEY for this data pack.",
            )
        )
        return {"data": None, "raw": {}}
    query = request["ticker"]
    search_url = f"{base_url}/search"
    search_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="fundamental",
        provider="CoinGecko",
        endpoint="/search",
        role="reference",
        source_role="fundamental_reference",
        url=search_url,
        params={"query": query},
        headers=headers,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_json=fetch_json,
        env=env,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    search_payload = search_result.payload
    search_attempts = search_result.attempts
    candidate = _pick_coingecko_candidate(search_payload, request)
    if search_attempts:
        _finish_attempt(search_attempts[-1], search_payload, accepted_count=1 if candidate else 0)
        commit_crypto_provider_cache(attempt=search_attempts[-1], payload=search_payload, collection=provider_cache_collection)
    attempts.extend(search_attempts)
    if not candidate:
        return {"data": None, "raw": {"search": search_payload}}

    coin_id = candidate["id"]
    detail_url = f"{base_url}/coins/{quote(str(coin_id), safe='')}"
    detail_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="fundamental",
        provider="CoinGecko",
        endpoint="/coins/{id}",
        role="reference",
        source_role="fundamental_reference",
        url=detail_url,
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        },
        headers=headers,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_json=fetch_json,
        env=env,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    detail_payload = detail_result.payload
    detail_attempts = detail_result.attempts
    data = _normalize_coingecko_detail(detail_payload)
    if detail_attempts:
        _finish_attempt(detail_attempts[-1], detail_payload, accepted_count=1 if data else 0)
        commit_crypto_provider_cache(attempt=detail_attempts[-1], payload=detail_payload, collection=provider_cache_collection)
    attempts.extend(detail_attempts)
    return {"data": data, "raw": {"search": search_payload, "detail": detail_payload}}


def _load_defillama_protocol(
    request: Mapping[str, Any],
    *,
    coingecko_id: str | None,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    attempts: list[dict[str, Any]],
    provider_cache_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> dict[str, Any]:
    base_url, path_prefix, auth_mode = _defillama_auth(env)
    protocols_url = f"{base_url}{path_prefix}/protocols"
    protocols_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="fundamental",
        provider="DefiLlama",
        endpoint=f"{path_prefix}/protocols",
        role="defi_operating_metrics",
        source_role="defi_operating_metrics",
        url=protocols_url,
        params={},
        headers={},
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_json=fetch_json,
        env=env,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    protocols_payload = protocols_result.payload
    protocols_attempts = protocols_result.attempts
    candidate = _pick_defillama_protocol(protocols_payload, request, coingecko_id=coingecko_id)
    if protocols_attempts:
        _finish_attempt(protocols_attempts[-1], protocols_payload, accepted_count=1 if candidate else 0)
        commit_crypto_provider_cache(attempt=protocols_attempts[-1], payload=protocols_payload, collection=provider_cache_collection)
    attempts.extend(protocols_attempts)
    if not candidate:
        return {"data": None, "raw": {"protocols": protocols_payload}}

    slug = str(candidate["slug"])
    detail_url = f"{base_url}{path_prefix}/protocol/{quote(slug, safe='')}"
    detail_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="fundamental",
        provider="DefiLlama",
        endpoint=f"{path_prefix}/protocol/{{protocol}}",
        role="defi_operating_metrics",
        source_role="defi_operating_metrics",
        url=detail_url,
        params={},
        headers={},
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_json=fetch_json,
        env=env,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    detail_payload = detail_result.payload
    detail_attempts = detail_result.attempts
    protocol = _normalize_defillama_protocol(candidate, detail_payload)
    if detail_attempts:
        _finish_attempt(detail_attempts[-1], detail_payload, accepted_count=1 if protocol else 0)
        commit_crypto_provider_cache(attempt=detail_attempts[-1], payload=detail_payload, collection=provider_cache_collection)
    attempts.extend(detail_attempts)

    fees_url = f"{base_url}{path_prefix}/summary/fees/{quote(slug, safe='')}"
    fees_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="fundamental",
        provider="DefiLlama",
        endpoint=f"{path_prefix}/summary/fees/{{protocol}}",
        role="defi_fees_revenue",
        source_role="defi_fees_revenue",
        url=fees_url,
        params={},
        headers={},
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        auth_mode=auth_mode,
        fetch_json=fetch_json,
        env=env,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    fees_payload = fees_result.payload
    fees_attempts = fees_result.attempts
    fees = _normalize_defillama_fees(fees_payload)
    if fees_attempts:
        _finish_attempt(fees_attempts[-1], fees_payload, accepted_count=1 if fees else 0)
        commit_crypto_provider_cache(attempt=fees_attempts[-1], payload=fees_payload, collection=provider_cache_collection)
    attempts.extend(fees_attempts)

    if protocol and fees:
        protocol["fees_revenue"] = fees
    return {"data": protocol, "raw": {"protocols": protocols_payload, "detail": detail_payload, "fees": fees_payload}}


def _coingecko_auth(env: Mapping[str, str]) -> tuple[str | None, dict[str, str], str]:
    pro_key = _env_text(env, "COINGECKO_PRO_API_KEY")
    if pro_key:
        return "https://pro-api.coingecko.com/api/v3", {"x-cg-pro-api-key": pro_key}, "pro_key_header"
    demo_key = _env_text(env, "COINGECKO_DEMO_API_KEY")
    if demo_key:
        return "https://api.coingecko.com/api/v3", {"x-cg-demo-api-key": demo_key}, "demo_key_header"
    return None, {}, "credential_missing"


def _defillama_auth(env: Mapping[str, str]) -> tuple[str, str, str]:
    api_key = _env_text(env, "DEFILLAMA_API_KEY")
    if api_key:
        return f"https://pro-api.llama.fi/{quote(api_key, safe='')}", "/api", "pro_path_key"
    return "https://api.llama.fi", "", "public_no_key"


def _blocked_attempt(
    *,
    provider: str,
    endpoint: str,
    role: str,
    auth_mode: str,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "provider": provider,
        "endpoint": endpoint,
        "role": role,
        "status": "config_blocked",
        "started_at": now,
        "finished_at": now,
        "elapsed_ms": 0,
        "timeout_ms": 0,
        "raw_count": 0,
        "accepted_count": 0,
        "auth_mode": auth_mode,
        "payload_hash": None,
        "error_code": error_code,
        "error_message_redacted": _redact(error_message),
    }


def _finish_attempt(attempt: dict[str, Any], payload: Any, *, accepted_count: int) -> None:
    attempt["raw_count"] = _raw_count(payload)
    attempt["accepted_count"] = accepted_count
    if attempt.get("status") in {
        "cache_hit",
        "cache_miss",
        "cache_stale",
        "config_blocked",
        "error",
        "rate_limited",
        "schema_invalid",
    }:
        return
    if accepted_count <= 0:
        attempt["status"] = "empty"


def _requests_get_json(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    if method.upper() == "POST":
        response = requests.post(url, params=params, headers=headers, json=json_body, timeout=timeout)
    else:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _pick_coingecko_candidate(payload: Any, request: Mapping[str, Any]) -> Mapping[str, Any] | None:
    coins = payload.get("coins") if isinstance(payload, Mapping) else None
    if not isinstance(coins, list):
        return None
    ticker = str(request["ticker"]).lower()
    names = {str(request["company_name"]).lower(), *[str(item).lower() for item in request.get("aliases", [])]}

    def score(item: Any) -> tuple[int, int, str]:
        if not isinstance(item, Mapping):
            return (99, 999999, "")
        symbol = str(item.get("symbol") or "").lower()
        coin_id = str(item.get("id") or "").lower()
        name = str(item.get("name") or "").lower()
        rank = item.get("market_cap_rank")
        rank_value = int(rank) if isinstance(rank, int) and rank > 0 else 999999
        if symbol == ticker:
            primary = 0
        elif coin_id == ticker or name in names or coin_id in names:
            primary = 1
        elif ticker in {coin_id, name}:
            primary = 2
        else:
            primary = 9
        return (primary, rank_value, coin_id)

    candidates = [item for item in coins if isinstance(item, Mapping) and item.get("id")]
    candidates.sort(key=score)
    if not candidates or score(candidates[0])[0] >= 9:
        return None
    return candidates[0]


def _normalize_coingecko_detail(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping) or not payload.get("id"):
        return None
    market_data = payload.get("market_data") if isinstance(payload.get("market_data"), Mapping) else {}
    links = payload.get("links") if isinstance(payload.get("links"), Mapping) else {}
    description = payload.get("description") if isinstance(payload.get("description"), Mapping) else {}
    data = {
        "id": str(payload.get("id")),
        "symbol": _upper_or_none(payload.get("symbol")),
        "name": _str_or_none(payload.get("name")),
        "web_slug": _str_or_none(payload.get("web_slug")),
        "asset_platform_id": _str_or_none(payload.get("asset_platform_id")),
        "categories": _compact_str_list(payload.get("categories"), limit=12),
        "hashing_algorithm": _str_or_none(payload.get("hashing_algorithm")),
        "genesis_date": _str_or_none(payload.get("genesis_date")),
        "description_en": _clean_description(description.get("en")),
        "links": {
            "homepage": _first_url(links.get("homepage")),
            "whitepaper": _str_or_none(links.get("whitepaper")),
            "blockchain_site": _first_url(links.get("blockchain_site")),
            "official_forum_url": _first_url(links.get("official_forum_url")),
        },
        "market_data": {
            "current_price_usd": _usd_value(market_data.get("current_price")),
            "market_cap_usd": _usd_value(market_data.get("market_cap")),
            "fdv_usd": _usd_value(market_data.get("fully_diluted_valuation")),
            "total_volume_usd": _usd_value(market_data.get("total_volume")),
            "circulating_supply": _number_or_none(market_data.get("circulating_supply")),
            "total_supply": _number_or_none(market_data.get("total_supply")),
            "max_supply": _number_or_none(market_data.get("max_supply")),
            "ath_usd": _usd_value(market_data.get("ath")),
            "atl_usd": _usd_value(market_data.get("atl")),
        },
        "last_updated": _str_or_none(payload.get("last_updated")),
    }
    return data


def _pick_defillama_protocol(
    payload: Any,
    request: Mapping[str, Any],
    *,
    coingecko_id: str | None,
) -> Mapping[str, Any] | None:
    if not isinstance(payload, list):
        return None
    ticker = str(request["ticker"]).upper()
    names = {str(request["company_name"]).lower(), *[str(item).lower() for item in request.get("aliases", [])]}
    if coingecko_id:
        names.add(coingecko_id.lower())

    def score(item: Any) -> tuple[int, float, str]:
        if not isinstance(item, Mapping):
            return (99, -1.0, "")
        symbol = str(item.get("symbol") or "").upper()
        slug = str(item.get("slug") or "").lower()
        name = str(item.get("name") or "").lower()
        gecko_id = str(item.get("gecko_id") or item.get("geckoId") or "").lower()
        tvl = _number_or_none(item.get("tvl")) or 0.0
        if gecko_id and gecko_id in names:
            primary = 0
        elif slug in names or name in names:
            primary = 1
        elif symbol == ticker and (slug == ticker.lower() or name == ticker.lower()):
            primary = 2
        else:
            primary = 9
        return (primary, -tvl, slug)

    candidates = [item for item in payload if isinstance(item, Mapping) and item.get("slug")]
    candidates.sort(key=score)
    if not candidates or score(candidates[0])[0] >= 9:
        return None
    return candidates[0]


def _normalize_defillama_protocol(candidate: Mapping[str, Any], payload: Any) -> dict[str, Any] | None:
    detail = payload if isinstance(payload, Mapping) else {}
    slug = _str_or_none(detail.get("slug")) or _str_or_none(candidate.get("slug"))
    if not slug:
        return None
    tvl_series = detail.get("tvl") if isinstance(detail.get("tvl"), list) else []
    latest_tvl = _latest_series_total(tvl_series) or _number_or_none(detail.get("tvl")) or _number_or_none(candidate.get("tvl"))
    data = {
        "slug": slug,
        "name": _str_or_none(detail.get("name")) or _str_or_none(candidate.get("name")),
        "symbol": _upper_or_none(detail.get("symbol")) or _upper_or_none(candidate.get("symbol")),
        "category": _str_or_none(detail.get("category")) or _str_or_none(candidate.get("category")),
        "chain": _str_or_none(detail.get("chain")) or _str_or_none(candidate.get("chain")),
        "chains": _compact_str_list(detail.get("chains") or candidate.get("chains"), limit=20),
        "tvl_usd": latest_tvl,
        "mcap_usd": _number_or_none(detail.get("mcap")) or _number_or_none(candidate.get("mcap")),
        "current_chain_tvls": _number_mapping(detail.get("currentChainTvls")),
        "methodology": _str_or_none(detail.get("methodology")),
        "listed_at": _str_or_none(candidate.get("listedAt")),
        "url": f"https://defillama.com/protocol/{slug}",
    }
    return data


def _normalize_defillama_fees(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    keys = (
        "total24h",
        "total48hto24h",
        "total7d",
        "total30d",
        "totalAllTime",
        "dailyFees",
        "dailyRevenue",
        "dailyProtocolRevenue",
        "dailyHoldersRevenue",
    )
    values = {key: _number_or_none(payload.get(key)) for key in keys if _number_or_none(payload.get(key)) is not None}
    if not values:
        return None
    return {
        "fees_revenue_usd": values,
        "source_time": _str_or_none(payload.get("timestamp")) or _str_or_none(payload.get("lastUpdated")),
    }


def _build_asset_data(coingecko: Mapping[str, Any] | None, defillama: Mapping[str, Any] | None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if coingecko:
        data["coin_metadata"] = {
            key: coingecko.get(key)
            for key in (
                "id",
                "symbol",
                "name",
                "web_slug",
                "asset_platform_id",
                "categories",
                "hashing_algorithm",
                "genesis_date",
                "description_en",
                "links",
                "last_updated",
            )
        }
        data["market_reference"] = coingecko.get("market_data", {})
    if defillama:
        data["defi_operating_metrics"] = defillama
    valuation = _build_valuation_context(coingecko, defillama)
    if valuation:
        data["valuation_context"] = valuation
    return data


def _build_valuation_context(coingecko: Mapping[str, Any] | None, defillama: Mapping[str, Any] | None) -> dict[str, Any]:
    market_cap = _nested_number(coingecko, "market_data", "market_cap_usd")
    fdv = _nested_number(coingecko, "market_data", "fdv_usd")
    tvl = _number_or_none(defillama.get("tvl_usd")) if defillama else None
    revenue_30d = _nested_number(defillama, "fees_revenue", "fees_revenue_usd", "total30d") if defillama else None
    valuation: dict[str, Any] = {}
    if market_cap is not None and tvl and tvl > 0:
        valuation["market_cap_to_tvl"] = round(market_cap / tvl, 4)
    if fdv is not None and tvl and tvl > 0:
        valuation["fdv_to_tvl"] = round(fdv / tvl, 4)
    if market_cap is not None and revenue_30d and revenue_30d > 0:
        valuation["market_cap_to_annualized_30d_revenue"] = round(market_cap / (revenue_30d * 12), 4)
    return valuation


def _build_sources(coingecko: Mapping[str, Any] | None, defillama: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    observed_at = _utc_now()
    sources: list[dict[str, Any]] = []
    if coingecko:
        coin_id = coingecko.get("id")
        sources.append(
            {
                "provider": "CoinGecko",
                "endpoint": "/coins/{id}",
                "url": f"https://www.coingecko.com/en/coins/{coin_id}",
                "observed_at": observed_at,
                "source_time": coingecko.get("last_updated"),
                "confidence": "reference_metadata_and_market_snapshot",
            }
        )
    if defillama:
        sources.append(
            {
                "provider": "DefiLlama",
                "endpoint": "/protocol/{protocol}",
                "url": defillama.get("url"),
                "observed_at": observed_at,
                "source_time": None,
                "confidence": "defi_protocol_operating_metrics",
            }
        )
        if defillama.get("fees_revenue"):
            sources.append(
                {
                    "provider": "DefiLlama",
                    "endpoint": "/summary/fees/{protocol}",
                    "url": defillama.get("url"),
                    "observed_at": observed_at,
                    "source_time": defillama["fees_revenue"].get("source_time"),
                    "confidence": "defi_fees_revenue_metrics",
                }
            )
    return sources


def _build_conflicts(coingecko: Mapping[str, Any] | None, defillama: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not coingecko or not defillama:
        return []
    conflicts: list[dict[str, Any]] = []
    cg_symbol = coingecko.get("symbol")
    dl_symbol = defillama.get("symbol")
    if cg_symbol and dl_symbol and str(cg_symbol).upper() != str(dl_symbol).upper():
        conflicts.append(
            {
                "field": "symbol",
                "providers": {"CoinGecko": cg_symbol, "DefiLlama": dl_symbol},
                "severity": "review_required",
                "message": "CoinGecko 与 DefiLlama 匹配到的 symbol 不一致。",
            }
        )
    cg_mcap = _nested_number(coingecko, "market_data", "market_cap_usd")
    dl_mcap = _number_or_none(defillama.get("mcap_usd"))
    if cg_mcap and dl_mcap:
        diff_ratio = abs(cg_mcap - dl_mcap) / max(cg_mcap, dl_mcap)
        if diff_ratio > 0.05:
            conflicts.append(
                {
                    "field": "market_cap_usd",
                    "providers": {"CoinGecko": cg_mcap, "DefiLlama": dl_mcap},
                    "severity": "material",
                    "diff_ratio": round(diff_ratio, 4),
                    "message": "CoinGecko 与 DefiLlama 市值口径差异超过 5%。",
                }
            )
    return conflicts


def _build_data_gaps(coingecko: Mapping[str, Any] | None, defillama: Mapping[str, Any] | None) -> list[str]:
    gaps: list[str] = []
    if not coingecko:
        gaps.append("CoinGecko 未返回可匹配币种，缺少基础元数据、市值、FDV、供应量和价格快照。")
    else:
        market_data = coingecko.get("market_data") if isinstance(coingecko.get("market_data"), Mapping) else {}
        for field, label in (
            ("market_cap_usd", "流通市值"),
            ("fdv_usd", "FDV"),
            ("circulating_supply", "流通量"),
            ("total_supply", "总供应量"),
        ):
            if market_data.get(field) is None:
                gaps.append(f"CoinGecko 未提供{label}。")
    if not defillama:
        gaps.append("DefiLlama 未匹配到 DeFi 协议；非 DeFi、纯 L1、meme 或交易所平台币可能没有 TVL/收入覆盖。")
    else:
        if defillama.get("tvl_usd") is None:
            gaps.append("DefiLlama 协议详情未提供当前 TVL。")
        if not defillama.get("fees_revenue"):
            gaps.append("DefiLlama 费用/收入摘要未返回可用费用或收入数据。")
    gaps.append("本资料包当前不覆盖代币解锁、治理提案、项目官网公告、开发者活跃度、活跃地址、交易所流入流出或大户行为。")
    return gaps


def _build_readiness(
    coingecko: Mapping[str, Any] | None,
    defillama: Mapping[str, Any] | None,
    data_gaps: list[str],
) -> dict[str, str]:
    if not coingecko and not defillama:
        return {"status": "insufficient", "reason": "CoinGecko 与 DefiLlama 均未返回可用资料。"}
    if coingecko and defillama and defillama.get("tvl_usd") is not None and defillama.get("fees_revenue"):
        return {"status": "ready", "reason": "币种基础资料、DeFi TVL 和费用/收入已覆盖。"}
    if coingecko and defillama:
        return {"status": "partial", "reason": "币种基础资料与 DeFi 协议资料部分覆盖，但仍有经营指标缺口。"}
    if coingecko:
        return {"status": "partial", "reason": "已有币种基础资料；DeFi/链上经营资料缺失或不适用。"}
    return {"status": "partial", "reason": "已有 DefiLlama 协议资料；CoinGecko 基础币种资料缺失。"}


def _build_quality(readiness: Mapping[str, str], data_gaps: list[str], conflicts: list[Mapping[str, Any]]) -> dict[str, Any]:
    status = readiness["status"]
    if status == "ready":
        coverage = 0.8
    elif status == "partial":
        coverage = 0.45
    else:
        coverage = 0.0
    warnings = [*data_gaps[:8]]
    if conflicts:
        warnings.append("存在数据来源口径冲突，报告中不得合并成单一确定值。")
    return {
        "status": "complete" if status == "ready" else ("failed" if status == "insufficient" else "partial"),
        "coverage_score": coverage,
        "freshness_status": "unknown",
        "warnings": warnings,
    }


def _build_reader_brief(pack: Mapping[str, Any]) -> str:
    asset = pack["asset"]
    readiness = pack["readiness"]
    data = pack.get("data") if isinstance(pack.get("data"), Mapping) else {}
    lines = [
        f"{asset} 的 CRYPTO 基本面资料包已返回，资料就绪度为 {_fundamental_status_zh(readiness['status'])}：{readiness['reason']}",
        "本摘要只说明资料事实与缺口，不构成投资建议。",
    ]
    metadata = data.get("coin_metadata") if isinstance(data.get("coin_metadata"), Mapping) else None
    market = data.get("market_reference") if isinstance(data.get("market_reference"), Mapping) else None
    if metadata:
        category_text = ", ".join(metadata.get("categories") or []) or "未提供分类"
        lines.append(
            f"CoinGecko 匹配到 {metadata.get('name')} ({metadata.get('symbol')})，分类：{category_text}。"
        )
    if market:
        lines.append(
            "CoinGecko 市场快照："
            f"美元价格 {_brief_value(market.get('current_price_usd'))}，"
            f"美元市值 {_brief_value(market.get('market_cap_usd'))}，"
            f"FDV {_brief_value(market.get('fdv_usd'))}，"
            f"流通量 {_brief_value(market.get('circulating_supply'))}，"
            f"总供应量 {_brief_value(market.get('total_supply'))}。"
        )
    defi = data.get("defi_operating_metrics") if isinstance(data.get("defi_operating_metrics"), Mapping) else None
    if defi:
        lines.append(
            "DefiLlama 匹配到协议 "
            f"{defi.get('name')}，类别 {defi.get('category') or '未提供'}，"
            f"链 {', '.join(defi.get('chains') or []) or defi.get('chain') or '未提供'}，"
            f"TVL（美元）{_brief_value(defi.get('tvl_usd'))}。"
        )
        fees = defi.get("fees_revenue") if isinstance(defi.get("fees_revenue"), Mapping) else None
        if fees:
            values = fees.get("fees_revenue_usd") if isinstance(fees.get("fees_revenue_usd"), Mapping) else {}
            lines.append(
                "DefiLlama 费用/收入："
                f"24小时 {_brief_value(values.get('total24h'))}，"
                f"30天 {_brief_value(values.get('total30d'))}。"
            )
    if pack.get("conflicts"):
        lines.append("存在数据来源口径冲突；报告中必须逐项列出，不能静默合并。")
    gaps = pack.get("data_gaps") if isinstance(pack.get("data_gaps"), list) else []
    if gaps:
        lines.append("数据缺口：" + "；".join(_fundamental_reader_gap(item) for item in gaps[:6]) + "。")
    return "\n".join(lines)


def _fundamental_status_zh(value: Any) -> str:
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


def _fundamental_reader_gap(value: Any) -> str:
    text = str(value).strip()
    replacements = {
        "readiness": "资料就绪度",
        "data_gaps": "资料缺口",
        "data_gap": "资料缺口",
        "provider_attempts": "来源尝试记录",
        "provider": "数据来源",
        "fees/revenue summary": "费用/收入摘要",
        "partial": "部分覆盖",
        "insufficient": "不足",
        "ready": "就绪",
        "credential": "接口凭证",
        "config": "配置",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _write_pack_files(context: Mapping[str, str], pack: Mapping[str, Any], raw_payload: Mapping[str, Any]) -> None:
    evidence_root = Path(context["evidence_root"])
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / f"{_TOOL_NAME}.json").write_text(
        json.dumps(_jsonable(pack), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    raw_dir = evidence_root / "provider_raw" / _TOOL_NAME
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "raw_payload.json").write_text(
        json.dumps(_jsonable(raw_payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = _optional_text(payload, field)
    if value is None:
        raise ValueError(f"tool_input.{field} is required")
    return value


def _optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"tool_input.{field} must be a string")
    stripped = value.strip()
    return stripped or None


def _env_text(env: Mapping[str, str], field: str) -> str | None:
    value = env.get(field)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, Mapping):
        coins = payload.get("coins")
        if isinstance(coins, list):
            return len(coins)
        data = payload.get("data")
        if isinstance(data, list):
            return len(data)
        return 1
    return 0


def _redact(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\b[A-Za-z0-9_=-]{20,}\b", "***", str(value))
    return text[:500]


def _clean_description(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = html.unescape(_HTML_TAG_RE.sub(" ", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_DESCRIPTION_CHARS] if text else None


def _compact_str_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        if len(result) >= limit:
            break
    return result


def _first_url(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None
    return _str_or_none(value)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _upper_or_none(value: Any) -> str | None:
    text = _str_or_none(value)
    return text.upper() if text else None


def _usd_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        return _number_or_none(value.get("usd"))
    return None


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _number_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        number = _number_or_none(item)
        if number is not None:
            result[str(key)] = number
    return result


def _latest_series_total(series: Any) -> float | None:
    if not isinstance(series, list) or not series:
        return None
    for item in reversed(series):
        if isinstance(item, Mapping):
            total = _number_or_none(item.get("totalLiquidityUSD"))
            if total is not None:
                return total
            total = _number_or_none(item.get("tvl"))
            if total is not None:
                return total
    return None


def _nested_number(payload: Mapping[str, Any] | None, *path: str) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return _number_or_none(current)


def _brief_value(value: Any) -> str:
    number = _number_or_none(value)
    if number is None:
        return "缺失"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number:,.0f}"
    return f"{number:.6g}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value
