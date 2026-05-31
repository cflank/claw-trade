from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    Market,
    NormalizedResult,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderKind,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.selection_local_baostock import (
    is_private_placement_method,
    normalize_baostock_a_share_code,
    parse_baostock_daily_row,
    parse_private_placement_event_date,
)
from claw_trade.data_gateway.store.mongo import (
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    sha256_text,
    utc_now_iso,
)

BAOSTOCK_MANIFEST_RELATIVE_PATH = Path("manifest") / "baostock_files.sha256"
BAOSTOCK_PROVIDER_ID = "local-baostock"
AKSHARE_PRIVATE_PLACEMENT_PROVIDER_ID = "akshare-private-placement"
OPTIONAL_A_SHARE_INDEX_DAILY_CODES = frozenset(
    {
        "sh.000816",
        "sh.000833",
        "sh.000842",
        "sh.000939",
        "sh.000940",
        "sh.000950",
        "sh.000951",
        "sh.000957",
        "sh.000958",
        "sh.000963",
        "sh.000975",
    }
)


@dataclass(frozen=True)
class SeedImportGap:
    gap_code: str
    severity: str
    message: str
    source_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class AShareSeedImportResult:
    import_run_id: str
    trade_date: str
    manifest_hash: str | None
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    data_gaps: tuple[SeedImportGap, ...]
    readback_counts: Mapping[str, int]
    warehouse_check_ref: str | None

    @property
    def blocker_gaps(self) -> tuple[SeedImportGap, ...]:
        return tuple(gap for gap in self.data_gaps if gap.severity == "blocker")


def import_a_share_seed_to_mongo(
    *,
    baostock_root: Path,
    private_placement_root: Path,
    trade_date: str,
    import_run_id: str,
    raw_store: Any,
    normalized_store: Any,
    attempt_store: Any,
    start_date: str = "2015-01-01",
) -> AShareSeedImportResult:
    baostock_root = baostock_root.expanduser()
    private_placement_root = private_placement_root.expanduser()
    gaps: list[SeedImportGap] = []
    raw_refs: list[str] = []
    normalized_refs: list[str] = []
    attempt_refs: list[str] = []
    normalized_readback_rows = 0

    manifest_path = baostock_root / BAOSTOCK_MANIFEST_RELATIVE_PATH
    manifest_hash: str | None = None
    manifest_entries: tuple[_ManifestEntry, ...] = ()
    if not manifest_path.exists():
        gaps.append(
            _gap(
                "seed_manifest_missing",
                "blocker",
                f"Baostock seed manifest missing: {manifest_path}",
                {"path": str(manifest_path)},
            )
        )
    elif manifest_path.stat().st_size == 0:
        gaps.append(
            _gap(
                "seed_manifest_empty",
                "blocker",
                f"Baostock seed manifest is empty: {manifest_path}",
                {"path": str(manifest_path)},
            )
        )
    else:
        manifest_bytes = manifest_path.read_bytes()
        manifest_hash = _sha256_bytes(manifest_bytes)
        manifest_entries, manifest_gaps = _parse_manifest(manifest_path=manifest_path, root=baostock_root)
        gaps.extend(manifest_gaps)
        if manifest_entries:
            gaps.extend(
                _validate_baostock_seed_coverage(
                    root=baostock_root,
                    manifest_entries=manifest_entries,
                    start_date=start_date,
                    trade_date=trade_date,
                )
            )

    stock_codes_for_private = _stock_codes_from_universe(baostock_root / "universe" / "a_share_stock_universe.csv")
    if not private_placement_root.exists():
        gaps.append(
            _gap(
                "private_placement_root_missing",
                "blocker",
                f"AkShare private placement seed root missing: {private_placement_root}",
                {"path": str(private_placement_root)},
            )
        )
    else:
        gaps.extend(
            _validate_private_placement_coverage(
                root=private_placement_root,
                stock_codes=stock_codes_for_private,
            )
        )

    if manifest_entries:
        gaps.extend(
            _validate_baostock_import_rows(
                manifest_entries=manifest_entries,
                trade_date=trade_date,
            )
        )

    if any(gap.severity == "blocker" for gap in gaps):
        _write_failure_attempt(
            attempt_store=attempt_store,
            import_run_id=import_run_id,
            trade_date=trade_date,
            provider=BAOSTOCK_PROVIDER_ID,
            endpoint="seed_import",
            error_code="seed_import_blocked",
            error_message="; ".join(gap.gap_code for gap in gaps if gap.severity == "blocker"),
            source_metadata={"manifest_hash": manifest_hash, "baostock_root": str(baostock_root)},
            attempt_refs=attempt_refs,
        )
        return AShareSeedImportResult(
            import_run_id=import_run_id,
            trade_date=trade_date,
            manifest_hash=manifest_hash,
            raw_refs=tuple(raw_refs),
            normalized_refs=tuple(normalized_refs),
            attempt_refs=tuple(attempt_refs),
            data_gaps=tuple(gaps),
            readback_counts={"raw": 0, "normalized_rows": 0, "attempts": len(attempt_refs)},
            warehouse_check_ref=None,
        )

    for entry in manifest_entries:
        if not entry.path.suffix.lower() == ".csv":
            continue
        file_gaps = _validate_manifest_entry(entry)
        if file_gaps:
            gaps.extend(file_gaps)
            continue
        imported = _import_baostock_csv(
            entry=entry,
            manifest_hash=manifest_hash,
            import_run_id=import_run_id,
            trade_date=trade_date,
            raw_store=raw_store,
            normalized_store=normalized_store,
            attempt_store=attempt_store,
            attempt_refs=attempt_refs,
        )
        raw_refs.extend(imported.raw_refs)
        normalized_refs.extend(imported.normalized_refs)
        normalized_readback_rows += _readback_normalized_rows(normalized_store, imported.normalized_mongo_refs)
        gaps.extend(imported.gaps)

    if private_placement_root.exists():
        imported_events = _import_private_placement_events(
            root=private_placement_root,
            import_run_id=import_run_id,
            trade_date=trade_date,
            raw_store=raw_store,
            normalized_store=normalized_store,
            attempt_store=attempt_store,
            manifest_hash=manifest_hash,
            attempt_refs=attempt_refs,
        )
        raw_refs.extend(imported_events.raw_refs)
        normalized_refs.extend(imported_events.normalized_refs)
        normalized_readback_rows += _readback_normalized_rows(normalized_store, imported_events.normalized_mongo_refs)
        gaps.extend(imported_events.gaps)

    warehouse_check_ref = None
    if normalized_refs and not any(gap.severity == "blocker" for gap in gaps):
        digest = _short_digest("|".join((import_run_id, trade_date, *(sorted(normalized_refs)))))
        warehouse_check_ref = f"warehouse-check://selection/{import_run_id}/{trade_date}/{digest}"

    return AShareSeedImportResult(
        import_run_id=import_run_id,
        trade_date=trade_date,
        manifest_hash=manifest_hash,
        raw_refs=tuple(raw_refs),
        normalized_refs=tuple(normalized_refs),
        attempt_refs=tuple(attempt_refs),
        data_gaps=tuple(gaps),
        readback_counts={
            "raw": len(raw_refs),
            "normalized_rows": normalized_readback_rows,
            "attempts": len(attempt_refs),
        },
        warehouse_check_ref=warehouse_check_ref,
    )


