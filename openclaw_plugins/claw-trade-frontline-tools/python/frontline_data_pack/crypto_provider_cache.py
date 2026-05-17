from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pymongo.errors import DuplicateKeyError, PyMongoError
import requests

from .crypto_pack_common import jsonable, payload_hash, raw_count, redact, utc_now
from .mongo_store import (
    COLLECTION_CRYPTO_PROVIDER_ATTEMPTS,
    COLLECTION_CRYPTO_PROVIDER_CACHE,
    COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS,
    check_mongo_health,
    create_mongo_store,
)


CRYPTO_CACHE_STORAGE = "mongo"
CRYPTO_NO_CACHE_STORAGE = "none"
SECRET_REDACTION = "***"
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60

_SECRET_FIELD_RE = re.compile(r"(authorization|api[_-]?key|token|secret|cookie|session)", re.IGNORECASE)
_LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_./+=:-]{24,}\b")
_SHA256_PREFIX = "sha256:"

FetchJson = Callable[..., Any]
FetchText = Callable[..., str]


@dataclass(frozen=True)
class CryptoFetchResult:
    payload: Any
    attempts: list[dict[str, Any]]


@dataclass(frozen=True)
class CryptoProviderCacheCollections:
    cache: Any | None
    attempts: Any | None
    rate_limits: Any | None


def resolve_crypto_provider_cache_collections(env: Mapping[str, str]) -> CryptoProviderCacheCollections:
    raw_enabled = env.get("CRYPTO_PROVIDER_CACHE_ENABLED")
    if raw_enabled is not None and raw_enabled.strip().lower() in {"0", "false", "no", "off"}:
        return CryptoProviderCacheCollections(cache=None, attempts=None, rate_limits=None)
    required = str(env.get("CRYPTO_PROVIDER_CACHE_REQUIRED") or "").strip().lower() in {"1", "true", "yes", "on"}
    uri = (env.get("CRYPTO_PROVIDER_CACHE_URI") or "").strip() or (env.get("CN_A_MONGODB_URI") or "").strip()
    if not uri:
        if required:
            raise ValueError("CRYPTO_PROVIDER_CACHE_REQUIRED=true but no CRYPTO_PROVIDER_CACHE_URI/CN_A_MONGODB_URI is configured")
        return CryptoProviderCacheCollections(cache=None, attempts=None, rate_limits=None)
    try:
        store = create_mongo_store(uri)
        health = check_mongo_health(store.client)
    except Exception as exc:  # noqa: BLE001
        if required:
            raise ValueError(f"CRYPTO provider cache MongoDB unavailable: {redact(str(exc))}") from exc
        return CryptoProviderCacheCollections(cache=None, attempts=None, rate_limits=None)
    if not health.ok:
        if required:
            raise ValueError(f"CRYPTO provider cache MongoDB unhealthy: {health.reason}")
        return CryptoProviderCacheCollections(cache=None, attempts=None, rate_limits=None)
    return CryptoProviderCacheCollections(
        cache=store.database[COLLECTION_CRYPTO_PROVIDER_CACHE],
        attempts=store.database[COLLECTION_CRYPTO_PROVIDER_ATTEMPTS],
        rate_limits=store.database[COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS],
    )


