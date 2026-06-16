from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zipfile import ZipFile

from claw_trade.data_gateway.crypto_prepackaged_importer import (
    BINANCE_PUBLIC_DATA_ENDPOINT_ID,
    CRYPTO_BINANCE_BAR_SCHEMA_ID,
    CRYPTO_CALENDAR,
    CRYPTO_EXCHANGE,
    CRYPTO_TIMEZONE,
    LOCAL_CRYPTO_PROVIDER_ID,
    build_crypto_bar_record,
    date_range_missing_count,
    import_crypto_prepackaged_to_repository,
    local_warehouse_empty_gap,
    parse_binance_kline_zip,
)
from claw_trade.data_gateway.models import DataRequest, GapReason, Market, WarehouseCheck
from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse


def _bar_record() -> dict[str, object]:
    return build_crypto_bar_record(
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        open_time=datetime(2026, 6, 1, tzinfo=UTC),
        close_time=datetime(2026, 6, 1, 23, 59, 59, 999000, tzinfo=UTC),
        open_price=Decimal("150.10"),
        high_price=Decimal("155.20"),
        low_price=Decimal("149.00"),
        close_price=Decimal("153.40"),
        volume=Decimal("1234.5"),
        quote_asset_volume=Decimal("188888.8"),
        number_of_trades=12345,
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:sample",
        as_of=datetime(2026, 6, 2, tzinfo=UTC),
    )


def _crypto_check(**overrides: object) -> WarehouseCheck:
    payload = {
        "request_id": "req-sol",
        "market": Market.CRYPTO,
        "symbol_id": "SOLUSDT",
        "universe_ref": None,
        "data_type": "daily_bar",
        "granularity": "daily",
        "fields": ("date", "open", "high", "low", "close", "volume", "amount"),
        "date_range_start": date(2026, 6, 1),
        "date_range_end": date(2026, 6, 1),
        "freshness_policy": "warehouse_only",
        "timezone": CRYPTO_TIMEZONE,
        "calendar": CRYPTO_CALENDAR,
        "as_of": datetime(2026, 6, 2, tzinfo=UTC),
    }
    payload.update(overrides)
    return WarehouseCheck(**payload)


def _crypto_request(**overrides: object) -> DataRequest:
    payload = {
        "request_id": "req-sol",
        "market": Market.CRYPTO,
        "symbol_id": "SOLUSDT",
        "universe_ref": None,
        "data_type": "daily_bar",
        "granularity": "daily",
        "fields": ("date", "open", "high", "low", "close", "volume", "amount"),
        "date_range_start": date(2026, 6, 1),
        "date_range_end": date(2026, 6, 1),
        "freshness_policy": "warehouse_only",
        "timezone": CRYPTO_TIMEZONE,
        "calendar": CRYPTO_CALENDAR,
        "exchange": CRYPTO_EXCHANGE,
        "currency": "USDT",
        "base_asset": "SOL",
        "quote_asset": "USDT",
        "consumer": "report",
        "consumer_id": "market_analyst",
        "as_of": datetime(2026, 6, 2, tzinfo=UTC),
    }
    payload.update(overrides)
    return DataRequest(**payload)


def test_build_crypto_daily_bar_record_matches_phase1_schema() -> None:
    record = _bar_record()

    assert record["dataset"] == "daily_bar"
    assert record["dataset_ref"] == "dataset:daily_bar:CRYPTO:spot:SOLUSDT:daily:2026-06-01:2026-06-01"
    assert record["market"] == "CRYPTO"
    assert record["symbol_id"] == "SOLUSDT"
    assert record["universe_ref"] == "binance_spot_all_symbols"
    assert record["granularity"] == "daily"
    assert record["period_start"] == date(2026, 6, 1)
    assert record["period_end"] == date(2026, 6, 1)
    assert record["exchange"] == CRYPTO_EXCHANGE
    assert record["currency"] == "USDT"
    assert record["timezone"] == CRYPTO_TIMEZONE
    assert record["calendar"] == CRYPTO_CALENDAR
    assert record["base_asset"] == "SOL"
    assert record["quote_asset"] == "USDT"
    assert record["source_market_segment"] == "spot"
    assert record["schema_id"] == CRYPTO_BINANCE_BAR_SCHEMA_ID
    assert record["source_roles"] == ("local_seed", "built_in_public")
    assert record["provider_lineage"] == {
        "provider_id": LOCAL_CRYPTO_PROVIDER_ID,
        "endpoint_id": BINANCE_PUBLIC_DATA_ENDPOINT_ID,
        "raw_refs": ("raw:CRYPTO:local_crypto_prepackaged:sample",),
        "remote_attempted": False,
        "market_segment": "spot",
        "source": "binance_public_data",
    }
    assert record["row"]["amount"] == "188888.8"  # type: ignore[index]


