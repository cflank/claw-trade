from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from config import SOCIAL_SCHEMA_VERSION
from observability import emit_json_log, record_metric
from pack_schema import (
    SOCIAL_PACK_SCHEMA_INVALID,
    SOCIAL_PROVIDER_ATTEMPTS_MISSING,
    SocialPackSchemaError,
    validate_social_pack_payload,
)

SOCIAL_FORBIDDEN_SOURCE_USED = "SOCIAL_FORBIDDEN_SOURCE_USED"
SOCIAL_SIGNAL_EVIDENCE_MISSING = "SOCIAL_SIGNAL_EVIDENCE_MISSING"
SOCIAL_FAILED_PACK_OVERSTATED = "SOCIAL_FAILED_PACK_OVERSTATED"
SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM = "SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM"

_FORBIDDEN_ENDPOINTS = frozenset(
    {
        "stock_hot_follow_xq",
        "stock_hot_tweet_xq",
        "stock_hot_deal_xq",
        "xueqiu_heat",
    }
)
_FORBIDDEN_SOURCE_TOKENS = ("stocktwits", "reddit", "yahoo", "google_news", "google-news")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？\n\r]+")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_VIKING_REF_PREFIX = "viking://resources/workflow/"
_NEGATION_TERMS = (
    "未提供",
    "未返回",
    "未读取",
    "未跟踪",
    "并未",
    "没有",
    "无法",
    "不能",
    "不得",
    "不足",
    "缺失",
    "未见",
    "无",
)
_COMPLETE_SENTIMENT_PATTERNS = (
    re.compile(r"情绪评分\s*[：:]\s*(?:10|[1-9](?:\.\d+)?)\s*分?"),
    re.compile(r"投资者情绪(?:明显)?偏(?:多|空)"),
    re.compile(r"市场情绪(?:明显)?(?:乐观|悲观)"),
    re.compile(r"(?:明确|强烈)看(?:多|空)"),
)
_SOURCE_CLAIM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kol": ("KOL", "kol", "意见领袖", "大V观点"),
    "original_post": ("原帖", "帖子正文", "帖文原文"),
    "user_view": ("用户观点", "用户看法", "投资者观点"),
    "retail_institution_split": ("散户机构分歧", "散户/机构分层", "散户与机构分歧"),
}
_SOURCE_SUPPORT_TOKENS: dict[str, tuple[str, ...]] = {
    "kol": ("kol", "influencer", "xueqiu"),
    "original_post": ("post", "tweet", "comment", "xueqiu"),
    "user_view": ("viewpoint", "comment", "user_view"),
    "retail_institution_split": ("retail", "institution"),
}


@dataclass(frozen=True)
class SocialPackContractResult:
    ok: bool
    schema_version: str | None
    visible_tools_ok: bool
    provider_attempts_ok: bool
    evidence_refs_ok: bool
    quality_gate_ok: bool
    forbidden_sources_absent: bool
    reason_codes: tuple[str, ...]


