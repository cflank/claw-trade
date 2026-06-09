#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence
from urllib.parse import urlparse

from claw_trade.data_gateway.warehouse import DatasetRepository

DATASETS = ("daily_bar", "valuation_metric")
MARKET = "CN_A"
GRANULARITY = "daily"
PROVIDER_ID = "local_a_share_factory_merge"
ENDPOINT_ID = "a_share_factory_seed_incremental_merge"
DEFAULT_SEED_ROOT = Path(".runtime/factory-seeds/a-share-cn-required-300td-20260608/normalized")
DEFAULT_INCREMENTAL_ROOT = Path(".runtime/dev-services/data-gateway/normalized")
REPLACE_COLLECTIONS = ("normalized_datasets", "raw_payloads", "provider_attempts", "dataset_manifests")


@dataclass(frozen=True)
class DatasetMergeSummary:
    dataset: str
    seed_parquet_files: int
    incremental_parquet_files: int
    output_path: str
    row_count: int
    symbol_count: int
    date_min: str
    date_max: str
    retained_trading_days: int
    retained_dates: tuple[str, ...]
    sha256: str
    field_set: tuple[str, ...]
    symbol_ids: tuple[str, ...]
    universe_refs: tuple[str, ...]
    dataset_ref_sample: tuple[str, ...]


