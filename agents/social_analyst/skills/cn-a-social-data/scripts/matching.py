from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Literal, Mapping
from unicodedata import normalize as unicode_normalize

from rules import load_keyword_categories

MatchType = Literal[
    "exact_ticker",
    "exchange_ticker",
    "company_name",
    "approved_alias",
    "provider_target_symbol",
    "related_symbol_only",
    "industry_or_topic_only",
    "unmatched",
]
SignalType = Literal["keyword", "heat_rank", "related_symbol", "narrative"]

SOCIAL_MATCH_PROFILE_INCOMPLETE = "SOCIAL_MATCH_PROFILE_INCOMPLETE"
SOCIAL_SIGNAL_EVIDENCE_MISSING = "SOCIAL_SIGNAL_EVIDENCE_MISSING"
SOCIAL_SIGNAL_SOURCE_FORBIDDEN = "SOCIAL_SIGNAL_SOURCE_FORBIDDEN"
SOCIAL_SIGNAL_UNMATCHED = "SOCIAL_SIGNAL_UNMATCHED"

_TARGET_MATCH_TYPES = frozenset(
    {
        "exact_ticker",
        "exchange_ticker",
        "company_name",
        "approved_alias",
        "provider_target_symbol",
    }
)

_TARGET_SYMBOL_ENDPOINTS = frozenset(
    {
        "STOCK_HOT_RANK_LATEST_EM",
        "STOCK_HOT_KEYWORD_EM",
        "STOCK_HOT_RANK_RELATE_EM",
    }
)
_RELATED_SOURCE_KIND = "RELATED_SYMBOL"
_HEAT_RANK_SOURCE_KIND = "HEAT_RANK"
_HEAT_KEYWORD_SOURCE_KIND = "HEAT_KEYWORD"
_NARRATIVE_SOURCE_KIND = "NARRATIVE_TEXT"
_FORBIDDEN_SOURCE_ENDPOINTS = frozenset(
    {
        "STOCK_HOT_FOLLOW_XQ",
        "STOCK_HOT_TWEET_XQ",
        "STOCK_HOT_DEAL_XQ",
        "XUEQIU_HEAT",
    }
)
_SOURCE_KIND_TO_SIGNAL_TYPE: dict[str, SignalType] = {
    _HEAT_RANK_SOURCE_KIND: "heat_rank",
    _HEAT_KEYWORD_SOURCE_KIND: "keyword",
    _RELATED_SOURCE_KIND: "related_symbol",
    _NARRATIVE_SOURCE_KIND: "narrative",
}


class SocialMatchingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SocialSignal:
    signal_id: str
    signal_type: SignalType
    platform: str
    provider: str
    endpoint: str
    title: str | None
    keyword: str | None
    symbol: str | None
    value: int | float | None
    rank: int | None
    rank_change: int | None
    source_time: str | None
    source_fetch_time: str
    matched_target: bool
    match_type: MatchType
    match_evidence_span: str | None
    evidence_type: str
    content_hash: str
    raw_payload_ref: str
    raw_index: int
    evidence_gap: str | None


@dataclass
class BucketedSignals:
    attention_signals: list[SocialSignal]
    topic_keyword_signals: list[SocialSignal]
    related_symbol_signals: list[SocialSignal]
    narrative_signals: list[SocialSignal]
    rejected_signals: list[SocialSignal]


