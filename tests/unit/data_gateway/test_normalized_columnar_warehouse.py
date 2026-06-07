from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from claw_trade.data_gateway.models import CoverageRequirement, GapReason, Market, WarehouseCheck
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse
from claw_trade.data_gateway.warehouse.repository import DatasetRepository
from claw_trade.data_gateway.warehouse.warehouse import Warehouse


def _collections() -> dict[str, dict[str, object]]:
    return {name: {} for name in DatasetRepository.collection_names()}


class _ManifestCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.find_calls: list[dict[str, Any]] = []

    def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def find_one(self, criteria: Mapping[str, Any]) -> dict[str, Any] | None:
        for doc in self.find(criteria):
            return doc
        return None

    def find(self, criteria: Mapping[str, Any], _projection: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        criteria_dict = dict(criteria)
        self.find_calls.append(criteria_dict)
        if not criteria_dict:
            raise AssertionError("dataset_manifests_full_scan")
        return [dict(doc) for doc in self.docs.values() if _manifest_matches_criteria(doc, criteria_dict)]

    def replace_one(self, _criteria: Mapping[str, Any], payload: Mapping[str, Any], *, upsert: bool = False) -> None:
        del upsert
        self.docs[str(payload["manifest_ref"])] = dict(payload)


def _manifest_matches_criteria(doc: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    for key, expected in criteria.items():
        actual = doc.get(key)
        if isinstance(expected, Mapping):
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            continue
        if actual != expected:
            return False
    return True


def _daily_row(symbol: str = "600519.SH") -> dict[str, object]:
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": symbol,
        "universe_ref": None,
        "granularity": "daily",
        "period_start": "2026-06-04",
        "period_end": "2026-06-04",
        "as_of": datetime(2026, 6, 4, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 5, tzinfo=UTC),
        "source_roles": ("built_in_public",),
        "exchange": "SH",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {"provider_id": "test"},
        "schema_id": "daily_bar.v1",
        "quality_flags": (),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000,
    }


def _warehouse_check(symbol: str = "600519.SH") -> WarehouseCheck:
    return WarehouseCheck(
        request_id=f"req-parquet:{symbol}",
        market=Market.CN_A,
        symbol_id=symbol,
        universe_ref=None,
        data_type="daily_bar",
        granularity="daily",
        fields=("close", "volume"),
        date_range_start=datetime(2026, 6, 4, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 4, tzinfo=UTC),
        freshness_policy="trading_day",
        timezone="Asia/Shanghai",
        calendar="CN_A_SSE_SZSE",
        as_of=datetime(2026, 6, 4, tzinfo=UTC),
    )


def _coverage(symbol: str = "600519.SH") -> CoverageRequirement:
    request_id = f"req-parquet:{symbol}"
    return CoverageRequirement(
        request_ids=(request_id,),
        expected_outputs=("daily_bar",),
        required_fields_by_request={request_id: ("close", "volume")},
    )


def _universe_check() -> WarehouseCheck:
    return WarehouseCheck(
        request_id="req-parquet:all-a-shares",
        market=Market.CN_A,
        symbol_id=None,
        universe_ref="all_a_shares",
        data_type="daily_bar",
        granularity="daily",
        fields=("close", "volume"),
        date_range_start=datetime(2026, 6, 4, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 4, tzinfo=UTC),
        freshness_policy="trading_day",
        timezone="Asia/Shanghai",
        calendar="CN_A_SSE_SZSE",
        as_of=datetime(2026, 6, 4, tzinfo=UTC),
    )


def _universe_coverage() -> CoverageRequirement:
    return CoverageRequirement(
        request_ids=("req-parquet:all-a-shares",),
        expected_outputs=("daily_bar",),
        required_fields_by_request={"req-parquet:all-a-shares": ("close", "volume")},
    )


def test_repository_writes_normalized_rows_to_parquet_manifest_not_mongo_rows(tmp_path) -> None:
    collections = _collections()
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )

    refs = repository.upsert_normalized_documents((_daily_row(),))

    assert refs
    assert collections["normalized_datasets"] == {}
    manifests = tuple(collections["dataset_manifests"].values())
    assert len(manifests) == 1
    assert manifests[0]["storage"] == "parquet"
    assert manifests[0]["sha256"]
    assert manifests[0]["dataset_refs"] == refs


