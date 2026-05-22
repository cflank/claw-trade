from __future__ import annotations

from typing import Any

import pytest

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderCallSpec,
    ProviderKind,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore, build_cache_key
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.raise_find: Exception | None = None
        self.raise_write: Exception | None = None

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        if self.raise_find is not None:
            raise self.raise_find
        key = query["_id"]
        doc = self.docs.get(key)
        if doc is None:
            return None
        return dict(doc) if _matches_query(doc, query) else None

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        del upsert
        if self.raise_write is not None:
            raise self.raise_write
        self.docs[query["_id"]] = dict(doc)

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        if self.raise_write is not None:
            raise self.raise_write
        key = query["_id"]
        current = self.docs.get(key)
        inserted = False
        if current is None:
            if not upsert:
                return
            current = {}
            inserted = True
        elif not _matches_query(current, query):
            return
        if inserted:
            current.update(update.get("$setOnInsert", {}))
        if "$inc" in update:
            for field, delta in update["$inc"].items():
                current[field] = int(current.get(field, 0)) + int(delta)
        current.update(update.get("$set", {}))
        if "_id" not in current:
            current["_id"] = key
        self.docs[key] = current

    def insert_one(self, doc: dict[str, Any]) -> None:
        if self.raise_write is not None:
            raise self.raise_write
        key = doc["_id"]
        if key in self.docs:
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("duplicate")
        self.docs[key] = dict(doc)

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: Any | None = None,
    ) -> dict[str, Any] | None:
        del return_document
        if self.raise_write is not None:
            raise self.raise_write
        key = query["_id"]
        current = self.docs.get(key)
        if current is not None and _matches_query(current, query):
            self.update_one(query, update, upsert=False)
            return dict(self.docs[key])
        if not upsert:
            return None
        if current is not None:
            return None
        self.update_one(query, update, upsert=True)
        created = self.docs.get(key)
        return dict(created) if created is not None else None


