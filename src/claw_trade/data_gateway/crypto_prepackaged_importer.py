from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from claw_trade.data_gateway.maintenance.normalized_rows import discard_normalized_mongo_rows
from claw_trade.data_gateway.models import DataGap, GapReason, GapSeverity, Market
from claw_trade.data_gateway.warehouse import DatasetRepository

LOCAL_CRYPTO_PROVIDER_ID = "local_crypto_prepackaged"
BINANCE_PUBLIC_DATA_ENDPOINT_ID = "binance_public_data_import"
CRYPTO_BINANCE_BAR_SCHEMA_ID = "crypto_binance_prepackaged_bar.v1"
CRYPTO_EXCHANGE = "BINANCE"
CRYPTO_CALENDAR = "CRYPTO_24_7"
CRYPTO_TIMEZONE = "UTC"
_KLINE_FIELDS: tuple[str, ...] = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
)
_KLINE_FIELD_ALIASES: dict[str, set[str]] = {
    "open_time": {"open_time"},
    "open": {"open"},
    "high": {"high"},
    "low": {"low"},
    "close": {"close"},
    "volume": {"volume"},
    "close_time": {"close_time"},
    "quote_asset_volume": {"quote_asset_volume", "quote_volume"},
    "number_of_trades": {"number_of_trades", "count"},
    "taker_buy_base_asset_volume": {"taker_buy_base_asset_volume", "taker_buy_volume"},
    "taker_buy_quote_asset_volume": {"taker_buy_quote_asset_volume", "taker_buy_quote_volume"},
    "ignore": {"ignore"},
}
_MONGO_FULL_DATASET_REFS_LIMIT = 500
_MONGO_DATASET_REFS_SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class CryptoPrepackagedImportResult:
    import_run_id: str
    market_segment: str
    interval: str
    symbol_id: str
    file_count: int
    row_count: int
    parsed_row_count: int
    deleted_existing_count: int
    dataset_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    manifest_ref: str
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_run_id": self.import_run_id,
            "market_segment": self.market_segment,
            "interval": self.interval,
            "symbol_id": self.symbol_id,
            "file_count": self.file_count,
            "row_count": self.row_count,
            "parsed_row_count": self.parsed_row_count,
            "deleted_existing_count": self.deleted_existing_count,
            "dataset_refs": list(self.dataset_refs),
            "raw_refs": list(self.raw_refs),
            "attempt_refs": list(self.attempt_refs),
            "manifest_ref": self.manifest_ref,
            "warnings": list(self.warnings),
        }