def validate_social_pack_schema(pack: dict[str, Any]) -> SocialPackContractResult:
    reason_codes: list[str] = []
    schema_version: str | None = None
    provider_attempts_ok = True
    evidence_refs_ok = True
    forbidden_sources_absent = True

    if not isinstance(pack, Mapping):
        _append_reason(reason_codes, SOCIAL_PACK_SCHEMA_INVALID)
        return SocialPackContractResult(
            ok=False,
            schema_version=None,
            visible_tools_ok=True,
            provider_attempts_ok=False,
            evidence_refs_ok=False,
            quality_gate_ok=True,
            forbidden_sources_absent=False,
            reason_codes=tuple(reason_codes),
        )

    schema_version = _as_text(pack.get("schema_version"))
    if schema_version != SOCIAL_SCHEMA_VERSION:
        _append_reason(reason_codes, SOCIAL_PACK_SCHEMA_INVALID)

    try:
        validate_social_pack_payload(pack)
    except SocialPackSchemaError as exc:
        if exc.code == SOCIAL_PROVIDER_ATTEMPTS_MISSING:
            _append_reason(reason_codes, SOCIAL_PROVIDER_ATTEMPTS_MISSING)
            provider_attempts_ok = False
        else:
            _append_reason(reason_codes, SOCIAL_PACK_SCHEMA_INVALID)

    attempts_value = pack.get("provider_attempts")
    if not isinstance(attempts_value, list) or not attempts_value:
        provider_attempts_ok = False
        _append_reason(reason_codes, SOCIAL_PROVIDER_ATTEMPTS_MISSING)
        attempts: list[Mapping[str, Any]] = []
    else:
        attempts = [item for item in attempts_value if isinstance(item, Mapping)]
        if len(attempts) != len(attempts_value):
            _append_reason(reason_codes, SOCIAL_PACK_SCHEMA_INVALID)
            provider_attempts_ok = False

    evidence_mapping = pack.get("evidence")
    evidence_raw_refs = _extract_evidence_raw_refs(evidence_mapping)
    if evidence_raw_refs is None:
        evidence_refs_ok = False
        _append_reason(reason_codes, SOCIAL_SIGNAL_EVIDENCE_MISSING)

    for attempt in attempts:
        provider = _as_text(attempt.get("provider")) or ""
        endpoint = _as_text(attempt.get("endpoint")) or ""
        if _is_forbidden_source(provider=provider, endpoint=endpoint):
            forbidden_sources_absent = False
            _append_reason(reason_codes, SOCIAL_FORBIDDEN_SOURCE_USED)

        if attempt.get("ok") is True:
            payload_hash = _as_text(attempt.get("payload_hash"))
            raw_payload_ref = _as_text(attempt.get("raw_payload_ref"))
            has_hash = bool(payload_hash and _SHA256_RE.match(payload_hash))
            has_ref = bool(raw_payload_ref and raw_payload_ref.startswith(_VIKING_REF_PREFIX))
            ref_declared = evidence_raw_refs is not None and raw_payload_ref in evidence_raw_refs
            if not has_hash or not has_ref or not ref_declared:
                evidence_refs_ok = False
                _append_reason(reason_codes, SOCIAL_SIGNAL_EVIDENCE_MISSING)

    if evidence_raw_refs is not None:
        evidence_obj = _as_mapping(evidence_mapping)
        provider_attempts_path = _as_text(evidence_obj.get("provider_attempts_path")) if evidence_obj else None
        pack_path = _as_text(evidence_obj.get("pack_path")) if evidence_obj else None
        if not (
            provider_attempts_path
            and provider_attempts_path.startswith(_VIKING_REF_PREFIX)
            and pack_path
            and pack_path.startswith(_VIKING_REF_PREFIX)
        ):
            evidence_refs_ok = False
            _append_reason(reason_codes, SOCIAL_SIGNAL_EVIDENCE_MISSING)

    ok = provider_attempts_ok and evidence_refs_ok and forbidden_sources_absent and not reason_codes
    result = SocialPackContractResult(
        ok=ok,
        schema_version=schema_version,
        visible_tools_ok=True,
        provider_attempts_ok=provider_attempts_ok,
        evidence_refs_ok=evidence_refs_ok,
        quality_gate_ok=True,
        forbidden_sources_absent=forbidden_sources_absent,
        reason_codes=tuple(reason_codes),
    )
    return _record_guard_result(result, event="social.guard.pack_schema")


def validate_social_report_against_pack(report: str, pack: dict[str, Any]) -> SocialPackContractResult:
    base = validate_social_pack_schema(pack)
    reason_codes = list(base.reason_codes)
    quality_gate_ok = True

    quality_status = _extract_quality_status(pack)
    if quality_status == "failed" and _report_contains_complete_sentiment_judgment(report):
        quality_gate_ok = False
        _append_reason(reason_codes, SOCIAL_FAILED_PACK_OVERSTATED)

    if _report_has_unsupported_source_claim(report, pack):
        quality_gate_ok = False
        _append_reason(reason_codes, SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM)

    ok = base.ok and quality_gate_ok and not reason_codes
    result = SocialPackContractResult(
        ok=ok,
        schema_version=base.schema_version,
        visible_tools_ok=base.visible_tools_ok,
        provider_attempts_ok=base.provider_attempts_ok,
        evidence_refs_ok=base.evidence_refs_ok,
        quality_gate_ok=quality_gate_ok,
        forbidden_sources_absent=base.forbidden_sources_absent,
        reason_codes=tuple(reason_codes),
    )
    return _record_guard_result(result, event="social.guard.report_check")


