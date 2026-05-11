from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, WriteError

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cache import (  # noqa: E402
    INDEX_CACHE_ID_UNIQUE,
    INDEX_PAYLOAD_HASH,
    INDEX_PRIMARY_QUERY_UNIQUE,
    INDEX_UPDATED_AT,
    SOCIAL_PROVIDER_CACHE_COLLECTION,
    ensure_social_provider_cache_collection,
)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _build_cache_record(
    *,
    cache_id: str,
    market: str = "CN_A",
    ticker: str = "600519.SH",
    provider: str = "akshare",
    endpoint: str = "stock_hot_rank_latest_em",
    query_fingerprint: str = "sha256:query-a",
    date_window: str = "2026-05-07",
    schema_version: str = "cn_a_social_pack.v1",
) -> dict[str, object]:
    now = _iso_now()
    return {
        "cache_id": cache_id,
        "market": market,
        "ticker": ticker,
        "provider": provider,
        "endpoint": endpoint,
        "query_fingerprint": query_fingerprint,
        "date_window": date_window,
        "as_of_date": "2026-05-07",
        "fetched_at": now,
        "schema_version": schema_version,
        "payload_hash": f"sha256:payload-{cache_id}",
        "raw_payload_ref": f"viking://resources/workflow/run/frontline/social_analyst/{cache_id}.json",
        "row_count": 3,
        "fields": {"rank": 1, "heat": 876.5},
        "ttl_seconds": 1800,
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture()
def mongo_client() -> MongoClient:
    mongo_uri = os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017")
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    yield client
    client.close()


@pytest.fixture()
def db_name() -> str:
    return f"test_social_provider_cache_{uuid.uuid4().hex}"


def test_init_creates_social_provider_cache_collection(mongo_client: MongoClient, db_name: str) -> None:
    ensure_social_provider_cache_collection(mongo_client, db_name)

    collection_names = mongo_client.get_database(db_name).list_collection_names()
    assert SOCIAL_PROVIDER_CACHE_COLLECTION in collection_names

    mongo_client.drop_database(db_name)


def test_validator_rejects_record_missing_required_field(mongo_client: MongoClient, db_name: str) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)
    invalid = _build_cache_record(cache_id="cache-missing-raw-ref")
    invalid.pop("raw_payload_ref")

    with pytest.raises(WriteError):
        collection.insert_one(invalid)

    mongo_client.drop_database(db_name)


def test_unique_cache_id_index_rejects_duplicate_cache_id(mongo_client: MongoClient, db_name: str) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)
    first = _build_cache_record(cache_id="cache-dup-id")
    second = _build_cache_record(cache_id="cache-dup-id")
    second["payload_hash"] = "sha256:payload-second"
    second["query_fingerprint"] = "sha256:query-b"

    collection.insert_one(first)
    with pytest.raises(DuplicateKeyError):
        collection.insert_one(second)

    mongo_client.drop_database(db_name)


def test_primary_query_unique_index_rejects_duplicate_query_key(
    mongo_client: MongoClient, db_name: str
) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)
    first = _build_cache_record(cache_id="cache-query-a")
    second = _build_cache_record(cache_id="cache-query-b")
    second["payload_hash"] = "sha256:payload-query-b"
    second["raw_payload_ref"] = "viking://resources/workflow/run/frontline/social_analyst/cache-query-b.json"

    collection.insert_one(first)
    with pytest.raises(DuplicateKeyError):
        collection.insert_one(second)

    mongo_client.drop_database(db_name)


def test_updated_at_and_payload_hash_indexes_match_dld_fields(
    mongo_client: MongoClient, db_name: str
) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)

    index_information = collection.index_information()

    assert index_information[INDEX_CACHE_ID_UNIQUE]["key"] == [("cache_id", 1)]
    assert index_information[INDEX_CACHE_ID_UNIQUE]["unique"] is True

    assert index_information[INDEX_PRIMARY_QUERY_UNIQUE]["key"] == [
        ("market", 1),
        ("ticker", 1),
        ("provider", 1),
        ("endpoint", 1),
        ("query_fingerprint", 1),
        ("date_window", 1),
        ("schema_version", 1),
    ]
    assert index_information[INDEX_PRIMARY_QUERY_UNIQUE]["unique"] is True

    assert index_information[INDEX_UPDATED_AT]["key"] == [("updated_at", 1)]
    assert "expireAfterSeconds" not in index_information[INDEX_UPDATED_AT]
    assert index_information[INDEX_PAYLOAD_HASH]["key"] == [("payload_hash", 1)]

    mongo_client.drop_database(db_name)
