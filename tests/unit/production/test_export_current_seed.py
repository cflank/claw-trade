from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import duckdb
import pytest

from claw_trade.data_gateway import factory_seed_integrity
from claw_trade.data_gateway.warehouse import DatasetRepository


def _load_cli_module():
    script_path = Path(__file__).parents[3] / "scripts" / "production" / "export_current_seed.py"
    spec = importlib.util.spec_from_file_location("export_current_seed", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_restore_module():
    script_path = Path(__file__).parents[3] / "scripts" / "selection" / "restore_a_share_factory_seed.py"
    spec = importlib.util.spec_from_file_location("restore_a_share_factory_seed", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_export_current_seed_package_writes_restore_compatible_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    restore_cli = _load_restore_module()
    columnar_root = tmp_path / "normalized"
    partition = (
        columnar_root
        / "market=CN_A"
        / "dataset=daily_bar"
        / "granularity=daily"
        / "partition-test.parquet"
    )
    partition.parent.mkdir(parents=True)
    _write_daily_bar_parquet(partition)
    valuation_partition = (
        columnar_root
        / "market=CN_A"
        / "dataset=valuation_metric"
        / "granularity=daily"
        / "partition-test.parquet"
    )
    valuation_partition.parent.mkdir(parents=True)
    _write_valuation_parquet(valuation_partition)
    output = tmp_path / "current-seed-20260629.tar"

    result = cli.export_current_seed_package(
        output_path=output,
        package_id="current-seed-20260629",
        columnar_root=columnar_root,
        records_by_collection={
            "raw_payloads": [
                {
                    "_id": "mongo-object-id",
                    "raw_ref": "raw:test",
                    "provider": "local_seed",
                    "created_at": datetime(2026, 6, 29, tzinfo=UTC),
                }
            ],
            "provider_attempts": [
                {
                    "attempt_ref": "attempt:test",
                    "provider": "local_seed",
                    "message": "unicode line separator: \u2028",
                    "created_at": datetime(2026, 6, 29, tzinfo=UTC),
                }
            ],
            "dataset_manifests": [
                {
                    "manifest_ref": "manifest:test",
                    "storage": "parquet",
                    "status": "active",
                    "market": "CN_A",
                    "dataset": "daily_bar",
                    "granularity": "daily",
                    "relative_path": "market=CN_A/dataset=daily_bar/granularity=daily/partition-test.parquet",
                    "path": str(partition),
                    "row_count": 2,
                },
                {
                    "manifest_ref": "manifest:valuation:test",
                    "storage": "parquet",
                    "status": "active",
                    "market": "CN_A",
                    "dataset": "valuation_metric",
                    "granularity": "daily",
                    "relative_path": "market=CN_A/dataset=valuation_metric/granularity=daily/partition-test.parquet",
                    "path": str(valuation_partition),
                    "row_count": 2,
                },
            ],
        },
        created_at=datetime(2026, 6, 29, tzinfo=UTC),
    )

    checksum_path = output.with_name(output.name + ".sha256")
    assert result.as_dict()["mongo_exported"] == {
        "raw_payloads": 1,
        "provider_attempts": 1,
        "dataset_manifests": 2,
    }
    assert output.exists()
    assert checksum_path.read_text(encoding="utf-8") == f"{sha256(output.read_bytes()).hexdigest()}  {output.name}\n"

    with tarfile.open(output, "r") as archive:
        names = set(archive.getnames())
        assert "current-seed-20260629/seed_manifest.json" in names
        assert "current-seed-20260629/mongo/raw_payloads.jsonl" in names
        assert "current-seed-20260629/mongo/provider_attempts.jsonl" in names
        assert "current-seed-20260629/mongo/dataset_manifests.jsonl" in names
        assert (
            "current-seed-20260629/data/normalized/"
            "market=CN_A/dataset=daily_bar/granularity=daily/partition-test.parquet"
        ) in names
        assert (
            "current-seed-20260629/data/normalized/"
            "market=CN_A/dataset=valuation_metric/granularity=daily/partition-test.parquet"
        ) in names

        raw_payload = _read_jsonl_member(archive, "current-seed-20260629/mongo/raw_payloads.jsonl")[0]
        assert "_id" not in raw_payload
        assert raw_payload["created_at"] == "2026-06-29T00:00:00+00:00"
        provider_attempt_text = archive.extractfile(  # type: ignore[union-attr]
            "current-seed-20260629/mongo/provider_attempts.jsonl"
        ).read().decode("utf-8")
        assert "\\u2028" in provider_attempt_text

        seed_manifest = json.loads(
            archive.extractfile("current-seed-20260629/seed_manifest.json").read().decode("utf-8")  # type: ignore[union-attr]
        )
        assert seed_manifest["package_type"] == "current_factory_seed"
        assert seed_manifest["markets"] == ["CN_A"]
        assert seed_manifest["datasets"]["CN_A"]["daily_bar"] == {
            "granularities": ["daily"],
            "manifests": 1,
            "rows": 2,
        }
        assert seed_manifest["datasets"]["CN_A"]["valuation_metric"] == {
            "granularities": ["daily"],
            "manifests": 1,
            "rows": 2,
        }
        assert seed_manifest["parquet_files"] == 2
        assert seed_manifest["parquet_bytes"] == partition.stat().st_size + valuation_partition.stat().st_size

    restored_collections = {name: {} for name in DatasetRepository.collection_names()}
    restored_root = tmp_path / "restored-normalized"
    restore_result = restore_cli.restore_factory_seed(
        package_path=output,
        checksum_path=checksum_path,
        repository=DatasetRepository(collections=restored_collections),
        columnar_root=restored_root,
    )
    assert restore_result.as_dict()["mongo_imported"] == {
        "raw_payloads": 1,
        "provider_attempts": 1,
        "dataset_manifests": 2,
    }
    restored_parquet = (
        restored_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-test.parquet"
    )
    with duckdb.connect(":memory:") as conn:
        assert conn.execute(
            "select json_extract_string(row_json, ?) from read_parquet(?)",
            ["$.company_name", str(restored_parquet)],
        ).fetchone() == ("平安银行",)


def test_export_current_seed_rejects_manifest_that_points_to_missing_parquet(tmp_path: Path) -> None:
    cli = _load_cli_module()
    columnar_root = tmp_path / "normalized"
    columnar_root.mkdir()

    with pytest.raises(ValueError, match="missing Parquet"):
        cli.export_current_seed_package(
            output_path=tmp_path / "current-seed-20260629.tar",
            package_id="current-seed-20260629",
            columnar_root=columnar_root,
            records_by_collection={
                "raw_payloads": [],
                "provider_attempts": [],
                "dataset_manifests": [
                    {
                        "manifest_ref": "manifest:missing",
                        "storage": "parquet",
                        "status": "active",
                        "market": "CN_A",
                        "dataset": "daily_bar",
                        "relative_path": "market=CN_A/dataset=daily_bar/granularity=daily/missing.parquet",
                    }
                ],
            },
        )


def test_export_current_seed_rejects_cn_a_daily_bar_without_company_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    columnar_root = tmp_path / "normalized"
    partition = (
        columnar_root
        / "market=CN_A"
        / "dataset=daily_bar"
        / "granularity=daily"
        / "partition-test.parquet"
    )
    partition.parent.mkdir(parents=True)
    _write_daily_bar_parquet(partition, company_name=None)
    _write_valuation_for_root(columnar_root)

    with pytest.raises(ValueError, match="company_name missing"):
        cli.export_current_seed_package(
            output_path=tmp_path / "current-seed-20260629.tar",
            package_id="current-seed-20260629",
            columnar_root=columnar_root,
            records_by_collection={
                "raw_payloads": [],
                "provider_attempts": [],
                "dataset_manifests": _cn_a_manifest_records(columnar_root),
            },
        )


def test_export_current_seed_rejects_shallow_cn_a_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    columnar_root = tmp_path / "normalized"
    partition = (
        columnar_root
        / "market=CN_A"
        / "dataset=daily_bar"
        / "granularity=daily"
        / "partition-test.parquet"
    )
    partition.parent.mkdir(parents=True)
    _write_daily_bar_parquet(partition, history_days=249)
    _write_valuation_for_root(columnar_root)

    with pytest.raises(ValueError, match="trading-day coverage insufficient"):
        cli.export_current_seed_package(
            output_path=tmp_path / "current-seed-20260629.tar",
            package_id="current-seed-20260629",
            columnar_root=columnar_root,
            records_by_collection={
                "raw_payloads": [],
                "provider_attempts": [],
                "dataset_manifests": _cn_a_manifest_records(columnar_root),
            },
        )


def test_cn_a_factory_seed_rejects_a_single_symbol_as_full_market(tmp_path: Path) -> None:
    columnar_root = tmp_path / "normalized"
    daily_path = (
        columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-test.parquet"
    )
    daily_path.parent.mkdir(parents=True)
    _write_daily_bar_parquet(daily_path)
    _write_valuation_for_root(columnar_root)

    with pytest.raises(ValueError, match="full-market coverage insufficient"):
        factory_seed_integrity.validate_cn_a_factory_seed(
            columnar_root,
            label="test seed",
            manifest_records=_cn_a_manifest_records(columnar_root),
        )


def test_cn_a_factory_seed_requires_valuation_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    columnar_root = tmp_path / "normalized"
    daily_path = (
        columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-test.parquet"
    )
    daily_path.parent.mkdir(parents=True)
    _write_daily_bar_parquet(daily_path)

    with pytest.raises(ValueError, match="missing CN_A valuation_metric"):
        factory_seed_integrity.validate_cn_a_factory_seed(
            columnar_root,
            label="test seed",
            manifest_records=_cn_a_manifest_records(columnar_root)[:1],
        )


@pytest.mark.parametrize(("full_history_symbols", "should_pass"), ((3, False), (4, True)))
def test_cn_a_factory_seed_enforces_eighty_percent_history_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    full_history_symbols: int,
    should_pass: bool,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 5)
    columnar_root = tmp_path / "normalized"
    for dataset in ("daily_bar", "valuation_metric"):
        _write_history_matrix(
            columnar_root,
            dataset=dataset,
            symbol_count=5,
            full_history_symbols=full_history_symbols,
        )

    if should_pass:
        result = factory_seed_integrity.validate_cn_a_factory_seed(
            columnar_root,
            label="test seed",
            manifest_records=_cn_a_manifest_records(columnar_root),
        )
        assert result["datasets"]["daily_bar"]["history_coverage_ratio"] == 0.8
    else:
        with pytest.raises(ValueError, match="history coverage insufficient"):
            factory_seed_integrity.validate_cn_a_factory_seed(
                columnar_root,
                label="test seed",
                manifest_records=_cn_a_manifest_records(columnar_root),
            )


def _read_jsonl_member(archive: tarfile.TarFile, member_name: str) -> tuple[dict[str, object], ...]:
    handle = archive.extractfile(member_name)
    assert handle is not None
    with handle:
        return tuple(json.loads(line) for line in handle.read().decode("utf-8").splitlines() if line.strip())


def _write_daily_bar_parquet(
    path: Path,
    *,
    company_name: str | None = "平安银行",
    history_days: int = 300,
) -> None:
    row = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "000001.SZ",
        "trade_date": "2026-05-27",
        "close": 10.76,
    }
    if company_name is not None:
        row["company_name"] = company_name
    with duckdb.connect(":memory:") as conn:
        conn.execute(
            """
            create table daily_bar as
            select
              'dataset:daily_bar:CN_A:test:' || i::VARCHAR as dataset_ref,
              'daily_bar'::VARCHAR as dataset,
              'CN_A'::VARCHAR as market,
              '000001.SZ'::VARCHAR as symbol_id,
              'daily'::VARCHAR as granularity,
              DATE '2026-05-27' - i::INTEGER as period_start,
              DATE '2026-05-27' - i::INTEGER as period_end,
              ?::VARCHAR as row_json
            from range(?) as history(i)
            """,
            [json.dumps(row, ensure_ascii=False), history_days],
        )
        conn.execute(f"COPY daily_bar TO '{path.as_posix()}' (FORMAT PARQUET)")


