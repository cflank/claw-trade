from __future__ import annotations

from datetime import UTC, date, datetime

from claw_trade.data_gateway.models import Market, WarehouseCheck
from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse


def _base_record() -> dict[str, object]:
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "600519.SH",
        "universe_ref": None,
        "granularity": "daily",
        "period_start": date(2026, 5, 1),
        "period_end": date(2026, 5, 31),
        "field_set": ("date", "open", "high", "low", "close", "volume"),
        "as_of": datetime(2026, 5, 31, 15, 0, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        "source_roles": ("official",),
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {"provider": "tushare", "endpoint": "daily"},
        "schema_id": "daily_bar.v1",
        "quality_flags": (),
        "row": {"date": "2026-05-31", "close": 1530.25},
    }


def _universe_record(symbol_id: str) -> dict[str, object]:
    record = _base_record()
    record["symbol_id"] = symbol_id
    record["universe_ref"] = "all_a_shares"
    row = record["row"]
    assert isinstance(row, dict)
    row["ticker"] = symbol_id
    return record


def _collections() -> dict[str, dict[str, object]]:
    return {name: {} for name in DatasetRepository.collection_names()}


def _warehouse_check() -> WarehouseCheck:
    return WarehouseCheck(
        request_id="req-coverage",
        market=Market.CN_A,
        symbol_id="600519.SH",
        universe_ref=None,
        data_type="daily_bar",
        granularity="daily",
        fields=("date", "close"),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        freshness_policy="trading_day",
        timezone="Asia/Shanghai",
        calendar="CN_A_SSE_SZSE",
        source_role_required="official",
        as_of=datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
    )


def _universe_warehouse_check() -> WarehouseCheck:
    return WarehouseCheck(
        request_id="req-universe-coverage",
        market=Market.CN_A,
        symbol_id=None,
        universe_ref="all_a_shares",
        data_type="daily_bar",
        granularity="daily",
        fields=("date", "close"),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        freshness_policy="trading_day",
        timezone="Asia/Shanghai",
        calendar="CN_A_SSE_SZSE",
        source_role_required="official",
        as_of=datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
    )


def _request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "600519.SH",
        "universe_ref": None,
        "granularity": "daily",
        "fields": ("date", "close"),
        "date_range_start": date(2026, 5, 1),
        "date_range_end": date(2026, 5, 31),
        "freshness_policy": "trading_day",
        "source_role_required": "official",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "as_of": datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return payload


def test_warehouse_ready_when_dataset_and_freshness_match() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(_request())

    assert result.status == "ready"
    assert result.gaps == ()
    assert result.rows
    assert result.dataset_refs


def test_warehouse_materialized_rows_preserve_scope_metadata_for_report_charts() -> None:
    record = _base_record()
    record["period_start"] = date(2026, 5, 31)
    record["period_end"] = date(2026, 5, 31)
    record["field_set"] = ("open", "high", "low", "close", "volume", "amount")
    record["row"] = {
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 1000.0,
        "amount": 11000.0,
    }
    check = _warehouse_check().model_copy(
        update={
            "fields": ("open", "high", "low", "close", "volume", "amount"),
            "date_range_start": date(2026, 5, 31),
            "date_range_end": date(2026, 5, 31),
        }
    )

    result = Warehouse(DatasetRepository(records=[record])).check((check,), None)

    assert result.status == "ready"
    assert result.rows[0]["date"] == date(2026, 5, 31)
    assert result.rows[0]["period_start"] == date(2026, 5, 31)
    assert result.rows[0]["dataset"] == "daily_bar"
    assert result.rows[0]["granularity"] == "daily"


def test_repository_writes_normalized_dataset_checksum() -> None:
    repo = DatasetRepository(records=[_base_record()])
    document = repo.get_normalized_document_for_maintenance("dataset:daily_bar:CN_A:600519.SH:daily:2026-05-01:2026-05-31")

    assert document is not None
    assert str(document["dataset_checksum"]).startswith("sha256:")
    assert document["dataset_checksum_algorithm"] == "sha256:canonical-json-v1"
    assert document["dataset_checksum_scope"] == "normalized-batch-v1"
    assert document["dataset_row_count"] == 1


