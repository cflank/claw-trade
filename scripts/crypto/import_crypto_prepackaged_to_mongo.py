#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from claw_trade.data_gateway.crypto_prepackaged_importer import import_crypto_prepackaged_to_repository
from claw_trade.data_gateway.crypto_symbols import DEFAULT_QUOTE_ASSET, KNOWN_QUOTE_ASSETS, PHASE1_CORE_ASSETS, map_asset_to_symbol
from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse

SUPPORTED_MARKET_SEGMENTS = ("spot", "usdm_futures")
SUPPORTED_INTERVALS = ("1d", "1h")


@dataclass(frozen=True)
class CryptoSymbolSpec:
    asset: str
    symbol_id: str
    base_asset: str
    quote_asset: str


@dataclass(frozen=True)
class CryptoImportJob:
    market_segment: str
    interval: str
    asset: str
    symbol_id: str
    base_asset: str
    quote_asset: str
    package_paths: tuple[Path, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "market_segment": self.market_segment,
            "interval": self.interval,
            "asset": self.asset,
            "symbol_id": self.symbol_id,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "package_paths": [str(path) for path in self.package_paths],
            "package_count": len(self.package_paths),
        }


@dataclass(frozen=True)
class CryptoImportPlan:
    jobs: tuple[CryptoImportJob, ...]
    warnings: tuple[str, ...] = ()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local Binance public data Kline ZIP packages into unified data warehouse.")
    parser.add_argument("--spot-root", type=Path)
    parser.add_argument("--futures-root", type=Path)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--import-run-id", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--symbol-file", type=Path)
    parser.add_argument("--symbol-manifest", action="append", type=Path, default=[])
    parser.add_argument("--phase1-sample", action="store_true")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--market-segment", action="append", choices=SUPPORTED_MARKET_SEGMENTS)
    parser.add_argument("--interval", action="append", choices=SUPPORTED_INTERVALS)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--confirm-replace-existing", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CRYPTO_MONGODB_URI") or "",
    )
    parser.add_argument(
        "--mongo-database",
        default=os.environ.get("DATA_GATEWAY_MONGODB_DATABASE") or os.environ.get("CRYPTO_MONGODB_DATABASE") or "",
    )
    parser.add_argument(
        "--columnar-root",
        type=Path,
        default=Path(os.environ["DATA_GATEWAY_COLUMNAR_ROOT"]) if os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT") else None,
        help="Root for normalized Parquet partitions. Defaults to the same DATA_GATEWAY_COLUMNAR_ROOT/runtime path as data_gateway runtime.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.replace_existing and not args.confirm_replace_existing:
        raise SystemExit("--replace-existing requires --confirm-replace-existing")

    plan = build_import_plan(
        spot_root=args.spot_root,
        futures_root=args.futures_root,
        symbols=_load_symbol_specs(
            args.symbol,
            args.symbol_file,
            tuple(args.symbol_manifest or ()),
            args.max_symbols,
            phase1_sample=bool(args.phase1_sample),
        ),
        market_segments=tuple(args.market_segment or SUPPORTED_MARKET_SEGMENTS),
        intervals=tuple(args.interval or SUPPORTED_INTERVALS),
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
    )
    jobs = plan.jobs
    output: dict[str, object] = {
        "schema_id": "crypto_prepackaged_import_cli_result.v1",
        "trade_date": args.trade_date,
        "import_run_id": args.import_run_id,
        "audit_only": bool(args.audit_only),
        "catalog_storage": "mongo",
        "normalized_storage": "parquet",
        "columnar_root": str(NormalizedColumnarWarehouse.from_env(root=args.columnar_root).root),
        "job_count": len(jobs),
        "jobs": [job.as_dict() for job in jobs],
        "warnings": list(plan.warnings),
    }
    if not args.audit_only:
        if not args.mongo_uri:
            raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI/CRYPTO_MONGODB_URI")
        repository = _repository_from_mongo(args.mongo_uri, args.mongo_database, columnar_root=args.columnar_root)
        results = []
        for job in jobs:
            result = import_crypto_prepackaged_to_repository(
                package_paths=job.package_paths,
                repository=repository,
                import_run_id=args.import_run_id,
                symbol_id=job.symbol_id,
                base_asset=job.base_asset,
                quote_asset=job.quote_asset,
                interval=job.interval,
                market_segment=job.market_segment,
                as_of=datetime.now(tz=UTC),
                replace_existing=bool(args.replace_existing),
                confirm_replace_existing=bool(args.confirm_replace_existing),
            )
            results.append(result.as_dict())
        output["results"] = results
    _emit_json(output, args.output_json)
    return 0


def build_import_jobs(
    *,
    spot_root: Path | None,
    futures_root: Path | None,
    symbols: Sequence[CryptoSymbolSpec] | None = None,
    assets: Sequence[str] | None = None,
    market_segments: Sequence[str],
    intervals: Sequence[str],
    start_date: date,
    end_date: date,
) -> tuple[CryptoImportJob, ...]:
    return build_import_plan(
        spot_root=spot_root,
        futures_root=futures_root,
        symbols=symbols,
        assets=assets,
        market_segments=market_segments,
        intervals=intervals,
        start_date=start_date,
        end_date=end_date,
    ).jobs


def build_import_plan(
    *,
    spot_root: Path | None,
    futures_root: Path | None,
    symbols: Sequence[CryptoSymbolSpec] | None = None,
    assets: Sequence[str] | None = None,
    market_segments: Sequence[str],
    intervals: Sequence[str],
    start_date: date,
    end_date: date,
) -> CryptoImportPlan:
    if end_date < start_date:
        raise ValueError("end-date must not be earlier than start-date")
    symbol_specs = tuple(symbols or _specs_from_assets(assets or ()))
    jobs: list[CryptoImportJob] = []
    warnings: list[str] = []
    requested_keys = {(mapping.symbol_id, interval) for mapping in symbol_specs for interval in intervals}
    for market_segment in market_segments:
        root = _root_for_segment(market_segment, spot_root=spot_root, futures_root=futures_root)
        if root is None:
            continue
        package_index = _index_packages(root, market_segment=market_segment, start_date=start_date, end_date=end_date)
        warnings.extend(_unscoped_package_warnings(market_segment, package_index, requested_keys))
        for mapping in symbol_specs:
            for interval in intervals:
                paths = package_index.get((mapping.symbol_id, interval), ())
                if not paths:
                    continue
                jobs.append(
                    CryptoImportJob(
                        market_segment=market_segment,
                        interval=interval,
                        asset=mapping.asset,
                        symbol_id=mapping.symbol_id,
                        base_asset=mapping.base_asset,
                        quote_asset=mapping.quote_asset,
                        package_paths=paths,
                    )
                )
    return CryptoImportPlan(jobs=tuple(jobs), warnings=tuple(warnings))


def _unscoped_package_warnings(
    market_segment: str,
    package_index: dict[tuple[str, str], tuple[Path, ...]],
    requested_keys: set[tuple[str, str]],
) -> tuple[str, ...]:
    warnings: list[str] = []
    for symbol_id, interval in sorted(set(package_index) - requested_keys):
        if not _is_usdt_symbol(symbol_id):
            continue
        warnings.append(
            "delisted_symbol_skipped: "
            f"{market_segment}:{symbol_id}:{interval} package_count={len(package_index[(symbol_id, interval)])} "
            "not in selected discovery or symbol scope"
        )
    return tuple(warnings)


def _index_packages(
    root: Path,
    *,
    market_segment: str,
    start_date: date,
    end_date: date,
) -> dict[tuple[str, str], tuple[Path, ...]]:
    matches: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(root.rglob("*.zip")):
        if path.name.endswith(".zip.CHECKSUM"):
            continue
        if not _path_matches_market_segment(path, market_segment):
            continue
        parsed = _parse_package_name(path.name)
        if parsed is None:
            continue
        symbol_id, interval, period_start, period_end = parsed
        if not _is_usdt_symbol(symbol_id):
            continue
        if period_end < start_date or period_start > end_date:
            continue
        _verify_package_checksum(path)
        matches.setdefault((symbol_id, interval), []).append(path)
    return {key: tuple(paths) for key, paths in matches.items()}


def _path_matches_market_segment(path: Path, market_segment: str) -> bool:
    parts = tuple(path.parts)
    has_spot_marker = "spot" in parts
    has_usdm_marker = "futures" in parts and "um" in parts
    if not has_spot_marker and not has_usdm_marker:
        return True
    if market_segment == "spot":
        return has_spot_marker and not has_usdm_marker
    if market_segment == "usdm_futures":
        return has_usdm_marker
    return False


def _parse_package_name(name: str) -> tuple[str, str, date, date] | None:
    if not name.endswith(".zip"):
        return None
    stem = name[:-4]
    symbol_id, interval, suffix = stem.partition("-")
    if not suffix:
        return None
    interval, _, suffix = suffix.partition("-")
    if not interval or not suffix:
        return None
    period = _period_from_package_suffix(suffix)
    if period is None:
        return None
    period_start, period_end = period
    return symbol_id, interval, period_start, period_end


def _period_from_package_suffix(suffix: str) -> tuple[date, date] | None:
    parts = suffix.split("-")
    try:
        if len(parts) == 2:
            year = int(parts[0])
            month = int(parts[1])
            start = date(year, month, 1)
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1).replace(day=1)
                end = date.fromordinal(end.toordinal() - 1)
            return start, end
        if len(parts) == 3:
            day = date.fromisoformat(suffix)
            return day, day
    except ValueError:
        return None
    return None