def match_deduplicate_and_bucket(rows: list[Any], profile: Any, config: Any) -> BucketedSignals:
    _validate_profile_for_matching(profile)
    buckets = BucketedSignals(
        attention_signals=[],
        topic_keyword_signals=[],
        related_symbol_signals=[],
        narrative_signals=[],
        rejected_signals=[],
    )

    candidates: list[SocialSignal] = []
    for row in rows:
        match_type = classify_match(row, profile)
        signal = _build_signal(row, profile, match_type)

        if not signal.raw_payload_ref or not signal.content_hash:
            buckets.rejected_signals.append(
                _replace_signal(
                    signal,
                    evidence_gap="missing_raw_ref_or_hash",
                )
            )
            continue

        if _is_forbidden_source(row):
            buckets.rejected_signals.append(
                _replace_signal(
                    signal,
                    evidence_gap=SOCIAL_SIGNAL_SOURCE_FORBIDDEN,
                )
            )
            continue

        candidates.append(signal)

    for signal in deduplicate_signals(candidates):
        if signal.match_type in _TARGET_MATCH_TYPES:
            if signal.signal_type == "heat_rank":
                buckets.attention_signals.append(signal)
                continue
            if signal.signal_type == "keyword":
                buckets.topic_keyword_signals.append(signal)
                continue
            if signal.signal_type == "narrative":
                buckets.narrative_signals.append(signal)
                continue
            buckets.rejected_signals.append(
                _replace_signal(signal, evidence_gap="unexpected_target_signal_kind")
            )
            continue

        if signal.match_type == "related_symbol_only":
            buckets.related_symbol_signals.append(signal)
            continue

        buckets.rejected_signals.append(
            _replace_signal(signal, evidence_gap=SOCIAL_SIGNAL_UNMATCHED)
        )

    _sort_and_trim_buckets(buckets, _resolve_bucket_limit(config))
    return buckets


def deduplicate_signals(signals: list[SocialSignal]) -> list[SocialSignal]:
    unique_signals: list[SocialSignal] = []
    seen: set[str] = set()
    for signal in signals:
        dedupe_key = _build_signal_dedupe_key(signal)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique_signals.append(signal)
    return unique_signals


def classify_match(row: Any, profile: Any) -> MatchType:
    text = _build_row_text(row)

    if _is_provider_target_symbol_match(row, profile):
        return "provider_target_symbol"
    if _contains_exact_code(text, _safe_profile_text(profile, "ticker_plain")):
        return "exact_ticker"
    if _contains_exact_exchange_ticker(text, _safe_profile_text(profile, "ticker")):
        return "exchange_ticker"

    company_name = _safe_profile_text(profile, "company_name")
    if company_name and _contains_term(text, company_name):
        return "company_name"

    for alias in _iter_approved_aliases(profile):
        if _contains_term(text, alias):
            return "approved_alias"

    if _safe_row_text(row, "source_kind") == _RELATED_SOURCE_KIND:
        return "related_symbol_only"
    if _contains_industry_or_topic_only(text, profile):
        return "industry_or_topic_only"
    return "unmatched"


def _build_row_text(row: Any) -> str:
    fields = _as_mapping(_safe_row_value(row, "fields"))
    parts = (
        fields.get("symbol"),
        fields.get("code"),
        fields.get("name"),
        fields.get("keyword"),
        fields.get("title"),
    )
    normalized_parts = [_normalize_text(value) for value in parts]
    return " ".join(part for part in normalized_parts if part)


def _is_provider_target_symbol_match(row: Any, profile: Any) -> bool:
    endpoint = _safe_row_text(row, "endpoint")
    if endpoint not in _TARGET_SYMBOL_ENDPOINTS:
        return False
    query = _as_mapping(_safe_row_value(row, "query"))
    query_symbol = _normalize_text(query.get("symbol"))
    profile_symbol = _safe_profile_text(profile, "eastmoney_symbol")
    return bool(query_symbol and profile_symbol and query_symbol == profile_symbol)


def _contains_exact_code(text: str, ticker_plain: str) -> bool:
    if not text or not ticker_plain:
        return False
    pattern = re.compile(rf"(?<![0-9A-Z]){re.escape(ticker_plain)}(?![0-9A-Z])")
    return bool(pattern.search(text))