def test_warehouse_rejects_record_with_invalid_dataset_checksum() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    repo.insert_normalized(_base_record())
    document = next(iter(collections["normalized_datasets"].values()))
    document["dataset_checksum"] = None

    result = Warehouse(repo).query(_request())

    assert result.status == "missing"
    assert result.rows == ()
    assert result.dataset_refs == ()
    assert any(gap.reason.value == "data_integrity_failed" for gap in result.gaps)


def test_warehouse_rejects_legacy_record_without_dataset_checksum_without_backfill() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    repo.insert_normalized(_base_record())
    document = next(iter(collections["normalized_datasets"].values()))
    document.pop("dataset_checksum", None)
    document.pop("dataset_checksum_algorithm", None)
    document.pop("dataset_checksum_scope", None)
    document.pop("dataset_row_count", None)

    result = Warehouse(repo).query(_request())

    assert result.status == "missing"
    assert result.rows == ()
    updated = next(iter(collections["normalized_datasets"].values()))
    assert "dataset_checksum" not in updated
    assert any(gap.reason.value == "data_integrity_failed" for gap in result.gaps)


def test_warehouse_rejects_record_with_missing_row_payload() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    repo.insert_normalized(_base_record())
    document = next(iter(collections["normalized_datasets"].values()))
    document.pop("row", None)

    result = Warehouse(repo).query(_request())

    assert result.status == "missing"
    assert result.rows == ()
    assert any(gap.reason.value == "data_integrity_failed" for gap in result.gaps)


def test_warehouse_coverage_check_does_not_materialize_rows() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).check_coverage((_warehouse_check(),), None)

    assert result.status == "ready"
    assert result.rows == ()
    assert result.dataset_refs
    coverage = result.freshness["coverage_by_request"][0]
    assert coverage["actual_start"] == "2026-05-01"
    assert coverage["actual_end"] == "2026-05-31"


def test_warehouse_coverage_check_queries_metadata_without_row_projection() -> None:
    record = _base_record()
    record["dataset_ref"] = "dataset:daily:600519"
    record["dataset_checksum"] = "sha256:batch"
    record["dataset_checksum_algorithm"] = "sha256:canonical-json-v1"
    record["dataset_checksum_scope"] = "normalized-batch-v1"
    record["dataset_row_count"] = 1
    normalized_collection = _NormalizedDatasetProjectionProbe(record)
    collections: dict[str, object] = _collections()
    collections["normalized_datasets"] = normalized_collection
    repo = DatasetRepository(collections=collections)  # type: ignore[arg-type]

    result = Warehouse(repo).check_coverage((_warehouse_check(),), None)

    assert result.status == "ready"
    assert result.rows == ()
    assert normalized_collection.projections == [{"row": 0}]


def test_warehouse_missing_request_records_empty_coverage_summary() -> None:
    result = Warehouse(DatasetRepository(records=[])).check((_warehouse_check(),), None)

    assert result.status == "missing"
    assert result.rows == ()
    assert result.dataset_refs == ()
    assert result.freshness["checked_requests"] == ["req-coverage"]
    assert result.freshness["coverage_by_request"] == (
        {
            "request_id": "req-coverage",
            "data_type": "daily_bar",
            "granularity": "daily",
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "universe_ref": None,
            "record_count": 0,
            "dataset_refs": (),
            "dataset_ref_count": 0,
            "actual_start": None,
            "actual_end": None,
            "expected_start": "2026-05-01",
            "expected_end": "2026-05-31",
            "missing_ranges": (),
        },
    )


