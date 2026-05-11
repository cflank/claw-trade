from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .models import ProviderResult, ProviderSpec


DEFAULT_MARKET_ADJUST = "qfq"
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
_ROW_REQUIRED_FIELDS = ("trade_date", "open", "close", "high", "low", "volume")
_OPTIONAL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "pre_close": ("pre_close", "preclose", "previous_close", "昨收", "昨收价", "昨收盘"),
    "change": ("change", "price_change", "chg", "涨跌额", "涨跌"),
    "pct_chg": ("pct_chg", "pct_change", "change_pct", "涨跌幅", "涨跌幅%"),
}


def normalize_market_rows(
    provider_results: Iterable[ProviderResult | Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_rows: list[dict[str, Any]] = []
    diagnostics: list[str] = []

    for result_index, item in enumerate(provider_results):
        metadata, raw_rows, item_diagnostics = _extract_provider_rows(item, result_index=result_index)
        diagnostics.extend(item_diagnostics)
        for row_index, raw_row in enumerate(raw_rows):
            row, row_diagnostics = _normalize_single_row(
                raw_row=raw_row,
                metadata=metadata,
                result_index=result_index,
                row_index=row_index,
            )
            diagnostics.extend(row_diagnostics)
            if row is not None:
                normalized_rows.append(row)

    return normalized_rows, diagnostics


def validate_ohlcv_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    accepted: list[dict[str, Any]] = []
    diagnostics: list[str] = []

    for index, row in enumerate(rows):
        accepted_row, row_diagnostics = _validate_single_row(row=row, row_index=index)
        diagnostics.extend(row_diagnostics)
        if accepted_row is not None:
            accepted.append(accepted_row)

    return accepted, diagnostics


def merge_market_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked_rows: list[tuple[int, int, Mapping[str, Any]]] = []
    for index, row in enumerate(rows):
        priority = str(row.get("priority", "P2")).upper()
        if priority not in {"P0", "P1"}:
            continue
        ranked_rows.append((_PRIORITY_RANK[priority], index, row))

    ranked_rows.sort(key=lambda item: (item[0], item[1]))
    by_trade_date: dict[str, dict[str, Any]] = {}
    for _, _, row in ranked_rows:
        trade_date = str(row.get("trade_date", "")).strip()
        if trade_date and trade_date not in by_trade_date:
            by_trade_date[trade_date] = dict(row)

    merged = [by_trade_date[key] for key in sorted(by_trade_date)]
    return merged


def build_market_quality_input(accepted_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(accepted_rows)
    status_candidate = "failed" if not rows else "partial"
    return {
        "accepted_row_count": len(rows),
        "status_candidate": status_candidate,
    }


def _extract_provider_rows(
    item: ProviderResult | Mapping[str, Any],
    *,
    result_index: int,
) -> tuple[dict[str, str], list[Mapping[str, Any]], list[str]]:
    diagnostics: list[str] = []
    if isinstance(item, ProviderResult):
        metadata = _metadata_from_spec(item.spec)
        rows = item.normalized_rows
        return metadata, rows, diagnostics

    if not isinstance(item, Mapping):
        diagnostics.append(f"schema_invalid:market_result[{result_index}]:not_mapping")
        return _default_metadata(), [], diagnostics

    spec_raw = item.get("spec")
    if isinstance(spec_raw, ProviderSpec):
        metadata = _metadata_from_spec(spec_raw)
    elif isinstance(spec_raw, Mapping):
        metadata = _metadata_from_mapping(spec_raw)
    else:
        metadata = _metadata_from_mapping(item)

    rows_raw = item.get("normalized_rows", item.get("rows", []))
    if not isinstance(rows_raw, list):
        diagnostics.append(
            "schema_invalid:"
            f"market_result[{result_index}]:rows_not_list:{metadata['provider']}:{metadata['endpoint']}"
        )
        return metadata, [], diagnostics

    mapping_rows: list[Mapping[str, Any]] = []
    for row_index, row in enumerate(rows_raw):
        if isinstance(row, Mapping):
            mapping_rows.append(row)
        else:
            diagnostics.append(
                "schema_invalid:"
                f"market_result[{result_index}]:row[{row_index}]_not_mapping:{metadata['provider']}:{metadata['endpoint']}"
            )
    return metadata, mapping_rows, diagnostics


def _normalize_single_row(
    *,
    raw_row: Mapping[str, Any],
    metadata: Mapping[str, str],
    result_index: int,
    row_index: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    diagnostics: list[str] = []
    row = dict(raw_row)

    trade_date = _normalize_trade_date(_first_value(row, ("trade_date", "date", "日期")))
    if trade_date is None:
        diagnostics.append(_row_diag(metadata, result_index, row_index, "invalid_trade_date"))
        return None, diagnostics

    values: dict[str, float] = {}
    for field_name, aliases in (
        ("open", ("open", "开盘")),
        ("close", ("close", "收盘")),
        ("high", ("high", "最高")),
        ("low", ("low", "最低")),
        ("volume", ("volume", "成交量")),
    ):
        numeric = _to_float(_first_value(row, aliases))
        if numeric is None:
            diagnostics.append(_row_diag(metadata, result_index, row_index, f"missing_or_invalid_{field_name}"))
            return None, diagnostics
        values[field_name] = numeric

    amount_value = _first_value(row, ("amount", "成交额"))
    amount = _to_float(amount_value) if amount_value is not None else None
    if amount_value is not None and amount is None:
        diagnostics.append(_row_diag(metadata, result_index, row_index, "invalid_amount"))
        return None, diagnostics

    adjust_raw = _first_value(row, ("adjust", "fq", "复权"))
    adjust = DEFAULT_MARKET_ADJUST if adjust_raw is None else str(adjust_raw).strip().lower()
    if adjust != DEFAULT_MARKET_ADJUST:
        diagnostics.append(_row_diag(metadata, result_index, row_index, f"unsupported_adjust_{adjust}"))
        return None, diagnostics

    normalized = {
        "trade_date": trade_date,
        "open": values["open"],
        "close": values["close"],
        "high": values["high"],
        "low": values["low"],
        "volume": values["volume"],
        "amount": amount,
        "adjust": DEFAULT_MARKET_ADJUST,
        "provider": metadata["provider"],
        "endpoint": metadata["endpoint"],
        "priority": metadata["priority"],
        "role": metadata["role"],
    }
    optional_pre_close = _to_float(_first_value(row, _OPTIONAL_FIELD_ALIASES["pre_close"]))
    if optional_pre_close is not None:
        normalized["pre_close"] = optional_pre_close

    optional_change = _to_float(_first_value(row, _OPTIONAL_FIELD_ALIASES["change"]))
    if optional_change is not None:
        normalized["change"] = optional_change

    optional_pct_chg = _to_percent_float(_first_value(row, _OPTIONAL_FIELD_ALIASES["pct_chg"]))
    if optional_pct_chg is not None:
        normalized["pct_chg"] = optional_pct_chg
    return normalized, diagnostics


def _validate_single_row(
    *,
    row: Mapping[str, Any],
    row_index: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    diagnostics: list[str] = []
    provider = str(row.get("provider", "unknown"))
    endpoint = str(row.get("endpoint", "unknown"))
    trade_date = _normalize_trade_date(row.get("trade_date"))
    if trade_date is None:
        diagnostics.append(f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:invalid_trade_date")
        return None, diagnostics

    values: dict[str, float] = {}
    for field_name in _ROW_REQUIRED_FIELDS[1:]:
        value = _to_float(row.get(field_name))
        if value is None:
            diagnostics.append(
                f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:{trade_date}:invalid_{field_name}"
            )
            return None, diagnostics
        values[field_name] = value

    open_price = values["open"]
    close_price = values["close"]
    high_price = values["high"]
    low_price = values["low"]
    volume = values["volume"]
    amount_raw = row.get("amount")
    amount = _to_float(amount_raw) if amount_raw is not None else None

    if open_price <= 0 or close_price <= 0 or high_price <= 0 or low_price <= 0:
        diagnostics.append(
            f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:{trade_date}:non_positive_price"
        )
        return None, diagnostics
    if high_price < max(open_price, close_price, low_price):
        diagnostics.append(
            f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:{trade_date}:high_lt_open_close_low"
        )
        return None, diagnostics
    if low_price > min(open_price, close_price, high_price):
        diagnostics.append(
            f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:{trade_date}:low_gt_open_close_high"
        )
        return None, diagnostics
    if volume < 0:
        diagnostics.append(f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:{trade_date}:negative_volume")
        return None, diagnostics
    if amount is not None and amount < 0:
        diagnostics.append(f"schema_invalid:market_row[{row_index}]:{provider}:{endpoint}:{trade_date}:negative_amount")
        return None, diagnostics

    accepted = dict(row)
    accepted["trade_date"] = trade_date
    accepted["open"] = open_price
    accepted["close"] = close_price
    accepted["high"] = high_price
    accepted["low"] = low_price
    accepted["volume"] = volume
    accepted["amount"] = amount
    accepted["adjust"] = str(row.get("adjust", DEFAULT_MARKET_ADJUST)).strip().lower() or DEFAULT_MARKET_ADJUST
    return accepted, diagnostics


def _metadata_from_spec(spec: ProviderSpec) -> dict[str, str]:
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "priority": spec.priority,
        "role": spec.role,
    }


def _metadata_from_mapping(raw: Mapping[str, Any]) -> dict[str, str]:
    default = _default_metadata()
    provider = str(raw.get("provider", default["provider"])).strip() or default["provider"]
    endpoint = str(raw.get("endpoint", default["endpoint"])).strip() or default["endpoint"]
    priority = str(raw.get("priority", default["priority"])).strip().upper() or default["priority"]
    role = str(raw.get("role", default["role"])).strip() or default["role"]
    if priority not in _PRIORITY_RANK:
        priority = "P2"
    return {
        "provider": provider,
        "endpoint": endpoint,
        "priority": priority,
        "role": role,
    }


def _default_metadata() -> dict[str, str]:
    return {
        "provider": "unknown",
        "endpoint": "unknown",
        "priority": "P2",
        "role": "unknown",
    }


def _first_value(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            return row[alias]
    return None


def _normalize_trade_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text == "":
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_percent_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_diag(metadata: Mapping[str, str], result_index: int, row_index: int, reason: str) -> str:
    return (
        "schema_invalid:"
        f"market_result[{result_index}]:{metadata['provider']}:{metadata['endpoint']}:"
        f"row[{row_index}]:{reason}"
    )


__all__ = [
    "DEFAULT_MARKET_ADJUST",
    "build_market_quality_input",
    "merge_market_rows",
    "normalize_market_rows",
    "validate_ohlcv_rows",
]