def test_crypto_daily_bar_record_roundtrips_through_warehouse() -> None:
    repo = DatasetRepository(records=[_bar_record()])
    result = Warehouse(repo).check((_crypto_check(),), None)

    assert result.status == "ready"
    assert result.dataset_refs == ("dataset:daily_bar:CRYPTO:spot:SOLUSDT:daily:2026-06-01:2026-06-01",)
    coverage = result.freshness["coverage_by_request"][0]
    assert coverage["actual_start"] == "2026-06-01"
    assert coverage["actual_end"] == "2026-06-01"
    assert date_range_missing_count(coverage) == 0


def test_local_warehouse_empty_gap_preserves_specific_code_in_human_text() -> None:
    gap = local_warehouse_empty_gap(_crypto_request(date_range_end=date(2026, 6, 7)))

    assert gap.reason == GapReason.WAREHOUSE_MISSING
    assert gap.market == Market.CRYPTO
    assert gap.symbol_id == "SOLUSDT"
    assert gap.base_asset == "SOL"
    assert gap.quote_asset == "USDT"
    assert gap.provider_ids_tried == (LOCAL_CRYPTO_PROVIDER_ID,)
    assert "local_warehouse_empty" in gap.human_readable


def test_date_range_missing_count_counts_explicit_ranges_and_empty_coverage() -> None:
    assert (
        date_range_missing_count(
            {
                "record_count": 1,
                "expected_start": "2026-06-01",
                "expected_end": "2026-06-07",
                "actual_start": "2026-06-04",
                "actual_end": "2026-06-07",
                "missing_ranges": (("2026-06-01", "2026-06-03"),),
            }
        )
        == 3
    )
    assert (
        date_range_missing_count(
            {
                "record_count": 0,
                "expected_start": "2026-06-01",
                "expected_end": "2026-06-07",
                "actual_start": None,
                "actual_end": None,
                "missing_ranges": (),
            }
        )
        == 7
    )


def test_build_crypto_intraday_bar_record_uses_intraday_dataset() -> None:
    record = build_crypto_bar_record(
        symbol_id="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1h",
        open_time=datetime(2026, 6, 1, 1, 0, tzinfo=UTC),
        close_time=datetime(2026, 6, 1, 1, 59, 59, 999000, tzinfo=UTC),
        open_price="67000",
        high_price="67100",
        low_price="66900",
        close_price="67050",
        volume="10",
        quote_asset_volume="670500",
        number_of_trades=500,
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:btc",
        as_of=datetime(2026, 6, 1, 2, 0, tzinfo=UTC),
    )

    assert record["dataset"] == "intraday_bar"
    assert record["granularity"] == "1h"
    assert record["period_start"] == datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
    assert record["period_end"] == datetime(2026, 6, 1, 1, 59, 59, 999000, tzinfo=UTC)
    assert "date" not in record["row"]  # type: ignore[operator]


def test_parse_binance_spot_kline_zip_with_millisecond_timestamps(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "SOLUSDT-1d-2024-06.zip",
        "1717200000000,150.10,155.20,149.00,153.40,1234.5,1717286399999,188888.8,12345,500.0,76500.0,0\n",
    )

    records = parse_binance_kline_zip(
        zip_path,
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        market_segment="spot",
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:sol",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
    )

    assert len(records) == 1
    assert records[0]["period_start"] == date(2024, 6, 1)
    assert records[0]["row"]["close"] == "153.40"  # type: ignore[index]
    assert records[0]["row"]["amount"] == "188888.8"  # type: ignore[index]


def test_parse_binance_spot_kline_zip_with_microsecond_timestamps(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "BTCUSDT-1h-2025-01.zip",
        "1735689600000000,93000,94000,92000,93500,10,1735693199999999,935000,100,5,467500,0\n",
    )

    records = parse_binance_kline_zip(
        zip_path,
        symbol_id="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1h",
        market_segment="spot",
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:btc",
        as_of=datetime(2025, 1, 1, 2, tzinfo=UTC),
    )

    assert records[0]["period_start"] == datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    assert records[0]["period_end"] == datetime(2025, 1, 1, 0, 59, 59, 999999, tzinfo=UTC)
    assert records[0]["dataset"] == "intraday_bar"


