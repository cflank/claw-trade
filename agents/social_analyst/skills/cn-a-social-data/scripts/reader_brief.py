from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

SOCIAL_BRIEF_INPUT_INVALID = "SOCIAL_BRIEF_INPUT_INVALID"
SOCIAL_BRIEF_FORBIDDEN_CLAIM = "SOCIAL_BRIEF_FORBIDDEN_CLAIM"

_LIMITED_BRIEF_TEXT = "本摘要仅保留数据统计、来源与缺口信息，其他结论已限制输出。"
_FORBIDDEN_CLAIM_TERMS = (
    "买入",
    "卖出",
    "持有",
    "利好",
    "利空",
    "看多",
    "看空",
    "建仓",
    "清仓",
    "原帖",
    "KOL",
    "用户观点",
    "散户机构分歧",
)

_MATCH_TYPE_LABELS = {
    "exact_ticker": "代码精确匹配",
    "exchange_ticker": "交易所代码匹配",
    "company_name": "公司名匹配",
    "approved_alias": "已批准简称匹配",
    "provider_target_symbol": "目标代码参数匹配",
    "related_symbol_only": "相关标的线索",
    "industry_or_topic_only": "行业或主题线索",
    "unmatched": "未命中",
}


class SocialBriefError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReaderBriefInput:
    profile: Any
    attempts: tuple[Any, ...]
    buckets: Any
    quality: Any
    date_window: Any


@dataclass(frozen=True)
class ReaderBrief:
    text: str
    included_counts: dict[str, int]
    included_sources: tuple[str, ...]
    limitation_codes: tuple[str, ...]


def build_reader_brief(input: ReaderBriefInput) -> ReaderBrief:
    if input.profile is None or input.quality is None:
        raise SocialBriefError(
            SOCIAL_BRIEF_INPUT_INVALID,
            "reader_brief 输入缺少 profile 或 quality",
        )
    quality_status = _safe_text(input.quality, "status")
    if quality_status not in {"complete", "partial", "failed"}:
        raise SocialBriefError(
            SOCIAL_BRIEF_INPUT_INVALID,
            f"reader_brief 质量状态无效: {quality_status or 'missing'}",
        )

    attempts = tuple(input.attempts or ())
    attention_count = _bucket_len(input.buckets, "attention_signals")
    keyword_count = _bucket_len(input.buckets, "topic_keyword_signals")
    related_count = _bucket_len(input.buckets, "related_symbol_signals")
    narrative_count = _bucket_len(input.buckets, "narrative_signals")
    accepted_count = attention_count + keyword_count + related_count + narrative_count

    provider_total = len(attempts)
    success_count = sum(
        1
        for attempt in attempts
        if _safe_bool(attempt, "ok") and _safe_text(attempt, "status") == "success"
    )
    empty_count = sum(1 for attempt in attempts if _safe_text(attempt, "status") == "empty")
    failed_count = sum(
        1
        for attempt in attempts
        if _safe_text(attempt, "status") in {"error", "timeout", "cancelled"}
        or (not _safe_bool(attempt, "ok") and _safe_text(attempt, "status") != "empty")
    )
    cache_hit_count = sum(1 for attempt in attempts if _safe_text(attempt, "cache_status") == "hit")
    live_count = sum(
        1
        for attempt in attempts
        if _safe_text(attempt, "cache_status") in {"miss", "stale", "schema_invalid"}
    )
    sources = _collect_sources(input)
    match_reason_text = _build_match_reason_summary(input)
    signal_time_text = _build_signal_time_summary(input)
    gaps = _collect_gap_reasons(input)

    counts = {
        "provider_total": provider_total,
        "provider_success": success_count,
        "provider_empty": empty_count,
        "provider_failed": failed_count,
        "cache_hit": cache_hit_count,
        "cache_live": live_count,
        "attention_signals": attention_count,
        "topic_keyword_signals": keyword_count,
        "related_symbol_signals": related_count,
        "narrative_signals": narrative_count,
        "accepted_signals": accepted_count,
    }
    limitation_codes = _collect_limitation_codes(input)

    brief_text = "".join(
        [
            _build_provider_sentence(counts),
            _build_source_sentence(sources),
            _build_time_sentence(input.date_window, signal_time_text),
            _build_signal_sentence(counts, match_reason_text),
            _build_cache_sentence(counts),
            _build_status_sentence(quality_status),
            _build_gap_sentence(gaps),
        ]
    )
    if _contains_forbidden_claim(brief_text):
        return ReaderBrief(
            text=_LIMITED_BRIEF_TEXT,
            included_counts=counts,
            included_sources=sources,
            limitation_codes=(SOCIAL_BRIEF_FORBIDDEN_CLAIM,),
        )

    return ReaderBrief(
        text=brief_text,
        included_counts=counts,
        included_sources=sources,
        limitation_codes=limitation_codes,
    )


