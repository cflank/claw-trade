#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, MutableMapping, Sequence
from urllib.parse import urlparse

from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse

DEFAULT_PACKAGE = Path("data/current-seed-20260626.tar")
MONGO_JSONL_COLLECTIONS = ("raw_payloads", "provider_attempts", "dataset_manifests")
REPLACE_COLLECTIONS = ("normalized_datasets", *MONGO_JSONL_COLLECTIONS)


@dataclass(frozen=True)
class RestoreResult:
    package_id: str
    package_path: str
    columnar_root: str
    parquet_files: int
    parquet_bytes: int
    mongo_imported: dict[str, int]
    seed_manifest: dict[str, Any]
    cleared_existing: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "restore",
            "package_id": self.package_id,
            "package_path": self.package_path,
            "columnar_root": self.columnar_root,
            "normalized_storage": "parquet",
            "catalog_storage": "mongo",
            "parquet_files": self.parquet_files,
            "parquet_bytes": self.parquet_bytes,
            "mongo_imported": self.mongo_imported,
            "normalized_datasets": 0,
            "cleared_existing": self.cleared_existing,
            "seed_manifest": self.seed_manifest,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore the clean A-share factory seed package into runtime storage.")
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--checksum", type=Path, default=None)
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
    parser.add_argument(
        "--columnar-root",
        type=Path,
        default=Path(os.environ["DATA_GATEWAY_COLUMNAR_ROOT"]) if os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT") else None,
    )
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--confirm-replace-existing", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    if args.replace_existing and not args.confirm_replace_existing:
        raise SystemExit("--replace-existing requires --confirm-replace-existing")
    if not args.mongo_uri:
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_SEED_MONGODB_URI/DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

    from pymongo import MongoClient

    database_name = args.mongo_database or _database_name_from_uri(args.mongo_uri)
    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    database = client[database_name]
    columnar_root = (
        args.columnar_root
        if args.columnar_root is not None
        else _default_factory_columnar_root(args.package)
    )
    repository = DatasetRepository.from_database(database)
    result = restore_factory_seed(
        package_path=args.package,
        checksum_path=args.checksum,
        repository=repository,
        database=database,
        columnar_root=NormalizedColumnarWarehouse.from_env(root=columnar_root).root,
        replace_existing=args.replace_existing,
    )
    payload = {"mongo_database": database_name, **result.as_dict()}
    _emit(payload, args.output_json)
    return 0


def restore_factory_seed(
    *,
    package_path: Path,
    checksum_path: Path | None,
    repository: DatasetRepository,
    database: Any | None = None,
    columnar_root: Path,
    replace_existing: bool = False,
) -> RestoreResult:
    package_path = package_path.resolve()
    if checksum_path is None:
        checksum_path = _default_checksum_path(package_path)
    _verify_package_checksum(package_path, checksum_path)

    with tarfile.open(package_path, "r") as archive:
        members = tuple(archive.getmembers())
        prefix = _validate_archive_members(members)
        seed_manifest = _read_json_member(archive, f"{prefix}/seed_manifest.json")
        package_id = str(seed_manifest.get("package_id") or prefix)
        mongo_records = {
            collection: _read_jsonl_member(archive, f"{prefix}/mongo/{collection}.jsonl")
            for collection in MONGO_JSONL_COLLECTIONS
        }
        mongo_records["dataset_manifests"] = _rebase_manifest_paths(
            mongo_records["dataset_manifests"],
            columnar_root=columnar_root,
        )
        cleared_existing: dict[str, Any] = {}
        if replace_existing:
            if database is None:
                raise ValueError("replace_existing requires database")
            cleared_existing = {
                "mongo": _clear_restore_collections(database),
                "columnar_root": _clear_columnar_root(columnar_root),
            }
        parquet_files, parquet_bytes = _extract_columnar_files(
            archive=archive,
            members=members,
            prefix=prefix,
            columnar_root=columnar_root,
        )

    imported = _import_mongo_jsonl(repository, mongo_records)
    return RestoreResult(
        package_id=package_id,
        package_path=str(package_path),
        columnar_root=str(columnar_root),
        parquet_files=parquet_files,
        parquet_bytes=parquet_bytes,
        mongo_imported=imported,
        seed_manifest=seed_manifest,
        cleared_existing=cleared_existing,
    )


def _verify_package_checksum(package_path: Path, checksum_path: Path) -> None:
    if not package_path.exists():
        raise FileNotFoundError(package_path)
    if not checksum_path.exists():
        raise FileNotFoundError(checksum_path)
    expected = _expected_sha256(checksum_path)
    actual = _file_sha256(package_path)
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {package_path}: expected {expected}, got {actual}")


def _default_checksum_path(package_path: Path) -> Path:
    candidate = package_path.with_suffix(package_path.suffix + ".sha256")
    if candidate.exists():
        return candidate
    return package_path.with_name(package_path.name + ".sha256")


def _default_factory_columnar_root(package_path: Path) -> Path:
    return Path(".runtime/factory-seeds") / package_path.stem / "normalized"


