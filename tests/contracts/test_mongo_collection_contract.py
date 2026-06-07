from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from claw_trade.data_gateway.execution import ProviderResultCache, ResultRefs
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter, RateLimitPolicy
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.maintenance.jobs import (
    CollectionMaintenanceJobRepository,
    MaintenanceJob,
)
from claw_trade.data_gateway.warehouse import ALLOWED_MONGO_COLLECTIONS, DatasetRepository


class _MongoLikeCollection:
    def __init__(self) -> None:
        self._docs: list[dict[str, object]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.find_calls: list[dict[str, object]] = []
        self.bulk_write_calls: list[dict[str, object]] = []

    def find(self, criteria: dict[str, object]) -> list[dict[str, object]]:
        self.find_calls.append(dict(criteria))
        if not criteria:
            return [dict(item) for item in self._docs]
        return [dict(item) for item in self._docs if all(item.get(k) == v for k, v in criteria.items())]

    def find_one(self, criteria: dict[str, object]) -> dict[str, object] | None:
        rows = self.find(criteria)
        return rows[0] if rows else None

    def replace_one(self, criteria: dict[str, object], doc: dict[str, object], *, upsert: bool = False) -> None:
        for idx, row in enumerate(self._docs):
            if all(row.get(k) == v for k, v in criteria.items()):
                self._docs[idx] = dict(doc)
                return
        if upsert:
            self._docs.append(dict(doc))

    def insert_one(self, doc: dict[str, object]) -> None:
        self._docs.append(dict(doc))

    def delete_one(self, criteria: dict[str, object]) -> None:
        for idx, row in enumerate(self._docs):
            if all(row.get(k) == v for k, v in criteria.items()):
                self._docs.pop(idx)
                return

    def bulk_write(self, operations: list[object], *, ordered: bool = True) -> None:
        self.bulk_write_calls.append({"count": len(operations), "ordered": ordered})
        for operation in operations:
            criteria = getattr(operation, "_filter")
            doc = getattr(operation, "_doc")
            upsert = bool(getattr(operation, "_upsert"))
            self._replace_one_internal(criteria, doc, upsert=upsert)

    def create_index(self, fields: list[tuple[str, int]], **kwargs: object) -> str:
        self.created_indexes.append({"fields": tuple(fields), **kwargs})
        return str(kwargs.get("name") or fields)

    def _replace_one_internal(self, criteria: dict[str, object], doc: dict[str, object], *, upsert: bool = False) -> None:
        for idx, row in enumerate(self._docs):
            if all(row.get(k) == v for k, v in criteria.items()):
                self._docs[idx] = dict(doc)
                return
        if upsert:
            self._docs.append(dict(doc))


class _MongoLikeDatabase:
    def __init__(self) -> None:
        self._collections = {name: _MongoLikeCollection() for name in ALLOWED_MONGO_COLLECTIONS}

    def __getitem__(self, name: str) -> _MongoLikeCollection:
        return self._collections[name]

    def collection(self, name: str) -> _MongoLikeCollection:
        return self._collections[name]


def test_only_eight_authorized_mongo_collections_are_used() -> None:
    assert ALLOWED_MONGO_COLLECTIONS == (
        "normalized_datasets",
        "raw_payloads",
        "provider_attempts",
        "provider_rate_limits",
        "single_flight_calls",
        "provider_result_cache",
        "dataset_manifests",
        "maintenance_jobs",
    )
    assert len(ALLOWED_MONGO_COLLECTIONS) == 8
    assert all(not name.startswith("open" + "bb_") for name in ALLOWED_MONGO_COLLECTIONS)


def test_repository_exposes_the_same_locked_collection_contract() -> None:
    assert DatasetRepository.collection_names() == ALLOWED_MONGO_COLLECTIONS


def test_execution_state_is_persisted_only_in_authorized_three_collections() -> None:
    repository = DatasetRepository()
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)

    cache = ProviderResultCache(repository=repository)
    cache.put_remote_success(
        cache_key="cache:key",
        refs=ResultRefs(dataset_refs=("dataset:1",), raw_refs=("raw:1",), attempt_refs=("attempt:1",)),
        fresh_until=now + timedelta(seconds=30),
        stale_until=now + timedelta(seconds=300),
    )

    limiter = RateLimiter(repository=repository, now_fn=lambda: now)
    assert limiter.reserve("rl:key", RateLimitPolicy(window_seconds=60, max_requests=10)).allowed is True

    sf = SingleFlight(repository=repository, now_fn=lambda: now)
    owner = sf.acquire("sf:key", lease_ttl_seconds=30)
    assert owner.kind == "owner"

    assert repository.read_provider_result_cache("cache:key") is not None
    assert repository.get_latest_rate_limit_record(rate_limit_key="rl:key") is not None
    assert repository.read_single_flight_call("sf:key") is not None


