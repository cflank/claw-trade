#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

BASE_URL = "https://data.binance.vision"
SPOT_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
USDM_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
SUPPORTED_MARKET_SEGMENTS = {"spot", "usdm_futures"}
SUPPORTED_INTERVALS = {"1d", "1h"}
DEFAULT_QUOTE_ASSET = "USDT"
KNOWN_QUOTE_ASSETS = (
    "FDUSD",
    "USDT",
    "USDC",
    "BUSD",
    "TUSD",
    "USD1",
    "USD",
    "BTC",
    "ETH",
    "BNB",
    "TRY",
    "EUR",
    "BRL",
    "AUD",
    "GBP",
    "IDR",
    "JPY",
)


@dataclass(frozen=True)
class BinanceDownloadItem:
    market_segment: str
    frequency: str
    symbol: str
    interval: str
    year: int
    month: int
    day: int | None
    url: str
    checksum_url: str
    relative_path: str
    checksum_relative_path: str
    checksum_required: bool
    status: str = "planned"
    file_size_bytes: int | None = None
    checksum_status: str = "not_checked"

    def as_dict(self) -> dict[str, object]:
        return {
            "market_segment": self.market_segment,
            "frequency": self.frequency,
            "symbol": self.symbol,
            "interval": self.interval,
            "year": self.year,
            "month": f"{self.month:02d}",
            "day": f"{self.day:02d}" if self.day is not None else None,
            "url": self.url,
            "checksum_url": self.checksum_url,
            "relative_path": self.relative_path,
            "checksum_relative_path": self.checksum_relative_path,
            "checksum_required": self.checksum_required,
            "status": self.status,
            "file_size_bytes": self.file_size_bytes,
            "checksum_status": self.checksum_status,
        }


def build_download_plan(
    *,
    output_root: Path,
    market_segment: str,
    interval: str,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    checksum_required: bool,
    symbol_metadata: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    if market_segment not in SUPPORTED_MARKET_SEGMENTS:
        raise ValueError(f"unsupported market segment: {market_segment}")
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"unsupported interval: {interval}")
    if end_date < start_date:
        raise ValueError("end-date must not be earlier than start-date")
    normalized_symbols = tuple(dict.fromkeys(_normalize_symbol(symbol) for symbol in symbols if symbol.strip()))
    if not normalized_symbols:
        raise ValueError("at least one symbol is required")

    items = tuple(
        item
        for symbol in normalized_symbols
        for item in _kline_items_for_range(
            output_root=output_root,
            market_segment=market_segment,
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            checksum_required=checksum_required,
        )
    )
    return {
        "schema_id": "binance_public_data_download_manifest.v1",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "output_root": str(output_root),
        "market_segment": market_segment,
        "interval": interval,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": normalized_symbols,
        "symbol_metadata": tuple(dict(item) for item in symbol_metadata),
        "checksum_required": checksum_required,
        "item_count": len(items),
        "items": tuple(item.as_dict() for item in items),
    }


