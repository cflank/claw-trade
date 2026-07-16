from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
from hashlib import sha256
from pathlib import Path

import duckdb
import pytest

from claw_trade.data_gateway import factory_seed_integrity
from claw_trade.data_gateway.warehouse import DatasetRepository


def _load_cli_module():
    script_path = Path(__file__).parents[3] / "scripts" / "selection" / "restore_a_share_factory_seed.py"
    spec = importlib.util.spec_from_file_location("restore_a_share_factory_seed", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _collections() -> dict[str, dict[str, object]]:
    return {name: {} for name in DatasetRepository.collection_names()}


def _write_package(
    tmp_path: Path,
    *,
    unsafe_member: str | None = None,
    company_name: str | None = "平安银行",
    missing_parquet_manifest: bool = False,
) -> tuple[Path, Path]:
    package_root = tmp_path / "pkg"
    normalized_root = package_root / "data" / "normalized"
    normalized_path = normalized_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily"
    valuation_path = normalized_root / "market=CN_A" / "dataset=valuation_metric" / "granularity=daily"
    mongo_path = package_root / "mongo"
    normalized_path.mkdir(parents=True)
    valuation_path.mkdir(parents=True)
    mongo_path.mkdir(parents=True)
    (package_root / "seed_manifest.json").write_text(
        json.dumps(
            {
                "package_id": "test-a-share-factory-seed",
                "package_type": "factory_seed_rebuild_pack",
                "datasets": {"daily_bar": {"rows": 1}},
            }
        ),
        encoding="utf-8",
    )
    _write_daily_bar_parquet(normalized_path / "partition-test.parquet", company_name=company_name)
    _write_valuation_parquet(valuation_path / "partition-test.parquet")
    (mongo_path / "raw_payloads.jsonl").write_text(
        json.dumps({"raw_ref": "raw:test", "provider": "local_a_share_required"}) + "\n",
        encoding="utf-8",
    )
    (mongo_path / "provider_attempts.jsonl").write_text(
        json.dumps(
            {
                "attempt_ref": "attempt:test",
                "provider": "local_a_share_required",
                "message": "unicode line separator: \u2028",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (mongo_path / "dataset_manifests.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {
                    "manifest_ref": "manifest:test",
                    "storage": "parquet",
                    "status": "active",
                    "market": "CN_A",
                    "dataset": "daily_bar",
                    "granularity": "daily",
                    "relative_path": "market=CN_A/dataset=daily_bar/granularity=daily/partition-test.parquet",
                    "path": ".runtime/dev-services/data-gateway/normalized/market=CN_A/dataset=daily_bar/granularity=daily/partition-test.parquet",
                },
                {
                    "manifest_ref": "manifest:valuation:test",
                    "storage": "parquet",
                    "status": "active",
                    "market": "CN_A",
                    "dataset": "valuation_metric",
                    "granularity": "daily",
                    "relative_path": "market=CN_A/dataset=valuation_metric/granularity=daily/partition-test.parquet",
                    "path": ".runtime/dev-services/data-gateway/normalized/market=CN_A/dataset=valuation_metric/granularity=daily/partition-test.parquet",
                },
                *(
                    [
                        {
                            "manifest_ref": "manifest:missing:test",
                            "storage": "parquet",
                            "status": "active",
                            "market": "CN_A",
                            "dataset": "daily_bar",
                            "granularity": "daily",
                            "relative_path": "market=CN_A/dataset=daily_bar/granularity=daily/missing.parquet",
                        }
                    ]
                    if missing_parquet_manifest
                    else []
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    tar_path = tmp_path / "factory.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(package_root, arcname="test-a-share-factory-seed")
        if unsafe_member is not None:
            info = tarfile.TarInfo(unsafe_member)
            payload = b"bad"
            info.size = len(payload)
            archive.addfile(info, fileobj=_BytesReader(payload))
    checksum_path = tmp_path / "factory.tar.sha256"
    checksum_path.write_text(f"{sha256(tar_path.read_bytes()).hexdigest()}  factory.tar\n", encoding="utf-8")
    return tar_path, checksum_path


def _write_daily_bar_parquet(path: Path, *, company_name: str | None) -> None:
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
            from range(300) as history(i)
            """,
            [json.dumps(row, ensure_ascii=False)],
        )
        conn.execute(f"COPY daily_bar TO '{path.as_posix()}' (FORMAT PARQUET)")


def _write_valuation_parquet(path: Path) -> None:
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
            from range(300) as history(i)
            """
        )
        conn.execute(f"COPY valuation_metric TO '{path.as_posix()}' (FORMAT PARQUET)")


class _BytesReader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        start = self._offset
        end = min(len(self._payload), start + size)
        self._offset = end
        return self._payload[start:end]


def test_restore_factory_seed_extracts_parquet_and_imports_only_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path)
    collections = _collections()
    repository = DatasetRepository(collections=collections)
    columnar_root = tmp_path / "normalized"

    result = cli.restore_factory_seed(
        package_path=tar_path,
        checksum_path=checksum_path,
        repository=repository,
        columnar_root=columnar_root,
    )

    payload = result.as_dict()
    assert payload["package_id"] == "test-a-share-factory-seed"
    assert payload["parquet_files"] == 2
    assert payload["mongo_imported"] == {
        "raw_payloads": 1,
        "provider_attempts": 1,
        "dataset_manifests": 2,
    }
    assert payload["normalized_datasets"] == 0
    assert payload["cleared_existing"] == {}
    restored_parquet = (
        columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-test.parquet"
    )
    assert restored_parquet.is_file()
    with duckdb.connect(":memory:") as conn:
        assert conn.execute(
            "select json_extract_string(row_json, ?) from read_parquet(?)",
            ["$.company_name", str(restored_parquet)],
        ).fetchone() == ("平安银行",)
    assert "raw:test" in collections["raw_payloads"]
    assert "attempt:test" in collections["provider_attempts"]
    assert "manifest:test" in collections["dataset_manifests"]
    assert "manifest:valuation:test" in collections["dataset_manifests"]
    assert collections["dataset_manifests"]["manifest:test"]["path"] == str(
        columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "partition-test.parquet"
    )
    assert collections["normalized_datasets"] == {}


def test_restore_factory_seed_rejects_checksum_mismatch(tmp_path: Path) -> None:
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path)
    checksum_path.write_text("0" * 64 + "  factory.tar\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        cli.restore_factory_seed(
            package_path=tar_path,
            checksum_path=checksum_path,
            repository=DatasetRepository(collections=_collections()),
            columnar_root=tmp_path / "normalized",
        )


def test_restore_factory_seed_rejects_cn_a_daily_bar_without_company_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path, company_name=None)

    with pytest.raises(ValueError, match="company_name missing"):
        cli.restore_factory_seed(
            package_path=tar_path,
            checksum_path=checksum_path,
            repository=DatasetRepository(collections=_collections()),
            columnar_root=tmp_path / "normalized",
        )


def test_restore_factory_seed_rejects_unsafe_archive_member(tmp_path: Path) -> None:
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path, unsafe_member="../escape.parquet")

    with pytest.raises(ValueError, match="unsafe tar member path"):
        cli.restore_factory_seed(
            package_path=tar_path,
            checksum_path=checksum_path,
            repository=DatasetRepository(collections=_collections()),
            columnar_root=tmp_path / "normalized",
        )


