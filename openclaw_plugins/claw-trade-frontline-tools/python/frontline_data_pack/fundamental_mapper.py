from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

from .models import FundamentalField, FundamentalFieldName, FundamentalUnit, ProviderResult


_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
_CORE_FIELD_ORDER: tuple[FundamentalFieldName, ...] = (
    "valuation.pe_ttm",
    "valuation.pb",
    "financial_indicators.roe",
    "income_statement.revenue",
    "income_statement.net_profit",
    "cash_flow.operating_cash_flow",
)
_CANONICAL_ALIASES: dict[str, FundamentalFieldName] = {
    "company_profile.industry": "company_profile.industry",
    "industry": "company_profile.industry",
    "所属行业": "company_profile.industry",
    "行业": "company_profile.industry",
    "company_profile.main_business": "company_profile.main_business",
    "main_business": "company_profile.main_business",
    "主营业务": "company_profile.main_business",
    "主营业务及产品": "company_profile.main_business",
    "主营范围": "company_profile.main_business",
    "经营范围": "company_profile.main_business",
    "valuation.pe_ttm": "valuation.pe_ttm",
    "pe_ttm": "valuation.pe_ttm",
    "pe": "valuation.pe_ttm",
    "市盈率-动态": "valuation.pe_ttm",
    "市盈率": "valuation.pe_ttm",
    "valuation.pb": "valuation.pb",
    "pb": "valuation.pb",
    "市净率": "valuation.pb",
    "valuation.total_mv": "valuation.total_mv",
    "total_mv": "valuation.total_mv",
    "总市值": "valuation.total_mv",
    "总市值(元)": "valuation.total_mv",
    "price_context.close": "price_context.close",
    "close": "price_context.close",
    "latest_price": "price_context.close",
    "last_price": "price_context.close",
    "最新价": "price_context.close",
    "现价": "price_context.close",
    "price_context.trade_date": "price_context.trade_date",
    "trade_date": "price_context.trade_date",
    "日期": "price_context.trade_date",
    "更新时间": "price_context.trade_date",
    "price_context.volume": "price_context.volume",
    "volume": "price_context.volume",
    "成交量": "price_context.volume",
    "financial_indicators.roe": "financial_indicators.roe",
    "roe": "financial_indicators.roe",
    "净资产收益率": "financial_indicators.roe",
    "financial_indicators.roa": "financial_indicators.roa",
    "roa": "financial_indicators.roa",
    "总资产收益率": "financial_indicators.roa",
    "financial_indicators.gross_margin": "financial_indicators.gross_margin",
    "gross_margin": "financial_indicators.gross_margin",
    "grossprofit_margin": "financial_indicators.gross_margin",
    "毛利率": "financial_indicators.gross_margin",
    "financial_indicators.netprofit_margin": "financial_indicators.netprofit_margin",
    "netprofit_margin": "financial_indicators.netprofit_margin",
    "销售净利率": "financial_indicators.netprofit_margin",
    "financial_indicators.debt_to_assets": "financial_indicators.debt_to_assets",
    "debt_to_assets": "financial_indicators.debt_to_assets",
    "资产负债率": "financial_indicators.debt_to_assets",
    "income_statement.revenue": "income_statement.revenue",
    "revenue": "income_statement.revenue",
    "营业总收入": "income_statement.revenue",
    "营业收入": "income_statement.revenue",
    "income_statement.net_profit": "income_statement.net_profit",
    "net_profit": "income_statement.net_profit",
    "净利润": "income_statement.net_profit",
    "归母净利润": "income_statement.net_profit",
    "income_statement.eps": "income_statement.eps",
    "eps": "income_statement.eps",
    "每股收益": "income_statement.eps",
    "基本每股收益": "income_statement.eps",
    "cash_flow.operating_cash_flow": "cash_flow.operating_cash_flow",
    "operating_cash_flow": "cash_flow.operating_cash_flow",
    "经营现金流": "cash_flow.operating_cash_flow",
    "经营活动产生的现金流量净额": "cash_flow.operating_cash_flow",
}
_DEFAULT_UNITS: dict[FundamentalFieldName, FundamentalUnit] = {
    "company_profile.industry": "text",
    "company_profile.main_business": "text",
    "valuation.pe_ttm": "ratio",
    "valuation.pb": "ratio",
    "valuation.total_mv": "cny",
    "price_context.close": "cny_per_share",
    "price_context.trade_date": "date",
    "price_context.volume": "share",
    "financial_indicators.roe": "%",
    "financial_indicators.roa": "%",
    "financial_indicators.gross_margin": "%",
    "financial_indicators.netprofit_margin": "%",
    "financial_indicators.debt_to_assets": "%",
    "income_statement.revenue": "cny",
    "income_statement.net_profit": "cny",
    "income_statement.eps": "cny_per_share",
    "cash_flow.operating_cash_flow": "cny",
}
_UNIT_ALIASES: dict[str, FundamentalUnit] = {
    "text": "text",
    "raw": "raw",
    "ratio": "ratio",
    "x": "ratio",
    "cny": "cny",
    "cny_per_share": "cny_per_share",
    "share": "share",
    "%": "%",
    "percent": "%",
    "date": "date",
}
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ROW_META_KEYS = frozenset(
    {
        "field_name",
        "field_path",
        "name",
        "path",
        "value",
        "unit",
        "report_period",
        "source_time",
        "provider",
        "endpoint",
        "raw_payload_ref",
        "payload_hash",
        "ticker_code",
    }
)