def build_crypto_bar_record(
    *,
    symbol_id: str,
    base_asset: str,
    quote_asset: str,
    interval: str,
    open_time: datetime,
    close_time: datetime,
    open_price: Decimal | float | str,
    high_price: Decimal | float | str,
    low_price: Decimal | float | str,
    close_price: Decimal | float | str,
    volume: Decimal | float | str,
    quote_asset_volume: Decimal | float | str,
    number_of_trades: int,
    raw_ref: str,
    as_of: datetime,
    market_segment: str = "spot",
    quality_flags: Sequence[str] = (),
) -> dict[str, Any]:
    normalized_open = _as_utc(open_time)
    normalized_close = _as_utc(close_time)
    dataset = "daily_bar" if interval == "1d" else "intraday_bar"
    granularity = "daily" if interval == "1d" else interval
    period_start: date | datetime
    period_end: date | datetime
    if dataset == "daily_bar":
        period_start = normalized_open.date()
        period_end = normalized_open.date()
    else:
        period_start = normalized_open
        period_end = normalized_close
    universe_ref = _universe_ref_for_market_segment(market_segment)
    dataset_ref = (
        f"dataset:{dataset}:{Market.CRYPTO.value}:{market_segment}:{symbol_id}:{granularity}:"
        f"{_dataset_ref_period_part(period_start)}:{_dataset_ref_period_part(period_end)}"
    )

    row = {
        "symbol_id": symbol_id,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "market_segment": market_segment,
        "interval": interval,
        "open_time": normalized_open,
        "close_time": normalized_close,
        "open": _decimal_text(open_price),
        "high": _decimal_text(high_price),
        "low": _decimal_text(low_price),
        "close": _decimal_text(close_price),
        "volume": _decimal_text(volume),
        "amount": _decimal_text(quote_asset_volume),
        "quote_asset_volume": _decimal_text(quote_asset_volume),
        "number_of_trades": int(number_of_trades),
    }
    if dataset == "daily_bar":
        row["date"] = period_start

    field_set = tuple(sorted(row))
    return {
        "dataset_ref": dataset_ref,
        "dataset": dataset,
        "market": Market.CRYPTO.value,
        "symbol_id": symbol_id,
        "universe_ref": universe_ref,
        "granularity": granularity,
        "period_start": period_start,
        "period_end": period_end,
        "field_set": field_set,
        "as_of": _as_utc(as_of),
        "fresh_until": _as_utc(as_of),
        "source_roles": ("local_seed", "built_in_public"),
        "exchange": CRYPTO_EXCHANGE,
        "currency": quote_asset,
        "timezone": CRYPTO_TIMEZONE,
        "calendar": CRYPTO_CALENDAR,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "source_market_segment": market_segment,
        "provider_lineage": {
            "provider_id": LOCAL_CRYPTO_PROVIDER_ID,
            "endpoint_id": BINANCE_PUBLIC_DATA_ENDPOINT_ID,
            "raw_refs": (raw_ref,),
            "remote_attempted": False,
            "market_segment": market_segment,
            "source": "binance_public_data",
        },
        "schema_id": CRYPTO_BINANCE_BAR_SCHEMA_ID,
        "quality_flags": tuple(quality_flags),
        "row": row,
    }


def _universe_ref_for_market_segment(market_segment: str) -> str:
    if market_segment == "spot":
        return "binance_spot_all_symbols"
    if market_segment == "usdm_futures":
        return "binance_usdm_all_symbols"
    raise ValueError(f"unsupported market segment: {market_segment}")