def _discover_packages(root: Path, *, symbol_id: str, interval: str, start_date: date, end_date: date) -> tuple[Path, ...]:
    matches: list[Path] = []
    for path in sorted(root.rglob(f"{symbol_id}-{interval}-*.zip")):
        if path.name.endswith(".zip.CHECKSUM"):
            continue
        parsed = _parse_package_name(path.name)
        if parsed is None:
            continue
        parsed_symbol, parsed_interval, period_start, period_end = parsed
        if parsed_symbol != symbol_id or parsed_interval != interval:
            continue
        if period_end < start_date or period_start > end_date:
            continue
        _verify_package_checksum(path)
        matches.append(path)
    return tuple(matches)


def _verify_package_checksum(path: Path) -> None:
    checksum_path = Path(f"{path}.CHECKSUM")
    if not checksum_path.exists():
        raise FileNotFoundError(f"checksum file missing for package: {checksum_path}")
    expected = _read_checksum(checksum_path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise ValueError(f"checksum failed for package: {path}")


def _read_checksum(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"checksum file empty: {path}")
    return text.split()[0]


def _period_from_package_name(name: str, *, symbol_id: str, interval: str) -> tuple[date, date] | None:
    prefix = f"{symbol_id}-{interval}-"
    if not name.startswith(prefix) or not name.endswith(".zip"):
        return None
    suffix = name[len(prefix) : -4]
    parts = suffix.split("-")
    try:
        if len(parts) == 2:
            year = int(parts[0])
            month = int(parts[1])
            start = date(year, month, 1)
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1).replace(day=1)
                end = date.fromordinal(end.toordinal() - 1)
            return start, end
        if len(parts) == 3:
            day = date.fromisoformat(suffix)
            return day, day
    except ValueError:
        return None
    return None