def test_parse_binance_usdm_futures_kline_zip(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "ETHUSDT-1h-2024-06.zip",
        "1717200000000,3500,3600,3400,3550,100,1717203599999,355000,2000,50,177500,0\n",
    )

    records = parse_binance_kline_zip(
        zip_path,
        symbol_id="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        interval="1h",
        market_segment="usdm_futures",
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:eth",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
    )

    assert records[0]["universe_ref"] == "binance_usdm_all_symbols"
    assert records[0]["source_market_segment"] == "usdm_futures"
    assert str(records[0]["dataset_ref"]).startswith("dataset:intraday_bar:CRYPTO:usdm_futures:ETHUSDT:1h:")
    assert records[0]["provider_lineage"]["market_segment"] == "usdm_futures"  # type: ignore[index]
    assert records[0]["row"]["quote_asset_volume"] == "355000"  # type: ignore[index]


def test_parse_binance_usdm_futures_kline_zip_with_header(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "BTCUSDT-1d-2025-01.zip",
        "\n".join(
            (
                "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore",
                "1735689600000,93548.80,95161.20,92796.50,94580.90,91236.660,1735775999999,8572615379.77510,1804360,45712.481,4296719477.46090,0",
            )
        )
        + "\n",
    )

    records = parse_binance_kline_zip(
        zip_path,
        symbol_id="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1d",
        market_segment="usdm_futures",
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:btc-futures",
        as_of=datetime(2025, 1, 2, tzinfo=UTC),
    )

    assert len(records) == 1
    assert records[0]["period_start"] == date(2025, 1, 1)
    assert records[0]["row"]["close"] == "94580.90"  # type: ignore[index]
    assert records[0]["row"]["amount"] == "8572615379.77510"  # type: ignore[index]


def test_parse_binance_kline_zip_fails_closed_on_schema_mismatch(tmp_path) -> None:
    zip_path = _kline_zip(tmp_path, "bad.zip", "1,2,3\n")

    try:
        _parse_sample_zip(zip_path)
    except ValueError as exc:
        assert "schema mismatch" in str(exc)
    else:
        raise AssertionError("expected schema mismatch to fail closed")


def test_parse_binance_kline_zip_fails_closed_on_unknown_timestamp_unit(tmp_path) -> None:
    zip_path = _kline_zip(tmp_path, "bad-timestamp.zip", "123,1,2,3,4,5,456,7,8,9,10,0\n")

    try:
        _parse_sample_zip(zip_path)
    except ValueError as exc:
        assert "unsupported timestamp unit" in str(exc)
    else:
        raise AssertionError("expected unknown timestamp unit to fail closed")


def test_parse_binance_kline_zip_rejects_coinm_futures(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "BTCUSD_PERP-1h-2024-06.zip",
        "1717200000000,1,2,3,4,5,1717203599999,7,8,9,10,0\n",
    )

    try:
        parse_binance_kline_zip(
            zip_path,
            symbol_id="BTCUSD_PERP",
            base_asset="BTC",
            quote_asset="USD",
            interval="1h",
            market_segment="coinm_futures",
            raw_ref="raw:CRYPTO:local_crypto_prepackaged:coinm",
            as_of=datetime(2024, 6, 2, tzinfo=UTC),
        )
    except ValueError as exc:
        assert "coinm_futures is not supported" in str(exc)
    else:
        raise AssertionError("expected coinm futures to be rejected")


def test_parse_binance_kline_zip_fails_closed_on_empty_csv(tmp_path) -> None:
    zip_path = _kline_zip(tmp_path, "empty.zip", "")

    try:
        _parse_sample_zip(zip_path)
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("expected empty csv to fail closed")