def _write_valuation_for_root(columnar_root: Path) -> Path:
    path = (
        columnar_root
        / "market=CN_A"
        / "dataset=valuation_metric"
        / "granularity=daily"
        / "partition-test.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_valuation_parquet(path)
    return path


def _write_valuation_parquet(path: Path, *, history_days: int = 300) -> None:
    with duckdb.connect(":memory:") as conn:
        conn.execute(
            """
            create table valuation_metric as
            select
              'dataset:valuation_metric:CN_A:test:' || i::VARCHAR as dataset_ref,
              'valuation_metric'::VARCHAR as dataset,
              'CN_A'::VARCHAR as market,
              '000001.SZ'::VARCHAR as symbol_id,
              'daily'::VARCHAR as granularity,
              DATE '2026-05-27' - i::INTEGER as period_start,
              DATE '2026-05-27' - i::INTEGER as period_end,
              '{}'::VARCHAR as row_json
            from range(?) as history(i)
            """,
            [history_days],
        )
        conn.execute(f"COPY valuation_metric TO '{path.as_posix()}' (FORMAT PARQUET)")


def _write_history_matrix(
    columnar_root: Path,
    *,
    dataset: str,
    symbol_count: int,
    full_history_symbols: int,
) -> Path:
    path = columnar_root / "market=CN_A" / f"dataset={dataset}" / "granularity=daily" / "partition-test.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(":memory:") as conn:
        conn.execute(
            """
            create table history_matrix as
            select
              'dataset:' || ? || ':CN_A:' || symbol_index::VARCHAR || ':' || day_index::VARCHAR as dataset_ref,
              ?::VARCHAR as dataset,
              'CN_A'::VARCHAR as market,
              lpad(symbol_index::VARCHAR, 6, '0') || '.SZ' as symbol_id,
              'daily'::VARCHAR as granularity,
              DATE '2026-05-27' - day_index::INTEGER as period_start,
              DATE '2026-05-27' - day_index::INTEGER as period_end,
              case
                when ? = 'daily_bar' then json_object('company_name', 'Company ' || symbol_index::VARCHAR)::VARCHAR
                else '{}'::VARCHAR
              end as row_json
            from range(?) as symbols(symbol_index)
            cross join range(case when symbol_index < ? then 300 else 20 end) as history(day_index)
            """,
            [dataset, dataset, dataset, symbol_count, full_history_symbols],
        )
        conn.execute(f"COPY history_matrix TO '{path.as_posix()}' (FORMAT PARQUET)")
    return path


def _cn_a_manifest_records(columnar_root: Path) -> list[dict[str, object]]:
    return [
        {
            "manifest_ref": "manifest:test",
            "storage": "parquet",
            "status": "active",
            "market": "CN_A",
            "dataset": "daily_bar",
            "granularity": "daily",
            "relative_path": "market=CN_A/dataset=daily_bar/granularity=daily/partition-test.parquet",
            "path": str(
                columnar_root
                / "market=CN_A"
                / "dataset=daily_bar"
                / "granularity=daily"
                / "partition-test.parquet"
            ),
            "row_count": 300,
        },
        {
            "manifest_ref": "manifest:valuation:test",
            "storage": "parquet",
            "status": "active",
            "market": "CN_A",
            "dataset": "valuation_metric",
            "granularity": "daily",
            "relative_path": "market=CN_A/dataset=valuation_metric/granularity=daily/partition-test.parquet",
            "path": str(
                columnar_root
                / "market=CN_A"
                / "dataset=valuation_metric"
                / "granularity=daily"
                / "partition-test.parquet"
            ),
            "row_count": 300,
        },
    ]
