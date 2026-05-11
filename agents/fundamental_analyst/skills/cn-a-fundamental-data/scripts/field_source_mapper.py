from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from cache import CacheInspectionResult
from models import ProviderResult


_RATIO_UNITS = frozenset({"x", "ratio"})
_PERCENT_UNITS = frozenset({"%", "percent"})
_CORE_CONFLICT_FIELDS = frozenset(
    {
        "valuation.pe_ttm",
        "valuation.pb",
        "financial_indicators.roe",
        "income_statement.net_profit",
        "cash_flow.operating_cash_flow",
    }
)


@dataclass(frozen=True)
class FieldSourceCandidate:
    field_path: str
    value: Any
    provider: str | None
    api_name: str | None
    payload_hash: str | None
    raw_payload_ref: str | None
    source_ref_path: str
    as_of: str | None
    fetched_at: str | None
    unit: str | None
    scale: str | None
    origin: str


@dataclass(frozen=True)
class FieldSourceMapperResult:
    facts: dict[str, Any]
    facts_by_path: dict[str, Any]
    field_sources: dict[str, dict[str, Any]]
    missing_fields: list[dict[str, str | bool | list[str]]]
    diagnostic_flags: list[dict[str, str]]
    evidence_capability_impacts: list[dict[str, str | bool | list[str]]]