def _extract_quality_status(pack: dict[str, Any]) -> str | None:
    quality = pack.get("quality")
    if not isinstance(quality, Mapping):
        return None
    return _as_text(quality.get("status"))


def _extract_evidence_raw_refs(evidence: object) -> set[str] | None:
    mapping = _as_mapping(evidence)
    if mapping is None:
        return None
    raw_refs = mapping.get("raw_payload_refs")
    if not isinstance(raw_refs, list):
        return None
    return {
        ref.strip()
        for ref in raw_refs
        if isinstance(ref, str) and ref.strip() and ref.strip().startswith(_VIKING_REF_PREFIX)
    }


def _report_contains_complete_sentiment_judgment(report: str) -> bool:
    text = report if isinstance(report, str) else ""
    if not text.strip():
        return False
    lowered = text.lower()
    for pattern in _COMPLETE_SENTIMENT_PATTERNS:
        if pattern.search(text):
            return True
    return "buy" in lowered or "sell" in lowered


def _report_has_unsupported_source_claim(report: str, pack: dict[str, Any]) -> bool:
    if not isinstance(report, str) or not report.strip():
        return False
    sentences = [line.strip() for line in _SENTENCE_SPLIT_RE.split(report) if line.strip()]
    for claim_type, keywords in _SOURCE_CLAIM_KEYWORDS.items():
        if not _contains_affirmative_claim(sentences, keywords):
            continue
        if not _pack_supports_claim(pack=pack, claim_type=claim_type):
            return True
    return False


def _contains_affirmative_claim(sentences: list[str], keywords: tuple[str, ...]) -> bool:
    for sentence in sentences:
        if not any(keyword in sentence for keyword in keywords):
            continue
        if any(term in sentence for term in _NEGATION_TERMS):
            continue
        return True
    return False


def _pack_supports_claim(*, pack: dict[str, Any], claim_type: str) -> bool:
    tokens = _SOURCE_SUPPORT_TOKENS.get(claim_type, ())
    if not tokens:
        return False
    attempts = pack.get("provider_attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            provider = (_as_text(attempt.get("provider")) or "").lower()
            endpoint = (_as_text(attempt.get("endpoint")) or "").lower()
            joined = f"{provider}.{endpoint}"
            if any(token in joined for token in tokens):
                return True
    data = pack.get("data")
    if isinstance(data, Mapping):
        for bucket_name in ("attention_signals", "topic_keyword_signals", "related_symbol_signals", "narrative_signals"):
            bucket = data.get(bucket_name)
            if not isinstance(bucket, list):
                continue
            for signal in bucket:
                if not isinstance(signal, Mapping):
                    continue
                keys_blob = " ".join(str(key).lower() for key in signal.keys())
                if any(token in keys_blob for token in tokens):
                    return True
    return False


def _record_guard_result(result: SocialPackContractResult, *, event: str) -> SocialPackContractResult:
    code = result.reason_codes[0] if result.reason_codes else "OK"
    record_metric("social.guard.result.count", tags={"ok": str(result.ok).lower(), "code": code})
    if not result.ok and result.reason_codes:
        for reason_code in result.reason_codes:
            record_metric("social.guard.failure_reason.count", tags={"code": reason_code})
    emit_json_log(
        level="INFO" if result.ok else "ERROR",
        event=event,
        code=code,
        endpoint="guard",
        fields={"reason_codes": list(result.reason_codes)},
    )
    return result


def _is_forbidden_source(*, provider: str, endpoint: str) -> bool:
    provider_l = provider.strip().lower()
    endpoint_l = endpoint.strip().lower()
    if endpoint_l in _FORBIDDEN_ENDPOINTS:
        return True
    combined = f"{provider_l}.{endpoint_l}"
    return any(token in combined for token in _FORBIDDEN_SOURCE_TOKENS)


def _append_reason(reason_codes: list[str], code: str) -> None:
    if code not in reason_codes:
        reason_codes.append(code)


def _as_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None
