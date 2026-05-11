from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pack_schema import DerivedSummaryItem


TEMPLATE_FINANCIAL_SNAPSHOT_OBTAINED = "financial_snapshot_obtained"
TEMPLATE_VALUATION_SNAPSHOT_OBTAINED = "valuation_snapshot_obtained"
TEMPLATE_MISSING_CORE_FIELD = "missing_core_field"
TEMPLATE_PROVIDER_CALL_FAILED = "provider_call_failed"
TEMPLATE_CROSS_SOURCE_CONFLICT = "cross_source_conflict"

TEMPLATE_WHITELIST = frozenset(
    {
        TEMPLATE_FINANCIAL_SNAPSHOT_OBTAINED,
        TEMPLATE_VALUATION_SNAPSHOT_OBTAINED,
        TEMPLATE_MISSING_CORE_FIELD,
        TEMPLATE_PROVIDER_CALL_FAILED,
        TEMPLATE_CROSS_SOURCE_CONFLICT,
    }
)

MAX_DERIVED_SUMMARY_ITEMS = 8

FORBIDDEN_CLAIM_WORDS = (
    "建议",
    "买入",
    "持有",
    "卖出",
    "观望",
    "目标价",
    "高估",
    "低估",
    "护城河",
    "行业龙头",
    "长期价值",
)

_FINANCIAL_SNAPSHOT_PRIORITY_FIELDS = (
    "financial_indicators.roe",
    "financial_indicators.roa",
    "financial_indicators.gross_margin",
    "financial_indicators.netprofit_margin",
    "financial_indicators.debt_to_assets",
    "income_statement.revenue",
    "income_statement.net_profit",
    "balance_sheet.total_assets",
    "balance_sheet.total_liabilities",
    "cash_flow.operating_cash_flow",
)
_VALUATION_PRIORITY_FIELDS = (
    "valuation.pe_ttm",
    "valuation.pb",
    "valuation.total_mv",
)


@dataclass(frozen=True)
class DerivedSummaryBuildResult:
    items: list[DerivedSummaryItem]
    valid: bool
    diagnostic_flags: list[dict[str, str]]


class DerivedSummaryBuilder:
    @classmethod
    def Build(cls, mapped: Any, diagnostics: Any) -> DerivedSummaryBuildResult:
        return cls().build(mapped, diagnostics)

    def build(self, mapped: Any, diagnostics: Any) -> DerivedSummaryBuildResult:
        field_sources = _extract_mapping(mapped, "field_sources")
        missing_fields = _extract_sequence(diagnostics, "missing_fields")
        diagnostic_flags = _extract_sequence(diagnostics, "diagnostic_flags")
        provider_attempts = _extract_provider_attempts(mapped, diagnostics)

        items: list[DerivedSummaryItem] = []
        financial_item = _build_financial_snapshot_item(field_sources)
        if financial_item is not None:
            items.append(financial_item)
        items.extend(_build_valuation_snapshot_items(field_sources))
        items.extend(_build_missing_core_field_items(missing_fields))
        items.extend(_build_provider_failed_items(provider_attempts))
        items.extend(_build_cross_source_conflict_items(diagnostic_flags))
        items = _dedupe_items(items)

        valid, invalid_reason = _validate_items(
            items=items,
            field_sources=field_sources,
            missing_fields=missing_fields,
            provider_attempts=provider_attempts,
            diagnostic_flags=diagnostic_flags,
        )
        if valid:
            return DerivedSummaryBuildResult(items=items, valid=True, diagnostic_flags=[])

        return DerivedSummaryBuildResult(
            items=[],
            valid=False,
            diagnostic_flags=[
                {
                    "code": "derived_summary_invalid",
                    "severity": "fail",
                    "reason": invalid_reason,
                }
            ],
        )


def contains_forbidden_claim_words(text: str) -> bool:
    return any(word in text for word in FORBIDDEN_CLAIM_WORDS)


def is_resolvable_source_ref(
    source_ref: str,
    *,
    field_sources: Mapping[str, Mapping[str, Any]],
    missing_fields: Sequence[Any],
    provider_attempts: Sequence[Any],
    diagnostic_flags: Sequence[Any],
) -> bool:
    if not isinstance(source_ref, str):
        return False
    source_ref_text = source_ref.strip()
    if source_ref_text == "":
        return False
    if source_ref_text.startswith("field_sources."):
        field_path = source_ref_text[len("field_sources.") :]
        return field_path in field_sources
    if source_ref_text.startswith("missing_fields["):
        index = _extract_index(source_ref_text, "missing_fields[")
        return index is not None and 0 <= index < len(missing_fields)
    if source_ref_text.startswith("provider_attempts["):
        index = _extract_index(source_ref_text, "provider_attempts[")
        return index is not None and 0 <= index < len(provider_attempts)
    if source_ref_text.startswith("diagnostic_flags["):
        index = _extract_index(source_ref_text, "diagnostic_flags[")
        return index is not None and 0 <= index < len(diagnostic_flags)
    return False


