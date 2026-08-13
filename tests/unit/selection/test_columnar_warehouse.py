from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import claw_trade.data_gateway._selection_batch as selection_batch_impl
from claw_trade.data_gateway.selection_api import fetch_selection_batch_from_data_gateway
from claw_trade.data_gateway.selection_integrity import validate_selection_columnar_manifest_ref
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse
from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse
from claw_trade.selection.models import (
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)


def test_columnar_warehouse_writes_reads_and_validates_hash(tmp_path: Path) -> None:
    plan = _plan("sel-run-columnar")
    warehouse = SelectionColumnarWarehouse(root=tmp_path)
    writer = warehouse.begin_write(plan=plan)
    writer.add_daily_rows(
        (
            {
                "market": "CN_A",
                "profile": "CN_A",
                "selection_trade_date": "2026-06-04",
                "ticker": "000001.SZ",
                "date": "2026-06-04",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 1000000.0,
                "dataset_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
                "source_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
            },
        )
    )
    writer.add_feature_rows(
        (
            {
                "ticker": "000001.SZ",
                "company_name": "平安银行",
                "industry": "银行",
                "source_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
                "trade_date": "2026-06-04",
                "selection_features_materialized": True,
                "amount": 1000000.0,
                "close": 10.5,
                "strategy_signal_myhhub_volume_rise": 1.0,
            },
        )
    )
    manifest = writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",),
    )

    assert manifest.daily_row_count == 1
    assert manifest.feature_row_count == 1
    assert (tmp_path / "market_data" / "market=CN_A" / "profile=CN_A" / "trade_date=2026-06-04" / "manifest.json.sha256").exists()
    assert warehouse.validate_manifest_ref(manifest.manifest_ref)
    rows = warehouse.read_feature_rows(
        manifest=manifest,
        columns=("ticker", "company_name", "selection_features_materialized", "amount", "missing_optional_field"),
        row_limit=10,
    )
    assert rows[0]["ticker"] == "000001.SZ"
    assert rows[0]["selection_features_materialized"] is True
    assert rows[0]["amount"] == 1000000.0
    assert rows[0]["missing_optional_field"] is None
    assert "close" not in rows[0]

    feature_path = tmp_path / manifest.feature_partitions[0].relative_path
    feature_path.write_bytes(feature_path.read_bytes() + b"corrupt")

    assert not warehouse.validate_manifest_ref(manifest.manifest_ref)


def test_columnar_warehouse_rejects_tampered_manifest_file(tmp_path: Path) -> None:
    plan = _plan("sel-run-manifest-tamper")
    warehouse = SelectionColumnarWarehouse(root=tmp_path)
    writer = warehouse.begin_write(plan=plan)
    writer.add_daily_rows(({"ticker": "000001.SZ", "date": "2026-06-04", "close": 10.5},))
    writer.add_feature_rows(
        (
            {
                "ticker": "000001.SZ",
                "company_name": "平安银行",
                "source_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
                "selection_features_materialized": True,
                "close": 10.5,
            },
        )
    )
    manifest = writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",),
    )
    manifest_path = tmp_path / "market_data" / "market=CN_A" / "profile=CN_A" / "trade_date=2026-06-04" / "manifest.json"
    manifest_path.write_text(f"{manifest_path.read_text(encoding='utf-8')}\n", encoding="utf-8")

    assert not warehouse.validate_manifest_ref(manifest.manifest_ref)


