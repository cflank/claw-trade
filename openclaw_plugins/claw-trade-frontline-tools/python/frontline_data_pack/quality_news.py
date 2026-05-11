from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import ProviderAttempt, Quality


_P0_NEWS_ENDPOINTS = frozenset(
    {
        ("akshare", "stock_news_em"),
        ("akshare", "stock_info_global_cls"),
    }
)
_FAIL_ATTEMPT_STATUSES = frozenset({"timeout", "error", "schema_invalid", "config_blocked"})
_L2_CORE_ERROR_CODES = frozenset({"L2_WRITE_FAILED", "L2_READBACK_FAILED", "L2_HASH_MISMATCH"})


@dataclass(frozen=True)
class NewsQualityInput:
    company_news_count: int
    industry_macro_count: int
    announcement_count: int
    rejected_count: int
    raw_total_count: int
    provider_attempts: list[ProviderAttempt]
    publish_times: list[str | None]
    as_of_date: str
    diagnostic_flags: list[str]


@dataclass(frozen=True)
class ComputeNewsQuality:
    def compute(self, input: NewsQualityInput) -> Quality:
        accepted_total = input.company_news_count + input.industry_macro_count + input.announcement_count
        has_background = input.industry_macro_count > 0 or input.announcement_count > 0
        warnings: list[str] = []

        status = "complete"
        if accepted_total == 0:
            status = "failed"
            warnings.append("news_accepted_total=0")
        elif input.company_news_count == 0:
            status = "partial"
            warnings.append("company_news_hard_match_missing")

        p0_attempts = [
            attempt
            for attempt in input.provider_attempts
            if (attempt.provider, attempt.endpoint) in _P0_NEWS_ENDPOINTS
        ]
        if p0_attempts and all(attempt.status in _FAIL_ATTEMPT_STATUSES for attempt in p0_attempts):
            if has_background:
                status = "partial"
                warnings.append("p0_news_sources_failed_background_only")
            else:
                status = "failed"
                warnings.append("p0_news_sources_failed")

        if _has_core_l2_failure(input.provider_attempts, input.diagnostic_flags):
            status = "failed"
            warnings.append("core_l2_write_or_verify_failed")

        denominator = max(input.raw_total_count, accepted_total + input.rejected_count, 1)
        coverage = accepted_total / denominator
        freshness = _compute_freshness_status(input.publish_times, input.as_of_date)

        return Quality(
            status=status,
            coverage_score=max(0.0, min(1.0, coverage)),
            freshness_status=freshness,
            warnings=warnings[:20],
        )


def _has_core_l2_failure(provider_attempts: list[ProviderAttempt], diagnostic_flags: list[str]) -> bool:
    for attempt in provider_attempts:
        if attempt.error_code in _L2_CORE_ERROR_CODES:
            return True
    for item in diagnostic_flags:
        lower = item.lower()
        if "l2_write_failed" in lower or "l2_readback_failed" in lower or "l2_hash_mismatch" in lower:
            return True
    return False


def _compute_freshness_status(publish_times: list[str | None], as_of_date: str) -> str:
    normalized = [value for value in publish_times if isinstance(value, str) and value.strip()]
    if not normalized:
        return "unknown"
    if any(value is None or (isinstance(value, str) and not value.strip()) for value in publish_times):
        return "unknown"

    parsed_as_of = _parse_as_of_date(as_of_date)
    if parsed_as_of is None:
        return "unknown"
    newest = max((_parse_datetime_like(value) for value in normalized), default=None)
    if newest is None:
        return "unknown"
    age_days = (parsed_as_of.date() - newest.date()).days
    if age_days <= 7:
        return "fresh"
    return "stale"


def _parse_as_of_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _parse_datetime_like(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


__all__ = [
    "ComputeNewsQuality",
    "NewsQualityInput",
]