def test_columnar_manifest_lookup_uses_targeted_query_not_full_scan(tmp_path) -> None:
    collections: dict[str, object] = _collections()
    manifest_collection = _ManifestCollection()
    collections["dataset_manifests"] = manifest_collection
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents((_daily_row(),))

    records = repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
        date_range_start=datetime(2026, 6, 4, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 4, tzinfo=UTC),
        require_integrity_metadata=True,
        include_row=True,
    )

    assert records
    assert manifest_collection.find_calls
    assert all(call for call in manifest_collection.find_calls)
    query_call = manifest_collection.find_calls[-1]
    assert query_call["storage"] == "parquet"
    assert query_call["status"] == "active"
    assert query_call["dataset"] == "daily_bar"
    assert query_call["market"] == "CN_A"
    assert query_call["period_start_min"] == {"$lte": "2026-06-04"}
    assert query_call["period_end_max"] == {"$gte": "2026-06-04"}


def test_warehouse_reads_normalized_rows_from_parquet(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents((_daily_row(),))
    warehouse = Warehouse(repository)

    result = warehouse.check((_warehouse_check(),), _coverage())

    assert result.satisfied is True
    assert result.rows[0]["close"] == 10.5
    assert result.rows[0]["volume"] == 1000
    assert "high" not in result.rows[0]
    assert result.dataset_refs


def test_repository_projects_requested_fields_from_columnar_rows(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents((_daily_row(),))

    records = repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
        date_range_start=datetime(2026, 6, 4, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 4, tzinfo=UTC),
        require_integrity_metadata=True,
        include_row=True,
        fields=("close",),
    )

    assert len(records) == 1
    assert records[0].row == {"close": 10.5}


def test_columnar_repository_does_not_silently_fallback_to_mongo_rows_when_configured(tmp_path) -> None:
    collections = _collections()
    old_mongo_repository = DatasetRepository(collections=collections)
    old_mongo_repository.upsert_normalized_documents((_daily_row("000001.SZ"),))
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "empty-normalized"),
    )

    records = repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="000001.SZ",
        universe_ref=None,
        date_range_start=datetime(2026, 6, 4, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 4, tzinfo=UTC),
        require_integrity_metadata=True,
        include_row=True,
    )

    assert records == ()


def test_columnar_read_detects_tampered_parquet_file(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents((_daily_row(),))
    manifest = repository.list_dataset_manifests()[0]
    Path(str(manifest["path"])).write_bytes(b"tampered")

    result = Warehouse(repository).check((_warehouse_check(),), _coverage())

    assert result.satisfied is False
    assert result.gaps
    assert result.gaps[0].reason == GapReason.DATA_INTEGRITY_FAILED


def test_columnar_read_detects_deleted_parquet_file(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents((_daily_row(),))
    manifest = repository.list_dataset_manifests()[0]
    Path(str(manifest["path"])).unlink()

    result = Warehouse(repository).check((_warehouse_check(),), _coverage())

    assert result.satisfied is False
    assert result.gaps
    assert result.gaps[0].reason == GapReason.DATA_INTEGRITY_FAILED


def test_columnar_upsert_supersedes_old_partition_for_same_dataset_ref(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    first = _daily_row()
    second = dict(first)
    second["close"] = 12.5
    repository.upsert_normalized_documents((first,))
    repository.upsert_normalized_documents((second,))

    result = Warehouse(repository).check((_warehouse_check(),), _coverage())
    manifests = repository.list_dataset_manifests()

    assert result.satisfied is True
    assert len(result.rows) == 1
    assert result.rows[0]["close"] == 12.5
    assert sum(1 for manifest in manifests if manifest.get("status") == "active") == 1
    assert sum(1 for manifest in manifests if manifest.get("status") == "superseded") == 1


def test_columnar_universe_coverage_uses_aggregate_metadata_not_row_materialization(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    first = _daily_row("600519.SH")
    first["universe_ref"] = "all_a_shares"
    second = _daily_row("000001.SZ")
    second["universe_ref"] = "all_a_shares"
    repository.upsert_normalized_documents((first, second))

    result = Warehouse(repository).check_coverage((_universe_check(),), _universe_coverage())

    assert result.satisfied is True
    assert result.rows == ()
    assert len(result.dataset_refs) == 2
    coverage = result.freshness["coverage_by_request"][0]
    assert coverage["record_count"] == 2
    assert coverage["read_mode"] == "aggregate_metadata"