def test_columnar_warehouse_normalizes_legacy_mongo_refs_without_rebuilding_cache(tmp_path: Path) -> None:
    plan = _plan("sel-run-legacy-normalized-ref")
    warehouse = SelectionColumnarWarehouse(root=tmp_path)
    writer = warehouse.begin_write(plan=plan)
    writer.add_daily_rows(({"ticker": "000001.SZ", "date": "2026-06-04", "close": 10.5},))
    writer.add_feature_rows(
        (
            {
                "ticker": "000001.SZ",
                "company_name": "平安银行",
                "source_ref": "normalized://mongo/normalized_datasets/dataset:daily_bar:CN_A:legacy",
                "selection_features_materialized": True,
                "close": 10.5,
            },
        )
    )
    manifest = writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("normalized://mongo/normalized_datasets/dataset:daily_bar:CN_A:legacy",),
    )

    assert warehouse.validate_manifest_ref(manifest.manifest_ref)
    assert manifest.normalized_refs == ("dataset://normalized/CN_A/daily_bar/daily/dataset:daily_bar:CN_A:legacy",)
    rows = warehouse.read_feature_rows(manifest=manifest, columns=("source_ref",), row_limit=10)
    assert rows[0]["source_ref"] == "dataset://normalized/CN_A/daily_bar/daily/dataset:daily_bar:CN_A:legacy"


def test_columnar_warehouse_rejects_incomplete_coverage_manifest(tmp_path: Path) -> None:
    plan = _plan("sel-run-incomplete-coverage")
    warehouse = SelectionColumnarWarehouse(root=tmp_path)
    writer = warehouse.begin_write(plan=plan)
    writer.add_daily_rows(({"ticker": "000001.SZ", "date": "2026-06-04", "close": 10.5},))
    writer.add_feature_rows(
        (
            {
                "ticker": "000001.SZ",
                "company_name": "平安银行",
                "source_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
                "selection_features_materialized": True,
                "close": 10.5,
            },
        )
    )
    writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",),
        coverage_status="incomplete",
        coverage_gap_codes=("selection_batch_rows_empty",),
    )

    assert warehouse.load_valid_manifest(plan=plan) is None


@pytest.mark.parametrize(("source_offset_seconds", "expected_valid"), ((-1, True), (1, False)))
def test_crypto_manifest_validity_tracks_seed_file_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_offset_seconds: int,
    expected_valid: bool,
) -> None:
    plan = _crypto_plan("sel-run-crypto-source-freshness")
    root = tmp_path / "selection"
    seed_root = tmp_path / "crypto-seed"
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(root))
    monkeypatch.setenv("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT", str(seed_root))
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_URI", raising=False)
    monkeypatch.delenv("CN_A_MONGODB_URI", raising=False)
    warehouse = SelectionColumnarWarehouse(root=root)
    manifest = _write_crypto_selection_manifest(warehouse=warehouse, plan=plan)
    seed_files = _write_crypto_seed(seed_root=seed_root, trade_date=plan.trade_date)
    manifest_created_at = datetime.fromisoformat(manifest.created_at.replace("Z", "+00:00"))
    source_mtime = (manifest_created_at + timedelta(seconds=source_offset_seconds)).timestamp()
    for path in seed_files:
        os.utime(path, (source_mtime, source_mtime))

    assert (
        validate_selection_columnar_manifest_ref(
            manifest.manifest_ref,
            expected_sha256=warehouse.manifest_sha256(manifest.manifest_ref),
        )
        is expected_valid
    )


def test_crypto_stale_manifest_is_rebuilt_once_then_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _crypto_plan("sel-run-crypto-rebuild-once")
    root = tmp_path / "selection"
    seed_root = tmp_path / "crypto-seed"
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(root))
    monkeypatch.setenv("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT", str(seed_root))
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_URI", raising=False)
    monkeypatch.delenv("CN_A_MONGODB_URI", raising=False)
    monkeypatch.setattr(selection_batch_impl, "_CRYPTO_HISTORY_COLUMNAR_ROOT", seed_root)
    monkeypatch.setattr(selection_batch_impl, "_crypto_runtime_repository_or_none", lambda: None)
    warehouse = SelectionColumnarWarehouse(root=root)
    stale_manifest = _write_crypto_selection_manifest(warehouse=warehouse, plan=plan)
    seed_files = _write_crypto_seed(seed_root=seed_root, trade_date=plan.trade_date)
    stale_created_at = datetime.fromisoformat(stale_manifest.created_at.replace("Z", "+00:00"))
    source_mtime = (stale_created_at + timedelta(microseconds=1)).timestamp()
    for path in seed_files:
        os.utime(path, (source_mtime, source_mtime))

    rebuilt = fetch_selection_batch_from_data_gateway(plan)
    reused = fetch_selection_batch_from_data_gateway(plan)

    assert rebuilt.columnar_manifest_ref is not None
    assert rebuilt.columnar_manifest_ref == reused.columnar_manifest_ref
    assert validate_selection_columnar_manifest_ref(
        rebuilt.columnar_manifest_ref,
        expected_sha256=rebuilt.columnar_manifest_sha256,
    )