@dataclass(frozen=True)
class _ManifestEntry:
    path: Path
    relative_path: str
    expected_sha256: str | None


@dataclass(frozen=True)
class _ImportedFile:
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    normalized_mongo_refs: tuple[str, ...]
    gaps: tuple[SeedImportGap, ...]


def _parse_manifest(*, manifest_path: Path, root: Path) -> tuple[tuple[_ManifestEntry, ...], tuple[SeedImportGap, ...]]:
    entries: list[_ManifestEntry] = []
    gaps: list[SeedImportGap] = []
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        parts = text.split()
        expected_sha256: str | None = None
        relative_path: str
        if len(parts) >= 2 and _is_hex_sha256(parts[0]):
            expected_sha256 = parts[0].lower()
            relative_path = parts[-1]
        else:
            relative_path = parts[-1]
        relative_path = relative_path.removeprefix("./")
        resolved = _resolve_manifest_entry_path(root=root, raw_path=relative_path)
        if resolved is None:
            gaps.append(
                _gap(
                    "seed_manifest_path_outside_root",
                    "blocker",
                    f"Manifest line points outside seed root: line={line_no}, path={relative_path}",
                    {"manifest": str(manifest_path), "line_no": line_no, "relative_path": relative_path},
                )
            )
            continue
        path, stored_relative_path = resolved
        relative_path = stored_relative_path
        entries.append(_ManifestEntry(path=path, relative_path=relative_path, expected_sha256=expected_sha256))
    if not entries:
        gaps.append(
            _gap(
                "seed_manifest_no_files",
                "blocker",
                f"Baostock manifest has no files: {manifest_path}",
                {"manifest": str(manifest_path)},
            )
        )
    return tuple(entries), tuple(gaps)


def _resolve_manifest_entry_path(*, root: Path, raw_path: str) -> tuple[Path, str] | None:
    root_path = root.resolve()
    candidate_paths: list[Path] = []
    candidate = Path(raw_path)
    if candidate.is_absolute():
        candidate_paths.append(candidate.resolve())
    else:
        candidate_paths.append((root / candidate).resolve())
        candidate_paths.append(candidate.resolve())

    for path in candidate_paths:
        if _path_relative_to(path, root_path) is not None and path.exists():
            return path, _path_relative_to(path, root_path) or raw_path
    for path in candidate_paths:
        relative = _path_relative_to(path, root_path)
        if relative is not None:
            return path, relative
    return None