def _contains_exact_exchange_ticker(text: str, ticker: str) -> bool:
    if not text or not ticker:
        return False
    pattern = re.compile(rf"(?<![0-9A-Z]){re.escape(ticker)}(?![0-9A-Z])")
    return bool(pattern.search(text))


def _contains_industry_or_topic_only(text: str, profile: Any) -> bool:
    if not text:
        return False
    terms = set(_forbidden_industry_or_topic_terms())
    industry = _safe_profile_text(profile, "industry")
    if industry:
        terms.add(industry)
    for term in terms:
        if _contains_term(text, term):
            return True
    return False


@lru_cache(maxsize=1)
def _forbidden_industry_or_topic_terms() -> frozenset[str]:
    categories = load_keyword_categories().get("categories")
    if not isinstance(categories, dict):
        return frozenset()
    terms: set[str] = set()
    for key in ("topic", "forbidden_as_target_match"):
        values = categories.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            normalized = _normalize_text(value)
            if normalized:
                terms.add(normalized)
    return frozenset(terms)


def _contains_term(text: str, term: str) -> bool:
    normalized_text = _normalize_text(text)
    normalized_term = _normalize_text(term)
    if not normalized_text or not normalized_term:
        return False
    return normalized_term in normalized_text


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicode_normalize("NFKC", value).strip()
    if not normalized:
        return ""
    return normalized.upper()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _safe_row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _safe_row_text(row: Any, key: str) -> str:
    return _normalize_text(_safe_row_value(row, key))


def _safe_profile_text(profile: Any, key: str) -> str:
    if isinstance(profile, Mapping):
        return _normalize_text(profile.get(key))
    return _normalize_text(getattr(profile, key, None))


def _iter_approved_aliases(profile: Any) -> tuple[str, ...]:
    aliases_obj = _safe_profile_value(profile, "approved_aliases")
    if not isinstance(aliases_obj, tuple):
        return ()
    aliases: list[str] = []
    for alias_obj in aliases_obj:
        if isinstance(alias_obj, Mapping):
            alias_value = alias_obj.get("alias")
        else:
            alias_value = getattr(alias_obj, "alias", None)
            if alias_value is None:
                alias_value = alias_obj
        alias = _normalize_text(alias_value)
        if alias:
            aliases.append(alias)
    return tuple(aliases)


def _safe_profile_value(profile: Any, key: str) -> Any:
    if isinstance(profile, Mapping):
        return profile.get(key)
    return getattr(profile, key, None)


def _validate_profile_for_matching(profile: Any) -> None:
    ticker_plain = _safe_profile_text(profile, "ticker_plain")
    company_name = _safe_profile_text(profile, "company_name")
    if ticker_plain or company_name:
        return
    raise SocialMatchingError(
        SOCIAL_MATCH_PROFILE_INCOMPLETE,
        "目标画像缺少 ticker_plain 与 company_name",
    )


