from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from claw_trade.data_gateway.warehouse import DatasetRepository


def _load_cli_module():
    script_path = Path(__file__).parents[3] / "scripts" / "selection" / "merge_a_share_factory_seed_incremental.py"
    spec = importlib.util.spec_from_file_location("merge_a_share_factory_seed_incremental", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _collections() -> dict[str, dict[str, object]]:
    return {name: {} for name in DatasetRepository.collection_names()}


def _write_partition(root: Path, dataset: str, rows: list[dict[str, Any]]) -> Path:
    import duckdb

    path = root / "market=CN_A" / f"dataset={dataset}" / "granularity=daily" / f"partition-{dataset}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE rows (
              dataset_ref VARCHAR,
              dataset VARCHAR,
              market VARCHAR,
              symbol_id VARCHAR,
              universe_ref VARCHAR,
              granularity VARCHAR,
              period_start VARCHAR,
              period_end VARCHAR,
              field_set_json VARCHAR,
              as_of VARCHAR,
              fresh_until VARCHAR,
              source_roles_json VARCHAR,
              dataset_checksum VARCHAR,
              dataset_checksum_algorithm VARCHAR,
              dataset_checksum_scope VARCHAR,
              dataset_row_count BIGINT,
              row_json VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["dataset_ref"],
                    row["dataset"],
                    row["market"],
                    row["symbol_id"],
                    row["universe_ref"],
                    row["granularity"],
                    row["period_start"],
                    row["period_end"],
                    row["field_set_json"],
                    row["as_of"],
                    row["fresh_until"],
                    row["source_roles_json"],
                    row["dataset_checksum"],
                    row["dataset_checksum_algorithm"],
                    row["dataset_checksum_scope"],
                    row["dataset_row_count"],
                    row["row_json"],
                )
                for row in rows
            ],
        )
        con.execute(f"COPY rows TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()
    return path


def _row(
    dataset: str,
    symbol: str,
    day: str,
    value: float,
    *,
    as_of: str = "2026-06-01T00:00:00+00:00",
    company_name: str | None = None,
) -> dict[str, Any]:
    row_field = "close" if dataset == "daily_bar" else "pe_ttm"
    row_json = {"trade_date": day, "symbol_id": symbol, row_field: value}
    if company_name is not None:
        row_json["company_name"] = company_name
    return {
        "dataset_ref": f"dataset:{dataset}:CN_A:{symbol}:daily:{day}:{day}",
        "dataset": dataset,
        "market": "CN_A",
        "symbol_id": symbol,
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": day,
        "period_end": day,
        "field_set_json": json.dumps([row_field, "trade_date", "symbol_id"], ensure_ascii=False),
        "as_of": as_of,
        "fresh_until": "2026-06-02T00:00:00+00:00",
        "source_roles_json": json.dumps(["local_seed"], ensure_ascii=False),
        "dataset_checksum": f"checksum:{dataset}:{symbol}:{day}:{value}",
        "dataset_checksum_algorithm": "sha256:canonical-json-v1",
        "dataset_checksum_scope": "normalized-batch-v1",
        "dataset_row_count": 1,
        "row_json": json.dumps(row_json, ensure_ascii=False, sort_keys=True),
    }


def _dataset_rows(dataset: str, days: list[str], *, offset: float = 0.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day_index, day in enumerate(days, start=1):
        rows.append(_row(dataset, "000001.SZ", day, day_index + offset))
        rows.append(_row(dataset, "000002.SZ", day, day_index + 10 + offset))
    return rows


def _read_rows(path: Path) -> list[dict[str, Any]]:
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            "SELECT dataset, symbol_id, period_start, row_json FROM read_parquet(?) ORDER BY dataset, symbol_id, period_start",
            [str(path)],
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "dataset": dataset,
            "symbol_id": symbol,
            "period_start": day,
            "row": json.loads(row_json),
        }
        for dataset, symbol, day, row_json in rows
    ]