def fetch_json_with_crypto_provider_cache(
    *,
    context: Mapping[str, str],
    request: Mapping[str, Any],
    domain: str,
    provider: str,
    endpoint: str,
    role: str,
    source_role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    auth_mode: str,
    fetch_json: FetchJson,
    env: Mapping[str, str],
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
    schema_version: str = "crypto_provider_cache.v1",
    ttl_seconds: int | None = None,
    cache_collection: Any | None = None,
    attempts_collection: Any | None = None,
    rate_limit_collection: Any | None = None,
) -> CryptoFetchResult:
    """Fetch provider JSON with CRYPTO-scoped cache and explicit attempt semantics.

    This helper is intentionally scoped to OpenClaw worker tool calls. It stores
    provider cache material in Mongo/non-OpenViking storage only; OpenViking is
    reserved for worker evidence and approved L1/L2 material.
    """

    def _fetch_provider_payload() -> Any:
        return fetch_json(
            url,
            params=dict(params),
            headers=dict(headers),
            timeout=timeout,
            method=method,
            json_body=dict(json_body) if json_body is not None else None,
        )

    return _fetch_with_crypto_provider_cache(
        context=context,
        request=request,
        domain=domain,
        provider=provider,
        endpoint=endpoint,
        role=role,
        source_role=source_role,
        url=url,
        params=params,
        headers=headers,
        timeout=timeout,
        auth_mode=auth_mode,
        env=env,
        method=method,
        json_body=json_body,
        schema_version=schema_version,
        ttl_seconds=ttl_seconds,
        cache_collection=cache_collection,
        attempts_collection=attempts_collection,
        rate_limit_collection=rate_limit_collection,
        fetch_provider_payload=_fetch_provider_payload,
        request_values=(headers, params, json_body or {}),
        text_payload=False,
    )


def fetch_text_with_crypto_provider_cache(
    *,
    context: Mapping[str, str],
    request: Mapping[str, Any],
    domain: str,
    provider: str,
    endpoint: str,
    role: str,
    source_role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    auth_mode: str,
    fetch_text: FetchText,
    env: Mapping[str, str],
    schema_version: str = "crypto_provider_cache.v1",
    ttl_seconds: int | None = None,
    cache_collection: Any | None = None,
    attempts_collection: Any | None = None,
    rate_limit_collection: Any | None = None,
) -> CryptoFetchResult:
    def _fetch_provider_payload() -> str:
        return fetch_text(url, params=dict(params), headers=dict(headers), timeout=timeout)

    return _fetch_with_crypto_provider_cache(
        context=context,
        request=request,
        domain=domain,
        provider=provider,
        endpoint=endpoint,
        role=role,
        source_role=source_role,
        url=url,
        params=params,
        headers=headers,
        timeout=timeout,
        auth_mode=auth_mode,
        env=env,
        method="GET",
        json_body=None,
        schema_version=schema_version,
        ttl_seconds=ttl_seconds,
        cache_collection=cache_collection,
        attempts_collection=attempts_collection,
        rate_limit_collection=rate_limit_collection,
        fetch_provider_payload=_fetch_provider_payload,
        request_values=(headers, params),
        text_payload=True,
    )