def download_items(
    manifest: dict[str, object],
    *,
    output_root: Path,
    retriever: Callable[[str, Path], None] | None = None,
    force: bool = False,
    ignore_missing: bool = False,
    progress_json: Path | None = None,
    progress_every: int = 100,
) -> dict[str, object]:
    downloader = retriever or _retrieve_url
    updated_items: list[dict[str, object]] = []
    for raw_item in manifest.get("items", ()):
        if not isinstance(raw_item, dict):
            raise ValueError("manifest item must be an object")
        item = dict(raw_item)
        zip_path = output_root / str(item["relative_path"])
        checksum_path = output_root / str(item["checksum_relative_path"])
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        if not force and zip_path.exists():
            file_size = zip_path.stat().st_size
            item["file_size_bytes"] = file_size
            if bool(item.get("checksum_required")) and checksum_path.exists():
                item["checksum_status"] = "verified" if verify_sha256(zip_path, checksum_path) else "failed"
                if item["checksum_status"] != "verified":
                    raise ValueError(f"checksum failed: {zip_path}")
            elif bool(item.get("checksum_required")):
                raise FileNotFoundError(f"checksum file missing for existing package: {checksum_path}")
            else:
                item["checksum_status"] = "skipped"
            item["status"] = "skipped_existing"
            updated_items.append(item)
            _write_progress(manifest, updated_items, progress_json=progress_json, progress_every=progress_every)
            continue
        try:
            downloader(str(item["url"]), zip_path)
        except urllib.error.HTTPError as exc:
            if ignore_missing and exc.code == 404:
                item["status"] = "missing"
                item["checksum_status"] = "missing"
                updated_items.append(item)
                _write_progress(manifest, updated_items, progress_json=progress_json, progress_every=progress_every)
                continue
            raise
        file_size = zip_path.stat().st_size
        item["file_size_bytes"] = file_size
        if bool(item.get("checksum_required")):
            try:
                downloader(str(item["checksum_url"]), checksum_path)
            except urllib.error.HTTPError as exc:
                if ignore_missing and exc.code == 404:
                    _remove_unverified_files(zip_path, checksum_path)
                    item["status"] = "missing"
                    item["checksum_status"] = "missing"
                    updated_items.append(item)
                    _write_progress(manifest, updated_items, progress_json=progress_json, progress_every=progress_every)
                    continue
                raise
            item["checksum_status"] = "verified" if verify_sha256(zip_path, checksum_path) else "failed"
            if item["checksum_status"] != "verified":
                raise ValueError(f"checksum failed: {zip_path}")
        else:
            item["checksum_status"] = "skipped"
        item["status"] = "downloaded"
        updated_items.append(item)
        _write_progress(manifest, updated_items, progress_json=progress_json, progress_every=progress_every)
    next_manifest = dict(manifest)
    next_manifest["items"] = tuple(updated_items)
    next_manifest["downloaded_at"] = datetime.now(tz=UTC).isoformat()
    return next_manifest


def verify_sha256(zip_path: Path, checksum_path: Path) -> bool:
    if not checksum_path.exists():
        raise FileNotFoundError(f"checksum file missing: {checksum_path}")
    expected = _read_checksum(checksum_path)
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return actual.lower() == expected.lower()


def _remove_unverified_files(zip_path: Path, checksum_path: Path) -> None:
    zip_path.unlink(missing_ok=True)
    checksum_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Binance public data Kline ZIP packages.")
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--market-segment", required=True, choices=sorted(SUPPORTED_MARKET_SEGMENTS))
    parser.add_argument("--interval", required=True, choices=sorted(SUPPORTED_INTERVALS))
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--symbol-file", type=Path)
    parser.add_argument("--all-symbols", action="store_true")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--checksum-required", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ignore-missing", action="store_true")
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    symbol_metadata = discover_symbols(args.market_segment) if args.all_symbols else ()
    symbols = _load_symbols(
        args.symbol,
        args.symbol_file,
        args.max_symbols,
        discovered_symbols=tuple(str(item["symbol"]) for item in symbol_metadata),
    )
    if args.max_symbols is not None and symbol_metadata:
        wanted = set(symbols)
        symbol_metadata = tuple(item for item in symbol_metadata if str(item["symbol"]) in wanted)
    manifest = build_download_plan(
        output_root=args.output_root,
        market_segment=args.market_segment,
        interval=args.interval,
        symbols=symbols,
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        checksum_required=bool(args.checksum_required),
        symbol_metadata=symbol_metadata,
    )
    if not args.dry_run:
        manifest = download_items(
            manifest,
            output_root=args.output_root,
            force=bool(args.force),
            ignore_missing=bool(args.ignore_missing),
            progress_json=args.output_json,
            progress_every=int(args.progress_every),
        )
    _emit_manifest(manifest, args.output_json)
    return 0