def map_fundamental_fields(
    provider_results: list[ProviderResult],
) -> tuple[dict[str, FundamentalField], list[str]]:
    mapped: dict[str, FundamentalField] = {}
    conflict_diagnostics: list[str] = []

    ordered_results = sorted(
        enumerate(provider_results),
        key=lambda item: (_priority_rank(item[1].spec.priority), item[0]),
    )
    for _, result in ordered_results:
        for row in result.normalized_rows:
            for field_name, value, unit in _row_candidates(row):
                payload_hash = _as_text(row.get("payload_hash")) or result.attempt.payload_hash
                raw_payload_ref = _as_text(row.get("raw_payload_ref")) or result.attempt.raw_payload_ref
                if payload_hash is None or raw_payload_ref is None:
                    continue
                if not _SHA256_RE.fullmatch(payload_hash):
                    continue

                source_time = _as_text(row.get("source_time")) or _as_text(row.get("price_context.trade_date"))
                report_period = _as_text(row.get("report_period"))
                normalized_value = _normalize_field_value(value)
                if normalized_value is None:
                    continue
                normalized_unit = unit or _DEFAULT_UNITS.get(field_name)
                incoming = FundamentalField(
                    field_name=field_name,
                    value=normalized_value,
                    unit=normalized_unit,
                    report_period=report_period,
                    source_time=source_time,
                    provider=result.spec.provider,
                    endpoint=result.spec.endpoint,
                    raw_payload_ref=raw_payload_ref,
                    payload_hash=payload_hash,
                    conflict_group=None,
                )
                existing = mapped.get(field_name)
                if existing is None:
                    mapped[field_name] = incoming
                    continue
                if ValuesConflict(existing, incoming):
                    conflict_diagnostics.append(
                        "field_conflict:"
                        f"{field_name}:kept={existing.provider}/{existing.endpoint}:{existing.value}"
                        f":incoming={incoming.provider}/{incoming.endpoint}:{incoming.value}"
                    )

    return mapped, conflict_diagnostics


def canonical_field_name(raw_field: Any) -> FundamentalFieldName | None:
    if isinstance(raw_field, Mapping):
        for key in ("field_name", "field_path", "name", "path"):
            canonical = canonical_field_name(raw_field.get(key))
            if canonical is not None:
                return canonical
        for key in raw_field.keys():
            canonical = canonical_field_name(key)
            if canonical is not None:
                return canonical
        return None
    if not isinstance(raw_field, str):
        return None
    normalized = _normalize_token(raw_field)
    if not normalized:
        return None
    return _CANONICAL_ALIASES.get(normalized)


def values_conflict(
    existing_field: FundamentalField,
    incoming: FundamentalField | Mapping[str, Any] | Any,
    incoming_unit: FundamentalUnit | None = None,
) -> bool:
    if isinstance(incoming, FundamentalField):
        candidate_value = incoming.value
        candidate_unit = incoming.unit
    elif isinstance(incoming, Mapping):
        candidate_value = incoming.get("value")
        candidate_unit = _normalize_unit(incoming.get("unit")) or incoming_unit
    else:
        candidate_value = incoming
        candidate_unit = incoming_unit

    if _normalize_unit(existing_field.unit) != _normalize_unit(candidate_unit):
        return True

    left_number = _as_number(existing_field.value)
    right_number = _as_number(candidate_value)
    if left_number is not None and right_number is not None:
        return not math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-9)

    left_text = _text_for_compare(existing_field.value)
    right_text = _text_for_compare(candidate_value)
    if left_text is None and right_text is None:
        return False
    return left_text != right_text


def compute_missing_core_fields(fields: Mapping[str, FundamentalField]) -> list[str]:
    missing: list[str] = []
    for field_name in _CORE_FIELD_ORDER:
        field = fields.get(field_name)
        if field is None or field.value in (None, ""):
            missing.append(field_name)
    return missing


def CanonicalFieldName(raw_field: Any) -> FundamentalFieldName | None:
    return canonical_field_name(raw_field)


def ValuesConflict(
    existing_field: FundamentalField,
    incoming: FundamentalField | Mapping[str, Any] | Any,
    incoming_unit: FundamentalUnit | None = None,
) -> bool:
    return values_conflict(existing_field, incoming, incoming_unit)


def _row_candidates(
    row: Mapping[str, Any],
) -> list[tuple[FundamentalFieldName, Any, FundamentalUnit | None]]:
    candidates: list[tuple[FundamentalFieldName, Any, FundamentalUnit | None]] = []
    unit_from_row = _normalize_unit(row.get("unit"))
    seen: set[str] = set()

    direct_name = canonical_field_name(row.get("field_name")) or canonical_field_name(row.get("field_path"))
    if direct_name is not None:
        candidates.append((direct_name, row.get("value"), unit_from_row))
        seen.add(direct_name)

    for key, value in row.items():
        if key in _ROW_META_KEYS:
            continue
        canonical = canonical_field_name(key)
        if canonical is None or canonical in seen:
            continue
        candidates.append((canonical, value, _DEFAULT_UNITS.get(canonical)))
        seen.add(canonical)

    return candidates


def _priority_rank(priority: str) -> int:
    return _PRIORITY_RANK.get(str(priority).upper(), 99)


def _normalize_token(value: str) -> str:
    return value.strip().replace(" ", "").lower()


def _as_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def _normalize_field_value(value: Any) -> str | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    number = _as_number(text)
    if number is not None:
        return number
    return text


def _normalize_unit(value: Any) -> FundamentalUnit | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = _normalize_token(value)
    return _UNIT_ALIASES.get(normalized)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _text_for_compare(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return str(value)


__all__ = [
    "CanonicalFieldName",
    "ValuesConflict",
    "canonical_field_name",
    "compute_missing_core_fields",
    "map_fundamental_fields",
    "values_conflict",
]
