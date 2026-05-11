from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pymongo import MongoClient

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cache import CacheKey, ensure_social_provider_cache_collection, upsert_provider_cache  # noqa: E402
from config import SOCIAL_SCHEMA_VERSION, SocialDataConfig  # noqa: E402


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _build_key() -> CacheKey:
    return CacheKey(
        market="CN_A",
        ticker="600519.SH",
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        query_fingerprint="sha256:query-a",
        date_window="2026-05-01:2026-05-07",
        schema_version=SOCIAL_SCHEMA_VERSION,
    )


def _build_config(*, mongodb_uri: str | None, cache_required: bool, db_name: str) -> SocialDataConfig:
    return SocialDataConfig(
        schema_version=SOCIAL_SCHEMA_VERSION,
        provider_timeout_seconds=10,
        pack_timeout_seconds=20,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri=mongodb_uri,
        mongodb_database=db_name,
        mongodb_cache_collection="social_provider_cache",
        cache_required=cache_required,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
        },
        p1_hot_up_enabled=True,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _build_raw_result() -> dict[str, Any]:
    return {
        "ok": True,
        "payload_hash": "sha256:" + ("a" * 64),
        "row_count": 3,
        "fields": {
            "symbol": "100.600519",
            "rank": 1,
            "heat": 888.5,
            "as_of_date": "2026-05-07",
        },
        "as_of_date": "2026-05-07",
        "fetched_at": _iso_now(),
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
    return f"test_soc_cache_upsert_{uuid.uuid4().hex}"


def test_upsert_provider_cache_persists_payload_hash_row_count_fields_and_ttl(
    mongo_client: MongoClient, db_name: str
) -> None:
    ensure_social_provider_cache_collection(mongo_client, db_name)
    key = _build_key()
    config = _build_config(
        mongodb_uri=os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017"),
        cache_required=False,
        db_name=db_name,
    )

    result = upsert_provider_cache(
        key=key,
        raw_result=_build_raw_result(),
        raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/raw-a.json",
        ttl_seconds=1800,
        now_iso=_iso_now(),
        config=config,
    )

    assert result.status == "miss"
    assert result.payload_hash == "sha256:" + ("a" * 64)
    assert result.raw_payload_ref == "viking://resources/workflow/run/frontline/social_analyst/raw-a.json"

    collection = mongo_client.get_database(db_name).get_collection("social_provider_cache")
    record = collection.find_one({"cache_id": key.cache_id()})
    assert record is not None
    assert record["payload_hash"] == "sha256:" + ("a" * 64)
    assert record["row_count"] == 3
    assert record["fields"]["symbol"] == "100.600519"
    assert record["ttl_seconds"] == 1800

    mongo_client.drop_database(db_name)


def test_upsert_provider_cache_returns_write_failed_when_raw_result_not_success(
    mongo_client: MongoClient, db_name: str
) -> None:
    ensure_social_provider_cache_collection(mongo_client, db_name)
    key = _build_key()
    config = _build_config(
        mongodb_uri=os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017"),
        cache_required=False,
        db_name=db_name,
    )

    result = upsert_provider_cache(
        key=key,
        raw_result={"ok": False, "payload_hash": "sha256:" + ("a" * 64), "row_count": 0, "fields": {}},
        raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/raw-failed.json",
        ttl_seconds=1800,
        now_iso=_iso_now(),
        config=config,
    )

    assert result.status == "write_failed"
    assert result.reason == "raw_result_not_success"

    collection = mongo_client.get_database(db_name).get_collection("social_provider_cache")
    assert collection.count_documents({"cache_id": key.cache_id()}) == 0

    mongo_client.drop_database(db_name)


def test_upsert_provider_cache_keeps_single_record_for_concurrent_same_key(
    mongo_client: MongoClient, db_name: str
) -> None:
    ensure_social_provider_cache_collection(mongo_client, db_name)
    key = _build_key()
    config = _build_config(
        mongodb_uri=os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017"),
        cache_required=False,
        db_name=db_name,
    )
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            result = upsert_provider_cache(
                key=key,
                raw_result=_build_raw_result(),
                raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/raw-race.json",
                ttl_seconds=1800,
                now_iso=_iso_now(),
                config=config,
            )
            with lock:
                results.append(result.status)
        except Exception as exc:  # pragma: no cover - 仅用于并发失败时留存错误
            with lock:
                errors.append(exc)

    first = threading.Thread(target=_worker)
    second = threading.Thread(target=_worker)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert errors == []
    assert sorted(results) == ["miss", "miss"]

    collection = mongo_client.get_database(db_name).get_collection("social_provider_cache")
    assert collection.count_documents({"cache_id": key.cache_id()}) == 1

    mongo_client.drop_database(db_name)