def discover_symbols(market_segment: str) -> tuple[dict[str, object], ...]:
    if market_segment == "spot":
        return _spot_symbols(_fetch_json(SPOT_EXCHANGE_INFO_URL))
    if market_segment == "usdm_futures":
        return _usdm_symbols(_fetch_json(USDM_EXCHANGE_INFO_URL))
    raise ValueError(f"unsupported market segment: {market_segment}")


def _spot_symbols(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for item in tuple(payload.get("symbols", ()) or ()):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "").upper() != "TRADING":
            continue
        if item.get("isSpotTradingAllowed") is False:
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        base = str(item.get("baseAsset") or "").strip().upper()
        quote = str(item.get("quoteAsset") or "").strip().upper()
        if quote != DEFAULT_QUOTE_ASSET:
            continue
        if symbol and base and quote:
            result.append(
                {
                    "symbol": symbol,
                    "base_asset": base,
                    "quote_asset": quote,
                    "status": "TRADING",
                    "market_segment": "spot",
                    "source": "binance_spot_exchange_info",
                }
            )
    return tuple(sorted(result, key=lambda item: str(item["symbol"])))


def _usdm_symbols(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for item in tuple(payload.get("symbols", ()) or ()):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "").upper() != "TRADING":
            continue
        if str(item.get("contractType") or "").upper() != "PERPETUAL":
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        base = str(item.get("baseAsset") or "").strip().upper()
        quote = str(item.get("quoteAsset") or "").strip().upper()
        if quote != DEFAULT_QUOTE_ASSET:
            continue
        if symbol and base and quote:
            result.append(
                {
                    "symbol": symbol,
                    "base_asset": base,
                    "quote_asset": quote,
                    "status": "TRADING",
                    "market_segment": "usdm_futures",
                    "contract_type": str(item.get("contractType") or ""),
                    "underlying_type": str(item.get("underlyingType") or ""),
                    "source": "binance_usdm_exchange_info",
                }
            )
    return tuple(sorted(result, key=lambda item: str(item["symbol"])))


def _monthly_item(
    *,
    output_root: Path,
    market_segment: str,
    symbol: str,
    interval: str,
    year: int,
    month: int,
    checksum_required: bool,
) -> BinanceDownloadItem:
    del output_root
    base_path = _base_path(market_segment, "monthly", symbol, interval)
    file_name = f"{symbol}-{interval}-{year}-{month:02d}.zip"
    relative_path = f"{base_path}{file_name}"
    url = _public_data_url(relative_path)
    checksum_relative_path = f"{relative_path}.CHECKSUM"
    return BinanceDownloadItem(
        market_segment=market_segment,
        frequency="monthly",
        symbol=symbol,
        interval=interval,
        year=year,
        month=month,
        day=None,
        url=url,
        checksum_url=_public_data_url(checksum_relative_path),
        relative_path=relative_path,
        checksum_relative_path=checksum_relative_path,
        checksum_required=checksum_required,
    )


def _daily_item(
    *,
    output_root: Path,
    market_segment: str,
    symbol: str,
    interval: str,
    day: date,
    checksum_required: bool,
) -> BinanceDownloadItem:
    del output_root
    base_path = _base_path(market_segment, "daily", symbol, interval)
    file_name = f"{symbol}-{interval}-{day.isoformat()}.zip"
    relative_path = f"{base_path}{file_name}"
    url = _public_data_url(relative_path)
    checksum_relative_path = f"{relative_path}.CHECKSUM"
    return BinanceDownloadItem(
        market_segment=market_segment,
        frequency="daily",
        symbol=symbol,
        interval=interval,
        year=day.year,
        month=day.month,
        day=day.day,
        url=url,
        checksum_url=_public_data_url(checksum_relative_path),
        relative_path=relative_path,
        checksum_relative_path=checksum_relative_path,
        checksum_required=checksum_required,
    )