@dataclass(frozen=True)
class MergeResult:
    seed_columnar_root: str
    incremental_columnar_root: str
    trading_days: int
    dry_run: bool
    metadata_replaced: bool
    datasets: dict[str, DatasetMergeSummary]
    mongo_imported: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "merge",
            "market": MARKET,
            "seed_columnar_root": self.seed_columnar_root,
            "incremental_columnar_root": self.incremental_columnar_root,
            "trading_days": self.trading_days,
            "dry_run": self.dry_run,
            "metadata_replaced": self.metadata_replaced,
            "normalized_storage": "parquet",
            "catalog_storage": "mongo",
            "normalized_datasets": 0,
            "mongo_imported": self.mongo_imported,
            "datasets": {
                dataset: {
                    "seed_parquet_files": summary.seed_parquet_files,
                    "incremental_parquet_files": summary.incremental_parquet_files,
                    "output_path": summary.output_path,
                    "row_count": summary.row_count,
                    "symbol_count": summary.symbol_count,
                    "date_min": summary.date_min,
                    "date_max": summary.date_max,
                    "retained_trading_days": summary.retained_trading_days,
                    "sha256": summary.sha256,
                }
                for dataset, summary in self.datasets.items()
            },
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge runtime A-share daily increments into the 300-trading-day factory seed.")
    parser.add_argument("--seed-columnar-root", type=Path, default=DEFAULT_SEED_ROOT)
    parser.add_argument("--incremental-columnar-root", type=Path, default=Path(os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT") or DEFAULT_INCREMENTAL_ROOT))
    parser.add_argument("--trading-days", type=int, default=300)
    parser.add_argument("--allow-incomplete-dataset-dates", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-merge", action="store_true")
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_SEED_MONGODB_URI")
        or os.environ.get("DATA_GATEWAY_MONGODB_URI")
        or os.environ.get("CN_A_MONGODB_URI")
        or "",
    )
    parser.add_argument(
        "--mongo-database",
        default=os.environ.get("DATA_GATEWAY_SEED_MONGODB_DATABASE")
        or os.environ.get("DATA_GATEWAY_MONGODB_DATABASE")
        or os.environ.get("CN_A_MONGODB_DATABASE")
        or "",
    )
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    if args.trading_days <= 0:
        raise SystemExit("--trading-days must be positive")
    if not args.dry_run and not args.confirm_merge:
        raise SystemExit("--confirm-merge is required unless --dry-run is set")
    if not args.dry_run and not args.mongo_uri:
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_SEED_MONGODB_URI/DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

    database = None
    repository = None
    database_name = ""
    if args.mongo_uri:
        from pymongo import MongoClient

        database_name = args.mongo_database or _database_name_from_uri(args.mongo_uri)
        client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
        database = client[database_name]
        repository = DatasetRepository.from_database(database)

    result = merge_factory_seed_incremental(
        seed_columnar_root=args.seed_columnar_root,
        incremental_columnar_root=args.incremental_columnar_root,
        trading_days=args.trading_days,
        repository=repository,
        database=database,
        dry_run=args.dry_run,
        require_matching_dataset_dates=not args.allow_incomplete_dataset_dates,
    )
    payload: dict[str, Any] = result.as_dict()
    if database_name:
        payload["mongo_database"] = database_name
    _emit(payload, args.output_json)
    return 0


def merge_factory_seed_incremental(
    *,
    seed_columnar_root: Path,
    incremental_columnar_root: Path,
    trading_days: int = 300,
    repository: DatasetRepository | None = None,
    database: Any | None = None,
    dry_run: bool = False,
    require_matching_dataset_dates: bool = True,
) -> MergeResult:
    if trading_days <= 0:
        raise ValueError("trading_days must be positive")
    seed_columnar_root = seed_columnar_root.resolve()
    incremental_columnar_root = incremental_columnar_root.resolve()
    _require_safe_seed_root(seed_columnar_root)

    with tempfile.TemporaryDirectory(prefix="claw-a-share-seed-merge-") as tmp:
        temp_root = Path(tmp) / "normalized"
        summaries = {
            dataset: _merge_dataset(
                dataset=dataset,
                seed_columnar_root=seed_columnar_root,
                incremental_columnar_root=incremental_columnar_root,
                output_root=temp_root,
                trading_days=trading_days,
            )
            for dataset in DATASETS
        }
        if require_matching_dataset_dates:
            _require_matching_date_windows(summaries)
        if not dry_run:
            _replace_dataset_dirs(seed_columnar_root=seed_columnar_root, merged_root=temp_root)
            records = _metadata_records(
                seed_columnar_root=seed_columnar_root,
                incremental_columnar_root=incremental_columnar_root,
                trading_days=trading_days,
                summaries=summaries,
            )
            if database is None or repository is None:
                raise ValueError("repository and database are required when dry_run is false")
            _clear_restore_collections(database)
            mongo_imported = _write_metadata(repository, records)
        else:
            mongo_imported = {"raw_payloads": 0, "provider_attempts": 0, "dataset_manifests": 0}

    return MergeResult(
        seed_columnar_root=str(seed_columnar_root),
        incremental_columnar_root=str(incremental_columnar_root),
        trading_days=trading_days,
        dry_run=dry_run,
        metadata_replaced=not dry_run,
        datasets=summaries,
        mongo_imported=mongo_imported,
    )


def _merge_dataset(
    *,
    dataset: str,
    seed_columnar_root: Path,
    incremental_columnar_root: Path,
    output_root: Path,
    trading_days: int,
) -> DatasetMergeSummary:
    seed_paths = _dataset_paths(seed_columnar_root, dataset)
    if not seed_paths:
        raise FileNotFoundError(f"missing seed Parquet for {dataset}: {seed_columnar_root}")
    incremental_paths = _dataset_paths(incremental_columnar_root, dataset)
    all_paths = (*seed_paths, *incremental_paths)
    target = _dataset_partition_path(output_root, dataset)
    target.parent.mkdir(parents=True, exist_ok=True)

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        con.execute(
            f"""
            CREATE TEMP TABLE combined AS
            SELECT *, 0 AS _source_priority FROM read_parquet({_duckdb_path_list(seed_paths)}, union_by_name=true)
            UNION ALL
            SELECT *, 1 AS _source_priority FROM read_parquet({_duckdb_path_list(incremental_paths)}, union_by_name=true)
            """
            if incremental_paths
            else f"""
            CREATE TEMP TABLE combined AS
            SELECT *, 0 AS _source_priority FROM read_parquet({_duckdb_path_list(seed_paths)}, union_by_name=true)
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE candidate_dates AS
            SELECT DISTINCT period_start AS trade_date
            FROM combined
            WHERE market = ? AND dataset = ? AND granularity = ? AND period_start IS NOT NULL AND period_start != ''
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [MARKET, dataset, GRANULARITY, trading_days],
        )
        retained_days = int(con.execute("SELECT COUNT(*) FROM candidate_dates").fetchone()[0] or 0)
        if retained_days == 0:
            raise ValueError(f"no trading dates found for {dataset}")
        retained_dates = tuple(
            str(row[0])
            for row in con.execute("SELECT trade_date FROM candidate_dates ORDER BY trade_date").fetchall()
        )
        con.execute(
            """
            CREATE TEMP TABLE selected AS
            SELECT dataset_ref, dataset, market, symbol_id, universe_ref, granularity, period_start, period_end,
                   field_set_json, as_of, fresh_until, source_roles_json, dataset_checksum,
                   dataset_checksum_algorithm, dataset_checksum_scope, dataset_row_count, row_json
            FROM (
                SELECT *,
                       row_number() OVER (
                         PARTITION BY market, dataset, granularity, coalesce(symbol_id, ''), period_start, period_end
                         ORDER BY _source_priority DESC, coalesce(as_of, '') DESC, dataset_ref DESC
                       ) AS rn
                FROM combined
                WHERE market = ? AND dataset = ? AND granularity = ?
                  AND period_start IN (SELECT trade_date FROM candidate_dates)
            )
            WHERE rn = 1
            ORDER BY symbol_id, period_start, dataset_ref
            """,
            [MARKET, dataset, GRANULARITY],
        )
        con.execute(f"COPY selected TO {_duckdb_string(target)} (FORMAT PARQUET)")
        row_count, symbol_count, date_min, date_max = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT symbol_id), MIN(period_start), MAX(period_end) FROM selected"
        ).fetchone()
        field_set = _merged_json_array_values(con, "field_set_json")
        universe_refs = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT DISTINCT universe_ref FROM selected WHERE universe_ref IS NOT NULL AND universe_ref != '' ORDER BY universe_ref"
            ).fetchall()
        )
        symbol_ids = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT DISTINCT symbol_id FROM selected WHERE symbol_id IS NOT NULL AND symbol_id != '' ORDER BY symbol_id"
            ).fetchall()
        )
        dataset_ref_sample = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT dataset_ref FROM selected WHERE dataset_ref IS NOT NULL AND dataset_ref != '' ORDER BY dataset_ref LIMIT 100"
            ).fetchall()
        )
    finally:
        con.close()

    return DatasetMergeSummary(
        dataset=dataset,
        seed_parquet_files=len(seed_paths),
        incremental_parquet_files=len(incremental_paths),
        output_path=str(_dataset_partition_path(seed_columnar_root, dataset)),
        row_count=int(row_count or 0),
        symbol_count=int(symbol_count or 0),
        date_min=str(date_min or ""),
        date_max=str(date_max or ""),
        retained_trading_days=retained_days,
        retained_dates=retained_dates,
        sha256=_file_sha256(target),
        field_set=field_set,
        symbol_ids=symbol_ids,
        universe_refs=universe_refs,
        dataset_ref_sample=dataset_ref_sample,
    )


def _dataset_paths(root: Path, dataset: str) -> tuple[Path, ...]:
    directory = root / f"market={MARKET}" / f"dataset={dataset}" / f"granularity={GRANULARITY}"
    if not directory.exists():
        return ()
    return tuple(sorted(path for path in directory.glob("*.parquet") if path.is_file()))


def _dataset_partition_path(root: Path, dataset: str) -> Path:
    return (
        root
        / f"market={MARKET}"
        / f"dataset={dataset}"
        / f"granularity={GRANULARITY}"
        / f"partition-factory-300td-{dataset}.parquet"
    )


def _require_matching_date_windows(summaries: Mapping[str, DatasetMergeSummary]) -> None:
    windows = {
        dataset: summary.retained_dates
        for dataset, summary in summaries.items()
    }
    if len(set(windows.values())) > 1:
        compact = {
            dataset: {
                "date_min": summary.date_min,
                "date_max": summary.date_max,
                "retained_trading_days": summary.retained_trading_days,
            }
            for dataset, summary in summaries.items()
        }
        raise ValueError(f"daily_bar and valuation_metric retained date windows differ: {compact}")


def _replace_dataset_dirs(*, seed_columnar_root: Path, merged_root: Path) -> None:
    for dataset in DATASETS:
        target_dir = seed_columnar_root / f"market={MARKET}" / f"dataset={dataset}" / f"granularity={GRANULARITY}"
        source_dir = merged_root / f"market={MARKET}" / f"dataset={dataset}" / f"granularity={GRANULARITY}"
        if not source_dir.exists():
            raise FileNotFoundError(source_dir)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir)


def _metadata_records(
    *,
    seed_columnar_root: Path,
    incremental_columnar_root: Path,
    trading_days: int,
    summaries: Mapping[str, DatasetMergeSummary],
) -> dict[str, tuple[dict[str, Any], ...]]:
    created_at = datetime.now(tz=UTC).isoformat()
    payload = {
        "seed_columnar_root": str(seed_columnar_root),
        "incremental_columnar_root": str(incremental_columnar_root),
        "trading_days": trading_days,
        "datasets": {
            dataset: {
                "row_count": summary.row_count,
                "symbol_count": summary.symbol_count,
                "date_min": summary.date_min,
                "date_max": summary.date_max,
                "retained_trading_days": summary.retained_trading_days,
                "incremental_parquet_files": summary.incremental_parquet_files,
            }
            for dataset, summary in summaries.items()
        },
    }
    digest = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    raw_ref = f"raw:{MARKET}:{PROVIDER_ID}:{digest[:12]}"
    attempt_ref = f"attempt:{PROVIDER_ID}:{ENDPOINT_ID}:{digest[:12]}"
    manifests = tuple(
        _manifest_for_summary(
            summary,
            seed_columnar_root=seed_columnar_root,
            created_at=created_at,
        )
        for summary in summaries.values()
    )
    return {
        "raw_payloads": (
            {
                "raw_ref": raw_ref,
                "metadata_ref": f"rawmeta:{MARKET}:{PROVIDER_ID}:{digest[:12]}",
                "provider": PROVIDER_ID,
                "endpoint": ENDPOINT_ID,
                "market": MARKET,
                "payload_hash": f"sha256:{digest}",
                "object_uri": None,
                "storage_mode": "metadata_only",
                "payload_size_bytes": len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")),
                "license_policy": {"raw_storage_mode": "metadata_only", "normalized_storage_allowed": True},
                "redaction_meta": {"payload_body_stored": False, "mongo_body_stored": False},
                "created_at": created_at,
                "payload_summary": payload,
            },
        ),
        "provider_attempts": (
            {
                "attempt_ref": attempt_ref,
                "provider": PROVIDER_ID,
                "endpoint": ENDPOINT_ID,
                "status": "local_seed_merged",
                "remote_attempted": False,
                "remote_success": False,
                "dataset_refs": tuple(ref for summary in summaries.values() for ref in summary.dataset_ref_sample[:50]),
                "dataset_ref_count": sum(summary.row_count for summary in summaries.values()),
                "dataset_ref_prefixes": tuple(f"dataset:{dataset}:{MARKET}:" for dataset in DATASETS),
                "raw_refs": (raw_ref,),
                "gap_codes": (),
                "source_metadata": payload,
                "created_at": created_at,
            },
        ),
        "dataset_manifests": manifests,
    }


def _manifest_for_summary(
    summary: DatasetMergeSummary,
    *,
    seed_columnar_root: Path,
    created_at: str,
) -> dict[str, Any]:
    relative_path = str(Path(summary.output_path).relative_to(seed_columnar_root))
    return {
        "manifest_ref": f"manifest:{MARKET}:{summary.dataset}:factory-300td-merged",
        "created_at": created_at,
        "dataset": summary.dataset,
        "dataset_ref_count": summary.row_count,
        "dataset_ref_prefixes": (f"dataset:{summary.dataset}:{MARKET}:",),
        "dataset_refs": (),
        "dataset_refs_sample": summary.dataset_ref_sample[:20],
        "dataset_refs_sha256": None,
        "dataset_refs_truncated": True,
        "field_set": summary.field_set,
        "granularity": GRANULARITY,
        "hash_algorithm": "sha256",
        "market": MARKET,
        "partition_id": f"factory-300td-{summary.dataset}",
        "path": summary.output_path,
        "period_end_max": summary.date_max,
        "period_start_min": summary.date_min,
        "relative_path": relative_path,
        "row_count": summary.row_count,
        "sha256": summary.sha256,
        "source_summary": f"factory_seed:{PROVIDER_ID}:300td_incremental_merge",
        "status": "active",
        "storage": "parquet",
        "symbol_ids": summary.symbol_ids,
        "universe_refs": summary.universe_refs,
    }


def _write_metadata(
    repository: DatasetRepository,
    records_by_collection: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, int]:
    for record in records_by_collection.get("raw_payloads", ()):
        repository.insert_raw_payload(record)
    for record in records_by_collection.get("provider_attempts", ()):
        repository.insert_provider_attempt(record)
    for record in records_by_collection.get("dataset_manifests", ()):
        repository.write_dataset_manifest(record)
    return {
        "raw_payloads": len(records_by_collection.get("raw_payloads", ())),
        "provider_attempts": len(records_by_collection.get("provider_attempts", ())),
        "dataset_manifests": len(records_by_collection.get("dataset_manifests", ())),
    }


def _clear_restore_collections(database: Any) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for name in REPLACE_COLLECTIONS:
        collection = database[name]
        if isinstance(collection, MutableMapping):
            deleted[name] = len(collection)
            collection.clear()
            continue
        deleter = getattr(collection, "delete_many", None)
        if not callable(deleter):
            raise TypeError(f"collection {name} does not support delete_many")
        result = deleter({})
        deleted[name] = int(getattr(result, "deleted_count", 0))
    return deleted


def _merged_json_array_values(con: Any, column: str) -> tuple[str, ...]:
    values: set[str] = set()
    for (raw,) in con.execute(f"SELECT DISTINCT {column} FROM selected WHERE {column} IS NOT NULL AND {column} != ''").fetchall():
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            values.update(str(item) for item in parsed if str(item).strip())
    return tuple(sorted(values))


def _require_safe_seed_root(root: Path) -> None:
    resolved = root.resolve()
    if str(resolved) in {resolved.anchor, str(Path.home().resolve())} or len(resolved.parts) < 3:
        raise ValueError(f"refusing unsafe seed root: {root}")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duckdb_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _duckdb_path_list(paths: Sequence[Path]) -> str:
    if not paths:
        raise ValueError("read_parquet path list cannot be empty")
    return "[" + ", ".join(_duckdb_string(path) for path in paths) + "]"


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade_a_share_factory_seed"


def _emit(payload: Mapping[str, Any], target: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    raise SystemExit(main())
