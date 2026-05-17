from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.operations import IndexModel

from .errors import MONGO_CONFIG_INVALID, FrontlineConfigError
from .observability import record_mongo_latency, record_span


MONGO_MAX_POOL_SIZE = 5
MONGO_CONNECT_TIMEOUT_MS = 2000
MONGO_SERVER_SELECTION_TIMEOUT_MS = 2000
MONGO_HEALTH_MAX_LATENCY_MS = 200

COLLECTION_PROVIDER_CACHE = "cn_a_provider_cache"
COLLECTION_PROVIDER_ATTEMPTS = "cn_a_provider_attempts"
COLLECTION_CRYPTO_PROVIDER_CACHE = "crypto_provider_cache"
COLLECTION_CRYPTO_PROVIDER_ATTEMPTS = "crypto_provider_attempts"
COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS = "crypto_provider_rate_limits"
COLLECTION_NORMALIZED_MARKET_PRICES = "cn_a_normalized_market_prices"
COLLECTION_NORMALIZED_NEWS_ITEMS = "cn_a_normalized_news_items"
COLLECTION_NORMALIZED_SOCIAL_SIGNALS = "cn_a_normalized_social_signals"
COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS = "cn_a_normalized_fundamental_fields"

MONGO_UNAVAILABLE = "MONGO_UNAVAILABLE"
MONGO_PING_SLOW = "MONGO_PING_SLOW"


@dataclass(frozen=True)
class MongoStore:
    client: Any
    database: Any
    database_name: str


@dataclass(frozen=True)
class MongoHealthStatus:
    ok: bool
    latency_ms: int | None
    reason: str | None


@dataclass(frozen=True)
class MongoIndexHealthStatus:
    ok: bool
    missing: dict[str, list[tuple[tuple[str, int], ...]]]


def create_mongo_store(
    mongodb_uri: str,
    *,
    mongo_client_factory: Callable[..., Any] = MongoClient,
) -> MongoStore:
    database_name = parse_mongodb_database_name(mongodb_uri)
    client = mongo_client_factory(
        mongodb_uri,
        maxPoolSize=MONGO_MAX_POOL_SIZE,
        connectTimeoutMS=MONGO_CONNECT_TIMEOUT_MS,
        serverSelectionTimeoutMS=MONGO_SERVER_SELECTION_TIMEOUT_MS,
    )
    database = client[database_name]
    return MongoStore(client=client, database=database, database_name=database_name)


def resolve_optional_mongo_database(
    mongodb_uri: str,
    *,
    cache_required: bool,
) -> Any | None:
    store = create_mongo_store(mongodb_uri)
    health = check_mongo_health(store.client)
    if health.ok:
        return store.database
    if cache_required:
        raise FrontlineConfigError(MONGO_CONFIG_INVALID, f"{MONGO_UNAVAILABLE}:MongoDB health check failed")
    return None


def parse_mongodb_database_name(mongodb_uri: str) -> str:
    if not isinstance(mongodb_uri, str) or not mongodb_uri.strip():
        raise FrontlineConfigError(MONGO_CONFIG_INVALID, "CN_A_MONGODB_URI 缺失")
    text = mongodb_uri.strip()
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise FrontlineConfigError(MONGO_CONFIG_INVALID, "CN_A_MONGODB_URI 非法") from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise FrontlineConfigError(
            MONGO_CONFIG_INVALID,
            "CN_A_MONGODB_URI 必须以 mongodb:// 或 mongodb+srv:// 开头",
        )
    database_name = parsed.path.strip("/")
    if not database_name:
        raise FrontlineConfigError(
            MONGO_CONFIG_INVALID,
            "CN_A_MONGODB_URI 必须在 path 中提供 database 名称",
        )
    if "/" in database_name:
        raise FrontlineConfigError(MONGO_CONFIG_INVALID, "CN_A_MONGODB_URI database path 非法")
    return database_name


