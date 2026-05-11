from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from observability import emit_json_log, record_metric, trace_span

QualityStatus = Literal["complete", "partial", "failed"]

P0_ALL_FAILED = "P0_ALL_FAILED"
NO_ACCEPTED_SIGNALS = "NO_ACCEPTED_SIGNALS"
EVIDENCE_REF_MISSING = "EVIDENCE_REF_MISSING"
ONLY_ONE_SIGNAL_BUCKET = "ONLY_ONE_SIGNAL_BUCKET"
ONLY_RELATED_SYMBOLS = "ONLY_RELATED_SYMBOLS"
SOURCE_CONCENTRATED = "SOURCE_CONCENTRATED"
TREND_FIELD_MISSING = "TREND_FIELD_MISSING"
PROVIDER_PARTIAL_FAILURE = "PROVIDER_PARTIAL_FAILURE"
CACHE_REF_PARTIAL = "CACHE_REF_PARTIAL"
COMPLETE_CONDITIONS_MET = "COMPLETE_CONDITIONS_MET"
WEAK_SOCIAL_EVIDENCE = "WEAK_SOCIAL_EVIDENCE"


@dataclass(frozen=True)
class QualityInput:
    attempts: tuple[Any, ...]
    buckets: Any
    required_p0_endpoints: tuple[str, ...]


@dataclass(frozen=True)
class QualityWarning:
    code: str
    message: str
    evidence_ref: str | None = None


@dataclass(frozen=True)
class QualityDecision:
    ok: bool
    status: QualityStatus
    social_judgment_allowed: bool
    missing_fields: tuple[str, ...]
    warnings: tuple[QualityWarning, ...]
    reason_codes: tuple[str, ...]


def evaluate_social_quality(input: QualityInput) -> QualityDecision:
    with trace_span("social.quality.evaluate", endpoint="quality"):
        attempts = tuple(input.attempts)
        attention_signals = _signal_bucket(input.buckets, "attention_signals")
        topic_keyword_signals = _signal_bucket(input.buckets, "topic_keyword_signals")
        related_symbol_signals = _signal_bucket(input.buckets, "related_symbol_signals")
        narrative_signals = _signal_bucket(input.buckets, "narrative_signals")
        accepted_signals = (
            attention_signals + topic_keyword_signals + related_symbol_signals + narrative_signals
        )

        p0_attempts = [attempt for attempt in attempts if _safe_text(attempt, "priority") == "P0"]
        p0_success_count = sum(
            1
            for attempt in p0_attempts
            if _safe_bool(attempt, "ok") and _safe_text(attempt, "status") == "success"
        )
        if p0_success_count == 0:
            return _record_quality_decision(_failed(P0_ALL_FAILED))

        if len(accepted_signals) == 0:
            return _record_quality_decision(_failed(NO_ACCEPTED_SIGNALS))

        if _has_missing_evidence(accepted_signals):
            return _record_quality_decision(_failed(EVIDENCE_REF_MISSING))

        attention_count = len(attention_signals)
        keyword_count = len(topic_keyword_signals)
        related_count = len(related_symbol_signals)
        narrative_count = len(narrative_signals)
        accepted_count = len(accepted_signals)
        bucket_count = sum(
            1
            for count in (attention_count, keyword_count, related_count, narrative_count)
            if count > 0
        )
        source_count = _count_distinct_sources(accepted_signals)
        has_trend = any(_has_trend_fields(signal) for signal in accepted_signals)
        provider_failures = sum(
            1
            for attempt in attempts
            if (not _safe_bool(attempt, "ok")) or _safe_text(attempt, "status") in {"error", "timeout"}
        )

        reason_codes: list[str] = []
        missing_fields: list[str] = []
        warnings: list[QualityWarning] = []

        if bucket_count == 1:
            _append_warning(
                warnings,
                reason_codes,
                code=ONLY_ONE_SIGNAL_BUCKET,
                message="有效线索只覆盖一类信号，覆盖面不足。",
            )
        if related_count > 0 and attention_count == 0 and keyword_count == 0:
            _append_warning(
                warnings,
                reason_codes,
                code=ONLY_RELATED_SYMBOLS,
                message="仅有相关标的线索，缺少目标热度或关键词信号。",
            )
        if source_count == 1:
            _append_warning(
                warnings,
                reason_codes,
                code=SOURCE_CONCENTRATED,
                message="线索来源集中，来源多样性不足。",
            )
        if not has_trend:
            missing_fields.append("rank_change_or_source_time")
            _append_warning(
                warnings,
                reason_codes,
                code=TREND_FIELD_MISSING,
                message="热度趋势字段缺失，无法支撑趋势判断。",
            )
        if provider_failures > 0:
            _append_warning(
                warnings,
                reason_codes,
                code=PROVIDER_PARTIAL_FAILURE,
                message="存在 provider 失败，样本完整性受限。",
            )

        if warnings:
            social_judgment_allowed = attention_count > 0 or keyword_count > 0
            return _record_quality_decision(
                QualityDecision(
                    ok=True,
                    status="partial",
                    social_judgment_allowed=social_judgment_allowed,
                    missing_fields=tuple(missing_fields),
                    warnings=tuple(warnings),
                    reason_codes=tuple(reason_codes),
                )
            )

        if attention_count >= 1 and (keyword_count >= 1 or related_count >= 1) and accepted_count > 0:
            return _record_quality_decision(
                QualityDecision(
                    ok=True,
                    status="complete",
                    social_judgment_allowed=True,
                    missing_fields=tuple(missing_fields),
                    warnings=tuple(),
                    reason_codes=(COMPLETE_CONDITIONS_MET,),
                )
            )

        return _record_quality_decision(
            QualityDecision(
                ok=True,
                status="partial",
                social_judgment_allowed=False,
                missing_fields=tuple(missing_fields),
                warnings=(
                    QualityWarning(
                        code=WEAK_SOCIAL_EVIDENCE,
                        message="线索强度不足，仅可用于限制性说明。",
                    ),
                ),
                reason_codes=(WEAK_SOCIAL_EVIDENCE,),
            )
        )