def _load_symbol_specs(
    raw_symbols: Sequence[str],
    symbol_file: Path | None,
    symbol_manifests: Sequence[Path],
    max_symbols: int | None,
    *,
    phase1_sample: bool = False,
) -> tuple[CryptoSymbolSpec, ...]:
    specs: dict[str, CryptoSymbolSpec] = {}
    for manifest_path in symbol_manifests:
        for spec in _specs_from_manifest(manifest_path):
            specs.setdefault(spec.symbol_id, spec)
    assets: list[str] = []
    assets.extend(raw_symbols)
    if symbol_file is not None:
        assets.extend(line.strip() for line in symbol_file.read_text(encoding="utf-8").splitlines())
    if not assets and phase1_sample:
        if specs:
            selected = tuple(specs.values())
            return selected[:max_symbols] if max_symbols is not None else selected
        assets.extend(PHASE1_CORE_ASSETS)
    if not assets and not specs:
        raise ValueError("explicit symbol scope required: pass --symbol, --symbol-file, --symbol-manifest, or --phase1-sample")
    for spec in _specs_from_assets(assets):
        specs.setdefault(spec.symbol_id, spec)
    selected = tuple(specs.values())
    if max_symbols is not None:
        if max_symbols <= 0:
            raise ValueError("--max-symbols must be positive")
        return selected[:max_symbols]
    return selected


def _specs_from_manifest(path: Path) -> tuple[CryptoSymbolSpec, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"symbol manifest must be an object: {path}")
    rows = payload.get("symbol_metadata") or ()
    specs: list[CryptoSymbolSpec] = []
    if isinstance(rows, list | tuple):
        for item in rows:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            base = str(item.get("base_asset") or "").strip().upper()
            quote = str(item.get("quote_asset") or "").strip().upper()
            if not symbol or not base or not quote:
                continue
            if quote != DEFAULT_QUOTE_ASSET or not _is_usdt_symbol(symbol):
                continue
            specs.append(CryptoSymbolSpec(asset=base, symbol_id=symbol, base_asset=base, quote_asset=quote))
    return tuple(specs)


def _specs_from_assets(values: Sequence[str]) -> tuple[CryptoSymbolSpec, ...]:
    specs: list[CryptoSymbolSpec] = []
    for value in tuple(dict.fromkeys(item.strip().upper() for item in values if item.strip())):
        mapping = map_asset_to_symbol(value)
        specs.append(
            CryptoSymbolSpec(
                asset=mapping.asset,
                symbol_id=mapping.symbol_id,
                base_asset=mapping.base_asset,
                quote_asset=mapping.quote_asset,
            )
        )
    return tuple(specs)


def _split_symbol_by_known_quote(symbol: str) -> tuple[str | None, str | None]:
    for quote in KNOWN_QUOTE_ASSETS:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return None, None


def _is_usdt_symbol(symbol: str) -> bool:
    base, quote = _split_symbol_by_known_quote(symbol.strip().upper())
    return bool(base) and quote == DEFAULT_QUOTE_ASSET


def _root_for_segment(market_segment: str, *, spot_root: Path | None, futures_root: Path | None) -> Path | None:
    if market_segment == "spot":
        return spot_root
    if market_segment == "usdm_futures":
        return futures_root
    raise ValueError(f"unsupported market segment: {market_segment}")


def _repository_from_mongo(mongo_uri: str, mongo_database: str, *, columnar_root: Path | None = None) -> DatasetRepository:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    database_name = mongo_database or _database_name_from_uri(mongo_uri)
    return DatasetRepository.from_database(
        client[database_name],
        normalized_columnar=NormalizedColumnarWarehouse.from_env(root=columnar_root),
        allow_normalized_mongo_read=True,
    )


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    raise ValueError("mongo database must be provided in URI path or --mongo-database")


def _emit_json(payload: dict[str, object], output_json: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if output_json is None:
        print(text)
        return
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
