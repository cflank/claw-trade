from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pymongo import MongoClient

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cache import (  # noqa: E402
    SOCIAL_CACHE_INSPECTION_BYPASSED,
    SOCIAL_CACHE_QUERY_FAILED,
    SOCIAL_CACHE_REQUIRED_MISSING,
    CacheInspectionCallState,
    CacheKey,
    ensure_social_provider_cache_collection,
    inspect_provider_cache,
)
from config import SOCIAL_SCHEMA_VERSION, SocialDataConfig  # noqa: E402


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_text(seed: str) -> str:
    return f"sha256:{seed * 64}"[:71]


def _build_cache_record(
    *,
    cache_id: str,
    fetched_at: str,
    ttl_seconds: int,
    raw_payload_ref: str,
) -> dict[str, object]:
    now = _iso_now()
    return {
        "cache_id": cache_id,
        "market": "CN_A",
        "ticker": "600519.SH",
        "provider": "akshare",
        "endpoint": "stock_hot_rank_latest_em",
        "query_fingerprint": "sha256:query-a",
        "date_window": "2026-05-01:2026-05-07",
        "as_of_date": "2026-05-07",
        "fetched_at": fetched_at,
        "schema_version": SOCIAL_SCHEMA_VERSION,
        "payload_hash": _hash_text("a"),
        "raw_payload_ref": raw_payload_ref,
        "row_count": 3,
        "fields": {
            "symbol": "100.600519",
            "rank": 1,
            "heat": 777.0,
            "as_of_date": "2026-05-07",
        },
        "ttl_seconds": ttl_seconds,
        "created_at": now,
        "updated_at": now,
    }


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


@pytest.fixture()
def mongo_client() -> MongoClient:
    mongo_uri = os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017")
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    yield client
    client.close()


@pytest.fixture()
def db_name() -> str:
    return f"test_soc_cache_insp_{uuid.uuid4().hex}"


def test_inspect_provider_cache_returns_hit_with_raw_ref_and_hash(
    mongo_client: MongoClient, db_name: str
) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)
    fresh_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    collection.insert_one(
        _build_cache_record(
            cache_id="cache-hit",
            fetched_at=fresh_time,
            ttl_seconds=1800,
            raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/cache-hit.json",
        )
    )

    result = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=_build_config(
            mongodb_uri=os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017"),
            cache_required=False,
            db_name=db_name,
        ),
    )

    assert result.status == "hit"
    assert result.payload_hash == _hash_text("a")
    assert result.raw_payload_ref == "viking://resources/workflow/run/frontline/social_analyst/cache-hit.json"

    mongo_client.drop_database(db_name)


def test_inspect_provider_cache_returns_stale_when_ttl_expired(
    mongo_client: MongoClient, db_name: str
) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)
    stale_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    collection.insert_one(
        _build_cache_record(
            cache_id="cache-stale",
            fetched_at=stale_time,
            ttl_seconds=1800,
            raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/cache-stale.json",
        )
    )

    result = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=_build_config(
            mongodb_uri=os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017"),
            cache_required=False,
            db_name=db_name,
        ),
    )

    assert result.status == "stale"
    assert result.payload_hash is None
    assert result.raw_payload_ref is None

    mongo_client.drop_database(db_name)


def test_inspect_provider_cache_returns_schema_invalid_for_non_viking_raw_ref(
    mongo_client: MongoClient, db_name: str
) -> None:
    collection = ensure_social_provider_cache_collection(mongo_client, db_name)
    fresh_time = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    collection.insert_one(
        _build_cache_record(
            cache_id="cache-invalid-ref",
            fetched_at=fresh_time,
            ttl_seconds=1800,
            raw_payload_ref="http://invalid-ref",
        )
    )

    result = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=_build_config(
            mongodb_uri=os.environ.get("CN_A_MONGODB_URI", "mongodb://127.0.0.1:27017"),
            cache_required=False,
            db_name=db_name,
        ),
    )

    assert result.status == "schema_invalid"
    assert result.payload_hash is None
    assert result.raw_payload_ref is None

    mongo_client.drop_database(db_name)


def test_inspect_provider_cache_returns_required_missing_when_uri_absent_and_cache_required() -> None:
    result = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=_build_config(mongodb_uri=None, cache_required=True, db_name="unused"),
    )

    assert result.status == "not_configured"
    assert result.reason == SOCIAL_CACHE_REQUIRED_MISSING


def test_inspect_provider_cache_bypasses_after_three_query_errors_in_same_call() -> None:
    call_state = CacheInspectionCallState()
    broken_config = _build_config(mongodb_uri="mongodb://:@", cache_required=False, db_name="unused")

    first = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=broken_config,
        call_state=call_state,
    )
    second = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=broken_config,
        call_state=call_state,
    )
    third = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=broken_config,
        call_state=call_state,
    )
    fourth = inspect_provider_cache(
        key=_build_key(),
        now_iso=_iso_now(),
        config=broken_config,
        call_state=call_state,
    )

    assert first.status == "not_configured"
    assert first.reason is not None and first.reason.startswith(f"{SOCIAL_CACHE_QUERY_FAILED}:")
    assert second.status == "not_configured"
    assert second.reason is not None and second.reason.startswith(f"{SOCIAL_CACHE_QUERY_FAILED}:")
    assert third.status == "not_configured"
    assert third.reason is not None and third.reason.startswith(f"{SOCIAL_CACHE_QUERY_FAILED}:")
    assert call_state.mongo_unavailable_for_call is True
    assert call_state.query_error_streak == 3

    assert fourth.status == "not_configured"
    assert fourth.reason == SOCIAL_CACHE_INSPECTION_BYPASSED
