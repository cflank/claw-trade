from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Sequence


MISSING_REASON_MISSING_TOKEN = "missing_token"
MISSING_REASON_MISSING_TOKEN_OR_DISABLED = "missing_token_or_disabled"
MISSING_REASON_EMPTY_RESPONSE = "empty_response"
MISSING_REASON_TIMEOUT = "timeout"
MISSING_REASON_SCHEMA_CHANGED = "schema_changed"
MISSING_REASON_PROVIDER_ERROR = "provider_error"
MISSING_REASON_PROVIDER_DISABLED = "provider_disabled"
MISSING_REASON_CACHE_STALE = "cache_stale"
MISSING_REASON_STALE = "stale"
MISSING_REASON_CROSS_PROVIDER_CONFLICT = "cross_provider_conflict"
MISSING_REASON_FIELD_SOURCE_MISSING = "field_source_missing"
MISSING_REASON_PROVIDER_ATTEMPTS_MISSING = "provider_attempts_missing"

MISSING_REASON_CODES = (
    MISSING_REASON_MISSING_TOKEN,
    MISSING_REASON_MISSING_TOKEN_OR_DISABLED,
    MISSING_REASON_EMPTY_RESPONSE,
    MISSING_REASON_TIMEOUT,
    MISSING_REASON_SCHEMA_CHANGED,
    MISSING_REASON_PROVIDER_ERROR,
    MISSING_REASON_PROVIDER_DISABLED,
    MISSING_REASON_CACHE_STALE,
    MISSING_REASON_STALE,
    MISSING_REASON_CROSS_PROVIDER_CONFLICT,
    MISSING_REASON_FIELD_SOURCE_MISSING,
    MISSING_REASON_PROVIDER_ATTEMPTS_MISSING,
)