def _path_relative_to(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _validate_manifest_entry(entry: _ManifestEntry) -> tuple[SeedImportGap, ...]:
    if not entry.path.exists():
        return (
            _gap(
                "seed_file_missing",
                "blocker",
                f"Seed file listed in manifest is missing: {entry.relative_path}",
                {"path": str(entry.path), "relative_path": entry.relative_path},
            ),
        )
    if entry.path.stat().st_size == 0:
        return (
            _gap(
                "seed_file_empty",
                "blocker",
                f"Seed file listed in manifest is empty: {entry.relative_path}",
                {"path": str(entry.path), "relative_path": entry.relative_path},
            ),
        )
    if entry.expected_sha256 is not None:
        actual = _sha256_bytes(entry.path.read_bytes()).removeprefix("sha256:")
        if actual != entry.expected_sha256:
            return (
                _gap(
                    "seed_file_hash_mismatch",
                    "blocker",
                    f"Seed file hash mismatch: {entry.relative_path}",
                    {
                        "path": str(entry.path),
                        "relative_path": entry.relative_path,
                        "expected_sha256": entry.expected_sha256,
                        "actual_sha256": actual,
                    },
                ),
            )
    return ()


def _validate_baostock_seed_coverage(
    *,
    root: Path,
    manifest_entries: tuple[_ManifestEntry, ...],
    start_date: str,
    trade_date: str,
) -> tuple[SeedImportGap, ...]:
    gaps: list[SeedImportGap] = []
    manifest_relatives = {entry.relative_path for entry in manifest_entries}
    stock_universe_relative = "universe/a_share_stock_universe.csv"
    index_universe_relative = "universe/index_universe.csv"
    basic_relative = "basic/stock_basic.csv"
    industry_relative = "basic/stock_industry.csv"

    gaps.extend(
        _validate_required_seed_files(
            root=root,
            manifest_relatives=manifest_relatives,
            category="stock_universe",
            required_relatives=(stock_universe_relative,),
            start_date=start_date,
            trade_date=trade_date,
            check_trade_date=False,
            check_start_date=False,
            expected_adjustflag=None,
            first_date_by_relative={},
        )
    )
    gaps.extend(
        _validate_required_seed_files(
            root=root,
            manifest_relatives=manifest_relatives,
            category="index_universe",
            required_relatives=(index_universe_relative,),
            start_date=start_date,
            trade_date=trade_date,
            check_trade_date=False,
            check_start_date=False,
            expected_adjustflag=None,
            first_date_by_relative={},
        )
    )
    gaps.extend(
        _validate_required_seed_files(
            root=root,
            manifest_relatives=manifest_relatives,
            category="basic",
            required_relatives=(basic_relative,),
            start_date=start_date,
            trade_date=trade_date,
            check_trade_date=False,
            check_start_date=False,
            expected_adjustflag=None,
            first_date_by_relative={},
        )
    )
    gaps.extend(
        _validate_required_seed_files(
            root=root,
            manifest_relatives=manifest_relatives,
            category="industry",
            required_relatives=(industry_relative,),
            start_date=start_date,
            trade_date=trade_date,
            check_trade_date=False,
            check_start_date=False,
            expected_adjustflag=None,
            first_date_by_relative={},
        )
    )
    stock_codes = _stock_codes_from_universe(root / stock_universe_relative)
    if not stock_codes:
        gaps.append(
            _gap(
                "stock_universe_coverage_empty",
                "blocker",
                "A-share stock universe is missing or contains no stock codes",
                {"relative_path": stock_universe_relative},
            )
        )
    index_codes = _stock_codes_from_universe(root / index_universe_relative)
    if not index_codes:
        gaps.append(
            _gap(
                "index_universe_coverage_empty",
                "blocker",
                "A-share index universe is missing or contains no index codes",
                {"relative_path": index_universe_relative},
            )
        )
    stock_basic_rows = _read_csv_rows(root / basic_relative)
    industry_rows = _read_csv_rows(root / industry_relative)
    gaps.extend(
        _validate_code_coverage(
            category="basic",
            expected_codes=stock_codes,
            actual_codes=_row_codes(stock_basic_rows),
            relative_path=basic_relative,
        )
    )
    gaps.extend(
        _validate_code_coverage(
            category="industry",
            expected_codes=stock_codes,
            actual_codes=_row_codes(industry_rows),
            relative_path=industry_relative,
            severity="warn",
        )
    )
    first_date_by_relative = {
        f"daily/qfq/{code}.csv": _expected_first_date(
            start_date=start_date,
            ipo_date=_stock_basic_by_code(stock_basic_rows).get(code, {}).get("ipoDate", ""),
        )
        for code in stock_codes
    }
    if stock_codes:
        gaps.extend(
            _validate_required_seed_files(
                root=root,
                manifest_relatives=manifest_relatives,
                category="qfq_daily",
                required_relatives=tuple(f"daily/qfq/{code}.csv" for code in stock_codes),
                start_date=start_date,
                trade_date=trade_date,
                check_trade_date=False,
                check_start_date=False,
                expected_adjustflag="2",
                first_date_by_relative=first_date_by_relative,
            )
        )
        gaps.extend(
            _validate_required_seed_files(
                root=root,
                manifest_relatives=manifest_relatives,
                category="adjust_factor",
                required_relatives=tuple(f"adjust_factor/{code}_factor.csv" for code in stock_codes),
                start_date=start_date,
                trade_date=trade_date,
                check_trade_date=False,
                check_start_date=False,
                expected_adjustflag=None,
                first_date_by_relative={},
            )
        )

    if index_codes:
        required_index_codes = tuple(code for code in index_codes if code not in OPTIONAL_A_SHARE_INDEX_DAILY_CODES)
        gaps.extend(
            _validate_required_seed_files(
                root=root,
                manifest_relatives=manifest_relatives,
                category="index_daily",
                required_relatives=tuple(f"index_daily/{code}.csv" for code in required_index_codes),
                start_date=start_date,
                trade_date=trade_date,
                check_trade_date=False,
                check_start_date=False,
                expected_adjustflag=None,
                first_date_by_relative={},
            )
        )
    return tuple(gaps)


def _validate_required_seed_files(
    *,
    root: Path,
    manifest_relatives: set[str],
    category: str,
    required_relatives: tuple[str, ...],
    start_date: str,
    trade_date: str,
    check_trade_date: bool,
    check_start_date: bool,
    expected_adjustflag: str | None,
    first_date_by_relative: Mapping[str, str],
) -> tuple[SeedImportGap, ...]:
    missing_files: list[str] = []
    missing_manifest_entries: list[str] = []
    empty_files: list[str] = []
    stale_files: list[Mapping[str, str]] = []
    incomplete_start_files: list[Mapping[str, str]] = []
    adjustflag_mismatches: list[Mapping[str, str | int]] = []
    for relative in required_relatives:
        path = root / relative
        if relative not in manifest_relatives:
            missing_manifest_entries.append(relative)
        if not path.exists():
            missing_files.append(relative)
            continue
        if path.stat().st_size == 0:
            empty_files.append(relative)
            continue
        if check_trade_date or check_start_date or expected_adjustflag is not None:
            audit = _audit_csv_file(path, expected_adjustflag=expected_adjustflag)
            if check_trade_date and audit["last_date"] and str(audit["last_date"]) < trade_date:
                stale_files.append({"relative_path": relative, "last_date": str(audit["last_date"]), "trade_date": trade_date})
            expected_first = first_date_by_relative.get(relative, start_date)
            if check_start_date and expected_first and audit["first_date"] and str(audit["first_date"]) > expected_first:
                incomplete_start_files.append(
                    {
                        "relative_path": relative,
                        "first_date": str(audit["first_date"]),
                        "expected_first": expected_first,
                    }
                )
            if expected_adjustflag is not None:
                if audit["adjustflag_missing"]:
                    adjustflag_mismatches.append(
                        {
                            "relative_path": relative,
                            "expected_adjustflag": expected_adjustflag,
                            "mismatch_count": int(audit["row_count"]),
                            "reason": "adjustflag_missing",
                        }
                    )
                elif int(audit["adjustflag_mismatch_count"]) > 0:
                    adjustflag_mismatches.append(
                        {
                            "relative_path": relative,
                            "expected_adjustflag": expected_adjustflag,
                            "mismatch_count": int(audit["adjustflag_mismatch_count"]),
                            "reason": "adjustflag_mismatch",
                        }
                    )

    gaps: list[SeedImportGap] = []
    if missing_files:
        gaps.append(
            _gap(
                f"{category}_coverage_missing_files",
                "blocker",
                f"{category} seed coverage missing files",
                {"category": category, "count": len(missing_files), "sample": tuple(missing_files[:50])},
            )
        )
    if missing_manifest_entries:
        gaps.append(
            _gap(
                f"{category}_coverage_missing_manifest_entries",
                "blocker",
                f"{category} seed coverage missing manifest entries",
                {"category": category, "count": len(missing_manifest_entries), "sample": tuple(missing_manifest_entries[:50])},
            )
        )
    if empty_files:
        gaps.append(
            _gap(
                f"{category}_coverage_empty_files",
                "blocker",
                f"{category} seed coverage has empty files",
                {"category": category, "count": len(empty_files), "sample": tuple(empty_files[:50])},
            )
        )
    if stale_files:
        gaps.append(
            _gap(
                f"{category}_coverage_stale_files",
                "blocker",
                f"{category} seed coverage stale before trade date",
                {"category": category, "count": len(stale_files), "sample": tuple(stale_files[:50])},
            )
        )
    if incomplete_start_files:
        gaps.append(
            _gap(
                f"{category}_coverage_start_incomplete",
                "blocker",
                f"{category} seed coverage starts after expected start date",
                {"category": category, "count": len(incomplete_start_files), "sample": tuple(incomplete_start_files[:50])},
            )
        )
    if adjustflag_mismatches:
        gaps.append(
            _gap(
                f"{category}_adjustflag_mismatch",
                "blocker",
                f"{category} seed adjustment flag does not match required QFQ value",
                {"category": category, "count": len(adjustflag_mismatches), "sample": tuple(adjustflag_mismatches[:50])},
            )
        )
    return tuple(gaps)


def _validate_baostock_import_rows(
    *,
    manifest_entries: tuple[_ManifestEntry, ...],
    trade_date: str,
) -> tuple[SeedImportGap, ...]:
    gaps: list[SeedImportGap] = []
    for entry in manifest_entries:
        if entry.path.suffix.lower() != ".csv":
            continue
        file_gaps = _validate_manifest_entry(entry)
        if file_gaps:
            gaps.extend(file_gaps)
            continue
        category = _baostock_category(entry.relative_path)
        rows, row_gaps = _normalize_baostock_rows(entry=entry, category=category, trade_date=trade_date)
        gaps.extend(row_gaps)
        if not row_gaps and not rows:
            gaps.append(
                _gap(
                    "seed_file_no_importable_rows",
                    "blocker",
                    f"Seed file has no importable rows: {entry.relative_path}",
                    {"path": str(entry.path), "relative_path": entry.relative_path, "category": category},
                )
            )
    return tuple(gaps)


def _read_csv_rows(path: Path) -> tuple[Mapping[str, str], ...]:
    if not path.exists() or path.stat().st_size == 0:
        return ()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(csv.DictReader(handle))


def _stock_codes_from_universe(path: Path) -> tuple[str, ...]:
    return tuple(sorted(_row_codes(_read_csv_rows(path))))


def _stock_basic_by_code(rows: tuple[Mapping[str, str], ...]) -> Mapping[str, Mapping[str, str]]:
    return {code: row for row in rows for code in (_normalize_seed_code_to_baostock(str(row.get("code") or "")),) if code}


def _row_codes(rows: tuple[Mapping[str, str], ...]) -> set[str]:
    codes: set[str] = set()
    for row in rows:
        raw = str(
            row.get("code")
            or row.get("股票代码")
            or row.get("证券代码")
            or row.get("symbol")
            or ""
        ).strip()
        code = _normalize_seed_code_to_baostock(raw)
        if code:
            codes.add(code)
    return codes


def _normalize_seed_code_to_baostock(raw: str) -> str | None:
    value = raw.strip().lower()
    if not value:
        return None
    if "." in value:
        left, right = value.split(".", 1)
        if left in {"sh", "sz", "bj"} and len(right) == 6 and right.isdigit():
            return f"{left}.{right}"
        if right in {"sh", "sz", "bj"} and len(left) == 6 and left.isdigit():
            return f"{right}.{left}"
    if len(value) == 6 and value.isdigit():
        if value.startswith(("6", "9")):
            return f"sh.{value}"
        if value.startswith(("0", "2", "3")):
            return f"sz.{value}"
        if value.startswith(("4", "8")):
            return f"bj.{value}"
    return None


def _validate_code_coverage(
    *,
    category: str,
    expected_codes: tuple[str, ...],
    actual_codes: set[str],
    relative_path: str,
    severity: str = "blocker",
) -> tuple[SeedImportGap, ...]:
    if not expected_codes:
        return ()
    missing = tuple(code for code in expected_codes if code not in actual_codes)
    if not missing:
        return ()
    return (
        _gap(
            f"{category}_coverage_missing_codes",
            severity,
            f"{category} seed coverage missing universe codes",
            {"category": category, "relative_path": relative_path, "count": len(missing), "sample": missing[:50]},
        ),
    )


def _expected_first_date(*, start_date: str, ipo_date: str) -> str:
    if not ipo_date:
        return start_date
    try:
        ipo = date.fromisoformat(ipo_date[:10])
        start = date.fromisoformat(start_date)
    except ValueError:
        return start_date
    expected = max(start, ipo) + timedelta(days=14)
    return expected.isoformat()


def _audit_csv_file(path: Path, *, expected_adjustflag: str | None) -> Mapping[str, str | int | bool]:
    last_date = ""
    first_date = ""
    row_count = 0
    adjustflag_missing = False
    adjustflag_mismatch_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        if expected_adjustflag is not None and "adjustflag" not in fieldnames:
            adjustflag_missing = True
        for row in reader:
            row_count += 1
            candidate = str(row.get("date") or row.get("dividOperateDate") or "").strip()
            if candidate and not first_date:
                first_date = candidate
            if candidate:
                last_date = candidate
            if expected_adjustflag is not None:
                actual = str(row.get("adjustflag") or "").strip()
                if actual != expected_adjustflag:
                    adjustflag_mismatch_count += 1
    return {
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
        "adjustflag_missing": adjustflag_missing,
        "adjustflag_mismatch_count": adjustflag_mismatch_count,
    }


def _validate_private_placement_coverage(
    *,
    root: Path,
    stock_codes: tuple[str, ...],
) -> tuple[SeedImportGap, ...]:
    gaps: list[SeedImportGap] = []
    if not stock_codes:
        gaps.append(
            _gap(
                "private_placement_universe_missing",
                "blocker",
                "Cannot validate AkShare private placement coverage without A-share stock universe",
                {"path": str(root)},
            )
        )
        return tuple(gaps)
    manifest_gaps = _validate_private_placement_manifest(root=root, expected_count=len(stock_codes))
    gaps.extend(manifest_gaps)
    missing_files: list[str] = []
    empty_files: list[str] = []
    missing_fields: list[str] = []
    for code in stock_codes:
        symbol = code.split(".", 1)[1] if "." in code else code
        relative = f"{symbol}.csv"
        path = root / relative
        if not path.exists():
            missing_files.append(relative)
            continue
        if path.stat().st_size == 0:
            empty_files.append(relative)
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            fieldnames = set(csv.DictReader(handle).fieldnames or ())
        if not {"公告日期", "发行方式"}.issubset(fieldnames):
            missing_fields.append(relative)
    if missing_files:
        gaps.append(
            _gap(
                "private_placement_coverage_missing_files",
                "blocker",
                "AkShare private placement seed coverage missing files",
                {"count": len(missing_files), "sample": tuple(missing_files[:50])},
            )
        )
    if empty_files:
        gaps.append(
            _gap(
                "private_placement_coverage_empty_files",
                "blocker",
                "AkShare private placement seed coverage has empty files",
                {"count": len(empty_files), "sample": tuple(empty_files[:50])},
            )
        )
    if missing_fields:
        gaps.append(
            _gap(
                "private_placement_coverage_missing_fields",
                "blocker",
                "AkShare private placement seed coverage files are missing required fields",
                {"count": len(missing_fields), "sample": tuple(missing_fields[:50]), "required_fields": ("公告日期", "发行方式")},
            )
        )
    return tuple(gaps)


def _validate_private_placement_manifest(*, root: Path, expected_count: int) -> tuple[SeedImportGap, ...]:
    manifest_dir = root / "manifest"
    manifests = tuple(sorted(path for path in manifest_dir.glob("*.json") if path.is_file()))
    if not manifests:
        return (
            _gap(
                "private_placement_manifest_missing",
                "blocker",
                f"AkShare private placement manifest missing: {manifest_dir}",
                {"path": str(manifest_dir)},
            ),
        )
    completed: list[Mapping[str, Any]] = []
    for path in manifests:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("status") == "completed":
            completed.append(payload)
    for payload in completed:
        stock_count = int(payload.get("stock_count") or 0)
        success_count = int(payload.get("success_count") or 0)
        failure_count = int(payload.get("failure_count") or 0)
        if stock_count >= expected_count and success_count >= expected_count and failure_count == 0:
            return ()
    return (
        _gap(
            "private_placement_manifest_incomplete",
            "blocker",
            "AkShare private placement manifest does not prove full stock-universe coverage",
            {
                "path": str(manifest_dir),
                "expected_count": expected_count,
                "completed_manifest_count": len(completed),
            },
        ),
    )


def _import_baostock_csv(
    *,
    entry: _ManifestEntry,
    manifest_hash: str | None,
    import_run_id: str,
    trade_date: str,
    raw_store: Any,
    normalized_store: Any,
    attempt_store: Any,
    attempt_refs: list[str],
) -> _ImportedFile:
    source_file_hash = _sha256_bytes(entry.path.read_bytes())
    category = _baostock_category(entry.relative_path)
    rows, gaps = _normalize_baostock_rows(entry=entry, category=category, trade_date=trade_date)
    if gaps:
        return _ImportedFile(raw_refs=(), normalized_refs=(), normalized_mongo_refs=(), gaps=gaps)
    if not rows:
        return _ImportedFile(
            raw_refs=(),
            normalized_refs=(),
            normalized_mongo_refs=(),
            gaps=(
                _gap(
                    "seed_file_no_importable_rows",
                    "blocker",
                    f"Seed file has no importable rows: {entry.relative_path}",
                    {"path": str(entry.path), "relative_path": entry.relative_path, "category": category},
                ),
            ),
        )
    request = _request(import_run_id=import_run_id, trade_date=trade_date, call_id=f"baostock:{_short_digest(entry.relative_path)}")
    spec = _spec(
        provider=BAOSTOCK_PROVIDER_ID,
        endpoint=f"seed_import:{category}",
        schema_id=_baostock_schema_id(category),
        trade_date=trade_date,
        params={
            "relative_path": entry.relative_path,
            "source_file_hash": source_file_hash,
            "manifest_hash": manifest_hash,
            "category": category,
        },
    )
    raw_ref = raw_store.write_raw(
        request=request,
        spec=spec,
        payload={
            "source": BAOSTOCK_PROVIDER_ID,
            "relative_path": entry.relative_path,
            "source_file_hash": source_file_hash,
            "manifest_hash": manifest_hash,
            "row_count": len(rows),
        },
        content_type="application/json",
        source_url=entry.path.resolve().as_uri(),
        raw_export_policy="metadata_only",
    )
    normalized = NormalizedResult(
        status=ProviderStatus.NOT_APPLICABLE,
        schema_id=_baostock_schema_id(category),
        rows=tuple(rows),
        compact_facts={
            "market": "CN_A",
            "source": BAOSTOCK_PROVIDER_ID,
            "category": category,
            "row_count": len(rows),
            "manifest_hash": manifest_hash,
            "source_file_hash": source_file_hash,
        },
        row_count=len(rows),
        field_units=_field_units(category),
        currency="CNY",
        timezone="Asia/Shanghai",
        source_raw_ref=raw_ref,
    )
    normalized_mongo_ref = normalized_store.write(request=request, spec=spec, normalized=normalized)
    attempt_ref = _write_success_attempt(
        attempt_store=attempt_store,
        import_run_id=import_run_id,
        trade_date=trade_date,
        provider=BAOSTOCK_PROVIDER_ID,
        endpoint=spec.endpoint,
        schema_id=normalized.schema_id,
        row_count=len(rows),
        raw_ref=raw_ref,
        normalized_ref=normalized_mongo_ref,
        source_metadata={
            "local_seed_import_status": "imported",
            "manifest_hash": manifest_hash,
            "source_file_hash": source_file_hash,
            "relative_path": entry.relative_path,
            "category": category,
        },
    )
    attempt_refs.append(attempt_ref)
    return _ImportedFile(
        raw_refs=(raw_ref,),
        normalized_refs=(_normalized_selection_ref(normalized_mongo_ref),),
        normalized_mongo_refs=(normalized_mongo_ref,),
        gaps=(),
    )


def _normalize_baostock_rows(
    *,
    entry: _ManifestEntry,
    category: str,
    trade_date: str,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[SeedImportGap, ...]]:
    with entry.path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        if not fieldnames:
            return (), (
                _gap(
                    "seed_file_missing_header",
                    "blocker",
                    f"Seed CSV has no header: {entry.relative_path}",
                    {"path": str(entry.path), "relative_path": entry.relative_path},
                ),
            )
        rows = tuple(reader)
    if category == "qfq_daily":
        return _normalize_qfq_daily_rows(entry=entry, rows=rows, fieldnames=fieldnames, trade_date=trade_date)
    return _normalize_generic_baostock_rows(entry=entry, rows=rows, category=category)


def _normalize_qfq_daily_rows(
    *,
    entry: _ManifestEntry,
    rows: tuple[Mapping[str, str], ...],
    fieldnames: tuple[str, ...],
    trade_date: str,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[SeedImportGap, ...]]:
    required_fields = {"date", "code", "open", "high", "low", "close", "volume"}
    missing = tuple(sorted(required_fields.difference(fieldnames)))
    if missing:
        return (), (
            _gap(
                "seed_file_missing_fields",
                "blocker",
                f"QFQ daily CSV missing fields: {entry.relative_path}",
                {"path": str(entry.path), "relative_path": entry.relative_path, "missing_fields": missing},
            ),
        )
    deduped: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_row in rows:
        raw_code = str(raw_row.get("code") or "").strip().lower()
        ticker = normalize_baostock_a_share_code(raw_code)
        mapped = parse_baostock_daily_row(raw_row)
        if ticker is None or mapped is None:
            continue
        row_date = str(mapped["date"])
        if row_date > trade_date:
            continue
        normalized_row = {
            **mapped,
            "ticker": ticker,
            "provider_raw_code": raw_code,
            "trade_date": row_date,
            "timezone": "Asia/Shanghai",
            "adjustment": "qfq",
            "currency": "CNY",
            "source_file": entry.relative_path,
        }
        key = (ticker, row_date)
        previous = deduped.get(key)
        if previous is not None and previous != normalized_row:
            return (), (
                _gap(
                    "qfq_daily_duplicate_conflict",
                    "blocker",
                    f"QFQ daily duplicate conflict: ticker={ticker}, date={row_date}",
                    {"path": str(entry.path), "relative_path": entry.relative_path, "ticker": ticker, "trade_date": row_date},
                ),
            )
        deduped[key] = normalized_row
    sorted_rows = tuple(deduped[key] for key in sorted(deduped))
    return sorted_rows, ()


def _normalize_generic_baostock_rows(
    *,
    entry: _ManifestEntry,
    rows: tuple[Mapping[str, str], ...],
    category: str,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[SeedImportGap, ...]]:
    normalized_rows: list[Mapping[str, Any]] = []
    for idx, row in enumerate(rows):
        clean = {str(key): str(value).strip() for key, value in row.items() if key is not None}
        raw_code = str(clean.get("code") or clean.get("股票代码") or "").strip().lower()
        ticker = normalize_baostock_a_share_code(raw_code) if raw_code else None
        normalized_rows.append(
            {
                **clean,
                "ticker": ticker,
                "provider_raw_code": raw_code or None,
                "source_category": category,
                "source_file": entry.relative_path,
                "source_row_number": idx + 2,
            }
        )
    return tuple(normalized_rows), ()


def _import_private_placement_events(
    *,
    root: Path,
    import_run_id: str,
    trade_date: str,
    raw_store: Any,
    normalized_store: Any,
    attempt_store: Any,
    manifest_hash: str | None,
    attempt_refs: list[str],
) -> _ImportedFile:
    csv_paths = tuple(sorted(path for path in root.rglob("*.csv") if path.is_file()))
    if not csv_paths:
        return _ImportedFile(
            raw_refs=(),
            normalized_refs=(),
            normalized_mongo_refs=(),
            gaps=(
                _gap(
                    "private_placement_files_missing",
                    "blocker",
                    f"AkShare private placement seed root has no CSV files: {root}",
                    {"path": str(root)},
                ),
            ),
        )
    all_raw_refs: list[str] = []
    all_normalized_refs: list[str] = []
    all_normalized_mongo_refs: list[str] = []
    all_gaps: list[SeedImportGap] = []
    for path in csv_paths:
        imported = _import_private_placement_file(
            path=path,
            root=root,
            import_run_id=import_run_id,
            trade_date=trade_date,
            raw_store=raw_store,
            normalized_store=normalized_store,
            attempt_store=attempt_store,
            manifest_hash=manifest_hash,
            attempt_refs=attempt_refs,
        )
        all_raw_refs.extend(imported.raw_refs)
        all_normalized_refs.extend(imported.normalized_refs)
        all_normalized_mongo_refs.extend(imported.normalized_mongo_refs)
        all_gaps.extend(imported.gaps)
    return _ImportedFile(
        raw_refs=tuple(all_raw_refs),
        normalized_refs=tuple(all_normalized_refs),
        normalized_mongo_refs=tuple(all_normalized_mongo_refs),
        gaps=tuple(all_gaps),
    )


def _import_private_placement_file(
    *,
    path: Path,
    root: Path,
    import_run_id: str,
    trade_date: str,
    raw_store: Any,
    normalized_store: Any,
    attempt_store: Any,
    manifest_hash: str | None,
    attempt_refs: list[str],
) -> _ImportedFile:
    relative_path = str(path.relative_to(root))
    source_file_hash = _sha256_bytes(path.read_bytes())
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = tuple(reader)
    missing = tuple(field for field in ("公告日期", "发行方式") if field not in fieldnames)
    if missing:
        return _ImportedFile(
            raw_refs=(),
            normalized_refs=(),
            normalized_mongo_refs=(),
            gaps=(
                _gap(
                    "private_placement_missing_fields",
                    "blocker",
                    f"Private placement CSV missing fields: {relative_path}",
                    {"path": str(path), "relative_path": relative_path, "missing_fields": missing},
                ),
            ),
        )
    ticker_from_file = _ticker_from_private_file(path)
    deduped: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for raw_row in rows:
        event_date = parse_private_placement_event_date(raw_row.get("公告日期"))
        if event_date is None or event_date.isoformat() > trade_date:
            continue
        event_type = str(raw_row.get("发行方式") or "").strip()
        if not is_private_placement_method(event_type):
            continue
        ticker = _ticker_from_private_row(raw_row) or ticker_from_file
        if ticker is None:
            continue
        normalized_row = {
            "ticker": ticker,
            "announcement_date": event_date.isoformat(),
            "event_type": event_type,
            "provider_raw_code": _raw_private_code(raw_row) or path.stem,
            "source": AKSHARE_PRIVATE_PLACEMENT_PROVIDER_ID,
            "source_file": relative_path,
            "raw": {str(key): str(value).strip() for key, value in raw_row.items() if key is not None},
        }
        key = (ticker, event_date.isoformat(), event_type)
        previous = deduped.get(key)
        if previous is not None and previous != normalized_row:
            return _ImportedFile(
                raw_refs=(),
                normalized_refs=(),
                normalized_mongo_refs=(),
                gaps=(
                    _gap(
                        "private_placement_duplicate_conflict",
                        "blocker",
                        f"Private placement duplicate conflict: ticker={ticker}, date={event_date.isoformat()}, event_type={event_type}",
                        {"path": str(path), "relative_path": relative_path, "ticker": ticker},
                    ),
                ),
            )
        deduped[key] = normalized_row
    normalized_rows = tuple(deduped[key] for key in sorted(deduped))
    if not normalized_rows:
        return _ImportedFile(raw_refs=(), normalized_refs=(), normalized_mongo_refs=(), gaps=())

    request = _request(
        import_run_id=import_run_id,
        trade_date=trade_date,
        call_id=f"akshare-private-placement:{_short_digest(relative_path)}",
    )
    spec = _spec(
        provider=AKSHARE_PRIVATE_PLACEMENT_PROVIDER_ID,
        endpoint="seed_import:private_placement_events",
        schema_id="cn_a.akshare.private_placement_events.v1",
        trade_date=trade_date,
        params={
            "relative_path": relative_path,
            "source_file_hash": source_file_hash,
            "baostock_manifest_hash": manifest_hash,
        },
    )
    raw_ref = raw_store.write_raw(
        request=request,
        spec=spec,
        payload={
            "source": AKSHARE_PRIVATE_PLACEMENT_PROVIDER_ID,
            "relative_path": relative_path,
            "source_file_hash": source_file_hash,
            "baostock_manifest_hash": manifest_hash,
            "row_count": len(normalized_rows),
        },
        content_type="application/json",
        source_url=path.resolve().as_uri(),
        raw_export_policy="metadata_only",
    )
    normalized = NormalizedResult(
        status=ProviderStatus.NOT_APPLICABLE,
        schema_id=spec.expected_schema_id,
        rows=normalized_rows,
        compact_facts={
            "market": "CN_A",
            "source": AKSHARE_PRIVATE_PLACEMENT_PROVIDER_ID,
            "row_count": len(normalized_rows),
            "source_file_hash": source_file_hash,
            "baostock_manifest_hash": manifest_hash,
        },
        row_count=len(normalized_rows),
        field_units={"announcement_date": "Asia/Shanghai trading calendar date"},
        currency="CNY",
        timezone="Asia/Shanghai",
        source_raw_ref=raw_ref,
    )
    normalized_mongo_ref = normalized_store.write(request=request, spec=spec, normalized=normalized)
    attempt_ref = _write_success_attempt(
        attempt_store=attempt_store,
        import_run_id=import_run_id,
        trade_date=trade_date,
        provider=AKSHARE_PRIVATE_PLACEMENT_PROVIDER_ID,
        endpoint=spec.endpoint,
        schema_id=normalized.schema_id,
        row_count=len(normalized_rows),
        raw_ref=raw_ref,
        normalized_ref=normalized_mongo_ref,
        source_metadata={
            "local_seed_import_status": "imported",
            "baostock_manifest_hash": manifest_hash,
            "source_file_hash": source_file_hash,
            "relative_path": relative_path,
        },
    )
    attempt_refs.append(attempt_ref)
    return _ImportedFile(
        raw_refs=(raw_ref,),
        normalized_refs=(_normalized_selection_ref(normalized_mongo_ref),),
        normalized_mongo_refs=(normalized_mongo_ref,),
        gaps=(),
    )


def _write_success_attempt(
    *,
    attempt_store: Any,
    import_run_id: str,
    trade_date: str,
    provider: str,
    endpoint: str,
    schema_id: str,
    row_count: int,
    raw_ref: str,
    normalized_ref: str,
    source_metadata: Mapping[str, Any],
) -> str:
    attempt_id = f"{import_run_id}:seed-import:{provider}:{endpoint}:{_short_digest(normalized_ref)}"
    attempt_store.write(
        _attempt(
            attempt_id=attempt_id,
            import_run_id=import_run_id,
            trade_date=trade_date,
            provider=provider,
            endpoint=endpoint,
            status=ProviderStatus.NOT_APPLICABLE,
            schema_id=schema_id,
            row_count=row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=None,
            error_message=None,
            source_metadata=source_metadata,
        )
    )
    return _attempt_ref(attempt_id)


def _write_failure_attempt(
    *,
    attempt_store: Any,
    import_run_id: str,
    trade_date: str,
    provider: str,
    endpoint: str,
    error_code: str,
    error_message: str,
    source_metadata: Mapping[str, Any],
    attempt_refs: list[str],
) -> None:
    attempt_id = f"{import_run_id}:seed-import:{provider}:{endpoint}:blocked"
    attempt_store.write(
        _attempt(
            attempt_id=attempt_id,
            import_run_id=import_run_id,
            trade_date=trade_date,
            provider=provider,
            endpoint=endpoint,
            status=ProviderStatus.FIELD_MISSING,
            schema_id="cn_a.seed_import.blocked.v1",
            row_count=0,
            raw_ref=None,
            normalized_ref=None,
            error_code=error_code,
            error_message=error_message,
            source_metadata=source_metadata,
        )
    )
    attempt_refs.append(_attempt_ref(attempt_id))


def _attempt(
    *,
    attempt_id: str,
    import_run_id: str,
    trade_date: str,
    provider: str,
    endpoint: str,
    status: ProviderStatus,
    schema_id: str,
    row_count: int,
    raw_ref: str | None,
    normalized_ref: str | None,
    error_code: str | None,
    error_message: str | None,
    source_metadata: Mapping[str, Any],
) -> ProviderAttempt:
    now = utc_now_iso()
    return ProviderAttempt(
        attempt_id=attempt_id,
        run_id=import_run_id,
        call_id="selection-seed-import",
        worker_id="selection_seed_importer",
        pack=PackDomain.SELECT_FEATURE.value,
        provider=provider,
        adapter_id=f"project.{provider}.seed_import",
        adapter_kind=ProviderKind.PROJECT_EXTENSION.value,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="seed-import-v1",
        endpoint=endpoint,
        source_role=SourceRole.MARKET_DATA,
        started_at=now,
        finished_at=now,
        status=status,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_selection_seed_import",
        coverage_quorum=1,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=False,
        cache_status=None,
        single_flight_role="none",
        shared_from_attempt_id=None,
        latency_ms=0,
        row_count=row_count,
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        error_code=error_code,
        error_message=error_message,
        schema_id=schema_id,
        license_note="local seed import; no HTTP evidence generated",
        source_metadata={**source_metadata, "trade_date": trade_date},
    )


def _request(*, import_run_id: str, trade_date: str, call_id: str) -> PackRequest:
    return PackRequest(
        run_id=import_run_id,
        call_id=call_id,
        worker_id="selection_seed_importer",
        market=Market.CN_A,
        domain=PackDomain.SELECT_FEATURE,
        ticker="CN_A_SELECTION_SEED",
        company_name="A股本地历史种子",
        start_date=trade_date,
        end_date=trade_date,
        current_date=trade_date,
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=86_400),
    )