def test_crypto_manifest_older_than_runtime_manifest_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _crypto_plan("sel-run-crypto-runtime-freshness")
    root = tmp_path / "selection"
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(root))
    monkeypatch.setenv("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT", str(tmp_path / "empty-seed"))
    monkeypatch.setenv("DATA_GATEWAY_MONGODB_URI", "mongodb://runtime")
    warehouse = SelectionColumnarWarehouse(root=root)
    manifest = _write_crypto_selection_manifest(warehouse=warehouse, plan=plan)
    source_created_at = (
        datetime.fromisoformat(manifest.created_at.replace("Z", "+00:00")) + timedelta(seconds=1)
    ).isoformat()
    repository = SimpleNamespace(
        list_dataset_manifests=lambda: (
            {
                "storage": "parquet",
                "status": "active",
                "market": "CRYPTO",
                "dataset": "daily_bar",
                "granularity": "daily",
                "period_start_min": plan.trade_date,
                "period_end_max": plan.trade_date,
                "universe_refs": ("binance_spot_all_symbols",),
                "created_at": source_created_at,
            },
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.selection_integrity.build_data_gateway_runtime_from_env",
        lambda: SimpleNamespace(repository=repository),
    )

    assert not validate_selection_columnar_manifest_ref(
        manifest.manifest_ref,
        expected_sha256=warehouse.manifest_sha256(manifest.manifest_ref),
    )


def test_columnar_warehouse_load_valid_manifest_rejects_plan_mismatch(tmp_path: Path) -> None:
    warehouse = SelectionColumnarWarehouse(root=tmp_path)
    writer = warehouse.begin_write(plan=_plan("sel-run-columnar"))
    writer.add_daily_rows(({"ticker": "000001.SZ", "date": "2026-06-04", "close": 10.5},))
    writer.add_feature_rows(
        (
            {
                "ticker": "000001.SZ",
                "company_name": "平安银行",
                "source_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
                "selection_features_materialized": True,
                "close": 10.5,
            },
        )
    )
    writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",),
    )

    assert warehouse.load_valid_manifest(plan=_plan("sel-run-columnar", trade_date="2026-06-03")) is None


def test_selection_batch_uses_valid_columnar_manifest_before_data_gateway(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "columnar"
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(root))
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_URI", raising=False)
    monkeypatch.delenv("CN_A_MONGODB_URI", raising=False)
    plan = _plan(
        "sel-run-columnar-cache",
        data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
    )
    writer = SelectionColumnarWarehouse(root=root).begin_write(plan=plan)
    writer.add_daily_rows(({"ticker": "000001.SZ", "date": "2026-06-04", "close": 10.5},))
    writer.add_feature_rows(
        (
            {
                "ticker": "000001.SZ",
                "company_name": "平安银行",
                "source_ref": "dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",
                "trade_date": "2026-06-04",
                "selection_features_materialized": True,
                "amount": 1000000.0,
                "close": 10.5,
            },
        )
    )
    manifest = writer.commit(
        provider_attempt_refs=("attempt://provider/columnar-cache",),
        normalized_refs=("dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",),
    )

    result = fetch_selection_batch_from_data_gateway(plan)

    assert result.columnar_manifest_ref == manifest.manifest_ref
    assert result.warehouse_check_ref == manifest.warehouse_check_ref
    assert result.rows[0]["ticker"] == "000001.SZ"
    assert result.attempt_refs == ("attempt://data-provider/provider:columnar-cache",)


