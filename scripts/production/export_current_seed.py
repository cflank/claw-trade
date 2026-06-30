#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

MONGO_JSONL_COLLECTIONS = ("raw_payloads", "provider_attempts", "dataset_manifests")
COLLECTION_KEY_FIELDS = {
    "raw_payloads": "raw_ref",
    "provider_attempts": "attempt_ref",
    "dataset_manifests": "manifest_ref",
}


@dataclass(frozen=True)
class ExportResult:
    package_id: str
    package_path: str
    checksum_path: str
    columnar_root: str
    parquet_files: int
    parquet_bytes: int
    mongo_exported: dict[str, int]
    seed_manifest: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "export",
            "package_id": self.package_id,
            "package_path": self.package_path,
            "checksum_path": self.checksum_path,
            "columnar_root": self.columnar_root,
            "normalized_storage": "parquet",
            "catalog_storage": "mongo_jsonl",
            "parquet_files": self.parquet_files,
            "parquet_bytes": self.parquet_bytes,
            "mongo_exported": self.mongo_exported,
            "seed_manifest": self.seed_manifest,
        }


def main(argv: Sequence[str] | None = None) -> int:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", type=Path)
    pre_args, _remaining = pre_parser.parse_known_args(argv)
    if pre_args.env_file is not None:
        _load_env_file(pre_args.env_file)

    today = datetime.now(UTC).strftime("%Y%m%d")
    default_output = Path("data") / f"current-seed-{today}.tar"

    parser = argparse.ArgumentParser(
        parents=[pre_parser],
        description="Export the current Mongo catalog and Parquet warehouse into a current-seed package.",
    )
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--package-id", default=None)
    parser.add_argument("--replace-output", action="store_true")
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_MONGODB_URI")
        or os.environ.get("DATA_GATEWAY_SEED_MONGODB_URI")
        or os.environ.get("CN_A_MONGODB_URI")
        or "",
    )
    parser.add_argument(
        "--mongo-database",
        default=os.environ.get("DATA_GATEWAY_MONGODB_DATABASE")
        or os.environ.get("DATA_GATEWAY_SEED_MONGODB_DATABASE")
        or os.environ.get("CN_A_MONGODB_DATABASE")
        or "",
    )
    parser.add_argument(
        "--columnar-root",
        type=Path,
        default=Path(os.environ["DATA_GATEWAY_COLUMNAR_ROOT"]) if os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT") else None,
    )
    parser.add_argument(
        "--include-inactive-manifests",
        action="store_true",
        help="Export inactive dataset manifests too. Default exports only active manifests.",
    )
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    if not args.mongo_uri:
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI")
    if args.columnar_root is None:
        raise SystemExit("missing --columnar-root or DATA_GATEWAY_COLUMNAR_ROOT")

    from pymongo import MongoClient

    database_name = args.mongo_database or _database_name_from_uri(args.mongo_uri)
    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    database = client[database_name]
    records_by_collection = _load_mongo_records(
        database,
        include_inactive_manifests=args.include_inactive_manifests,
    )
    result = export_current_seed_package(
        output_path=args.output,
        package_id=args.package_id or args.output.stem,
        columnar_root=args.columnar_root,
        records_by_collection=records_by_collection,
        replace_output=args.replace_output,
    )
    payload = {"mongo_database": database_name, **result.as_dict()}
    _emit(payload, args.output_json)
    return 0


