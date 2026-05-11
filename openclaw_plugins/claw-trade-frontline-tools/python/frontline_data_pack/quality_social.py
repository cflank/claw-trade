from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import EvidenceRef, ProviderAttempt, Quality, SocialSignal


_L2_CORE_ERROR_CODES = frozenset({"L2_WRITE_FAILED", "L2_READBACK_FAILED", "L2_HASH_MISMATCH"})


@dataclass(frozen=True)
class SocialQualityInput:
    signals: list[SocialSignal]
    provider_attempts: list[ProviderAttempt]
    l2_refs: list[EvidenceRef]
    diagnostic_flags: list[str]


def compute_social_quality(
    signals: list[SocialSignal],
    attempts: list[ProviderAttempt],
    l2_refs: list[EvidenceRef],
    *,
    diagnostic_flags: list[str] | None = None,
) -> Quality:
    return ComputeSocialQuality().compute(
        SocialQualityInput(
            signals=signals,
            provider_attempts=attempts,
            l2_refs=l2_refs,
            diagnostic_flags=[] if diagnostic_flags is None else list(diagnostic_flags),
        )
    )


@dataclass(frozen=True)
class ComputeSocialQuality:
    def compute(self, input: SocialQualityInput) -> Quality:
        warnings: list[str] = []
        accepted_total = len(input.signals)
        raw_total = sum(attempt.raw_count for attempt in input.provider_attempts)
        if accepted_total == 0:
            return Quality(
                status="failed",
                coverage_score=0.0,
                freshness_status="unknown",
                warnings=["social_target_accepted_signal_zero"],
            )

        if _has_core_l2_failure(input.provider_attempts, input.diagnostic_flags):
            return Quality(
                status="failed",
                coverage_score=_coverage_score(accepted_total=accepted_total, raw_total=raw_total),
                freshness_status=_compute_freshness(input.signals),
                warnings=["core_l2_write_or_verify_failed"],
            )

        if not _has_auditable_provider_raw_ref(input.l2_refs):
            return Quality(
                status="failed",
                coverage_score=_coverage_score(accepted_total=accepted_total, raw_total=raw_total),
                freshness_status=_compute_freshness(input.signals),
                warnings=["social_core_raw_evidence_missing"],
            )

        bucket_counts = _bucket_counts(input.signals)
        status = "complete"
        if _has_only_platform_heat(bucket_counts):
            status = "partial"
            warnings.append("social_only_platform_heat")
        elif _is_source_too_narrow(bucket_counts):
            status = "partial"
            warnings.append("social_source_too_narrow")

        return Quality(
            status=status,
            coverage_score=_coverage_score(accepted_total=accepted_total, raw_total=raw_total),
            freshness_status=_compute_freshness(input.signals),
            warnings=warnings[:20],
        )


def _has_core_l2_failure(provider_attempts: list[ProviderAttempt], diagnostic_flags: list[str]) -> bool:
    for attempt in provider_attempts:
        if attempt.error_code in _L2_CORE_ERROR_CODES:
            return True
    for item in diagnostic_flags:
        lower = item.lower()
        if "l2_write_failed" in lower:
            return True
        if "l2_readback_failed" in lower:
            return True
        if "l2_hash_mismatch" in lower:
            return True
    return False


def _has_auditable_provider_raw_ref(l2_refs: list[EvidenceRef]) -> bool:
    for ref in l2_refs:
        if ref.kind != "provider_raw":
            continue
        if ref.readback_verified:
            return True
    return False


def _bucket_counts(signals: list[SocialSignal]) -> dict[str, int]:
    counts = {"attention": 0, "topic_keyword": 0, "related_symbol": 0, "narrative": 0}
    for signal in signals:
        counts[signal.signal_type] = counts.get(signal.signal_type, 0) + 1
    return counts


def _has_only_platform_heat(bucket_counts: dict[str, int]) -> bool:
    return (
        bucket_counts.get("attention", 0) > 0
        and bucket_counts.get("topic_keyword", 0) == 0
        and bucket_counts.get("related_symbol", 0) == 0
        and bucket_counts.get("narrative", 0) == 0
    )


def _is_source_too_narrow(bucket_counts: dict[str, int]) -> bool:
    return (
        bucket_counts.get("attention", 0) == 0
        and bucket_counts.get("topic_keyword", 0) == 0
        and bucket_counts.get("related_symbol", 0) > 0
        and bucket_counts.get("narrative", 0) == 0
    )


def _coverage_score(*, accepted_total: int, raw_total: int) -> float:
    denominator = max(raw_total, accepted_total, 1)
    return max(0.0, min(1.0, accepted_total / denominator))


def _compute_freshness(signals: list[SocialSignal]) -> str:
    newest: datetime | None = None
    for signal in signals:
        parsed = _parse_datetime_like(signal.observed_at)
        if parsed is None:
            continue
        if newest is None or parsed > newest:
            newest = parsed
    if newest is None:
        return "unknown"
    age_days = (datetime.now(newest.tzinfo) - newest).days
    if age_days <= 3:
        return "fresh"
    return "stale"


def _parse_datetime_like(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


__all__ = [
    "ComputeSocialQuality",
    "SocialQualityInput",
    "compute_social_quality",
]
