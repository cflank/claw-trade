from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from claw_trade.data_gateway.execution import GateDecision, ResultRefs
from claw_trade.data_gateway.execution.fetch_engine import FetchResult
from claw_trade.data_gateway.ingest import IngestResult
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore


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
    universe_ref: str | None = None
    raw_storage_mode: str = "store_full"
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    license_policy: dict[str, object] | None = None


class _BrokenAttemptLog(AttemptLog):
    def record(self, **kwargs: object) -> tuple[str, ...]:  # type: ignore[override]
        raise RuntimeError("write failed")


def test_ingest_pipeline_success_writes_raw_normalized_attempt_refs() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert ingest.status == "ingested"
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.dataset_refs
    assert ingest.attempt_refs


def test_normalized_store_writes_one_checksum_for_batch() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    store = NormalizedStore(repository=repo)

    refs = store.upsert(
        (
            {
                "dataset": "daily_bar",
                "market": "CN_A",
                "symbol_id": "000001.SZ",
                "granularity": "daily",
                "period_start": date(2026, 5, 31),
                "period_end": date(2026, 5, 31),
                "close": 10.2,
                "exchange": "SZSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider_id": "test", "endpoint_id": "daily"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            },
            {
                "dataset": "daily_bar",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "daily",
                "period_start": date(2026, 5, 31),
                "period_end": date(2026, 5, 31),
                "close": 1530.25,
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider_id": "test", "endpoint_id": "daily"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            },
        )
    )

    documents = tuple(repo.get_normalized_document_for_maintenance(ref) for ref in refs)
    assert len(documents) == 2
    checksums = {str(document["dataset_checksum"]) for document in documents if document is not None}
    assert len(checksums) == 1
    assert all(document is not None and document["dataset_row_count"] == 2 for document in documents)
    assert all(
        document is not None and document["dataset_checksum_scope"] == "normalized-batch-v1"
        for document in documents
    )


def test_ingest_pipeline_normalizes_trade_date_and_returns_auditable_dataset_ref() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        granularity="DAILY",
        date_range_start=date(2026, 5, 31),
        date_range_end=date(2026, 5, 31),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "trade_date": "20260531", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs
    stored = repo.get_normalized_document_for_maintenance(ingest.dataset_refs[0])
    assert stored is not None
    assert stored["period_start"] == date(2026, 5, 31)
    assert stored["period_end"] == date(2026, 5, 31)
    assert stored["source_raw_refs"] == ingest.raw_refs

    warehouse_result = Warehouse(repo).query(
        {
            "request_id": "req-daily",
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "000001.SZ",
            "granularity": "daily",
            "fields": ("close",),
            "date_range_start": date(2026, 5, 31),
            "date_range_end": date(2026, 5, 31),
            "freshness_policy": "trading_day",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "as_of": datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
        }
    )
    assert warehouse_result.status == "ready"
    assert warehouse_result.dataset_refs == ingest.dataset_refs


def test_ingest_pipeline_preserves_universe_ref_for_warehouse_recheck() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        universe_ref="all_a_shares",
        date_range_start=date(2026, 6, 4),
        date_range_end=date(2026, 6, 4),
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {"dataset": "daily_bar", "symbol_id": "000001.SZ", "trade_date": "20260604", "close": 10.2},
            {"dataset": "daily_bar", "symbol_id": "430047.BJ", "trade_date": "20260604", "close": 15.3},
        ],
    )

    ingest = pipeline.ingest(result, batch)

    assert len(ingest.dataset_refs) == 2
    stored_documents = tuple(repo.get_normalized_document_for_maintenance(ref) for ref in ingest.dataset_refs)
    assert all(document is not None and document["universe_ref"] == "all_a_shares" for document in stored_documents)
    assert all(
        document is not None and document["row"]["universe_ref"] == "all_a_shares"
        for document in stored_documents
    )

    warehouse_result = Warehouse(repo).query(
        {
            "request_id": "req-universe",
            "dataset": "daily_bar",
            "market": "CN_A",
            "universe_ref": "all_a_shares",
            "granularity": "daily",
            "fields": ("close",),
            "date_range_start": date(2026, 6, 4),
            "date_range_end": date(2026, 6, 4),
            "freshness_policy": "warehouse_only",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "as_of": datetime(2026, 6, 4, 18, 0, tzinfo=UTC),
        }
    )

    assert warehouse_result.status == "ready"
    assert set(warehouse_result.dataset_refs) == set(ingest.dataset_refs)