def test_warehouse_query_pushes_date_range_into_repository_criteria() -> None:
    normalized_collection = _NormalizedDatasetCriteriaProbe()
    collections: dict[str, object] = _collections()
    collections["normalized_datasets"] = normalized_collection
    repo = DatasetRepository(collections=collections)  # type: ignore[arg-type]

    Warehouse(repo).check_coverage((_universe_warehouse_check(),), None)

    assert normalized_collection.criteria
    criteria = normalized_collection.criteria[0]
    assert criteria["universe_ref"] == "all_a_shares"
    assert criteria["period_start"] == {"$lte": "2026-05-31"}
    assert criteria["period_end"] == {"$gte": "2026-05-01"}
    assert criteria["dataset_checksum"] == {"$exists": True, "$ne": None}


def test_warehouse_universe_coverage_uses_metadata_aggregation_when_available() -> None:
    normalized_collection = _NormalizedDatasetAggregateProbe(
        actual_count=2,
        expected_count=2,
    )
    collections: dict[str, object] = _collections()
    collections["normalized_datasets"] = normalized_collection
    repo = DatasetRepository(collections=collections)  # type: ignore[arg-type]
    collections["provider_attempts"]["attempt:aggregate"] = {
        "attempt_ref": "attempt:aggregate",
        "dataset_refs": ("dataset:one",),
    }

    result = Warehouse(repo).check_coverage((_universe_warehouse_check(),), None)

    assert result.status == "ready"
    assert result.rows == ()
    assert result.dataset_refs == ("dataset:one", "dataset:two")
    assert result.attempt_refs == ("attempt:aggregate",)
    assert normalized_collection.aggregate_pipelines
    assert normalized_collection.find_limits == [50]
    coverage = result.freshness["coverage_by_request"][0]
    assert coverage["read_mode"] == "aggregate_metadata"
    assert coverage["record_count"] == 2


def test_warehouse_stream_coverage_preserves_wide_period_ranges() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    refs = repo.upsert_normalized_documents(
        (
            _universe_record("000001.SZ"),
            _universe_record("600519.SH"),
        )
    )
    collections["provider_attempts"]["attempt:wide-range"] = {
        "attempt_ref": "attempt:wide-range",
        "dataset_refs": refs,
    }

    result = Warehouse(repo).check_coverage((_universe_warehouse_check(),), None)

    assert result.status == "ready"
    assert result.rows == ()
    assert result.attempt_refs == ("attempt:wide-range",)
    coverage = result.freshness["coverage_by_request"][0]
    assert coverage["actual_start"] == "2026-05-01"
    assert coverage["actual_end"] == "2026-05-31"
    assert coverage["missing_ranges"] == ()


def test_warehouse_aggregation_rejects_universe_batch_with_deleted_records() -> None:
    normalized_collection = _NormalizedDatasetAggregateProbe(
        actual_count=1,
        expected_count=2,
    )
    collections: dict[str, object] = _collections()
    collections["normalized_datasets"] = normalized_collection
    repo = DatasetRepository(collections=collections)  # type: ignore[arg-type]

    result = Warehouse(repo).check_coverage((_universe_warehouse_check(),), None)

    assert result.status == "missing"
    assert result.rows == ()
    assert result.dataset_refs == ()
    assert any(gap.reason.value == "data_integrity_failed" for gap in result.gaps)
    assert any("expected=2, actual=1" in gap.human_readable for gap in result.gaps)
    coverage = result.freshness["coverage_by_request"][0]
    assert coverage["integrity_mismatch_ranges"] == (
        {"start": "2026-05-01", "end": "2026-05-31"},
    )


def test_warehouse_coverage_check_rejects_universe_batch_with_deleted_records() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    refs = repo.upsert_normalized_documents(
        (
            _universe_record("000001.SZ"),
            _universe_record("600519.SH"),
        )
    )
    collections["normalized_datasets"].pop(refs[0])

    result = Warehouse(repo).check_coverage((_universe_warehouse_check(),), None)

    assert result.status == "missing"
    assert result.rows == ()
    assert any(gap.reason.value == "data_integrity_failed" for gap in result.gaps)
    assert any("expected=2, actual=1" in gap.human_readable for gap in result.gaps)


