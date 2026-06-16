from __future__ import annotations

from datetime import UTC, date, datetime

from claw_trade.data_gateway.crypto_prepackaged_importer import (
    BINANCE_PUBLIC_DATA_ENDPOINT_ID,
    CRYPTO_BINANCE_BAR_SCHEMA_ID,
    CRYPTO_CALENDAR,
    CRYPTO_EXCHANGE,
    CRYPTO_TIMEZONE,
    LOCAL_CRYPTO_PROVIDER_ID,
    build_crypto_bar_record,
    import_crypto_prepackaged_to_repository,
    local_warehouse_empty_gap,
)
from claw_trade.data_gateway.models import DataRequest, GapReason, Market
from claw_trade.data_gateway.warehouse import DatasetRepository


def test_crypto_prepackaged_attempt_contract_uses_local_seed_status(tmp_path) -> None:
    from zipfile import ZipFile

    zip_path = tmp_path / "BTCUSDT-1d-2024-06.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "BTCUSDT-1d-2024-06.csv",
            "1717200000000,67000,68000,66000,67500,10,1717286399999,675000,1000,5,337500,0\n",
        )
    repo = DatasetRepository()

    result = import_crypto_prepackaged_to_repository(
        package_paths=(zip_path,),
        repository=repo,
        import_run_id="import-btc",
        symbol_id="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1d",
        market_segment="spot",
        as_of=datetime(2024, 6, 2, tzinfo=UTC),
    )

    attempt = repo.get_provider_attempt(result.attempt_refs[0])
    assert attempt is not None
    assert attempt["provider"] == LOCAL_CRYPTO_PROVIDER_ID
    assert attempt["endpoint"] == BINANCE_PUBLIC_DATA_ENDPOINT_ID
    assert attempt["status"] == "local_seed_imported"
    assert attempt["remote_attempted"] is False
    assert attempt["remote_success"] is False


def test_crypto_prepackaged_contract_reuses_existing_warehouse_collections() -> None:
    assert "normalized_datasets" in DatasetRepository.collection_names()
    assert "raw_payloads" in DatasetRepository.collection_names()
    assert "provider_attempts" in DatasetRepository.collection_names()
    assert "dataset_manifests" in DatasetRepository.collection_names()
    assert not any("crypto_prepackaged" in name for name in DatasetRepository.collection_names())


def test_crypto_prepackaged_normalized_row_contract() -> None:
    record = build_crypto_bar_record(
        symbol_id="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        interval="1d",
        open_time=datetime(2026, 6, 1, tzinfo=UTC),
        close_time=datetime(2026, 6, 1, 23, 59, 59, 999000, tzinfo=UTC),
        open_price="3500",
        high_price="3550",
        low_price="3450",
        close_price="3525",
        volume="100",
        quote_asset_volume="352500",
        number_of_trades=2000,
        raw_ref="raw:CRYPTO:local_crypto_prepackaged:eth",
        as_of=datetime(2026, 6, 2, tzinfo=UTC),
    )

    assert record["dataset"] == "daily_bar"
    assert record["dataset_ref"] == "dataset:daily_bar:CRYPTO:spot:ETHUSDT:daily:2026-06-01:2026-06-01"
    assert record["market"] == Market.CRYPTO.value
    assert record["universe_ref"] == "binance_spot_all_symbols"
    assert record["exchange"] == CRYPTO_EXCHANGE
    assert record["timezone"] == CRYPTO_TIMEZONE
    assert record["calendar"] == CRYPTO_CALENDAR
    assert record["base_asset"] == "ETH"
    assert record["quote_asset"] == "USDT"
    assert record["source_market_segment"] == "spot"
    assert record["schema_id"] == CRYPTO_BINANCE_BAR_SCHEMA_ID
    assert record["provider_lineage"]["provider_id"] == LOCAL_CRYPTO_PROVIDER_ID  # type: ignore[index]
    assert record["provider_lineage"]["endpoint_id"] == BINANCE_PUBLIC_DATA_ENDPOINT_ID  # type: ignore[index]
    assert record["provider_lineage"]["remote_attempted"] is False  # type: ignore[index]


def test_local_warehouse_empty_contract_is_explicit_without_remote_success() -> None:
    request = DataRequest(
        request_id="req-btc",
        market=Market.CRYPTO,
        symbol_id="BTCUSDT",
        universe_ref=None,
        data_type="daily_bar",
        granularity="daily",
        fields=("close",),
        date_range_start=date(2026, 6, 1),
        date_range_end=date(2026, 6, 7),
        freshness_policy="warehouse_only",
        timezone=CRYPTO_TIMEZONE,
        calendar=CRYPTO_CALENDAR,
        exchange=CRYPTO_EXCHANGE,
        currency="USDT",
        base_asset="BTC",
        quote_asset="USDT",
        consumer="report",
        consumer_id="market_analyst",
        as_of=datetime(2026, 6, 8, tzinfo=UTC),
    )

    gap = local_warehouse_empty_gap(request)

    assert gap.reason == GapReason.WAREHOUSE_MISSING
    assert gap.provider_ids_tried == (LOCAL_CRYPTO_PROVIDER_ID,)
    assert "local_warehouse_empty" in gap.human_readable