def _base_path(market_segment: str, frequency: str, symbol: str, interval: str) -> str:
    if market_segment == "spot":
        market_root = "data/spot"
    elif market_segment == "usdm_futures":
        market_root = "data/futures/um"
    else:
        raise ValueError(f"unsupported market segment: {market_segment}")
    return f"{market_root}/{frequency}/klines/{symbol}/{interval}/"


def _public_data_url(relative_path: str) -> str:
    return f"{BASE_URL}/{urllib.parse.quote(relative_path, safe='/')}"


def _kline_items_for_range(
    *,
    output_root: Path,
    market_segment: str,
    symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    checksum_required: bool,
) -> Iterable[BinanceDownloadItem]:
    cursor = start_date
    while cursor <= end_date:
        month_end = _month_end(cursor)
        if cursor.day == 1 and month_end <= end_date:
            yield _monthly_item(
                output_root=output_root,
                market_segment=market_segment,
                symbol=symbol,
                interval=interval,
                year=cursor.year,
                month=cursor.month,
                checksum_required=checksum_required,
            )
            cursor = month_end + timedelta(days=1)
            continue
        daily_end = min(end_date, month_end)
        day = cursor
        while day <= daily_end:
            yield _daily_item(
                output_root=output_root,
                market_segment=market_segment,
                symbol=symbol,
                interval=interval,
                day=day,
                checksum_required=checksum_required,
            )
            day += timedelta(days=1)
        cursor = daily_end + timedelta(days=1)


def _month_end(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def _load_symbols(
    raw_symbols: Sequence[str],
    symbol_file: Path | None,
    max_symbols: int | None,
    *,
    discovered_symbols: Sequence[str] = (),
) -> tuple[str, ...]:
    symbols: list[str] = []
    symbols.extend(raw_symbols)
    if symbol_file is not None:
        symbols.extend(line.strip() for line in symbol_file.read_text(encoding="utf-8").splitlines())
    if discovered_symbols:
        symbols.extend(discovered_symbols)
    normalized = tuple(dict.fromkeys(_normalize_symbol(symbol) for symbol in symbols if symbol.strip()))
    if not normalized:
        raise ValueError("at least one symbol is required; pass --symbol, --symbol-file, or --all-symbols")
    if max_symbols is not None:
        if max_symbols <= 0:
            raise ValueError("--max-symbols must be positive")
        return normalized[:max_symbols]
    return normalized


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("/", "").replace("-", "").replace(".", "")
    if not normalized:
        raise ValueError("symbol must be non-empty")
    if not normalized.isalnum():
        raise ValueError(f"invalid symbol: {symbol}")
    if normalized.endswith(DEFAULT_QUOTE_ASSET) and len(normalized) > len(DEFAULT_QUOTE_ASSET):
        return normalized
    for quote in KNOWN_QUOTE_ASSETS:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return f"{normalized[: -len(quote)]}{DEFAULT_QUOTE_ASSET}"
    return f"{normalized}{DEFAULT_QUOTE_ASSET}"


def _retrieve_url(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url) as response:
        destination.write_bytes(response.read())


def _fetch_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json response must be an object: {url}")
    return payload


def _write_progress(
    manifest: dict[str, object],
    updated_items: Sequence[dict[str, object]],
    *,
    progress_json: Path | None,
    progress_every: int,
) -> None:
    if progress_json is None:
        return
    if progress_every <= 0:
        progress_every = 1
    if len(updated_items) % progress_every != 0:
        return
    next_manifest = dict(manifest)
    next_manifest["items"] = tuple(updated_items)
    next_manifest["download_progress"] = {
        "processed_items": len(updated_items),
        "planned_items": len(tuple(manifest.get("items", ()) or ())),
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    _emit_manifest(next_manifest, progress_json)


def _read_checksum(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"checksum file empty: {path}")
    return text.split()[0]


def _emit_manifest(manifest: dict[str, object], output_json: Path | None) -> None:
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    raise SystemExit(main())