def test_warehouse_collapses_universe_legacy_integrity_gaps_to_batch_gap() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    repo.upsert_normalized_documents(
        (
            _universe_record("000001.SZ"),
            _universe_record("600519.SH"),
        )
    )
    for document in collections["normalized_datasets"].values():
        document.pop("dataset_checksum", None)
        document.pop("dataset_checksum_algorithm", None)
        document.pop("dataset_checksum_scope", None)
        document.pop("dataset_row_count", None)

    result = Warehouse(repo).check_coverage((_universe_warehouse_check(),), None)

    integrity_gaps = tuple(gap for gap in result.gaps if gap.reason.value == "data_integrity_failed")
    assert len(integrity_gaps) == 1
    assert "invalid_records=2" in integrity_gaps[0].human_readable


def test_warehouse_universe_check_ignores_legacy_invalid_records_after_valid_backfill() -> None:
    collections = _collections()
    repo = DatasetRepository(collections=collections)
    repo.upsert_normalized_documents(
        (
            _universe_record("000001.SZ"),
            _universe_record("600519.SH"),
        )
    )
    legacy = _universe_record("legacy-wide-range")
    legacy["dataset_ref"] = "dataset:daily_bar:CN_A:legacy-wide-range:daily:2025-01-01:2026-12-31"
    legacy["period_start"] = date(2025, 1, 1)
    legacy["period_end"] = date(2026, 12, 31)
    repo.insert_normalized(legacy)
    legacy_document = collections["normalized_datasets"][legacy["dataset_ref"]]
    legacy_document.pop("dataset_checksum", None)
    legacy_document.pop("dataset_checksum_algorithm", None)
    legacy_document.pop("dataset_checksum_scope", None)
    legacy_document.pop("dataset_row_count", None)

    result = Warehouse(repo).check((_universe_warehouse_check(),), None)

    assert result.status == "ready"
    assert result.gaps == ()
    assert len(result.dataset_refs) == 2
    assert legacy["dataset_ref"] not in result.dataset_refs


def test_warehouse_ready_result_includes_import_attempt_lineage() -> None:
    record = _base_record()
    record["dataset_ref"] = "dataset:daily:600519"
    repo = DatasetRepository(records=[record])
    repo.insert_provider_attempt(
        {
            "attempt_ref": "attempt:local-import:1",
            "provider": "local_a_share_prepackaged",
            "endpoint": "a_share_prepackaged_selection_import",
            "status": "local_seed_imported",
            "remote_attempted": False,
            "remote_success": False,
            "dataset_refs": ("dataset:daily:600519",),
            "raw_refs": ("raw:local-import:1",),
        }
    )

    result = Warehouse(repo).query(_request())

    assert result.status == "ready"
    assert result.dataset_refs == ("dataset:daily:600519",)
    assert result.attempt_refs == ("attempt:local-import:1",)


def test_repository_filters_provider_attempts_by_dataset_refs() -> None:
    provider_attempts = _ProviderAttemptCriteriaProbe()
    collections: dict[str, object] = _collections()
    collections["provider_attempts"] = provider_attempts
    repo = DatasetRepository(collections=collections)  # type: ignore[arg-type]

    refs = repo.find_provider_attempt_refs_by_dataset_ref(("dataset:one", "dataset:two"))

    assert provider_attempts.criteria == [{"dataset_refs": {"$in": ("dataset:one", "dataset:two")}}]
    assert refs == {"dataset:one": ("attempt:one",)}


def test_repository_finds_company_names_by_symbol_ids_without_integrity_requirement() -> None:
    collections = _collections()
    normalized = collections["normalized_datasets"]
    normalized["identity:old"] = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "600519.SH",
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2025-01-01",
        "period_end": "2025-01-01",
        "row": {"company_name": "旧名称"},
    }
    normalized["identity:new"] = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "600519.SH",
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2026-01-01",
        "period_end": "2026-01-01",
        "row": {"company_name": "贵州茅台"},
    }
    normalized["identity:other"] = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "000001.SZ",
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2026-01-01",
        "period_end": "2026-01-01",
        "row": {"company_name": "平安银行"},
    }
    repo = DatasetRepository(collections=collections)

    names = repo.find_company_names_by_symbol_ids(
        dataset="daily_bar",
        market="CN_A",
        symbol_ids=("600519.SH", "000001.SZ", "999999.SH"),
    )

    assert names == {"600519.SH": "贵州茅台", "000001.SZ": "平安银行"}