def _failed(code: str) -> QualityDecision:
    return QualityDecision(
        ok=False,
        status="failed",
        social_judgment_allowed=False,
        missing_fields=tuple(),
        warnings=tuple(),
        reason_codes=(code,),
    )


def _append_warning(
    warnings: list[QualityWarning],
    reason_codes: list[str],
    *,
    code: str,
    message: str,
) -> None:
    if code in reason_codes:
        return
    reason_codes.append(code)
    warnings.append(QualityWarning(code=code, message=message))


def _signal_bucket(buckets: Any, key: str) -> list[Any]:
    signals = _safe_value(buckets, key)
    if isinstance(signals, list):
        return signals
    if isinstance(signals, tuple):
        return list(signals)
    return []


def _has_missing_evidence(signals: list[Any]) -> bool:
    for signal in signals:
        raw_payload_ref = _safe_text(signal, "raw_payload_ref")
        content_hash = _safe_text(signal, "content_hash")
        if raw_payload_ref == "" or content_hash == "":
            return True
    return False


def _count_distinct_sources(signals: list[Any]) -> int:
    sources: set[tuple[str, str, str]] = set()
    for signal in signals:
        provider = _safe_text(signal, "provider")
        platform = _safe_text(signal, "platform")
        endpoint = _safe_text(signal, "endpoint")
        source = (provider, platform, endpoint)
        if source == ("", "", ""):
            continue
        sources.add(source)
    return len(sources)


def _has_trend_fields(signal: Any) -> bool:
    rank_change = _safe_value(signal, "rank_change")
    if isinstance(rank_change, bool):
        return rank_change
    if isinstance(rank_change, int):
        return True
    if isinstance(rank_change, float):
        return True
    if isinstance(rank_change, str) and rank_change.strip() != "":
        return True
    source_time = _safe_text(signal, "source_time")
    return source_time != ""


def _safe_value(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _safe_text(obj: Any, key: str) -> str:
    value = _safe_value(obj, key)
    if isinstance(value, str):
        return value.strip()
    return ""


def _safe_bool(obj: Any, key: str) -> bool:
    value = _safe_value(obj, key)
    return value is True


def _record_quality_decision(decision: QualityDecision) -> QualityDecision:
    record_metric("social.quality.status.count", tags={"status": decision.status})
    for reason_code in decision.reason_codes:
        record_metric("social.quality.failed_reason.count", tags={"code": reason_code})
    emit_json_log(
        level="INFO" if decision.ok else "ERROR",
        event="social.quality.decision",
        code=decision.reason_codes[0] if decision.reason_codes else decision.status,
        endpoint="quality",
        fields={"status": decision.status, "reason_codes": list(decision.reason_codes)},
    )
    return decision
