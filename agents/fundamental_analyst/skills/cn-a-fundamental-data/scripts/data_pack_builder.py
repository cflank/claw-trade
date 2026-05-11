from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from models import NormalizedInput
from pack_schema import (
    BalanceSheetFacts,
    CashFlowFacts,
    CompanyProfileFacts,
    DataPack,
    DataPackEvidence,
    DataPackQuality,
    DerivedSummaryItem,
    Facts,
    FinancialIndicatorsFacts,
    IncomeStatementFacts,
    ProfileInfo,
    PriceContextFacts,
    QueryInfo,
    ValuationFacts,
)

_REQUIRED_TOP_LEVEL_SECTIONS = (
    "profile",
    "query",
    "facts",
    "field_sources",
    "provider_attempts",
    "missing_fields",
    "freshness",
    "evidence_capabilities",
    "diagnostic_flags",
    "derived_summary",
    "quality",
    "evidence",
    "schema_version",
)


class DataPackBuilder:
    @classmethod
    def Build(
        cls,
        *,
        normalized_input: NormalizedInput,
        mapped: Any,
        freshness: Any,
        diagnostics: Any,
        evidence_capabilities: Any,
        derived_summary_result: Any,
        provider_results: Sequence[Any] | None,
        cache_result: Any,
    ) -> DataPack:
        return cls().build(
            normalized_input=normalized_input,
            mapped=mapped,
            freshness=freshness,
            diagnostics=diagnostics,
            evidence_capabilities=evidence_capabilities,
            derived_summary_result=derived_summary_result,
            provider_results=provider_results,
            cache_result=cache_result,
        )

    def build(
        self,
        *,
        normalized_input: NormalizedInput,
        mapped: Any,
        freshness: Any,
        diagnostics: Any,
        evidence_capabilities: Any,
        derived_summary_result: Any,
        provider_results: Sequence[Any] | None,
        cache_result: Any,
    ) -> DataPack:
        facts_by_path = _extract_facts_by_path(mapped)
        field_sources = _extract_mapping(mapped, "field_sources")
        missing_fields = _extract_list(diagnostics, "missing_fields")
        diagnostic_flags = _extract_list(diagnostics, "diagnostic_flags")
        attempts = _collect_provider_attempts(cache_result, provider_results, diagnostics)

        quality_payload = _extract_quality_payload(diagnostics)
        status = _as_text(quality_payload.get("status")) or "failed"
        ok = _extract_ok(diagnostics, status)

        derived_summary_items, derived_summary_valid, derived_summary_flags = _extract_derived_summary(derived_summary_result)
        if not derived_summary_valid:
            derived_summary_items = []
            diagnostic_flags.extend(derived_summary_flags)
            _append_fail_flag_once(
                diagnostic_flags,
                code="derived_summary_invalid",
                reason=_first_non_empty(
                    [_as_text(item.get("reason")) for item in derived_summary_flags if isinstance(item, Mapping)]
                )
                or "derived_summary_invalid",
            )
            status = "failed"
            ok = False

        missing_source_paths = _find_missing_source_paths(facts_by_path, field_sources)
        if missing_source_paths:
            for field_path in missing_source_paths:
                missing_fields.append(
                    {
                        "field_path": field_path,
                        "reason": "field_source_missing",
                        "domain": _resolve_domain(field_path),
                        "is_core_field": True,
                    }
                )
                diagnostic_flags.append(
                    {
                        "code": "field_source_missing",
                        "field_path": field_path,
                        "domain": _resolve_domain(field_path),
                        "severity": "fail",
                        "detail": "fact_has_no_field_source",
                    }
                )
            status = "failed"
            ok = False

        legal_combo = _is_legal_ok_status(ok=ok, status=status)
        if not legal_combo:
            _append_fail_flag_once(diagnostic_flags, code="invalid_quality_combo", reason=f"ok={ok},status={status}")
            status = "failed"
            ok = False

        warnings = _normalize_warnings(
            quality_payload.get("warnings"),
            diagnostic_flags=diagnostic_flags,
        )
        if status == "failed":
            warnings = sorted(set(warnings))

        facts_payload = _extract_mapping(mapped, "facts")
        facts = _build_facts(facts_payload, facts_by_path)
        profile = ProfileInfo(
            ticker=normalized_input.raw_ticker,
            canonical_code=normalized_input.canonical_code,
            market=normalized_input.market,
            company_name=_resolve_company_name(facts_payload, facts_by_path),
        )
        query = QueryInfo(
            start_date=normalized_input.start_date,
            end_date=normalized_input.end_date,
            current_date=normalized_input.current_date,
        )
        quality = DataPackQuality(
            status=status,
            is_partial=status == "partial",
            warnings=warnings,
        )
        evidence = DataPackEvidence(
            raw_payload_refs=_collect_raw_payload_refs(attempts, field_sources),
            content_hash="",
        )
        pack = DataPack(
            ok=ok,
            profile=profile,
            query=query,
            facts=facts,
            field_sources=field_sources,
            provider_attempts=list(attempts),
            missing_fields=_dedupe_items(missing_fields),
            freshness=_extract_freshness_map(freshness),
            evidence_capabilities=_extract_evidence_capabilities(evidence_capabilities),
            diagnostic_flags=_dedupe_items(diagnostic_flags),
            derived_summary=derived_summary_items,
            quality=quality,
            evidence=evidence,
        )

        payload_without_hash = pack.to_dict()
        _assert_required_top_level_sections(payload_without_hash)
        content_hash = _compute_content_hash(payload_without_hash)
        return DataPack(
            ok=pack.ok,
            profile=pack.profile,
            query=pack.query,
            facts=pack.facts,
            field_sources=pack.field_sources,
            provider_attempts=pack.provider_attempts,
            missing_fields=pack.missing_fields,
            freshness=pack.freshness,
            evidence_capabilities=pack.evidence_capabilities,
            diagnostic_flags=pack.diagnostic_flags,
            derived_summary=pack.derived_summary,
            quality=pack.quality,
            evidence=DataPackEvidence(
                raw_payload_refs=pack.evidence.raw_payload_refs,
                content_hash=content_hash,
            ),
        )