def _build_financial_snapshot_item(field_sources: Mapping[str, Mapping[str, Any]]) -> DerivedSummaryItem | None:
    selected_fields = [field_path for field_path in _FINANCIAL_SNAPSHOT_PRIORITY_FIELDS if field_path in field_sources]
    if len(selected_fields) == 0:
        return None
    selected_fields = selected_fields[:4]
    report_period = _resolve_report_period(field_sources, selected_fields)
    field_list = "、".join(_to_metric_name(field_path) for field_path in selected_fields)
    source_field_path = selected_fields[0]
    return DerivedSummaryItem(
        template_id=TEMPLATE_FINANCIAL_SNAPSHOT_OBTAINED,
        text=f"已取得{report_period}报告期的{field_list}。",
        source_ref=f"field_sources.{source_field_path}",
        inputs={
            "report_period": report_period,
            "field_list": field_list,
        },
    )


def _build_valuation_snapshot_items(field_sources: Mapping[str, Mapping[str, Any]]) -> list[DerivedSummaryItem]:
    items: list[DerivedSummaryItem] = []
    for field_path in _VALUATION_PRIORITY_FIELDS:
        payload = field_sources.get(field_path)
        if not isinstance(payload, Mapping):
            continue
        provider_api = _as_text(payload.get("api_name")) or _as_text(payload.get("provider")) or "unknown_provider"
        as_of_date = _as_text(payload.get("as_of")) or "unknown_date"
        metric_name = _to_metric_name(field_path)
        items.append(
            DerivedSummaryItem(
                template_id=TEMPLATE_VALUATION_SNAPSHOT_OBTAINED,
                text=f"{metric_name}来自{provider_api}，日期为{as_of_date}。",
                source_ref=f"field_sources.{field_path}",
                inputs={
                    "metric_name": metric_name,
                    "as_of_date": as_of_date,
                    "provider_api": provider_api,
                },
            )
        )
    return items


def _build_missing_core_field_items(missing_fields: Sequence[Any]) -> list[DerivedSummaryItem]:
    items: list[DerivedSummaryItem] = []
    for index, item in enumerate(missing_fields):
        if not isinstance(item, Mapping):
            continue
        if bool(item.get("is_core_field")) is False:
            continue
        field_name = _to_metric_name(_as_text(item.get("field_path")) or _as_text(item.get("field_name")) or "unknown_field")
        reason_code = _as_text(item.get("reason")) or _as_text(item.get("reason_code")) or "unknown_reason"
        items.append(
            DerivedSummaryItem(
                template_id=TEMPLATE_MISSING_CORE_FIELD,
                text=f"{field_name}未取得，原因：{reason_code}。",
                source_ref=f"missing_fields[{index}]",
                inputs={
                    "field_name": field_name,
                    "reason_code": reason_code,
                },
            )
        )
    return items


def _build_provider_failed_items(provider_attempts: Sequence[Any]) -> list[DerivedSummaryItem]:
    items: list[DerivedSummaryItem] = []
    for index, item in enumerate(provider_attempts):
        provider = _as_text(_value_from_attr_or_key(item, "provider")) or "unknown_provider"
        api_name = _as_text(_value_from_attr_or_key(item, "api_name")) or "unknown_api"
        status = _as_text(_value_from_attr_or_key(item, "status")) or "unknown_status"
        if status == "success":
            continue
        reason_code = _as_text(_value_from_attr_or_key(item, "reason")) or status
        items.append(
            DerivedSummaryItem(
                template_id=TEMPLATE_PROVIDER_CALL_FAILED,
                text=f"{provider}.{api_name}状态为{status}，原因：{reason_code}。",
                source_ref=f"provider_attempts[{index}]",
                inputs={
                    "provider": provider,
                    "api_name": api_name,
                    "status": status,
                    "reason_code": reason_code,
                },
            )
        )
    return items


