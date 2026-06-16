from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def _daily_row_for_day(day_offset: int, symbol: str = "600519.SH") -> dict[str, object]:
    day = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day_offset)
    row = _daily_row(symbol=symbol)
    row["period_start"] = day.date().isoformat()
    row["period_end"] = day.date().isoformat()
    row["dataset_ref"] = f"dataset:daily_bar:CN_A:{symbol}:daily:{day.date().isoformat()}:{day.date().isoformat()}"
    return row


def _crypto_quote_row(*, dataset_ref: str, day: str, price: float) -> dict[str, object]:
    return {
        "dataset_ref": dataset_ref,
        "dataset": "quote_snapshot",
        "market": "CRYPTO",
        "symbol_id": "SOLUSDT",
        "universe_ref": None,
        "granularity": "realtime",
        "period_start": day,
        "period_end": day,
        "as_of": datetime.fromisoformat(f"{day}T00:00:00+00:00"),
        "fresh_until": datetime.fromisoformat(f"{day}T00:05:00+00:00"),
        "source_roles": ("official",),
        "exchange": "BINANCE",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": "SOL",
        "quote_asset": "USDT",
        "provider_lineage": {"provider_id": "crypto_binance_spot_market", "endpoint_id": "ticker_24hr"},
        "schema_id": "quote_snapshot.v1",
        "quality_flags": (),
        "price": price,
        "price_unit": "USDT",
        "timestamp": datetime.fromisoformat(f"{day}T00:00:00+00:00"),
    }


def _crypto_derivative_row(
    *,
    dataset_ref: str,
    endpoint_id: str,
    fields: Mapping[str, object],
) -> dict[str, object]:
    base: dict[str, object] = {
        "dataset_ref": dataset_ref,
        "dataset": "crypto_derivative_metric",
        "market": "CRYPTO",
        "symbol_id": "SOLUSDT",
        "universe_ref": None,
        "granularity": "1h",
        "period_start": "2026-06-10",
        "period_end": "2026-06-10",
        "as_of": datetime(2026, 6, 10, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 10, 1, tzinfo=UTC),
        "source_roles": ("paid_data",),
        "exchange": "COINGLASS",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": "SOL",
        "quote_asset": "USDT",
        "provider_lineage": {"provider_id": "crypto_coinglass_derivatives", "endpoint_id": endpoint_id},
        "schema_id": "crypto_derivative_metric.v1",
        "quality_flags": (),
        "source_raw_refs": (f"raw:{endpoint_id}",),
        "timestamp": datetime(2026, 6, 10, tzinfo=UTC),
    }
    base.update(fields)
    base["field_set"] = tuple(sorted(key for key in base if key not in {"dataset_ref", "field_set"}))
    return base


def _crypto_valuation_row(
    *,
    dataset_ref: str,
    source_role: str,
    provider_id: str,
    endpoint_id: str,
    fields: Mapping[str, object],
) -> dict[str, object]:
    base: dict[str, object] = {
        "dataset_ref": dataset_ref,
        "dataset": "valuation_metric",
        "market": "CRYPTO",
        "symbol_id": "BNBUSDT",
        "universe_ref": None,
        "granularity": "realtime",
        "period_start": "2026-06-10",
        "period_end": "2026-06-10",
        "as_of": datetime(2026, 6, 10, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 10, 1, tzinfo=UTC),
        "source_roles": (source_role,),
        "exchange": "CRYPTO",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": "BNB",
        "quote_asset": "USDT",
        "provider_lineage": {"provider_id": provider_id, "endpoint_id": endpoint_id},
        "schema_id": "valuation_metric.v1",
        "quality_flags": (),
        "timestamp": datetime(2026, 6, 10, tzinfo=UTC),
    }
    base.update(fields)
    base["field_set"] = tuple(sorted(key for key in base if key not in {"dataset_ref", "field_set"}))
    return base


