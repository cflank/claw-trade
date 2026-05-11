from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


_CORE_VALUATION_FIELDS = (
    "valuation.pe_ttm",
    "valuation.pb",
    "valuation.total_mv",
)
_CORE_INDICATOR_FIELDS = (
    "financial_indicators.roe",
    "financial_indicators.roa",
    "financial_indicators.gross_margin",
    "financial_indicators.netprofit_margin",
    "financial_indicators.debt_to_assets",
)
_CORE_FINANCIAL_DOMAINS = (
    "financial_indicators",
    "income_statement",
    "balance_sheet",
    "cash_flow",
)
_TREND_CONFLICT_DOMAINS = frozenset(
    {
        "valuation",
        "financial_indicators",
        "income_statement",
        "balance_sheet",
        "cash_flow",
    }
)

_STATUS_AVAILABLE = "available"
_STATUS_DEGRADED = "degraded"
_STATUS_BLOCKED = "blocked"

_BLOCK_REASON_FINANCIAL_SNAPSHOT = "core_financial_fields_incomplete"
_BLOCK_REASON_FINANCIAL_TREND = "financial_trend_not_ready"
_BLOCK_REASON_VALUATION = "valuation_core_fields_incomplete"
_BLOCK_REASON_COMPANY_PROFILE = "company_profile_missing"
_BLOCK_REASON_VALUATION_JUDGMENT = "data_service_never_decides_overvalued_or_undervalued"
_BLOCK_REASON_TARGET_PRICE = "data_service_never_supports_target_price"
_BLOCK_REASON_RATING = "data_service_never_supports_buy_hold_sell_rating"

_DOMAIN_TO_CAPABILITIES = {
    "company_profile": ("company_profile",),
    "financial_indicators": ("financial_snapshot", "financial_trend"),
    "income_statement": ("financial_snapshot", "financial_trend"),
    "balance_sheet": ("financial_snapshot", "financial_trend"),
    "cash_flow": ("financial_snapshot", "financial_trend"),
    "valuation": ("valuation_snapshot", "financial_trend"),
}

_STALE_DOMAIN_TO_CAPABILITIES = {
    "company_profile": ("company_profile",),
    "financial_indicators": ("financial_snapshot", "financial_trend"),
    "income_statement": ("financial_snapshot", "financial_trend"),
    "balance_sheet": ("financial_snapshot", "financial_trend"),
    "cash_flow": ("financial_snapshot", "financial_trend"),
    "valuation": ("valuation_snapshot",),
}


@dataclass
class _CapabilityState:
    status: str
    supported_fields: set[str] = field(default_factory=set)
    supported_periods: set[str] = field(default_factory=set)
    blocked_claims: set[str] = field(default_factory=set)
    blocked_reasons: set[str] = field(default_factory=set)
    degraded_reasons: set[str] = field(default_factory=set)
    reason: str = ""

    def block(self, reason: str) -> None:
        normalized = _normalize_text(reason) or "evidence_blocked"
        self.status = _STATUS_BLOCKED
        self.blocked_reasons.add(normalized)
        if self.reason == "":
            self.reason = normalized

    def degrade(self, reason: str) -> None:
        normalized = _normalize_text(reason) or "evidence_degraded"
        if self.status == _STATUS_AVAILABLE:
            self.status = _STATUS_DEGRADED
        self.degraded_reasons.add(normalized)


