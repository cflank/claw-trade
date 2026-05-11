from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.cache import (  # noqa: E402
    build_provider_attempt_id,
    build_provider_cache_document_id,
    insert_provider_attempt,
    inspect_provider_cache,
    upsert_provider_cache,
)
from frontline_data_pack.errors import (  # noqa: E402
    MONGO_SCHEMA_INVALID,
    MONGO_UNAVAILABLE,
    FrontlineValidationError,
)
from frontline_data_pack.models import (  # noqa: E402
    ProviderAttempt,
    ProviderCacheDocument,
    ProviderCacheKey,
)


class _FakeCollection:
    def __init__(self, *, find_one_doc: MappingLike | None = None, fail: bool = False) -> None:
        self.find_one_doc = find_one_doc
        self.fail = fail
        self.last_find_query: dict[str, Any] | None = None
        self.last_update: dict[str, Any] | None = None
        self.inserted_doc: dict[str, Any] | None = None

    def find_one(self, query: dict[str, Any]) -> MappingLike | None:
        if self.fail:
            raise RuntimeError("mongo down")
        self.last_find_query = query
        return self.find_one_doc

    def update_one(self, flt: dict[str, Any], update: dict[str, Any], *, upsert: bool) -> None:
        if self.fail:
            raise RuntimeError("mongo down")
        self.last_update = {"filter": flt, "update": update, "upsert": upsert}

    def insert_one(self, doc: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("mongo down")
        self.inserted_doc = doc


type MappingLike = dict[str, Any]


def test_t_mdb_002_inspect_provider_cache_returns_hit_and_mongo_ref_for_fresh_doc() -> None:
    now = datetime.now(UTC)
    key = _cache_key()
    cache_id = build_provider_cache_document_id(key)
    collection = _FakeCollection(
        find_one_doc={
            "_id": cache_id,
            "market": "CN_A",
            "domain": "news",
            "ticker": "600519.SH",
            "provider": "akshare",
            "endpoint": "stock_news_em",
            "query_fingerprint": QUERY_FINGERPRINT,
            "schema_version": "cn_a_news_pack.v1",
            "fetched_at": (now - timedelta(minutes=5)).isoformat(),
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
            "payload_hash": SHA,
            "raw_payload_ref": "viking://resources/workflow/run-1/frontline/news_analyst/call-1/provider_raw/1.json",
            "raw_payload_size_bytes": 128,
            "normalized_ref": None,
            "raw_count": 10,
            "accepted_count": 7,
            "schema_validated_at": now.isoformat(),
        }
    )

    result = inspect_provider_cache(
        key,
        now_utc=now.isoformat(),
        collection=collection,
    )

    assert result.status == "cache_hit"
    assert result.cache_ref == f"cn_a_provider_cache:{cache_id}"
    assert result.reason is None
    assert result.freshness_age_seconds is not None


def test_t_mdb_002_inspect_provider_cache_returns_stale_when_expired() -> None:
    now = datetime.now(UTC)
    key = _cache_key()
    collection = _FakeCollection(
        find_one_doc={
            "_id": build_provider_cache_document_id(key),
            "market": "CN_A",
            "domain": "news",
            "ticker": "600519.SH",
            "provider": "akshare",
            "endpoint": "stock_news_em",
            "query_fingerprint": QUERY_FINGERPRINT,
            "schema_version": "cn_a_news_pack.v1",
            "fetched_at": (now - timedelta(hours=2)).isoformat(),
            "expires_at": now.isoformat(),
            "payload_hash": SHA,
            "raw_payload_ref": "viking://resources/workflow/run-1/frontline/news_analyst/call-1/provider_raw/1.json",
            "raw_payload_size_bytes": 99,
            "normalized_ref": None,
            "raw_count": 1,
            "accepted_count": 1,
            "schema_validated_at": now.isoformat(),
        }
    )

    result = inspect_provider_cache(
        key,
        now_utc=now.isoformat(),
        collection=collection,
    )
    assert result.status == "cache_stale"
    assert result.cache_ref is not None


def test_t_mdb_002_inspect_provider_cache_returns_schema_invalid_when_raw_ref_missing() -> None:
    now = datetime.now(UTC)
    key = _cache_key()
    collection = _FakeCollection(
        find_one_doc={
            "_id": build_provider_cache_document_id(key),
            "market": "CN_A",
            "domain": "news",
            "ticker": "600519.SH",
            "provider": "akshare",
            "endpoint": "stock_news_em",
            "query_fingerprint": QUERY_FINGERPRINT,
            "schema_version": "cn_a_news_pack.v1",
            "fetched_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
            "payload_hash": SHA,
            "raw_payload_ref": "",
            "raw_payload_size_bytes": 100,
            "normalized_ref": None,
            "raw_count": 2,
            "accepted_count": 1,
            "schema_validated_at": now.isoformat(),
        }
    )

    result = inspect_provider_cache(
        key,
        now_utc=now.isoformat(),
        collection=collection,
    )
    assert result.status == "schema_invalid"


def test_t_mdb_002_inspect_provider_cache_sets_error_reason_with_cache_required() -> None:
    key = _cache_key()
    result = inspect_provider_cache(
        key,
        now_utc=datetime.now(UTC).isoformat(),
        collection=_FakeCollection(fail=True),
        cache_required=True,
    )

    assert result.status == "error"
    assert result.reason is not None
    assert MONGO_UNAVAILABLE in result.reason
    assert "cache_required=true" in result.reason


def test_t_mdb_002_upsert_provider_cache_returns_collection_ref() -> None:
    now = datetime.now(UTC).isoformat()
    key = _cache_key()
    cache_id = build_provider_cache_document_id(key)
    document = ProviderCacheDocument(
        _id=cache_id,
        key=key,
        fetched_at=now,
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/news_analyst/call-1/provider_raw/2.json",
        raw_payload_size_bytes=150,
        normalized_ref=None,
        raw_count=8,
        accepted_count=6,
        schema_validated_at=now,
    )
    collection = _FakeCollection()

    ref = upsert_provider_cache(document, collection=collection)
    assert ref == f"cn_a_provider_cache:{cache_id}"
    assert collection.last_update is not None
    assert collection.last_update["upsert"] is True


def test_t_mdb_002_upsert_provider_cache_rejects_non_viking_raw_ref() -> None:
    now = datetime.now(UTC).isoformat()
    key = _cache_key()
    document = ProviderCacheDocument(
        _id=build_provider_cache_document_id(key),
        key=key,
        fetched_at=now,
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        payload_hash=SHA,
        raw_payload_ref="s3://bucket/payload.json",
        raw_payload_size_bytes=150,
        normalized_ref=None,
        raw_count=8,
        accepted_count=6,
        schema_validated_at=now,
    )
    with pytest.raises(FrontlineValidationError) as error:
        upsert_provider_cache(document, collection=_FakeCollection())
    assert error.value.code == MONGO_SCHEMA_INVALID


def test_t_mdb_002_insert_provider_attempt_uses_derived_attempt_id() -> None:
    attempt = _provider_attempt(started_at="2026-05-08T13:00:00Z")
    collection = _FakeCollection()

    ref = insert_provider_attempt(
        attempt,
        run_id="run-123",
        call_id="call-456",
        ticker="600519.SH",
        domain="news",
        worker_id="news_analyst",
        collection=collection,
    )

    expected_attempt_id = build_provider_attempt_id(
        run_id="run-123",
        call_id="call-456",
        provider=attempt.provider,
        endpoint=attempt.endpoint,
        started_at=attempt.started_at,
    )
    assert ref == f"cn_a_provider_attempts:{expected_attempt_id}"
    assert collection.inserted_doc is not None
    assert collection.inserted_doc["_id"] == expected_attempt_id
    assert "approval" not in collection.inserted_doc


def _cache_key() -> ProviderCacheKey:
    return ProviderCacheKey(
        market="CN_A",
        domain="news",
        ticker="600519.SH",
        provider="akshare",
        endpoint="stock_news_em",
        query_fingerprint=QUERY_FINGERPRINT,
        schema_version="cn_a_news_pack.v1",
    )


def _provider_attempt(*, started_at: str) -> ProviderAttempt:
    return ProviderAttempt(
        provider="akshare",
        endpoint="stock_news_em",
        role="p0_company_news",
        status="success",
        started_at=started_at,
        finished_at="2026-05-08T13:00:03Z",
        elapsed_ms=3000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FINGERPRINT,
        raw_count=6,
        accepted_count=4,
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/news_analyst/call-1/provider_raw/3.json",
        error_code=None,
        error_message_redacted=None,
    )


SHA = "sha256:" + ("a" * 64)
QUERY_FINGERPRINT = "sha256:" + ("b" * 64)
