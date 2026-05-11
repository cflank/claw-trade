from __future__ import annotations

from typing import Any

from pymongo.collection import Collection
from pymongo.database import Database

FUNDAMENTAL_CACHE_COLLECTION_NAME = "cn_a_fundamental_cache"
FUNDAMENTAL_CACHE_SCHEMA_VERSION = "cn_a_fundamental_pack.v1"
FUNDAMENTAL_CACHE_MARKET = "CN_A"
FUNDAMENTAL_CACHE_PROVIDERS = ("tushare", "akshare")

INDEX_TICKER_API_PERIOD = "idx_ticker_api_period"
INDEX_PROVIDER_API_PAYLOAD_UNIQUE = "uk_provider_api_payload"
INDEX_PAYLOAD_HASH = "idx_payload_hash"
INDEX_TTL_EXPIRE_AT = "ttl_expire_at"


def build_fundamental_cache_json_schema() -> dict[str, Any]:
    return {
        "bsonType": "object",
        "required": [
            "ticker",
            "market",
            "provider",
            "api_name",
            "fetched_at",
            "schema_version",
            "payload_hash",
            "raw_payload_ref",
            "metric_definition_version",
            "fields",
            "created_at",
            "updated_at",
        ],
        "properties": {
            "ticker": {"bsonType": "string", "pattern": r"^[0-9]{6}\.(SH|SZ)$"},
            "market": {"enum": [FUNDAMENTAL_CACHE_MARKET]},
            "provider": {"enum": list(FUNDAMENTAL_CACHE_PROVIDERS)},
            "api_name": {"bsonType": "string"},
            "report_period": {"bsonType": ["string", "null"]},
            "announce_date": {"bsonType": ["string", "null"]},
            "as_of": {"bsonType": ["string", "null"]},
            "fetched_at": {"bsonType": "date"},
            "schema_version": {"enum": [FUNDAMENTAL_CACHE_SCHEMA_VERSION]},
            "payload_hash": {"bsonType": "string", "pattern": r"^sha256:[a-f0-9]{64}$"},
            "raw_payload_ref": {"bsonType": "string"},
            "metric_definition_version": {"bsonType": "string"},
            "expires_at": {"bsonType": ["date", "null"]},
            "fields": {
                "bsonType": "object",
                "additionalProperties": {
                    "bsonType": "object",
                    "required": ["value", "unit", "scale", "field_path"],
                    "properties": {
                        "field_path": {"bsonType": "string"},
                        "value": {},
                        "unit": {"bsonType": ["string", "null"]},
                        "scale": {"bsonType": ["string", "null"]},
                    },
                },
            },
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }


def build_fundamental_cache_validator() -> dict[str, Any]:
    return {"$jsonSchema": build_fundamental_cache_json_schema()}


def ensure_fundamental_cache_collection(db: Database[Any]) -> Collection[Any]:
    validator = build_fundamental_cache_validator()
    collection_name = FUNDAMENTAL_CACHE_COLLECTION_NAME
    names = db.list_collection_names(filter={"name": collection_name})
    if collection_name not in names:
        db.create_collection(
            collection_name,
            validator=validator,
            validationLevel="strict",
            validationAction="error",
        )
    else:
        db.command(
            {
                "collMod": collection_name,
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
    return db.get_collection(collection_name)


def ensure_fundamental_cache_indexes(collection: Collection[Any]) -> tuple[str, str, str, str]:
    index_1 = collection.create_index(
        [("ticker", 1), ("market", 1), ("api_name", 1), ("report_period", -1), ("as_of", -1)],
        name=INDEX_TICKER_API_PERIOD,
    )
    index_2 = collection.create_index(
        [
            ("provider", 1),
            ("api_name", 1),
            ("ticker", 1),
            ("report_period", 1),
            ("announce_date", 1),
            ("as_of", 1),
            ("payload_hash", 1),
        ],
        name=INDEX_PROVIDER_API_PAYLOAD_UNIQUE,
        unique=True,
    )
    index_3 = collection.create_index([("payload_hash", 1)], name=INDEX_PAYLOAD_HASH)
    index_4 = collection.create_index(
        [("expires_at", 1)],
        name=INDEX_TTL_EXPIRE_AT,
        expireAfterSeconds=0,
    )
    return (index_1, index_2, index_3, index_4)