def test_each_authorized_collection_has_repository_access_contract() -> None:
    repository = DatasetRepository()
    repository.insert_normalized(
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "universe_ref": None,
            "granularity": "daily",
            "period_start": datetime(2026, 5, 1, tzinfo=UTC),
            "period_end": datetime(2026, 5, 31, tzinfo=UTC),
            "field_set": ("close",),
            "as_of": datetime(2026, 5, 31, tzinfo=UTC),
            "fresh_until": datetime(2026, 6, 1, tzinfo=UTC),
            "source_roles": ("official",),
            "exchange": "SSE",
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "base_asset": None,
            "quote_asset": None,
            "provider_lineage": {"provider": "p", "endpoint": "e"},
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
            "row": {"close": 1},
        }
    )
    assert repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
    )

    raw_ref = repository.insert_raw_payload({"raw_ref": "raw:CN_A:p:1", "provider": "p", "endpoint": "e"})
    assert repository.get_raw_payload(raw_ref) is not None
    attempt_ref = repository.insert_provider_attempt({"attempt_ref": "attempt:p:e:1", "provider": "p", "endpoint": "e"})
    assert repository.get_provider_attempt(attempt_ref) is not None

    now = datetime(2026, 5, 31, tzinfo=UTC)
    _ = repository.get_or_init_rate_limit_record(
        rate_limit_key="rl:key",
        window_start=now,
        window_seconds=60,
        max_requests=10,
        safety_margin=0,
        overflow_policy="fail_fast",
    )
    assert repository.get_latest_rate_limit_record(rate_limit_key="rl:key") is not None

    assert repository.try_insert_single_flight_call(key="sf:key", owner_token="owner", lease_expires_at=now)
    assert repository.read_single_flight_call("sf:key") is not None

    repository.write_provider_result_cache(
        cache_key="cache:key",
        status="remote_success",
        dataset_refs=("dataset:1",),
        raw_refs=("raw:1",),
        attempt_refs=("attempt:1",),
        fresh_until=now + timedelta(seconds=10),
        stale_until=now + timedelta(seconds=30),
    )
    assert repository.read_provider_result_cache("cache:key") is not None

    manifest_ref = repository.write_dataset_manifest({"manifest_ref": "manifest:1", "dataset_refs": ("dataset:1",)})
    assert repository.get_dataset_manifest(manifest_ref) is not None

    maintenance_repo = CollectionMaintenanceJobRepository(repository=repository)
    job = MaintenanceJob(job_id="job:1", job_type="seed_import", market="CN_A", dataset_scope="daily_bar")
    maintenance_repo.save(job)
    loaded = maintenance_repo.get(job.job_id)
    assert loaded is not None
    assert loaded.job_id == "job:1"


def test_collection_like_database_adapter_is_shared_across_repository_instances() -> None:
    mongo_like = _MongoLikeDatabase()
    repo_a = DatasetRepository.from_database(mongo_like)
    repo_b = DatasetRepository.from_database(mongo_like)

    raw_ref = repo_a.insert_raw_payload({"raw_ref": "raw:US:sec:1", "provider": "sec", "endpoint": "filings"})
    assert repo_b.get_raw_payload(raw_ref) is not None

    attempt_ref = repo_a.insert_provider_attempt(
        {"attempt_ref": "attempt:sec:filings:1", "provider": "sec", "endpoint": "filings", "remote_success": False}
    )
    assert repo_b.get_provider_attempt(attempt_ref) is not None


