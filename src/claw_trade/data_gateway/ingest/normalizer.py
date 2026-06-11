from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from claw_trade.data_gateway.models import Market

from . import DataGap


@dataclass(frozen=True)
class NormalizedBatch:
    rows: tuple[dict[str, Any], ...]
    gaps: tuple[DataGap, ...]


class Normalizer:
    def normalize(self, result: Any, batch: Any, raw_refs: tuple[str, ...]) -> NormalizedBatch:
        payload = result.payload
        rows = _coerce_rows(payload)
        mapped_rows: list[dict[str, Any]] = []
        gaps: list[DataGap] = []
        batch_granularity = _normalize_granularity(getattr(batch, "granularity", None))
        if batch_granularity is None:
            gaps.append(_gap(batch, "field_missing", raw_refs, required_fields=("granularity",)))

        for row in rows:
            next_row = _map_field_aliases(row)
            next_row["market"] = _enum_value(getattr(batch, "market", next_row.get("market")))
            next_row["dataset"] = _first_text(getattr(batch, "data_type", None), next_row.get("dataset"))
            _copy_optional_text_field(next_row, batch, "universe_ref")

            row_granularity = _normalize_granularity(next_row.get("granularity"))
            if row_granularity and batch_granularity and row_granularity != batch_granularity:
                gaps.append(
                    _gap(
                        batch,
                        "granularity_mismatch",
                        raw_refs,
                        message=f"provider granularity {row_granularity} does not match requested {batch_granularity}",
                    )
                )
            next_row["granularity"] = batch_granularity or row_granularity

            next_row["provider_lineage"] = _provider_lineage(batch, raw_refs)
            next_row["source_raw_refs"] = raw_refs
            next_row.setdefault("source_roles", ("official",))
            next_row.setdefault("as_of", getattr(result, "fetched_at", None))
            next_row.setdefault("fresh_until", getattr(result, "fetched_at", None))
            _copy_market_field(next_row, batch, "exchange")
            _copy_market_field(next_row, batch, "currency")
            _copy_market_field(next_row, batch, "timezone")
            _copy_market_field(next_row, batch, "calendar")
            _copy_asset_fields(next_row, batch)
            _normalize_temporal_fields(next_row)
            mapped_rows.append(next_row)

        required = _normalization_required_fields(batch)
        missing = tuple(field for field in required if not any(field in row and row[field] is not None for row in mapped_rows))
        if missing:
            gaps.append(
                _gap(batch, "field_missing", raw_refs, required_fields=missing)
            )

        market_required_missing = _missing_market_dimensions(mapped_rows)
        if market_required_missing:
            gaps.append(
                _gap(batch, "field_missing", raw_refs, required_fields=market_required_missing)
            )

        market = _enum_value(getattr(batch, "market", "")).upper()
        if market == "CRYPTO":
            missing_assets = _missing_crypto_assets(mapped_rows)
            if missing_assets:
                gaps.append(
                    _gap(batch, "field_missing", raw_refs, required_fields=missing_assets)
                )
        elif _has_any_asset_fields(mapped_rows) or _batch_has_any_asset_fields(batch):
            gaps.append(
                _gap(
                    batch,
                    "invalid_request",
                    raw_refs,
                    message="non-CRYPTO rows must not include base_asset/quote_asset",
                )
            )

        range_gap = _date_range_gap(mapped_rows, batch, raw_refs)
        if range_gap is not None:
            gaps.append(range_gap)

        blocking_gap = any(_is_blocking_gap(gap) for gap in gaps)
        rows_to_store = () if blocking_gap else tuple(mapped_rows)
        return NormalizedBatch(rows=rows_to_store, gaps=tuple(gaps))


def _coerce_rows(payload: Any) -> tuple[dict[str, Any], ...]:
    if payload is None:
        return ()
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return tuple(dict(item) for item in rows if isinstance(item, dict))
        return (dict(payload),)
    if isinstance(payload, list):
        return tuple(dict(item) for item in payload if isinstance(item, dict))
    return ()