def test_columnar_warehouse_rejects_feature_row_limit_exceeded(tmp_path: Path) -> None:
    plan = _plan("sel-run-columnar-row-limit")
    warehouse = SelectionColumnarWarehouse(root=tmp_path)
    writer = warehouse.begin_write(plan=plan)
    writer.add_daily_rows(({"ticker": "000001.SZ", "date": "2026-06-04", "close": 10.5},))
    writer.add_feature_rows(
        (
            {
                "ticker": f"{idx:06d}.SZ",
                "company_name": f"样本{idx}",
                "source_ref": f"dataset://normalized/CN_A/daily/daily/{idx:06d}.SZ/2026-06-04",
                "selection_features_materialized": True,
                "amount": 1000000.0,
            }
            for idx in range(2)
        )
    )
    manifest = writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("dataset://normalized/CN_A/daily/daily/000001.SZ/2026-06-04",),
    )

    try:
        warehouse.read_feature_rows(manifest=manifest, columns=("ticker", "amount"), row_limit=1)
    except ValueError as exc:
        assert str(exc) == "selection_columnar_row_limit_exceeded"
    else:
        raise AssertionError("row limit must be enforced")


def _plan(
    selection_run_id: str,
    *,
    trade_date: str = "2026-06-04",
    data_need_audit_ref: str | None = None,
) -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id=selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        data_need_audit_ref=data_need_audit_ref or f"plan://selection/cn_a/{trade_date}/batch-v1",
        approved_strategy_config_ref="config://selection/cn_a/v1",
        trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
    )


def _crypto_plan(selection_run_id: str) -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id=selection_run_id,
        market=SelectionMarket.CRYPTO,
        profile=SelectionProfile.CRYPTO,
        trade_date="2026-06-04",
        lookback_trading_days=180,
        universe_scope="spot_usdt",
        data_need_audit_ref="plan://selection/crypto/2026-06-04/batch-v1",
        approved_strategy_config_ref="config://crypto-selection-v1",
        trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
    )


def _write_crypto_selection_manifest(*, warehouse: SelectionColumnarWarehouse, plan: SelectionRunPlan):
    writer = warehouse.begin_write(plan=plan)
    writer.add_daily_rows(({"ticker": "BTCUSDT", "date": plan.trade_date, "close": 10.5},))
    writer.add_feature_rows(
        ({"ticker": "BTCUSDT", "source_ref": "dataset://normalized/CRYPTO/daily/BTCUSDT", "close": 10.5},)
    )
    return writer.commit(
        provider_attempt_refs=("attempt://provider/1",),
        normalized_refs=("dataset://normalized/CRYPTO/daily/BTCUSDT",),
    )


def _write_crypto_seed(*, seed_root: Path, trade_date: str) -> tuple[Path, ...]:
    end = date.fromisoformat(trade_date)
    records = []
    for offset in range(180):
        day = end - timedelta(days=179 - offset)
        close = 100.0 + offset
        records.append(
            {
                "dataset_ref": f"dataset:daily_bar:CRYPTO:spot:BTCUSDT:daily:{day}:{day}",
                "dataset": "daily_bar",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "universe_ref": "binance_spot_all_symbols",
                "granularity": "daily",
                "period_start": day.isoformat(),
                "period_end": day.isoformat(),
                "source_roles": ("local_seed", "built_in_public"),
                "row": {
                    "symbol_id": "BTCUSDT",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "market_segment": "spot",
                    "date": day.isoformat(),
                    "open": close - 1.0,
                    "high": close + 1.0,
                    "low": close - 2.0,
                    "close": close,
                    "volume": 1_000_000.0,
                    "amount": 2_000_000.0,
                },
            }
        )
    NormalizedColumnarWarehouse(seed_root).write_records(records)
    return tuple(seed_root.glob("market=CRYPTO/dataset=daily_bar/granularity=daily/*.parquet"))