def export_current_seed_package(
    *,
    output_path: Path,
    package_id: str,
    columnar_root: Path,
    records_by_collection: Mapping[str, Sequence[Mapping[str, Any]]],
    replace_output: bool = False,
    created_at: datetime | None = None,
) -> ExportResult:
    package_id = _validate_package_id(package_id)
    output_path = output_path.resolve()
    checksum_path = _default_checksum_path(output_path)
    columnar_root = columnar_root.resolve()
    if not columnar_root.is_dir():
        raise FileNotFoundError(f"columnar root does not exist or is not a directory: {columnar_root}")
    if output_path.exists() and not replace_output:
        raise FileExistsError(f"output already exists: {output_path}; pass --replace-output to overwrite")

    normalized_records = {
        collection: _normalize_records(records_by_collection.get(collection, ()), collection=collection)
        for collection in MONGO_JSONL_COLLECTIONS
    }
    normalized_records["dataset_manifests"] = _normalize_manifest_paths(
        normalized_records["dataset_manifests"],
        columnar_root=columnar_root,
    )
    _verify_manifest_files_exist(normalized_records["dataset_manifests"], columnar_root=columnar_root)
    parquet_files = _collect_parquet_files(columnar_root)
    if not parquet_files:
        raise ValueError(f"columnar root contains no Parquet files: {columnar_root}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="current-seed-export-", dir=str(output_path.parent)) as tmp_name:
        tmp_dir = Path(tmp_name)
        package_root = tmp_dir / package_id
        package_root.mkdir(parents=True)
        mongo_root = package_root / "mongo"
        data_root = package_root / "data" / "normalized"
        mongo_root.mkdir(parents=True)
        data_root.mkdir(parents=True)

        parquet_count, parquet_bytes = _copy_parquet_files(
            parquet_files=parquet_files,
            columnar_root=columnar_root,
            target_root=data_root,
        )
        for collection, records in normalized_records.items():
            _write_jsonl(mongo_root / f"{collection}.jsonl", records)

        seed_manifest = _build_seed_manifest(
            package_id=package_id,
            created_at=created_at or datetime.now(UTC),
            records_by_collection=normalized_records,
            parquet_files=parquet_count,
            parquet_bytes=parquet_bytes,
        )
        (package_root / "seed_manifest.json").write_text(
            json.dumps(seed_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (package_root / "README.txt").write_text(
            "current factory seed: Mongo metadata JSONL + normalized Parquet files\n",
            encoding="utf-8",
        )

        tmp_archive = tmp_dir / output_path.name
        with tarfile.open(tmp_archive, "w") as archive:
            archive.add(package_root, arcname=package_id)
        if output_path.exists():
            output_path.unlink()
        shutil.move(str(tmp_archive), output_path)

    checksum = _file_sha256(output_path)
    checksum_path.write_text(f"{checksum}  {output_path.name}\n", encoding="utf-8")
    return ExportResult(
        package_id=package_id,
        package_path=str(output_path),
        checksum_path=str(checksum_path),
        columnar_root=str(columnar_root),
        parquet_files=parquet_count,
        parquet_bytes=parquet_bytes,
        mongo_exported={collection: len(records) for collection, records in normalized_records.items()},
        seed_manifest=seed_manifest,
    )


def _load_mongo_records(
    database: Any,
    *,
    include_inactive_manifests: bool,
) -> dict[str, tuple[dict[str, Any], ...]]:
    records: dict[str, tuple[dict[str, Any], ...]] = {}
    for collection in MONGO_JSONL_COLLECTIONS:
        criteria: dict[str, Any] = {}
        if collection == "dataset_manifests" and not include_inactive_manifests:
            criteria = {"status": "active"}
        rows = tuple(dict(row) for row in database[collection].find(criteria))
        records[collection] = _normalize_records(rows, collection=collection)
    return records


def _normalize_records(
    records: Sequence[Mapping[str, Any]],
    *,
    collection: str,
) -> tuple[dict[str, Any], ...]:
    normalized = tuple(_json_safe_document(record) for record in records)
    return tuple(sorted(normalized, key=lambda record: _record_sort_key(record, collection=collection)))


def _json_safe_document(record: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(record)
    document.pop("_id", None)
    safe = _json_safe_value(document)
    if not isinstance(safe, dict):
        raise TypeError("expected JSON object after normalization")
    return safe


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _record_sort_key(record: Mapping[str, Any], *, collection: str) -> tuple[str, str]:
    key_field = COLLECTION_KEY_FIELDS.get(collection, "")
    key = str(record.get(key_field) or "")
    fallback = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return (key, fallback)


def _normalize_manifest_paths(
    records: Sequence[Mapping[str, Any]],
    *,
    columnar_root: Path,
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    root = columnar_root.resolve()
    for record in records:
        doc = dict(record)
        if doc.get("storage") == "parquet":
            relative_path = str(doc.get("relative_path") or "").strip()
            if not relative_path:
                absolute_path = Path(str(doc.get("path") or "")).expanduser()
                try:
                    relative_path = str(absolute_path.resolve().relative_to(root))
                except ValueError as exc:
                    raise ValueError(f"manifest has parquet path outside columnar root: {doc.get('manifest_ref')}") from exc
                doc["relative_path"] = relative_path
        normalized.append(doc)
    return tuple(normalized)


def _verify_manifest_files_exist(
    records: Sequence[Mapping[str, Any]],
    *,
    columnar_root: Path,
) -> None:
    missing: list[str] = []
    for record in records:
        if record.get("storage") != "parquet":
            continue
        relative_path = str(record.get("relative_path") or "").strip()
        if not relative_path:
            missing.append(f"{record.get('manifest_ref')}: missing relative_path")
            continue
        if PurePosixPath(relative_path).is_absolute() or ".." in PurePosixPath(relative_path).parts:
            missing.append(f"{record.get('manifest_ref')}: unsafe relative_path {relative_path}")
            continue
        if not (columnar_root / Path(relative_path)).is_file():
            missing.append(f"{record.get('manifest_ref')}: {relative_path}")
    if missing:
        sample = "; ".join(missing[:10])
        suffix = "" if len(missing) <= 10 else f"; ... and {len(missing) - 10} more"
        raise ValueError(f"dataset manifest points to missing Parquet file(s): {sample}{suffix}")


def _collect_parquet_files(columnar_root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in columnar_root.rglob("*.parquet") if path.is_file()))


def _copy_parquet_files(
    *,
    parquet_files: Sequence[Path],
    columnar_root: Path,
    target_root: Path,
) -> tuple[int, int]:
    total_bytes = 0
    for source in parquet_files:
        relative = source.relative_to(columnar_root)
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        total_bytes += target.stat().st_size
    return len(parquet_files), total_bytes


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, default=str))
            handle.write("\n")


def _build_seed_manifest(
    *,
    package_id: str,
    created_at: datetime,
    records_by_collection: Mapping[str, Sequence[Mapping[str, Any]]],
    parquet_files: int,
    parquet_bytes: int,
) -> dict[str, Any]:
    dataset_summary: dict[str, dict[str, dict[str, Any]]] = {}
    markets: set[str] = set()
    for record in records_by_collection.get("dataset_manifests", ()):
        market = str(record.get("market") or "UNKNOWN")
        dataset = str(record.get("dataset") or "UNKNOWN")
        markets.add(market)
        market_summary = dataset_summary.setdefault(market, {})
        item = market_summary.setdefault(dataset, {"manifests": 0, "rows": 0, "granularities": []})
        item["manifests"] += 1
        row_count = record.get("row_count")
        if isinstance(row_count, int | float):
            item["rows"] += int(row_count)
        granularity = str(record.get("granularity") or "").strip()
        if granularity and granularity not in item["granularities"]:
            item["granularities"].append(granularity)

    for market_summary in dataset_summary.values():
        for item in market_summary.values():
            item["granularities"] = sorted(item["granularities"])

    return {
        "package_id": package_id,
        "package_type": "current_factory_seed",
        "created_at": created_at.isoformat(),
        "markets": sorted(markets),
        "datasets": dataset_summary,
        "raw_payload_count": len(records_by_collection.get("raw_payloads", ())),
        "provider_attempt_count": len(records_by_collection.get("provider_attempts", ())),
        "dataset_manifest_count": len(records_by_collection.get("dataset_manifests", ())),
        "parquet_files": parquet_files,
        "parquet_bytes": parquet_bytes,
    }


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_checksum_path(package_path: Path) -> Path:
    return package_path.with_name(package_path.name + ".sha256")


def _validate_package_id(package_id: str) -> str:
    cleaned = package_id.strip()
    if not cleaned:
        raise ValueError("package_id is empty")
    path = PurePosixPath(cleaned)
    if path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
        raise ValueError(f"unsafe package_id: {package_id}")
    return cleaned


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"env file does not exist: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _emit(payload: Mapping[str, Any], target: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    raise SystemExit(main())
