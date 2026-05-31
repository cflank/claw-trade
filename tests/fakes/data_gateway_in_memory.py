from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from claw_trade.data_gateway.packs.service import _GateControlledProviderExecutor
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore
from claw_trade.data_gateway.store.single_flight import MongoSingleFlightCoordinator


class InMemoryCollection:
    name = "test_collection"

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        key = query["_id"]
        current = self.docs.get(key)
        if current is None:
            if not upsert and "$setOnInsert" not in update:
                return
            current = {}
        current.update(update.get("$setOnInsert", {}))
        for field, delta in update.get("$inc", {}).items():
            current[field] = int(current.get(field, 0)) + int(delta)
        current.update(update.get("$set", {}))
        if "_id" not in current:
            current["_id"] = key
        self.docs[key] = current

    def insert_one(self, doc: dict[str, Any]) -> None:
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("duplicate")
        self.docs[doc["_id"]] = dict(doc)

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        key = query["_id"]
        if key not in self.docs and not upsert:
            return
        self.docs[key] = dict(doc)

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        key = query.get("_id")
        if not isinstance(key, str):
            return None
        doc = self.docs.get(key)
        if doc is None or not _matches(doc, query):
            return None
        return dict(doc)

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: Any | None = None,
    ) -> dict[str, Any] | None:
        del return_document
        key = query["_id"]
        current = self.find_one(query)
        if current is None and not upsert:
            return None
        self.update_one(query if current is not None else {"_id": key}, update, upsert=upsert)
        return self.find_one({"_id": key})


def build_gate_controlled_executor() -> _GateControlledProviderExecutor:
    attempt_store = MongoAttemptStore(InMemoryCollection())
    return _GateControlledProviderExecutor(
        helper=ProviderExecutionEvidenceHelper(
            raw_store=MongoRawPayloadStore(InMemoryCollection()),
            normalized_store=MongoNormalizedStore(InMemoryCollection()),
            attempt_store=attempt_store,
            http_evidence_store=MongoProviderHttpEvidenceStore(InMemoryCollection()),
        ),
        cache_store=MongoCacheStore(InMemoryCollection()),
        rate_limit_store=MongoRateLimitStore(InMemoryCollection()),
        single_flight=MongoSingleFlightCoordinator(
            collection=InMemoryCollection(),
            attempt_store=attempt_store,
            wait_timeout_seconds=0.1,
            poll_interval_seconds=0.001,
        ),
        attempt_store=attempt_store,
    )


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for field, expected in query.items():
        if field == "_id":
            continue
        if field == "$or" and isinstance(expected, list):
            if not any(_matches(doc, item) for item in expected if isinstance(item, dict)):
                return False
            continue
        value = doc.get(field)
        if isinstance(expected, dict):
            if "$lt" in expected and not (value is not None and value < expected["$lt"]):
                return False
            if "$lte" in expected and not (value is not None and value <= expected["$lte"]):
                return False
            if "$exists" in expected and ((field in doc) != bool(expected["$exists"])):
                return False
            continue
        if value != expected:
            return False
    return True