def _fetch_with_crypto_provider_cache(
    *,
    context: Mapping[str, str],
    request: Mapping[str, Any],
    domain: str,
    provider: str,
    endpoint: str,
    role: str,
    source_role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    auth_mode: str,
    env: Mapping[str, str],
    method: str,
    json_body: Mapping[str, Any] | None,
    schema_version: str,
    ttl_seconds: int | None,
    cache_collection: Any | None,
    attempts_collection: Any | None,
    rate_limit_collection: Any | None,
    fetch_provider_payload: Callable[[], Any],
    request_values: tuple[Mapping[str, Any], ...],
    text_payload: bool,
) -> CryptoFetchResult:
    now = utc_now()
    ttl_seconds = ttl_seconds if ttl_seconds is not None else ttl_seconds_for_provider(
        provider=provider,
        endpoint=endpoint,
        domain=domain,
        request=request,
        env=env,
    )
    cache_enabled = crypto_cache_enabled(env=env, cache_collection=cache_collection)
    key_fields = build_crypto_cache_key_fields(
        request=request,
        domain=domain,
        provider=provider,
        endpoint=endpoint,
        method=method,
        url=url,
        params=params,
        headers=headers,
        json_body=json_body,
        schema_version=schema_version,
        auth_mode=auth_mode,
        source_role=source_role,
        tool_name=context["tool_name"],
    )
    cache_key = build_crypto_cache_document_id(key_fields)
    query_fingerprint = key_fields["query_fingerprint"]
    base = _attempt_base(
        context=context,
        request=request,
        domain=domain,
        provider=provider,
        endpoint=endpoint,
        method=method,
        role=role,
        source_role=source_role,
        auth_mode=auth_mode,
        cache_key=cache_key,
        query_fingerprint=query_fingerprint,
        timeout=timeout,
        cache_storage=CRYPTO_CACHE_STORAGE if cache_enabled else CRYPTO_NO_CACHE_STORAGE,
    )

    attempts: list[dict[str, Any]] = []
    if cache_enabled:
        inspection = inspect_crypto_provider_cache(
            cache_key=cache_key,
            key_fields=key_fields,
            now_utc=now,
            collection=cache_collection,
        )
        if inspection["status"] == "cache_hit":
            attempt = _status_attempt(
                base,
                status="cache_hit",
                started_at=now,
                finished_at=utc_now(),
                elapsed_ms=0,
                raw_count=int(inspection["raw_count"]),
                accepted_count=int(inspection["accepted_count"]),
                payload_hash=str(inspection["payload_hash"]),
                fetched_at=str(inspection["fetched_at"]),
                expires_at=str(inspection["expires_at"]),
                freshness_age_seconds=inspection["freshness_age_seconds"],
            )
            attempts.append(attempt)
            insert_crypto_provider_attempt(attempt, collection=attempts_collection)
            return CryptoFetchResult(payload=inspection["raw_payload"], attempts=attempts)
        attempts.append(
            _status_attempt(
                base,
                status=str(inspection["status"]),
                started_at=now,
                finished_at=utc_now(),
                elapsed_ms=0,
                error_code=inspection.get("error_code"),
                error_message_redacted=inspection.get("error_message_redacted"),
                freshness_age_seconds=inspection.get("freshness_age_seconds"),
            )
        )

    rate_limit = claim_crypto_provider_rate_limit(
        provider=provider,
        endpoint=endpoint,
        env=env,
        collection=rate_limit_collection,
    )
    if not rate_limit["allowed"]:
        attempt = _status_attempt(
            base,
            status="rate_limited",
            started_at=now,
            finished_at=utc_now(),
            elapsed_ms=0,
            error_code="rate_limited",
            error_message_redacted=rate_limit["reason"],
        )
        attempts.append(attempt)
        insert_crypto_provider_attempts(attempts, collection=attempts_collection)
        return CryptoFetchResult(payload=None, attempts=attempts)

    started_at = utc_now()
    started = time.perf_counter()
    payload: Any = None
    status = "success"
    error_code: str | None = None
    error_message: str | None = None
    try:
        payload = fetch_provider_payload()
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 429:
            status = "rate_limited"
            error_code = "http_429"
        else:
            status = "error"
            error_code = f"http_{status_code}" if status_code else "http_error"
        error_message = redact_secret_text(str(exc), request_values=request_values)
    except requests.Timeout as exc:
        status = "error"
        error_code = "timeout"
        error_message = redact_secret_text(str(exc), request_values=request_values)
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error_code = exc.__class__.__name__
        error_message = redact_secret_text(str(exc), request_values=request_values)
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    payload_raw_count = 1 if text_payload and payload else raw_count(payload)
    attempt = _status_attempt(
        base,
        status=status,
        started_at=started_at,
        finished_at=utc_now(),
        elapsed_ms=elapsed_ms,
        raw_count=payload_raw_count,
        payload_hash=payload_hash(payload) if payload is not None else None,
        fetched_at=utc_now() if status == "success" else None,
        expires_at=_expires_at_utc(ttl_seconds) if status == "success" else None,
        error_code=error_code,
        error_message_redacted=redact(error_message) if error_message else None,
    )
    attempts.append(attempt)
    return CryptoFetchResult(payload=payload, attempts=attempts)