def _expected_sha256(checksum_path: Path) -> str:
    text = checksum_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"empty checksum file: {checksum_path}")
    value = text.split()[0].strip().lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"invalid checksum file: {checksum_path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_archive_members(members: Sequence[tarfile.TarInfo]) -> str:
    prefixes: set[str] = set()
    required = {
        "seed_manifest.json",
        "mongo/raw_payloads.jsonl",
        "mongo/provider_attempts.jsonl",
        "mongo/dataset_manifests.jsonl",
    }
    found: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe tar member path: {member.name}")
        if len(path.parts) < 2:
            continue
        prefixes.add(path.parts[0])
        relative = PurePosixPath(*path.parts[1:])
        if str(relative) in required:
            found.add(str(relative))
        if relative.parts[:2] == ("data", "normalized"):
            if member.isfile() and path.suffix != ".parquet":
                raise ValueError(f"unexpected file under data/normalized: {member.name}")
            continue
        if relative.parts[:1] == ("mongo",):
            if member.isfile() and path.name not in {f"{collection}.jsonl" for collection in MONGO_JSONL_COLLECTIONS}:
                raise ValueError(f"unexpected mongo metadata file: {member.name}")
            continue
        if member.isfile() and str(relative) not in {"seed_manifest.json", "README.txt"}:
            raise ValueError(f"unexpected file in factory package: {member.name}")
    if len(prefixes) != 1:
        raise ValueError(f"factory package must contain exactly one top-level directory, found {sorted(prefixes)}")
    missing = required - found
    if missing:
        raise ValueError(f"factory package missing required file(s): {sorted(missing)}")
    return next(iter(prefixes))


def _extract_columnar_files(
    *,
    archive: tarfile.TarFile,
    members: Sequence[tarfile.TarInfo],
    prefix: str,
    columnar_root: Path,
) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    source_root = PurePosixPath(prefix) / "data" / "normalized"
    for member in members:
        if not member.isfile():
            continue
        source_path = PurePosixPath(member.name)
        if source_path.parts[: len(source_root.parts)] != source_root.parts:
            continue
        relative = PurePosixPath(*source_path.parts[len(source_root.parts) :])
        if not relative.parts or relative.suffix != ".parquet":
            continue
        target = columnar_root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"cannot read tar member: {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        count += 1
        total_bytes += target.stat().st_size
    if count == 0:
        raise ValueError("factory package contains no Parquet files")
    return count, total_bytes


def _read_json_member(archive: tarfile.TarFile, member_name: str) -> dict[str, Any]:
    handle = archive.extractfile(member_name)
    if handle is None:
        raise ValueError(f"cannot read tar member: {member_name}")
    with handle:
        payload = json.loads(handle.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {member_name}")
    return payload


def _read_jsonl_member(archive: tarfile.TarFile, member_name: str) -> tuple[dict[str, Any], ...]:
    handle = archive.extractfile(member_name)
    if handle is None:
        raise ValueError(f"cannot read tar member: {member_name}")
    records: list[dict[str, Any]] = []
    with handle:
        for line in handle.read().decode("utf-8").split("\n"):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"expected JSON object line in {member_name}")
            records.append(record)
    return tuple(records)


def _import_mongo_jsonl(
    repository: DatasetRepository,
    records_by_collection: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, int]:
    imported: dict[str, int] = {}
    for record in records_by_collection.get("raw_payloads", ()):
        repository.insert_raw_payload(record)
    imported["raw_payloads"] = len(records_by_collection.get("raw_payloads", ()))

    for record in records_by_collection.get("provider_attempts", ()):
        repository.insert_provider_attempt(record)
    imported["provider_attempts"] = len(records_by_collection.get("provider_attempts", ()))

    for record in records_by_collection.get("dataset_manifests", ()):
        repository.write_dataset_manifest(record)
    imported["dataset_manifests"] = len(records_by_collection.get("dataset_manifests", ()))
    return imported


def _rebase_manifest_paths(
    records: Sequence[Mapping[str, Any]],
    *,
    columnar_root: Path,
) -> tuple[dict[str, Any], ...]:
    rebased: list[dict[str, Any]] = []
    for record in records:
        doc = dict(record)
        if doc.get("storage") == "parquet":
            relative_path = str(doc.get("relative_path") or "").strip()
            if relative_path:
                doc["path"] = str(columnar_root / Path(relative_path))
        rebased.append(doc)
    return tuple(rebased)


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


def _clear_columnar_root(columnar_root: Path) -> dict[str, int]:
    root = columnar_root.resolve()
    if str(root) in {root.anchor, str(Path.home().resolve())} or len(root.parts) < 3:
        raise ValueError(f"refusing to clear unsafe columnar root: {columnar_root}")
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return {"files": 0, "dirs": 0, "bytes": 0}
    if not root.is_dir():
        raise ValueError(f"columnar root is not a directory: {columnar_root}")

    file_count = 0
    dir_count = 0
    byte_count = 0
    for child in tuple(root.iterdir()):
        if child.is_dir():
            files = tuple(path for path in child.rglob("*") if path.is_file())
            dirs = tuple(path for path in child.rglob("*") if path.is_dir())
            file_count += len(files)
            dir_count += len(dirs) + 1
            byte_count += sum(path.stat().st_size for path in files)
            shutil.rmtree(child)
            continue
        if child.is_file():
            file_count += 1
            byte_count += child.stat().st_size
            child.unlink()
            continue
        child.unlink(missing_ok=True)
    return {"files": file_count, "dirs": dir_count, "bytes": byte_count}


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