def build_collection_index_models() -> Mapping[str, tuple[IndexModel, ...]]:
    return {
        COLLECTION_PROVIDER_CACHE: (
            IndexModel(
                [
                    ("market", 1),
                    ("domain", 1),
                    ("ticker", 1),
                    ("provider", 1),
                    ("endpoint", 1),
                    ("query_fingerprint", 1),
                    ("schema_version", 1),
                ],
                unique=True,
            ),
            IndexModel([("market", 1), ("domain", 1), ("ticker", 1), ("fetched_at", -1)]),
            IndexModel([("expires_at", 1)], expireAfterSeconds=0),
            IndexModel([("payload_hash", 1)]),
        ),
        COLLECTION_PROVIDER_ATTEMPTS: (
            IndexModel([("run_id", 1), ("call_id", 1), ("provider", 1), ("endpoint", 1)]),
            IndexModel([("market", 1), ("domain", 1), ("ticker", 1), ("started_at", -1)]),
            IndexModel([("status", 1), ("started_at", -1)]),
        ),
        COLLECTION_CRYPTO_PROVIDER_CACHE: (
            IndexModel(
                [
                    ("market", 1),
                    ("domain", 1),
                    ("ticker", 1),
                    ("provider", 1),
                    ("endpoint", 1),
                    ("method", 1),
                    ("query_fingerprint", 1),
                    ("schema_version", 1),
                    ("source_role", 1),
                ],
                unique=True,
            ),
            IndexModel([("market", 1), ("domain", 1), ("ticker", 1), ("fetched_at", -1)]),
            IndexModel([("expires_at", 1)], expireAfterSeconds=0),
            IndexModel([("payload_hash", 1)]),
        ),
        COLLECTION_CRYPTO_PROVIDER_ATTEMPTS: (
            IndexModel([("run_id", 1), ("call_id", 1), ("provider", 1), ("endpoint", 1)]),
            IndexModel([("market", 1), ("domain", 1), ("ticker", 1), ("started_at", -1)]),
            IndexModel([("status", 1), ("started_at", -1)]),
        ),
        COLLECTION_CRYPTO_PROVIDER_RATE_LIMITS: (
            IndexModel([("provider", 1), ("endpoint", 1), ("window_start", 1)], unique=True),
            IndexModel([("window_expires_at", 1)], expireAfterSeconds=0),
        ),
        COLLECTION_NORMALIZED_MARKET_PRICES: (
            IndexModel([("market", 1), ("ticker", 1), ("adjust", 1), ("trade_date", 1)], unique=True),
            IndexModel([("expires_at", 1)], expireAfterSeconds=0),
        ),
        COLLECTION_NORMALIZED_NEWS_ITEMS: (
            IndexModel([("market", 1), ("ticker", 1), ("publish_time", -1)]),
            IndexModel([("expires_at", 1)], expireAfterSeconds=0),
        ),
        COLLECTION_NORMALIZED_SOCIAL_SIGNALS: (
            IndexModel([("market", 1), ("ticker", 1), ("signal_type", 1), ("signal_time", -1)]),
            IndexModel([("expires_at", 1)], expireAfterSeconds=0),
        ),
        COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS: (
            IndexModel([("market", 1), ("ticker", 1), ("field_name", 1), ("report_period", -1)]),
            IndexModel([("expires_at", 1)], expireAfterSeconds=0),
        ),
    }


def initialize_mongo_indexes(database: Any) -> dict[str, list[str]]:
    created: dict[str, list[str]] = {}
    for collection_name, index_models in build_collection_index_models().items():
        collection = database[collection_name]
        created[collection_name] = list(collection.create_indexes(list(index_models)))
    return created


def check_mongo_health(
    client: Any,
    *,
    max_latency_ms: int = MONGO_HEALTH_MAX_LATENCY_MS,
    clock: Callable[[], float] = perf_counter,
) -> MongoHealthStatus:
    started = clock()
    try:
        client.admin.command("ping")
    except PyMongoError:
        elapsed_ms = int((clock() - started) * 1000)
        record_mongo_latency(operation="ping", elapsed_ms=elapsed_ms)
        record_span(
            "mongo.inspect",
            status="error",
            elapsed_ms=elapsed_ms,
            fields={"domain": "mongo", "status": "error", "elapsed_ms": elapsed_ms, "operation": "ping"},
        )
        return MongoHealthStatus(ok=False, latency_ms=elapsed_ms, reason=MONGO_UNAVAILABLE)
    except Exception:
        elapsed_ms = int((clock() - started) * 1000)
        record_mongo_latency(operation="ping", elapsed_ms=elapsed_ms)
        record_span(
            "mongo.inspect",
            status="error",
            elapsed_ms=elapsed_ms,
            fields={"domain": "mongo", "status": "error", "elapsed_ms": elapsed_ms, "operation": "ping"},
        )
        return MongoHealthStatus(ok=False, latency_ms=elapsed_ms, reason=MONGO_UNAVAILABLE)

    elapsed_ms = int((clock() - started) * 1000)
    record_mongo_latency(operation="ping", elapsed_ms=elapsed_ms)
    record_span(
        "mongo.inspect",
        status="success",
        elapsed_ms=elapsed_ms,
        fields={"domain": "mongo", "status": "success", "elapsed_ms": elapsed_ms, "operation": "ping"},
    )
    if elapsed_ms > max_latency_ms:
        return MongoHealthStatus(ok=False, latency_ms=elapsed_ms, reason=MONGO_PING_SLOW)
    return MongoHealthStatus(ok=True, latency_ms=elapsed_ms, reason=None)


def check_mongo_index_health(database: Any) -> MongoIndexHealthStatus:
    expected = build_collection_index_models()
    missing: dict[str, list[tuple[tuple[str, int], ...]]] = {}

    for collection_name, index_models in expected.items():
        collection = database[collection_name]
        try:
            index_info = collection.index_information()
        except Exception:
            missing[collection_name] = [_index_signature(model.document.get("key", {})) for model in index_models]
            continue

        existing_signatures = {
            _index_signature(entry.get("key", {}))
            for entry in index_info.values()
            if isinstance(entry, Mapping)
        }

        for model in index_models:
            signature = _index_signature(model.document.get("key", {}))
            if signature not in existing_signatures:
                missing.setdefault(collection_name, []).append(signature)

    return MongoIndexHealthStatus(ok=not missing, missing=missing)


def _index_signature(raw: Any) -> tuple[tuple[str, int], ...]:
    if isinstance(raw, dict):
        return tuple((str(key), int(value)) for key, value in raw.items())
    if isinstance(raw, list):
        pairs: list[tuple[str, int]] = []
        for item in raw:
            if isinstance(item, tuple) and len(item) == 2:
                pairs.append((str(item[0]), int(item[1])))
        return tuple(pairs)
    return ()