def _spec(
    *,
    provider: str,
    endpoint: str,
    schema_id: str,
    trade_date: str,
    params: Mapping[str, Any],
) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"selection_seed_import:{provider}:{endpoint}:{sha256_text(json.dumps(params, sort_keys=True, default=str))}",
        provider=provider,
        adapter_id=f"project.{provider}.seed_import",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="seed-import-v1",
        endpoint=endpoint,
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.SELECT_FEATURE,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_selection_seed_import",
        coverage_quorum=1,
        params={**params, "trade_date": trade_date},
        cache_ttl_seconds=0,
        license_policy_id="local_seed_audit",
        expected_schema_id=schema_id,
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        raw_export_policy="metadata_only",
        data_type="cn_a_selection_seed_import",
    )


def _baostock_category(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    if "/daily/qfq/" in f"/{normalized}" or normalized.startswith("daily/qfq/"):
        return "qfq_daily"
    if "adjust_factor" in normalized:
        return "adjust_factor"
    if "index_daily" in normalized:
        return "index_daily"
    if "industry" in normalized:
        return "industry"
    if "basic" in normalized:
        return "basic"
    if "universe" in normalized:
        return "universe"
    return "seed_table"


def _baostock_schema_id(category: str) -> str:
    return f"cn_a.baostock.{category}.v1"


def _field_units(category: str) -> Mapping[str, str]:
    if category == "qfq_daily":
        return {
            "open": "CNY",
            "high": "CNY",
            "low": "CNY",
            "close": "CNY",
            "volume": "shares",
            "amount": "CNY",
            "turn": "percent",
            "p_change_pct": "percent",
        }
    return {}


def _ticker_from_private_file(path: Path) -> str | None:
    stem = path.stem.replace("_", ".").upper()
    if "." in stem:
        code, market = stem.split(".", 1)
        if len(code) == 6 and code.isdigit() and market in {"SH", "SZ", "BJ"}:
            return f"{code}.{market}"
    if len(stem) == 6 and stem.isdigit():
        if stem.startswith(("6", "9")):
            return f"{stem}.SH"
        if stem.startswith(("0", "2", "3")):
            return f"{stem}.SZ"
        if stem.startswith(("4", "8")):
            return f"{stem}.BJ"
    return None


def _ticker_from_private_row(row: Mapping[str, str]) -> str | None:
    raw = _raw_private_code(row)
    if raw is None:
        return None
    if "." in raw:
        code, market = raw.upper().split(".", 1)
        if len(code) == 6 and code.isdigit() and market in {"SH", "SZ", "BJ"}:
            return f"{code}.{market}"
    if len(raw) == 6 and raw.isdigit():
        return _ticker_from_private_file(Path(f"{raw}.csv"))
    return None


def _raw_private_code(row: Mapping[str, str]) -> str | None:
    for key in ("股票代码", "证券代码", "代码", "code"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _readback_normalized_rows(normalized_store: Any, refs: tuple[str, ...]) -> int:
    total = 0
    for ref in refs:
        item = normalized_store.read(ref)
        total += int(getattr(item, "row_count", 0) or 0)
    return total


def _normalized_selection_ref(mongo_ref: str) -> str:
    body = mongo_ref.removeprefix("mongo://")
    collection, _, document_id = body.partition("/")
    if collection == OPENBB_NORMALIZED and document_id:
        return f"normalized://mongo/{collection}/{document_id}"
    return mongo_ref


def _attempt_ref(attempt_id: str) -> str:
    return f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/{attempt_id}"


def _gap(gap_code: str, severity: str, message: str, source_metadata: Mapping[str, Any]) -> SeedImportGap:
    return SeedImportGap(
        gap_code=gap_code,
        severity=severity,
        message=message,
        source_metadata=dict(source_metadata),
    )


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _short_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _is_hex_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value)