def _extract_derived_summary(result: Any) -> tuple[list[DerivedSummaryItem], bool, list[dict[str, str]]]:
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        items = [_coerce_derived_summary_item(item) for item in result]
        return items, True, []

    valid = bool(_value_from_attr_or_key(result, "valid"))
    raw_items = _value_from_attr_or_key(result, "items")
    raw_flags = _value_from_attr_or_key(result, "diagnostic_flags")
    items: list[DerivedSummaryItem] = []
    if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes, bytearray)):
        items = [_coerce_derived_summary_item(item) for item in raw_items]
    flags = []
    if isinstance(raw_flags, Sequence) and not isinstance(raw_flags, (str, bytes, bytearray)):
        for item in raw_flags:
            if isinstance(item, Mapping):
                flags.append({str(key): _stringify(item.get(key)) for key in item.keys()})
    return items, valid, flags


def _coerce_derived_summary_item(item: Any) -> DerivedSummaryItem:
    if isinstance(item, DerivedSummaryItem):
        return item
    if is_dataclass(item):
        item = asdict(item)
    if isinstance(item, Mapping):
        inputs = item.get("inputs")
        normalized_inputs: dict[str, str] = {}
        if isinstance(inputs, Mapping):
            normalized_inputs = {str(key): _stringify(inputs.get(key)) for key in inputs.keys()}
        return DerivedSummaryItem(
            template_id=_as_text(item.get("template_id")) or "",
            text=_as_text(item.get("text")) or "",
            source_ref=_as_text(item.get("source_ref")) or "",
            inputs=normalized_inputs,
        )
    raise ValueError("invalid derived summary item")


def _extract_mapping(source: Any, key: str) -> dict[str, Any]:
    value = _value_from_attr_or_key(source, key)
    if isinstance(value, Mapping):
        return {str(item_key): value[item_key] for item_key in value.keys()}
    return {}


