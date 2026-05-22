from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.guards.fundamental_claim_rules import (
    CLAIM_DICTIONARY_REVISION_ID,
    CLAIM_RULES_VERSION,
    COMPLIANT_DOWNGRADE_PHRASES,
    CONCLUSION_PATTERNS,
    METRIC_FIELD_PATHS,
    METRIC_PATTERNS,
    NARRATIVE_PATTERNS,
    TREND_KEYWORDS,
)

UNSUPPORTED_CLAIM = "unsupported_claim"
CLAIM_INPUT_INVALID = "claim_input_invalid"

ClaimType = Literal["metric", "conclusion", "narrative"]

_METRIC_GAP_CONTEXT_PHRASES: tuple[str, ...] = (
    "缺失",
    "未提供",
    "无法判断",
    "证据不足",
    "待补充",
    "不可用",
    "证据缺口",
)
_METRIC_ASSERTION_CUE_PATTERN = re.compile(
    r"(?:保持|维持|处于|位于)[^。！？!?；;\n]{0,6}(?:高位|低位)"
    r"|(?:毛利率|净利率|资产负债率|ROE|ROA)[^。！？!?；;\n]{0,8}(?:高|低|改善|恶化|提升|下降|上升|回落|回升)"
    r"|(?:高于|低于|优于|弱于|改善|恶化|提升|下降|上升|回落|回升)",
    re.IGNORECASE,
)
_METRIC_NUMERIC_ASSERTION_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|倍|元|亿元|万亿|亿|万)")
_TARGET_PRICE_ASSERTION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|港元|美元|美金|人民币|HKD|USD|CNY|USDT|USDC|U|块)"
    r"|(?:目标价|目标价格|合理价|目标位)[^。！？!?；;\n]{0,16}\d+(?:\.\d+)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FundamentalReportClaim:
    claim_type: ClaimType
    claim_key: str
    text: str
    span_start: int
    span_end: int
    needs_trend: bool


@dataclass(frozen=True)
class UnsupportedClaim:
    claim: FundamentalReportClaim
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class FundamentalClaimGateEvaluation:
    rule_version: str
    dictionary_revision_id: str
    claims: tuple[FundamentalReportClaim, ...]
    unsupported_claims: tuple[UnsupportedClaim, ...]
    guard: GuardResult


def parse_report_claims_by_rules_v1(report_text: str) -> tuple[FundamentalReportClaim, ...]:
    claims: list[FundamentalReportClaim] = []
    seen: set[tuple[str, str, int, int]] = set()

    for metric_key, pattern_list in METRIC_PATTERNS.items():
        for pattern in pattern_list:
            for match in re.finditer(pattern, report_text, flags=re.IGNORECASE):
                if _is_metric_gap_context(report_text, match.start(), match.end()):
                    continue
                text = match.group(0)
                claim = FundamentalReportClaim(
                    claim_type="metric",
                    claim_key=metric_key,
                    text=text,
                    span_start=match.start(),
                    span_end=match.end(),
                    needs_trend=_context_has_trend_signal(report_text, match.start(), match.end()),
                )
                _add_unique_claim(claims, seen, claim)

    for claim_key, pattern_list in CONCLUSION_PATTERNS.items():
        for pattern in pattern_list:
            for match in re.finditer(pattern, report_text, flags=re.IGNORECASE):
                if _is_compliant_downgrade_context(report_text, match.start(), match.end()):
                    continue
                if claim_key == "target_price" and not _is_target_price_assertion_context(
                    report_text,
                    match.start(),
                    match.end(),
                ):
                    continue
                claim = FundamentalReportClaim(
                    claim_type="conclusion",
                    claim_key=claim_key,
                    text=match.group(0),
                    span_start=match.start(),
                    span_end=match.end(),
                    needs_trend=False,
                )
                _add_unique_claim(claims, seen, claim)

    for claim_key, pattern_list in NARRATIVE_PATTERNS.items():
        for pattern in pattern_list:
            for match in re.finditer(pattern, report_text):
                context = _context_window(report_text, match.start(), match.end(), size=30)
                claim = FundamentalReportClaim(
                    claim_type="narrative",
                    claim_key=claim_key,
                    text=context,
                    span_start=match.start(),
                    span_end=match.end(),
                    needs_trend=False,
                )
                _add_unique_claim(claims, seen, claim)

    claims.sort(key=lambda item: item.span_start)
    return tuple(claims)


def unsupported_reasons_for_claim_v1(
    claim: FundamentalReportClaim,
    pack: dict[str, Any],
) -> tuple[str, ...]:
    reasons: list[str] = []

    if claim.claim_type == "metric":
        field_path = METRIC_FIELD_PATHS.get(claim.claim_key)
        if field_path is None:
            reasons.append("metric_mapping_missing")
        else:
            if not _has_non_empty_fact(pack, field_path):
                reasons.append("required_fact_missing")
            if not _has_field_source(pack, field_path):
                reasons.append("field_source_missing")
        if claim.needs_trend and _capability_status(pack, "financial_trend") != "available":
            reasons.append("financial_trend_unavailable")

    if claim.claim_type == "conclusion":
        # Guard source: AGENTS truthfulness redline for unsupported target prices.
        # 2026-05-20 human approval removed runtime expression gates for ratings,
        # buy/sell direction, and broad valuation wording; only concrete target
        # price assertions remain hard-gated here.
        if claim.claim_key == "target_price" and _is_capability_blocked(pack, "target_price"):
            reasons.append("target_price_blocked")

    if claim.claim_type == "narrative":
        # 软叙事词不作为 hard gate。真实性硬门只拦具体数值和具体目标价；
        # “护城河/龙头/定价能力”这类表达交给 prompt
        # 和人工评审收口，避免为了咬文嚼字阻断 frontline 报告生成。
        return tuple(reasons)

    return tuple(reasons)


