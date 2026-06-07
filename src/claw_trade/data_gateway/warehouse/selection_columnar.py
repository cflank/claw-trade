from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import duckdb
import pandas as pd

from claw_trade.data_gateway.refs import (
    normalize_legacy_normalized_dataset_ref,
    normalize_provider_attempt_ref,
)
from claw_trade.selection.models import SelectionMarket, SelectionProfile, SelectionRunPlan

_SCHEMA_VERSION = "selection-columnar-warehouse-v1"
_DEFAULT_ROOT = Path(".runtime/selection/columnar")
_DAILY_CHUNK_SIZE = 50_000
_FEATURE_CHUNK_SIZE = 1_000
_FEATURE_READ_BATCH_SIZE = 1_000
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
@dataclass(frozen=True)
class SelectionColumnarPartition:
    dataset: str
    relative_path: str
    row_count: int
    content_sha256: str


@dataclass(frozen=True)
class SelectionColumnarManifest:
    schema_version: str
    manifest_ref: str
    market: SelectionMarket
    profile: SelectionProfile
    trade_date: str
    lookback_trading_days: int
    universe_scope: str
    approved_strategy_config_ref: str
    created_at: str
    daily_partitions: tuple[SelectionColumnarPartition, ...]
    feature_partitions: tuple[SelectionColumnarPartition, ...]
    provider_attempt_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    warehouse_check_ref: str
    coverage_status: str
    coverage_checked_at: str
    coverage_gap_codes: tuple[str, ...]

    @property
    def daily_row_count(self) -> int:
        return sum(item.row_count for item in self.daily_partitions)

    @property
    def feature_row_count(self) -> int:
        return sum(item.row_count for item in self.feature_partitions)


