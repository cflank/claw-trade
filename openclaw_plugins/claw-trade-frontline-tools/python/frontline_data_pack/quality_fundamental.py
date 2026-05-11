from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .models import FundamentalField, ProviderAttempt, Quality


_CORE_FIELDS: tuple[str, ...] = (
    "valuation.pe_ttm",
    "valuation.pb",
    "financial_indicators.roe",
    "income_statement.revenue",
    "income_statement.net_profit",
    "cash_flow.operating_cash_flow",
)
_CORE_PROVIDER_ENDPOINTS = frozenset(
    {
        ("akshare", "company_info"),
        ("akshare", "stock_individual_info_em"),
        ("akshare", "financial_abstract"),
        ("akshare", "stock_financial_abstract_ths"),
        ("akshare", "stock_zh_a_spot_em"),
    }
)
_FAILED_ATTEMPT_STATUSES = frozenset({"timeout", "error", "schema_invalid", "empty", "config_blocked"})
_L2_CORE_ERROR_CODES = frozenset({"L2_WRITE_FAILED", "L2_READBACK_FAILED", "L2_HASH_MISMATCH"})


@dataclass(frozen=True)
class FundamentalQualityInput:
    fields: Mapping[str, FundamentalField]
    missing_core_fields: list[str]
    attempts: list[ProviderAttempt]
    as_of_date: str | None
    diagnostic_flags: list[str]


def compute_fundamental_quality(
    fields: Mapping[str, FundamentalField],
    missing_core_fields: list[str],
    attempts: list[ProviderAttempt],
    *,
    as_of_date: str | None = None,
    diagnostic_flags: list[str] | None = None,
) -> Quality:
    return ComputeFundamentalQuality().compute(
        FundamentalQualityInput(
            fields=fields,
            missing_core_fields=list(missing_core_fields),
            attempts=attempts,
            as_of_date=as_of_date,
            diagnostic_flags=[] if diagnostic_flags is None else list(diagnostic_flags),
        )
    )


@dataclass(frozen=True)
class ComputeFundamentalQuality:
    def compute(self, input: FundamentalQualityInput) -> Quality:
        warnings: list[str] = []
        has_company_profile = _has_company_profile(input.fields)
        has_valuation_core = _has_valuation_core(input.fields)
        has_financial_or_cashflow = _has_financial_or_cashflow_core(input.fields)
        core_attempts = _core_attempts(input.attempts)

        if (
            not input.fields
            and core_attempts
            and all(attempt.status in _FAILED_ATTEMPT_STATUSES for attempt in core_attempts)
        ):
            warnings.append("fundamental_core_providers_all_failed")
            return Quality(
                status="failed",
                coverage_score=_coverage_score(input.fields),
                freshness_status=_compute_freshness(input.fields, input.as_of_date),
                warnings=_build_warnings(warnings, input.missing_core_fields),
            )

        if _has_core_l2_failure(input.attempts, input.diagnostic_flags):
            warnings.append("core_l2_write_or_verify_failed")
            return Quality(
                status="failed",
                coverage_score=_coverage_score(input.fields),
                freshness_status=_compute_freshness(input.fields, input.as_of_date),
                warnings=_build_warnings(warnings, input.missing_core_fields),
            )

        if not has_company_profile:
            warnings.append("company_profile_missing")
            return Quality(
                status="failed",
                coverage_score=_coverage_score(input.fields),
                freshness_status=_compute_freshness(input.fields, input.as_of_date),
                warnings=_build_warnings(warnings, input.missing_core_fields),
            )

        status = "partial"
        if has_valuation_core and has_financial_or_cashflow:
            status = "complete"

        if status != "complete":
            warnings.append("fundamental_core_fields_missing_or_incomplete")

        return Quality(
            status=status,
            coverage_score=_coverage_score(input.fields),
            freshness_status=_compute_freshness(input.fields, input.as_of_date),
            warnings=_build_warnings(warnings, input.missing_core_fields),
        )


def _core_attempts(attempts: list[ProviderAttempt]) -> list[ProviderAttempt]:
    output: list[ProviderAttempt] = []
    for attempt in attempts:
        if attempt.provider == "tushare" or (attempt.provider, attempt.endpoint) in _CORE_PROVIDER_ENDPOINTS:
            output.append(attempt)
    return output


def _has_company_profile(fields: Mapping[str, FundamentalField]) -> bool:
    for key in ("company_profile.industry", "company_profile.main_business"):
        if key in fields and fields[key].value not in (None, ""):
            return True
    return False


def _has_valuation_core(fields: Mapping[str, FundamentalField]) -> bool:
    for key in ("valuation.pe_ttm", "valuation.pb", "financial_indicators.roe"):
        field = fields.get(key)
        if field is None or field.value in (None, ""):
            return False
    return True


def _has_financial_or_cashflow_core(fields: Mapping[str, FundamentalField]) -> bool:
    revenue = fields.get("income_statement.revenue")
    net_profit = fields.get("income_statement.net_profit")
    operating_cash_flow = fields.get("cash_flow.operating_cash_flow")
    has_financial_group = (
        revenue is not None
        and net_profit is not None
        and revenue.value not in (None, "")
        and net_profit.value not in (None, "")
    )
    has_cash_flow_group = operating_cash_flow is not None and operating_cash_flow.value not in (None, "")
    return has_financial_group or has_cash_flow_group


def _has_core_l2_failure(provider_attempts: list[ProviderAttempt], diagnostic_flags: list[str]) -> bool:
    for attempt in provider_attempts:
        if attempt.error_code in _L2_CORE_ERROR_CODES:
            return True
    for item in diagnostic_flags:
        lower = item.lower()
        if "l2_write_failed" in lower or "l2_readback_failed" in lower or "l2_hash_mismatch" in lower:
            return True
    return False


def _coverage_score(fields: Mapping[str, FundamentalField]) -> float:
    populated = 0
    for key in _CORE_FIELDS:
        field = fields.get(key)
        if field is not None and field.value not in (None, ""):
            populated += 1
    return max(0.0, min(1.0, populated / len(_CORE_FIELDS)))


def _build_warnings(base: list[str], missing_core_fields: list[str]) -> list[str]:
    warnings = list(base)
    if missing_core_fields:
        warnings.append("missing_core_fields:" + ",".join(missing_core_fields))
    return warnings[:20]


def _compute_freshness(fields: Mapping[str, FundamentalField], as_of_date: str | None) -> str:
    if not fields:
        return "unknown"
    as_of = _parse_datetime_like(as_of_date) if as_of_date else None
    newest: datetime | None = None
    for field in fields.values():
        parsed = _parse_datetime_like(field.source_time) or _parse_datetime_like(field.report_period)
        if parsed is None:
            continue
        if newest is None or parsed > newest:
            newest = parsed
    if newest is None or as_of is None:
        return "unknown"
    age_days = (as_of.date() - newest.date()).days
    if age_days <= 90:
        return "fresh"
    return "stale"


def _parse_datetime_like(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


__all__ = [
    "ComputeFundamentalQuality",
    "FundamentalQualityInput",
    "compute_fundamental_quality",
]