def _matches_query(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "_id":
            if doc.get("_id") != value:
                return False
            continue
        if key == "$or":
            options = value if isinstance(value, list) else []
            return any(_matches_query(doc, option) for option in options)
        if isinstance(value, dict) and "$lt" in value:
            current = doc.get(key)
            if current is None or not current < value["$lt"]:
                return False
            continue
        if isinstance(value, dict) and "$exists" in value:
            exists = key in doc
            if exists is not bool(value["$exists"]):
                return False
            continue
        if doc.get(key) != value:
            return False
    return True


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-cache",
        call_id="call-cache",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="000001.SZ",
        company_name="平安银行",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key="market:tushare:daily",
        provider="tushare",
        adapter_id="project.tushare",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={"ticker": "000001.SZ"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="market.daily.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def test_cache_store_state_machine_keeps_miss_stale_error_and_cached_empty() -> None:
    collection = _FakeCollection()
    store = MongoCacheStore(collection)
    spec = _spec()
    request = _request()

    miss = store.get(spec, request)
    assert miss.status == ProviderStatus.CACHE_MISS
    assert miss.receipt.status == ProviderStatus.CACHE_MISS

    empty = store.put_empty(spec=spec, request=request, ttl_seconds=300, evidence_hash="sha256:empty")
    assert empty.status == ProviderStatus.CACHED_EMPTY

    got_empty = store.get(spec, request)
    assert got_empty.status == ProviderStatus.CACHED_EMPTY

    key, _ = build_cache_key(spec, request)
    collection.docs[key]["expires_at"] = "2000-01-01T00:00:00+00:00"
    stale_empty = store.get(spec, request)
    assert stale_empty.status == ProviderStatus.CACHE_STALE
    assert stale_empty.receipt.status == ProviderStatus.CACHE_STALE
    assert stale_empty.receipt.cached_empty is True

    written = store.put_success(
        spec=spec,
        request=request,
        raw_ref="mongo://openbb_raw_payloads/raw-1",
        normalized_ref="mongo://openbb_normalized/norm-1",
        evidence_hash="sha256:ok",
        ttl_seconds=300,
    )
    assert written.status == ProviderStatus.CACHE_HIT
    hit = store.get(spec, request)
    assert hit.status == ProviderStatus.CACHE_HIT
    assert hit.usable_raw_ref == "mongo://openbb_raw_payloads/raw-1"
    assert hit.usable_normalized_ref == "mongo://openbb_normalized/norm-1"

    collection.docs[key]["expires_at"] = "2000-01-01T00:00:00+00:00"
    stale = store.get(spec, request)
    assert stale.status == ProviderStatus.CACHE_STALE

    collection.raise_find = RuntimeError("mongo unavailable")
    error = store.get(spec, request)
    assert error.status == ProviderStatus.CACHE_ERROR
    assert error.receipt.status == ProviderStatus.CACHE_ERROR


def test_evidence_write_failure_maps_to_evidence_write_failed_and_blocks_success_refs() -> None:
    spec = _spec()
    request = _request()

    raw_collection = _FakeCollection()
    raw_collection.raise_write = RuntimeError("raw write failed")
    raw_store = MongoRawPayloadStore(raw_collection)
    with pytest.raises(DataGatewayError) as raw_exc:
        raw_store.write_raw(
            request=request,
            spec=spec,
            payload={"k": "v"},
            content_type="application/json",
            source_url="https://example.com",
        )
    assert raw_exc.value.code == DataGatewayErrorCode.EVIDENCE_WRITE_FAILED

    normalized_collection = _FakeCollection()
    normalized_collection.raise_write = RuntimeError("normalized write failed")
    normalized_store = MongoNormalizedStore(normalized_collection)
    with pytest.raises(DataGatewayError) as normalized_exc:
        from claw_trade.data_gateway.models import NormalizedResult

        normalized_store.write(
            request=request,
            spec=spec,
            normalized=NormalizedResult(
                status=ProviderStatus.REMOTE_SUCCESS,
                schema_id="market.daily.v1",
                rows=({"close": 10.2},),
                compact_facts={"close": 10.2},
                row_count=1,
                field_units={"close": "CNY"},
                currency="CNY",
                timezone="Asia/Shanghai",
                source_raw_ref="mongo://openbb_raw_payloads/raw-1",
            ),
        )
    assert normalized_exc.value.code == DataGatewayErrorCode.EVIDENCE_WRITE_FAILED

    attempt_collection = _FakeCollection()
    attempt_store = MongoAttemptStore(attempt_collection)
    from claw_trade.data_gateway.models import ProviderAttempt

    with pytest.raises(DataGatewayError) as attempt_exc:
        attempt_store.write(
            ProviderAttempt(
                attempt_id="attempt-consumer",
                run_id=request.run_id,
                call_id=request.call_id,
                worker_id=request.worker_id,
                pack=request.domain.value,
                provider=spec.provider,
                adapter_id=spec.adapter_id,
                adapter_kind=spec.provider_kind.value,
                provider_kind=spec.provider_kind,
                provider_config_version=spec.provider_config_version,
                endpoint=spec.endpoint,
                source_role=spec.source_role,
                started_at="2026-05-17T10:00:00+00:00",
                finished_at="2026-05-17T10:00:00+00:00",
                status=ProviderStatus.REMOTE_SUCCESS,
                required=spec.required,
                attempt_required=spec.attempt_required,
                coverage_group=spec.coverage_group,
                coverage_quorum=spec.coverage_quorum,
                priority_source=spec.priority_source,
                user_preferred=spec.user_preferred,
                from_cache=False,
                cache_status=None,
                single_flight_role="consumer",
                shared_from_attempt_id="attempt-owner",
                latency_ms=0,
                row_count=1,
                raw_ref="mongo://openbb_raw_payloads/raw-owner",
                normalized_ref="mongo://openbb_normalized/norm-owner",
                error_code=None,
                error_message=None,
                schema_id=spec.expected_schema_id,
                license_note="ok",
            )
        )
    assert attempt_exc.value.code == DataGatewayErrorCode.EVIDENCE_WRITE_FAILED


def test_raw_export_policy_redacted_does_not_store_inline_payload() -> None:
    spec = _spec()
    request = _request()
    raw_collection = _FakeCollection()
    raw_store = MongoRawPayloadStore(raw_collection)

    result = raw_store.write_raw_with_result(
        request=request,
        spec=spec,
        payload={"secret": "do-not-inline", "value": 1},
        content_type="application/json",
        source_url="https://example.com",
        raw_export_policy="redacted",
    )

    doc = raw_collection.docs[result.payload_hash]
    assert doc["payload_storage"] == "redacted"
    assert doc["payload"] is None
    assert doc["object_ref"] is None
    assert "do-not-inline" not in doc["redacted_snapshot"]
    assert doc["last_seen_run_id"] == request.run_id


def test_rate_limit_store_reserve_tracks_usage_and_blocks_after_limit() -> None:
    collection = _FakeCollection()
    store = MongoRateLimitStore(collection, default_window_seconds=60)
    spec = _spec()
    request = _request()

    assert store.reserve(spec, request, limit=1, window_seconds=60) is True
    assert store.reserve(spec, request, limit=1, window_seconds=60) is False

    docs = list(collection.docs.values())
    assert len(docs) == 1
    assert docs[0]["provider"] == spec.provider
    assert docs[0]["endpoint"] == spec.endpoint
    assert docs[0]["limit"] == 1
    assert docs[0]["used"] == 1
    assert docs[0]["last_error"] == "rate_limit_exhausted"


def test_rate_limit_store_write_failure_is_explicit_error() -> None:
    collection = _FakeCollection()
    collection.raise_write = RuntimeError("rate-limit write failed")
    store = MongoRateLimitStore(collection)
    spec = _spec()
    request = _request()

    with pytest.raises(DataGatewayError) as excinfo:
        store.reserve(spec, request, limit=1)
    assert excinfo.value.code == DataGatewayErrorCode.EVIDENCE_WRITE_FAILED
