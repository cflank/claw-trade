#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from claw_trade.data_gateway.a_share_prepackaged_importer import load_a_share_identity_facts
from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse

PROVIDER_ID = "local_a_share_required"
ENDPOINT_ID = "a_share_required_columnar_import"
MARKET = "CN_A"
UNIVERSE_REF = "all_a_shares"
CALENDAR = "CN_A_SSE_SZSE"
TIMEZONE = "Asia/Shanghai"
CURRENCY = "CNY"
FACTOR_2026_START = "20260101"
DATASET_SCOPE = ("daily_bar", "valuation_metric")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import required local A-share fields into the normalized Parquet warehouse.")
    parser.add_argument("--daily-root", required=True, type=Path)
    parser.add_argument("--factor-root", required=True, type=Path)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--start-date", help="Optional inclusive normalized row start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Optional inclusive normalized row end date, YYYY-MM-DD. Defaults to --trade-date when omitted.")
    parser.add_argument("--import-run-id", required=True)
    parser.add_argument("--local-root", action="append", default=[], type=Path)
    parser.add_argument(
        "--identity-source-order",
        default=os.environ.get("CLAW_TRADE_A_SHARE_IDENTITY_SOURCE_ORDER", ""),
        help="Comma-separated online identity source order, for example: baostock,akshare,tushare.",
    )
    parser.add_argument(
        "--identity-fetch-timeout-seconds",
        default=float(os.environ.get("CLAW_TRADE_A_SHARE_IDENTITY_FETCH_TIMEOUT_SECONDS", "12")),
        type=float,
    )
    parser.add_argument("--tushare-token", default=os.environ.get("TUSHARE_TOKEN") or os.environ.get("CN_A_TUSHARE_TOKEN") or "")
    parser.add_argument("--tushare-endpoint-url", default=os.environ.get("TUSHARE_HTTP_URL") or "")
    parser.add_argument("--require-company-identity", action="store_true", help="Skip symbols that cannot be resolved to a real company identity.")
    parser.add_argument("--mongo-uri", default=os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "")
    parser.add_argument("--mongo-database", default=os.environ.get("DATA_GATEWAY_MONGODB_DATABASE") or os.environ.get("CN_A_MONGODB_DATABASE") or "")
    parser.add_argument("--columnar-root", type=Path, default=Path(os.environ["DATA_GATEWAY_COLUMNAR_ROOT"]) if os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT") else None)
    parser.add_argument("--work-dir", type=Path, default=Path(gettempdir()))
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--confirm-replace-existing", action="store_true")
    parser.add_argument("--keep-combined-zip", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    if args.replace_existing and not args.confirm_replace_existing:
        raise SystemExit("--replace-existing requires --confirm-replace-existing")
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be positive")
    start_date = _optional_iso_date(args.start_date, "--start-date")
    end_date = _optional_iso_date(args.end_date or args.trade_date, "--end-date/--trade-date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise SystemExit("--start-date cannot be after --end-date")

    combined = _prepare_factor_full_zip(args.factor_root, args.work_dir)
    try:
        audit = _audit_inputs(daily_root=args.daily_root, factor_root=args.factor_root, factor_full_zip=combined.path)
        if args.audit_only:
            _emit({"mode": "audit_only", "audit": audit}, args.output_json)
            return 0
        if not args.mongo_uri:
            raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

        from pymongo import MongoClient

        database_name = args.mongo_database or _database_name_from_uri(args.mongo_uri)
        client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
        database = client[database_name]
        repository = DatasetRepository.from_database(
            database,
            normalized_columnar=NormalizedColumnarWarehouse.from_env(root=args.columnar_root),
        )
        superseded_count = 0
        if args.replace_existing:
            superseded_count = _supersede_existing_required_manifests(database, import_run_id=args.import_run_id)

        raw_ref = _write_raw_package_record(
            repository=repository,
            import_run_id=args.import_run_id,
            audit=audit,
        )
        stats = _import_required_records(
            repository=repository,
            daily_root=args.daily_root,
            factor_full_zip=combined.path,
            factor_2026_zip=args.factor_root / "stk_factor_pro2026.zip",
            raw_ref=raw_ref,
            import_run_id=args.import_run_id,
            trade_date=args.trade_date,
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            chunk_size=args.chunk_size,
            max_symbols=args.max_symbols,
            local_roots=tuple(args.local_root),
            identity_source_order=_csv_items(args.identity_source_order),
            identity_fetch_timeout_seconds=args.identity_fetch_timeout_seconds,
            tushare_token=args.tushare_token or None,
            tushare_endpoint_url=args.tushare_endpoint_url or None,
            require_company_identity=args.require_company_identity,
        )
        attempt_ref = _write_attempt_record(
            repository=repository,
            import_run_id=args.import_run_id,
            raw_ref=raw_ref,
            stats=stats,
        )
        job_manifest_ref = _write_job_manifest(
            repository=repository,
            import_run_id=args.import_run_id,
            trade_date=args.trade_date,
            raw_ref=raw_ref,
            attempt_ref=attempt_ref,
            stats=stats,
            superseded_count=superseded_count,
        )
        output = {
            "mode": "import",
            "mongo_database": database_name,
            "catalog_storage": "mongo",
            "normalized_storage": "parquet",
            "columnar_root": str(NormalizedColumnarWarehouse.from_env(root=args.columnar_root).root),
            "raw_ref": raw_ref,
            "attempt_ref": attempt_ref,
            "job_manifest_ref": job_manifest_ref,
            "superseded_existing_manifest_count": superseded_count,
            **stats,
        }
        _emit(output, args.output_json)
        return 0
    finally:
        if combined.created and not args.keep_combined_zip:
            combined.path.unlink(missing_ok=True)


class _CombinedZip:
    def __init__(self, *, path: Path, created: bool) -> None:
        self.path = path
        self.created = created


def _prepare_factor_full_zip(factor_root: Path, work_dir: Path) -> _CombinedZip:
    direct = factor_root / "stk_factor_pro.zip"
    if _zip_can_list(direct):
        return _CombinedZip(path=direct, created=False)

    parts = tuple(factor_root / f"stk_factor_pro.zip.{suffix}" for suffix in ("001", "002", "003"))
    missing = tuple(str(path) for path in parts if not path.exists())
    if missing:
        raise SystemExit(f"missing factor split zip part(s): {missing}")

    work_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256("|".join(f"{path}:{path.stat().st_size}:{int(path.stat().st_mtime)}" for path in parts).encode("utf-8")).hexdigest()[:16]
    combined = work_dir / f"stk_factor_pro_combined-{fingerprint}.zip"
    if _zip_can_list(combined):
        return _CombinedZip(path=combined, created=True)
    with combined.open("wb") as out:
        for part in parts:
            with part.open("rb") as handle:
                shutil.copyfileobj(handle, out, length=16 * 1024 * 1024)
    if not _zip_can_list(combined):
        combined.unlink(missing_ok=True)
        raise SystemExit("combined factor split zip is not readable")
    return _CombinedZip(path=combined, created=True)


def _audit_inputs(*, daily_root: Path, factor_root: Path, factor_full_zip: Path) -> dict[str, Any]:
    factor_2026_zip = factor_root / "stk_factor_pro2026.zip"
    daily_files = tuple(sorted(daily_root.glob("*.csv"))) if daily_root.exists() else ()
    return {
        "daily_root": str(daily_root),
        "daily_file_count": len(daily_files),
        "daily_bytes": sum(path.stat().st_size for path in daily_files),
        "factor_root": str(factor_root),
        "factor_full_zip": str(factor_full_zip),
        "factor_full_zip_bytes": factor_full_zip.stat().st_size if factor_full_zip.exists() else 0,
        "factor_2026_zip": str(factor_2026_zip),
        "factor_2026_zip_bytes": factor_2026_zip.stat().st_size if factor_2026_zip.exists() else 0,
        "factor_full_symbol_count": len(_zip_symbols(factor_full_zip)),
        "factor_2026_symbol_count": len(_zip_symbols(factor_2026_zip)) if factor_2026_zip.exists() else 0,
        "split_parts": [
            {"path": str(path), "bytes": path.stat().st_size}
            for path in sorted(factor_root.glob("stk_factor_pro.zip.*"))
            if path.is_file()
        ],
    }


def _import_required_records(
    *,
    repository: DatasetRepository,
    daily_root: Path,
    factor_full_zip: Path,
    factor_2026_zip: Path,
    raw_ref: str,
    import_run_id: str,
    trade_date: str,
    start_date: str | None,
    end_date: str | None,
    chunk_size: int,
    max_symbols: int | None,
    local_roots: tuple[Path, ...],
    identity_source_order: tuple[str, ...],
    identity_fetch_timeout_seconds: float,
    tushare_token: str | None,
    tushare_endpoint_url: str | None,
    require_company_identity: bool,
) -> dict[str, Any]:
    as_of = datetime.now(tz=UTC)
    records: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "import_run_id": import_run_id,
        "trade_date": trade_date,
        "start_date": start_date,
        "end_date": end_date,
        "chunks_written": 0,
        "dataset_ref_count": 0,
        "dataset_ref_sample": [],
        "daily_bar_rows": 0,
        "valuation_metric_rows": 0,
        "daily_only_symbol_count": 0,
        "factor_full_symbol_count": 0,
        "factor_2026_symbol_count": 0,
        "identity_sources_attempted": [],
        "identity_sources_loaded": [],
        "identity_loaded_count": 0,
        "identity_errors": [],
        "company_identity_required": require_company_identity,
        "company_identity_skipped_symbol_count": 0,
        "company_identity_skipped_symbols_sample": [],
        "company_name_missing_symbol_count": 0,
        "date_ranges": {},
        "warnings": [],
    }

    factor_2026_symbols = _zip_symbols(factor_2026_zip) if factor_2026_zip.exists() else set()
    factor_full_symbols = _zip_symbols(factor_full_zip)
    factor_symbols = set(factor_full_symbols).union(factor_2026_symbols)
    selected_full_symbols = _limit_symbols(factor_full_symbols, max_symbols)
    selected_2026_symbols = _limit_symbols(factor_2026_symbols, max_symbols)
    daily_only_symbols = set(_daily_symbols(daily_root)).difference(factor_symbols)
    selected_daily_only = _limit_symbols(daily_only_symbols, max_symbols)
    selected_symbols = tuple(sorted(set(selected_full_symbols).union(selected_2026_symbols).union(selected_daily_only)))
    identity_facts, identity_summary = load_a_share_identity_facts(
        local_roots,
        symbols=selected_symbols,
        trade_date=trade_date,
        source_order=identity_source_order,
        fetch_timeout_seconds=identity_fetch_timeout_seconds,
        tushare_token=tushare_token,
        tushare_endpoint_url=tushare_endpoint_url,
    )
    stats["identity_sources_attempted"] = list(identity_summary.attempted_sources)
    stats["identity_sources_loaded"] = list(identity_summary.loaded_sources)
    stats["identity_loaded_count"] = identity_summary.loaded_count
    stats["identity_errors"] = list(identity_summary.errors)
    if require_company_identity and selected_symbols and not identity_facts:
        raise SystemExit("company identity is required but no real company identities were loaded")
    if require_company_identity:
        selected_full_symbols, selected_2026_symbols, selected_daily_only, skipped_symbols = _filter_symbols_by_identity(
            full_symbols=selected_full_symbols,
            factor_2026_symbols=selected_2026_symbols,
            daily_only_symbols=selected_daily_only,
            identity_facts=identity_facts,
        )
        selected_symbols = tuple(sorted(set(selected_full_symbols).union(selected_2026_symbols).union(selected_daily_only)))
        stats["company_identity_skipped_symbol_count"] = len(skipped_symbols)
        stats["company_identity_skipped_symbols_sample"] = list(skipped_symbols[:50])
    stats["factor_full_symbol_count"] = len(selected_full_symbols)
    stats["factor_2026_symbol_count"] = len(selected_2026_symbols)
    stats["company_name_missing_symbol_count"] = sum(1 for symbol in selected_symbols if symbol not in identity_facts)

    def add_record(record: dict[str, Any]) -> None:
        records.append(record)
        dataset = str(record["dataset"])
        if dataset == "daily_bar":
            stats["daily_bar_rows"] += 1
        elif dataset == "valuation_metric":
            stats["valuation_metric_rows"] += 1
        _update_date_range(stats, dataset, str(record["period_start"]))
        if len(records) >= chunk_size:
            _flush_records(repository=repository, records=records, stats=stats)

    for source in (
        _FactorSource(label="factor_full", zip_path=factor_full_zip, symbols=selected_full_symbols, skip_2026_symbols=factor_2026_symbols),
        _FactorSource(label="factor_2026", zip_path=factor_2026_zip, symbols=selected_2026_symbols, skip_2026_symbols=set()),
    ):
        if not source.zip_path.exists():
            continue
        _stream_factor_records(
            source=source,
            raw_ref=raw_ref,
            as_of=as_of,
            identity_facts=identity_facts,
            start_date=start_date,
            end_date=end_date,
            add_record=add_record,
        )

    stats["daily_only_symbol_count"] = len(selected_daily_only)
    for symbol in sorted(selected_daily_only):
        daily_path = daily_root / f"{symbol.replace('.', '_')}.csv"
        if not daily_path.exists():
            daily_path = daily_root / f"{symbol}.csv"
        if not daily_path.exists():
            continue
        for record in _daily_only_records(
            symbol=symbol,
            path=daily_path,
            raw_ref=raw_ref,
            as_of=as_of,
            identity=identity_facts.get(symbol),
            start_date=start_date,
            end_date=end_date,
        ):
            add_record(record)

    _flush_records(repository=repository, records=records, stats=stats)
    if stats["daily_only_symbol_count"]:
        stats["warnings"].append("daily_only_symbols_imported_without_valuation_metric")
    if stats["company_identity_skipped_symbol_count"]:
        stats["warnings"].append("company_identity_missing_symbols_skipped")
    if stats["company_name_missing_symbol_count"]:
        stats["warnings"].append("company_name_missing_symbols")
    return stats


class _FactorSource:
    def __init__(self, *, label: str, zip_path: Path, symbols: set[str], skip_2026_symbols: set[str]) -> None:
        self.label = label
        self.zip_path = zip_path
        self.symbols = symbols
        self.skip_2026_symbols = skip_2026_symbols


def _filter_symbols_by_identity(
    *,
    full_symbols: set[str],
    factor_2026_symbols: set[str],
    daily_only_symbols: set[str],
    identity_facts: Mapping[str, Any],
) -> tuple[set[str], set[str], set[str], tuple[str, ...]]:
    allowed = set(identity_facts)
    selected = set(full_symbols).union(factor_2026_symbols).union(daily_only_symbols)
    skipped = tuple(sorted(symbol for symbol in selected if symbol not in allowed))
    return full_symbols & allowed, factor_2026_symbols & allowed, daily_only_symbols & allowed, skipped


def _stream_factor_records(
    *,
    source: _FactorSource,
    raw_ref: str,
    as_of: datetime,
    identity_facts: Mapping[str, Any],
    start_date: str | None,
    end_date: str | None,
    add_record: Any,
) -> None:
    with ZipFile(source.zip_path) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        for name in names:
            symbol = _ticker_from_name(Path(name).name)
            if symbol is None or symbol not in source.symbols:
                continue
            source_ref = _source_ref(source=source, entry_name=name)
            with archive.open(name) as raw_handle:
                text_handle = (line.decode("utf-8-sig") for line in raw_handle)
                reader = csv.DictReader(text_handle)
                for raw in reader:
                    raw_date = str(raw.get("trade_date") or "").strip()
                    if source.label == "factor_full" and symbol in source.skip_2026_symbols and raw_date >= FACTOR_2026_START:
                        continue
                    row_date = _factor_date_text(raw_date)
                    if row_date is None:
                        continue
                    if not _date_in_range(row_date, start=start_date, end=end_date):
                        continue
                    daily = _daily_bar_from_factor(
                        symbol=symbol,
                        row_date=row_date,
                        raw=raw,
                        raw_ref=raw_ref,
                        as_of=as_of,
                        source_ref=source_ref,
                        identity=identity_facts.get(symbol),
                    )
                    if daily is not None:
                        add_record(daily)
                    valuation = _valuation_from_factor(symbol=symbol, row_date=row_date, raw=raw, raw_ref=raw_ref, as_of=as_of, source_ref=source_ref)
                    if valuation is not None:
                        add_record(valuation)


def _daily_bar_from_factor(
    *,
    symbol: str,
    row_date: str,
    raw: Mapping[str, str],
    raw_ref: str,
    as_of: datetime,
    source_ref: str,
    identity: Any = None,
) -> dict[str, Any] | None:
    open_value = _float(raw.get("open_qfq") or raw.get("open"))
    high_value = _float(raw.get("high_qfq") or raw.get("high"))
    low_value = _float(raw.get("low_qfq") or raw.get("low"))
    close_value = _float(raw.get("close_qfq") or raw.get("close"))
    volume = _float(raw.get("vol"))
    if None in {open_value, high_value, low_value, close_value, volume}:
        return None
    row = {
        "ticker": symbol,
        "symbol_id": symbol,
        "date": row_date,
        "trade_date": row_date,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume,
        "adjustment": "qfq",
        "source_ref": source_ref,
    }
    _apply_identity(row, identity=identity)
    _set_float(row, "amount", _amount_yuan(raw.get("amount")))
    _set_float(row, "p_change_pct", _float(raw.get("pct_chg")))
    _set_float(row, "pre_close_raw", _float(raw.get("pre_close")))
    _set_float(row, "change_raw", _float(raw.get("change")))
    _set_float(row, "raw_open", _float(raw.get("open")))
    _set_float(row, "raw_high", _float(raw.get("high")))
    _set_float(row, "raw_low", _float(raw.get("low")))
    _set_float(row, "raw_close", _float(raw.get("close")))
    _set_float(row, "hfq_open", _float(raw.get("open_hfq")))
    _set_float(row, "hfq_high", _float(raw.get("high_hfq")))
    _set_float(row, "hfq_low", _float(raw.get("low_hfq")))
    _set_float(row, "hfq_close", _float(raw.get("close_hfq")))
    _set_float(row, "adj_factor", _float(raw.get("adj_factor")))
    return _record(dataset="daily_bar", symbol=symbol, row_date=row_date, row=row, raw_ref=raw_ref, as_of=as_of, schema_id="cn_a_daily_bar_required.v1")


def _valuation_from_factor(
    *,
    symbol: str,
    row_date: str,
    raw: Mapping[str, str],
    raw_ref: str,
    as_of: datetime,
    source_ref: str,
) -> dict[str, Any] | None:
    row: dict[str, Any] = {
        "ticker": symbol,
        "symbol_id": symbol,
        "date": row_date,
        "trade_date": row_date,
        "market_cap_unit": "CNY_10K",
        "share_unit": "10K_shares",
        "source_ref": source_ref,
    }
    for target, source in {
        "pe": "pe",
        "pe_ttm": "pe_ttm",
        "pb": "pb",
        "ps": "ps",
        "ps_ttm": "ps_ttm",
        "market_cap": "total_mv",
        "float_market_cap": "circ_mv",
        "total_share": "total_share",
        "float_share": "float_share",
        "free_share": "free_share",
        "turnover_rate": "turnover_rate",
        "turnover_rate_f": "turnover_rate_f",
        "volume_ratio": "volume_ratio",
        "dv_ratio": "dv_ratio",
        "dv_ttm": "dv_ttm",
    }.items():
        _set_float(row, target, _float(raw.get(source)))
    if len(row) <= 7:
        return None
    return _record(dataset="valuation_metric", symbol=symbol, row_date=row_date, row=row, raw_ref=raw_ref, as_of=as_of, schema_id="cn_a_valuation_metric_required.v1")


def _daily_only_records(
    *,
    symbol: str,
    path: Path,
    raw_ref: str,
    as_of: datetime,
    identity: Any = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Iterable[dict[str, Any]]:
    rows = _read_csv_rows(path)
    latest_adj = _latest_adj_factor(rows)
    for raw in rows:
        row_date = _date_text(raw.get("trade_date") or raw.get("date"))
        if row_date is None:
            continue
        if not _date_in_range(row_date, start=start_date, end=end_date):
            continue
        open_value = _float(raw.get("open"))
        high_value = _float(raw.get("high"))
        low_value = _float(raw.get("low"))
        close_value = _float(raw.get("close"))
        volume = _float(raw.get("vol"))
        if None in {open_value, high_value, low_value, close_value, volume}:
            continue
        adj_factor = _float(raw.get("adj_factor"))
        adjustment = "none"
        if adj_factor is not None and latest_adj is not None and latest_adj > 0:
            ratio = adj_factor / latest_adj
            open_value *= ratio
            high_value *= ratio
            low_value *= ratio
            close_value *= ratio
            adjustment = "qfq"
        row = {
            "ticker": symbol,
            "symbol_id": symbol,
            "date": row_date,
            "trade_date": row_date,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume,
            "adjustment": adjustment,
            "source_ref": f"file://{path}",
        }
        _apply_identity(row, identity=identity)
        _set_float(row, "amount", _amount_yuan(raw.get("amount")))
        _set_float(row, "p_change_pct", _float(raw.get("pct_chg")))
        _set_float(row, "pre_close_raw", _float(raw.get("pre_close")))
        _set_float(row, "change_raw", _float(raw.get("change")))
        _set_float(row, "adj_factor", adj_factor)
        yield _record(dataset="daily_bar", symbol=symbol, row_date=row_date, row=row, raw_ref=raw_ref, as_of=as_of, schema_id="cn_a_daily_bar_required.v1", quality_flags=("factor_missing",))


def _record(
    *,
    dataset: str,
    symbol: str,
    row_date: str,
    row: Mapping[str, Any],
    raw_ref: str,
    as_of: datetime,
    schema_id: str,
    quality_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "market": MARKET,
        "symbol_id": symbol,
        "universe_ref": UNIVERSE_REF,
        "granularity": "daily",
        "period_start": row_date,
        "period_end": row_date,
        "field_set": tuple(sorted(str(key) for key, value in row.items() if value is not None)),
        "as_of": as_of,
        "fresh_until": as_of,
        "source_roles": ("local_seed",),
        "exchange": _exchange_for_symbol(symbol),
        "currency": CURRENCY,
        "timezone": TIMEZONE,
        "calendar": CALENDAR,
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {
            "provider_id": PROVIDER_ID,
            "endpoint_id": ENDPOINT_ID,
            "raw_refs": (raw_ref,),
            "remote_attempted": False,
        },
        "schema_id": schema_id,
        "quality_flags": quality_flags,
        "row": dict(row),
    }


def _flush_records(*, repository: DatasetRepository, records: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    if not records:
        return
    refs = repository.upsert_normalized_documents(tuple(records))
    stats["chunks_written"] += 1
    stats["dataset_ref_count"] += len(refs)
    sample = stats["dataset_ref_sample"]
    if isinstance(sample, list) and len(sample) < 100:
        sample.extend(refs[: max(0, 100 - len(sample))])
    records.clear()


def _supersede_existing_required_manifests(database: Any, *, import_run_id: str) -> int:
    now = datetime.now(tz=UTC).isoformat()
    criteria = {
        "market": MARKET,
        "storage": "parquet",
        "status": "active",
        "dataset": {"$in": DATASET_SCOPE},
    }
    result = database["dataset_manifests"].update_many(
        criteria,
        {
            "$set": {
                "status": "superseded",
                "superseded_at": now,
                "superseded_by_import_run_id": import_run_id,
                "supersession_reason": "a_share_required_full_columnar_reimport",
            }
        },
    )
    return int(result.modified_count)


def _write_raw_package_record(*, repository: DatasetRepository, import_run_id: str, audit: Mapping[str, Any]) -> str:
    payload = {
        "import_run_id": import_run_id,
        "audit": audit,
        "storage_policy": "metadata_only",
    }
    digest = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return repository.insert_raw_payload(
        {
            "raw_ref": f"raw:CN_A:{PROVIDER_ID}:{digest[:12]}",
            "metadata_ref": f"rawmeta:CN_A:{PROVIDER_ID}:{digest[:12]}",
            "provider": PROVIDER_ID,
            "endpoint": ENDPOINT_ID,
            "market": MARKET,
            "payload_hash": f"sha256:{digest}",
            "object_uri": None,
            "storage_mode": "metadata_only",
            "payload_size_bytes": len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")),
            "license_policy": {"raw_storage_mode": "metadata_only", "normalized_storage_allowed": True},
            "redaction_meta": {"payload_body_stored": False, "mongo_body_stored": False},
            "created_at": datetime.now(tz=UTC),
            "payload_summary": payload,
        }
    )


def _write_attempt_record(*, repository: DatasetRepository, import_run_id: str, raw_ref: str, stats: Mapping[str, Any]) -> str:
    return repository.insert_provider_attempt(
        {
            "attempt_ref": f"attempt:{PROVIDER_ID}:{ENDPOINT_ID}:{uuid4().hex[:12]}",
            "provider": PROVIDER_ID,
            "endpoint": ENDPOINT_ID,
            "status": "local_seed_imported",
            "remote_attempted": False,
            "remote_success": False,
            "dataset_refs": tuple(stats.get("dataset_ref_sample", ()) or ()),
            "dataset_ref_count": int(stats.get("dataset_ref_count") or 0),
            "raw_refs": (raw_ref,),
            "gap_codes": tuple(stats.get("warnings", ()) or ()),
            "source_metadata": {
                "import_run_id": import_run_id,
                "execution_scope": "a_share_required_columnar_import",
                "daily_bar_rows": stats.get("daily_bar_rows"),
                "valuation_metric_rows": stats.get("valuation_metric_rows"),
                "chunks_written": stats.get("chunks_written"),
                "date_ranges": stats.get("date_ranges"),
            },
            "created_at": datetime.now(tz=UTC),
        }
    )


def _write_job_manifest(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    trade_date: str,
    raw_ref: str,
    attempt_ref: str,
    stats: Mapping[str, Any],
    superseded_count: int,
) -> str:
    return repository.write_dataset_manifest(
        {
            "manifest_ref": f"manifest:CN_A:a_share_required:{import_run_id}",
            "job_id": import_run_id,
            "job_type": "seed_import",
            "market": MARKET,
            "dataset_scope": "daily_bar+valuation_metric",
            "trade_date": trade_date,
            "source_summary": "local:a_share_required:daily+factor",
            "dataset_refs": tuple(stats.get("dataset_ref_sample", ()) or ()),
            "dataset_ref_count": int(stats.get("dataset_ref_count") or 0),
            "raw_refs": (raw_ref,),
            "attempt_refs": (attempt_ref,),
            "output_refs": {
                "dataset_ref_sample": tuple(stats.get("dataset_ref_sample", ()) or ()),
                "dataset_ref_count": int(stats.get("dataset_ref_count") or 0),
                "raw_refs": (raw_ref,),
                "attempt_refs": (attempt_ref,),
            },
            "gap_summary": tuple(stats.get("warnings", ()) or ()),
            "row_counts": {
                "daily_bar": int(stats.get("daily_bar_rows") or 0),
                "valuation_metric": int(stats.get("valuation_metric_rows") or 0),
            },
            "date_ranges": stats.get("date_ranges"),
            "cleanup": {
                "superseded_existing_manifest_count": superseded_count,
                "dataset_scope": DATASET_SCOPE,
            },
            "created_at": datetime.now(tz=UTC),
        }
    )


def _source_ref(*, source: _FactorSource, entry_name: str) -> str:
    if source.label == "factor_full":
        return f"zip+split://stk_factor_pro.zip.001+002+003/{entry_name}"
    return f"zip://{source.zip_path}/{entry_name}"


def _zip_symbols(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with ZipFile(path) as archive:
        return {
            symbol
            for symbol in (_ticker_from_name(Path(name).name) for name in archive.namelist() if name.lower().endswith(".csv"))
            if symbol is not None
        }


def _daily_symbols(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {symbol for symbol in (_ticker_from_name(path.name) for path in root.glob("*.csv")) if symbol is not None}


def _limit_symbols(symbols: set[str], max_symbols: int | None) -> set[str]:
    ordered = sorted(symbols)
    if max_symbols is not None:
        ordered = ordered[: max(0, max_symbols)]
    return set(ordered)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _latest_adj_factor(rows: Sequence[Mapping[str, str]]) -> float | None:
    latest_date: str | None = None
    latest_adj: float | None = None
    for row in rows:
        row_date = _date_text(row.get("trade_date") or row.get("date"))
        adj = _float(row.get("adj_factor"))
        if row_date is None or adj is None:
            continue
        if latest_date is None or row_date > latest_date:
            latest_date = row_date
            latest_adj = adj
    return latest_adj


def _set_float(row: dict[str, Any], key: str, value: float | None) -> None:
    if value is not None:
        row[key] = value


def _apply_identity(row: dict[str, Any], *, identity: Any) -> None:
    if identity is None:
        return
    company_name = str(getattr(identity, "company_name", "") or "").strip()
    if company_name:
        row["company_name"] = company_name
    industry = str(getattr(identity, "industry", "") or "").strip()
    if industry:
        row["industry"] = industry
    list_date = str(getattr(identity, "list_date", "") or "").strip()
    if list_date:
        row["list_date"] = list_date
    source_ref = str(getattr(identity, "source_ref", "") or "").strip()
    if source_ref:
        row["identity_source_ref"] = source_ref


def _amount_yuan(value: str | None) -> float | None:
    amount = _float(value)
    if amount is None:
        return None
    return amount * 1000.0


def _float(value: str | object | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _factor_date_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8])).isoformat()
    except ValueError:
        return None


def _date_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return _factor_date_text(text)
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _optional_iso_date(value: str | None, label: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"{label} must be YYYY-MM-DD") from exc


def _date_in_range(row_date: str, *, start: str | None, end: str | None) -> bool:
    if start is not None and row_date < start:
        return False
    if end is not None and row_date > end:
        return False
    return True


def _ticker_from_name(name: str) -> str | None:
    stem = Path(name).stem.strip().upper().replace("_", ".")
    parts = stem.split(".")
    if len(parts) != 2:
        return None
    code, suffix = parts
    if not code.isdigit() or suffix not in {"SZ", "SH", "BJ"}:
        return None
    return f"{code}.{suffix}"


def _exchange_for_symbol(symbol: str) -> str:
    suffix = symbol.rsplit(".", 1)[-1].upper()
    return {"SZ": "SZSE", "SH": "SSE", "BJ": "BSE"}.get(suffix, "CN_A")


def _update_date_range(stats: dict[str, Any], dataset: str, row_date: str) -> None:
    ranges = stats.setdefault("date_ranges", {})
    item = ranges.setdefault(dataset, {"min": row_date, "max": row_date})
    item["min"] = min(str(item["min"]), row_date)
    item["max"] = max(str(item["max"]), row_date)


def _zip_can_list(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with ZipFile(path) as archive:
            archive.infolist()
    except (BadZipFile, OSError):
        return False
    return True


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade"


def _emit(payload: Mapping[str, Any], output_json: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(text + "\n", encoding="utf-8")
    print(text)


def _csv_items(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


if __name__ == "__main__":
    raise SystemExit(main())