def _build_provider_sentence(counts: Mapping[str, int]) -> str:
    return (
        "本次 social 资料包覆盖 "
        f"{counts['provider_total']} 个 provider endpoint，"
        f"成功 {counts['provider_success']} 个，"
        f"空结果 {counts['provider_empty']} 个，"
        f"失败或超时 {counts['provider_failed']} 个。"
    )


def _build_source_sentence(sources: tuple[str, ...]) -> str:
    if not sources:
        return "未形成可追溯来源列表。"
    return f"来源覆盖：{'、'.join(sources)}。"


def _build_time_sentence(date_window: Any, signal_time_text: str) -> str:
    start_date = _safe_text(date_window, "start_date")
    end_date = _safe_text(date_window, "end_date")
    if start_date or end_date:
        date_text = f"{start_date or '未知'} 至 {end_date or '未知'}"
    else:
        date_text = "未知时间窗口"
    return f"统计窗口：{date_text}；线索时间：{signal_time_text}。"


def _build_signal_sentence(counts: Mapping[str, int], match_reason_text: str) -> str:
    return (
        "目标相关线索："
        f"热度 {counts['attention_signals']} 条，"
        f"关键词 {counts['topic_keyword_signals']} 条，"
        f"相关标的 {counts['related_symbol_signals']} 条，"
        f"事实型叙事 {counts['narrative_signals']} 条；"
        f"匹配原因：{match_reason_text}。"
    )


def _build_cache_sentence(counts: Mapping[str, int]) -> str:
    return (
        "缓存状态："
        f"命中 {counts['cache_hit']} 次，"
        f"真实请求 {counts['cache_live']} 次。"
    )


def _build_status_sentence(quality_status: str) -> str:
    if quality_status == "partial":
        return "证据限制：当前资料仅可用于限制性事实说明，进行社交情绪判断时必须同步披露样本缺口。"
    if quality_status == "failed":
        return "当前资料不足以支持社交情绪判断，只能说明 provider 失败、匹配缺失或证据缺口。"
    return "当前资料满足事实型社交线索汇总条件。"


def _build_gap_sentence(gaps: tuple[str, ...]) -> str:
    if not gaps:
        return "缺口：未发现新增缺口。"
    return f"缺口：{'；'.join(gaps)}。"


def _build_match_reason_summary(input: ReaderBriefInput) -> str:
    reason_counts: dict[str, int] = {}
    for signal in _iter_accepted_signals(input.buckets):
        match_type = _safe_text(signal, "match_type") or "unmatched"
        reason_counts[match_type] = reason_counts.get(match_type, 0) + 1
    if not reason_counts:
        return "无目标匹配线索"
    reason_items = []
    for match_type, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        label = _MATCH_TYPE_LABELS.get(match_type, match_type)
        reason_items.append(f"{label} {count} 条")
    return "，".join(reason_items)


