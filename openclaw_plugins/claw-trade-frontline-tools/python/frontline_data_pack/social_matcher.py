from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping

from .models import NewsTargetProfile
from .security import normalize_ticker


SocialTargetProfile = NewsTargetProfile
_CODE_RE = re.compile(r"(\d{5,6})")
_TEXT_EVIDENCE_KEYS = (
    "text_excerpt",
    "text",
    "content",
    "summary",
    "url",
    "source_url",
    "original_url",
    "link",
)
_NARRATIVE_SOURCE_LINK_KEYS = ("url", "link", "source_url", "original_url")
_SEARCH_PROVIDER_MARKERS = ("bocha", "jina", "tavily")
_SEARCH_ENDPOINT_MARKERS = ("public_page_search", "search")
_SIGNAL_TYPE_ALIASES = {
    "attention": "attention",
    "heat_rank": "attention",
    "topic_keyword": "topic_keyword",
    "keyword": "topic_keyword",
    "related_symbol": "related_symbol",
    "narrative": "narrative",
}
_MATCH_TEXT_KEYS = (
    "symbol",
    "ticker",
    "target_ticker",
    "stock_code",
    "code",
    "name",
    "title",
    "keyword",
    "text_excerpt",
    "text",
    "content",
    "summary",
    "query",
)


@dataclass
class BucketSocialSignals:
    attention_signals: list[dict[str, Any]]
    topic_keyword_signals: list[dict[str, Any]]
    related_symbol_signals: list[dict[str, Any]]
    narrative_signals: list[dict[str, Any]]
    rejected_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attention_signals": self.attention_signals,
            "topic_keyword_signals": self.topic_keyword_signals,
            "related_symbol_signals": self.related_symbol_signals,
            "narrative_signals": self.narrative_signals,
            "rejected_count": self.rejected_count,
        }


def match_social_signal(raw_signal: Mapping[str, Any], target: SocialTargetProfile) -> dict[str, Any] | None:
    normalized_target = _normalize_target(target)
    signal_type = _normalize_signal_type(raw_signal.get("signal_type"))
    if signal_type is None:
        return None

    match_span = _match_target_span(raw_signal=raw_signal, target=normalized_target)
    if not match_span:
        return None

    signal = {
        "signal_id": _build_signal_id(raw_signal, normalized_target.ticker, signal_type),
        "signal_type": signal_type,
        "source_platform": _resolve_source_platform(raw_signal),
        "observed_at": _optional_string(raw_signal.get("observed_at"))
        or _optional_string(raw_signal.get("source_time"))
        or _optional_string(raw_signal.get("publish_time"))
        or "",
        "target_ticker": normalized_target.ticker,
        "matched_target": True,
        "match_evidence_span": match_span,
        "rank": _to_int_or_none(raw_signal.get("rank")),
        "heat_value": _to_float_or_none(raw_signal.get("heat_value")),
        "keyword": _optional_string(raw_signal.get("keyword")),
        "related_ticker": _normalize_related_ticker(raw_signal.get("related_ticker")),
        "text_excerpt": _optional_string(raw_signal.get("text_excerpt"))
        or _optional_string(raw_signal.get("content"))
        or _optional_string(raw_signal.get("summary")),
        "url": _optional_string(raw_signal.get("url")),
        "link": _optional_string(raw_signal.get("link")),
        "source_url": _optional_string(raw_signal.get("source_url")),
        "original_url": _optional_string(raw_signal.get("original_url")),
        "provider": _optional_string(raw_signal.get("provider")) or "unknown",
        "endpoint": _optional_string(raw_signal.get("endpoint")) or "unknown",
        "raw_payload_ref": _optional_string(raw_signal.get("raw_payload_ref")) or "",
        "payload_hash": _optional_string(raw_signal.get("payload_hash")) or "",
    }

    if signal_type == "narrative":
        if _is_search_narrative_signal(signal) and not _has_narrative_source_link(signal):
            return None
        if not has_text_evidence(signal):
            return None
    return signal