def _crypto_intraday_row(*, hour: int, close: float) -> dict[str, object]:
    start = datetime(2026, 6, 10, hour, tzinfo=UTC)
    end = start + timedelta(minutes=59, seconds=59, milliseconds=999)
    start_ref = start.isoformat().replace("+00:00", "Z")
    end_ref = end.isoformat().replace("+00:00", "Z")
    return {
        "dataset_ref": f"dataset:intraday_bar:CRYPTO:spot:BTCUSDT:1h:{start_ref}:{end_ref}",
        "dataset": "intraday_bar",
        "market": "CRYPTO",
        "symbol_id": "BTCUSDT",
        "universe_ref": "binance_spot_all_symbols",
        "granularity": "1h",
        "period_start": start,
        "period_end": end,
        "field_set": ("open", "high", "low", "close", "volume", "amount", "open_time", "close_time"),
        "as_of": end,
        "fresh_until": end + timedelta(hours=1),
        "source_roles": ("official",),
        "exchange": "BINANCE",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "provider_lineage": {"provider_id": "crypto_binance_spot_market", "endpoint_id": "spot_intraday_bar"},
        "schema_id": "intraday_bar.v1",
        "quality_flags": (),
        "open": close - 1.0,
        "high": close + 2.0,
        "low": close - 2.0,
        "close": close,
        "volume": 10.0 + hour,
        "volume_unit": "BTC",
        "amount": (10.0 + hour) * close,
        "amount_unit": "USDT",
        "open_time": start,
        "close_time": end,
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


def test_repository_reads_columnar_rows_from_read_only_seed_manifest(tmp_path) -> None:
    runtime_collections = _collections()
    seed_collections = _collections()
    columnar = NormalizedColumnarWarehouse(tmp_path / "normalized")
    result = columnar.write_records((_daily_row(),))
    assert result.manifest is not None
    seed_collections["dataset_manifests"][str(result.manifest["manifest_ref"])] = dict(result.manifest)
    repository = DatasetRepository.from_database(
        runtime_collections,
        seed_database=seed_collections,
        normalized_columnar=columnar,
    )

    rows = repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
    )

    assert len(rows) == 1
    assert rows[0].row["close"] == 10.5
    assert runtime_collections["dataset_manifests"] == {}


def test_columnar_manifest_rebases_legacy_path_to_current_root(tmp_path) -> None:
    runtime_collections = _collections()
    seed_collections = _collections()
    original_root = tmp_path / "original" / "normalized"
    relocated_root = tmp_path / "relocated" / "normalized"
    original_columnar = NormalizedColumnarWarehouse(original_root)
    result = original_columnar.write_records((_daily_row(),))
    manifest = dict(result.manifest)
    manifest.pop("relative_path", None)

    original_path = Path(str(manifest["path"]))
    relocated_path = (
        relocated_root
        / f"market={manifest['market']}"
        / f"dataset={manifest['dataset']}"
        / f"granularity={manifest['granularity']}"
        / original_path.name
    )
    relocated_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.rename(relocated_path)
    seed_collections["dataset_manifests"][str(manifest["manifest_ref"])] = manifest
    repository = DatasetRepository.from_database(
        runtime_collections,
        seed_database=seed_collections,
        normalized_columnar=NormalizedColumnarWarehouse(relocated_root),
    )

    rows = repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
    )

    assert len(rows) == 1
    assert rows[0].row["close"] == 10.5


def test_large_columnar_manifest_stores_compact_dataset_ref_lineage(tmp_path) -> None:
    collections = _collections()
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    rows = tuple(_daily_row_for_day(offset) for offset in range(501))

    refs = repository.upsert_normalized_documents(rows)

    assert len(refs) == 501
    manifests = tuple(collections["dataset_manifests"].values())
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["relative_path"].endswith(".parquet")
    assert len(manifest["dataset_refs"]) == 20
    assert manifest["dataset_ref_count"] == 501
    assert manifest["dataset_refs_truncated"] is True
    assert manifest["dataset_refs_sample"] == manifest["dataset_refs"]
    assert manifest["dataset_refs_sha256"].startswith("sha256:")
    assert manifest["dataset_ref_prefixes"] == ("dataset:daily_bar:CN_A:600519.SH:daily:",)

    result = Warehouse(repository).check(
        (
            _warehouse_check(),
        ),
        _coverage(),
    )
    assert result.status == "ready"


