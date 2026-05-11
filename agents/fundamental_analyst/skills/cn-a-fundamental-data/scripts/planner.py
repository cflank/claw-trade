from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from models import ApiCallSpec, NormalizedInput
from provider_specs import (
    AKSHARE_V1_API_TEMPLATES,
    TUSHARE_V1_API_TEMPLATES,
    render_akshare_spec,
    render_tushare_spec,
)

FND_PROVIDER_PLAN_INVALID = "FND_PROVIDER_PLAN_INVALID"
FND_PROVIDER_PLAN_MISSING_PRIMARY_SOURCE = "FND_PROVIDER_PLAN_MISSING_PRIMARY_SOURCE"
FND_AKSHARE_SUPPLEMENT_DISABLED = "FND_AKSHARE_SUPPLEMENT_DISABLED"
FND_AKSHARE_SUPPLEMENT_UNRESOLVED = "FND_AKSHARE_SUPPLEMENT_UNRESOLVED"


class ProviderPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ProviderPlan:
    phase1_calls: tuple[ApiCallSpec, ...]
    requires_runtime_phase2: bool
    diagnostics: tuple[str, ...]
    log_fields: dict[str, str]


@dataclass(frozen=True)
class AkshareSupplementPlan:
    phase2_calls: tuple[ApiCallSpec, ...]
    unresolved_families: tuple[str, ...]
    diagnostics: tuple[str, ...]
    log_fields: dict[str, str]


@dataclass(frozen=True)
class DeterministicProviderPlanner:
    disable_tushare: bool = False

    def build_plan(self, normalized: NormalizedInput, cache_result: Any) -> ProviderPlan:
        return BuildProviderPlan(normalized, cache_result, disable_tushare=self.disable_tushare)


_TUSHARE_FAILURE_STATUSES = {"empty", "error", "timeout", "schema_invalid", "skipped"}
_FIELD_FAMILY_MIN_COVERAGE_V1 = {
    "company_profile": 1,
    "price_context": 1,
    "valuation": 1,
    "financial_indicators": 1,
    "income_statement": 1,
    "balance_sheet": 1,
    "cash_flow": 1,
    "business_segments": 1,
    "dividend": 1,
    "shareholders": 1,
}


def BuildAkshareSupplementPlanFromTushareResult(
    tushare_results: Sequence[Any] | None,
    cache_result: Any,
    normalized: NormalizedInput,
    *,
    disable_akshare: bool = False,
) -> AkshareSupplementPlan:
    results = tuple(tushare_results or ())
    tushare_field_families = _collect_field_families(TUSHARE_V1_API_TEMPLATES)
    by_api_name = _index_tushare_results_by_api_name(results)
    family_coverage: dict[str, set[str]] = {}
    unresolved_families: set[str] = set()
    diagnostics: list[str] = []

    for template in TUSHARE_V1_API_TEMPLATES:
        result = by_api_name.get(template.api_name)
        families = _split_field_families(template.field_family)
        if result is None:
            unresolved_families.update(families)
            diagnostics.append(f"phase1_missing_result:{template.api_name}")
            continue

        status = _extract_result_status(result)
        if status in _TUSHARE_FAILURE_STATUSES:
            unresolved_families.update(families)
            diagnostics.append(f"phase1_status_unresolved:{template.api_name}:{status}")
            continue

        if status != "success":
            unresolved_families.update(families)
            diagnostics.append(f"phase1_status_unresolved:{template.api_name}:unknown_{status}")
            continue

        covered_paths = _collect_result_paths(result)
        for family in families:
            matched = {path for path in covered_paths if _path_matches_family(path, family)}
            family_coverage.setdefault(family, set()).update(matched)

    for family in sorted(tushare_field_families):
        threshold = _FIELD_FAMILY_MIN_COVERAGE_V1.get(family, 1)
        covered_count = len(family_coverage.get(family, set()))
        if covered_count < threshold:
            unresolved_families.add(family)
            diagnostics.append(f"phase1_low_coverage:{family}:{covered_count}/{threshold}")

    phase2_calls: list[ApiCallSpec] = []
    for template in AKSHARE_V1_API_TEMPLATES:
        families = _split_field_families(template.field_family)
        if not families:
            continue
        if any(family in unresolved_families for family in families) or _is_static_uncovered_family(
            template.field_family, tushare_field_families
        ):
            phase2_calls.append(render_akshare_spec(template.api_name, normalized))

    if disable_akshare:
        if unresolved_families:
            diagnostics.append(
                f"{FND_AKSHARE_SUPPLEMENT_UNRESOLVED}:{','.join(sorted(unresolved_families))}"
            )
        diagnostics.append(f"{FND_AKSHARE_SUPPLEMENT_DISABLED}:akshare_disabled")
        phase2_calls = []

    for call in phase2_calls:
        _assert_api_call_spec_complete(call)

    return AkshareSupplementPlan(
        phase2_calls=tuple(phase2_calls),
        unresolved_families=tuple(sorted(unresolved_families)),
        diagnostics=tuple(_dedupe_keep_order(diagnostics)),
        log_fields=_build_phase2_log_fields(
            cache_result=cache_result,
            tushare_results=results,
            unresolved_families=unresolved_families,
            phase2_calls=phase2_calls,
            disable_akshare=disable_akshare,
        ),
    )