def test_repository_initializes_key_and_query_indexes_for_mongo_collections() -> None:
    mongo_like = _MongoLikeDatabase()
    DatasetRepository.from_database(mongo_like)

    normalized_indexes = mongo_like.collection("normalized_datasets").created_indexes
    assert {
        "normalized_datasets__dataset_ref",
        "normalized_datasets__dataset__market__symbol_id__universe_ref",
    } <= {str(index["name"]) for index in normalized_indexes}
    key_index = next(index for index in normalized_indexes if index["name"] == "normalized_datasets__dataset_ref")
    assert key_index["unique"] is True


def test_repository_bulk_upserts_normalized_documents_for_mongo_backend() -> None:
    mongo_like = _MongoLikeDatabase()
    repo = DatasetRepository.from_database(mongo_like)
    refs = repo.upsert_normalized_documents(
        (
            _normalized_record(symbol_id="600519.SH", close=1.0),
            _normalized_record(symbol_id="000001.SZ", close=2.0),
        )
    )

    normalized = mongo_like.collection("normalized_datasets")
    assert len(refs) == 2
    assert normalized.bulk_write_calls == [{"count": 2, "ordered": False}]
    assert normalized.find({}) and len(normalized.find({})) == 2


def test_legacy_query_normalized_uses_mongo_criteria_instead_of_full_collection_scan() -> None:
    mongo_like = _MongoLikeDatabase()
    repo = DatasetRepository.from_database(mongo_like, allow_normalized_mongo_read=True)
    repo.upsert_normalized_documents(
        (
            _normalized_record(symbol_id="600519.SH", close=1.0),
            _normalized_record(symbol_id="000001.SZ", close=2.0),
        )
    )
    normalized = mongo_like.collection("normalized_datasets")
    normalized.find_calls.clear()

    rows = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
    )

    assert len(rows) == 1
    assert normalized.find_calls == [
        {"dataset": "daily_bar", "market": "CN_A", "symbol_id": "600519.SH"}
    ]


def test_database_backed_repository_does_not_read_mongo_normalized_rows_by_default() -> None:
    mongo_like = _MongoLikeDatabase()
    repo = DatasetRepository.from_database(mongo_like)
    repo.upsert_normalized_documents((_normalized_record(symbol_id="600519.SH", close=1.0),))

    rows = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
    )

    assert rows == ()


def test_database_backed_direct_normalized_row_helpers_are_maintenance_only() -> None:
    from claw_trade.data_gateway.maintenance.normalized_rows import discard_normalized_mongo_rows

    mongo_like = _MongoLikeDatabase()
    repo = DatasetRepository.from_database(mongo_like)
    refs = repo.upsert_normalized_documents((_normalized_record(symbol_id="600519.SH", close=1.0),))

    assert not hasattr(repo, "get_normalized_document")
    assert not hasattr(repo, "delete_normalized_documents")

    assert repo.get_normalized_document_for_maintenance(refs[0]) is not None
    with pytest.raises(RuntimeError, match="normalized_mongo_discard_requires_explicit_confirmation"):
        discard_normalized_mongo_rows(repo, {"dataset": "daily_bar"})
    dry_run = discard_normalized_mongo_rows(repo, {"dataset": "daily_bar"}, dry_run=True)
    assert dry_run.matched_count == 1
    assert dry_run.deleted_count == 0
    assert repo.get_normalized_document_for_maintenance(refs[0]) is not None
    result = discard_normalized_mongo_rows(repo, {"dataset": "daily_bar"}, confirmed=True)
    assert result.deleted_count == 1
    assert repo.get_normalized_document_for_maintenance(refs[0]) is None


def _normalized_record(*, symbol_id: str, close: float) -> dict[str, object]:
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": symbol_id,
        "universe_ref": None,
        "granularity": "daily",
        "period_start": datetime(2026, 5, 1, tzinfo=UTC),
        "period_end": datetime(2026, 5, 1, tzinfo=UTC),
        "field_set": ("close",),
        "as_of": datetime(2026, 5, 1, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 1, tzinfo=UTC),
        "source_roles": ("official",),
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {"provider": "p", "endpoint": "e"},
        "schema_id": "daily_bar.v1",
        "quality_flags": (),
        "row": {"close": close},
    }
