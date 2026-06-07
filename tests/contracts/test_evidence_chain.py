from __future__ import annotations

from dataclasses import dataclass

from claw_trade.data_gateway.execution.fetch_engine import FetchResult
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.warehouse import (
    ALLOWED_MONGO_COLLECTIONS,
    DatasetRepository,
    Warehouse,
)
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse


@dataclass(frozen=True)
class _Batch:
    provider_id: str = "provider"
    endpoint_id: str = "endpoint"
    market: str = "CN_A"
    data_type: str = "daily_bar"
    granularity: str = "daily"
    fields_union: tuple[str, ...] = ("close",)
    exchange: str | None = "SSE"
    currency: str | None = "CNY"
    timezone: str | None = "Asia/Shanghai"
    calendar: str | None = "CN_A_SSE_SZSE"
    base_asset: str | None = None
    quote_asset: str | None = None
    raw_storage_mode: str = "store_full"


class _DropAttemptLog(AttemptLog):
    def record(self, **kwargs: object) -> tuple[str, ...]:  # type: ignore[override]
        return ()


def _pipeline(attempt_log: AttemptLog | None = None) -> IngestPipeline:
    return IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=attempt_log or AttemptLog(),
    )


def test_remote_success_requires_raw_and_attempt_refs_for_auditable_success() -> None:
    batch = _Batch()
    fetch = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.3}],
    )
    ingest = _pipeline().ingest(fetch, batch)
    assert ingest.remote_success is True
    assert ingest.status == "ingested"
    assert ingest.raw_refs
    assert ingest.attempt_refs
    assert ingest.dataset_refs


def test_evidence_write_failure_is_fail_closed_and_not_remote_success() -> None:
    batch = _Batch()
    fetch = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.3}],
    )
    ingest = _pipeline(attempt_log=_DropAttemptLog()).ingest(fetch, batch)
    assert ingest.remote_success is False
    assert ingest.status == "failed"
    assert [gap.reason for gap in ingest.gaps] == ["evidence_write_failed"]


def test_remote_success_with_metadata_only_or_no_store_keeps_auditable_refs() -> None:
    for mode in ("metadata_only", "no_store"):
        repo = DatasetRepository()
        pipeline = IngestPipeline(
            raw_store=RawStore(repository=repo),
            normalizer=Normalizer(),
            normalized_store=NormalizedStore(repository=repo),
            attempt_log=AttemptLog(repository=repo),
        )
        batch = _Batch(raw_storage_mode=mode)
        fetch = FetchResult.from_success(
            batch,
            payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.3}],
        )
        ingest = pipeline.ingest(fetch, batch)
        assert ingest.remote_success is True
        assert ingest.raw_refs
        assert ingest.attempt_refs
        raw_doc = repo.get_raw_payload(ingest.raw_refs[0])
        assert raw_doc is not None
        assert raw_doc["storage_mode"] == mode
        assert raw_doc["object_uri"] is None
        assert raw_doc["metadata_ref"]


def test_non_remote_statuses_must_not_be_remote_success() -> None:
    pipeline = _pipeline()
    batch = _Batch()
    for status in ("cache_hit", "shared_result", "rate_limited", "cached_empty", "cooldown_skipped"):
        gate = type("Gate", (), {"kind": status, "refs": None, "evidence_refs": ("attempt:x",)})()
        ingest = pipeline.record_gate_result(batch, gate)
        assert ingest.remote_success is False


class _MongoLikeCollection:
    def __init__(self) -> None:
        self._docs: list[dict[str, object]] = []

    def find(self, criteria: dict[str, object]) -> list[dict[str, object]]:
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


class _MongoLikeDatabase:
    def __init__(self) -> None:
        self._collections = {name: _MongoLikeCollection() for name in ALLOWED_MONGO_COLLECTIONS}

    def __getitem__(self, name: str) -> _MongoLikeCollection:
        return self._collections[name]


def test_ingest_store_writes_are_visible_across_repository_instances_with_shared_collection_adapter(tmp_path) -> None:
    db = _MongoLikeDatabase()
    columnar_root = tmp_path / "normalized"
    writer_repo = DatasetRepository.from_database(
        db,
        normalized_columnar=NormalizedColumnarWarehouse(columnar_root),
    )
    reader_repo = DatasetRepository.from_database(
        db,
        normalized_columnar=NormalizedColumnarWarehouse(columnar_root),
    )
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=writer_repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=writer_repo),
        attempt_log=AttemptLog(repository=writer_repo),
    )
    batch = _Batch()
    fetch = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.3}],
    )
    ingest = pipeline.ingest(fetch, batch)
    assert ingest.dataset_refs
    assert ingest.raw_refs
    assert ingest.attempt_refs

    wh = Warehouse(reader_repo)
    result = wh.query(
        {
            "request_id": "req-x",
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "000001.SZ",
            "granularity": "daily",
            "fields": ("close",),
            "freshness_policy": "trading_day",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
        }
    )
    assert result.status == "ready"
    assert reader_repo.get_raw_payload(ingest.raw_refs[0]) is not None
    assert reader_repo.get_provider_attempt(ingest.attempt_refs[0]) is not None
