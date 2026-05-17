from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Mapping

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.crypto_provider_cache import (  # noqa: E402
    build_crypto_cache_document_id,
    build_crypto_cache_key_fields,
    claim_crypto_provider_rate_limit,
    commit_crypto_provider_cache,
    fetch_json_with_crypto_provider_cache,
    fetch_text_with_crypto_provider_cache,
    redact_secret_text,
    redact_url,
    sanitized_request_parts,
)
from frontline_data_pack.crypto_pack_common import finish_attempt, payload_hash  # noqa: E402


SECRET = "SECRET_TOKEN_1234567890abcdef"


def test_crypto_cache_key_is_stable_and_does_not_leak_secrets() -> None:
    first = _key_fields(params={"symbol": "BTCUSDT", "api_key": SECRET, "limit": 1000})
    second = _key_fields(params={"limit": 1000, "api_key": SECRET, "symbol": "BTCUSDT"})

    assert first == second
    cache_id = build_crypto_cache_document_id(first)
    rendered = repr({"cache_id": cache_id, "key": first})
    assert SECRET not in rendered


def test_secret_redaction_covers_path_query_headers_body_and_error_text() -> None:
    url = f"https://api.telegram.org/bot{SECRET}/getUpdates?api_key={SECRET}&q=BTC"
    sanitized = sanitized_request_parts(
        url=url,
        params={"token": SECRET, "q": "BTC"},
        headers={"Authorization": f"Bearer {SECRET}", "User-Agent": "test"},
        json_body={"nested": {"secret": SECRET, "plain": "ok"}},
    )

    assert SECRET not in repr(sanitized)
    redacted_url = redact_url(url, secret_values={SECRET})
    assert SECRET not in redacted_url
    assert "bot***" in redacted_url
    assert SECRET not in redact_secret_text(f"provider failed with {SECRET}", request_values=({"token": SECRET},))


def test_fresh_cache_hit_does_not_call_provider_and_is_not_success() -> None:
    collection = _FakeCollection()
    key_fields = _key_fields(clean=True)
    cache_key = build_crypto_cache_document_id(key_fields)
    payload = [["cached"]]
    collection.docs[cache_key] = _cache_doc(cache_key=cache_key, key_fields=key_fields, payload=payload, accepted_count=1)

    result = fetch_json_with_crypto_provider_cache(
        context=_context(),
        request=_request(),
        domain="market",
        provider="Binance",
        endpoint="/api/v3/klines",
        role="chart_ohlcv_supplement",
        source_role="chart_ohlcv_supplement_not_coinglass_replacement",
        url="https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT"},
        headers={},
        timeout=15,
        auth_mode="public",
        fetch_json=lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
        env={},
        cache_collection=collection,
    )

    assert result.payload == payload
    assert [attempt["status"] for attempt in result.attempts] == ["cache_hit"]
    assert result.attempts[0]["status"] != "success"
    assert result.attempts[0]["accepted_count"] == 1
    assert result.attempts[0]["cache_is_openviking"] is False


def test_stale_cache_records_stale_then_calls_provider_and_writes_cache() -> None:
    collection = _FakeCollection()
    key_fields = _key_fields(clean=True)
    cache_key = build_crypto_cache_document_id(key_fields)
    collection.docs[cache_key] = _cache_doc(
        cache_key=cache_key,
        key_fields=key_fields,
        payload=[],
        accepted_count=0,
        expires_at=_iso(datetime.now(timezone.utc) - timedelta(seconds=10)),
    )

    result = fetch_json_with_crypto_provider_cache(
        context=_context(),
        request=_request(),
        domain="market",
        provider="Binance",
        endpoint="/api/v3/klines",
        role="chart_ohlcv_supplement",
        source_role="chart_ohlcv_supplement_not_coinglass_replacement",
        url="https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT"},
        headers={},
        timeout=15,
        auth_mode="public",
        fetch_json=lambda *_args, **_kwargs: [[1, "1", "2", "1", "2", "100"]],
        env={},
        cache_collection=collection,
    )
    finish_attempt(result.attempts[-1], result.payload, accepted_count=1)
    commit_crypto_provider_cache(attempt=result.attempts[-1], payload=result.payload, collection=collection)

    assert [attempt["status"] for attempt in result.attempts] == ["cache_stale", "success"]
    assert "cache_write_error_code" not in result.attempts[-1]
    assert collection.docs[cache_key]["accepted_count"] == 1
    assert "viking://" not in repr(collection.docs[cache_key])


def test_rate_limit_blocks_provider_and_records_rate_limited_attempt() -> None:
    result = fetch_json_with_crypto_provider_cache(
        context=_context(),
        request=_request(),
        domain="market",
        provider="Binance",
        endpoint="/api/v3/klines",
        role="chart_ohlcv_supplement",
        source_role="chart_ohlcv_supplement_not_coinglass_replacement",
        url="https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT"},
        headers={},
        timeout=15,
        auth_mode="public",
        fetch_json=lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
        env={"CRYPTO_RATE_LIMIT_BINANCE_PER_MINUTE": "0"},
        rate_limit_collection=_FakeCollection(),
    )

    assert result.payload is None
    assert result.attempts[-1]["status"] == "rate_limited"
    assert result.attempts[-1]["error_code"] == "rate_limited"


def test_cached_empty_remains_cache_hit_but_accepted_count_is_zero() -> None:
    collection = _FakeCollection()
    key_fields = _key_fields(clean=True)
    cache_key = build_crypto_cache_document_id(key_fields)
    collection.docs[cache_key] = _cache_doc(cache_key=cache_key, key_fields=key_fields, payload=[], accepted_count=0)

    result = fetch_json_with_crypto_provider_cache(
        context=_context(),
        request=_request(),
        domain="market",
        provider="Binance",
        endpoint="/api/v3/klines",
        role="chart_ohlcv_supplement",
        source_role="chart_ohlcv_supplement_not_coinglass_replacement",
        url="https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT"},
        headers={},
        timeout=15,
        auth_mode="public",
        fetch_json=lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
        env={},
        cache_collection=collection,
    )

    assert result.attempts[0]["status"] == "cache_hit"
    assert result.attempts[0]["accepted_count"] == 0