def test_incremental_merge_rolls_seed_to_latest_trading_days_and_replaces_metadata(tmp_path: Path) -> None:
    cli = _load_cli_module()
    seed_root = tmp_path / "seed"
    incremental_root = tmp_path / "incremental"
    collections = _collections()
    collections["dataset_manifests"]["old"] = {"manifest_ref": "old"}
    collections["raw_payloads"]["raw:old"] = {"raw_ref": "raw:old"}
    collections["provider_attempts"]["attempt:old"] = {"attempt_ref": "attempt:old"}

    for dataset in ("daily_bar", "valuation_metric"):
        seed_rows = _dataset_rows(dataset, ["2026-01-01", "2026-01-02", "2026-01-03"])
        if dataset == "daily_bar":
            seed_rows[0] = _row(dataset, "000001.SZ", "2026-01-01", 1.0, company_name="平安银行")
        _write_partition(seed_root, dataset, seed_rows)
    _write_partition(
        incremental_root,
        "daily_bar",
        [
            _row("daily_bar", "000001.SZ", "2026-01-03", 99.0, as_of="2026-06-03T00:00:00+00:00"),
            *_dataset_rows("daily_bar", ["2026-01-04"], offset=100.0),
        ],
    )
    _write_partition(
        incremental_root,
        "valuation_metric",
        [
            _row("valuation_metric", "000001.SZ", "2026-01-03", 88.0, as_of="2026-06-03T00:00:00+00:00"),
            *_dataset_rows("valuation_metric", ["2026-01-04"], offset=200.0),
        ],
    )

    result = cli.merge_factory_seed_incremental(
        seed_columnar_root=seed_root,
        incremental_columnar_root=incremental_root,
        trading_days=3,
        repository=DatasetRepository(collections=collections),
        database=collections,
    )

    payload = result.as_dict()
    assert payload["dry_run"] is False
    assert payload["mongo_imported"] == {
        "raw_payloads": 1,
        "provider_attempts": 1,
        "dataset_manifests": 2,
    }
    assert collections["normalized_datasets"] == {}
    assert len(collections["dataset_manifests"]) == 2
    assert len(collections["raw_payloads"]) == 1
    assert len(collections["provider_attempts"]) == 1
    assert payload["datasets"]["daily_bar"]["date_min"] == "2026-01-02"
    assert payload["datasets"]["daily_bar"]["date_max"] == "2026-01-04"
    assert payload["datasets"]["valuation_metric"]["date_min"] == "2026-01-02"
    assert payload["datasets"]["valuation_metric"]["date_max"] == "2026-01-04"

    daily_rows = _read_rows(seed_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-factory-300td-daily_bar.parquet")
    assert {row["period_start"] for row in daily_rows} == {"2026-01-02", "2026-01-03", "2026-01-04"}
    assert next(row for row in daily_rows if row["symbol_id"] == "000001.SZ" and row["period_start"] == "2026-01-03")["row"]["close"] == 99.0
    assert next(row for row in daily_rows if row["symbol_id"] == "000001.SZ" and row["period_start"] == "2026-01-03")["row"]["company_name"] == "平安银行"
    assert next(row for row in daily_rows if row["symbol_id"] == "000001.SZ" and row["period_start"] == "2026-01-04")["row"]["close"] == 101.0


def test_incremental_merge_rejects_half_updated_dataset_window_by_default(tmp_path: Path) -> None:
    cli = _load_cli_module()
    seed_root = tmp_path / "seed"
    incremental_root = tmp_path / "incremental"

    for dataset in ("daily_bar", "valuation_metric"):
        _write_partition(seed_root, dataset, _dataset_rows(dataset, ["2026-01-01", "2026-01-02", "2026-01-03"]))
    _write_partition(incremental_root, "daily_bar", _dataset_rows("daily_bar", ["2026-01-04"], offset=100.0))

    with pytest.raises(ValueError, match="retained date windows differ"):
        cli.merge_factory_seed_incremental(
            seed_columnar_root=seed_root,
            incremental_columnar_root=incremental_root,
            trading_days=3,
            repository=DatasetRepository(collections=_collections()),
            database=_collections(),
        )


def test_incremental_merge_dry_run_does_not_modify_seed_or_metadata(tmp_path: Path) -> None:
    cli = _load_cli_module()
    seed_root = tmp_path / "seed"
    incremental_root = tmp_path / "incremental"
    collections = _collections()
    collections["dataset_manifests"]["old"] = {"manifest_ref": "old"}

    for dataset in ("daily_bar", "valuation_metric"):
        _write_partition(seed_root, dataset, _dataset_rows(dataset, ["2026-01-01", "2026-01-02", "2026-01-03"]))
        _write_partition(incremental_root, dataset, _dataset_rows(dataset, ["2026-01-04"], offset=100.0))

    result = cli.merge_factory_seed_incremental(
        seed_columnar_root=seed_root,
        incremental_columnar_root=incremental_root,
        trading_days=3,
        repository=DatasetRepository(collections=collections),
        database=collections,
        dry_run=True,
    )

    assert result.as_dict()["dry_run"] is True
    assert collections["dataset_manifests"] == {"old": {"manifest_ref": "old"}}
    daily_rows = _read_rows(seed_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-daily_bar.parquet")
    assert {row["period_start"] for row in daily_rows} == {"2026-01-01", "2026-01-02", "2026-01-03"}