def bucket_social_signals(signals: Iterable[Mapping[str, Any]]) -> BucketSocialSignals:
    buckets = BucketSocialSignals(
        attention_signals=[],
        topic_keyword_signals=[],
        related_symbol_signals=[],
        narrative_signals=[],
        rejected_count=0,
    )
    for signal in signals:
        signal_type = _normalize_signal_type(signal.get("signal_type"))
        if signal_type == "attention":
            buckets.attention_signals.append(dict(signal))
        elif signal_type == "topic_keyword":
            buckets.topic_keyword_signals.append(dict(signal))
        elif signal_type == "related_symbol":
            buckets.related_symbol_signals.append(dict(signal))
        elif signal_type == "narrative":
            buckets.narrative_signals.append(dict(signal))
        else:
            buckets.rejected_count += 1
    return buckets


def match_and_bucket_social_signals(
    raw_signals: Iterable[Mapping[str, Any]],
    target: SocialTargetProfile,
) -> BucketSocialSignals:
    accepted_signals: list[dict[str, Any]] = []
    rejected_count = 0
    for raw_signal in raw_signals:
        matched = match_social_signal(raw_signal, target)
        if matched is None:
            rejected_count += 1
            continue
        if not matched.get("matched_target") or not _optional_string(matched.get("match_evidence_span")):
            rejected_count += 1
            continue
        accepted_signals.append(matched)

    buckets = bucket_social_signals(accepted_signals)
    buckets.rejected_count += rejected_count
    return buckets


class CountTargetSignals:
    def __call__(self, buckets: BucketSocialSignals | Mapping[str, Any]) -> int:
        return count_target_signals(buckets)


def count_target_signals(buckets: BucketSocialSignals | Mapping[str, Any]) -> int:
    count = 0
    for signal in _iter_bucket_signals(buckets):
        if signal.get("matched_target") is not True:
            continue
        if not _optional_string(signal.get("match_evidence_span")):
            continue
        count += 1
    return count


class HasTextEvidence:
    def __call__(self, data: Any) -> bool:
        return has_text_evidence(data)


def has_text_evidence(data: Any) -> bool:
    if isinstance(data, Mapping):
        for key in _TEXT_EVIDENCE_KEYS:
            if _optional_string(data.get(key)):
                return True
        for signal in _iter_bucket_signals(data):
            if has_text_evidence(signal):
                return True
        return False
    if isinstance(data, BucketSocialSignals):
        return any(has_text_evidence(signal) for signal in _iter_bucket_signals(data))
    return False


def compute_social_evidence_strength(buckets: BucketSocialSignals | Mapping[str, Any]) -> dict[str, Any]:
    target_signal_count = count_target_signals(buckets)
    has_text = has_text_evidence(buckets)
    return {
        "target_signal_count": target_signal_count,
        "has_text_evidence": has_text,
        "social_judgment_allowed": target_signal_count > 0 and has_text,
    }


def _normalize_target(target: SocialTargetProfile) -> SocialTargetProfile:
    ticker = normalize_ticker(target.ticker)
    aliases = [alias for alias in target.approved_aliases if _optional_string(alias)]
    return SocialTargetProfile(
        ticker=ticker,
        company_name=target.company_name,
        approved_aliases=aliases,
        industry=target.industry,
    )