def BuildProviderPlan(
    normalized: NormalizedInput,
    cache_result: Any,
    *,
    disable_tushare: bool = False,
) -> ProviderPlan:
    phase1_calls: list[ApiCallSpec] = []
    diagnostics: list[str] = []

    tushare_field_families = _collect_field_families(TUSHARE_V1_API_TEMPLATES)
    if disable_tushare:
        diagnostics.append(f"{FND_PROVIDER_PLAN_MISSING_PRIMARY_SOURCE}:tushare_disabled")
    else:
        for template in TUSHARE_V1_API_TEMPLATES:
            phase1_calls.append(render_tushare_spec(template.api_name, normalized))

    for template in AKSHARE_V1_API_TEMPLATES:
        if _is_static_uncovered_family(template.field_family, tushare_field_families):
            phase1_calls.append(render_akshare_spec(template.api_name, normalized))

    for call in phase1_calls:
        _assert_api_call_spec_complete(call)

    log_fields = _build_log_fields(cache_result=cache_result, disable_tushare=disable_tushare, phase1_calls=phase1_calls)

    return ProviderPlan(
        phase1_calls=tuple(phase1_calls),
        requires_runtime_phase2=True,
        diagnostics=tuple(diagnostics),
        log_fields=log_fields,
    )


def _collect_field_families(templates: tuple[Any, ...]) -> set[str]:
    covered: set[str] = set()
    for template in templates:
        families = _split_field_families(template.field_family)
        covered.update(families)
    return covered


def _is_static_uncovered_family(field_family: str, tushare_covered: set[str]) -> bool:
    families = _split_field_families(field_family)
    if not families:
        return False
    return any(family not in tushare_covered for family in families)


def _split_field_families(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _assert_api_call_spec_complete(call: ApiCallSpec) -> None:
    if call.provider not in {"tushare", "akshare"}:
        raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"provider 不合法: {call.provider}")
    if call.api_name.strip() == "":
        raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, "api_name 不能为空")
    if call.field_family.strip() == "":
        raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"field_family 不能为空: {call.api_name}")
    if not isinstance(call.required, bool):
        raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"required 必须是 bool: {call.api_name}")
    if call.timeout_ms <= 0:
        raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"timeout_ms 必须大于 0: {call.api_name}")
    if call.retry_limit < 0:
        raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"retry_limit 不能小于 0: {call.api_name}")
    for key, value in call.parameters.items():
        if key.strip() == "":
            raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"parameters key 不能为空: {call.api_name}")
        if value is None or str(value).strip() == "":
            raise ProviderPlanError(FND_PROVIDER_PLAN_INVALID, f"parameters value 不能为空: {call.api_name}.{key}")