def test_import_crypto_prepackaged_to_repository_writes_evidence_chain(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "SOLUSDT-1d-2024-06.zip",
        "1717200000000,150.10,155.20,149.00,153.40,1234.5,1717286399999,188888.8,12345,500.0,76500.0,0\n",
    )
    repo = DatasetRepository()

    result = import_crypto_prepackaged_to_repository(
        package_paths=(zip_path,),
        repository=repo,
        import_run_id="crypto-import-1",
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        market_segment="spot",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
    )

    assert result.row_count == 1
    assert result.file_count == 1
    assert result.dataset_refs == ("dataset:daily_bar:CRYPTO:spot:SOLUSDT:daily:2024-06-01:2024-06-01",)
    assert result.raw_refs
    assert result.attempt_refs
    assert result.manifest_ref

    raw = repo.get_raw_payload(result.raw_refs[0])
    assert raw is not None
    assert raw["storage_mode"] == "metadata_only"
    assert raw["provider"] == LOCAL_CRYPTO_PROVIDER_ID
    assert raw["endpoint"] == BINANCE_PUBLIC_DATA_ENDPOINT_ID

    attempt = repo.get_provider_attempt(result.attempt_refs[0])
    assert attempt is not None
    assert attempt["provider"] == LOCAL_CRYPTO_PROVIDER_ID
    assert attempt["endpoint"] == BINANCE_PUBLIC_DATA_ENDPOINT_ID
    assert attempt["status"] == "local_seed_imported"
    assert attempt["remote_attempted"] is False
    assert attempt["remote_success"] is False
    assert attempt["dataset_refs"] == result.dataset_refs

    manifest = repo.get_dataset_manifest(result.manifest_ref)
    assert manifest is not None
    assert manifest["job_type"] == "seed_import"
    assert manifest["market"] == "CRYPTO"
    assert manifest["dataset_scope"] == "daily_bar"
    assert manifest["source_summary"] == "local:crypto_prepackaged:binance_public_data"

    warehouse_result = Warehouse(repo).check(
        (
            _crypto_check(
                date_range_start=date(2024, 6, 1),
                date_range_end=date(2024, 6, 1),
                as_of=datetime(2024, 6, 2, tzinfo=UTC),
            ),
        ),
        None,
    )
    assert warehouse_result.status == "ready"
    assert warehouse_result.dataset_refs == result.dataset_refs


def test_import_crypto_prepackaged_compacts_large_mongo_lineage_but_preserves_attempt_lookup(tmp_path) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for offset in range(501):
        open_time = start + timedelta(hours=offset)
        close_time = open_time + timedelta(hours=1) - timedelta(microseconds=1)
        rows.append(
            ",".join(
                (
                    str(int(open_time.timestamp() * 1_000_000)),
                    "93000",
                    "94000",
                    "92000",
                    "93500",
                    "10",
                    str(int(close_time.timestamp() * 1_000_000)),
                    "935000",
                    "100",
                    "5",
                    "467500",
                    "0",
                )
            )
        )
    zip_path = _kline_zip(tmp_path, "BTCUSDT-1h-2025-01.zip", "\n".join(rows) + "\n")
    repo = DatasetRepository()

    result = import_crypto_prepackaged_to_repository(
        package_paths=(zip_path,),
        repository=repo,
        import_run_id="crypto-import-large-lineage",
        symbol_id="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1h",
        market_segment="spot",
        as_of=datetime(2025, 1, 22, tzinfo=UTC),
    )

    assert len(result.dataset_refs) == 501
    attempt = repo.get_provider_attempt(result.attempt_refs[0])
    assert attempt is not None
    assert len(attempt["dataset_refs"]) == 20
    assert attempt["dataset_ref_count"] == 501
    assert attempt["dataset_refs_truncated"] is True
    assert attempt["dataset_refs_sha256"].startswith("sha256:")
    assert attempt["dataset_ref_prefixes"] == ("dataset:intraday_bar:CRYPTO:spot:BTCUSDT:1h:",)

    manifest = repo.get_dataset_manifest(result.manifest_ref)
    assert manifest is not None
    assert len(manifest["dataset_refs"]) == 20
    assert manifest["dataset_ref_count"] == 501
    assert manifest["output_refs"]["dataset_ref_count"] == 501
    assert manifest["output_refs"]["dataset_refs_truncated"] is True

    warehouse_result = Warehouse(repo).check(
        (
                _crypto_check(
                    symbol_id="BTCUSDT",
                    data_type="intraday_bar",
                    granularity="1h",
                    fields=("open", "high", "low", "close", "volume", "amount"),
                    date_range_start=start,
                    date_range_end=start + timedelta(hours=500),
                    as_of=datetime(2025, 1, 22, tzinfo=UTC),
                ),
        ),
        None,
    )
    assert warehouse_result.status == "ready"
    assert warehouse_result.attempt_refs == result.attempt_refs