def test_repository_finds_company_names_from_quote_name_field() -> None:
    collections = _collections()
    normalized = collections["normalized_datasets"]
    normalized["quote:name"] = {
        "dataset": "quote_snapshot",
        "market": "CN_A",
        "symbol_id": "688017.SH",
        "universe_ref": None,
        "granularity": "realtime",
        "period_start": "2026-06-06",
        "period_end": "2026-06-06",
        "row": {"name": "绿的谐波"},
    }
    repo = DatasetRepository(collections=collections)

    names = repo.find_company_names_by_symbol_ids(
        dataset="quote_snapshot",
        market="CN_A",
        symbol_ids=("688017.SH",),
    )

    assert names == {"688017.SH": "绿的谐波"}


def test_repository_finds_company_names_from_columnar_quote_snapshot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    record = _base_record()
    record.update(
        {
            "dataset": "quote_snapshot",
            "symbol_id": "688017.SH",
            "granularity": "realtime",
            "period_start": date(2026, 6, 6),
            "period_end": date(2026, 6, 6),
            "field_set": ("symbol_id", "name", "company_name"),
            "row": {"symbol_id": "688017.SH", "name": "绿的谐波", "company_name": "绿的谐波"},
        }
    )
    repo = DatasetRepository(
        collections=_collections(),
        normalized_columnar=NormalizedColumnarWarehouse(tmp_path),
        allow_normalized_mongo_read=False,
    )
    repo.insert_normalized(record)

    names = repo.find_company_names_by_symbol_ids(
        dataset="quote_snapshot",
        market="CN_A",
        symbol_ids=("688017.SH",),
    )

    assert names == {"688017.SH": "绿的谐波"}