def is_claim_unbacked_by_evidence_v1(
    claim: FundamentalReportClaim,
    pack: dict[str, Any],
) -> bool:
    return len(unsupported_reasons_for_claim_v1(claim, pack)) > 0


def evaluate_fundamental_report_claims_v1(
    report_text: str,
    pack: dict[str, Any],
) -> FundamentalClaimGateEvaluation:
    claims = parse_report_claims_by_rules_v1(report_text)
    unsupported: list[UnsupportedClaim] = []
    for claim in claims:
        reason_codes = unsupported_reasons_for_claim_v1(claim, pack)
        if reason_codes:
            unsupported.append(UnsupportedClaim(claim=claim, reason_codes=reason_codes))

    if unsupported:
        guard = guard_failed(
            category="claim",
            reason=f"fundamental report 存在 unsupported claim: {len(unsupported)}",
            paths=(),
            early_stop=True,
        )
    else:
        guard = guard_passed(category="claim")

    return FundamentalClaimGateEvaluation(
        rule_version=CLAIM_RULES_VERSION,
        dictionary_revision_id=CLAIM_DICTIONARY_REVISION_ID,
        claims=claims,
        unsupported_claims=tuple(unsupported),
        guard=guard,
    )


def evaluate_fundamental_report_claims_from_paths_v1(
    report_path: Path,
    pack_path: Path,
) -> FundamentalClaimGateEvaluation:
    report_text = report_path.read_text(encoding="utf-8")
    pack_raw = json.loads(pack_path.read_text(encoding="utf-8"))
    if not isinstance(pack_raw, dict):
        raise ValueError("fundamental pack 必须是 JSON 对象")
    return evaluate_fundamental_report_claims_v1(report_text=report_text, pack=pack_raw)


def _add_unique_claim(
    claims: list[FundamentalReportClaim],
    seen: set[tuple[str, str, int, int]],
    claim: FundamentalReportClaim,
) -> None:
    dedupe_key = (claim.claim_type, claim.claim_key, claim.span_start, claim.span_end)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    claims.append(claim)


def _context_has_trend_signal(text: str, start: int, end: int) -> bool:
    window = _context_window(text, start, end, size=20)
    return any(keyword in window for keyword in TREND_KEYWORDS)


def _context_window(text: str, start: int, end: int, *, size: int) -> str:
    left = max(0, start - size)
    right = min(len(text), end + size)
    return text[left:right]


def _is_compliant_downgrade_context(text: str, start: int, end: int) -> bool:
    sentence = _sentence_window(text, start, end)
    normalized = re.sub(r"\s+", "", sentence)
    return any(phrase in normalized for phrase in COMPLIANT_DOWNGRADE_PHRASES)


def _is_metric_gap_context(text: str, start: int, end: int) -> bool:
    sentence = _sentence_window(text, start, end)
    normalized = re.sub(r"\s+", "", sentence)
    has_gap_phrase = any(phrase in normalized for phrase in _METRIC_GAP_CONTEXT_PHRASES)
    if not has_gap_phrase:
        return False
    if _METRIC_NUMERIC_ASSERTION_PATTERN.search(sentence):
        return False
    if _METRIC_ASSERTION_CUE_PATTERN.search(sentence):
        return False
    return True


def _is_target_price_assertion_context(text: str, start: int, end: int) -> bool:
    if _is_metric_gap_context(text, start, end):
        return False
    sentence = _sentence_window(text, start, end)
    return _TARGET_PRICE_ASSERTION_PATTERN.search(sentence) is not None


def _sentence_window(text: str, start: int, end: int) -> str:
    separators = "。！？!?;\n"
    left = start
    while left > 0 and text[left - 1] not in separators:
        left -= 1
    right = end
    while right < len(text) and text[right] not in separators:
        right += 1
    return text[left:right]


def _capability_status(pack: dict[str, Any], capability_key: str) -> str:
    capabilities = pack.get("evidence_capabilities")
    if not isinstance(capabilities, dict):
        return "unknown"
    payload = capabilities.get(capability_key)
    if not isinstance(payload, dict):
        return "unknown"
    status = payload.get("status")
    if isinstance(status, str):
        return status.strip().lower()
    return "unknown"


def _is_capability_blocked(pack: dict[str, Any], capability_key: str) -> bool:
    status = _capability_status(pack, capability_key)
    return status in {"blocked", "禁写"}


def _has_non_empty_fact(pack: dict[str, Any], field_path: str) -> bool:
    facts = pack.get("facts")
    if not isinstance(facts, dict):
        return False
    value = _value_at_path(facts, field_path)
    return _is_non_empty_value(value)


def _has_field_source(pack: dict[str, Any], field_path: str) -> bool:
    field_sources = pack.get("field_sources")
    if not isinstance(field_sources, dict):
        return False
    return field_path in field_sources or f"facts.{field_path}" in field_sources


def _has_business_segments_evidence(pack: dict[str, Any]) -> bool:
    facts = pack.get("facts")
    if not isinstance(facts, dict):
        return False
    business_segments = facts.get("business_segments")
    if not isinstance(business_segments, list) or not business_segments:
        return False
    field_sources = pack.get("field_sources")
    if not isinstance(field_sources, dict):
        return False
    for key in field_sources.keys():
        if not isinstance(key, str):
            continue
        if key.startswith("business_segments") or key.startswith("facts.business_segments"):
            return True
    return False


def _value_at_path(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _is_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True