class SelectionColumnarWarehouse:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or _default_root()).resolve()

    @classmethod
    def default(cls) -> "SelectionColumnarWarehouse":
        return cls()

    def begin_write(self, *, plan: SelectionRunPlan) -> "SelectionColumnarWriteSession":
        return SelectionColumnarWriteSession(warehouse=self, plan=plan)

    def load_valid_manifest(self, *, plan: SelectionRunPlan) -> SelectionColumnarManifest | None:
        path = self._manifest_path(plan)
        if not path.exists():
            return None
        if not self._manifest_hash_matches_sidecar(path):
            return None
        try:
            manifest = _manifest_from_payload(_read_json_object(path))
        except (ValueError, TypeError, OSError):
            return None
        if not _manifest_matches_plan(manifest, plan):
            return None
        if not self.validate_manifest(manifest):
            return None
        return manifest

    def validate_manifest_ref(self, manifest_ref: str, *, expected_sha256: str | None = None) -> bool:
        try:
            path = self._path_from_manifest_ref(manifest_ref)
            if expected_sha256 is not None and _sha256_file(path) != expected_sha256:
                return False
            if not self._manifest_hash_matches_sidecar(path):
                return False
            manifest = _manifest_from_payload(_read_json_object(path))
        except (ValueError, TypeError, OSError):
            return False
        return self.validate_manifest(manifest)

    def manifest_sha256(self, manifest_ref: str) -> str | None:
        try:
            path = self._path_from_manifest_ref(manifest_ref)
        except ValueError:
            return None
        if not path.exists() or not path.is_file():
            return None
        return _sha256_file(path)

    def validate_manifest(self, manifest: SelectionColumnarManifest) -> bool:
        if manifest.schema_version != _SCHEMA_VERSION:
            return False
        if manifest.coverage_status != "verified":
            return False
        if manifest.daily_row_count <= 0 or manifest.feature_row_count <= 0:
            return False
        for partition in (*manifest.daily_partitions, *manifest.feature_partitions):
            path = self.root / partition.relative_path
            if not path.exists() or not path.is_file():
                return False
            if _sha256_file(path) != partition.content_sha256:
                return False
        return True

    def read_feature_rows(
        self,
        *,
        manifest: SelectionColumnarManifest,
        columns: Sequence[str],
        row_limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        if not self.validate_manifest(manifest):
            raise ValueError("selection_columnar_manifest_invalid")
        if row_limit <= 0:
            raise ValueError("selection_columnar_row_limit_invalid")
        paths = [str((self.root / item.relative_path).resolve()) for item in manifest.feature_partitions]
        if not paths:
            return ()
        requested_columns = _dedupe_columns(columns)
        if not requested_columns:
            raise ValueError("selection_columnar_projection_empty")
        with duckdb.connect(database=":memory:") as con:
            available_columns = _read_parquet_columns(con, paths)
            select_columns = ", ".join(
                _projected_column_sql(column, available_columns=available_columns) for column in requested_columns
            )
            relation = con.execute(
                f"SELECT {select_columns} FROM read_parquet(?) LIMIT ?",
                [paths, row_limit + 1],
            )
            rows: list[Mapping[str, object]] = []
            while True:
                batch = relation.fetchmany(_FEATURE_READ_BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    rows.append(_clean_mapping(dict(zip(requested_columns, row, strict=True))))
                    if len(rows) > row_limit:
                        raise ValueError("selection_columnar_row_limit_exceeded")
        return tuple(rows)

    def _manifest_path(self, plan: SelectionRunPlan) -> Path:
        return self._partition_dir(plan) / "manifest.json"

    def _partition_dir(self, plan: SelectionRunPlan) -> Path:
        return (
            self.root
            / "market_data"
            / f"market={plan.market.value}"
            / f"profile={plan.profile.value}"
            / f"trade_date={plan.trade_date}"
        )

    def _path_from_manifest_ref(self, manifest_ref: str) -> Path:
        prefix = "columnar://selection/"
        if not manifest_ref.startswith(prefix):
            raise ValueError("unsupported_selection_columnar_manifest_ref")
        relative = manifest_ref[len(prefix) :]
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("unsafe_selection_columnar_manifest_ref")
        return path

    def _manifest_hash_matches_sidecar(self, path: Path) -> bool:
        try:
            sidecar = _manifest_sha256_sidecar_path(path)
            expected = sidecar.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return bool(expected) and _sha256_file(path) == expected


class SelectionColumnarWriteSession:
    def __init__(self, *, warehouse: SelectionColumnarWarehouse, plan: SelectionRunPlan) -> None:
        self._warehouse = warehouse
        self._plan = plan
        self._base_dir = warehouse._partition_dir(plan)
        self._staging_dir = self._base_dir.with_name(f"{self._base_dir.name}.staging-{plan.selection_run_id}")
        self._daily_buffer: list[Mapping[str, object]] = []
        self._feature_buffer: list[Mapping[str, object]] = []
        self._daily_partitions: list[SelectionColumnarPartition] = []
        self._feature_partitions: list[SelectionColumnarPartition] = []
        self._daily_index = 0
        self._feature_index = 0
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    def add_daily_rows(self, rows: Iterable[Mapping[str, object]]) -> None:
        for row in rows:
            self._daily_buffer.append(_scalar_mapping(row))
            if len(self._daily_buffer) >= _DAILY_CHUNK_SIZE:
                self._flush_daily()

    def add_feature_rows(self, rows: Iterable[Mapping[str, object]]) -> None:
        for row in rows:
            self._feature_buffer.append(_scalar_mapping(row))
            if len(self._feature_buffer) >= _FEATURE_CHUNK_SIZE:
                self._flush_feature()

    def commit(
        self,
        *,
        provider_attempt_refs: tuple[str, ...],
        normalized_refs: tuple[str, ...],
        coverage_status: str = "verified",
        coverage_gap_codes: tuple[str, ...] = (),
    ) -> SelectionColumnarManifest:
        self._flush_daily()
        self._flush_feature()
        created_at = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
        final_dir = self._base_dir
        if final_dir.exists():
            backup_dir = final_dir.with_name(f"{final_dir.name}.previous-{self._plan.selection_run_id}")
            if backup_dir.exists():
                _remove_tree(backup_dir)
            final_dir.replace(backup_dir)
        self._staging_dir.replace(final_dir)
        daily = tuple(
            _partition_in_final_dir(partition, staging_dir=self._staging_dir, final_dir=final_dir, root=self._warehouse.root)
            for partition in self._daily_partitions
        )
        features = tuple(
            _partition_in_final_dir(partition, staging_dir=self._staging_dir, final_dir=final_dir, root=self._warehouse.root)
            for partition in self._feature_partitions
        )
        manifest_ref = _manifest_ref(self._warehouse.root, final_dir / "manifest.json")
        manifest = SelectionColumnarManifest(
            schema_version=_SCHEMA_VERSION,
            manifest_ref=manifest_ref,
            market=self._plan.market,
            profile=self._plan.profile,
            trade_date=self._plan.trade_date,
            lookback_trading_days=self._plan.lookback_trading_days,
            universe_scope=self._plan.universe_scope,
            approved_strategy_config_ref=self._plan.approved_strategy_config_ref,
            created_at=created_at,
            daily_partitions=daily,
            feature_partitions=features,
            provider_attempt_refs=tuple(dict.fromkeys(normalize_provider_attempt_ref(ref) for ref in provider_attempt_refs)),
            normalized_refs=tuple(dict.fromkeys(normalize_legacy_normalized_dataset_ref(ref) for ref in normalized_refs)),
            warehouse_check_ref=(
                f"warehouse-check://selection-columnar/{self._plan.market.value}/"
                f"{self._plan.profile.value}/{self._plan.trade_date}"
            ),
            coverage_status=coverage_status,
            coverage_checked_at=created_at,
            coverage_gap_codes=coverage_gap_codes,
        )
        manifest_path = final_dir / "manifest.json"
        manifest_path.write_text(json.dumps(_manifest_to_payload(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
        _manifest_sha256_sidecar_path(manifest_path).write_text(_sha256_file(manifest_path), encoding="utf-8")
        return manifest

    def _flush_daily(self) -> None:
        if not self._daily_buffer:
            return
        self._daily_index += 1
        path = self._staging_dir / "daily_bar" / f"part-{self._daily_index:05d}.parquet"
        self._daily_partitions.append(_write_parquet_partition(path, dataset="daily_bar", rows=self._daily_buffer, root=self._warehouse.root))
        self._daily_buffer = []

    def _flush_feature(self) -> None:
        if not self._feature_buffer:
            return
        self._feature_index += 1
        path = self._staging_dir / "selection_features" / f"part-{self._feature_index:05d}.parquet"
        self._feature_partitions.append(
            _write_parquet_partition(path, dataset="selection_features", rows=self._feature_buffer, root=self._warehouse.root)
        )
        self._feature_buffer = []


def _default_root() -> Path:
    return Path(os.environ.get("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", "") or _DEFAULT_ROOT)


def _write_parquet_partition(
    path: Path,
    *,
    dataset: str,
    rows: list[Mapping[str, object]],
    root: Path,
) -> SelectionColumnarPartition:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    with duckdb.connect(database=":memory:") as con:
        con.register("selection_rows", frame)
        con.execute(f"COPY selection_rows TO {_duckdb_string(path)} (FORMAT PARQUET)")
    return SelectionColumnarPartition(
        dataset=dataset,
        relative_path=str(path.resolve().relative_to(root)),
        row_count=len(rows),
        content_sha256=_sha256_file(path),
    )


def _partition_in_final_dir(
    partition: SelectionColumnarPartition,
    *,
    staging_dir: Path,
    final_dir: Path,
    root: Path,
) -> SelectionColumnarPartition:
    old_path = root / partition.relative_path
    new_path = final_dir / old_path.relative_to(staging_dir)
    return SelectionColumnarPartition(
        dataset=partition.dataset,
        relative_path=str(new_path.resolve().relative_to(root)),
        row_count=partition.row_count,
        content_sha256=partition.content_sha256,
    )


def _manifest_ref(root: Path, path: Path) -> str:
    return f"columnar://selection/{path.resolve().relative_to(root)}"


def _manifest_matches_plan(manifest: SelectionColumnarManifest, plan: SelectionRunPlan) -> bool:
    return (
        manifest.market == plan.market
        and manifest.profile == plan.profile
        and manifest.trade_date == plan.trade_date
        and manifest.lookback_trading_days == plan.lookback_trading_days
        and manifest.universe_scope == plan.universe_scope
        and manifest.approved_strategy_config_ref == plan.approved_strategy_config_ref
    )


def _manifest_to_payload(manifest: SelectionColumnarManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "manifest_ref": manifest.manifest_ref,
        "market": manifest.market.value,
        "profile": manifest.profile.value,
        "trade_date": manifest.trade_date,
        "lookback_trading_days": manifest.lookback_trading_days,
        "universe_scope": manifest.universe_scope,
        "approved_strategy_config_ref": manifest.approved_strategy_config_ref,
        "created_at": manifest.created_at,
        "daily_partitions": [_partition_to_payload(item) for item in manifest.daily_partitions],
        "feature_partitions": [_partition_to_payload(item) for item in manifest.feature_partitions],
        "provider_attempt_refs": list(manifest.provider_attempt_refs),
        "normalized_refs": list(manifest.normalized_refs),
        "warehouse_check_ref": manifest.warehouse_check_ref,
        "coverage_status": manifest.coverage_status,
        "coverage_checked_at": manifest.coverage_checked_at,
        "coverage_gap_codes": list(manifest.coverage_gap_codes),
    }


def _manifest_from_payload(payload: Mapping[str, Any]) -> SelectionColumnarManifest:
    return SelectionColumnarManifest(
        schema_version=str(payload.get("schema_version") or ""),
        manifest_ref=str(payload.get("manifest_ref") or ""),
        market=SelectionMarket(str(payload.get("market") or "")),
        profile=SelectionProfile(str(payload.get("profile") or "")),
        trade_date=str(payload.get("trade_date") or ""),
        lookback_trading_days=int(payload.get("lookback_trading_days") or 0),
        universe_scope=str(payload.get("universe_scope") or ""),
        approved_strategy_config_ref=str(payload.get("approved_strategy_config_ref") or ""),
        created_at=str(payload.get("created_at") or ""),
        daily_partitions=tuple(_partition_from_payload(item) for item in _payload_list(payload.get("daily_partitions"))),
        feature_partitions=tuple(_partition_from_payload(item) for item in _payload_list(payload.get("feature_partitions"))),
        provider_attempt_refs=tuple(
            dict.fromkeys(normalize_provider_attempt_ref(str(item)) for item in _payload_list(payload.get("provider_attempt_refs")))
        ),
        normalized_refs=tuple(
            dict.fromkeys(normalize_legacy_normalized_dataset_ref(item) for item in _payload_list(payload.get("normalized_refs")))
        ),
        warehouse_check_ref=str(payload.get("warehouse_check_ref") or ""),
        coverage_status=str(payload.get("coverage_status") or ""),
        coverage_checked_at=str(payload.get("coverage_checked_at") or ""),
        coverage_gap_codes=tuple(str(item) for item in _payload_list(payload.get("coverage_gap_codes"))),
    )


def _partition_to_payload(partition: SelectionColumnarPartition) -> dict[str, object]:
    return {
        "dataset": partition.dataset,
        "relative_path": partition.relative_path,
        "row_count": partition.row_count,
        "content_sha256": partition.content_sha256,
    }


def _partition_from_payload(payload: object) -> SelectionColumnarPartition:
    if not isinstance(payload, Mapping):
        raise ValueError("selection_columnar_partition_invalid")
    return SelectionColumnarPartition(
        dataset=str(payload.get("dataset") or ""),
        relative_path=str(payload.get("relative_path") or ""),
        row_count=int(payload.get("row_count") or 0),
        content_sha256=str(payload.get("content_sha256") or ""),
    )


def _payload_list(value: object) -> tuple[object, ...]:
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _read_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("selection_columnar_manifest_not_object")
    return payload


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_sha256_sidecar_path(path: Path) -> Path:
    return path.with_suffix(f"{path.suffix}.sha256")


def _scalar_mapping(row: Mapping[str, object]) -> Mapping[str, object]:
    result: dict[str, object] = {}
    for key, value in row.items():
        if isinstance(value, str | int | float | bool) or value is None:
            result[str(key)] = value
        elif isinstance(value, datetime):
            result[str(key)] = value.isoformat()
        elif hasattr(value, "isoformat"):
            result[str(key)] = value.isoformat()  # type: ignore[call-arg]
        else:
            result[str(key)] = json.dumps(value, ensure_ascii=False, default=str)
    return result


def _clean_mapping(row: Mapping[str, object]) -> Mapping[str, object]:
    result: dict[str, object] = {}
    for key, value in row.items():
        if pd.isna(value):
            result[str(key)] = None
        elif str(key) in {"dataset_ref", "source_ref", "valuation_source_ref", "private_placement_source_ref"}:
            result[str(key)] = normalize_legacy_normalized_dataset_ref(value)
        else:
            result[str(key)] = value
    return result


def _duckdb_string(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _dedupe_columns(columns: Sequence[str]) -> tuple[str, ...]:
    output: list[str] = []
    for raw in columns:
        column = str(raw).strip()
        if not column:
            continue
        if column in output:
            continue
        if not _IDENTIFIER_PATTERN.match(column):
            raise ValueError(f"unsafe_selection_columnar_column:{column}")
        output.append(column)
    return tuple(output)


def _read_parquet_columns(con: duckdb.DuckDBPyConnection, paths: Sequence[str]) -> set[str]:
    rows = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [list(paths)]).fetchall()
    return {str(row[0]) for row in rows if row and str(row[0]).strip()}


def _projected_column_sql(column: str, *, available_columns: set[str]) -> str:
    quoted = f'"{column}"'
    if column in available_columns:
        return quoted
    return f"NULL AS {quoted}"


def _remove_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    path.rmdir()