def test_import_crypto_prepackaged_distinguishes_parsed_and_unique_rows(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "SOLUSDT-1d-2024-06.zip",
        "\n".join(
            (
                "1717200000000,150.10,155.20,149.00,153.40,1234.5,1717286399999,188888.8,12345,500.0,76500.0,0",
                "1717200000000,150.10,155.20,149.00,153.40,1234.5,1717286399999,188888.8,12345,500.0,76500.0,0",
            )
        )
        + "\n",
    )
    repo = DatasetRepository()

    result = import_crypto_prepackaged_to_repository(
        package_paths=(zip_path,),
        repository=repo,
        import_run_id="crypto-import-duplicates",
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        market_segment="spot",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
    )

    assert result.parsed_row_count == 2
    assert result.row_count == 1
    assert len(result.dataset_refs) == 1
    manifest = repo.get_dataset_manifest(result.manifest_ref)
    assert manifest is not None
    assert manifest["audit"]["parsed_row_count"] == 2
    assert manifest["audit"]["row_count"] == 1
    assert manifest["audit"]["duplicate_row_count"] == 1


def test_import_crypto_prepackaged_replace_existing_requires_confirmation(tmp_path) -> None:
    zip_path = _kline_zip(
        tmp_path,
        "SOLUSDT-1d-2024-06.zip",
        "1717200000000,150.10,155.20,149.00,153.40,1234.5,1717286399999,188888.8,12345,500.0,76500.0,0\n",
    )

    try:
        import_crypto_prepackaged_to_repository(
            package_paths=(zip_path,),
            repository=DatasetRepository(),
            import_run_id="crypto-import-1",
            symbol_id="SOLUSDT",
            base_asset="SOL",
            quote_asset="USDT",
            interval="1d",
            market_segment="spot",
            as_of=datetime(2024, 6, 2, tzinfo=UTC),
            replace_existing=True,
        )
    except ValueError as exc:
        assert "confirm_replace_existing" in str(exc)
    else:
        raise AssertionError("expected replace-existing without confirmation to fail closed")


def test_import_crypto_prepackaged_replace_existing_keeps_other_market_segment(tmp_path) -> None:
    spot_zip = _kline_zip(
        tmp_path,
        "SOLUSDT-1d-2024-06.zip",
        "1717200000000,150.10,155.20,149.00,153.40,1234.5,1717286399999,188888.8,12345,500.0,76500.0,0\n",
    )
    repo = DatasetRepository()
    existing_spot = build_crypto_bar_record(
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        open_time=datetime(2024, 6, 1, tzinfo=UTC),
        close_time=datetime(2024, 6, 1, 23, 59, 59, 999000, tzinfo=UTC),
        open_price="100",
        high_price="101",
        low_price="99",
        close_price="100",
        volume="1",
        quote_asset_volume="100",
        number_of_trades=1,
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:old-spot",
        as_of=datetime(2024, 6, 1, tzinfo=UTC),
        market_segment="spot",
    )
    existing_usdm = build_crypto_bar_record(
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        open_time=datetime(2024, 6, 1, tzinfo=UTC),
        close_time=datetime(2024, 6, 1, 23, 59, 59, 999000, tzinfo=UTC),
        open_price="200",
        high_price="201",
        low_price="199",
        close_price="200",
        volume="1",
        quote_asset_volume="200",
        number_of_trades=1,
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:old-usdm",
        as_of=datetime(2024, 6, 1, tzinfo=UTC),
        market_segment="usdm_futures",
    )
    repo.upsert_normalized_documents((existing_spot, existing_usdm))

    result = import_crypto_prepackaged_to_repository(
        package_paths=(spot_zip,),
        repository=repo,
        import_run_id="crypto-import-replace-spot",
        symbol_id="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        interval="1d",
        market_segment="spot",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
        replace_existing=True,
        confirm_replace_existing=True,
    )

    assert result.deleted_existing_count == 1
    assert repo.get_normalized_document_for_maintenance(str(existing_usdm["dataset_ref"])) is not None
    assert repo.get_normalized_document_for_maintenance(str(existing_spot["dataset_ref"])) is not None
    assert repo.get_normalized_document_for_maintenance(str(existing_spot["dataset_ref"]))["row"]["close"] == "153.40"  # type: ignore[index]


def _parse_sample_zip(zip_path):
    return parse_binance_kline_zip(
        zip_path,
        symbol_id="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1d",
        market_segment="spot",
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:sample",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
    )


def _kline_zip(tmp_path, name: str, csv_body: str):
    zip_path = tmp_path / name
    csv_name = name.replace(".zip", ".csv")
    with ZipFile(zip_path, "w") as archive:
        archive.writestr(csv_name, csv_body)
    return zip_path
