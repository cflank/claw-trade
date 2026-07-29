from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

_ROW_EXCLUDE_FIELDS = {
    "dataset_ref",
    "dataset",
    "market",
    "symbol_id",
    "universe_ref",
    "granularity",
    "period_start",
    "period_end",
    "field_set",
    "as_of",
    "fresh_until",
    "source_roles",
    "dataset_checksum",
    "dataset_checksum_algorithm",
    "dataset_checksum_scope",
    "dataset_row_count",
}
_MANIFEST_FULL_DATASET_REFS_LIMIT = 500
_MANIFEST_DATASET_REFS_SAMPLE_LIMIT = 20
# ponytail: process-local single-flight; use a file lock only if ordered reads move to multiple processes.
_ORDERED_READ_LOCK = Lock()


@dataclass(frozen=True)
class NormalizedColumnarWriteResult:
    dataset_refs: tuple[str, ...]
    manifest: dict[str, Any]


class NormalizedColumnarWarehouse:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_env(cls, *, root: str | Path | None = None) -> "NormalizedColumnarWarehouse":
        if root is None:
            import os

            raw = os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT", "").strip()
            if raw:
                root = raw
            else:
                root = Path(".runtime/dev-services/data-gateway/normalized")
        return cls(Path(root))

    def write_records(self, records: Sequence[Mapping[str, Any]]) -> NormalizedColumnarWriteResult:
        if not records:
            return NormalizedColumnarWriteResult(dataset_refs=(), manifest={})
        first = records[0]
        dataset = str(first.get("dataset") or "unknown_dataset")
        market = str(first.get("market") or "unknown_market")
        granularity = str(first.get("granularity") or "unknown")
        partition_id = uuid4().hex[:16]
        partition_dir = self.root / f"market={_safe_token(market)}" / f"dataset={_safe_token(dataset)}" / f"granularity={_safe_token(granularity)}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        partition_path = partition_dir / f"partition-{partition_id}.parquet"

        rows = [_parquet_row(record) for record in records]
        self._write_parquet(partition_path, rows)
        file_sha256 = _file_sha256(partition_path)
        dataset_refs = tuple(dict.fromkeys(str(record.get("dataset_ref")) for record in records if record.get("dataset_ref")))
        manifest_dataset_refs = _manifest_dataset_refs(dataset_refs)
        dataset_refs_sha256 = _dataset_refs_sha256(dataset_refs)
        manifest = {
            "manifest_ref": f"manifest:normalized:{market}:{dataset}:{granularity}:{partition_id}",
            "status": "active",
            "storage": "parquet",
            "market": market,
            "dataset": dataset,
            "granularity": granularity,
            "partition_id": partition_id,
            "path": str(partition_path),
            "relative_path": str(partition_path.relative_to(self.root)),
            "row_count": len(rows),
            "dataset_refs": manifest_dataset_refs,
            "dataset_ref_count": len(dataset_refs),
            "dataset_refs_truncated": len(manifest_dataset_refs) < len(dataset_refs),
            "dataset_refs_sample": manifest_dataset_refs,
            "dataset_refs_sha256": dataset_refs_sha256,
            "dataset_ref_prefixes": _dataset_ref_prefixes(dataset_refs),
            "symbol_ids": tuple(dict.fromkeys(str(record.get("symbol_id")) for record in records if record.get("symbol_id"))),
            "universe_refs": tuple(dict.fromkeys(str(record.get("universe_ref")) for record in records if record.get("universe_ref"))),
            "field_set": tuple(sorted({field for record in records for field in tuple(record.get("field_set", ()) or ())})),
            "source_roles": tuple(sorted({str(role) for record in records for role in tuple(record.get("source_roles", ()) or ()) if str(role)})),
            "period_start_min": min((_period_text(record.get("period_start")) or "") for record in records),
            "period_end_max": max((_period_text(record.get("period_end")) or "") for record in records),
            "sha256": file_sha256,
            "hash_algorithm": "sha256",
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
        return NormalizedColumnarWriteResult(dataset_refs=dataset_refs, manifest=manifest)

    def query_documents(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
        include_row: bool = True,
        fields: Sequence[str] = (),
        manifests: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[dict[str, Any], ...]:
        selected_manifests = self._selected_manifests(
            manifests,
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            start=_range_start_text(date_range_start),
            end=_range_end_text(date_range_end),
        )
        if not selected_manifests:
            return ()
        start = _range_start_text(date_range_start)
        end = _range_end_text(date_range_end)
        rows = tuple(
            self.iter_documents(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=start,
                date_range_end=end,
                require_integrity_metadata=require_integrity_metadata,
                include_row=include_row,
                fields=fields,
                manifests=selected_manifests,
                _already_selected=True,
            )
        )
        return rows

    def iter_documents(self, **kwargs: Any) -> Iterable[dict[str, Any]]:
        already_selected = bool(kwargs.pop("_already_selected", False))
        order_by_symbol_id = bool(kwargs.pop("order_by_symbol_id", False))
        manifests = tuple(kwargs.pop("manifests", ()) or ())
        dataset = str(kwargs["dataset"])
        market = str(kwargs["market"])
        symbol_id = kwargs.get("symbol_id")
        universe_ref = kwargs.get("universe_ref")
        start = _range_start_text(kwargs.get("date_range_start"))
        end = _range_end_text(kwargs.get("date_range_end"))
        require_integrity_metadata = bool(kwargs.get("require_integrity_metadata", False))
        include_row = bool(kwargs.get("include_row", True))
        requested_fields = _requested_fields(kwargs.get("fields"))
        fields = requested_fields if order_by_symbol_id else _projected_fields(requested_fields)
        selected_manifests = (
            manifests
            if already_selected
            else self._selected_manifests(
                manifests,
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                start=start,
                end=end,
            )
        )
        seen_refs: set[str] = set()
        import duckdb

        with duckdb.connect(":memory:") as connection:
            if order_by_symbol_id:
                with _ORDERED_READ_LOCK:
                    valid_paths: list[Path] = []
                    for manifest in selected_manifests:
                        invalid = self._invalid_manifest_document(manifest)
                        if invalid is not None:
                            raise RuntimeError(f"invalid normalized columnar manifest: {invalid['dataset_ref']}")
                        valid_paths.append(self._manifest_path(manifest))
                    if not valid_paths:
                        return
                    connection.execute("SET memory_limit='512MB'")
                    connection.execute("SET threads=1")
                    connection.execute("SET preserve_insertion_order=false")
                    yield from self._iter_partition_rows(
                        tuple(valid_paths),
                        connection=connection,
                        dataset=dataset,
                        market=market,
                        symbol_id=symbol_id,
                        universe_ref=universe_ref,
                        start=start,
                        end=end,
                        require_integrity_metadata=require_integrity_metadata,
                        include_row=include_row,
                        fields=fields,
                        order_by_symbol_id=True,
                    )
                return
            for manifest in selected_manifests:
                invalid = self._invalid_manifest_document(manifest)
                if invalid is not None:
                    ref = str(invalid["dataset_ref"])
                    if ref not in seen_refs:
                        seen_refs.add(ref)
                        yield invalid
                    continue
                try:
                    rows = self._iter_partition_rows(
                        self._manifest_path(manifest),
                        connection=connection,
                        dataset=dataset,
                        market=market,
                        symbol_id=symbol_id,
                        universe_ref=universe_ref,
                        start=start,
                        end=end,
                        require_integrity_metadata=require_integrity_metadata,
                        include_row=include_row,
                        fields=fields,
                        order_by_symbol_id=False,
                    )
                    for row in rows:
                        if requested_fields and not _document_has_requested_fields(row, requested_fields):
                            continue
                        ref = str(row.get("dataset_ref"))
                        if ref in seen_refs:
                            continue
                        seen_refs.add(ref)
                        yield row
                except Exception:
                    invalid = _invalid_manifest_document(manifest, "read_failed")
                    ref = str(invalid["dataset_ref"])
                    if ref not in seen_refs:
                        seen_refs.add(ref)
                        yield invalid

    def count_documents(self, **kwargs: Any) -> int:
        manifests = tuple(kwargs.pop("manifests", ()) or ())
        selected_manifests = self._selected_manifests(
            manifests,
            dataset=str(kwargs["dataset"]),
            market=str(kwargs["market"]),
            symbol_id=kwargs.get("symbol_id"),
            universe_ref=kwargs.get("universe_ref"),
            start=_range_start_text(kwargs.get("date_range_start")),
            end=_range_end_text(kwargs.get("date_range_end")),
        )
        total = 0
        import duckdb

        with duckdb.connect(":memory:") as connection:
            for manifest in selected_manifests:
                if self._invalid_manifest_document(manifest) is not None:
                    continue
                total += self._count_partition_rows(
                    self._manifest_path(manifest),
                    connection=connection,
                    dataset=str(kwargs["dataset"]),
                    market=str(kwargs["market"]),
                    symbol_id=kwargs.get("symbol_id"),
                    universe_ref=kwargs.get("universe_ref"),
                    start=_range_start_text(kwargs.get("date_range_start")),
                    end=_range_end_text(kwargs.get("date_range_end")),
                    require_integrity_metadata=bool(kwargs.get("require_integrity_metadata", False)),
                )
        return total

    def aggregate_coverage(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
        manifests: Sequence[Mapping[str, Any]] = (),
        sample_limit: int = 50,
    ) -> dict[str, Any] | None:
        start = _range_start_text(date_range_start)
        end = _range_end_text(date_range_end)
        selected_manifests = self._selected_manifests(
            manifests,
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            start=start,
            end=end,
        )
        if not selected_manifests or any(self._invalid_manifest_document(manifest) is not None for manifest in selected_manifests):
            return None
        record_count = 0
        dataset_refs: list[str] = []
        ranges: set[tuple[Any, Any]] = set()
        starts: set[Any] = set()
        ends: set[Any] = set()
        field_sets: set[tuple[Any, ...]] = set()
        source_role_sets: set[tuple[Any, ...]] = set()
        freshest_as_of: Any | None = None
        freshest_until: Any | None = None
        checksum_counts: dict[str, dict[str, Any]] = {}
        import duckdb

        with duckdb.connect(":memory:") as connection:
            for manifest in selected_manifests:
                try:
                    summary = self._partition_summary(
                        self._manifest_path(manifest),
                        connection=connection,
                        dataset=dataset,
                        market=market,
                        symbol_id=symbol_id,
                        universe_ref=universe_ref,
                        start=start,
                        end=end,
                        require_integrity_metadata=require_integrity_metadata,
                        sample_limit=max(0, sample_limit - len(dataset_refs)),
                    )
                except Exception:
                    return None
                record_count += int(summary["record_count"])
                dataset_refs.extend(str(ref) for ref in summary["dataset_refs"])
                ranges.update(tuple(item) for item in summary["ranges"])
                starts.update(summary["starts"])
                ends.update(summary["ends"])
                field_sets.update(tuple(item) for item in summary["field_sets"])
                source_role_sets.update(tuple(item) for item in summary["source_role_sets"])
                freshest_as_of = _max_non_empty(freshest_as_of, summary["freshest_as_of"])
                freshest_until = _max_non_empty(freshest_until, summary["freshest_until"])
                for checksum_row in summary["checksum_counts"]:
                    checksum = str(checksum_row["checksum"])
                    current = checksum_counts.setdefault(
                        checksum,
                        {
                            "checksum": checksum,
                            "actual_count": 0,
                            "expected_min": checksum_row["expected_min"],
                            "expected_max": checksum_row["expected_max"],
                            "min_start": checksum_row["min_start"],
                            "max_end": checksum_row["max_end"],
                        },
                    )
                    current["actual_count"] += int(checksum_row["actual_count"])
                    current["expected_min"] = _min_non_empty(current["expected_min"], checksum_row["expected_min"])
                    current["expected_max"] = _max_non_empty(current["expected_max"], checksum_row["expected_max"])
                    current["min_start"] = _min_non_empty(current["min_start"], checksum_row["min_start"])
                    current["max_end"] = _max_non_empty(current["max_end"], checksum_row["max_end"])
        return {
            "record_count": record_count,
            "dataset_refs": tuple(dataset_refs[:sample_limit]),
            "ranges": tuple(sorted(ranges)),
            "starts": tuple(sorted(starts)),
            "ends": tuple(sorted(ends)),
            "field_sets": tuple(sorted(field_sets)),
            "source_role_sets": tuple(sorted(source_role_sets)),
            "freshest_as_of": freshest_as_of,
            "freshest_until": freshest_until,
            "checksum_counts": tuple(checksum_counts.values()),
        }

    @staticmethod
    def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
        import duckdb
        import pandas as pd

        frame = pd.DataFrame(rows)
        with duckdb.connect(":memory:") as conn:
            conn.register("normalized_rows", frame)
            conn.execute("COPY normalized_rows TO ? (FORMAT PARQUET)", [str(path)])

    @staticmethod
    def _iter_partition_rows(
        path: Path | Sequence[Path],
        *,
        connection: Any,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        start: str | None,
        end: str | None,
        require_integrity_metadata: bool,
        include_row: bool,
        fields: Sequence[str],
        order_by_symbol_id: bool,
    ) -> Iterable[dict[str, Any]]:
        where, params = _where_clause(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            start=start,
            end=end,
            require_integrity_metadata=require_integrity_metadata,
        )
        columns = [
            "dataset_ref",
            "dataset",
            "market",
            "symbol_id",
            "universe_ref",
            "granularity",
            "period_start",
            "period_end",
            "field_set_json",
            "as_of",
            "fresh_until",
            "source_roles_json",
            "dataset_checksum",
            "dataset_checksum_algorithm",
            "dataset_checksum_scope",
            "dataset_row_count",
        ]
        projected_fields = _requested_fields(fields) if order_by_symbol_id else _projected_fields(fields)
        row_extract_columns: list[str] = []
        row_extract_aliases: list[str] = []
        if include_row and projected_fields:
            for index, _field in enumerate(projected_fields):
                alias = f"row_field_{index}"
                row_extract_columns.append(f"json_extract(row_json, ?) AS {alias}")
                row_extract_aliases.append(alias)
        elif include_row:
            columns.append("row_json")
        select_expressions = (*columns, *row_extract_columns)
        result_columns = (*columns, *row_extract_aliases)
        query = f"SELECT {', '.join(select_expressions)} FROM read_parquet(?, union_by_name=true) WHERE {where}"
        json_path_params = tuple(_json_path_for_field(field) for field in projected_fields) if row_extract_columns else ()
        path_param: str | list[str]
        if isinstance(path, Path):
            path_param = str(path)
        else:
            path_param = [str(item) for item in path]
        query_params = [*json_path_params, path_param, *params]
        if order_by_symbol_id and row_extract_columns:
            import duckdb

            paths = [path] if isinstance(path, Path) else list(path)
            with TemporaryDirectory(prefix="claw-trade-projected-") as temp_dir:
                projected_paths: list[str] = []
                for batch_index in range(0, len(paths), 16):
                    projected_path = Path(temp_dir) / f"projected-{batch_index // 16:04d}.parquet"
                    with duckdb.connect(":memory:") as stage_connection:
                        stage_connection.execute("SET memory_limit='512MB'")
                        stage_connection.execute("SET threads=1")
                        stage_connection.execute("SET preserve_insertion_order=false")
                        stage_connection.execute(
                            f"COPY ({query}) TO ? (FORMAT PARQUET, ROW_GROUP_SIZE 10000)",
                            [
                                str(projected_path),
                                *json_path_params,
                                [str(item) for item in paths[batch_index : batch_index + 16]],
                                *params,
                            ],
                        )
                    projected_paths.append(str(projected_path))
                relation = connection.execute(
                    f"SELECT {', '.join(result_columns)} FROM read_parquet(?, union_by_name=true) "
                    "ORDER BY symbol_id, period_start, dataset_ref",
                    [projected_paths],
                )
                while True:
                    rows = relation.fetchmany(1000)
                    if not rows:
                        break
                    for row in rows:
                        yield _document_from_parquet_row(
                            dict(zip(result_columns, row, strict=True)),
                            include_row=include_row,
                            fields=projected_fields,
                        )
            return
        else:
            if order_by_symbol_id:
                query += " ORDER BY symbol_id, period_start, dataset_ref"
            relation = connection.execute(query, query_params)
        while True:
            rows = relation.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                yield _document_from_parquet_row(
                    dict(zip(result_columns, row, strict=True)),
                    include_row=include_row,
                    fields=projected_fields,
                )

    @staticmethod
    def _count_partition_rows(
        path: Path,
        *,
        connection: Any,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        start: str | None,
        end: str | None,
        require_integrity_metadata: bool,
    ) -> int:
        where, params = _where_clause(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            start=start,
            end=end,
            require_integrity_metadata=require_integrity_metadata,
        )
        return int(
            connection.execute(f"SELECT COUNT(*) FROM read_parquet(?) WHERE {where}", [str(path), *params]).fetchone()[0]
        )

    @staticmethod
    def _partition_summary(
        path: Path,
        *,
        connection: Any,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        start: str | None,
        end: str | None,
        require_integrity_metadata: bool,
        sample_limit: int,
    ) -> dict[str, Any]:
        where, params = _where_clause(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            start=start,
            end=end,
            require_integrity_metadata=require_integrity_metadata,
        )
        record_count, min_start, max_end, freshest_as_of, freshest_until = connection.execute(
            f"SELECT COUNT(*), MIN(period_start), MAX(period_end), MAX(as_of), MAX(fresh_until) FROM read_parquet(?) WHERE {where}",
            [str(path), *params],
        ).fetchone()
        dataset_refs = tuple(
            str(row[0])
            for row in connection.execute(
                f"SELECT dataset_ref FROM read_parquet(?) WHERE {where} LIMIT ?",
                [str(path), *params, sample_limit],
            ).fetchall()
        )
        field_sets = tuple(
            tuple(json.loads(str(row[0] or "[]")))
            for row in connection.execute(
                f"SELECT DISTINCT field_set_json FROM read_parquet(?) WHERE {where}",
                [str(path), *params],
            ).fetchall()
        )
        source_role_sets = tuple(
            tuple(json.loads(str(row[0] or "[]")))
            for row in connection.execute(
                f"SELECT DISTINCT source_roles_json FROM read_parquet(?) WHERE {where}",
                [str(path), *params],
            ).fetchall()
        )
        checksum_counts = tuple(
            {
                "checksum": str(row[0]),
                "actual_count": int(row[1]),
                "expected_min": row[2],
                "expected_max": row[3],
                "min_start": row[4],
                "max_end": row[5],
            }
            for row in connection.execute(
                f"""
                SELECT dataset_checksum, COUNT(*), MIN(dataset_row_count), MAX(dataset_row_count), MIN(period_start), MAX(period_end)
                FROM read_parquet(?)
                WHERE {where}
                GROUP BY dataset_checksum
                """,
                [str(path), *params],
            ).fetchall()
            if row[0]
        )
        return {
            "record_count": int(record_count or 0),
            "dataset_refs": dataset_refs,
            "ranges": ((min_start, max_end),) if min_start and max_end else (),
            "starts": (min_start,) if min_start else (),
            "ends": (max_end,) if max_end else (),
            "field_sets": field_sets,
            "source_role_sets": source_role_sets,
            "freshest_as_of": freshest_as_of,
            "freshest_until": freshest_until,
            "checksum_counts": checksum_counts,
        }

    def _selected_manifests(
        self,
        manifests: Sequence[Mapping[str, Any]],
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        start: str | None,
        end: str | None,
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            manifest
            for manifest in manifests
            if self._manifest_matches(
                manifest,
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                start=start,
                end=end,
            )
        )

    @staticmethod
    def _manifest_matches(
        manifest: Mapping[str, Any],
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        start: str | None,
        end: str | None,
    ) -> bool:
        if manifest.get("storage") != "parquet" or manifest.get("status", "active") != "active":
            return False
        if str(manifest.get("dataset") or "") != dataset or str(manifest.get("market") or "") != market:
            return False
        symbol_ids = tuple(str(item) for item in tuple(manifest.get("symbol_ids", ()) or ()))
        if symbol_id and symbol_ids and symbol_id not in symbol_ids:
            return False
        universe_refs = tuple(str(item) for item in tuple(manifest.get("universe_refs", ()) or ()))
        if universe_ref and universe_refs and universe_ref not in universe_refs:
            return False
        manifest_start = _period_text(manifest.get("period_start_min"))
        manifest_end = _range_end_text(manifest.get("period_end_max"))
        if end and manifest_start and manifest_start > end:
            return False
        if start and manifest_end and manifest_end < start:
            return False
        return True

    def _invalid_manifest_document(self, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self._manifest_path(manifest)
        expected_hash = str(manifest.get("sha256") or "").strip()
        if not path.exists():
            return _invalid_manifest_document(manifest, "missing_file")
        if expected_hash and _file_sha256(path) != expected_hash:
            return _invalid_manifest_document(manifest, "sha256_mismatch")
        return None

    def _manifest_path(self, manifest: Mapping[str, Any]) -> Path:
        raw_path = Path(str(manifest.get("path") or ""))
        if raw_path.exists():
            return raw_path

        relative_path = str(manifest.get("relative_path") or "").strip()
        candidates: list[Path] = []
        if relative_path:
            candidates.append(self.root / relative_path)

        derived_relative_path = self._derived_manifest_relative_path(manifest=manifest, raw_path=raw_path)
        if derived_relative_path is not None:
            candidates.append(self.root / derived_relative_path)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else raw_path

    @staticmethod
    def _derived_manifest_relative_path(*, manifest: Mapping[str, Any], raw_path: Path) -> Path | None:
        market = str(manifest.get("market") or "").strip()
        dataset = str(manifest.get("dataset") or "").strip()
        granularity = str(manifest.get("granularity") or "").strip()
        filename = raw_path.name
        if not filename:
            partition_id = str(manifest.get("partition_id") or "").strip()
            if partition_id:
                filename = f"partition-{partition_id}.parquet"
        if not market or not dataset or not granularity or not filename:
            return None
        return (
            Path(f"market={_safe_token(market)}")
            / f"dataset={_safe_token(dataset)}"
            / f"granularity={_safe_token(granularity)}"
            / filename
        )


def _parquet_row(record: Mapping[str, Any]) -> dict[str, Any]:
    row = record.get("row")
    row_payload = row if isinstance(row, Mapping) and row else _row_from_record(record)
    field_set = tuple(record.get("field_set", ()) or ()) or tuple(sorted(row_payload))
    return {
        "dataset_ref": str(record.get("dataset_ref") or ""),
        "dataset": str(record.get("dataset") or ""),
        "market": str(record.get("market") or ""),
        "symbol_id": _optional_text(record.get("symbol_id")),
        "universe_ref": _optional_text(record.get("universe_ref")),
        "granularity": str(record.get("granularity") or ""),
        "period_start": _period_text(record.get("period_start")),
        "period_end": _period_text(record.get("period_end")),
        "field_set_json": json.dumps(field_set, ensure_ascii=False, default=str),
        "as_of": _datetime_text(record.get("as_of")),
        "fresh_until": _datetime_text(record.get("fresh_until")),
        "source_roles_json": json.dumps(tuple(record.get("source_roles", ()) or ()), ensure_ascii=False, default=str),
        "dataset_checksum": _optional_text(record.get("dataset_checksum")),
        "dataset_checksum_algorithm": _optional_text(record.get("dataset_checksum_algorithm")),
        "dataset_checksum_scope": _optional_text(record.get("dataset_checksum_scope")),
        "dataset_row_count": int(record.get("dataset_row_count") or 0),
        "row_json": json.dumps(row_payload, ensure_ascii=False, default=str, sort_keys=True),
    }


def _manifest_dataset_refs(dataset_refs: Sequence[str]) -> tuple[str, ...]:
    if len(dataset_refs) <= _MANIFEST_FULL_DATASET_REFS_LIMIT:
        return tuple(dataset_refs)
    head_count = _MANIFEST_DATASET_REFS_SAMPLE_LIMIT // 2
    tail_count = _MANIFEST_DATASET_REFS_SAMPLE_LIMIT - head_count
    return tuple((*dataset_refs[:head_count], *dataset_refs[-tail_count:]))


def _dataset_refs_sha256(dataset_refs: Sequence[str]) -> str:
    return "sha256:" + sha256(json.dumps(tuple(dataset_refs), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _dataset_ref_prefixes(dataset_refs: Sequence[str]) -> tuple[str, ...]:
    prefixes: list[str] = []
    for dataset_ref in dataset_refs:
        prefix = _dataset_ref_lookup_prefix(dataset_ref)
        if prefix is None:
            continue
        if prefix not in prefixes:
            prefixes.append(prefix)
    return tuple(prefixes)


def _dataset_ref_lookup_prefix(dataset_ref: str) -> str | None:
    parts = str(dataset_ref).split(":")
    if len(parts) < 6 or parts[0] != "dataset":
        return None
    if len(parts) >= 8 and parts[2] == "CRYPTO":
        return ":".join(parts[:6]) + ":"
    return ":".join(parts[:5]) + ":"


def _document_from_parquet_row(row: Mapping[str, Any], *, include_row: bool, fields: Sequence[str] = ()) -> dict[str, Any]:
    document = {
        "dataset_ref": row.get("dataset_ref"),
        "dataset": row.get("dataset"),
        "market": row.get("market"),
        "symbol_id": row.get("symbol_id"),
        "universe_ref": row.get("universe_ref"),
        "granularity": row.get("granularity"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "field_set": tuple(json.loads(str(row.get("field_set_json") or "[]"))),
        "as_of": row.get("as_of"),
        "fresh_until": row.get("fresh_until"),
        "source_roles": tuple(json.loads(str(row.get("source_roles_json") or "[]"))),
        "dataset_checksum": row.get("dataset_checksum"),
        "dataset_checksum_algorithm": row.get("dataset_checksum_algorithm"),
        "dataset_checksum_scope": row.get("dataset_checksum_scope"),
        "dataset_row_count": int(row.get("dataset_row_count") or 0),
    }
    projected_fields = _projected_fields(fields)
    if include_row and projected_fields:
        projected_row: dict[str, Any] = {}
        for index, field in enumerate(projected_fields):
            value = _json_extract_value(row.get(f"row_field_{index}"))
            if value is not _MISSING_JSON_FIELD:
                projected_row[field] = value
        document["row"] = projected_row
    elif include_row:
        document["row"] = json.loads(str(row.get("row_json") or "{}"))
    return document


def _document_has_requested_fields(document: Mapping[str, Any], fields: Sequence[str]) -> bool:
    field_set = {str(field) for field in tuple(document.get("field_set", ()) or ())}
    row = document.get("row")
    row_fields = set(row) if isinstance(row, Mapping) else set()
    document_fields = set(document)
    return all(str(field) in field_set or str(field) in row_fields or str(field) in document_fields for field in fields)


def _invalid_manifest_document(manifest: Mapping[str, Any], reason: str) -> dict[str, Any]:
    manifest_ref = str(manifest.get("manifest_ref") or manifest.get("path") or "manifest:unknown")
    dataset_refs = tuple(str(item) for item in tuple(manifest.get("dataset_refs", ()) or ()) if str(item).strip())
    return {
        "dataset_ref": dataset_refs[0] if dataset_refs else manifest_ref,
        "dataset": str(manifest.get("dataset") or ""),
        "market": str(manifest.get("market") or ""),
        "symbol_id": None,
        "universe_ref": None,
        "granularity": str(manifest.get("granularity") or ""),
        "period_start": _period_text(manifest.get("period_start_min")),
        "period_end": _period_text(manifest.get("period_end_max")),
        "field_set": tuple(manifest.get("field_set", ()) or ()),
        "as_of": None,
        "fresh_until": None,
        "source_roles": (),
        "dataset_checksum": f"integrity_failed:{manifest_ref}:{reason}",
        "dataset_checksum_algorithm": "sha256:invalid",
        "dataset_checksum_scope": "normalized-batch-v1",
        "dataset_row_count": 0,
        "row": {},
    }


def _safe_token(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value.strip())
    return safe or "unknown"


def _row_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in record.items()
        if key not in _ROW_EXCLUDE_FIELDS and value is not None
    }


_MISSING_JSON_FIELD = object()
_STRUCTURAL_ROW_FIELDS: tuple[str, ...] = ("open_time", "close_time", "timestamp", "time")


def _projected_fields(value: Any) -> tuple[str, ...]:
    requested = _requested_fields(value)
    if not requested:
        return ()
    return tuple(dict.fromkeys((*requested, *_STRUCTURAL_ROW_FIELDS)))


def _requested_fields(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = (value,)
    elif isinstance(value, Sequence):
        candidates = tuple(value)
    else:
        return ()
    return tuple(str(field).strip() for field in candidates if str(field).strip())


def _json_path_for_field(field: str) -> str:
    return f'$.{json.dumps(field, ensure_ascii=False)}'


def _json_extract_value(value: Any) -> Any:
    if value is None:
        return _MISSING_JSON_FIELD
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    text = str(value)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _period_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _range_start_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat() if _is_midnight_datetime(value) else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10] if _is_date_only_text(text) or _is_midnight_text(text) else text


def _range_end_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if _is_midnight_datetime(value):
            return _end_of_day_text(value.date().isoformat())
        return value.isoformat()
    if isinstance(value, date):
        return _end_of_day_text(value.isoformat())
    text = str(value).strip()
    if not text:
        return None
    if _is_date_only_text(text) or _is_midnight_text(text):
        return _end_of_day_text(text[:10])
    return text


def _is_midnight_datetime(value: datetime) -> bool:
    return value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0


def _is_date_only_text(text: str) -> bool:
    return len(text) == 10 and text[4:5] == "-" and text[7:8] == "-"


def _is_midnight_text(text: str) -> bool:
    if "T" not in text and " " not in text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return _is_midnight_datetime(parsed)


def _end_of_day_text(day: str) -> str:
    return f"{day}T23:59:59.999999"


def _datetime_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _where_clause(
    *,
    dataset: str,
    market: str,
    symbol_id: str | None,
    universe_ref: str | None,
    start: str | None,
    end: str | None,
    require_integrity_metadata: bool = False,
) -> tuple[str, list[Any]]:
    where = ["dataset = ?", "market = ?"]
    params: list[Any] = [dataset, market]
    if symbol_id:
        where.append("symbol_id = ?")
        params.append(symbol_id)
    if universe_ref:
        where.append("universe_ref = ?")
        params.append(universe_ref)
    if end:
        where.append("period_start <= ?")
        params.append(end)
    if start:
        where.append("period_end >= ?")
        params.append(start)
    if require_integrity_metadata:
        where.extend(
            (
                "dataset_checksum IS NOT NULL",
                "dataset_checksum_algorithm = 'sha256:canonical-json-v1'",
                "dataset_checksum_scope = 'normalized-batch-v1'",
                "dataset_row_count > 0",
            )
        )
    return " AND ".join(where), params


def _min_non_empty(left: Any, right: Any) -> Any:
    if left in (None, ""):
        return right
    if right in (None, ""):
        return left
    return left if left <= right else right


def _max_non_empty(left: Any, right: Any) -> Any:
    if left in (None, ""):
        return right
    if right in (None, ""):
        return left
    return left if left >= right else right