def _dataset_ref_period_part(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def parse_binance_kline_zip(
    zip_path: Path,
    *,
    symbol_id: str,
    base_asset: str,
    quote_asset: str,
    interval: str,
    market_segment: str,
    raw_ref: str,
    as_of: datetime,
) -> tuple[dict[str, Any], ...]:
    if market_segment == "coinm_futures":
        raise ValueError("coinm_futures is not supported in CRYPTO Phase 1")
    if market_segment not in {"spot", "usdm_futures"}:
        raise ValueError(f"unsupported market segment: {market_segment}")
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = tuple(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if not csv_names:
            raise ValueError(f"kline zip contains no csv: {zip_path}")
        for name in csv_names:
            with archive.open(name) as raw_file:
                text_lines = (line.decode("utf-8").strip() for line in raw_file)
                reader = csv.reader(text_lines)
                header_map: dict[str, int] | None = None
                for line_number, columns in enumerate(reader, start=1):
                    if not columns or all(not column.strip() for column in columns):
                        continue
                    if header_map is None and _looks_like_kline_header(columns):
                        header_map = _kline_header_map(columns, zip_path=zip_path, line_number=line_number)
                        continue
                    normalized_columns = _columns_from_header(columns, header_map) if header_map is not None else columns
                    records.append(
                        _parse_kline_columns(
                            normalized_columns,
                            zip_path=zip_path,
                            line_number=line_number,
                            symbol_id=symbol_id,
                            base_asset=base_asset,
                            quote_asset=quote_asset,
                            interval=interval,
                            market_segment=market_segment,
                            raw_ref=raw_ref,
                            as_of=as_of,
                        )
                    )
    if not records:
        raise ValueError(f"kline csv is empty: {zip_path}")
    return tuple(records)


def import_crypto_prepackaged_to_repository(
    *,
    package_paths: Sequence[Path],
    repository: DatasetRepository,
    import_run_id: str,
    symbol_id: str,
    base_asset: str,
    quote_asset: str,
    interval: str,
    market_segment: str,
    as_of: datetime,
    replace_existing: bool = False,
    confirm_replace_existing: bool = False,
) -> CryptoPrepackagedImportResult:
    if replace_existing and not confirm_replace_existing:
        raise ValueError("replace-existing requires confirm_replace_existing")
    normalized_paths = tuple(Path(path) for path in package_paths)
    if not normalized_paths:
        raise ValueError("at least one package path is required")

    deleted_existing_count = 0
    if replace_existing:
        discard_result = discard_normalized_mongo_rows(
            repository,
            {
                "market": Market.CRYPTO.value,
                "symbol_id": symbol_id,
                "granularity": "daily" if interval == "1d" else interval,
                "source_market_segment": market_segment,
                "provider_lineage.provider_id": LOCAL_CRYPTO_PROVIDER_ID,
                "provider_lineage.endpoint_id": BINANCE_PUBLIC_DATA_ENDPOINT_ID,
                "provider_lineage.market_segment": market_segment,
            },
            confirmed=True,
        )
        deleted_existing_count = discard_result.deleted_count

    raw_refs: list[str] = []
    records: list[dict[str, Any]] = []
    for path in normalized_paths:
        raw_ref = _write_raw_package_record(
            repository=repository,
            import_run_id=import_run_id,
            package_path=path,
            symbol_id=symbol_id,
            interval=interval,
            market_segment=market_segment,
        )
        raw_refs.append(raw_ref)
        records.extend(
            parse_binance_kline_zip(
                path,
                symbol_id=symbol_id,
                base_asset=base_asset,
                quote_asset=quote_asset,
                interval=interval,
                market_segment=market_segment,
                raw_ref=raw_ref,
                as_of=as_of,
            )
        )
    dataset_refs = repository.upsert_normalized_documents(tuple(records))
    parsed_row_count = len(records)
    unique_row_count = len(dataset_refs)
    attempt_ref = _write_attempt_record(
        repository=repository,
        import_run_id=import_run_id,
        dataset_refs=dataset_refs,
        raw_refs=tuple(raw_refs),
        symbol_id=symbol_id,
        interval=interval,
        market_segment=market_segment,
    )
    manifest_ref = _write_manifest(
        repository=repository,
        import_run_id=import_run_id,
        dataset_refs=dataset_refs,
        raw_refs=tuple(raw_refs),
        attempt_refs=(attempt_ref,),
        symbol_id=symbol_id,
        interval=interval,
        market_segment=market_segment,
        file_count=len(normalized_paths),
        row_count=unique_row_count,
        parsed_row_count=parsed_row_count,
        deleted_existing_count=deleted_existing_count,
    )
    return CryptoPrepackagedImportResult(
        import_run_id=import_run_id,
        market_segment=market_segment,
        interval=interval,
        symbol_id=symbol_id,
        file_count=len(normalized_paths),
        row_count=unique_row_count,
        parsed_row_count=parsed_row_count,
        deleted_existing_count=deleted_existing_count,
        dataset_refs=dataset_refs,
        raw_refs=tuple(raw_refs),
        attempt_refs=(attempt_ref,),
        manifest_ref=manifest_ref,
    )


def local_warehouse_empty_gap(check: Any) -> DataGap:
    request_id = str(_read(check, "request_id") or "unknown")
    symbol_id = _optional_text(_read(check, "symbol_id"))
    universe_ref = _optional_text(_read(check, "universe_ref"))
    return DataGap(
        gap_id=f"gap:{request_id}:local_warehouse_empty",
        request_id=request_id,
        severity=GapSeverity.BLOCKER,
        reason=GapReason.WAREHOUSE_MISSING,
        market=Market.CRYPTO,
        symbol_id=symbol_id,
        exchange=_optional_text(_read(check, "exchange")),
        currency=_optional_text(_read(check, "currency")),
        timezone=_optional_text(_read(check, "timezone")),
        calendar=_optional_text(_read(check, "calendar")),
        base_asset=_optional_text(_read(check, "base_asset")),
        quote_asset=_optional_text(_read(check, "quote_asset")),
        data_type=str(_read(check, "data_type") or "unknown"),
        granularity=str(_read(check, "granularity") or "unknown"),
        required_fields=(),
        provider_ids_tried=(LOCAL_CRYPTO_PROVIDER_ID,),
        evidence_refs=(),
        human_readable=(
            "local_warehouse_empty: local CRYPTO seed warehouse has no bars for "
            f"{symbol_id or universe_ref}"
        ),
        as_of=datetime.now(tz=UTC),
    )


def date_range_missing_count(coverage_item: Mapping[str, Any]) -> int:
    missing_ranges = coverage_item.get("missing_ranges") or ()
    total = 0
    for item in missing_ranges:
        start, end = _coerce_missing_range(item)
        if start is None or end is None:
            continue
        if end < start:
            continue
        total += (end - start).days + 1
    if total:
        return total

    record_count = int(coverage_item.get("record_count") or 0)
    expected_start = _to_date(coverage_item.get("expected_start"))
    expected_end = _to_date(coverage_item.get("expected_end"))
    actual_start = _to_date(coverage_item.get("actual_start"))
    actual_end = _to_date(coverage_item.get("actual_end"))
    if record_count == 0 and expected_start and expected_end and expected_end >= expected_start:
        return (expected_end - expected_start).days + 1
    if not expected_start or not expected_end:
        return 0
    if actual_start is None or actual_end is None:
        return 0

    missing = 0
    if actual_start > expected_start:
        missing += (actual_start - expected_start).days
    if actual_end < expected_end:
        missing += (expected_end - actual_end).days
    return missing


def _coerce_missing_range(item: Any) -> tuple[date | None, date | None]:
    if isinstance(item, Mapping):
        return _to_date(item.get("start")), _to_date(item.get("end"))
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return _to_date(item[0]), _to_date(item[1])
    return None, None


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal_text(value: Decimal | float | str) -> str:
    return format(Decimal(str(value)), "f")


def _looks_like_kline_header(columns: Sequence[str]) -> bool:
    normalized = {_normalize_header_name(column) for column in columns}
    return "open_time" in normalized and "close_time" in normalized


def _kline_header_map(columns: Sequence[str], *, zip_path: Path, line_number: int) -> dict[str, int]:
    normalized_columns = tuple(_normalize_header_name(column) for column in columns)
    mapping: dict[str, int] = {}
    for field_name, aliases in _KLINE_FIELD_ALIASES.items():
        for index, column_name in enumerate(normalized_columns):
            if column_name in aliases:
                mapping[field_name] = index
                break
    missing = tuple(field_name for field_name in _KLINE_FIELDS if field_name not in mapping)
    if missing:
        raise ValueError(f"kline csv schema mismatch: {zip_path}:{line_number}: missing header fields {missing}")
    return mapping


def _columns_from_header(columns: Sequence[str], header_map: Mapping[str, int]) -> tuple[str, ...]:
    try:
        return tuple(columns[header_map[field_name]] for field_name in _KLINE_FIELDS)
    except IndexError as exc:
        raise ValueError("kline csv schema mismatch: row has fewer columns than header") from exc


def _normalize_header_name(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _parse_kline_columns(
    columns: Sequence[str],
    *,
    zip_path: Path,
    line_number: int,
    symbol_id: str,
    base_asset: str,
    quote_asset: str,
    interval: str,
    market_segment: str,
    raw_ref: str,
    as_of: datetime,
) -> dict[str, Any]:
    if len(columns) != 12:
        raise ValueError(f"kline csv schema mismatch: {zip_path}:{line_number}: expected 12 columns, got {len(columns)}")
    try:
        return build_crypto_bar_record(
            symbol_id=symbol_id,
            base_asset=base_asset,
            quote_asset=quote_asset,
            interval=interval,
            open_time=_parse_binance_timestamp(columns[0]),
            close_time=_parse_binance_timestamp(columns[6]),
            open_price=_decimal_text(columns[1]),
            high_price=_decimal_text(columns[2]),
            low_price=_decimal_text(columns[3]),
            close_price=_decimal_text(columns[4]),
            volume=_decimal_text(columns[5]),
            quote_asset_volume=_decimal_text(columns[7]),
            number_of_trades=int(columns[8]),
            raw_ref=raw_ref,
            as_of=as_of,
            market_segment=market_segment,
        )
    except ValueError as exc:
        raise ValueError(f"kline csv parse failed: {zip_path}:{line_number}: {exc}") from exc


def _parse_binance_timestamp(value: str) -> datetime:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"invalid timestamp: {value}")
    raw = int(text)
    if 10_000_000_000_000 <= raw < 10_000_000_000_000_000:
        return datetime.fromtimestamp(raw / 1_000_000, tz=UTC)
    if 10_000_000_000 <= raw < 10_000_000_000_000:
        return datetime.fromtimestamp(raw / 1_000, tz=UTC)
    raise ValueError(f"unsupported timestamp unit: {value}")


def _write_raw_package_record(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    package_path: Path,
    symbol_id: str,
    interval: str,
    market_segment: str,
) -> str:
    payload = {
        "import_run_id": import_run_id,
        "package_path": str(package_path),
        "package_name": package_path.name,
        "package_size_bytes": package_path.stat().st_size,
        "symbol_id": symbol_id,
        "interval": interval,
        "market_segment": market_segment,
    }
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return repository.insert_raw_payload(
        {
            "raw_ref": f"raw:CRYPTO:{LOCAL_CRYPTO_PROVIDER_ID}:{digest[:12]}",
            "metadata_ref": f"rawmeta:CRYPTO:{LOCAL_CRYPTO_PROVIDER_ID}:{digest[:12]}",
            "provider": LOCAL_CRYPTO_PROVIDER_ID,
            "endpoint": BINANCE_PUBLIC_DATA_ENDPOINT_ID,
            "market": Market.CRYPTO.value,
            "payload_hash": f"sha256:{digest}",
            "object_uri": str(package_path),
            "storage_mode": "metadata_only",
            "payload_size_bytes": payload["package_size_bytes"],
            "license_policy": {"raw_storage_mode": "metadata_only", "normalized_storage_allowed": True},
            "redaction_meta": {"payload_body_stored": False, "mongo_body_stored": False},
            "created_at": datetime.now(tz=UTC),
            "payload_summary": payload,
        }
    )


def _write_attempt_record(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    dataset_refs: tuple[str, ...],
    raw_refs: tuple[str, ...],
    symbol_id: str,
    interval: str,
    market_segment: str,
) -> str:
    compact_refs = _compact_dataset_refs_for_mongo(dataset_refs)
    return repository.insert_provider_attempt(
        {
            "attempt_ref": f"attempt:{LOCAL_CRYPTO_PROVIDER_ID}:{BINANCE_PUBLIC_DATA_ENDPOINT_ID}:{uuid4().hex[:12]}",
            "provider": LOCAL_CRYPTO_PROVIDER_ID,
            "endpoint": BINANCE_PUBLIC_DATA_ENDPOINT_ID,
            "status": "local_seed_imported",
            "remote_attempted": False,
            "remote_success": False,
            "dataset_refs": compact_refs["dataset_refs"],
            "dataset_ref_count": compact_refs["dataset_ref_count"],
            "dataset_refs_truncated": compact_refs["dataset_refs_truncated"],
            "dataset_refs_sample": compact_refs["dataset_refs_sample"],
            "dataset_refs_sha256": compact_refs["dataset_refs_sha256"],
            "dataset_ref_prefixes": _dataset_ref_prefixes_for_import(
                symbol_id=symbol_id,
                interval=interval,
                market_segment=market_segment,
            ),
            "raw_refs": raw_refs,
            "gap_codes": (),
            "source_metadata": {
                "import_run_id": import_run_id,
                "execution_scope": "local_crypto_prepackaged_seed_import",
                "symbol_id": symbol_id,
                "interval": interval,
                "market_segment": market_segment,
            },
            "created_at": datetime.now(tz=UTC),
        }
    )


def _write_manifest(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    dataset_refs: tuple[str, ...],
    raw_refs: tuple[str, ...],
    attempt_refs: tuple[str, ...],
    symbol_id: str,
    interval: str,
    market_segment: str,
    file_count: int,
    row_count: int,
    parsed_row_count: int,
    deleted_existing_count: int,
) -> str:
    dataset_scope = "daily_bar" if interval == "1d" else "intraday_bar"
    compact_refs = _compact_dataset_refs_for_mongo(dataset_refs)
    return repository.write_dataset_manifest(
        {
            "manifest_ref": f"manifest:CRYPTO:crypto_prepackaged:{import_run_id}:{symbol_id}:{interval}:{market_segment}",
            "job_id": import_run_id,
            "job_type": "seed_import",
            "market": Market.CRYPTO.value,
            "dataset_scope": dataset_scope,
            "source_summary": "local:crypto_prepackaged:binance_public_data",
            "dataset_refs": compact_refs["dataset_refs"],
            "dataset_ref_count": compact_refs["dataset_ref_count"],
            "dataset_refs_truncated": compact_refs["dataset_refs_truncated"],
            "dataset_refs_sample": compact_refs["dataset_refs_sample"],
            "dataset_refs_sha256": compact_refs["dataset_refs_sha256"],
            "raw_refs": raw_refs,
            "attempt_refs": attempt_refs,
            "output_refs": {
                "dataset_refs": compact_refs["dataset_refs"],
                "dataset_ref_count": compact_refs["dataset_ref_count"],
                "dataset_refs_truncated": compact_refs["dataset_refs_truncated"],
                "dataset_refs_sample": compact_refs["dataset_refs_sample"],
                "dataset_refs_sha256": compact_refs["dataset_refs_sha256"],
                "raw_refs": raw_refs,
                "attempt_refs": attempt_refs,
            },
            "gap_summary": (),
            "audit": {
                "file_count": file_count,
                "row_count": row_count,
                "parsed_row_count": parsed_row_count,
                "unique_dataset_ref_count": row_count,
                "duplicate_row_count": max(parsed_row_count - row_count, 0),
                "symbol_id": symbol_id,
                "interval": interval,
                "market_segment": market_segment,
            },
            "cleanup": {
                "replace_existing_provider_scope": deleted_existing_count > 0,
                "deleted_existing_count": deleted_existing_count,
                "provider_id": LOCAL_CRYPTO_PROVIDER_ID,
                "dataset_scope": dataset_scope,
            },
            "created_at": datetime.now(tz=UTC),
        }
    )


def _compact_dataset_refs_for_mongo(dataset_refs: Sequence[str]) -> dict[str, Any]:
    refs = tuple(dict.fromkeys(str(ref) for ref in dataset_refs if str(ref).strip()))
    digest = sha256(json.dumps(refs, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if len(refs) <= _MONGO_FULL_DATASET_REFS_LIMIT:
        return {
            "dataset_refs": refs,
            "dataset_ref_count": len(refs),
            "dataset_refs_truncated": False,
            "dataset_refs_sample": refs,
            "dataset_refs_sha256": f"sha256:{digest}",
        }
    head_count = _MONGO_DATASET_REFS_SAMPLE_LIMIT // 2
    tail_count = _MONGO_DATASET_REFS_SAMPLE_LIMIT - head_count
    sample = tuple((*refs[:head_count], *refs[-tail_count:]))
    return {
        "dataset_refs": sample,
        "dataset_ref_count": len(refs),
        "dataset_refs_truncated": True,
        "dataset_refs_sample": sample,
        "dataset_refs_sha256": f"sha256:{digest}",
    }


def _dataset_ref_prefixes_for_import(*, symbol_id: str, interval: str, market_segment: str) -> tuple[str, ...]:
    dataset = "daily_bar" if interval == "1d" else "intraday_bar"
    granularity = "daily" if interval == "1d" else interval
    return (f"dataset:{dataset}:{Market.CRYPTO.value}:{market_segment}:{symbol_id}:{granularity}:",)


def _read(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text or None
