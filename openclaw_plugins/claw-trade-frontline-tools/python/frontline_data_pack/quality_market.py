from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import ChartRef, EvidenceRef, MarketIndicators, MarketPriceRow, ProviderAttempt, Quality


_CORE_MARKET_ENDPOINTS = frozenset(
    {
        ("akshare", "stock_zh_a_hist"),
        ("eastmoney_direct", "push2his_kline"),
        ("sina", "stock_zh_a_daily"),
        ("tencent", "stock_zh_a_hist_tx"),
    }
)
_FAILED_ATTEMPT_STATUSES = frozenset({"timeout", "error", "schema_invalid", "empty", "config_blocked"})
_HARD_FAILED_ATTEMPT_STATUSES = frozenset({"timeout", "error", "schema_invalid", "config_blocked"})
_L2_CORE_ERROR_CODES = frozenset({"L2_WRITE_FAILED", "L2_READBACK_FAILED", "L2_HASH_MISMATCH"})
_MONGO_FAILURE_MARKERS = (
    "MONGO_WRITE_FAILED",
    "provider_cache_upsert_failed",
    "provider_attempt_insert_failed",
)
_MIN_COMPLETE_OHLCV_ROWS = 20


@dataclass(frozen=True)
class MarketQualityInput:
    rows: list[MarketPriceRow]
    indicators: MarketIndicators | None
    chart_refs: list[ChartRef]
    attempts: list[ProviderAttempt]
    l2_refs: list[EvidenceRef]
    diagnostic_flags: list[str]
    as_of_date: str | None


def compute_market_quality(
    rows: list[MarketPriceRow],
    indicators: MarketIndicators | None,
    chart_refs: list[ChartRef],
    attempts: list[ProviderAttempt],
    l2_refs: list[EvidenceRef],
    *,
    diagnostic_flags: list[str] | None = None,
    as_of_date: str | None = None,
) -> Quality:
    return ComputeMarketQuality().compute(
        MarketQualityInput(
            rows=list(rows),
            indicators=indicators,
            chart_refs=list(chart_refs),
            attempts=list(attempts),
            l2_refs=list(l2_refs),
            diagnostic_flags=[] if diagnostic_flags is None else list(diagnostic_flags),
            as_of_date=as_of_date,
        )
    )


@dataclass(frozen=True)
class ComputeMarketQuality:
    def compute(self, input: MarketQualityInput) -> Quality:
        warnings: list[str] = []
        coverage = _coverage_score(input.rows)
        freshness = _compute_freshness(input.rows, input.as_of_date)
        core_attempts = _core_attempts(input.attempts)

        if not input.rows:
            warnings.append("market_ohlcv_empty")
            return Quality(
                status="failed",
                coverage_score=coverage,
                freshness_status=freshness,
                warnings=warnings,
            )

        if core_attempts and all(attempt.status in _FAILED_ATTEMPT_STATUSES for attempt in core_attempts):
            warnings.append("market_p0_p1_sources_all_failed")
            return Quality(
                status="failed",
                coverage_score=coverage,
                freshness_status=freshness,
                warnings=warnings,
            )

        if _has_core_l2_failure(core_attempts, input.diagnostic_flags):
            warnings.append("core_l2_write_or_verify_failed")
            return Quality(
                status="failed",
                coverage_score=coverage,
                freshness_status=freshness,
                warnings=warnings,
            )

        if _has_core_success(core_attempts) and not _has_auditable_raw_ref(input.l2_refs):
            warnings.append("market_core_raw_evidence_missing")
            return Quality(
                status="failed",
                coverage_score=coverage,
                freshness_status=freshness,
                warnings=warnings,
            )

        status = "complete"
        if not _has_min_complete_ohlcv_rows(input.rows):
            status = "partial"
            warnings.append(
                f"market_ohlcv_window_insufficient:rows={len(input.rows)},min_required={_MIN_COMPLETE_OHLCV_ROWS}"
            )
        if any(attempt.status in _HARD_FAILED_ATTEMPT_STATUSES for attempt in core_attempts):
            status = "partial"
            warnings.append("market_partial_provider_failures")
        if input.indicators is None:
            status = "partial"
            warnings.append("market_indicators_missing")
        if not input.chart_refs:
            status = "partial"
            warnings.append("market_chart_evidence_gap")
        if _has_mongo_failure(input.diagnostic_flags):
            status = "partial"
            warnings.append("market_mongo_upsert_failed")
        if freshness == "stale":
            status = "partial"
            warnings.append("market_freshness_stale")

        return Quality(
            status=status,
            coverage_score=coverage,
            freshness_status=freshness,
            warnings=warnings[:20],
        )


def _core_attempts(attempts: list[ProviderAttempt]) -> list[ProviderAttempt]:
    return [
        attempt
        for attempt in attempts
        if (attempt.provider, attempt.endpoint) in _CORE_MARKET_ENDPOINTS
    ]


def _has_core_success(core_attempts: list[ProviderAttempt]) -> bool:
    return any(attempt.status == "success" for attempt in core_attempts)


def _has_core_l2_failure(core_attempts: list[ProviderAttempt], diagnostic_flags: list[str]) -> bool:
    for attempt in core_attempts:
        if attempt.error_code in _L2_CORE_ERROR_CODES:
            return True
    for item in diagnostic_flags:
        lower = item.lower()
        if "l2_write_failed:provider_raw" in lower:
            return True
        if "l2_readback_failed" in lower or "l2_hash_mismatch" in lower:
            return True
    return False


def _has_auditable_raw_ref(l2_refs: list[EvidenceRef]) -> bool:
    for ref in l2_refs:
        if ref.kind == "provider_raw" and ref.readback_verified:
            return True
    return False


def _has_mongo_failure(diagnostic_flags: list[str]) -> bool:
    for flag in diagnostic_flags:
        for marker in _MONGO_FAILURE_MARKERS:
            if marker in flag:
                return True
    return False


def _has_min_complete_ohlcv_rows(rows: list[MarketPriceRow]) -> bool:
    return len(rows) >= _MIN_COMPLETE_OHLCV_ROWS


def _coverage_score(rows: list[MarketPriceRow]) -> float:
    if not rows:
        return 0.0
    return max(0.0, min(1.0, len(rows) / 60.0))


def _compute_freshness(rows: list[MarketPriceRow], as_of_date: str | None) -> str:
    if not rows:
        return "unknown"
    if as_of_date is None:
        return "unknown"
    try:
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    except ValueError:
        return "unknown"
    latest = rows[-1].trade_date
    try:
        latest_date = datetime.strptime(latest, "%Y-%m-%d").date()
    except ValueError:
        return "unknown"
    age_days = (as_of - latest_date).days
    if age_days <= 7:
        return "fresh"
    return "stale"


__all__ = [
    "ComputeMarketQuality",
    "MarketQualityInput",
    "compute_market_quality",
]