def _build_cross_source_conflict_items(diagnostic_flags: Sequence[Any]) -> list[DerivedSummaryItem]:
    items: list[DerivedSummaryItem] = []
    for index, item in enumerate(diagnostic_flags):
        code = _as_text(_value_from_attr_or_key(item, "code"))
        if code != "cross_provider_conflict":
            continue
        field_name = _to_metric_name(
            _as_text(_value_from_attr_or_key(item, "field_path"))
            or _as_text(_value_from_attr_or_key(item, "field_name"))
            or "unknown_field"
        )
        provider_set = _as_text(_value_from_attr_or_key(item, "providers")) or "unknown_providers"
        items.append(
            DerivedSummaryItem(
                template_id=TEMPLATE_CROSS_SOURCE_CONFLICT,
                text=f"{field_name}存在跨源口径冲突，来源：{provider_set}。",
                source_ref=f"diagnostic_flags[{index}]",
                inputs={
                    "field_name": field_name,
                    "provider_set": provider_set,
                },
            )
        )
    return items


def _validate_items(
    *,
    items: Sequence[DerivedSummaryItem],
    field_sources: Mapping[str, Mapping[str, Any]],
    missing_fields: Sequence[Any],
    provider_attempts: Sequence[Any],
    diagnostic_flags: Sequence[Any],
) -> tuple[bool, str]:
    if len(items) > MAX_DERIVED_SUMMARY_ITEMS:
        return False, "item_limit_exceeded"
    for item in items:
        if item.template_id not in TEMPLATE_WHITELIST:
            return False, "template_not_allowed"
        if not is_resolvable_source_ref(
            item.source_ref,
            field_sources=field_sources,
            missing_fields=missing_fields,
            provider_attempts=provider_attempts,
            diagnostic_flags=diagnostic_flags,
        ):
            return False, "source_ref_unresolvable"
        if contains_forbidden_claim_words(item.text):
            return False, "forbidden_claim_word"
        for text in item.inputs.values():
            if contains_forbidden_claim_words(text):
                return False, "forbidden_claim_word"
    return True, ""


def _dedupe_items(items: Sequence[DerivedSummaryItem]) -> list[DerivedSummaryItem]:
    deduped: list[DerivedSummaryItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.template_id, item.source_ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _resolve_report_period(field_sources: Mapping[str, Mapping[str, Any]], field_paths: Sequence[str]) -> str:
    for field_path in field_paths:
        payload = field_sources.get(field_path)
        if not isinstance(payload, Mapping):
            continue
        report_period = _as_text(payload.get("report_period"))
        if report_period:
            return report_period
    for field_path in field_paths:
        payload = field_sources.get(field_path)
        if not isinstance(payload, Mapping):
            continue
        as_of = _as_text(payload.get("as_of"))
        if as_of:
            return as_of
    return "unknown_period"


def _to_metric_name(field_path: str) -> str:
    if "." not in field_path:
        return field_path
    return field_path.split(".")[-1]


def _extract_provider_attempts(mapped: Any, diagnostics: Any) -> list[Any]:
    attempts = _extract_sequence(diagnostics, "provider_attempts")
    if len(attempts) > 0:
        return attempts
    return _extract_sequence(mapped, "provider_attempts")


def _extract_mapping(value: Any, key: str) -> dict[str, Mapping[str, Any]]:
    target = _value_from_attr_or_key(value, key)
    if not isinstance(target, Mapping):
        return {}
    output: dict[str, Mapping[str, Any]] = {}
    for raw_key, payload in target.items():
        if not isinstance(raw_key, str):
            continue
        normalized = raw_key.strip()
        if normalized == "":
            continue
        if not isinstance(payload, Mapping):
            output[normalized] = {}
            continue
        output[normalized] = payload
    return output


def _extract_sequence(value: Any, key: str) -> list[Any]:
    target = _value_from_attr_or_key(value, key)
    if isinstance(target, Sequence) and not isinstance(target, (str, bytes, bytearray)):
        return list(target)
    return []


def _extract_index(source_ref: str, prefix: str) -> int | None:
    if not source_ref.startswith(prefix) or not source_ref.endswith("]"):
        return None
    index_text = source_ref[len(prefix) : -1]
    if index_text == "" or not index_text.isdigit():
        return None
    return int(index_text)


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text != "":
            return text
    return None


def _value_from_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


__all__ = [
    "DerivedSummaryBuildResult",
    "DerivedSummaryBuilder",
    "FORBIDDEN_CLAIM_WORDS",
    "MAX_DERIVED_SUMMARY_ITEMS",
    "TEMPLATE_CROSS_SOURCE_CONFLICT",
    "TEMPLATE_FINANCIAL_SNAPSHOT_OBTAINED",
    "TEMPLATE_MISSING_CORE_FIELD",
    "TEMPLATE_PROVIDER_CALL_FAILED",
    "TEMPLATE_VALUATION_SNAPSHOT_OBTAINED",
    "TEMPLATE_WHITELIST",
    "contains_forbidden_claim_words",
    "is_resolvable_source_ref",
]