def test_ingest_pipeline_requested_date_range_without_date_field_produces_datagap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(date_range_start=date(2026, 5, 31), date_range_end=date(2026, 5, 31))
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs == ()
    assert any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_stores_partial_rows_when_provider_date_range_is_short() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "date": "2026-05-15", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is False
    assert ingest.dataset_refs
    assert any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_normalized_store_keeps_same_period_different_field_sets_separate() -> None:
    from claw_trade.data_gateway.models import WarehouseCheck
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    store = NormalizedStore(repository=repo)

    open_interest_refs = store.upsert(
        (
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "realtime",
                "period_start": date(2026, 6, 1),
                "period_end": date(2026, 6, 1),
                "open_interest": 100.0,
                "timestamp": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                "exchange": "COINGLASS",
                "currency": "USDT",
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "provider_lineage": {"provider_id": "coinglass", "endpoint_id": "open_interest"},
                "schema_id": "crypto_derivative_metric.v1",
                "quality_flags": (),
                "as_of": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                "fresh_until": datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
            },
        )
    )
    netflow_refs = store.upsert(
        (
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "realtime",
                "period_start": date(2026, 6, 1),
                "period_end": date(2026, 6, 1),
                "net_inflow": 25.0,
                "timestamp": datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
                "exchange": "COINGLASS",
                "currency": "USDT",
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "provider_lineage": {"provider_id": "coinglass", "endpoint_id": "netflow"},
                "schema_id": "crypto_derivative_metric.v1",
                "quality_flags": (),
                "as_of": datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
                "fresh_until": datetime(2026, 6, 1, 13, 5, tzinfo=UTC),
            },
        )
    )

    assert open_interest_refs != netflow_refs
    assert repo.get_normalized_document_for_maintenance(open_interest_refs[0])["row"]["open_interest"] == 100.0
    assert repo.get_normalized_document_for_maintenance(netflow_refs[0])["row"]["net_inflow"] == 25.0

    result = Warehouse(repo).check(
        (
            WarehouseCheck(
                request_id="req-open-interest",
                market="CRYPTO",
                symbol_id="BTCUSDT",
                data_type="crypto_derivative_metric",
                granularity="realtime",
                fields=("open_interest",),
                date_range_start=date(2026, 6, 1),
                date_range_end=date(2026, 6, 1),
                freshness_policy="immutable_seed",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 1, 12, 10, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert result.status == "ready"
    assert not any(gap.reason == "field_missing" for gap in result.gaps)


def test_normalized_store_field_set_excludes_empty_values() -> None:
    from claw_trade.data_gateway.models import WarehouseCheck
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    refs = NormalizedStore(repository=repo).upsert(
        (
            {
                "dataset": "defi_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "realtime",
                "period_start": date(2026, 6, 1),
                "period_end": date(2026, 6, 1),
                "tvl": None,
                "chains": ("bitcoin",),
                "category": "chain",
                "exchange": "DEFILLAMA",
                "currency": "USD",
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "provider_lineage": {"provider_id": "defillama", "endpoint_id": "protocol"},
                "schema_id": "defi_metric.v1",
                "quality_flags": (),
                "as_of": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                "fresh_until": datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
            },
        )
    )

    stored = repo.get_normalized_document_for_maintenance(refs[0])
    assert "tvl" not in stored["field_set"]

    result = Warehouse(repo).check(
        (
            WarehouseCheck(
                request_id="req-defi",
                market="CRYPTO",
                symbol_id="BTCUSDT",
                data_type="defi_metric",
                granularity="realtime",
                fields=("tvl",),
                freshness_policy="immutable_seed",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 1, 12, 10, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert result.status == "partial"
    assert any(gap.reason == "field_missing" and gap.required_fields == ("tvl",) for gap in result.gaps)


def test_ingest_pipeline_rejects_provider_granularity_mismatch() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(granularity="daily")
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "granularity": "intraday", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs == ()
    assert any(gap.reason == "granularity_mismatch" for gap in ingest.gaps)


def test_ingest_pipeline_missing_market_dimensions_produce_datagap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(exchange=None, currency=None, timezone=None, calendar=None)
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is False
    assert ingest.dataset_refs == ()
    field_missing = [gap for gap in ingest.gaps if gap.reason == "field_missing"]
    assert field_missing
    assert set(field_missing[0].required_fields) >= {"exchange", "currency", "timezone", "calendar"}


def test_ingest_pipeline_non_crypto_with_asset_fields_produces_invalid_request_gap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(base_asset="BTC", quote_asset="USDT")
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is False
    assert ingest.dataset_refs == ()
    assert any(gap.reason == "invalid_request" for gap in ingest.gaps)


def test_ingest_pipeline_license_metadata_only_keeps_auditable_raw_attempt_refs() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(raw_storage_mode="metadata_only")
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.attempt_refs
    raw_doc = repo.get_raw_payload(ingest.raw_refs[0])
    assert raw_doc is not None
    assert raw_doc["storage_mode"] == "metadata_only"
    assert raw_doc["object_uri"] is None
    assert raw_doc["metadata_ref"]
    assert "payload" not in raw_doc
    assert "body" not in raw_doc
    assert "raw_payload" not in raw_doc


def test_ingest_pipeline_license_no_store_keeps_auditable_raw_attempt_refs() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(raw_storage_mode="no_store")
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.attempt_refs
    raw_doc = repo.get_raw_payload(ingest.raw_refs[0])
    assert raw_doc is not None
    assert raw_doc["storage_mode"] == "no_store"
    assert raw_doc["object_uri"] is None
    assert raw_doc["metadata_ref"]
    assert "payload" not in raw_doc
    assert "body" not in raw_doc
    assert "raw_payload" not in raw_doc


def test_ingest_pipeline_license_blocked_normalized_storage_keeps_raw_audit_but_no_dataset_ref() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        raw_storage_mode="metadata_only",
        license_policy={"raw_storage_mode": "metadata_only", "normalized_storage_allowed": False},
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "trade_date": "20260531", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is False
    assert ingest.raw_refs
    assert ingest.dataset_refs == ()
    assert any(gap.reason == "license_blocked" for gap in ingest.gaps)


def test_ingest_pipeline_non_remote_gate_is_not_remote_success() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    gate = GateDecision(kind="cache_hit", refs=ResultRefs(dataset_refs=("dataset:cache",), raw_refs=("raw:cache",)))
    ingest = pipeline.record_gate_result(batch, gate)
    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.dataset_refs == ("dataset:cache",)


def test_ingest_pipeline_rate_limited_gate_does_not_crash_and_is_non_remote() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    gate = GateDecision(kind="rate_limited", evidence_refs=("attempt:rate",))
    ingest = pipeline.record_gate_result(batch, gate)
    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.attempt_refs
    assert ingest.gaps
    assert ingest.gaps[0].reason == "rate_limited"


def test_ingest_pipeline_evidence_write_failed_is_fail_closed() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=_BrokenAttemptLog(),
    )
    batch = _Batch()
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert isinstance(ingest, IngestResult)
    assert ingest.status == "failed"
    assert ingest.remote_success is False
    assert ingest.gaps
    assert ingest.gaps[0].reason == "evidence_write_failed"