def _build_signal_time_summary(input: ReaderBriefInput) -> str:
    times = []
    for signal in _iter_accepted_signals(input.buckets):
        source_time = _safe_text(signal, "source_time")
        if source_time:
            times.append(source_time)
    if not times:
        return "无可用 source_time"
    ordered = sorted(times)
    return f"{ordered[0]} 至 {ordered[-1]}"


def _collect_gap_reasons(input: ReaderBriefInput) -> tuple[str, ...]:
    gaps: list[str] = []
    seen: set[str] = set()

    for missing_field in _iter_text_list(_safe_value(input.quality, "missing_fields")):
        _append_unique(gaps, seen, f"缺字段:{missing_field}")
    for warning in _iter_values(_safe_value(input.quality, "warnings")):
        code = _safe_text(warning, "code")
        message = _safe_text(warning, "message")
        if code or message:
            _append_unique(gaps, seen, f"{code}:{message}".strip(":"))

    for signal in _iter_values(_safe_value(input.buckets, "rejected_signals")):
        evidence_gap = _safe_text(signal, "evidence_gap")
        if evidence_gap:
            _append_unique(gaps, seen, f"rejected:{evidence_gap}")

    for attempt in _iter_values(input.attempts):
        empty_reason = _safe_text(attempt, "empty_reason")
        if empty_reason:
            _append_unique(gaps, seen, f"attempt:{empty_reason}")
        error_code = _safe_text(_safe_value(attempt, "error"), "code")
        if error_code:
            _append_unique(gaps, seen, f"error:{error_code}")

    if not input.attempts:
        _append_unique(gaps, seen, "provider_attempts_missing")
    return tuple(gaps)


def _collect_sources(input: ReaderBriefInput) -> tuple[str, ...]:
    sources: list[str] = []
    seen: set[str] = set()

    for attempt in _iter_values(input.attempts):
        provider = _safe_text(attempt, "provider")
        endpoint = _safe_text(attempt, "endpoint")
        source = ".".join(part for part in (provider, endpoint) if part)
        if source:
            _append_unique(sources, seen, source)

    for signal in _iter_accepted_signals(input.buckets):
        provider = _safe_text(signal, "provider")
        platform = _safe_text(signal, "platform")
        endpoint = _safe_text(signal, "endpoint")
        source = ".".join(part for part in (provider, platform, endpoint) if part)
        if source:
            _append_unique(sources, seen, source)

    return tuple(sources)


def _collect_limitation_codes(input: ReaderBriefInput) -> tuple[str, ...]:
    codes: list[str] = []
    seen: set[str] = set()
    quality_status = _safe_text(input.quality, "status")
    if quality_status == "partial":
        _append_unique(codes, seen, "QUALITY_PARTIAL")
    if quality_status == "failed":
        _append_unique(codes, seen, "QUALITY_FAILED")
    for reason_code in _iter_text_list(_safe_value(input.quality, "reason_codes")):
        _append_unique(codes, seen, reason_code)
    return tuple(codes)


def _iter_accepted_signals(buckets: Any) -> list[Any]:
    all_signals: list[Any] = []
    for key in (
        "attention_signals",
        "topic_keyword_signals",
        "related_symbol_signals",
        "narrative_signals",
    ):
        all_signals.extend(_iter_values(_safe_value(buckets, key)))
    return all_signals


def _bucket_len(buckets: Any, key: str) -> int:
    return len(_iter_values(_safe_value(buckets, key)))


def _append_unique(items: list[str], seen: set[str], value: str) -> None:
    normalized = value.strip()
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    items.append(normalized)


def _contains_forbidden_claim(text: str) -> bool:
    for token in _FORBIDDEN_CLAIM_TERMS:
        if token in text:
            return True
    return False


def _iter_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _iter_text_list(value: Any) -> list[str]:
    values = _iter_values(value)
    texts: list[str] = []
    for item in values:
        text = item.strip() if isinstance(item, str) else ""
        if text:
            texts.append(text)
    return texts


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
