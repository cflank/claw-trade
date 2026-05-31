from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallResult,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.providers.gate import GateRateLimitPolicy, run_provider_call_gate
from claw_trade.data_gateway.providers.run_plan import RateLimitPlanMetadata
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore, build_cache_key
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.single_flight import MongoSingleFlightCoordinator


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            key = query["_id"]
            doc = self.docs.get(key)
            if doc is None:
                return None
            return dict(doc) if _matches_query(doc, query) else None

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        del upsert
        with self._lock:
            self.docs[query["_id"]] = dict(doc)

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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
        if isinstance(value, dict) and "$lte" in value:
            current = doc.get(key)
            if current is None or not current <= value["$lte"]:
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


def _request(*, call_id: str) -> PackRequest:
    return PackRequest(
        run_id="run-gate-contract",
        call_id=call_id,
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


def _owner_success_result(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    attempt_id: str,
    raw_ref: str,
    normalized_ref: str,
    attempt_store: MongoAttemptStore | None = None,
) -> ProviderResult:
    started = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    attempt = ProviderAttempt(
        attempt_id=attempt_id,
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
        started_at=started,
        finished_at=started,
        status=ProviderStatus.REMOTE_SUCCESS,
        required=spec.required,
        attempt_required=spec.attempt_required,
        coverage_group=spec.coverage_group,
        coverage_quorum=spec.coverage_quorum,
        priority_source=spec.priority_source,
        user_preferred=spec.user_preferred,
        from_cache=False,
        cache_status=None,
        single_flight_role="owner",
        shared_from_attempt_id=None,
        latency_ms=10,
        row_count=1,
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        error_code=None,
        error_message=None,
        schema_id=spec.expected_schema_id,
        license_note="ok",
    )
    if attempt_store is not None:
        attempt_store.write(attempt)
    return ProviderResult(
        spec=spec,
        status=ProviderStatus.REMOTE_SUCCESS,
        request_id=None,
        requested_at=started,
        latency_ms=10,
        source_role=spec.source_role,
        freshness=FreshnessStatus.FRESH_REMOTE,
        license_note="ok",
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        rows=({"close": 10.2},),
        row_count=1,
        cache_receipt=None,
        attempt=attempt,
    )


def test_cache_hit_branch_writes_cache_hit_attempt_and_does_not_call_owner() -> None:
    spec = _spec()
    request = _request(call_id="call-cache-hit")
    cache_collection = _FakeCollection()
    rate_collection = _FakeCollection()
    flight_collection = _FakeCollection()
    attempt_collection = _FakeCollection()

    cache_store = MongoCacheStore(cache_collection)
    cache_store.put_success(
        spec=spec,
        request=request,
        raw_ref="mongo://openbb_raw_payloads/raw-cache",
        normalized_ref="mongo://openbb_normalized/norm-cache",
        evidence_hash="sha256:cache-hit",
        ttl_seconds=300,
    )
    attempt_store = MongoAttemptStore(attempt_collection)
    rate_store = MongoRateLimitStore(rate_collection)
    single_flight = MongoSingleFlightCoordinator(
        collection=flight_collection,
        attempt_store=attempt_store,
        wait_timeout_seconds=0.2,
    )

    called = {"count": 0}

    def owner_call() -> ProviderResult:
        called["count"] += 1
        return _owner_success_result(
            request=request,
            spec=spec,
            attempt_id="attempt-owner-cache-should-not-run",
            raw_ref="mongo://openbb_raw_payloads/raw-owner",
            normalized_ref="mongo://openbb_normalized/norm-owner",
        )

    result = run_provider_call_gate(
        request=request,
        spec=spec,
        cache_store=cache_store,
        rate_limit_store=rate_store,
        single_flight=single_flight,
        attempt_store=attempt_store,
        owner_call=owner_call,
    )

    assert called["count"] == 0
    assert result.status == ProviderStatus.CACHE_HIT
    assert result.remote_success is False
    assert result.attempt_ref is not None
    attempt_doc = attempt_collection.docs[result.attempt_ref]
    assert attempt_doc["status"] == ProviderStatus.CACHE_HIT.value
    assert attempt_doc["single_flight_role"] == "none"


def test_cached_empty_branch_writes_cached_empty_attempt_and_gap() -> None:
    spec = _spec()
    request = _request(call_id="call-cached-empty")
    cache_collection = _FakeCollection()
    rate_collection = _FakeCollection()
    flight_collection = _FakeCollection()
    attempt_collection = _FakeCollection()

    cache_store = MongoCacheStore(cache_collection)
    cache_store.put_empty(spec=spec, request=request, ttl_seconds=300, evidence_hash="sha256:empty")
    attempt_store = MongoAttemptStore(attempt_collection)
    rate_store = MongoRateLimitStore(rate_collection)
    single_flight = MongoSingleFlightCoordinator(
        collection=flight_collection,
        attempt_store=attempt_store,
        wait_timeout_seconds=0.2,
    )

    result = run_provider_call_gate(
        request=request,
        spec=spec,
        cache_store=cache_store,
        rate_limit_store=rate_store,
        single_flight=single_flight,
        attempt_store=attempt_store,
        owner_call=lambda: _owner_success_result(
            request=request,
            spec=spec,
            attempt_id="attempt-owner-cached-empty-should-not-run",
            raw_ref="mongo://openbb_raw_payloads/raw-owner",
            normalized_ref="mongo://openbb_normalized/norm-owner",
        ),
    )

    assert result.status == ProviderStatus.CACHED_EMPTY
    assert result.remote_success is False
    assert len(result.data_gaps) == 1
    assert result.data_gaps[0].reason.value == "cached_empty"
    assert result.attempt_ref is not None
    attempt_doc = attempt_collection.docs[result.attempt_ref]
    assert attempt_doc["status"] == ProviderStatus.CACHED_EMPTY.value


def test_stale_cache_must_continue_to_owner_remote_success_and_refresh_cache() -> None:
    spec = _spec()
    request = _request(call_id="call-stale-cache")
    cache_collection = _FakeCollection()
    rate_collection = _FakeCollection()
    flight_collection = _FakeCollection()
    attempt_collection = _FakeCollection()

    cache_store = MongoCacheStore(cache_collection)
    cache_store.put_success(
        spec=spec,
        request=request,
        raw_ref="mongo://openbb_raw_payloads/raw-old",
        normalized_ref="mongo://openbb_normalized/norm-old",
        evidence_hash="sha256:old",
        ttl_seconds=300,
    )
    cache_key, _ = build_cache_key(spec, request)
    cache_collection.docs[cache_key]["expires_at"] = "2000-01-01T00:00:00+00:00"

    attempt_store = MongoAttemptStore(attempt_collection)
    rate_store = MongoRateLimitStore(rate_collection)
    single_flight = MongoSingleFlightCoordinator(
        collection=flight_collection,
        attempt_store=attempt_store,
        wait_timeout_seconds=0.3,
    )

    called = {"count": 0}

    def owner_call() -> ProviderResult:
        called["count"] += 1
        return _owner_success_result(
            request=request,
            spec=spec,
            attempt_id="attempt-owner-stale",
            raw_ref="mongo://openbb_raw_payloads/raw-new",
            normalized_ref="mongo://openbb_normalized/norm-new",
        )

    result = run_provider_call_gate(
        request=request,
        spec=spec,
        cache_store=cache_store,
        rate_limit_store=rate_store,
        single_flight=single_flight,
        attempt_store=attempt_store,
        owner_call=owner_call,
    )

    assert called["count"] == 1
    assert result.status == ProviderStatus.REMOTE_SUCCESS
    assert result.remote_success is True
    latest = cache_store.get(spec, request)
    assert latest.status == ProviderStatus.CACHE_HIT
    assert latest.usable_raw_ref == "mongo://openbb_raw_payloads/raw-new"
    assert latest.usable_normalized_ref == "mongo://openbb_normalized/norm-new"


def test_rate_limit_blocked_writes_rate_limited_attempt_without_owner_call() -> None:
    spec = _spec()
    request = _request(call_id="call-rate-limited")
    cache_collection = _FakeCollection()
    rate_collection = _FakeCollection()
    flight_collection = _FakeCollection()
    attempt_collection = _FakeCollection()

    cache_store = MongoCacheStore(cache_collection)
    attempt_store = MongoAttemptStore(attempt_collection)
    rate_store = MongoRateLimitStore(rate_collection, default_window_seconds=60)
    single_flight = MongoSingleFlightCoordinator(
        collection=flight_collection,
        attempt_store=attempt_store,
        wait_timeout_seconds=0.2,
    )
    assert rate_store.reserve(spec, request, limit=1, window_seconds=60) is True

    called = {"count": 0}

    def owner_call() -> ProviderResult:
        called["count"] += 1
        return _owner_success_result(
            request=request,
            spec=spec,
            attempt_id="attempt-owner-rate-should-not-run",
            raw_ref="mongo://openbb_raw_payloads/raw-owner",
            normalized_ref="mongo://openbb_normalized/norm-owner",
        )

    result = run_provider_call_gate(
        request=request,
        spec=spec,
        cache_store=cache_store,
        rate_limit_store=rate_store,
        single_flight=single_flight,
        attempt_store=attempt_store,
        owner_call=owner_call,
        rate_limit_policy=GateRateLimitPolicy(
            metadata=RateLimitPlanMetadata(window_seconds=60, estimated_cost=1, hard_reserved=True),
            hard_limit=1,
        ),
    )

    assert called["count"] == 0
    assert result.status == ProviderStatus.RATE_LIMITED
    assert result.remote_success is False
    assert len(result.data_gaps) == 1
    assert result.data_gaps[0].reason.value == "rate_limited"
    assert result.attempt_ref is not None
    attempt_doc = attempt_collection.docs[result.attempt_ref]
    assert attempt_doc["status"] == ProviderStatus.RATE_LIMITED.value
    assert attempt_doc["raw_ref"] is None
    assert attempt_doc["normalized_ref"] is None


def test_single_flight_consumer_returns_shared_result_not_remote_success() -> None:
    spec = _spec()
    owner_request = _request(call_id="call-owner")
    consumer_request = _request(call_id="call-consumer")
    cache_collection = _FakeCollection()
    rate_collection = _FakeCollection()
    flight_collection = _FakeCollection()
    attempt_collection = _FakeCollection()

    cache_store = MongoCacheStore(cache_collection)
    attempt_store = MongoAttemptStore(attempt_collection)
    rate_store = MongoRateLimitStore(rate_collection)
    single_flight = MongoSingleFlightCoordinator(
        collection=flight_collection,
        attempt_store=attempt_store,
        lease_seconds=15,
        wait_timeout_seconds=1.5,
        poll_interval_seconds=0.01,
    )

    called = {"count": 0}

    def owner_call() -> ProviderResult:
        called["count"] += 1
        result = _owner_success_result(
            request=owner_request,
            spec=spec,
            attempt_id="attempt-owner-single-flight",
            raw_ref="mongo://openbb_raw_payloads/raw-owner-sf",
            normalized_ref="mongo://openbb_normalized/norm-owner-sf",
            attempt_store=attempt_store,
        )
        time.sleep(0.12)
        return result

    owner_result_box: dict[str, ProviderCallResult] = {}
    owner_error_box: dict[str, Exception] = {}

    def owner_runner() -> None:
        try:
            owner_result_box["value"] = run_provider_call_gate(
                request=owner_request,
                spec=spec,
                cache_store=cache_store,
                rate_limit_store=rate_store,
                single_flight=single_flight,
                attempt_store=attempt_store,
                owner_call=owner_call,
            )
        except Exception as exc:  # noqa: BLE001
            owner_error_box["value"] = exc

    thread = threading.Thread(target=owner_runner)
    thread.start()
    time.sleep(0.03)

    consumer_result = run_provider_call_gate(
        request=consumer_request,
        spec=spec,
        cache_store=cache_store,
        rate_limit_store=rate_store,
        single_flight=single_flight,
        attempt_store=attempt_store,
        owner_call=owner_call,
    )
    thread.join(timeout=2)

    assert "value" not in owner_error_box
    owner_result = owner_result_box["value"]
    assert owner_result.status == ProviderStatus.REMOTE_SUCCESS
    assert owner_result.remote_success is True
    assert consumer_result.status == ProviderStatus.SHARED_RESULT
    assert consumer_result.remote_success is False
    assert consumer_result.shared_owner_attempt_ref == "attempt-owner-single-flight"
    assert called["count"] == 1
    consumer_attempt = attempt_collection.docs[consumer_result.attempt_ref or ""]
    assert consumer_attempt["status"] == ProviderStatus.SHARED_RESULT.value
    assert consumer_attempt["single_flight_role"] == "consumer"


def test_owner_remote_success_without_result_refs_is_rejected() -> None:
    spec = _spec()
    request = _request(call_id="call-owner-missing-refs")
    cache_collection = _FakeCollection()
    rate_collection = _FakeCollection()
    flight_collection = _FakeCollection()
    attempt_collection = _FakeCollection()

    cache_store = MongoCacheStore(cache_collection)
    attempt_store = MongoAttemptStore(attempt_collection)
    rate_store = MongoRateLimitStore(rate_collection)
    single_flight = MongoSingleFlightCoordinator(
        collection=flight_collection,
        attempt_store=attempt_store,
        wait_timeout_seconds=0.5,
    )

    def owner_call() -> ProviderResult:
        base = _owner_success_result(
            request=request,
            spec=spec,
            attempt_id="attempt-owner-missing-refs",
            raw_ref="mongo://openbb_raw_payloads/raw-owner",
            normalized_ref="mongo://openbb_normalized/norm-owner",
        )
        return ProviderResult(
            spec=base.spec,
            status=base.status,
            request_id=base.request_id,
            requested_at=base.requested_at,
            latency_ms=base.latency_ms,
            source_role=base.source_role,
            freshness=base.freshness,
            license_note=base.license_note,
            raw_ref=None,
            normalized_ref=None,
            rows=base.rows,
            row_count=base.row_count,
            cache_receipt=base.cache_receipt,
            attempt=base.attempt,
            error_code=base.error_code,
            error_message=base.error_message,
        )

    with pytest.raises(ValueError) as excinfo:
        run_provider_call_gate(
            request=request,
            spec=spec,
            cache_store=cache_store,
            rate_limit_store=rate_store,
            single_flight=single_flight,
            attempt_store=attempt_store,
            owner_call=owner_call,
        )

    assert "remote_success requires raw_ref and normalized_ref" in str(excinfo.value)