def _build_signal(row: Any, profile: Any, match_type: MatchType) -> SocialSignal:
    fields = _as_mapping(_safe_row_value(row, "fields"))
    source_kind = _safe_row_text(row, "source_kind")
    signal_type = _SOURCE_KIND_TO_SIGNAL_TYPE.get(source_kind, "narrative")

    symbol = _normalize_optional_text(
        _first_non_empty(
            fields.get("symbol"),
            fields.get("code"),
            fields.get("股票代码"),
            fields.get("证券代码"),
        )
    )
    keyword = _normalize_optional_text(
        _first_non_empty(fields.get("keyword"), fields.get("关键词"), fields.get("topic"))
    )
    title = _normalize_optional_text(
        _first_non_empty(
            fields.get("title"),
            fields.get("name"),
            fields.get("关键词名称"),
        )
    )
    signal = SocialSignal(
        signal_id=_build_signal_id(row, fields=fields, match_type=match_type),
        signal_type=signal_type,
        platform=_normalize_optional_text(_safe_row_value(row, "platform")) or "",
        provider=_normalize_optional_text(_safe_row_value(row, "provider")) or "",
        endpoint=_normalize_optional_text(_safe_row_value(row, "endpoint")) or "",
        title=title,
        keyword=keyword,
        symbol=symbol,
        value=_coerce_float(_first_non_empty(fields.get("value"), fields.get("heat"), fields.get("热度"))),
        rank=_coerce_int(_first_non_empty(fields.get("rank"), fields.get("排名"), fields.get("当前排名"))),
        rank_change=_coerce_int(
            _first_non_empty(
                fields.get("rank_change"),
                fields.get("排名变化"),
                fields.get("排名较昨日变动"),
                fields.get("排名变化值"),
            )
        ),
        source_time=_extract_source_time(fields),
        source_fetch_time=_normalize_optional_text(
            _first_non_empty(
                _safe_row_value(row, "source_fetch_time"),
                _safe_row_value(row, "fetched_at"),
            )
        )
        or "",
        matched_target=match_type in _TARGET_MATCH_TYPES,
        match_type=match_type,
        match_evidence_span=_resolve_match_evidence_span(row, profile, match_type),
        evidence_type="provider_raw_payload_ref",
        content_hash=_normalize_optional_text(
            _first_non_empty(_safe_row_value(row, "content_hash"), _safe_row_value(row, "payload_hash"))
        )
        or "",
        raw_payload_ref=_normalize_optional_text(_safe_row_value(row, "raw_payload_ref")) or "",
        raw_index=_coerce_int(_safe_row_value(row, "raw_index")) or -1,
        evidence_gap=None,
    )
    if signal.source_time is None:
        return _replace_signal(signal, evidence_gap="missing_source_time")
    return signal


def _replace_signal(signal: SocialSignal, *, evidence_gap: str | None) -> SocialSignal:
    return SocialSignal(
        signal_id=signal.signal_id,
        signal_type=signal.signal_type,
        platform=signal.platform,
        provider=signal.provider,
        endpoint=signal.endpoint,
        title=signal.title,
        keyword=signal.keyword,
        symbol=signal.symbol,
        value=signal.value,
        rank=signal.rank,
        rank_change=signal.rank_change,
        source_time=signal.source_time,
        source_fetch_time=signal.source_fetch_time,
        matched_target=signal.matched_target,
        match_type=signal.match_type,
        match_evidence_span=signal.match_evidence_span,
        evidence_type=signal.evidence_type,
        content_hash=signal.content_hash,
        raw_payload_ref=signal.raw_payload_ref,
        raw_index=signal.raw_index,
        evidence_gap=evidence_gap,
    )


def _resolve_match_evidence_span(row: Any, profile: Any, match_type: MatchType) -> str | None:
    text = _build_row_text(row)
    fields = _as_mapping(_safe_row_value(row, "fields"))
    if match_type == "provider_target_symbol":
        return _safe_profile_text(profile, "eastmoney_symbol") or None
    if match_type == "exact_ticker":
        return _safe_profile_text(profile, "ticker_plain") or None
    if match_type == "exchange_ticker":
        return _safe_profile_text(profile, "ticker") or None
    if match_type == "company_name":
        return _safe_profile_text(profile, "company_name") or None
    if match_type == "approved_alias":
        for alias in _iter_approved_aliases(profile):
            if _contains_term(text, alias):
                return alias
        return None
    if match_type == "related_symbol_only":
        return _normalize_optional_text(
            _first_non_empty(fields.get("symbol"), fields.get("code"), fields.get("name"))
        )
    if match_type == "industry_or_topic_only":
        industry = _safe_profile_text(profile, "industry")
        if industry and _contains_term(text, industry):
            return industry
        for term in _forbidden_industry_or_topic_terms():
            if _contains_term(text, term):
                return term
    return None