def _extract_list(source: Any, key: str) -> list[dict[str, Any]]:
    value = _value_from_attr_or_key(source, key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if is_dataclass(item):
            item = asdict(item)
        if isinstance(item, Mapping):
            result.append({str(item_key): item[item_key] for item_key in item.keys()})
    return result


def _extract_facts_by_path(mapped: Any) -> dict[str, Any]:
    facts_by_path = _extract_mapping(mapped, "facts_by_path")
    facts_payload = _extract_mapping(mapped, "facts")
    collected: dict[str, Any] = dict(facts_by_path)
    _flatten_facts("", facts_payload, collected)
    return collected


def _flatten_facts(prefix: str, node: Any, output: dict[str, Any]) -> None:
    if not isinstance(node, Mapping):
        return
    for key, value in node.items():
        if not isinstance(key, str):
            continue
        field_path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            _flatten_facts(field_path, value, output)
            continue
        output[field_path] = value


def _find_missing_source_paths(
    facts_by_path: Mapping[str, Any],
    field_sources: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    missing: list[str] = []
    for field_path in sorted(facts_by_path.keys()):
        value = facts_by_path[field_path]
        if not _is_non_empty_value(value):
            continue
        if field_path not in field_sources:
            missing.append(field_path)
    return missing


def _extract_quality_payload(diagnostics: Any) -> dict[str, Any]:
    value = _value_from_attr_or_key(diagnostics, "quality")
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(key): value[key] for key in value.keys()}
    return {"status": "failed", "is_partial": False, "warnings": []}


def _extract_ok(diagnostics: Any, status: str) -> bool:
    raw_ok = _value_from_attr_or_key(diagnostics, "ok")
    if isinstance(raw_ok, bool):
        return raw_ok
    return status in {"ok", "partial"}


def _is_legal_ok_status(*, ok: bool, status: str) -> bool:
    if ok is False and status == "failed":
        return True
    if ok is True and status == "partial":
        return True
    if ok is True and status == "ok":
        return True
    return False


def _build_facts(facts_payload: Mapping[str, Any], facts_by_path: Mapping[str, Any]) -> Facts:
    company_profile_payload = _mapping_value(facts_payload, "company_profile")
    price_context_payload = _mapping_value(facts_payload, "price_context")
    valuation_payload = _mapping_value(facts_payload, "valuation")
    indicators_payload = _mapping_value(facts_payload, "financial_indicators")
    income_payload = _mapping_value(facts_payload, "income_statement")
    balance_payload = _mapping_value(facts_payload, "balance_sheet")
    cash_payload = _mapping_value(facts_payload, "cash_flow")

    business_segments = _list_of_dicts(_value_from_attr_or_key(facts_payload, "business_segments"))
    dividend = _list_of_dicts(_value_from_attr_or_key(facts_payload, "dividend"))
    shareholders_raw = _value_from_attr_or_key(facts_payload, "shareholders")
    shareholders = _dict_of_list_of_dicts(shareholders_raw)

    if not company_profile_payload:
        company_profile_payload = _mapping_from_path_prefix(facts_by_path, "company_profile.")
    if not price_context_payload:
        price_context_payload = _mapping_from_path_prefix(facts_by_path, "price_context.")
    if not valuation_payload:
        valuation_payload = _mapping_from_path_prefix(facts_by_path, "valuation.")
    if not indicators_payload:
        indicators_payload = _mapping_from_path_prefix(facts_by_path, "financial_indicators.")
    if not income_payload:
        income_payload = _mapping_from_path_prefix(facts_by_path, "income_statement.")
    if not balance_payload:
        balance_payload = _mapping_from_path_prefix(facts_by_path, "balance_sheet.")
    if not cash_payload:
        cash_payload = _mapping_from_path_prefix(facts_by_path, "cash_flow.")

    return Facts(
        company_profile=CompanyProfileFacts(
            name=_to_text_or_none(_value_from_attr_or_key(company_profile_payload, "name")),
            industry=_to_text_or_none(_value_from_attr_or_key(company_profile_payload, "industry")),
            main_business=_to_text_or_none(_value_from_attr_or_key(company_profile_payload, "main_business")),
        ),
        price_context=PriceContextFacts(
            close=_to_float_or_none(_value_from_attr_or_key(price_context_payload, "close")),
            trade_date=_to_text_or_none(_value_from_attr_or_key(price_context_payload, "trade_date")),
            volume=_to_float_or_none(_value_from_attr_or_key(price_context_payload, "volume")),
        ),
        valuation=ValuationFacts(
            pe_ttm=_to_float_or_none(_value_from_attr_or_key(valuation_payload, "pe_ttm")),
            pb=_to_float_or_none(_value_from_attr_or_key(valuation_payload, "pb")),
            total_mv=_to_float_or_none(_value_from_attr_or_key(valuation_payload, "total_mv")),
        ),
        financial_indicators=FinancialIndicatorsFacts(
            roe=_to_float_or_none(_value_from_attr_or_key(indicators_payload, "roe")),
            roa=_to_float_or_none(_value_from_attr_or_key(indicators_payload, "roa")),
            gross_margin=_to_float_or_none(_value_from_attr_or_key(indicators_payload, "gross_margin")),
            netprofit_margin=_to_float_or_none(_value_from_attr_or_key(indicators_payload, "netprofit_margin")),
            debt_to_assets=_to_float_or_none(_value_from_attr_or_key(indicators_payload, "debt_to_assets")),
        ),
        income_statement=IncomeStatementFacts(
            revenue=_to_float_or_none(_value_from_attr_or_key(income_payload, "revenue")),
            net_profit=_to_float_or_none(_value_from_attr_or_key(income_payload, "net_profit")),
            eps=_to_float_or_none(_value_from_attr_or_key(income_payload, "eps")),
        ),
        balance_sheet=BalanceSheetFacts(
            total_assets=_to_float_or_none(_value_from_attr_or_key(balance_payload, "total_assets")),
            total_liabilities=_to_float_or_none(_value_from_attr_or_key(balance_payload, "total_liabilities")),
            total_equity=_to_float_or_none(_value_from_attr_or_key(balance_payload, "total_equity")),
        ),
        cash_flow=CashFlowFacts(
            operating_cash_flow=_to_float_or_none(_value_from_attr_or_key(cash_payload, "operating_cash_flow")),
        ),
        business_segments=business_segments,
        dividend=dividend,
        shareholders=shareholders,
    )


def _resolve_company_name(facts_payload: Mapping[str, Any], facts_by_path: Mapping[str, Any]) -> str | None:
    company_profile = _mapping_value(facts_payload, "company_profile")
    company_name = _as_text(_value_from_attr_or_key(company_profile, "name"))
    if company_name is not None:
        return company_name
    return _as_text(facts_by_path.get("company_profile.name"))


def _extract_freshness_map(freshness: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(freshness, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key in sorted(freshness.keys(), key=lambda item: str(item)):
        path = str(key)
        payload = freshness[key]
        if not isinstance(payload, Mapping):
            continue
        result[path] = {str(item_key): payload[item_key] for item_key in payload.keys()}
    return result


def _extract_evidence_capabilities(value: Any) -> dict[str, dict[str, str | list[str]]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, dict[str, str | list[str]]] = {}
    for key in sorted(value.keys(), key=lambda item: str(item)):
        capability = value[key]
        if not isinstance(capability, Mapping):
            continue
        normalized_capability: dict[str, str | list[str]] = {}
        for item_key in capability.keys():
            raw = capability[item_key]
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                normalized_capability[str(item_key)] = sorted(_stringify(item) for item in raw)
            else:
                normalized_capability[str(item_key)] = _stringify(raw)
        result[str(key)] = normalized_capability
    return result


def _collect_provider_attempts(
    cache_result: Any,
    provider_results: Sequence[Any] | None,
    diagnostics: Any,
) -> list[Any]:
    attempts: list[Any] = []

    cache_attempt = _value_from_attr_or_key(cache_result, "attempt")
    if _looks_like_attempt(cache_attempt):
        attempts.append(cache_attempt)

    if provider_results is not None:
        for result in provider_results:
            attempt = _value_from_attr_or_key(result, "attempt")
            if _looks_like_attempt(attempt):
                attempts.append(attempt)
            elif _looks_like_attempt(result):
                attempts.append(result)

    if not attempts:
        diagnostics_attempts = _value_from_attr_or_key(diagnostics, "provider_attempts")
        if isinstance(diagnostics_attempts, Sequence) and not isinstance(diagnostics_attempts, (str, bytes, bytearray)):
            for item in diagnostics_attempts:
                if _looks_like_attempt(item):
                    attempts.append(item)

    return attempts


def _collect_raw_payload_refs(attempts: Sequence[Any], field_sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    refs: set[str] = set()

    for attempt in attempts:
        raw_ref = _as_text(_value_from_attr_or_key(attempt, "raw_payload_ref"))
        if raw_ref:
            refs.add(raw_ref)

    for source in field_sources.values():
        if not isinstance(source, Mapping):
            continue
        raw_ref = _as_text(source.get("raw_payload_ref"))
        if raw_ref:
            refs.add(raw_ref)
        supporting_sources = source.get("supporting_sources")
        if isinstance(supporting_sources, Sequence) and not isinstance(supporting_sources, (str, bytes, bytearray)):
            for item in supporting_sources:
                if isinstance(item, Mapping):
                    candidate = _as_text(item.get("raw_payload_ref"))
                    if candidate:
                        refs.add(candidate)

    return sorted(refs)


def _assert_required_top_level_sections(payload: Mapping[str, Any]) -> None:
    for key in _REQUIRED_TOP_LEVEL_SECTIONS:
        if key not in payload:
            raise ValueError(f"missing top-level section: {key}")


def _compute_content_hash(payload_without_hash: Mapping[str, Any]) -> str:
    evidence = payload_without_hash.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("payload.evidence missing")

    patched = dict(payload_without_hash)
    patched["evidence"] = dict(evidence)
    patched["evidence"]["content_hash"] = ""

    canonical = json.dumps(
        patched,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _normalize_warnings(raw_warnings: Any, *, diagnostic_flags: Sequence[Mapping[str, Any]]) -> list[str]:
    warnings: set[str] = set()
    if isinstance(raw_warnings, Sequence) and not isinstance(raw_warnings, (str, bytes, bytearray)):
        for item in raw_warnings:
            text = _as_text(item)
            if text:
                warnings.add(text)

    for item in diagnostic_flags:
        severity = _as_text(item.get("severity"))
        code = _as_text(item.get("code"))
        if code is None:
            continue
        if severity == "fail":
            continue
        warnings.add(code)

    return sorted(warnings)


def _append_fail_flag_once(flags: list[dict[str, Any]], *, code: str, reason: str) -> None:
    for item in flags:
        if _as_text(item.get("code")) == code:
            return
    flags.append(
        {
            "code": code,
            "severity": "fail",
            "reason": reason,
        }
    )


def _mapping_value(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = _value_from_attr_or_key(mapping, key)
    if isinstance(value, Mapping):
        return {str(item_key): value[item_key] for item_key in value.keys()}
    return {}


def _mapping_from_path_prefix(facts_by_path: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field_path, value in facts_by_path.items():
        if not isinstance(field_path, str):
            continue
        if not field_path.startswith(prefix):
            continue
        result[field_path[len(prefix) :]] = value
    return result


def _list_of_dicts(value: Any) -> list[dict[str, str | float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, str | float]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        normalized: dict[str, str | float] = {}
        for key, raw in item.items():
            parsed = _to_str_or_float(raw)
            if parsed is not None:
                normalized[str(key)] = parsed
        if normalized:
            result.append(normalized)
    return result


def _dict_of_list_of_dicts(value: Any) -> dict[str, list[dict[str, str | float]]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, list[dict[str, str | float]]] = {}
    for key, items in value.items():
        normalized = _list_of_dicts(items)
        if normalized:
            result[str(key)] = normalized
    return result


def _to_float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_str_or_float(value: Any) -> str | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        return text
    return None


def _to_text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


def _looks_like_attempt(value: Any) -> bool:
    if value is None:
        return False
    provider = _value_from_attr_or_key(value, "provider")
    status = _value_from_attr_or_key(value, "status")
    return _as_text(provider) is not None and _as_text(status) is not None


def _dedupe_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        normalized = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def _resolve_domain(field_path: str) -> str:
    if "." not in field_path:
        return field_path
    return field_path.split(".", 1)[0]


def _value_from_attr_or_key(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _is_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, Mapping):
        return len(value) > 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value) > 0
    return True


def _first_non_empty(values: Sequence[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None
