from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import MONGO_CONFIG_INVALID, FrontlineConfigError  # noqa: E402
from frontline_data_pack.mongo_store import (  # noqa: E402
    COLLECTION_CRYPTO_PROVIDER_ATTEMPTS,
    COLLECTION_CRYPTO_PROVIDER_CACHE,
    COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS,
    COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS,
    COLLECTION_NORMALIZED_MARKET_PRICES,
    COLLECTION_NORMALIZED_NEWS_ITEMS,
    COLLECTION_NORMALIZED_SOCIAL_SIGNALS,
    COLLECTION_PROVIDER_ATTEMPTS,
    COLLECTION_PROVIDER_CACHE,
    MONGO_CONNECT_TIMEOUT_MS,
    MONGO_MAX_POOL_SIZE,
    MONGO_PING_SLOW,
    MONGO_SERVER_SELECTION_TIMEOUT_MS,
    MONGO_UNAVAILABLE,
    build_collection_index_models,
    check_mongo_health,
    create_mongo_store,
    initialize_mongo_indexes,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.received_index_models = None

    def create_indexes(self, index_models):  # noqa: ANN001
        self.received_index_models = list(index_models)
        return [f"idx_{index}" for index, _ in enumerate(index_models)]


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        collection = self.collections.get(name)
        if collection is None:
            collection = _FakeCollection()
            self.collections[name] = collection
        return collection


class _FakeClient:
    def __init__(self) -> None:
        self.database = _FakeDatabase()
        self.admin = _FakeAdmin()

    def __getitem__(self, name: str) -> _FakeDatabase:  # noqa: ARG002
        return self.database


class _FakeAdmin:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    def command(self, name: str) -> dict[str, int]:
        if self.should_fail:
            raise RuntimeError("mongo unavailable")
        if name != "ping":
            raise AssertionError(f"unexpected command: {name}")
        return {"ok": 1}


def test_create_mongo_store_uses_uri_path_database_and_connection_options() -> None:
    captured: dict[str, object] = {}

    def _factory(uri: str, **kwargs: object) -> _FakeClient:
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return _FakeClient()

    store = create_mongo_store(
        "mongodb://localhost:27017/claw_trade",
        mongo_client_factory=_factory,
    )

    assert store.database_name == "claw_trade"
    assert captured["uri"] == "mongodb://localhost:27017/claw_trade"
    assert captured["kwargs"] == {
        "maxPoolSize": MONGO_MAX_POOL_SIZE,
        "connectTimeoutMS": MONGO_CONNECT_TIMEOUT_MS,
        "serverSelectionTimeoutMS": MONGO_SERVER_SELECTION_TIMEOUT_MS,
    }


def test_create_mongo_store_rejects_uri_without_database_path() -> None:
    with pytest.raises(FrontlineConfigError) as error:
        create_mongo_store("mongodb://localhost:27017")
    assert error.value.code == MONGO_CONFIG_INVALID


def test_initialize_mongo_indexes_builds_required_indexes_and_ttl() -> None:
    store = create_mongo_store(
        "mongodb://localhost:27017/claw_trade",
        mongo_client_factory=lambda _uri, **_kwargs: _FakeClient(),
    )
    created = initialize_mongo_indexes(store.database)

    expected_collections = {
        COLLECTION_CRYPTO_PROVIDER_ATTEMPTS,
        COLLECTION_CRYPTO_PROVIDER_CACHE,
        COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS,
        COLLECTION_PROVIDER_CACHE,
        COLLECTION_PROVIDER_ATTEMPTS,
        COLLECTION_NORMALIZED_MARKET_PRICES,
        COLLECTION_NORMALIZED_NEWS_ITEMS,
        COLLECTION_NORMALIZED_SOCIAL_SIGNALS,
        COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS,
    }
    assert set(created.keys()) == expected_collections

    provider_cache_docs = [model.document for model in store.database[COLLECTION_PROVIDER_CACHE].received_index_models]
    assert any(
        doc["key"]
        == {
            "market": 1,
            "domain": 1,
            "ticker": 1,
            "provider": 1,
            "endpoint": 1,
            "query_fingerprint": 1,
            "schema_version": 1,
        }
        and doc.get("unique") is True
        for doc in provider_cache_docs
    )
    assert any(doc["key"] == {"expires_at": 1} and doc.get("expireAfterSeconds") == 0 for doc in provider_cache_docs)

    crypto_provider_cache_docs = [
        model.document for model in store.database[COLLECTION_CRYPTO_PROVIDER_CACHE].received_index_models
    ]
    assert any(
        doc["key"]
        == {
            "market": 1,
            "domain": 1,
            "ticker": 1,
            "provider": 1,
            "endpoint": 1,
            "method": 1,
            "query_fingerprint": 1,
            "schema_version": 1,
            "source_role": 1,
        }
        and doc.get("unique") is True
        for doc in crypto_provider_cache_docs
    )
    assert any(
        doc["key"] == {"expires_at": 1} and doc.get("expireAfterSeconds") == 0
        for doc in crypto_provider_cache_docs
    )

    crypto_rate_limit_docs = [
        model.document for model in store.database[COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS].received_index_models
    ]
    assert any(
        doc["key"] == {"provider": 1, "endpoint": 1, "window_start": 1}
        and doc.get("unique") is True
        for doc in crypto_rate_limit_docs
    )
    assert any(
        doc["key"] == {"window_expires_at": 1} and doc.get("expireAfterSeconds") == 0
        for doc in crypto_rate_limit_docs
    )

    market_docs = [model.document for model in store.database[COLLECTION_NORMALIZED_MARKET_PRICES].received_index_models]
    assert any(
        doc["key"] == {"market": 1, "ticker": 1, "adjust": 1, "trade_date": 1}
        and doc.get("unique") is True
        for doc in market_docs
    )
    assert any(doc["key"] == {"expires_at": 1} and doc.get("expireAfterSeconds") == 0 for doc in market_docs)

    for collection_name in expected_collections:
        for model in store.database[collection_name].received_index_models:
            for key in model.document["key"]:
                assert "approval" not in key.lower()


def test_check_mongo_health_returns_unhealthy_when_ping_is_slow_or_fails() -> None:
    slow_client = _FakeClient()
    slow_status = check_mongo_health(
        slow_client,
        max_latency_ms=200,
        clock=_clock([0.0, 0.250]),
    )
    assert slow_status.ok is False
    assert slow_status.reason == MONGO_PING_SLOW
    assert slow_status.latency_ms == 250

    failed_client = _FakeClient()
    failed_client.admin = _FakeAdmin(should_fail=True)
    failed_status = check_mongo_health(failed_client, clock=_clock([0.0, 0.010]))
    assert failed_status.ok is False
    assert failed_status.reason == MONGO_UNAVAILABLE


def test_build_collection_index_models_contains_all_required_collections() -> None:
    index_models = build_collection_index_models()
    assert set(index_models.keys()) == {
        COLLECTION_CRYPTO_PROVIDER_ATTEMPTS,
        COLLECTION_CRYPTO_PROVIDER_CACHE,
        COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS,
        COLLECTION_PROVIDER_CACHE,
        COLLECTION_PROVIDER_ATTEMPTS,
        COLLECTION_NORMALIZED_MARKET_PRICES,
        COLLECTION_NORMALIZED_NEWS_ITEMS,
        COLLECTION_NORMALIZED_SOCIAL_SIGNALS,
        COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS,
    }


def _clock(values: list[float]):
    iterator = iter(values)

    def _next() -> float:
        return next(iterator)

    return _next