def _sort_and_trim_buckets(buckets: BucketedSignals, max_signals_per_bucket: int) -> None:
    buckets.attention_signals.sort(
        key=lambda signal: (
            signal.rank is None,
            signal.rank if signal.rank is not None else math.inf,
            signal.value is None,
            -float(signal.value) if signal.value is not None else 0.0,
        )
    )
    buckets.topic_keyword_signals.sort(
        key=lambda signal: (
            signal.value is None,
            -float(signal.value) if signal.value is not None else 0.0,
        )
    )
    buckets.related_symbol_signals.sort(
        key=lambda signal: (
            signal.value is None,
            -float(signal.value) if signal.value is not None else 0.0,
        )
    )
    buckets.narrative_signals.sort(
        key=_narrative_sort_key
    )
    buckets.rejected_signals.sort(
        key=lambda signal: (
            signal.evidence_gap is None,
            signal.evidence_gap or "",
        )
    )

    buckets.attention_signals = buckets.attention_signals[:max_signals_per_bucket]
    buckets.topic_keyword_signals = buckets.topic_keyword_signals[:max_signals_per_bucket]
    buckets.related_symbol_signals = buckets.related_symbol_signals[:max_signals_per_bucket]
    buckets.narrative_signals = buckets.narrative_signals[:max_signals_per_bucket]
    buckets.rejected_signals = buckets.rejected_signals[:max_signals_per_bucket]


def _build_signal_dedupe_key(signal: SocialSignal) -> str:
    canonical = json.dumps(
        {
            "provider": signal.provider,
            "endpoint": signal.endpoint,
            "signal_type": signal.signal_type,
            "keyword": signal.keyword,
            "symbol": signal.symbol,
            "rank": signal.rank,
            "value": signal.value,
            "source_time": signal.source_time,
            "content_hash": signal.content_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _build_signal_id(row: Any, *, fields: Mapping[str, Any], match_type: MatchType) -> str:
    canonical = json.dumps(
        {
            "provider": _normalize_optional_text(_safe_row_value(row, "provider")),
            "endpoint": _normalize_optional_text(_safe_row_value(row, "endpoint")),
            "raw_index": _coerce_int(_safe_row_value(row, "raw_index")),
            "payload_hash": _normalize_optional_text(_safe_row_value(row, "payload_hash")),
            "source_kind": _normalize_optional_text(_safe_row_value(row, "source_kind")),
            "match_type": match_type,
            "fields": dict(fields),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"signal:{digest[:16]}"


def _resolve_bucket_limit(config: Any) -> int:
    if isinstance(config, Mapping):
        value = config.get("max_signals_per_bucket")
    else:
        value = getattr(config, "max_signals_per_bucket", None)
    if not isinstance(value, int) or value <= 0:
        return 50
    return value


def _is_forbidden_source(row: Any) -> bool:
    endpoint = _safe_row_text(row, "endpoint")
    return endpoint in _FORBIDDEN_SOURCE_ENDPOINTS


def _extract_source_time(fields: Mapping[str, Any]) -> str | None:
    candidate = _normalize_optional_text(
        _first_non_empty(
            fields.get("source_time"),
            fields.get("time"),
            fields.get("datetime"),
            fields.get("as_of_date"),
            fields.get("date"),
            fields.get("日期"),
            fields.get("更新时间"),
        )
    )
    if candidate is None:
        return None
    return candidate


def _parse_source_time_epoch(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{normalized}T00:00:00+00:00")
        except ValueError:
            return None
        return parsed.timestamp()


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return None


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    return normalized


def _narrative_sort_key(signal: SocialSignal) -> tuple[bool, float]:
    timestamp = _parse_source_time_epoch(signal.source_time)
    if timestamp is None:
        return True, 0.0
    return False, -timestamp


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value)
    text = _normalize_optional_text(str(value))
    if text is None:
        return None
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return None
        return number
    text = _normalize_optional_text(str(value))
    if text is None:
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return None
    if math.isnan(number):
        return None
    return number