def test_text_provider_cache_hit_does_not_call_provider_and_keeps_source_role() -> None:
    collection = _FakeCollection()
    first = fetch_text_with_crypto_provider_cache(
        context=_context(tool_name="crypto_news_data_pack", worker_id="news_analyst"),
        request=_request(),
        domain="news",
        provider="OfficialSource",
        endpoint="https://example.com/feed.xml",
        role="official_announcement",
        source_role="official_announcement_original_source",
        url="https://example.com/feed.xml",
        params={},
        headers={},
        timeout=15,
        auth_mode="public_or_configured_url",
        fetch_text=lambda *_args, **_kwargs: "<rss>AAVE official item</rss>",
        env={},
        cache_collection=collection,
    )
    finish_attempt(first.attempts[-1], first.payload, accepted_count=1)
    commit_crypto_provider_cache(attempt=first.attempts[-1], payload=first.payload, collection=collection)

    second = fetch_text_with_crypto_provider_cache(
        context=_context(tool_name="crypto_news_data_pack", worker_id="news_analyst"),
        request=_request(),
        domain="news",
        provider="OfficialSource",
        endpoint="https://example.com/feed.xml",
        role="official_announcement",
        source_role="official_announcement_original_source",
        url="https://example.com/feed.xml",
        params={},
        headers={},
        timeout=15,
        auth_mode="public_or_configured_url",
        fetch_text=lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
        env={},
        cache_collection=collection,
    )

    assert second.payload == "<rss>AAVE official item</rss>"
    assert second.attempts[0]["status"] == "cache_hit"
    assert second.attempts[0]["raw_count"] == 1
    assert second.attempts[0]["source_role"] == "official_announcement_original_source"


def test_rate_limit_claim_uses_window_collection() -> None:
    collection = _FakeCollection()

    first = claim_crypto_provider_rate_limit(
        provider="Example",
        endpoint="/limited",
        env={"CRYPTO_RATE_LIMIT_EXAMPLE_PER_MINUTE": "1"},
        collection=collection,
    )
    second = claim_crypto_provider_rate_limit(
        provider="Example",
        endpoint="/limited",
        env={"CRYPTO_RATE_LIMIT_EXAMPLE_PER_MINUTE": "1"},
        collection=collection,
    )

    assert first["allowed"] is True
    assert second["allowed"] is False


def _key_fields(*, params: Mapping[str, Any] | None = None, clean: bool = False) -> dict[str, Any]:
    return build_crypto_cache_key_fields(
        request=_request(),
        domain="market",
        provider="Binance",
        endpoint="/api/v3/klines",
        method="GET",
        url="https://api.binance.com/api/v3/klines" if clean else f"https://api.binance.com/api/v3/klines?api_key={SECRET}",
        params=params or {"symbol": "BTCUSDT"},
        headers={} if clean else {"Authorization": f"Bearer {SECRET}"},
        json_body=None if clean else {"secret": SECRET},
        schema_version="crypto_provider_cache.v1",
        auth_mode="public",
        source_role="chart_ohlcv_supplement_not_coinglass_replacement",
        tool_name="crypto_market_data_pack",
    )


def _context(*, tool_name: str = "crypto_market_data_pack", worker_id: str = "market_analyst") -> dict[str, str]:
    return {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": worker_id,
        "call_id": "call-1",
        "tool_name": tool_name,
    }


def _request() -> dict[str, str]:
    return {
        "ticker": "BTC",
        "start_date": "2026-05-01",
        "end_date": "2026-05-15",
        "current_date": "2026-05-15",
    }


def _cache_doc(
    *,
    cache_key: str,
    key_fields: Mapping[str, Any],
    payload: Any,
    accepted_count: int,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return {
        "_id": cache_key,
        "cache_key": cache_key,
        "market": key_fields["market"],
        "domain": key_fields["domain"],
        "ticker": key_fields["ticker"],
        "date_range": key_fields["date_range"],
        "provider": key_fields["provider"],
        "endpoint": key_fields["endpoint"],
        "method": key_fields["method"],
        "source_role": key_fields["source_role"],
        "auth_mode": key_fields["auth_mode"],
        "tool_name": key_fields["tool_name"],
        "schema_version": key_fields["schema_version"],
        "query_fingerprint": key_fields["query_fingerprint"],
        "fetched_at": _iso(datetime.now(timezone.utc) - timedelta(seconds=5)),
        "expires_at": expires_at or _iso(datetime.now(timezone.utc) + timedelta(seconds=300)),
        "payload_hash": payload_hash(payload),
        "raw_count": len(payload) if isinstance(payload, list) else 1,
        "accepted_count": accepted_count,
        "raw_payload": payload,
        "cache_storage": "mongo",
        "cache_is_openviking": False,
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
        if "_id" in update.get("$set", {}):
            raise ValueError("Updating the path '_id' would create a conflict at '_id'")
        doc = self.docs.get(key)
        if doc is None:
            if not upsert:
                return
            doc = {"_id": key}
            doc.update(update.get("$setOnInsert", {}))
            self.docs[key] = doc
        doc.update(update.get("$set", {}))
        for field, increment in update.get("$inc", {}).items():
            doc[field] = int(doc.get(field, 0)) + int(increment)

    def insert_one(self, document: Mapping[str, Any]) -> None:
        self.inserted.append(dict(document))