PRICE_WINDOW_DAYS_DEFAULT = 7
_PRICE_STALE_DOMAINS = frozenset({"price_context", "valuation"})
_REPORT_DOMAINS = frozenset(
    {
        "financial_indicators",
        "income_statement",
        "balance_sheet",
        "cash_flow",
        "business_segments",
        "dividend",
        "shareholders",
    }
)
_CORE_DOMAIN_FLAGS = {
    "valuation": True,
    "financial_indicators": True,
    "income_statement": True,
    "balance_sheet": True,
    "cash_flow": True,
    "company_profile": False,
    "price_context": False,
    "business_segments": False,
    "dividend": False,
    "shareholders": False,
}
_CORE_VALUATION_FIELDS = (
    "valuation.pe_ttm",
    "valuation.pb",
)
_COMPLETE_REQUIRED_INDICATOR_FIELDS = ("financial_indicators.roe",)
_COMPLETE_REQUIRED_INCOME_FIELDS = (
    "income_statement.revenue",
    "income_statement.net_profit",
)
_COMPLETE_REQUIRED_CASH_FLOW_FIELDS = ("cash_flow.operating_cash_flow",)
_CORE_INDICATOR_FIELDS = (
    "financial_indicators.roe",
    "financial_indicators.roa",
    "financial_indicators.gross_margin",
    "financial_indicators.netprofit_margin",
    "financial_indicators.debt_to_assets",
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
_COMPLETE_REQUIRED_DOMAINS = (
    "valuation",
    "financial_indicators",
)


@dataclass(frozen=True)
class FreshnessChecker:
    price_window_days: int = PRICE_WINDOW_DAYS_DEFAULT

    @classmethod
    def Check(
        cls,
        field_sources: Mapping[str, Mapping[str, Any]],
        current_date: str,
    ) -> dict[str, dict[str, Any]]:
        return cls().check(field_sources, current_date)

    def check(
        self,
        field_sources: Mapping[str, Mapping[str, Any]],
        current_date: str,
    ) -> dict[str, dict[str, Any]]:
        if self.price_window_days <= 0:
            raise ValueError("price_window_days must be positive")
        current_day = _parse_date(current_date)
        freshness: dict[str, dict[str, Any]] = {}

        for field_path in sorted(field_sources.keys()):
            source_payload = field_sources.get(field_path)
            payload = source_payload if isinstance(source_payload, Mapping) else {}
            domain = _resolve_domain(field_path)
            as_of = _as_text(payload.get("as_of"))
            provider = _as_text(payload.get("provider"))
            diagnostic_flags: list[dict[str, str]] = []
            reason: str | None = None
            is_stale = False
            age_days: int | None = None

            if current_day is None:
                is_stale = True
                reason = MISSING_REASON_SCHEMA_CHANGED
                diagnostic_flags.append(
                    _build_flag(
                        code=MISSING_REASON_SCHEMA_CHANGED,
                        field_path=field_path,
                        domain=domain,
                        detail="current_date_unparseable",
                    )
                )
            elif as_of is None:
                is_stale = True
                reason = MISSING_REASON_FIELD_SOURCE_MISSING
                diagnostic_flags.append(
                    _build_flag(
                        code=MISSING_REASON_FIELD_SOURCE_MISSING,
                        field_path=field_path,
                        domain=domain,
                        detail="as_of_missing",
                    )
                )
            else:
                as_of_day = _parse_date(as_of)
                if as_of_day is None:
                    is_stale = True
                    reason = MISSING_REASON_SCHEMA_CHANGED
                    diagnostic_flags.append(
                        _build_flag(
                            code=MISSING_REASON_SCHEMA_CHANGED,
                            field_path=field_path,
                            domain=domain,
                            detail="as_of_unparseable",
                        )
                    )
                else:
                    age_days = (current_day - as_of_day).days
                    if age_days < 0:
                        is_stale = True
                        reason = MISSING_REASON_SCHEMA_CHANGED
                        diagnostic_flags.append(
                            _build_flag(
                                code=MISSING_REASON_SCHEMA_CHANGED,
                                field_path=field_path,
                                domain=domain,
                                detail="as_of_in_future",
                            )
                        )
                    elif domain in _PRICE_STALE_DOMAINS and age_days > self.price_window_days:
                        is_stale = True
                        reason = _resolve_stale_reason(provider)
                        diagnostic_flags.append(
                            _build_flag(
                                code=reason,
                                field_path=field_path,
                                domain=domain,
                                detail="stale_window_exceeded",
                            )
                        )
                    elif domain in _REPORT_DOMAINS:
                        # Financial report domains do not drop raw references on TTL judgement.
                        is_stale = False
                        reason = None

            freshness[field_path] = {
                "is_stale": is_stale,
                "reason": reason,
                "as_of": as_of,
                "age_days": age_days,
                "domain": domain,
                "diagnostic_flags": diagnostic_flags,
            }
        return freshness


@dataclass(frozen=True)
class FundamentalCompletenessResult:
    required_domains: tuple[str, ...]
    domain_results: dict[str, dict[str, Any]]
    core_thresholds: dict[str, bool]
    trend_ready: bool
    trend_observation: dict[str, Any]
    missing_fields: list[dict[str, Any]]
    diagnostic_flags: list[dict[str, str]]
    capability_degradation_hints: dict[str, dict[str, Any]]
    is_complete: bool


@dataclass(frozen=True)
class MissingFieldDiagnosticsResult:
    ok: bool
    quality: dict[str, Any]
    missing_fields: list[dict[str, Any]]
    diagnostic_flags: list[dict[str, str]]
    completeness: FundamentalCompletenessResult
    capability_degradation_hints: dict[str, dict[str, Any]]


class MissingFieldDiagnostics:
    @classmethod
    def Compute(
        cls,
        mapped: Any,
        freshness: Mapping[str, Mapping[str, Any]] | Sequence[Any] | None = None,
        provider_attempts: Sequence[Any] | None = None,
    ) -> MissingFieldDiagnosticsResult:
        return cls().compute(mapped, freshness, provider_attempts)

    def compute(
        self,
        mapped: Any,
        freshness: Mapping[str, Mapping[str, Any]] | Sequence[Any] | None = None,
        provider_attempts: Sequence[Any] | None = None,
    ) -> MissingFieldDiagnosticsResult:
        resolved_freshness, resolved_attempts = _resolve_inputs(freshness, provider_attempts)
        attempts = _extract_provider_attempts(resolved_attempts)
        facts_by_path = _extract_facts_by_path(mapped)
        field_sources = _extract_mapping(mapped, "field_sources")

        missing_fields: list[dict[str, Any]] = list(_extract_list(mapped, "missing_fields"))
        diagnostic_flags: list[dict[str, str]] = list(_extract_list(mapped, "diagnostic_flags"))
        provider_missing_fields, provider_flags = _build_provider_missing_fields(attempts)
        missing_fields.extend(provider_missing_fields)
        diagnostic_flags.extend(provider_flags)

        fact_source_missing_detected = False
        if len(attempts) == 0:
            missing_fields.append(
                {
                    "field_path": "provider_attempts",
                    "reason": MISSING_REASON_PROVIDER_ATTEMPTS_MISSING,
                    "domain": "provider_attempts",
                    "is_core_field": True,
                }
            )
            diagnostic_flags.append(
                _build_flag(
                    code=MISSING_REASON_PROVIDER_ATTEMPTS_MISSING,
                    field_path="provider_attempts",
                    domain="provider_attempts",
                    detail="provider_attempts_empty",
                    severity="fail",
                )
            )

        for field_path, value in facts_by_path.items():
            if not _is_non_empty_value(value):
                continue
            if field_path in field_sources:
                continue
            missing_fields.append(
                {
                    "field_path": field_path,
                    "reason": MISSING_REASON_FIELD_SOURCE_MISSING,
                    "domain": _resolve_domain(field_path),
                    "is_core_field": _is_core_domain(_resolve_domain(field_path)),
                }
            )
            fact_source_missing_detected = True
            diagnostic_flags.append(
                _build_flag(
                    code=MISSING_REASON_FIELD_SOURCE_MISSING,
                    field_path=field_path,
                    domain=_resolve_domain(field_path),
                    detail="fact_has_no_field_source",
                    severity="fail",
                )
            )

        completeness = EvaluateFundamentalCompleteness(mapped, resolved_freshness)
        missing_fields.extend(completeness.missing_fields)
        diagnostic_flags.extend(completeness.diagnostic_flags)

        core_thresholds = completeness.core_thresholds
        core_ready = (
            bool(core_thresholds.get("valuation"))
            and bool(core_thresholds.get("financial_indicators"))
            and (
                bool(core_thresholds.get("income_statement"))
                or bool(core_thresholds.get("cash_flow"))
            )
        )
        hard_failed = (len(attempts) == 0) or fact_source_missing_detected
        if hard_failed or not core_ready:
            status = "failed"
            ok = False
        elif completeness.is_complete:
            status = "ok"
            ok = True
        else:
            status = "partial"
            ok = True

        quality = _build_quality(status, diagnostic_flags)
        _assert_quality_combo(ok, status)
        return MissingFieldDiagnosticsResult(
            ok=ok,
            quality=quality,
            missing_fields=_dedupe_missing_fields(missing_fields),
            diagnostic_flags=_dedupe_diagnostic_flags(diagnostic_flags),
            completeness=completeness,
            capability_degradation_hints=completeness.capability_degradation_hints,
        )


def EvaluateFundamentalCompleteness(
    mapped: Any,
    freshness: Mapping[str, Mapping[str, Any]] | None = None,
) -> FundamentalCompletenessResult:
    freshness_map = freshness if isinstance(freshness, Mapping) else {}
    facts_by_path = _extract_facts_by_path(mapped)
    mapped_missing_fields = list(_extract_list(mapped, "missing_fields"))

    domain_results: dict[str, dict[str, Any]] = {}
    missing_fields: list[dict[str, Any]] = []
    diagnostic_flags: list[dict[str, str]] = []

    valuation_count, valuation_invalid = _count_valid_core_fields(
        facts_by_path,
        freshness_map,
        _CORE_VALUATION_FIELDS,
    )
    valuation_pass = valuation_count == len(_CORE_VALUATION_FIELDS)
    domain_results["valuation"] = _build_domain_result(
        domain="valuation",
        passed=valuation_pass,
        missing_or_blocked=valuation_invalid,
    )
    if not valuation_pass:
        _append_domain_failures(
            domain="valuation",
            missing_or_blocked=valuation_invalid,
            missing_fields=missing_fields,
            diagnostic_flags=diagnostic_flags,
            reason_code=MISSING_REASON_EMPTY_RESPONSE,
            is_core_field=True,
            default_field_path="valuation",
            severity="fail",
        )

    indicator_count, indicator_invalid = _count_valid_core_fields(
        facts_by_path,
        freshness_map,
        _COMPLETE_REQUIRED_INDICATOR_FIELDS,
    )
    indicators_pass = indicator_count == len(_COMPLETE_REQUIRED_INDICATOR_FIELDS)
    domain_results["financial_indicators"] = _build_domain_result(
        domain="financial_indicators",
        passed=indicators_pass,
        missing_or_blocked=indicator_invalid,
    )
    if not indicators_pass:
        _append_domain_failures(
            domain="financial_indicators",
            missing_or_blocked=indicator_invalid,
            missing_fields=missing_fields,
            diagnostic_flags=diagnostic_flags,
            reason_code=MISSING_REASON_EMPTY_RESPONSE,
            is_core_field=True,
            default_field_path="financial_indicators",
            severity="fail",
        )

    income_count, income_invalid = _count_valid_core_fields(
        facts_by_path,
        freshness_map,
        _COMPLETE_REQUIRED_INCOME_FIELDS,
    )
    income_pass = income_count == len(_COMPLETE_REQUIRED_INCOME_FIELDS)
    balance_pass, _ = _evaluate_statement_domain(
        domain="balance_sheet",
        facts_by_path=facts_by_path,
        freshness=freshness_map,
    )
    cash_count, cash_invalid = _count_valid_core_fields(
        facts_by_path,
        freshness_map,
        _COMPLETE_REQUIRED_CASH_FLOW_FIELDS,
    )
    cash_pass = cash_count == len(_COMPLETE_REQUIRED_CASH_FLOW_FIELDS)
    domain_results["income_statement"] = _build_domain_result(
        domain="income_statement",
        passed=income_pass,
        missing_or_blocked=[] if income_pass else income_invalid,
    )
    domain_results["balance_sheet"] = _build_domain_result(
        domain="balance_sheet",
        passed=balance_pass,
        missing_or_blocked=[] if balance_pass else ["balance_sheet"],
    )
    domain_results["cash_flow"] = _build_domain_result(
        domain="cash_flow",
        passed=cash_pass,
        missing_or_blocked=[] if cash_pass else cash_invalid,
    )
    if not income_pass:
        _append_domain_failures(
            domain="income_statement",
            missing_or_blocked=income_invalid,
            missing_fields=missing_fields,
            diagnostic_flags=diagnostic_flags,
            reason_code=MISSING_REASON_EMPTY_RESPONSE,
            is_core_field=True,
            default_field_path="income_statement",
            severity="fail",
        )
    if not cash_pass:
        _append_domain_failures(
            domain="cash_flow",
            missing_or_blocked=cash_invalid,
            missing_fields=missing_fields,
            diagnostic_flags=diagnostic_flags,
            reason_code=MISSING_REASON_EMPTY_RESPONSE,
            is_core_field=True,
            default_field_path="cash_flow",
            severity="fail",
        )

    for domain in ("company_profile", "price_context", "business_segments", "dividend", "shareholders"):
        passed, _ = _evaluate_non_core_domain(domain, facts_by_path)
        domain_results[domain] = _build_domain_result(
            domain=domain,
            passed=passed,
            missing_or_blocked=[] if passed else [domain],
        )
        if not passed:
            _append_domain_failures(
                domain=domain,
                missing_or_blocked=[domain],
                missing_fields=missing_fields,
                diagnostic_flags=diagnostic_flags,
                reason_code=MISSING_REASON_EMPTY_RESPONSE,
                is_core_field=False,
                default_field_path=domain,
                severity="warn",
            )

    trend_observation = _evaluate_trend_readiness(mapped, facts_by_path, mapped_missing_fields)
    trend_ready = bool(trend_observation["trend_ready"])
    if not trend_ready:
        diagnostic_flags.append(
            _build_flag(
                code=trend_observation["reason"],
                field_path="trend",
                domain="trend",
                detail="financial_trend_not_ready",
                severity="warn",
            )
        )
        missing_fields.append(
            {
                "field_path": "trend",
                "reason": trend_observation["reason"],
                "domain": "trend",
                "is_core_field": False,
            }
        )

    core_thresholds = {
        "valuation": valuation_pass,
        "financial_indicators": indicators_pass,
        "income_statement": income_pass,
        "balance_sheet": balance_pass,
        "cash_flow": cash_pass,
    }
    capability_hints = _build_capability_hints(
        core_thresholds=core_thresholds,
        trend_observation=trend_observation,
    )
    required_pass = all(domain_results[domain]["passed"] for domain in _COMPLETE_REQUIRED_DOMAINS)
    is_complete = required_pass and (income_pass or cash_pass)
    return FundamentalCompletenessResult(
        required_domains=_COMPLETE_REQUIRED_DOMAINS,
        domain_results=domain_results,
        core_thresholds=core_thresholds,
        trend_ready=trend_ready,
        trend_observation=trend_observation,
        missing_fields=_dedupe_missing_fields(missing_fields),
        diagnostic_flags=_dedupe_diagnostic_flags(diagnostic_flags),
        capability_degradation_hints=capability_hints,
        is_complete=is_complete,
    )


def _resolve_domain(field_path: str) -> str:
    if "." not in field_path:
        return field_path
    return field_path.split(".", 1)[0]


def _resolve_stale_reason(provider: str | None) -> str:
    if provider in {"mongodb", "mongodb_cache"}:
        return MISSING_REASON_CACHE_STALE
    return MISSING_REASON_STALE


def _build_flag(
    *,
    code: str,
    field_path: str,
    domain: str,
    detail: str,
    severity: str | None = None,
) -> dict[str, str]:
    payload: dict[str, str] = {
        "code": code,
        "field_path": field_path,
        "domain": domain,
        "detail": detail,
    }
    if severity is not None:
        payload["severity"] = severity
    return payload


def _parse_date(value: str | None) -> date | None:
    text = _as_text(value)
    if text is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()


def _as_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _resolve_inputs(
    freshness: Mapping[str, Mapping[str, Any]] | Sequence[Any] | None,
    provider_attempts: Sequence[Any] | None,
) -> tuple[Mapping[str, Mapping[str, Any]], Sequence[Any]]:
    if provider_attempts is None and _looks_like_attempts(freshness):
        return {}, list(freshness)  # type: ignore[arg-type]
    resolved_freshness: Mapping[str, Mapping[str, Any]]
    if isinstance(freshness, Mapping):
        resolved_freshness = freshness
    else:
        resolved_freshness = {}
    return resolved_freshness, provider_attempts or []


def _looks_like_attempts(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return False
    if len(value) == 0:
        return True
    first = value[0]
    if isinstance(first, Mapping):
        return "status" in first or "attempt" in first
    return hasattr(first, "status") or hasattr(first, "attempt")


def _extract_provider_attempts(provider_attempts: Sequence[Any]) -> list[Any]:
    attempts: list[Any] = []
    for item in provider_attempts:
        if hasattr(item, "attempt"):
            attempts.append(getattr(item, "attempt"))
            continue
        if isinstance(item, Mapping) and "attempt" in item:
            attempts.append(item["attempt"])
            continue
        attempts.append(item)
    return attempts


def _build_provider_missing_fields(attempts: Sequence[Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    missing_fields: list[dict[str, Any]] = []
    diagnostic_flags: list[dict[str, str]] = []
    for attempt in attempts:
        provider = _read_attempt_text(attempt, "provider")
        status = _read_attempt_text(attempt, "status")
        reason = _read_attempt_text(attempt, "reason")
        if provider is None or status is None:
            continue

        if provider == "tushare" and status == "skipped" and reason in {
            MISSING_REASON_MISSING_TOKEN,
            MISSING_REASON_PROVIDER_DISABLED,
            MISSING_REASON_MISSING_TOKEN_OR_DISABLED,
        }:
            missing_fields.append(
                {
                    "field_path": "provider.tushare",
                    "reason": MISSING_REASON_MISSING_TOKEN_OR_DISABLED,
                    "domain": "provider",
                    "is_core_field": True,
                }
            )
            diagnostic_flags.append(
                _build_flag(
                    code=MISSING_REASON_MISSING_TOKEN_OR_DISABLED,
                    field_path="provider.tushare",
                    domain="provider",
                    detail=f"tushare_{reason or status}",
                    severity="fail",
                )
            )
            continue

        if provider == "akshare" and status == "skipped" and reason == MISSING_REASON_PROVIDER_DISABLED:
            missing_fields.append(
                {
                    "field_path": "provider.akshare",
                    "reason": MISSING_REASON_PROVIDER_DISABLED,
                    "domain": "provider",
                    "is_core_field": False,
                }
            )
            diagnostic_flags.append(
                _build_flag(
                    code=MISSING_REASON_PROVIDER_DISABLED,
                    field_path="provider.akshare",
                    domain="provider",
                    detail="akshare_provider_disabled",
                    severity="warn",
                )
            )
            continue

        if status == "timeout" and provider in {"tushare", "akshare"}:
            missing_fields.append(
                {
                    "field_path": f"provider.{provider}",
                    "reason": MISSING_REASON_TIMEOUT,
                    "domain": "provider",
                    "is_core_field": provider == "tushare",
                }
            )
            diagnostic_flags.append(
                _build_flag(
                    code=MISSING_REASON_TIMEOUT,
                    field_path=f"provider.{provider}",
                    domain="provider",
                    detail=f"{provider}_timeout",
                    severity="fail" if provider == "tushare" else "warn",
                )
            )
    return missing_fields, diagnostic_flags


def _read_attempt_text(attempt: Any, field: str) -> str | None:
    value: Any = None
    if isinstance(attempt, Mapping):
        value = attempt.get(field)
    elif hasattr(attempt, field):
        value = getattr(attempt, field)
    return _as_text(value)


def _extract_mapping(mapped: Any, attr_name: str) -> dict[str, Any]:
    value = getattr(mapped, attr_name, None)
    if isinstance(value, Mapping):
        return {str(key): value[key] for key in value.keys()}
    if isinstance(mapped, Mapping):
        mapped_value = mapped.get(attr_name)
        if isinstance(mapped_value, Mapping):
            return {str(key): mapped_value[key] for key in mapped_value.keys()}
    return {}


def _extract_list(mapped: Any, attr_name: str) -> list[dict[str, Any]]:
    value = getattr(mapped, attr_name, None)
    if value is None and isinstance(mapped, Mapping):
        value = mapped.get(attr_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append({str(key): item[key] for key in item.keys()})
    return result


def _extract_facts_by_path(mapped: Any) -> dict[str, Any]:
    facts_by_path = getattr(mapped, "facts_by_path", None)
    if facts_by_path is None and isinstance(mapped, Mapping):
        facts_by_path = mapped.get("facts_by_path")
    if isinstance(facts_by_path, Mapping):
        return {str(key): facts_by_path[key] for key in facts_by_path.keys()}
    facts = getattr(mapped, "facts", None)
    if facts is None and isinstance(mapped, Mapping):
        facts = mapped.get("facts")
    if not isinstance(facts, Mapping):
        return {}
    flattened: dict[str, Any] = {}
    _flatten_nested_dict(facts, flattened, prefix="")
    return flattened


def _flatten_nested_dict(source: Mapping[str, Any], output: dict[str, Any], prefix: str) -> None:
    for raw_key, value in source.items():
        key = str(raw_key)
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            _flatten_nested_dict(value, output, path)
            continue
        output[path] = value


def _is_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, Mapping):
        return len(value) > 0
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return len(value) > 0
    return True


def _count_valid_core_fields(
    facts_by_path: Mapping[str, Any],
    freshness: Mapping[str, Mapping[str, Any]],
    field_paths: Iterable[str],
) -> tuple[int, list[str]]:
    valid_count = 0
    invalid_fields: list[str] = []
    for field_path in field_paths:
        value = facts_by_path.get(field_path)
        if not _is_non_empty_value(value):
            invalid_fields.append(field_path)
            continue
        if _is_stale_core_field(field_path, freshness):
            invalid_fields.append(field_path)
            continue
        valid_count += 1
    return valid_count, invalid_fields


def _is_stale_core_field(field_path: str, freshness: Mapping[str, Mapping[str, Any]]) -> bool:
    payload = freshness.get(field_path)
    if not isinstance(payload, Mapping):
        return False
    return bool(payload.get("is_stale"))


def _evaluate_statement_domain(
    *,
    domain: str,
    facts_by_path: Mapping[str, Any],
    freshness: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    valid_paths: list[str] = []
    prefix = f"{domain}."
    for field_path, value in facts_by_path.items():
        if not field_path.startswith(prefix):
            continue
        if not _is_non_empty_value(value):
            continue
        if _is_stale_core_field(field_path, freshness):
            continue
        valid_paths.append(field_path)
    return (len(valid_paths) >= 1), valid_paths


def _evaluate_non_core_domain(domain: str, facts_by_path: Mapping[str, Any]) -> tuple[bool, list[str]]:
    prefix = f"{domain}."
    valid_paths: list[str] = []
    for field_path, value in facts_by_path.items():
        if field_path == domain and _is_non_empty_value(value):
            valid_paths.append(field_path)
            continue
        if field_path.startswith(prefix) and _is_non_empty_value(value):
            valid_paths.append(field_path)
    return (len(valid_paths) >= 1), valid_paths


def _build_domain_result(*, domain: str, passed: bool, missing_or_blocked: list[str]) -> dict[str, Any]:
    return {
        "domain": domain,
        "passed": passed,
        "missing_or_blocked": missing_or_blocked,
    }


def _append_domain_failures(
    *,
    domain: str,
    missing_or_blocked: list[str],
    missing_fields: list[dict[str, Any]],
    diagnostic_flags: list[dict[str, str]],
    reason_code: str,
    is_core_field: bool,
    default_field_path: str,
    severity: str,
) -> None:
    targets = missing_or_blocked if len(missing_or_blocked) > 0 else [default_field_path]
    for field_path in targets:
        reason = reason_code
        if field_path == "trend":
            reason = MISSING_REASON_EMPTY_RESPONSE
        missing_fields.append(
            {
                "field_path": field_path,
                "reason": reason,
                "domain": domain,
                "is_core_field": is_core_field,
            }
        )
        diagnostic_flags.append(
            _build_flag(
                code=reason,
                field_path=field_path,
                domain=domain,
                detail="domain_incomplete",
                severity=severity,
            )
        )


def _evaluate_trend_readiness(
    mapped: Any,
    facts_by_path: Mapping[str, Any],
    mapped_missing_fields: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    periods = _collect_period_candidates(mapped, facts_by_path)
    comparable_periods = sorted({_normalize_period_value(value) for value in periods if _normalize_period_value(value)})
    annual_periods = sorted({item[:4] for item in comparable_periods if item.endswith("1231")})
    conflict_fields = _collect_trend_conflict_fields(mapped_missing_fields)
    if len(periods) == 0:
        return {
            "trend_ready": False,
            "reason": MISSING_REASON_EMPTY_RESPONSE,
            "comparable_period_count": 0,
            "annual_period_count": 0,
            "periods": [],
            "annual_periods": [],
            "conflict_fields": conflict_fields,
            "periods_source": "unavailable",
        }
    if len(conflict_fields) > 0:
        return {
            "trend_ready": False,
            "reason": MISSING_REASON_CROSS_PROVIDER_CONFLICT,
            "comparable_period_count": len(comparable_periods),
            "annual_period_count": len(annual_periods),
            "periods": comparable_periods,
            "annual_periods": annual_periods,
            "conflict_fields": conflict_fields,
            "periods_source": "mapped",
        }
    if len(comparable_periods) < 2 or len(annual_periods) < 2:
        return {
            "trend_ready": False,
            "reason": MISSING_REASON_EMPTY_RESPONSE,
            "comparable_period_count": len(comparable_periods),
            "annual_period_count": len(annual_periods),
            "periods": comparable_periods,
            "annual_periods": annual_periods,
            "conflict_fields": conflict_fields,
            "periods_source": "mapped",
        }
    return {
        "trend_ready": True,
        "reason": "",
        "comparable_period_count": len(comparable_periods),
        "annual_period_count": len(annual_periods),
        "periods": comparable_periods,
        "annual_periods": annual_periods,
        "conflict_fields": conflict_fields,
        "periods_source": "mapped",
    }


def _collect_period_candidates(mapped: Any, facts_by_path: Mapping[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key, value in facts_by_path.items():
        if key.endswith(".period") or key.endswith(".end_date") or key.endswith(".ann_date"):
            text = _as_text(value)
            if text is not None:
                candidates.append(text)
    mapped_periods = getattr(mapped, "periods", None)
    if mapped_periods is None and isinstance(mapped, Mapping):
        mapped_periods = mapped.get("periods")
    if isinstance(mapped_periods, Sequence) and not isinstance(mapped_periods, (str, bytes, bytearray)):
        for value in mapped_periods:
            text = _as_text(value)
            if text is not None:
                candidates.append(text)
    return candidates


def _collect_trend_conflict_fields(mapped_missing_fields: Sequence[dict[str, Any]]) -> list[str]:
    conflicts: list[str] = []
    for item in mapped_missing_fields:
        reason = _as_text(item.get("reason"))
        field_path = _as_text(item.get("field_path"))
        if reason != MISSING_REASON_CROSS_PROVIDER_CONFLICT or field_path is None:
            continue
        if _resolve_domain(field_path) not in _TREND_CONFLICT_DOMAINS:
            continue
        conflicts.append(field_path)
    return sorted(set(conflicts))


def _normalize_period_value(value: str) -> str | None:
    text = _as_text(value)
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    if len(digits) == 4:
        return f"{digits}1231"
    return None


def _build_capability_hints(
    *,
    core_thresholds: Mapping[str, bool],
    trend_observation: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    financial_snapshot_ready = all(
        bool(core_thresholds.get(key))
        for key in ("financial_indicators", "income_statement", "balance_sheet", "cash_flow")
    )
    valuation_ready = bool(core_thresholds.get("valuation"))
    trend_ready = bool(trend_observation.get("trend_ready"))

    capability_hints: dict[str, dict[str, Any]] = {
        "financial_snapshot": {
            "status": "available" if financial_snapshot_ready else "blocked",
            "blocked_reasons": [] if financial_snapshot_ready else ["core_financial_fields_incomplete"],
        },
        "valuation_snapshot": {
            "status": "available" if valuation_ready else "blocked",
            "blocked_reasons": [] if valuation_ready else ["valuation_core_fields_incomplete"],
        },
        "financial_trend": {
            "status": "available" if trend_ready else "blocked",
            "blocked_reasons": [] if trend_ready else [_as_text(trend_observation.get("reason")) or "trend_not_ready"],
        },
    }
    return capability_hints


def _is_core_domain(domain: str) -> bool:
    return bool(_CORE_DOMAIN_FLAGS.get(domain, False))


def _build_quality(status: str, diagnostic_flags: Sequence[dict[str, str]]) -> dict[str, Any]:
    warnings = sorted(
        {
            _as_text(item.get("code")) or ""
            for item in diagnostic_flags
            if _as_text(item.get("severity")) != "fail" and _as_text(item.get("code")) is not None
        }
    )
    return {
        "status": status,
        "is_partial": status == "partial",
        "warnings": [item for item in warnings if item != ""],
    }


def _assert_quality_combo(ok: bool, status: str) -> None:
    if ok is False and status == "failed":
        return
    if ok is True and status == "partial":
        return
    if ok is True and status == "ok":
        return
    raise ValueError(f"invalid quality combo: ok={ok}, status={status}")


def _dedupe_missing_fields(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        field_path = _as_text(item.get("field_path")) or ""
        reason = _as_text(item.get("reason")) or ""
        key = (field_path, reason)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_diagnostic_flags(items: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        code = _as_text(item.get("code")) or ""
        field_path = _as_text(item.get("field_path")) or ""
        domain = _as_text(item.get("domain")) or ""
        detail = _as_text(item.get("detail")) or ""
        key = (code, field_path, domain, detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


__all__ = [
    "EvaluateFundamentalCompleteness",
    "FreshnessChecker",
    "FundamentalCompletenessResult",
    "MISSING_REASON_CACHE_STALE",
    "MISSING_REASON_CODES",
    "MISSING_REASON_CROSS_PROVIDER_CONFLICT",
    "MISSING_REASON_EMPTY_RESPONSE",
    "MISSING_REASON_FIELD_SOURCE_MISSING",
    "MISSING_REASON_MISSING_TOKEN",
    "MISSING_REASON_MISSING_TOKEN_OR_DISABLED",
    "MISSING_REASON_PROVIDER_ATTEMPTS_MISSING",
    "MISSING_REASON_PROVIDER_DISABLED",
    "MISSING_REASON_PROVIDER_ERROR",
    "MISSING_REASON_SCHEMA_CHANGED",
    "MISSING_REASON_STALE",
    "MISSING_REASON_TIMEOUT",
    "MissingFieldDiagnostics",
    "MissingFieldDiagnosticsResult",
    "PRICE_WINDOW_DAYS_DEFAULT",
]
