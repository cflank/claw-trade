from __future__ import annotations

from datetime import UTC, date, datetime

from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse


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