class FieldSourceMapper:
    def map(self, cache_result: CacheInspectionResult, provider_results: list[ProviderResult]) -> FieldSourceMapperResult:
        return self.Map(cache_result, provider_results)

    def Map(self, cache_result: CacheInspectionResult, provider_results: list[ProviderResult]) -> FieldSourceMapperResult:
        grouped: dict[str, list[FieldSourceCandidate]] = {}
        self._collect_cache_candidates(grouped, cache_result.reusable_fields)
        self._collect_provider_candidates(grouped, provider_results)

        facts_by_path: dict[str, Any] = {}
        field_sources: dict[str, dict[str, Any]] = {}
        missing_fields: list[dict[str, str | bool | list[str]]] = []
        diagnostic_flags: list[dict[str, str]] = []
        impact_accumulator: dict[str, dict[str, Any]] = {}

        for field_path in sorted(grouped.keys()):
            candidates = grouped[field_path]
            if len(candidates) == 0:
                continue
            if self._all_equivalent(candidates):
                chosen = self._choose_best_candidate(candidates)
                facts_by_path[field_path] = chosen.value
                field_sources[field_path] = self._build_field_source(chosen, candidates)
                continue

            providers = sorted(
                {
                    candidate.provider
                    for candidate in candidates
                    if isinstance(candidate.provider, str) and candidate.provider.strip() != ""
                }
            )
            domain = _resolve_domain(field_path)
            is_core = field_path in _CORE_CONFLICT_FIELDS
            missing_fields.append(
                {
                    "field_path": field_path,
                    "reason": "cross_provider_conflict",
                    "domain": domain,
                    "is_core_field": is_core,
                    "provider_set": providers,
                }
            )
            diagnostic_flags.append(
                {
                    "code": "cross_provider_conflict",
                    "field_path": field_path,
                    "domain": domain,
                    "severity": "fail" if is_core else "warn",
                    "providers": ",".join(providers),
                }
            )
            impact = impact_accumulator.setdefault(
                domain,
                {
                    "domain": domain,
                    "capability": f"{domain}_evidence",
                    "status": "degraded",
                    "reason": "cross_provider_conflict",
                    "affected_fields": set(),
                    "is_core_impact": False,
                },
            )
            impact["affected_fields"].add(field_path)
            if is_core:
                impact["status"] = "unavailable"
                impact["is_core_impact"] = True

        facts = _build_nested_facts(facts_by_path)
        evidence_capability_impacts = self._finalize_impacts(impact_accumulator)
        return FieldSourceMapperResult(
            facts=facts,
            facts_by_path=facts_by_path,
            field_sources=field_sources,
            missing_fields=missing_fields,
            diagnostic_flags=diagnostic_flags,
            evidence_capability_impacts=evidence_capability_impacts,
        )

    def _collect_cache_candidates(
        self,
        grouped: dict[str, list[FieldSourceCandidate]],
        reusable_fields: Mapping[str, dict[str, Any]],
    ) -> None:
        for field_path, payload in reusable_fields.items():
            if not isinstance(field_path, str):
                continue
            path = field_path.strip()
            if path == "":
                continue
            if not isinstance(payload, Mapping):
                continue
            value = payload.get("value")
            if value is None:
                continue
            candidate = FieldSourceCandidate(
                field_path=path,
                value=value,
                provider=_as_text(payload.get("provider")) or "mongodb_cache",
                api_name=_as_text(payload.get("api_name")),
                payload_hash=_as_text(payload.get("payload_hash")),
                raw_payload_ref=_as_text(payload.get("raw_payload_ref")),
                source_ref_path=_as_text(payload.get("source_ref_path")) or f"field_sources.{path}",
                as_of=_as_text(payload.get("as_of")),
                fetched_at=_as_text(payload.get("fetched_at")),
                unit=_as_text(payload.get("unit")),
                scale=_as_text(payload.get("scale")),
                origin="cache",
            )
            grouped.setdefault(path, []).append(candidate)

    def _collect_provider_candidates(
        self,
        grouped: dict[str, list[FieldSourceCandidate]],
        provider_results: Iterable[ProviderResult],
    ) -> None:
        for provider_result in provider_results:
            attempt = provider_result.attempt
            if attempt.status != "success":
                continue
            for item in provider_result.extracted_fields:
                if not isinstance(item, tuple) or len(item) != 3:
                    continue
                field_path, value_payload, source_ref_path = item
                if not isinstance(field_path, str):
                    continue
                path = field_path.strip()
                if path == "":
                    continue
                source_ref = source_ref_path if isinstance(source_ref_path, str) and source_ref_path.strip() else f"field_sources.{path}"
                value = value_payload
                unit = None
                scale = None
                provider = attempt.provider
                api_name = attempt.api_name
                payload_hash = provider_result.raw_payload_hash or attempt.raw_payload_hash
                raw_payload_ref = provider_result.raw_payload_ref or attempt.raw_payload_ref
                as_of = attempt.as_of
                fetched_at = attempt.fetched_at
                if isinstance(value_payload, Mapping):
                    value = value_payload.get("value")
                    unit = _as_text(value_payload.get("unit"))
                    scale = _as_text(value_payload.get("scale"))
                    provider = _as_text(value_payload.get("provider")) or provider
                    api_name = _as_text(value_payload.get("api_name")) or api_name
                    payload_hash = _as_text(value_payload.get("payload_hash")) or payload_hash
                    raw_payload_ref = _as_text(value_payload.get("raw_payload_ref")) or raw_payload_ref
                    as_of = _as_text(value_payload.get("as_of")) or as_of
                    fetched_at = _as_text(value_payload.get("fetched_at")) or fetched_at
                if value is None:
                    continue
                candidate = FieldSourceCandidate(
                    field_path=path,
                    value=value,
                    provider=provider,
                    api_name=api_name,
                    payload_hash=_as_text(payload_hash),
                    raw_payload_ref=_as_text(raw_payload_ref),
                    source_ref_path=source_ref,
                    as_of=_as_text(as_of),
                    fetched_at=_as_text(fetched_at),
                    unit=unit,
                    scale=scale,
                    origin="provider",
                )
                grouped.setdefault(path, []).append(candidate)

    def _all_equivalent(self, candidates: list[FieldSourceCandidate]) -> bool:
        baseline = candidates[0]
        for candidate in candidates[1:]:
            if not self._candidates_equivalent(baseline, candidate):
                return False
        return True

    def _candidates_equivalent(self, left: FieldSourceCandidate, right: FieldSourceCandidate) -> bool:
        left_numeric = _to_numeric_with_dimension(left.value, left.unit, left.scale)
        right_numeric = _to_numeric_with_dimension(right.value, right.unit, right.scale)
        if left_numeric is not None and right_numeric is not None:
            if left_numeric[0] != right_numeric[0]:
                return False
            return abs(left_numeric[1] - right_numeric[1]) <= 1e-9

        left_text = _as_text(left.value)
        right_text = _as_text(right.value)
        if left_text is not None and right_text is not None:
            return left_text == right_text

        return left.value == right.value

    def _choose_best_candidate(self, candidates: list[FieldSourceCandidate]) -> FieldSourceCandidate:
        return max(candidates, key=self._candidate_sort_key)

    def _candidate_sort_key(self, candidate: FieldSourceCandidate) -> tuple[int, int, int]:
        fetched_rank = _timestamp_rank(candidate.fetched_at)
        as_of_rank = _timestamp_rank(candidate.as_of)
        provider_rank = _provider_rank(candidate.provider)
        return (fetched_rank, as_of_rank, provider_rank)

    def _build_field_source(
        self,
        chosen: FieldSourceCandidate,
        candidates: list[FieldSourceCandidate],
    ) -> dict[str, Any]:
        refs = sorted(
            {
                candidate.source_ref_path
                for candidate in candidates
                if isinstance(candidate.source_ref_path, str) and candidate.source_ref_path.strip() != ""
            }
        )
        sources = [
            {
                "provider": candidate.provider,
                "api_name": candidate.api_name,
                "payload_hash": candidate.payload_hash,
                "raw_payload_ref": candidate.raw_payload_ref,
                "source_ref_path": candidate.source_ref_path,
                "as_of": candidate.as_of,
                "fetched_at": candidate.fetched_at,
                "unit": candidate.unit,
                "scale": candidate.scale,
                "value": candidate.value,
                "origin": candidate.origin,
            }
            for candidate in sorted(candidates, key=self._candidate_sort_key, reverse=True)
        ]
        return {
            "provider": chosen.provider,
            "api_name": chosen.api_name,
            "payload_hash": chosen.payload_hash,
            "raw_payload_ref": chosen.raw_payload_ref,
            "source_ref_path": chosen.source_ref_path,
            "as_of": chosen.as_of,
            "fetched_at": chosen.fetched_at,
            "unit": chosen.unit,
            "scale": chosen.scale,
            "value": chosen.value,
            "supporting_source_ref_paths": refs,
            "supporting_sources": sources,
        }

    def _finalize_impacts(self, impact_accumulator: dict[str, dict[str, Any]]) -> list[dict[str, str | bool | list[str]]]:
        impacts: list[dict[str, str | bool | list[str]]] = []
        for domain in sorted(impact_accumulator.keys()):
            raw = impact_accumulator[domain]
            impacts.append(
                {
                    "domain": str(raw["domain"]),
                    "capability": str(raw["capability"]),
                    "status": str(raw["status"]),
                    "reason": str(raw["reason"]),
                    "affected_fields": sorted(str(item) for item in raw["affected_fields"]),
                    "is_core_impact": bool(raw["is_core_impact"]),
                }
            )
        return impacts