def test_restore_replace_existing_clears_seed_storage_collections_and_columnar_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path)
    collections = _collections()
    collections["normalized_datasets"]["dataset:old"] = {"dataset_ref": "dataset:old"}
    collections["raw_payloads"]["raw:old"] = {"raw_ref": "raw:old"}
    collections["provider_attempts"]["attempt:old"] = {"attempt_ref": "attempt:old"}
    collections["dataset_manifests"]["manifest:old"] = {"manifest_ref": "manifest:old"}
    collections["maintenance_jobs"]["job:keep"] = {"job_id": "job:keep"}
    columnar_root = tmp_path / "normalized"
    stale_partition = columnar_root / "market=CN_A" / "dataset=company_news" / "granularity=event" / "old.parquet"
    stale_partition.parent.mkdir(parents=True)
    stale_partition.write_bytes(b"old")

    result = cli.restore_factory_seed(
        package_path=tar_path,
        checksum_path=checksum_path,
        repository=DatasetRepository(collections=collections),
        database=collections,
        columnar_root=columnar_root,
        replace_existing=True,
    )

    payload = result.as_dict()

    assert payload["cleared_existing"]["mongo"] == {
        "normalized_datasets": 1,
        "raw_payloads": 1,
        "provider_attempts": 1,
        "dataset_manifests": 1,
    }
    assert payload["cleared_existing"]["columnar_root"] == {
        "files": 1,
        "dirs": 3,
        "bytes": 3,
    }
    assert collections["normalized_datasets"] == {}
    assert "raw:old" not in collections["raw_payloads"]
    assert "attempt:old" not in collections["provider_attempts"]
    assert "manifest:old" not in collections["dataset_manifests"]
    assert "raw:test" in collections["raw_payloads"]
    assert "attempt:test" in collections["provider_attempts"]
    assert "manifest:test" in collections["dataset_manifests"]
    assert "manifest:valuation:test" in collections["dataset_manifests"]
    assert not stale_partition.exists()
    assert (
        columnar_root
        / "market=CN_A"
        / "dataset=daily_bar"
        / "granularity=daily"
        / "partition-test.parquet"
    ).exists()
    assert collections["maintenance_jobs"] == {"job:keep": {"job_id": "job:keep"}}