def _normalization_required_fields(batch: Any) -> tuple[str, ...]:
    requested = tuple(str(field).strip() for field in tuple(getattr(batch, "fields_union", ()) or getattr(batch, "required_fields", ())) if str(field).strip())
    capability_fields = tuple(str(field).strip() for field in tuple(getattr(batch, "capability_fields", ()) or ()) if str(field).strip())
    if not requested or not capability_fields:
        return requested
    capability_set = set(capability_fields)
    return tuple(field for field in requested if field in capability_set)


def _map_field_aliases(row: dict[str, Any]) -> dict[str, Any]:
    mapped = dict(row)
    aliases = {
        "trade_date": "date",
        "Date": "date",
        "vol": "volume",
        "Volume": "volume",
    }
    for source, target in aliases.items():
        if source in mapped and target not in mapped:
            mapped[target] = mapped[source]
    return mapped


def _copy_market_field(row: dict[str, Any], batch: Any, key: str) -> None:
    _copy_optional_text_field(row, batch, key)


def _copy_optional_text_field(row: dict[str, Any], batch: Any, key: str) -> None:
    if _non_empty_str(row.get(key)):
        return
    value = getattr(batch, key, None)
    if _non_empty_str(value):
        row[key] = value


def _copy_asset_fields(row: dict[str, Any], batch: Any) -> None:
    market = str(getattr(batch, "market", "")).upper()
    if market == "CRYPTO":
        for key in ("base_asset", "quote_asset"):
            if not _non_empty_str(row.get(key)):
                value = getattr(batch, key, None)
                if _non_empty_str(value):
                    row[key] = str(value).upper()
        return
    row.setdefault("base_asset", None)
    row.setdefault("quote_asset", None)


def _normalize_temporal_fields(row: dict[str, Any]) -> None:
    if "date" in row:
        parsed_date = _parse_date(row.get("date"))
        if parsed_date is not None:
            row["date"] = parsed_date
            row.setdefault("period_start", parsed_date)
            row.setdefault("period_end", parsed_date)

    for key in ("timestamp", "published_at"):
        if key not in row:
            continue
        parsed_dt = _parse_datetime(row.get(key), row.get("timezone"))
        if parsed_dt is None:
            continue
        row[key] = parsed_dt
        row.setdefault("timestamp_utc", parsed_dt.astimezone(UTC))
        row.setdefault("period_start", parsed_dt.date())
        row.setdefault("period_end", parsed_dt.date())

    for key in ("event_date", "unlock_date", "ex_date"):
        if key not in row:
            continue
        parsed_date = _parse_date(row.get(key))
        if parsed_date is None:
            continue
        row[key] = parsed_date
        row.setdefault("period_start", parsed_date)
        row.setdefault("period_end", parsed_date)


