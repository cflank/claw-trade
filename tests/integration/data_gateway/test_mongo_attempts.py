from __future__ import annotations

from datetime import UTC, datetime
import multiprocessing as mp
import os
from pathlib import Path
from uuid import uuid4

from pymongo import MongoClient
import pytest

from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.store import (
    MongoAttemptStore,
    MongoSingleFlightCoordinator,
    ensure_openbb_store_indexes,
)
from claw_trade.data_gateway.store.mongo import OPENBB_PROVIDER_ATTEMPTS, OPENBB_SINGLE_FLIGHT_CALLS


def _require_mongo_uri() -> str:
    uri = (os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_SOCIAL_MONGODB_URI") or "").strip()
    if not uri:
        pytest.skip("缺少真实 Mongo 环境变量 DATA_GATEWAY_MONGODB_URI/CN_A_SOCIAL_MONGODB_URI，跳过集成验收")
    return uri


def _request(*, run_id: str, call_id: str) -> PackRequest:
    return PackRequest(
        run_id=run_id,
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


@pytest.mark.integration
def test_single_flight_persistent_lease_allows_only_one_owner_attempt() -> None:
    uri = _require_mongo_uri()
    db_name = f"claw_trade_it_{uuid4().hex[:8]}"
    run_id = f"run-{uuid4().hex[:10]}"
    spec = _spec()
    call_owner = f"call-owner-{uuid4().hex[:6]}"
    call_consumer = f"call-consumer-{uuid4().hex[:6]}"

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    ensure_openbb_store_indexes(db)
    db[OPENBB_PROVIDER_ATTEMPTS].delete_many({})
    db[OPENBB_SINGLE_FLIGHT_CALLS].delete_many({})
    client.close()

    queue: mp.Queue[tuple[str, str]] = mp.Queue()
    owner_proc = mp.Process(
        target=_single_flight_worker,
        args=(uri, db_name, run_id, call_owner, spec.call_key, queue),
    )
    consumer_proc = mp.Process(
        target=_single_flight_worker,
        args=(uri, db_name, run_id, call_consumer, spec.call_key, queue),
    )
    owner_proc.start()
    consumer_proc.start()
    owner_proc.join(timeout=30)
    consumer_proc.join(timeout=30)
    assert owner_proc.exitcode == 0
    assert consumer_proc.exitcode == 0

    results = [queue.get(timeout=5), queue.get(timeout=5)]
    statuses = sorted(status for status, _role in results)
    roles = sorted(role for _status, role in results)
    assert statuses == [ProviderStatus.REMOTE_SUCCESS.value, ProviderStatus.SHARED_RESULT.value]
    assert roles == ["consumer", "owner"]

    verify_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    verify_db = verify_client[db_name]
    attempts = list(verify_db[OPENBB_PROVIDER_ATTEMPTS].find({"run_id": run_id}))
    owner_attempts = [row for row in attempts if row.get("single_flight_role") == "owner"]
    consumer_attempts = [row for row in attempts if row.get("single_flight_role") == "consumer"]
    assert len(owner_attempts) == 1
    assert len(consumer_attempts) == 1
    assert owner_attempts[0]["status"] == ProviderStatus.REMOTE_SUCCESS.value
    assert consumer_attempts[0]["status"] == ProviderStatus.SHARED_RESULT.value
    assert consumer_attempts[0]["shared_from_attempt_id"] == owner_attempts[0]["attempt_id"]
    assert consumer_attempts[0]["raw_ref"] == owner_attempts[0]["raw_ref"]
    assert consumer_attempts[0]["normalized_ref"] == owner_attempts[0]["normalized_ref"]
    verify_db.client.drop_database(db_name)
    verify_client.close()


def _single_flight_worker(
    uri: str,
    db_name: str,
    run_id: str,
    call_id: str,
    call_key: str,
    queue: mp.Queue[tuple[str, str]],
) -> None:
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    attempt_store = MongoAttemptStore(db[OPENBB_PROVIDER_ATTEMPTS])
    coordinator = MongoSingleFlightCoordinator(
        collection=db[OPENBB_SINGLE_FLIGHT_CALLS],
        attempt_store=attempt_store,
        lease_seconds=15,
        wait_timeout_seconds=10,
    )
    spec = _spec()
    request = _request(run_id=run_id, call_id=call_id)

    def _owner_fn() -> ProviderResult:
        started = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
        attempt = ProviderAttempt(
            attempt_id=f"{call_id}:owner",
            run_id=run_id,
            call_id=call_id,
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
            latency_ms=30,
            row_count=1,
            raw_ref=f"mongo://openbb_raw_payloads/{run_id}-raw",
            normalized_ref=f"mongo://openbb_normalized/{run_id}-norm",
            error_code=None,
            error_message=None,
            schema_id=spec.expected_schema_id,
            license_note="ok",
        )
        attempt_store.write(attempt)
        return ProviderResult(
            spec=spec,
            status=ProviderStatus.REMOTE_SUCCESS,
            request_id=None,
            requested_at=started,
            latency_ms=30,
            source_role=spec.source_role,
            freshness=FreshnessStatus.FRESH_REMOTE,
            license_note="ok",
            raw_ref=attempt.raw_ref,
            normalized_ref=attempt.normalized_ref,
            rows=({"close": 10.2},),
            row_count=1,
            cache_receipt=None,
            attempt=attempt,
        )

    result = coordinator.run(request=request, spec=spec, fn=_owner_fn)
    queue.put((result.status.value, result.attempt.single_flight_role))
    client.close()