def _normalize_signal_type(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    return _SIGNAL_TYPE_ALIASES.get(text.strip().lower())


def _match_target_span(*, raw_signal: Mapping[str, Any], target: SocialTargetProfile) -> str:
    code6 = target.ticker.split(".", 1)[0]
    tokens = (code6, target.ticker, target.company_name, *target.approved_aliases)

    haystacks = [_build_match_text(raw_signal)]
    for key in _MATCH_TEXT_KEYS:
        value = raw_signal.get(key)
        if isinstance(value, Mapping):
            haystacks.append(_build_match_text(value))
        elif value is not None:
            haystacks.append(str(value))

    for token in tokens:
        token_text = _optional_string(token)
        if token_text is None:
            continue
        for haystack in haystacks:
            span = _find_match_span(haystack, token_text)
            if span:
                return span
    return ""


def _build_match_text(raw_signal: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in _MATCH_TEXT_KEYS:
        value = raw_signal.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            parts.append(_build_match_text(value))
            continue
        parts.append(str(value))
    return " ".join(part for part in parts if part)


def _find_match_span(text: str, token: str) -> str:
    if not text or not token:
        return ""
    if token.isdigit() and len(token) == 6:
        pattern = re.compile(rf"(?<![0-9A-Z]){re.escape(token)}(?![0-9A-Z])", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return match.group(0)
        return ""
    index = text.find(token)
    if index < 0:
        return ""
    return text[index : index + len(token)]


def _build_signal_id(raw_signal: Mapping[str, Any], ticker: str, signal_type: str) -> str:
    material = "|".join(
        [
            _optional_string(raw_signal.get("provider")) or "",
            _optional_string(raw_signal.get("endpoint")) or "",
            ticker,
            signal_type,
            _optional_string(raw_signal.get("observed_at")) or "",
            _optional_string(raw_signal.get("keyword"))
            or _optional_string(raw_signal.get("related_ticker"))
            or _optional_string(raw_signal.get("heat_value"))
            or _optional_string(raw_signal.get("rank"))
            or "",
        ]
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _resolve_source_platform(raw_signal: Mapping[str, Any]) -> str:
    source_platform = (_optional_string(raw_signal.get("source_platform")) or "").lower()
    if source_platform in {"eastmoney", "akshare", "bocha", "jina", "tavily", "alphaear_news_source_list", "xueqiu_guba"}:
        return source_platform
    provider = (_optional_string(raw_signal.get("provider")) or "").lower()
    if "akshare" in provider or "eastmoney" in provider:
        return "eastmoney"
    return "akshare"


def _normalize_related_ticker(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    if "." in text:
        try:
            return normalize_ticker(text)
        except Exception:  # noqa: BLE001
            return text
    code_match = _CODE_RE.search(text)
    if code_match is None:
        return text
    code = code_match.group(1)
    if len(code) == 5:
        return f"{code}.HK"
    exchange = "SH" if code.startswith("6") else "SZ"
    return f"{code}.{exchange}"


def _to_int_or_none(value: Any) -> int | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value: Any) -> float | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _iter_bucket_signals(buckets: BucketSocialSignals | Mapping[str, Any]) -> list[dict[str, Any]]:
    if isinstance(buckets, BucketSocialSignals):
        return [
            *buckets.attention_signals,
            *buckets.topic_keyword_signals,
            *buckets.related_symbol_signals,
            *buckets.narrative_signals,
        ]
    if not isinstance(buckets, Mapping):
        return []

    output: list[dict[str, Any]] = []
    for key in ("attention_signals", "topic_keyword_signals", "related_symbol_signals", "narrative_signals"):
        bucket = buckets.get(key)
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if isinstance(item, Mapping):
                output.append(dict(item))
    return output


def _is_search_narrative_signal(signal: Mapping[str, Any]) -> bool:
    provider = (_optional_string(signal.get("provider")) or "").lower()
    endpoint = (_optional_string(signal.get("endpoint")) or "").lower()
    if any(marker in provider for marker in _SEARCH_PROVIDER_MARKERS):
        return True
    if any(marker in endpoint for marker in _SEARCH_ENDPOINT_MARKERS):
        return True
    return False


def _has_narrative_source_link(signal: Mapping[str, Any]) -> bool:
    return any(_optional_string(signal.get(key)) for key in _NARRATIVE_SOURCE_LINK_KEYS)


__all__ = [
    "BucketSocialSignals",
    "CountTargetSignals",
    "HasTextEvidence",
    "SocialTargetProfile",
    "bucket_social_signals",
    "compute_social_evidence_strength",
    "count_target_signals",
    "has_text_evidence",
    "match_and_bucket_social_signals",
    "match_social_signal",
]
