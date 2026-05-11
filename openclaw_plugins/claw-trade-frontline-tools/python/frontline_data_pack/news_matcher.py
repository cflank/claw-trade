from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import NewsTargetProfile, NewsToolInput
from .security import normalize_ticker


_SEARCH_PROVIDERS = frozenset({"bocha", "tavily", "jina", "newsnow", "minimax"})
_ANNOUNCEMENT_ENDPOINT_HINTS = ("anns_d", "announcement")
_ANNOUNCEMENT_FIELD_NAMES = ("announcement_subject", "subject", "notice_subject")
_MACRO_KEYWORDS = (
    "宏观",
    "经济",
    "央行",
    "货币",
    "利率",
    "通胀",
    "财政",
    "国债",
    "人民币",
    "政策",
    "监管",
    "PMI",
    "CPI",
    "PPI",
    "GDP",
    "社融",
)


@dataclass(frozen=True)
class DeduplicateByTitleUrlTime:
    def deduplicate(self, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            key = (
                _normalize_for_key(item.get("title")),
                _normalize_for_key(item.get("url")),
                _normalize_for_key(item.get("publish_time")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(dict(item))
        return deduped


def resolve_news_target_profile(
    tool_input: NewsToolInput | Mapping[str, Any],
    approved_artifact_refs: Iterable[Any] | None = None,
) -> NewsTargetProfile:
    _ = approved_artifact_refs
    payload = _coerce_news_tool_input(tool_input)
    ticker = normalize_ticker(_require_non_empty_string(payload.get("ticker"), field_name="ticker"))
    company_name = _require_non_empty_string(payload.get("company_name"), field_name="company_name")
    industry = _optional_string(payload.get("industry"))

    aliases = _read_aliases(payload.get("approved_aliases"))
    aliases = _deduplicate_preserve_order(aliases)
    aliases = [alias for alias in aliases if alias not in {company_name, ticker.split(".", 1)[0]}]

    return NewsTargetProfile(
        ticker=ticker,
        company_name=company_name,
        approved_aliases=aliases,
        industry=industry,
    )


def classify_news_item(
    raw_item: Mapping[str, Any],
    profile: NewsTargetProfile,
) -> dict[str, Any]:
    title = _optional_string(raw_item.get("title")) or ""
    summary = _optional_string(raw_item.get("summary")) or _optional_string(raw_item.get("content"))
    source = _optional_string(raw_item.get("source")) or "unknown"
    publish_time = _optional_string(raw_item.get("publish_time"))
    url = _optional_string(raw_item.get("url"))
    provider = _optional_string(raw_item.get("provider")) or "unknown"
    endpoint = _optional_string(raw_item.get("endpoint")) or "unknown"
    raw_payload_ref = _optional_string(raw_item.get("raw_payload_ref")) or ""
    payload_hash = _read_payload_hash(raw_item)
    news_id = _build_news_id(title=title, url=url, publish_time=publish_time, provider=provider)

    bucket = "rejected"
    match_type = "industry_keyword"
    match_evidence_span = ""

    company_match = _match_company_hard_rules(raw_item=raw_item, title=title, summary=summary, profile=profile)
    if company_match is not None:
        bucket, match_type, match_evidence_span = company_match
    else:
        industry_match = _match_industry(raw_item=raw_item, title=title, summary=summary, profile=profile)
        if industry_match is not None:
            bucket, match_type, match_evidence_span = industry_match

    if bucket == "company_news":
        if _is_search_provider(provider=provider, endpoint=endpoint) and not url:
            bucket = "rejected"
            match_evidence_span = ""
        if not match_evidence_span:
            bucket = "rejected"

    return {
        "news_id": news_id,
        "title": title,
        "summary": summary,
        "source": source,
        "publish_time": publish_time,
        "url": url,
        "bucket": bucket,
        "match_type": match_type,
        "match_evidence_span": match_evidence_span,
        "provider": provider,
        "endpoint": endpoint,
        "raw_payload_ref": raw_payload_ref,
        "payload_hash": payload_hash,
    }


def classify_news_items(
    raw_items: Iterable[Mapping[str, Any]],
    profile: NewsTargetProfile,
) -> dict[str, Any]:
    company_news: list[dict[str, Any]] = []
    industry_macro: list[dict[str, Any]] = []
    announcements: list[dict[str, Any]] = []
    rejected_count = 0

    for raw_item in raw_items:
        classified = classify_news_item(raw_item, profile)
        bucket = classified.get("bucket")

        if bucket == "company_news":
            if not _optional_string(classified.get("match_evidence_span")):
                rejected_count += 1
                continue
            company_news.append(classified)
            continue
        if bucket == "industry_macro":
            industry_macro.append(classified)
            continue
        if bucket == "announcement":
            announcements.append(classified)
            continue

        rejected_count += 1

    return {
        "company_news": company_news,
        "industry_macro": industry_macro,
        "announcement": announcements,
        "rejected_count": rejected_count,
    }


def deduplicate_by_title_url_time(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return DeduplicateByTitleUrlTime().deduplicate(items)


def _coerce_news_tool_input(tool_input: NewsToolInput | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(tool_input, NewsToolInput):
        return {
            "ticker": tool_input.ticker,
            "market": tool_input.market,
            "company_name": tool_input.company_name,
            "industry": tool_input.industry,
            "start_date": tool_input.start_date,
            "end_date": tool_input.end_date,
            "aliases": tool_input.aliases,
            "approved_aliases": tool_input.approved_aliases,
            "approved_artifact_refs": tool_input.approved_artifact_refs,
        }
    if not isinstance(tool_input, Mapping):
        raise TypeError("tool_input must be NewsToolInput or mapping")
    return dict(tool_input)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _require_non_empty_string(value: Any, *, field_name: str) -> str:
    normalized = _optional_string(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required")
    return normalized


def _read_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    aliases: list[str] = []
    for item in value:
        alias = _optional_string(item)
        if alias is None:
            continue
        aliases.append(alias)
    return aliases


def _deduplicate_preserve_order(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _contains_span(text: str | None, needle: str | None) -> str:
    if text is None:
        return ""
    if needle is None:
        return ""
    index = text.find(needle)
    if index < 0:
        return ""
    return text[index : index + len(needle)]


def _match_company_hard_rules(
    *,
    raw_item: Mapping[str, Any],
    title: str,
    summary: str | None,
    profile: NewsTargetProfile,
) -> tuple[str, str, str] | None:
    allow_summary_for_company_match = not _is_search_provider(
        provider=_optional_string(raw_item.get("provider")) or "",
        endpoint=_optional_string(raw_item.get("endpoint")) or "",
    )
    ticker_code = profile.ticker.split(".", 1)[0]
    for code_token in (ticker_code, profile.ticker):
        span = _contains_span(title, code_token)
        if not span and allow_summary_for_company_match:
            span = _contains_span(summary, code_token)
        if span:
            return ("company_news", "code", span)

    span = _contains_span(title, profile.company_name)
    if not span and allow_summary_for_company_match:
        span = _contains_span(summary, profile.company_name)
    if span:
        return ("company_news", "company_name", span)

    for alias in profile.approved_aliases:
        span = _contains_span(title, alias)
        if not span and allow_summary_for_company_match:
            span = _contains_span(summary, alias)
        if span:
            return ("company_news", "approved_alias", span)

    for field_name in _ANNOUNCEMENT_FIELD_NAMES:
        subject = _optional_string(raw_item.get(field_name))
        span = _contains_span(subject, profile.company_name)
        if not span:
            span = _contains_span(subject, ticker_code)
        if not span:
            for alias in profile.approved_aliases:
                span = _contains_span(subject, alias)
                if span:
                    break
        if span:
            return ("announcement", "announcement_subject", span)

    if _looks_like_announcement(
        provider=_optional_string(raw_item.get("provider")) or "",
        endpoint=_optional_string(raw_item.get("endpoint")) or "",
    ):
        for token in (profile.company_name, ticker_code, *profile.approved_aliases):
            span = _contains_span(title, token) or _contains_span(summary, token)
            if span:
                return ("announcement", "announcement_subject", span)

    return None


def _match_industry(
    *,
    raw_item: Mapping[str, Any],
    title: str,
    summary: str | None,
    profile: NewsTargetProfile,
) -> tuple[str, str, str] | None:
    if profile.industry is None:
        return None
    span = _contains_span(title, profile.industry)
    if not span:
        span = _contains_span(summary, profile.industry)
    if not span:
        for keyword in _MACRO_KEYWORDS:
            span = _contains_span(title, keyword)
            if not span:
                span = _contains_span(summary, keyword)
            if span:
                break
    if not span:
        return None
    return ("industry_macro", "industry_keyword", span)


def _looks_like_announcement(*, provider: str, endpoint: str) -> bool:
    text = f"{provider.lower()}::{endpoint.lower()}"
    return any(hint in text for hint in _ANNOUNCEMENT_ENDPOINT_HINTS)


def _is_search_provider(*, provider: str, endpoint: str) -> bool:
    provider_normalized = provider.strip().lower()
    endpoint_normalized = endpoint.strip().lower()
    if provider_normalized in _SEARCH_PROVIDERS:
        return True
    if "search" in endpoint_normalized:
        return True
    return False


def _build_news_id(*, title: str, url: str | None, publish_time: str | None, provider: str) -> str:
    raw = f"{title}|{url or ''}|{publish_time or ''}|{provider}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_payload_hash(raw_item: Mapping[str, Any]) -> str:
    existing = _optional_string(raw_item.get("payload_hash"))
    if existing and re.fullmatch(r"sha256:[0-9a-f]{64}", existing):
        return existing
    serialized = json.dumps(dict(raw_item), ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_for_key(value: Any) -> str:
    text = _optional_string(value)
    if text is None:
        return ""
    return text


__all__ = [
    "DeduplicateByTitleUrlTime",
    "classify_news_item",
    "classify_news_items",
    "deduplicate_by_title_url_time",
    "resolve_news_target_profile",
]