class _NormalizedDatasetProjectionProbe:
    def __init__(self, record: dict[str, object]) -> None:
        self._record = dict(record)
        self.projections: list[dict[str, int] | None] = []

    def find(self, criteria: dict[str, object], projection: dict[str, int] | None = None):
        del criteria
        self.projections.append(projection)
        document = dict(self._record)
        if projection == {"row": 0}:
            document.pop("row", None)
        return (document,)

    def create_index(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class _NormalizedDatasetCriteriaProbe:
    def __init__(self) -> None:
        self.criteria: list[dict[str, object]] = []

    def find(self, criteria: dict[str, object], projection: dict[str, int] | None = None):
        del projection
        self.criteria.append(criteria)
        return ()

    def create_index(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class _LimitedCursor:
    def __init__(self, rows: tuple[dict[str, object], ...], owner: "_NormalizedDatasetAggregateProbe") -> None:
        self._rows = rows
        self._owner = owner

    def limit(self, limit: int) -> "_LimitedCursor":
        self._owner.find_limits.append(limit)
        return _LimitedCursor(self._rows[:limit], self._owner)

    def __iter__(self):
        return iter(self._rows)


class _NormalizedDatasetAggregateProbe:
    def __init__(self, *, actual_count: int, expected_count: int) -> None:
        self.actual_count = actual_count
        self.expected_count = expected_count
        self.aggregate_pipelines: list[list[dict[str, object]]] = []
        self.find_limits: list[int] = []

    def aggregate(self, pipeline: list[dict[str, object]]):
        assert isinstance(pipeline, list)
        self.aggregate_pipelines.append(pipeline)
        group = pipeline[-1].get("$group", {})
        assert isinstance(group, dict)
        if group.get("_id") == "$dataset_checksum":
            return (
                {
                    "_id": "sha256:batch",
                    "actual_count": self.actual_count,
                    "expected_min": self.expected_count,
                    "expected_max": self.expected_count,
                    "min_start": "2026-05-01",
                    "max_end": "2026-05-31",
                },
            )
        return (
            {
                "_id": None,
                "record_count": self.actual_count,
                "ranges": ({"start": "2026-05-01", "end": "2026-05-31"},),
                "starts": ("2026-05-01",),
                "ends": ("2026-05-31",),
                "field_sets": (("date", "close"),),
                "source_role_sets": (("official",),),
                "freshest_as_of": datetime(2026, 5, 31, 15, 0, tzinfo=UTC),
                "freshest_until": datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            },
        )

    def find(self, criteria: dict[str, object], projection: dict[str, int] | None = None):
        del criteria, projection
        rows = (
            {"dataset_ref": "dataset:one"},
            {"dataset_ref": "dataset:two"},
        )
        return _LimitedCursor(rows, self)

    def create_index(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class _ProviderAttemptCriteriaProbe:
    def __init__(self) -> None:
        self.criteria: list[dict[str, object]] = []

    def find(self, criteria: dict[str, object], projection: dict[str, int] | None = None):
        del projection
        self.criteria.append(criteria)
        return (
            {"attempt_ref": "attempt:one", "dataset_refs": ("dataset:one",)},
        )

    def create_index(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def test_warehouse_rejects_granularity_mismatch() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(_request(granularity="intraday"))

    assert result.status == "partial"
    assert any(gap.reason.value == "granularity_mismatch" for gap in result.gaps)


def test_warehouse_rejects_missing_field() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(_request(fields=("date", "close", "amount")))

    assert result.status == "partial"
    assert any(gap.reason.value == "field_missing" for gap in result.gaps)


def test_warehouse_rejects_date_range_not_covered() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(_request(date_range_start=date(2026, 4, 1)))

    assert result.status == "partial"
    assert any(gap.reason.value == "date_range_missing" for gap in result.gaps)


def test_warehouse_rejects_stale_data_for_ttl_policy() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(
        _request(
            freshness_policy="ttl_30m",
            as_of=datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
        )
    )

    assert result.status == "partial"
    assert any(gap.reason.value == "warehouse_stale" for gap in result.gaps)


def test_warehouse_respects_official_source_role_requirement() -> None:
    record = _base_record()
    record["source_roles"] = ("discovery",)
    repo = DatasetRepository(records=[record])
    result = Warehouse(repo).query(_request(source_role_required="official"))

    assert result.status == "partial"
    assert any(gap.reason.value == "warehouse_missing" for gap in result.gaps)


def test_cn_a_daily_bar_does_not_satisfy_news_dataset_request() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(_request(dataset="company_news", fields=("title", "published_at")))

    assert result.status == "missing"
    assert any(gap.reason.value == "warehouse_missing" for gap in result.gaps)


def test_warehouse_missing_request_market_fields_returns_gap_without_default_timezone_or_calendar() -> None:
    repo = DatasetRepository(records=[_base_record()])
    result = Warehouse(repo).query(_request(timezone=None, calendar=None))

    assert result.status == "missing"
    assert any(gap.reason.value == "invalid_request" for gap in result.gaps)
    assert all(gap.timezone != "UTC" for gap in result.gaps)
    assert all(gap.calendar != "GENERIC" for gap in result.gaps)


def test_warehouse_uses_matching_granularity_rows_when_legacy_rows_exist() -> None:
    legacy = _base_record()
    legacy["granularity"] = "1d"
    legacy["period_start"] = date(2026, 1, 1)
    legacy["period_end"] = date(2026, 4, 30)
    current = _base_record()
    current["granularity"] = "daily"
    current["period_start"] = date(2026, 5, 1)
    current["period_end"] = date(2026, 5, 1)
    current["dataset_ref"] = "dataset:daily-current"
    repo = DatasetRepository(records=[legacy, current])

    result = Warehouse(repo).query(
        _request(
            granularity="daily",
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 1),
            freshness_policy="immutable_seed",
        )
    )

    assert result.status == "ready"
    assert result.dataset_refs == ("dataset:daily-current",)
    assert not any(gap.reason.value == "granularity_mismatch" for gap in result.gaps)