def commit_crypto_provider_cache(
    *,
    attempt: Mapping[str, Any],
    payload: Any,
    collection: Any | None,
) -> str | None:
    if collection is None:
        return None
    if attempt.get("status") not in {"success", "empty"}:
        return None
    cache_key = str(attempt.get("cache_key") or "")
    if not cache_key.startswith(_SHA256_PREFIX):
        return None
    document = {
        "_id": cache_key,
        "cache_key": cache_key,
        "market": attempt.get("market"),
        "domain": attempt.get("domain"),
        "ticker": attempt.get("ticker"),
        "date_range": attempt.get("date_range"),
        "provider": attempt.get("provider"),
        "endpoint": attempt.get("endpoint"),
        "method": attempt.get("method"),
        "source_role": attempt.get("source_role"),
        "auth_mode": attempt.get("auth_mode"),
        "tool_name": attempt.get("tool_name"),
        "schema_version": attempt.get("schema_version"),
        "query_fingerprint": attempt.get("query_fingerprint"),
        "fetched_at": attempt.get("fetched_at"),
        "expires_at": attempt.get("expires_at"),
        "payload_hash": attempt.get("payload_hash"),
        "raw_count": attempt.get("raw_count", 0),
        "accepted_count": attempt.get("accepted_count", 0),
        "raw_payload": jsonable(payload),
        "cache_storage": CRYPTO_CACHE_STORAGE,
        "cache_is_openviking": False,
    }
    _validate_crypto_cache_document(document)
    set_document = {key: value for key, value in document.items() if key != "_id"}
    try:
        collection.update_one(
            {"_id": cache_key},
            {"$set": set_document, "$setOnInsert": {"_id": cache_key}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(attempt, dict):
            attempt["cache_write_error_code"] = "cache_write_failed"
            attempt["cache_write_error_redacted"] = redact(str(exc))
        return None
    return f"{COLLECTION_CRYPTO_PROVIDER_CACHE}:{cache_key}"


def inspect_crypto_provider_cache(
    *,
    cache_key: str,
    key_fields: Mapping[str, Any],
    now_utc: str,
    collection: Any | None,
) -> dict[str, Any]:
    if collection is None:
        return {"status": "cache_miss"}
    try:
        doc = collection.find_one({"_id": cache_key})
    except PyMongoError as exc:
        return {"status": "schema_invalid", "error_code": "cache_read_error", "error_message_redacted": redact(str(exc))}
    except Exception as exc:
        return {"status": "schema_invalid", "error_code": "cache_read_error", "error_message_redacted": redact(str(exc))}
    if doc is None:
        return {"status": "cache_miss"}
    if not isinstance(doc, Mapping):
        return {"status": "schema_invalid", "error_code": "cache_doc_not_mapping"}
    if _crypto_cache_doc_invalid(doc, key_fields):
        return {"status": "schema_invalid", "error_code": "cache_doc_schema_invalid"}
    expires_at = _parse_utc(str(doc["expires_at"]))
    now = _parse_utc(now_utc)
    freshness_age_seconds = _freshness_age_seconds(str(doc["fetched_at"]), now)
    payload = doc.get("raw_payload")
    if expires_at <= now:
        return {
            "status": "cache_stale",
            "freshness_age_seconds": freshness_age_seconds,
            "payload_hash": doc["payload_hash"],
        }
    return {
        "status": "cache_hit",
        "raw_payload": payload,
        "payload_hash": doc["payload_hash"],
        "raw_count": int(doc["raw_count"]),
        "accepted_count": int(doc["accepted_count"]),
        "fetched_at": doc["fetched_at"],
        "expires_at": doc["expires_at"],
        "freshness_age_seconds": freshness_age_seconds,
    }


def insert_crypto_provider_attempts(attempts: list[dict[str, Any]], *, collection: Any | None) -> None:
    for attempt in attempts:
        insert_crypto_provider_attempt(attempt, collection=collection)


def insert_crypto_provider_attempt(attempt: Mapping[str, Any], *, collection: Any | None) -> None:
    if collection is None:
        return
    payload = dict(attempt)
    stable_text = "\n".join(
        str(payload.get(field) or "")
        for field in ("run_id", "call_id", "provider", "endpoint", "started_at", "status", "cache_key")
    )
    payload["_id"] = _SHA256_PREFIX + hashlib.sha256(stable_text.encode("utf-8")).hexdigest()
    try:
        collection.insert_one(payload)
    except DuplicateKeyError:
        existing = collection.find_one({"_id": payload["_id"]})
        if isinstance(existing, Mapping):
            existing_payload = dict(existing)
            existing_payload.pop("_id", None)
            expected_payload = dict(payload)
            expected_payload.pop("_id", None)
            if existing_payload == expected_payload:
                return
        raise


def provider_attempt_gap_messages(attempts: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> list[str]:
    gaps: list[str] = []
    for attempt in attempts:
        provider = attempt.get("provider") or "provider"
        endpoint = attempt.get("endpoint") or "endpoint"
        status = attempt.get("status")
        if status == "cache_hit" and int(attempt.get("accepted_count") or 0) <= 0:
            gaps.append(f"{provider} {endpoint} 命中缓存但 accepted_count=0；不能视为资料覆盖完成。")
        if attempt.get("cache_write_error_code"):
            gaps.append(f"{provider} {endpoint} provider 数据已取得，但写入 CRYPTO provider cache 失败；本轮资料可用但不能声称已共享缓存。")
        elif status == "rate_limited":
            gaps.append(f"{provider} {endpoint} 被限流；本轮未取得 fresh provider 数据。")
        elif status == "config_blocked":
            gaps.append(f"{provider} {endpoint} 配置缺失或非法；本轮未取得 provider 数据。")
        elif status == "schema_invalid":
            gaps.append(f"{provider} {endpoint} cache 或响应结构不合格；本轮不可作为有效资料。")
        elif status == "error":
            code = attempt.get("error_code") or "error"
            gaps.append(f"{provider} {endpoint} 请求失败：{code}。")
        elif status == "empty":
            gaps.append(f"{provider} {endpoint} 返回成功但 accepted_count=0；不能视为资料覆盖完成。")
    return gaps


def claim_crypto_provider_rate_limit(
    *,
    provider: str,
    endpoint: str,
    env: Mapping[str, str],
    collection: Any | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if collection is None:
        return {"allowed": True, "reason": None}
    limit = _rate_limit_for_provider(provider=provider, endpoint=endpoint, env=env)
    if limit <= 0:
        return {"allowed": False, "reason": f"{provider} {endpoint} rate limit is 0"}
    now = datetime.now(timezone.utc) if now is None else now
    window_seconds = _int_env(env, "CRYPTO_PROVIDER_RATE_LIMIT_WINDOW_SECONDS", DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
    window_start_epoch = int(now.timestamp()) // window_seconds * window_seconds
    window_start = datetime.fromtimestamp(window_start_epoch, tz=timezone.utc)
    window_expires_at = window_start + timedelta(seconds=window_seconds)
    key_payload = {"provider": provider, "endpoint": endpoint, "window_start": _iso(window_start)}
    key = _SHA256_PREFIX + hashlib.sha256(stable_json_dumps(key_payload).encode("utf-8")).hexdigest()
    doc = collection.find_one({"_id": key})
    if isinstance(doc, Mapping) and int(doc.get("count", 0)) >= limit:
        return {"allowed": False, "reason": f"{provider} {endpoint} rate limit exceeded"}
    collection.update_one(
        {"_id": key},
        {
            "$setOnInsert": {
                "_id": key,
                "provider": provider,
                "endpoint": endpoint,
                "window_start": _iso(window_start),
                "window_expires_at": _iso(window_expires_at),
                "limit": limit,
            },
            "$inc": {"count": 1},
        },
        upsert=True,
    )
    return {"allowed": True, "reason": None}


def build_crypto_cache_key_fields(
    *,
    request: Mapping[str, Any],
    domain: str,
    provider: str,
    endpoint: str,
    method: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    json_body: Mapping[str, Any] | None,
    schema_version: str,
    auth_mode: str,
    source_role: str,
    tool_name: str,
) -> dict[str, Any]:
    sanitized = sanitized_request_parts(url=url, params=params, headers=headers, json_body=json_body)
    fingerprint_payload = {
        "url": sanitized["url"],
        "params": sanitized["params"],
        "headers": sanitized["headers"],
        "json_body": sanitized["json_body"],
        "method": method.upper(),
    }
    query_fingerprint = _SHA256_PREFIX + hashlib.sha256(stable_json_dumps(fingerprint_payload).encode("utf-8")).hexdigest()
    return {
        "market": "CRYPTO",
        "domain": domain,
        "ticker": str(request.get("ticker") or ""),
        "date_range": {"start_date": request.get("start_date"), "end_date": request.get("end_date")},
        "provider": provider,
        "endpoint": endpoint,
        "method": method.upper(),
        "query_fingerprint": query_fingerprint,
        "schema_version": schema_version,
        "auth_mode": auth_mode,
        "source_role": source_role,
        "tool_name": tool_name,
    }


def build_crypto_cache_document_id(key_fields: Mapping[str, Any]) -> str:
    return _SHA256_PREFIX + hashlib.sha256(stable_json_dumps(key_fields).encode("utf-8")).hexdigest()


def sanitized_request_parts(
    *,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    json_body: Mapping[str, Any] | None,
) -> dict[str, Any]:
    secret_values = _collect_secret_values(headers, params, json_body or {})
    return {
        "url": redact_url(url, secret_values=secret_values),
        "params": redact_mapping(params, secret_values=secret_values),
        "headers": redact_mapping(headers, secret_values=secret_values),
        "json_body": redact_mapping(json_body or {}, secret_values=secret_values),
    }


def redact_url(url: str, *, secret_values: set[str] | None = None) -> str:
    secret_values = secret_values or set()
    parsed = urlsplit(url)
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        query_pairs.append((key, SECRET_REDACTION if _looks_secret_key(key) or value in secret_values else _redact_text(value, secret_values)))
    path_parts: list[str] = []
    for part in parsed.path.split("/"):
        replacement = _redact_text(part, secret_values)
        if part.startswith("bot") and len(part) > 3:
            replacement = "bot" + SECRET_REDACTION
        path_parts.append(replacement)
    return urlunsplit((parsed.scheme, parsed.netloc, "/".join(path_parts), urlencode(query_pairs), parsed.fragment))


def redact_mapping(value: Any, *, secret_values: set[str] | None = None) -> Any:
    secret_values = secret_values or set()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = SECRET_REDACTION if _looks_secret_key(key_text) else redact_mapping(item, secret_values=secret_values)
        return result
    if isinstance(value, list):
        return [redact_mapping(item, secret_values=secret_values) for item in value]
    if isinstance(value, tuple):
        return [redact_mapping(item, secret_values=secret_values) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    return value


def redact_secret_text(value: str, *, request_values: tuple[Mapping[str, Any], ...]) -> str:
    secrets: set[str] = set()
    for item in request_values:
        secrets.update(_collect_secret_values(item))
    return _redact_text(value, secrets)


def ttl_seconds_for_provider(
    *,
    provider: str,
    endpoint: str,
    domain: str,
    request: Mapping[str, Any],
    env: Mapping[str, str],
) -> int:
    provider_l = provider.lower()
    endpoint_l = endpoint.lower()
    if provider_l == "bb" and endpoint_l == "build_trade_context":
        return _int_env(env, "CRYPTO_CACHE_TTL_BB_TRADE_CONTEXT_SECONDS", 300)
    if provider_l == "binance" and endpoint_l == "/api/v3/klines":
        if str(request.get("end_date") or "") == str(request.get("current_date") or ""):
            return _int_env(env, "CRYPTO_CACHE_TTL_BINANCE_SPOT_OHLCV_SECONDS", 900)
        return _int_env(env, "CRYPTO_CACHE_TTL_BINANCE_HISTORY_SECONDS", 21600)
    if provider_l == "coingecko":
        return _int_env(env, "CRYPTO_CACHE_TTL_COINGECKO_SECONDS", 900)
    if provider_l == "defillama":
        return _int_env(env, "CRYPTO_CACHE_TTL_DEFILLAMA_SECONDS", 21600)
    if provider_l == "alternative.me":
        return _int_env(env, "CRYPTO_CACHE_TTL_ALTERNATIVE_ME_SECONDS", 43200)
    if provider_l == "polymarket":
        return _int_env(env, "CRYPTO_CACHE_TTL_POLYMARKET_SECONDS", 300)
    if provider_l in {"lunarcrush"}:
        return _int_env(env, "CRYPTO_CACHE_TTL_LUNARCRUSH_SECONDS", 900)
    if provider_l in {"x", "reddit", "telegram", "discord"}:
        return _int_env(env, "CRYPTO_CACHE_TTL_SOCIAL_SECONDS", 300)
    if "search" in provider_l or "search" in endpoint_l or domain == "news":
        return _int_env(env, "CRYPTO_CACHE_TTL_NEWS_SECONDS", 1800)
    return _int_env(env, "CRYPTO_CACHE_TTL_DEFAULT_SECONDS", 1800)


def crypto_cache_enabled(*, env: Mapping[str, str], cache_collection: Any | None) -> bool:
    if cache_collection is None:
        return False
    raw = env.get("CRYPTO_PROVIDER_CACHE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def stable_json_dumps(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _attempt_base(
    *,
    context: Mapping[str, str],
    request: Mapping[str, Any],
    domain: str,
    provider: str,
    endpoint: str,
    method: str,
    role: str,
    source_role: str,
    auth_mode: str,
    cache_key: str,
    query_fingerprint: str,
    timeout: int,
    cache_storage: str,
) -> dict[str, Any]:
    return {
        "run_id": context["run_id"],
        "stage": context["stage"],
        "worker_id": context["worker_id"],
        "call_id": context["call_id"],
        "tool_name": context["tool_name"],
        "market": "CRYPTO",
        "domain": domain,
        "ticker": request.get("ticker"),
        "date_range": {"start_date": request.get("start_date"), "end_date": request.get("end_date")},
        "provider": provider,
        "endpoint": endpoint,
        "method": method.upper(),
        "role": role,
        "source_role": source_role,
        "auth_mode": auth_mode,
        "cache_key": cache_key,
        "query_fingerprint": query_fingerprint,
        "schema_version": "crypto_provider_cache.v1",
        "timeout_ms": timeout * 1000,
        "cache_storage": cache_storage,
        "cache_is_openviking": False,
    }


def _status_attempt(
    base: Mapping[str, Any],
    *,
    status: str,
    started_at: str,
    finished_at: str,
    elapsed_ms: int,
    raw_count: int = 0,
    accepted_count: int = 0,
    payload_hash: str | None = None,
    fetched_at: str | None = None,
    expires_at: str | None = None,
    freshness_age_seconds: int | None = None,
    error_code: str | None = None,
    error_message_redacted: str | None = None,
) -> dict[str, Any]:
    attempt = dict(base)
    attempt.update(
        {
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_ms": elapsed_ms,
            "raw_count": raw_count,
            "accepted_count": accepted_count,
            "payload_hash": payload_hash,
            "fetched_at": fetched_at,
            "expires_at": expires_at,
            "freshness_age_seconds": freshness_age_seconds,
            "error_code": error_code,
            "error_message_redacted": error_message_redacted,
        }
    )
    return attempt


def _crypto_cache_doc_invalid(doc: Mapping[str, Any], key_fields: Mapping[str, Any]) -> bool:
    required = (
        "cache_key",
        "market",
        "domain",
        "ticker",
        "date_range",
        "provider",
        "endpoint",
        "method",
        "source_role",
        "auth_mode",
        "tool_name",
        "schema_version",
        "query_fingerprint",
        "fetched_at",
        "expires_at",
        "payload_hash",
        "raw_count",
        "accepted_count",
        "raw_payload",
        "cache_storage",
        "cache_is_openviking",
    )
    if any(field not in doc for field in required):
        return True
    if doc.get("cache_is_openviking") is not False:
        return True
    if doc.get("cache_storage") != CRYPTO_CACHE_STORAGE:
        return True
    if any("viking://" in str(doc.get(field) or "") for field in ("cache_key", "payload_hash")):
        return True
    for field in ("market", "domain", "ticker", "provider", "endpoint", "method", "source_role", "auth_mode", "tool_name", "schema_version", "query_fingerprint"):
        if doc.get(field) != key_fields.get(field):
            return True
    if not str(doc.get("payload_hash") or "").startswith(_SHA256_PREFIX):
        return True
    return False


def _validate_crypto_cache_document(document: Mapping[str, Any]) -> None:
    if document.get("cache_is_openviking") is not False:
        raise ValueError("crypto provider cache must not use OpenViking as cache source")
    if "viking://" in stable_json_dumps(document):
        raise ValueError("crypto provider cache document must not contain viking:// refs")
    for field in ("cache_key", "payload_hash", "query_fingerprint"):
        if not str(document.get(field) or "").startswith(_SHA256_PREFIX):
            raise ValueError(f"crypto provider cache document {field} must be sha256")
    if document.get("raw_payload") is None:
        raise ValueError("crypto provider cache document raw_payload is required")


def _collect_secret_values(*values: Any) -> set[str]:
    secrets: set[str] = set()
    for value in values:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _looks_secret_key(str(key)):
                    text = str(item)
                    if text:
                        secrets.add(text)
                secrets.update(_collect_secret_values(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                secrets.update(_collect_secret_values(item))
    return {secret for secret in secrets if secret and secret != SECRET_REDACTION}


def _looks_secret_key(key: str) -> bool:
    return bool(_SECRET_FIELD_RE.search(key))


def _redact_text(value: str, secret_values: set[str]) -> str:
    text = str(value)
    for secret in sorted(secret_values, key=len, reverse=True):
        if secret:
            text = text.replace(secret, SECRET_REDACTION)
    text = re.sub(r"Bearer\s+[^\s,;]+", f"Bearer {SECRET_REDACTION}", text, flags=re.IGNORECASE)
    text = _LONG_SECRET_RE.sub(SECRET_REDACTION, text)
    return text


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _expires_at_utc(ttl_seconds: int) -> str:
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds))


def _freshness_age_seconds(fetched_at: str, now: datetime) -> int:
    try:
        fetched = _parse_utc(fetched_at)
    except ValueError:
        return 0
    return max(0, int((now - fetched).total_seconds()))


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return default
    return max(0, parsed)


def _rate_limit_for_provider(*, provider: str, endpoint: str, env: Mapping[str, str]) -> int:
    normalized_provider = re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_")
    normalized_endpoint = re.sub(r"[^A-Z0-9]+", "_", endpoint.upper()).strip("_")
    specific = env.get(f"CRYPTO_RATE_LIMIT_{normalized_provider}_{normalized_endpoint}_PER_MINUTE")
    if specific is not None:
        return _int_env(env, f"CRYPTO_RATE_LIMIT_{normalized_provider}_{normalized_endpoint}_PER_MINUTE", 60)
    return _int_env(env, f"CRYPTO_RATE_LIMIT_{normalized_provider}_PER_MINUTE", 60)


__all__ = [
    "COLLECTION_CRYPTO_PROVIDER_ATTEMPTS",
    "COLLECTION_CRYPTO_PROVIDER_CACHE",
    "COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS",
    "CryptoFetchResult",
    "CryptoProviderCacheCollections",
    "build_crypto_cache_document_id",
    "build_crypto_cache_key_fields",
    "claim_crypto_provider_rate_limit",
    "commit_crypto_provider_cache",
    "fetch_json_with_crypto_provider_cache",
    "fetch_text_with_crypto_provider_cache",
    "insert_crypto_provider_attempts",
    "provider_attempt_gap_messages",
    "redact_mapping",
    "redact_secret_text",
    "redact_url",
    "resolve_crypto_provider_cache_collections",
    "sanitized_request_parts",
    "ttl_seconds_for_provider",
]