def test_columnar_batch_upsert_dedupes_duplicate_dataset_refs_before_writing(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    first = _daily_row()
    second = dict(first)
    second["close"] = 12.5

    refs = repository.upsert_normalized_documents((first, second))

    result = Warehouse(repository).check((_warehouse_check(),), _coverage())
    manifests = repository.list_dataset_manifests()

    assert refs == ("dataset:daily_bar:CN_A:600519.SH:daily:2026-06-04:2026-06-04",)
    assert len(manifests) == 1
    assert manifests[0]["row_count"] == 1
    assert result.satisfied is True
    assert len(result.rows) == 1
    assert result.rows[0]["close"] == 12.5


def test_realtime_columnar_upsert_supersedes_stale_same_symbol_manifest(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents(
        (_crypto_quote_row(dataset_ref="dataset:quote_snapshot:CRYPTO:old-short-ref", day="2026-06-09", price=100.0),)
    )
    old_manifest = repository.list_dataset_manifests()[0]
    Path(str(old_manifest["path"])).unlink()

    repository.upsert_normalized_documents(
        (_crypto_quote_row(dataset_ref="dataset:quote_snapshot:CRYPTO:new-short-ref", day="2026-06-10", price=110.0),)
    )

    manifests = {str(item["manifest_ref"]): item for item in repository.list_dataset_manifests()}
    old_manifest_after = manifests[str(old_manifest["manifest_ref"])]
    assert old_manifest_after["status"] == "superseded"
    result = Warehouse(repository).check(
        (
            WarehouseCheck(
                request_id="req-parquet:SOLUSDT:quote",
                market=Market.CRYPTO,
                symbol_id="SOLUSDT",
                universe_ref=None,
                data_type="quote_snapshot",
                granularity="realtime",
                fields=("price", "timestamp", "symbol_id"),
                date_range_start=datetime(2026, 6, 9, tzinfo=UTC),
                date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
                freshness_policy="realtime",
                timezone="UTC",
                calendar="CRYPTO_24_7",
            ),
        ),
        CoverageRequirement(
            request_ids=("req-parquet:SOLUSDT:quote",),
            expected_outputs=("quote_snapshot",),
            required_fields_by_request={"req-parquet:SOLUSDT:quote": ("price", "timestamp", "symbol_id")},
        ),
    )

    assert len(result.rows) == 1
    assert result.rows[0]["price"] == 110.0
    assert not any(gap.reason == GapReason.DATA_INTEGRITY_FAILED for gap in result.gaps)


def test_columnar_intraday_keeps_same_day_hour_rows_across_upserts(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )

    repository.upsert_normalized_documents((_crypto_intraday_row(hour=0, close=100.0),))
    repository.upsert_normalized_documents((_crypto_intraday_row(hour=1, close=101.0),))

    active_manifests = tuple(
        manifest
        for manifest in repository.list_dataset_manifests()
        if manifest["dataset"] == "intraday_bar" and manifest["status"] == "active"
    )
    rows = repository.query_normalized(
        dataset="intraday_bar",
        market="CRYPTO",
        symbol_id="BTCUSDT",
        universe_ref="binance_spot_all_symbols",
        date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
        require_integrity_metadata=True,
        include_row=True,
        fields=("close", "volume"),
    )
    coverage = repository.aggregate_normalized_coverage(
        dataset="intraday_bar",
        market="CRYPTO",
        symbol_id="BTCUSDT",
        universe_ref="binance_spot_all_symbols",
        date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
        require_integrity_metadata=True,
    )

    assert len(active_manifests) == 2
    assert sorted(str(manifest["period_start_min"]) for manifest in active_manifests) == [
        "2026-06-10T00:00:00+00:00",
        "2026-06-10T01:00:00+00:00",
    ]
    assert len(rows) == 2
    assert {record.period_start for record in rows} == {
        "2026-06-10T00:00:00+00:00",
        "2026-06-10T01:00:00+00:00",
    }
    assert {record.row["close"] for record in rows} == {100.0, 101.0}
    assert coverage is not None
    assert coverage.record_count == 2


def test_columnar_daily_small_window_does_not_supersede_larger_history(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )

    repository.upsert_normalized_documents(tuple(_daily_row_for_day(index) for index in range(10)))
    large_manifest = repository.list_dataset_manifests()[0]

    repository.upsert_normalized_documents((_daily_row_for_day(0), _daily_row_for_day(1)))

    manifests = {str(item["manifest_ref"]): item for item in repository.list_dataset_manifests()}
    active_manifests = [item for item in manifests.values() if item["dataset"] == "daily_bar" and item["status"] == "active"]
    rows = repository.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id="600519.SH",
        universe_ref=None,
        date_range_start=datetime(2026, 1, 1, tzinfo=UTC),
        date_range_end=datetime(2026, 1, 10, tzinfo=UTC),
        require_integrity_metadata=True,
        include_row=True,
    )

    assert manifests[str(large_manifest["manifest_ref"])]["status"] == "active"
    assert len(active_manifests) == 2
    assert len(rows) == 10
    assert {record.period_start for record in rows} == {f"2026-01-{day:02d}" for day in range(1, 11)}


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
    assert query_call["period_start_min"] == {"$lte": "2026-06-04T23:59:59.999999"}
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


def test_metric_columnar_manifests_with_different_fields_do_not_supersede_each_other(tmp_path) -> None:
    collections = _collections()
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents(
        (
            _crypto_derivative_row(
                dataset_ref="dataset:crypto_derivative_metric:CRYPTO:SOLUSDT:funding:2026-06-10",
                endpoint_id="futures_funding_rate",
                fields={"funding_rate": 0.0001, "funding_rate_unit": "ratio"},
            ),
        )
    )
    repository.upsert_normalized_documents(
        (
            _crypto_derivative_row(
                dataset_ref="dataset:crypto_derivative_metric:CRYPTO:SOLUSDT:long-short:2026-06-10",
                endpoint_id="futures_long_short_ratio",
                fields={"long_short_ratio": 1.25},
            ),
        )
    )

    active_manifests = [
        item
        for item in collections["dataset_manifests"].values()
        if item["dataset"] == "crypto_derivative_metric" and item["status"] == "active"
    ]
    assert len(active_manifests) == 2

    warehouse = Warehouse(repository)
    funding_result = warehouse.check(
        (
            WarehouseCheck(
                request_id="req-sol-funding",
                market=Market.CRYPTO,
                symbol_id="SOLUSDT",
                universe_ref=None,
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("funding_rate", "funding_rate_unit"),
                date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
                date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
                freshness_policy="trading_day",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 10, 0, 30, tzinfo=UTC),
            ),
        ),
        None,
    )
    long_short_result = warehouse.check(
        (
            WarehouseCheck(
                request_id="req-sol-long-short",
                market=Market.CRYPTO,
                symbol_id="SOLUSDT",
                universe_ref=None,
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("long_short_ratio",),
                date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
                date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
                freshness_policy="trading_day",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 10, 0, 30, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert funding_result.satisfied is True
    assert len(funding_result.rows) == 1
    assert funding_result.rows[0]["funding_rate"] == 0.0001
    assert long_short_result.satisfied is True
    assert len(long_short_result.rows) == 1
    assert long_short_result.rows[0]["long_short_ratio"] == 1.25


def test_metric_columnar_manifests_keep_cvd_when_taker_fields_overlap(tmp_path) -> None:
    collections = _collections()
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents(
        (
            _crypto_derivative_row(
                dataset_ref="dataset:crypto_derivative_metric:CRYPTO:SOLUSDT:cvd:2026-06-10",
                endpoint_id="futures_cvd_history",
                fields={
                    "cvd": 100.0,
                    "taker_buy_volume": 60.0,
                    "taker_sell_volume": 40.0,
                    "taker_volume_unit": "USD",
                },
            ),
        )
    )
    repository.upsert_normalized_documents(
        (
            _crypto_derivative_row(
                dataset_ref="dataset:crypto_derivative_metric:CRYPTO:SOLUSDT:taker:2026-06-10",
                endpoint_id="futures_taker_buy_sell",
                fields={
                    "taker_buy_volume": 70.0,
                    "taker_sell_volume": 30.0,
                    "taker_buy_sell_ratio": 2.3333333333,
                    "taker_volume_unit": "USD",
                },
            ),
        )
    )

    active_manifests = [
        item
        for item in collections["dataset_manifests"].values()
        if item["dataset"] == "crypto_derivative_metric" and item["status"] == "active"
    ]
    assert len(active_manifests) == 2

    cvd_result = Warehouse(repository).check(
        (
            WarehouseCheck(
                request_id="req-sol-cvd",
                market=Market.CRYPTO,
                symbol_id="SOLUSDT",
                universe_ref=None,
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit"),
                date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
                date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
                freshness_policy="trading_day",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 10, 0, 30, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert cvd_result.satisfied is True
    assert len(cvd_result.rows) == 1
    assert cvd_result.rows[0]["cvd"] == 100.0


def test_public_valuation_manifest_does_not_supersede_paid_manifest(tmp_path) -> None:
    collections = _collections()
    repository = DatasetRepository(
        collections=collections,
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    dataset_ref = "dataset:valuation_metric:CRYPTO:BNBUSDT:realtime:2026-06-10:2026-06-10"
    repository.upsert_normalized_documents(
        (
            _crypto_valuation_row(
                dataset_ref=dataset_ref,
                source_role="paid_data",
                provider_id="crypto_coinglass_derivatives",
                endpoint_id="spot_coins_markets",
                fields={
                    "price": 650.0,
                    "price_unit": "USD",
                    "market_cap": 95000000000.0,
                    "market_cap_unit": "USD",
                    "fdv": 96000000000.0,
                    "fdv_unit": "USD",
                    "total_supply": 147000000.0,
                    "supply_unit": "BNB",
                },
            ),
        )
    )
    paid_manifest_ref = repository.list_dataset_manifests()[0]["manifest_ref"]
    repository.upsert_normalized_documents(
        (
            _crypto_valuation_row(
                dataset_ref=dataset_ref,
                source_role="built_in_public",
                provider_id="crypto_coingecko_market",
                endpoint_id="coins_markets",
                fields={
                    "price": 640.0,
                    "price_unit": "USD",
                    "market_cap": 94000000000.0,
                    "market_cap_unit": "USD",
                },
            ),
        )
    )

    manifests = {str(item["manifest_ref"]): item for item in repository.list_dataset_manifests()}
    assert manifests[str(paid_manifest_ref)]["status"] == "active"

    rows = repository.query_normalized(
        dataset="valuation_metric",
        market="CRYPTO",
        symbol_id="BNBUSDT",
        universe_ref=None,
        date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
        date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
        require_integrity_metadata=True,
        include_row=True,
        fields=("price", "market_cap", "fdv", "total_supply"),
    )
    assert len(rows) == 1
    assert rows[0].source_roles == ("paid_data",)
    assert rows[0].row["fdv"] == 96000000000.0


def test_warehouse_prefers_required_paid_source_role_when_public_rows_also_exist(tmp_path) -> None:
    repository = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path / "normalized"),
    )
    repository.upsert_normalized_documents(
        (
            _crypto_valuation_row(
                dataset_ref="dataset:valuation_metric:CRYPTO:BNBUSDT:realtime:2026-06-10:2026-06-10",
                source_role="paid_data",
                provider_id="crypto_coinglass_derivatives",
                endpoint_id="spot_coins_markets",
                fields={
                    "price": 650.0,
                    "price_unit": "USD",
                    "market_cap": 95000000000.0,
                    "market_cap_unit": "USD",
                    "fdv": 96000000000.0,
                    "fdv_unit": "USD",
                },
            ),
            _crypto_valuation_row(
                dataset_ref="dataset:valuation_metric:CRYPTO:BNBUSDT:realtime:2026-06-10T00:01:00:2026-06-10T00:01:00",
                source_role="built_in_public",
                provider_id="crypto_coingecko_market",
                endpoint_id="coins_markets",
                fields={
                    "price": 640.0,
                    "price_unit": "USD",
                    "market_cap": 94000000000.0,
                    "market_cap_unit": "USD",
                },
            ),
        )
    )

    result = Warehouse(repository).check(
        (
            WarehouseCheck(
                request_id="req-bnb-paid-valuation",
                market=Market.CRYPTO,
                symbol_id="BNBUSDT",
                universe_ref=None,
                data_type="valuation_metric",
                granularity="realtime",
                fields=("price", "market_cap", "fdv"),
                date_range_start=datetime(2026, 6, 10, tzinfo=UTC),
                date_range_end=datetime(2026, 6, 10, tzinfo=UTC),
                freshness_policy="ttl_1h",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 10, 0, 30, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert result.satisfied is True
    assert len(result.rows) == 1
    assert result.rows[0]["price"] == 650.0
    assert result.rows[0]["fdv"] == 96000000000.0


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