def _build_log_fields(*, cache_result: Any, disable_tushare: bool, phase1_calls: list[ApiCallSpec]) -> dict[str, str]:
    cache_status, cache_reason = _extract_cache_status_and_reason(cache_result)
    return {
        "provider_order": "mongodb_inspect_then_tushare_then_akshare_static_uncovered",
        "cache_status": cache_status,
        "cache_reason": cache_reason,
        "tushare_enabled": "false" if disable_tushare else "true",
        "phase1_call_count": str(len(phase1_calls)),
    }


def _build_phase2_log_fields(
    *,
    cache_result: Any,
    tushare_results: tuple[Any, ...],
    unresolved_families: set[str],
    phase2_calls: list[ApiCallSpec],
    disable_akshare: bool,
) -> dict[str, str]:
    cache_status, cache_reason = _extract_cache_status_and_reason(cache_result)
    status_values = [_extract_result_status(item) for item in tushare_results]
    success_count = sum(1 for status in status_values if status == "success")
    return {
        "provider_order": "mongodb_inspect_then_tushare_phase1_then_akshare_phase2",
        "cache_status": cache_status,
        "cache_reason": cache_reason,
        "phase1_tushare_result_count": str(len(tushare_results)),
        "phase1_tushare_success_count": str(success_count),
        "phase1_unresolved_family_count": str(len(unresolved_families)),
        "phase1_unresolved_families": ",".join(sorted(unresolved_families)),
        "phase2_akshare_enabled": "false" if disable_akshare else "true",
        "phase2_call_count": str(len(phase2_calls)),
    }


def _extract_cache_status_and_reason(cache_result: Any) -> tuple[str, str]:
    cache_status = "unknown"
    cache_reason = "unknown"
    if cache_result is None:
        return cache_status, cache_reason
    attempt = getattr(cache_result, "attempt", None)
    if attempt is None:
        return cache_status, cache_reason
    status = getattr(attempt, "status", None)
    reason = getattr(attempt, "reason", None)
    if isinstance(status, str) and status.strip() != "":
        cache_status = status
    if isinstance(reason, str) and reason.strip() != "":
        cache_reason = reason
    return cache_status, cache_reason


def _index_tushare_results_by_api_name(results: tuple[Any, ...]) -> dict[str, Any]:
    by_api_name: dict[str, Any] = {}
    for result in results:
        attempt = getattr(result, "attempt", None)
        if attempt is None:
            continue
        provider = getattr(attempt, "provider", None)
        api_name = getattr(attempt, "api_name", None)
        if provider != "tushare":
            continue
        if not isinstance(api_name, str) or api_name.strip() == "":
            continue
        by_api_name[api_name] = result
    return by_api_name


def _extract_result_status(result: Any) -> str:
    attempt = getattr(result, "attempt", None)
    if attempt is None:
        return "unknown"
    status = getattr(attempt, "status", None)
    if not isinstance(status, str):
        return "unknown"
    value = status.strip()
    if value == "":
        return "unknown"
    return value


def _collect_result_paths(result: Any) -> set[str]:
    paths: set[str] = set()
    extracted_fields = getattr(result, "extracted_fields", None)
    if isinstance(extracted_fields, list):
        for item in extracted_fields:
            if isinstance(item, tuple) and len(item) >= 1:
                field_path = item[0]
                if isinstance(field_path, str) and field_path.strip() != "":
                    paths.add(field_path.strip())
    attempt = getattr(result, "attempt", None)
    if attempt is not None:
        field_coverage = getattr(attempt, "field_coverage", None)
        if isinstance(field_coverage, list):
            for path in field_coverage:
                if isinstance(path, str) and path.strip() != "":
                    paths.add(path.strip())
    return paths


def _path_matches_family(path: str, family: str) -> bool:
    if path == family or path.startswith(f"{family}."):
        return True
    if path == f"facts.{family}" or path.startswith(f"facts.{family}."):
        return True
    return False


def _dedupe_keep_order(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)
