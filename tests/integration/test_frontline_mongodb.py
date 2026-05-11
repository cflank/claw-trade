from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.cache import (  # noqa: E402
    build_provider_cache_document_id,
    inspect_provider_cache,
    upsert_provider_cache,
)
from frontline_data_pack.models import ProviderCacheDocument, ProviderCacheKey  # noqa: E402
from frontline_data_pack.mongo_store import (  # noqa: E402
    COLLECTION_PROVIDER_CACHE,
    build_collection_index_models,
    check_mongo_health,
    create_mongo_store,
    initialize_mongo_indexes,
)


MONGO_URI = "mongodb://127.0.0.1:27017/claw_trade_ttest004"
NOW_UTC = "2026-05-09T10:00:00+00:00"


@pytest.fixture()
def mongo_db():
    store = create_mongo_store(MONGO_URI)
    health = check_mongo_health(store.client)
    if not health.ok:
        pytest.skip(f"local mongodb unavailable: reason={health.reason}, latency_ms={health.latency_ms}")
    db = store.database
    try:
        for collection_name in (
            COLLECTION_PROVIDER_CACHE,
            "cn_a_provider_attempts",
            "cn_a_normalized_market_prices",
            "cn_a_normalized_news_items",
            "cn_a_normalized_social_signals",
            "cn_a_normalized_fundamental_fields",
        ):
            db[collection_name].delete_many({})
        yield db
    finally:
        store.client.close()


def _cache_key() -> ProviderCacheKey:
    return ProviderCacheKey(
        market="CN_A",
        domain="market",
        ticker="600519.SH",
        provider="akshare",
        endpoint="stock_zh_a_hist",
        query_fingerprint="sha256:" + ("1" * 64),
        schema_version="cn_a_market_pack.v1",
    )


def _cache_doc(*, payload_hash_seed: str, raw_payload_ref: str, expires_at: str) -> ProviderCacheDocument:
    key = _cache_key()
    return ProviderCacheDocument(
        _id=build_provider_cache_document_id(key),
        key=key,
        fetched_at=NOW_UTC,
        expires_at=expires_at,
        payload_hash="sha256:" + hashlib.sha256(payload_hash_seed.encode("utf-8")).hexdigest(),
        raw_payload_ref=raw_payload_ref,
        raw_payload_size_bytes=128,
        normalized_ref=None,
        raw_count=1,
        accepted_count=1,
        schema_validated_at=NOW_UTC,
    )


def test_t_test_004_mongodb_index_init_and_ttl(mongo_db) -> None:
    created = initialize_mongo_indexes(mongo_db)
    assert COLLECTION_PROVIDER_CACHE in created

    expected = build_collection_index_models()
    expected_signatures = {
        tuple(index_model.document["key"].items()) for index_model in expected[COLLECTION_PROVIDER_CACHE]
    }
    index_info = mongo_db[COLLECTION_PROVIDER_CACHE].index_information()
    existing_signatures = {tuple(item["key"]) for item in index_info.values() if "key" in item}
    assert expected_signatures.issubset(existing_signatures)

    ttl_indexes = [
        item
        for item in index_info.values()
        if item.get("key") == [("expires_at", 1)] and item.get("expireAfterSeconds") == 0
    ]
    assert ttl_indexes, "provider_cache 缺少 expires_at TTL index(expireAfterSeconds=0)"


def test_t_test_004_mongodb_cache_inspect_upsert_and_stale(mongo_db) -> None:
    initialize_mongo_indexes(mongo_db)
    collection = mongo_db[COLLECTION_PROVIDER_CACHE]
    key = _cache_key()

    miss = inspect_provider_cache(key, now_utc=NOW_UTC, collection=collection, cache_required=False)
    assert miss.status == "cache_miss"

    future_expire = (datetime(2026, 5, 9, 10, 0, 0, tzinfo=UTC) + timedelta(hours=1)).isoformat()
    document = _cache_doc(
        payload_hash_seed="first",
        raw_payload_ref=(
            "viking://resources/workflow/it-run/frontline/market_analyst/it-call/evidence/"
            "provider_raw/akshare/stock_zh_a_hist/1.json"
        ),
        expires_at=future_expire,
    )
    upsert_ref = upsert_provider_cache(document, collection=collection)
    assert upsert_ref.startswith(f"{COLLECTION_PROVIDER_CACHE}:")

    hit = inspect_provider_cache(key, now_utc=NOW_UTC, collection=collection, cache_required=False)
    assert hit.status == "cache_hit"
    assert hit.cache_ref is not None

    past_expire = (datetime(2026, 5, 9, 10, 0, 0, tzinfo=UTC) - timedelta(minutes=1)).isoformat()
    collection.update_one({"_id": document._id}, {"$set": {"expires_at": past_expire}})
    stale = inspect_provider_cache(key, now_utc=NOW_UTC, collection=collection, cache_required=False)
    assert stale.status == "cache_stale"


def test_t_test_004_mongodb_concurrent_cache_upsert_no_duplicate_current_cache(mongo_db) -> None:
    initialize_mongo_indexes(mongo_db)
    collection = mongo_db[COLLECTION_PROVIDER_CACHE]
    key = _cache_key()

    query = {
        "market": key.market,
        "domain": key.domain,
        "ticker": key.ticker,
        "provider": key.provider,
        "endpoint": key.endpoint,
        "query_fingerprint": key.query_fingerprint,
        "schema_version": key.schema_version,
    }
    collection.delete_many(query)

    expires_at = (datetime(2026, 5, 9, 10, 0, 0, tzinfo=UTC) + timedelta(hours=1)).isoformat()
    doc_a = _cache_doc(
        payload_hash_seed="A",
        raw_payload_ref=(
            "viking://resources/workflow/it-run/frontline/market_analyst/call-A/evidence/"
            "provider_raw/akshare/stock_zh_a_hist/1.json"
        ),
        expires_at=expires_at,
    )
    doc_b = _cache_doc(
        payload_hash_seed="B",
        raw_payload_ref=(
            "viking://resources/workflow/it-run/frontline/market_analyst/call-B/evidence/"
            "provider_raw/akshare/stock_zh_a_hist/1.json"
        ),
        expires_at=expires_at,
    )

    def _do_upsert(document: ProviderCacheDocument) -> str:
        return upsert_provider_cache(document, collection=collection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        refs = list(executor.map(_do_upsert, (doc_a, doc_b)))

    assert len(refs) == 2
    assert collection.count_documents(query) == 1
    persisted = collection.find_one(query)
    assert persisted is not None
    assert persisted["payload_hash"] in {doc_a.payload_hash, doc_b.payload_hash}