def test_restore_replace_existing_validates_before_clearing_current_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path, company_name=None)
    collections = _collections()
    collections["raw_payloads"]["raw:old"] = {"raw_ref": "raw:old"}
    collections["dataset_manifests"]["manifest:old"] = {"manifest_ref": "manifest:old"}
    columnar_root = tmp_path / "normalized"
    old_partition = columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "old.parquet"
    old_partition.parent.mkdir(parents=True)
    old_partition.write_bytes(b"old-seed")

    with pytest.raises(ValueError, match="company_name missing"):
        cli.restore_factory_seed(
            package_path=tar_path,
            checksum_path=checksum_path,
            repository=DatasetRepository(collections=collections),
            database=collections,
            columnar_root=columnar_root,
            replace_existing=True,
        )

    assert collections["raw_payloads"] == {"raw:old": {"raw_ref": "raw:old"}}
    assert collections["dataset_manifests"] == {"manifest:old": {"manifest_ref": "manifest:old"}}
    assert old_partition.read_bytes() == b"old-seed"


def test_restore_replace_existing_rejects_missing_manifest_target_before_clearing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_seed_integrity, "_MIN_FULL_MARKET_SYMBOLS", 1)
    cli = _load_cli_module()
    tar_path, checksum_path = _write_package(tmp_path, missing_parquet_manifest=True)
    collections = _collections()
    collections["raw_payloads"]["raw:old"] = {"raw_ref": "raw:old"}
    columnar_root = tmp_path / "normalized"
    old_partition = columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily" / "old.parquet"
    old_partition.parent.mkdir(parents=True)
    old_partition.write_bytes(b"old-seed")

    with pytest.raises(ValueError, match="active manifest records point to missing Parquet"):
        cli.restore_factory_seed(
            package_path=tar_path,
            checksum_path=checksum_path,
            repository=DatasetRepository(collections=collections),
            database=collections,
            columnar_root=columnar_root,
            replace_existing=True,
        )

    assert collections["raw_payloads"] == {"raw:old": {"raw_ref": "raw:old"}}
    assert old_partition.read_bytes() == b"old-seed"