def _build_nested_facts(facts_by_path: Mapping[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for field_path, value in facts_by_path.items():
        segments = field_path.split(".")
        cursor = nested
        for segment in segments[:-1]:
            child = cursor.get(segment)
            if not isinstance(child, dict):
                child = {}
                cursor[segment] = child
            cursor = child
        cursor[segments[-1]] = value
    return nested


def _resolve_domain(field_path: str) -> str:
    if "." not in field_path:
        return field_path
    return field_path.split(".", 1)[0]


def _provider_rank(provider: str | None) -> int:
    normalized = provider.strip().lower() if isinstance(provider, str) else ""
    if normalized == "tushare":
        return 30
    if normalized == "akshare":
        return 20
    if normalized in {"mongodb_cache", "mongodb"}:
        return 10
    return 0


def _timestamp_rank(value: str | None) -> int:
    if not isinstance(value, str):
        return 0
    text = value.strip()
    if text == "":
        return 0
    dt = _parse_datetime(value)
    if dt is not None:
        return int(dt.timestamp())
    compact = text.replace("-", "").replace(":", "").replace("T", "").replace("Z", "").replace("+", "")
    digits = "".join(ch for ch in compact if ch.isdigit())
    if digits == "":
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _to_numeric_with_dimension(value: Any, unit: str | None, scale: str | None) -> tuple[str, float] | None:
    if isinstance(value, bool):
        return None
    numeric: float | None = None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if text == "":
            return None
        if text.endswith("%"):
            text = text[:-1]
        try:
            numeric = float(text)
        except ValueError:
            return None
    if numeric is None:
        return None

    normalized_unit = (unit or "").strip().lower()
    normalized_scale = (scale or "").strip().lower()

    if normalized_unit in _RATIO_UNITS:
        return ("ratio", numeric)
    if normalized_unit in _PERCENT_UNITS:
        return ("percent", numeric)
    if normalized_unit == "10k_cny":
        return ("currency_cny", numeric * 10000.0)
    if normalized_unit == "cny":
        return ("currency_cny", numeric)
    if normalized_unit == "cny_per_share":
        return ("cny_per_share", numeric)
    if normalized_unit == "share":
        return ("share", numeric)
    if normalized_unit == "date":
        return None
    if normalized_scale == "10k":
        return ("raw_scaled", numeric * 10000.0)
    return ("raw", numeric)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


__all__ = [
    "FieldSourceMapper",
    "FieldSourceMapperResult",
]