class EvidenceCapabilityClassifier:
    @classmethod
    def Classify(
        cls,
        mapped: Any,
        freshness: Mapping[str, Mapping[str, Any]] | None,
        diagnostics: Any,
    ) -> dict[str, dict[str, str | list[str]]]:
        return cls().classify(mapped, freshness, diagnostics)

    def classify(
        self,
        mapped: Any,
        freshness: Mapping[str, Mapping[str, Any]] | None,
        diagnostics: Any,
    ) -> dict[str, dict[str, str | list[str]]]:
        facts_by_path = _extract_facts_by_path(mapped)
        freshness_map = freshness if isinstance(freshness, Mapping) else {}
        hints = _extract_capability_hints(diagnostics)
        completeness = _extract_completeness(diagnostics)
        core_thresholds = _extract_core_thresholds(completeness)
        trend_observation = _extract_trend_observation(completeness)

        states: dict[str, _CapabilityState] = {
            "company_profile": _CapabilityState(status=_STATUS_AVAILABLE),
            "financial_snapshot": _CapabilityState(status=_hint_status(hints, "financial_snapshot")),
            "financial_trend": _CapabilityState(status=_hint_status(hints, "financial_trend")),
            "valuation_snapshot": _CapabilityState(status=_hint_status(hints, "valuation_snapshot")),
            "valuation_judgment": _CapabilityState(status=_STATUS_BLOCKED),
            "target_price": _CapabilityState(status=_STATUS_BLOCKED),
            "rating": _CapabilityState(status=_STATUS_BLOCKED),
        }

        states["company_profile"].blocked_claims.add("company_profile_claim")
        states["financial_snapshot"].blocked_claims.add("financial_snapshot_claim")
        states["financial_trend"].blocked_claims.add("financial_trend_claim")
        states["valuation_snapshot"].blocked_claims.add("valuation_snapshot_claim")
        states["valuation_judgment"].blocked_claims.add("overvalued_or_undervalued_judgment")
        states["target_price"].blocked_claims.add("target_price_statement")
        states["rating"].blocked_claims.add("buy_hold_sell_rating")

        company_fields = _collect_supported_fields(facts_by_path, "company_profile")
        financial_fields = set()
        for domain in _CORE_FINANCIAL_DOMAINS:
            financial_fields.update(_collect_supported_fields(facts_by_path, domain))
        valuation_fields = _collect_supported_fields(facts_by_path, "valuation")
        trend_periods = _collect_comparable_periods(mapped, facts_by_path)
        annual_periods = _collect_annual_periods(trend_periods)
        trend_conflicts = _collect_trend_conflicts(mapped, diagnostics)

        states["company_profile"].supported_fields.update(company_fields)
        states["financial_snapshot"].supported_fields.update(financial_fields)
        states["valuation_snapshot"].supported_fields.update(valuation_fields)
        states["financial_trend"].supported_periods.update(trend_periods)

        for capability in ("financial_snapshot", "financial_trend", "valuation_snapshot"):
            for reason in _hint_blocked_reasons(hints, capability):
                states[capability].block(reason)

        self._apply_mapped_impacts(states, mapped)
        self._apply_freshness_impact(states, freshness_map)
        self._apply_conflict_missing_fields(states, mapped, diagnostics)

        financial_snapshot_ready = _resolve_financial_snapshot_gate(core_thresholds, facts_by_path, freshness_map)
        valuation_snapshot_ready = _resolve_valuation_gate(core_thresholds, facts_by_path, freshness_map)
        trend_ready = _resolve_trend_gate(
            trend_observation=trend_observation,
            comparable_periods=trend_periods,
            annual_periods=annual_periods,
            conflict_fields=trend_conflicts,
        )

        if len(company_fields) == 0:
            states["company_profile"].block(_BLOCK_REASON_COMPANY_PROFILE)
        if not financial_snapshot_ready:
            states["financial_snapshot"].block(_BLOCK_REASON_FINANCIAL_SNAPSHOT)
        if not valuation_snapshot_ready:
            states["valuation_snapshot"].block(_BLOCK_REASON_VALUATION)
        if not trend_ready:
            trend_reason = _normalize_text(_mapping_get(trend_observation, "reason")) or _BLOCK_REASON_FINANCIAL_TREND
            states["financial_trend"].block(trend_reason)

        states["valuation_judgment"].block(_BLOCK_REASON_VALUATION_JUDGMENT)
        states["target_price"].block(_BLOCK_REASON_TARGET_PRICE)
        states["rating"].block(_BLOCK_REASON_RATING)

        return {
            "company_profile": _serialize_state(states["company_profile"]),
            "financial_snapshot": _serialize_state(states["financial_snapshot"]),
            "financial_trend": _serialize_state(states["financial_trend"]),
            "valuation_snapshot": _serialize_state(states["valuation_snapshot"]),
            "valuation_judgment": _serialize_state(states["valuation_judgment"]),
            "target_price": _serialize_state(states["target_price"]),
            "rating": _serialize_state(states["rating"]),
        }

    def _apply_mapped_impacts(self, states: dict[str, _CapabilityState], mapped: Any) -> None:
        impacts = _extract_sequence(mapped, "evidence_capability_impacts")
        for item in impacts:
            domain = _normalize_text(_mapping_get(item, "domain"))
            if domain is None:
                continue
            target_capabilities = _DOMAIN_TO_CAPABILITIES.get(domain, ())
            impact_status = _normalize_text(_mapping_get(item, "status")) or ""
            reason = _normalize_text(_mapping_get(item, "reason")) or "mapped_evidence_impact"
            is_core_impact = bool(_mapping_get(item, "is_core_impact"))
            for capability in target_capabilities:
                state = states[capability]
                if impact_status in {"unavailable", _STATUS_BLOCKED} or is_core_impact:
                    state.block(reason)
                elif impact_status in {"degraded", "partial"}:
                    state.degrade(reason)

    def _apply_freshness_impact(
        self,
        states: dict[str, _CapabilityState],
        freshness_map: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for field_path, freshness_item in freshness_map.items():
            if not isinstance(freshness_item, Mapping):
                continue
            if bool(freshness_item.get("is_stale")) is False:
                continue
            domain = _normalize_text(freshness_item.get("domain")) or _resolve_domain(field_path)
            reason = _normalize_text(freshness_item.get("reason")) or "stale"
            capabilities = _STALE_DOMAIN_TO_CAPABILITIES.get(domain, ())
            for capability in capabilities:
                state = states[capability]
                if capability == "valuation_snapshot" and field_path in _CORE_VALUATION_FIELDS:
                    state.block(reason)
                else:
                    state.degrade(reason)

    def _apply_conflict_missing_fields(self, states: dict[str, _CapabilityState], mapped: Any, diagnostics: Any) -> None:
        merged_missing_fields: list[Mapping[str, Any]] = []
        merged_missing_fields.extend(_extract_sequence(mapped, "missing_fields"))
        merged_missing_fields.extend(_extract_sequence(diagnostics, "missing_fields"))
        for item in merged_missing_fields:
            reason = _normalize_text(_mapping_get(item, "reason"))
            if reason != "cross_provider_conflict":
                continue
            field_path = _normalize_text(_mapping_get(item, "field_path"))
            domain = _resolve_domain(field_path or _normalize_text(_mapping_get(item, "domain")) or "")
            is_core = bool(_mapping_get(item, "is_core_field"))
            for capability in _DOMAIN_TO_CAPABILITIES.get(domain, ()):
                if is_core:
                    states[capability].block(reason)
                else:
                    states[capability].degrade(reason)


def _serialize_state(state: _CapabilityState) -> dict[str, str | list[str]]:
    return {
        "status": state.status,
        "supported_fields": sorted(state.supported_fields),
        "supported_periods": sorted(state.supported_periods),
        "blocked_claims": sorted(state.blocked_claims),
        "blocked_reasons": sorted(state.blocked_reasons),
        "degraded_reasons": sorted(state.degraded_reasons),
        "reason": state.reason,
    }


def _extract_capability_hints(diagnostics: Any) -> Mapping[str, Mapping[str, Any]]:
    raw = _value_from_attr_or_key(diagnostics, "capability_degradation_hints")
    if isinstance(raw, Mapping):
        return raw  # type: ignore[return-value]
    return {}


def _extract_completeness(diagnostics: Any) -> Any:
    return _value_from_attr_or_key(diagnostics, "completeness")


def _extract_core_thresholds(completeness: Any) -> Mapping[str, Any]:
    raw = _value_from_attr_or_key(completeness, "core_thresholds")
    if isinstance(raw, Mapping):
        return raw
    return {}


def _extract_trend_observation(completeness: Any) -> Mapping[str, Any]:
    raw = _value_from_attr_or_key(completeness, "trend_observation")
    if isinstance(raw, Mapping):
        return raw
    return {}


def _resolve_financial_snapshot_gate(
    core_thresholds: Mapping[str, Any],
    facts_by_path: Mapping[str, Any],
    freshness_map: Mapping[str, Mapping[str, Any]],
) -> bool:
    if all(key in core_thresholds for key in _CORE_FINANCIAL_DOMAINS):
        return all(bool(core_thresholds.get(key)) for key in _CORE_FINANCIAL_DOMAINS)

    indicator_count = 0
    for field_path in _CORE_INDICATOR_FIELDS:
        if _has_non_empty_fact(facts_by_path, field_path) and not _is_stale(freshness_map, field_path):
            indicator_count += 1
    if indicator_count < 3:
        return False
    for domain in ("income_statement", "balance_sheet", "cash_flow"):
        if len(_collect_supported_fields(facts_by_path, domain, freshness_map)) == 0:
            return False
    return True


def _resolve_valuation_gate(
    core_thresholds: Mapping[str, Any],
    facts_by_path: Mapping[str, Any],
    freshness_map: Mapping[str, Mapping[str, Any]],
) -> bool:
    if "valuation" in core_thresholds:
        return bool(core_thresholds.get("valuation"))
    valid = 0
    for field_path in _CORE_VALUATION_FIELDS:
        if _has_non_empty_fact(facts_by_path, field_path) and not _is_stale(freshness_map, field_path):
            valid += 1
    return valid >= 2


def _resolve_trend_gate(
    *,
    trend_observation: Mapping[str, Any],
    comparable_periods: Sequence[str],
    annual_periods: Sequence[str],
    conflict_fields: Sequence[str],
) -> bool:
    if bool(trend_observation.get("trend_ready")):
        return len(conflict_fields) == 0
    if len(comparable_periods) >= 2 and len(annual_periods) >= 2 and len(conflict_fields) == 0:
        return True
    return False


def _collect_supported_fields(
    facts_by_path: Mapping[str, Any],
    domain: str,
    freshness_map: Mapping[str, Mapping[str, Any]] | None = None,
) -> set[str]:
    result: set[str] = set()
    prefix = f"{domain}."
    for field_path, value in facts_by_path.items():
        if not (field_path == domain or field_path.startswith(prefix)):
            continue
        if not _is_non_empty_value(value):
            continue
        if freshness_map is not None and _is_stale(freshness_map, field_path):
            continue
        result.add(field_path)
    return result


def _collect_comparable_periods(mapped: Any, facts_by_path: Mapping[str, Any]) -> list[str]:
    periods: set[str] = set()
    for field_path, value in facts_by_path.items():
        if not (field_path.endswith(".period") or field_path.endswith(".end_date") or field_path.endswith(".ann_date")):
            continue
        normalized = _normalize_period(value)
        if normalized is not None:
            periods.add(normalized)
    mapped_periods = _value_from_attr_or_key(mapped, "periods")
    if isinstance(mapped_periods, Sequence) and not isinstance(mapped_periods, (str, bytes, bytearray)):
        for item in mapped_periods:
            normalized = _normalize_period(item)
            if normalized is not None:
                periods.add(normalized)
    return sorted(periods)


def _collect_annual_periods(comparable_periods: Sequence[str]) -> list[str]:
    annual = sorted({item[:4] for item in comparable_periods if len(item) == 8 and item.endswith("1231")})
    return annual


def _collect_trend_conflicts(mapped: Any, diagnostics: Any) -> list[str]:
    candidates: list[Mapping[str, Any]] = []
    candidates.extend(_extract_sequence(mapped, "missing_fields"))
    candidates.extend(_extract_sequence(diagnostics, "missing_fields"))
    conflicts: set[str] = set()
    for item in candidates:
        reason = _normalize_text(_mapping_get(item, "reason"))
        field_path = _normalize_text(_mapping_get(item, "field_path"))
        if reason != "cross_provider_conflict" or field_path is None:
            continue
        if _resolve_domain(field_path) not in _TREND_CONFLICT_DOMAINS:
            continue
        conflicts.add(field_path)
    return sorted(conflicts)


def _extract_sequence(source: Any, attr_name: str) -> list[Mapping[str, Any]]:
    raw = _value_from_attr_or_key(source, attr_name)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    result: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            result.append(item)
    return result


def _extract_facts_by_path(mapped: Any) -> dict[str, Any]:
    raw = _value_from_attr_or_key(mapped, "facts_by_path")
    if isinstance(raw, Mapping):
        return {str(key): raw[key] for key in raw.keys()}
    facts = _value_from_attr_or_key(mapped, "facts")
    if not isinstance(facts, Mapping):
        return {}
    flattened: dict[str, Any] = {}
    _flatten_mapping(facts, flattened, prefix="")
    return flattened


def _flatten_mapping(source: Mapping[str, Any], output: dict[str, Any], prefix: str) -> None:
    for key, value in source.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            _flatten_mapping(value, output, path)
            continue
        output[path] = value


def _hint_status(hints: Mapping[str, Mapping[str, Any]], capability: str) -> str:
    payload = hints.get(capability)
    if not isinstance(payload, Mapping):
        return _STATUS_AVAILABLE
    status = _normalize_text(payload.get("status"))
    if status in {_STATUS_AVAILABLE, _STATUS_DEGRADED, _STATUS_BLOCKED}:
        return status
    return _STATUS_AVAILABLE


def _hint_blocked_reasons(hints: Mapping[str, Mapping[str, Any]], capability: str) -> list[str]:
    payload = hints.get(capability)
    if not isinstance(payload, Mapping):
        return []
    raw_reasons = payload.get("blocked_reasons")
    if not isinstance(raw_reasons, Sequence) or isinstance(raw_reasons, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for reason in raw_reasons:
        normalized = _normalize_text(reason)
        if normalized is None:
            continue
        result.append(normalized)
    return result


def _has_non_empty_fact(facts_by_path: Mapping[str, Any], field_path: str) -> bool:
    return _is_non_empty_value(facts_by_path.get(field_path))


def _is_stale(freshness_map: Mapping[str, Mapping[str, Any]], field_path: str) -> bool:
    payload = freshness_map.get(field_path)
    if not isinstance(payload, Mapping):
        return False
    return bool(payload.get("is_stale"))


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


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    return normalized


def _normalize_period(value: Any) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    if len(digits) == 4:
        return f"{digits}1231"
    return None


def _resolve_domain(field_path: str) -> str:
    if "." not in field_path:
        return field_path
    return field_path.split(".", 1)[0]


def _value_from_attr_or_key(source: Any, name: str) -> Any:
    value = getattr(source, name, None)
    if value is not None:
        return value
    if isinstance(source, Mapping):
        return source.get(name)
    return None


def _mapping_get(source: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return None


__all__ = ["EvidenceCapabilityClassifier"]