def _missing_market_dimensions(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    required = ("exchange", "currency", "timezone", "calendar")
    missing: list[str] = []
    for key in required:
        present = bool(rows) and all(_non_empty_str(row.get(key)) for row in rows)
        if not present:
            missing.append(key)
    return tuple(missing)


def _missing_crypto_assets(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    missing: list[str] = []
    if not any(_non_empty_str(row.get("base_asset")) for row in rows):
        missing.append("base_asset")
    if not any(_non_empty_str(row.get("quote_asset")) for row in rows):
        missing.append("quote_asset")
    return tuple(missing)


def _has_any_asset_fields(rows: list[dict[str, Any]]) -> bool:
    return any(
        _non_empty_str(row.get("base_asset")) or _non_empty_str(row.get("quote_asset"))
        for row in rows
    )


def _batch_has_any_asset_fields(batch: Any) -> bool:
    return _non_empty_str(getattr(batch, "base_asset", None)) or _non_empty_str(getattr(batch, "quote_asset", None))


def _non_empty_str(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text)


def _is_blocking_gap(gap: DataGap) -> bool:
    reason = str(getattr(gap.reason, "value", gap.reason))
    if reason == "invalid_request":
        return True
    if reason == "granularity_mismatch":
        return True
    if reason == "date_range_missing":
        return str(gap.human_readable).startswith("normalized rows do not expose")
    if reason != "field_missing":
        return False
    return bool(set(gap.required_fields) & {"exchange", "currency", "timezone", "calendar", "base_asset", "quote_asset"})


def _first_symbol_id(batch: Any) -> str | None:
    symbols = tuple(getattr(batch, "symbol_ids", ()) or ())
    if symbols:
        return str(symbols[0])
    symbol_id = getattr(batch, "symbol_id", None)
    return None if symbol_id is None else str(symbol_id)


def _first_request_id(batch: Any) -> str:
    request_ids = tuple(getattr(batch, "request_ids", ()) or ())
    if request_ids:
        return str(request_ids[0])
    return str(getattr(batch, "request_id", "ingest"))


def _gap(
    batch: Any,
    reason: str,
    raw_refs: tuple[str, ...],
    *,
    required_fields: tuple[str, ...] = (),
    message: str | None = None,
) -> DataGap:
    return DataGap.by_reason(
        reason,
        request_id=_first_request_id(batch),
        market=_market_for_gap(batch),
        data_type=_data_type_for_gap(batch),
        granularity=_granularity_for_gap(batch),
        required_fields=required_fields,
        evidence_refs=raw_refs,
        message=message,
        symbol_id=_first_symbol_id(batch),
    )


def _market_for_gap(batch: Any) -> Market:
    raw = getattr(batch, "market", None)
    if isinstance(raw, Market):
        return raw
    try:
        return Market(str(raw))
    except ValueError:
        return Market.CN_A


def _data_type_for_gap(batch: Any) -> str:
    value = getattr(batch, "data_type", None)
    if value is None or not str(value).strip():
        return "missing_data_type"
    return str(value).strip()


def _granularity_for_gap(batch: Any) -> str:
    value = _normalize_granularity(getattr(batch, "granularity", None))
    return value or "missing_granularity"


def _normalize_granularity(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if _non_empty_str(value):
            return str(value).strip()
    return None


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _provider_lineage(batch: Any, raw_refs: tuple[str, ...]) -> dict[str, Any]:
    return {
        "provider_id": getattr(batch, "provider_id", None),
        "endpoint_id": getattr(batch, "endpoint_id", None),
        "provider_config_version": getattr(batch, "provider_config_version", None),
        "raw_refs": raw_refs,
    }


def _date_range_gap(rows: list[dict[str, Any]], batch: Any, raw_refs: tuple[str, ...]) -> DataGap | None:
    request_start = _to_date(getattr(batch, "date_range_start", None))
    request_end = _to_date(getattr(batch, "date_range_end", None))
    if request_start is None and request_end is None:
        return None
    batch_granularity = _normalize_granularity(getattr(batch, "granularity", None))
    if batch_granularity in {"event", "realtime"}:
        return None

    starts = [_to_date(row.get("period_start")) for row in rows]
    ends = [_to_date(row.get("period_end")) for row in rows]
    starts = [item for item in starts if item is not None]
    ends = [item for item in ends if item is not None]
    if not starts or not ends:
        return _gap(batch, "date_range_missing", raw_refs, message="normalized rows do not expose period_start/period_end")

    actual_start = min(starts)
    actual_end = max(ends)
    if request_start is not None and actual_start > request_start:
        return _gap(batch, "date_range_missing", raw_refs)
    if request_end is not None and actual_end < request_end:
        return _gap(batch, "date_range_missing", raw_refs)
    return None


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_date(value)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        parsed_dt = _parse_datetime(text, None)
        return None if parsed_dt is None else parsed_dt.date()


def _parse_datetime(value: Any, timezone_name: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    elif value is None:
        return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed_date = _parse_date(text)
            if parsed_date is None:
                return None
            return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
    if parsed.tzinfo is None:
        zone = _zoneinfo(timezone_name)
        if zone is None:
            return None
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(UTC)


def _zoneinfo(timezone_name: Any) -> ZoneInfo | None:
    if not _non_empty_str(timezone_name):
        return None
    try:
        return ZoneInfo(str(timezone_name))
    except ZoneInfoNotFoundError:
        return None
