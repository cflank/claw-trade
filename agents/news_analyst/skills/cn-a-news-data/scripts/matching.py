from __future__ import annotations

from config_loader import load_alias_conflict_blacklist, load_cn_a_news_config, load_keyword_rules
from models import KeywordRule, MatchResult, NewsDataPackRequest, QueryPlan, RawNewsItem
from observability import record_metric, trace_span

_MACRO_CATEGORIES: frozenset[str] = frozenset({"政策", "宏观消费"})
_ANNOUNCEMENT_SOURCE_HINTS: tuple[str, ...] = ("announcement", "anns_d", "公告")
_ANNOUNCEMENT_AUDIT_SUBJECT_PREFIXES: tuple[str, ...] = ("- ts_code:", "- name:")


def _normalize_non_empty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _unique_keep_order(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _build_alias_blacklist() -> set[str]:
    config = load_cn_a_news_config()
    conflicts = load_alias_conflict_blacklist(config.alias_conflict_blacklist_path)
    blacklist: set[str] = set()
    for conflict in conflicts:
        alias = _normalize_non_empty_str(conflict.alias)
        if alias is not None:
            blacklist.add(alias)
    return blacklist


def _collect_macro_keywords(keyword_rules: list[KeywordRule]) -> list[str]:
    macro_terms: list[str] = []
    for rule in keyword_rules:
        if rule.category not in _MACRO_CATEGORIES:
            continue
        normalized_term = _normalize_non_empty_str(rule.term)
        if normalized_term is None:
            continue
        macro_terms.append(normalized_term)
    return _unique_keep_order(macro_terms)


def build_query_plan(request: NewsDataPackRequest) -> QueryPlan:
    config = load_cn_a_news_config()
    keyword_rules = load_keyword_rules(config.keyword_rules_path)
    alias_blacklist = _build_alias_blacklist()

    company_keywords: list[str] = []
    for fixed_value in (request.ticker, request.exchange_ticker, request.company_name):
        normalized_value = _normalize_non_empty_str(fixed_value)
        if normalized_value is not None:
            company_keywords.append(normalized_value)

    for alias in [*request.approved_aliases, *request.approved_historical_names]:
        normalized_alias = _normalize_non_empty_str(alias)
        if normalized_alias is None:
            continue
        if normalized_alias in alias_blacklist:
            continue
        company_keywords.append(normalized_alias)

    normalized_industry = _normalize_non_empty_str(request.industry)
    industry_keywords = [] if normalized_industry is None else [normalized_industry]

    return QueryPlan(
        ticker=request.ticker,
        exchange_ticker=request.exchange_ticker,
        company_name=request.company_name,
        industry=request.industry,
        start_date=request.start_date,
        end_date=request.end_date,
        company_keywords=_unique_keep_order(company_keywords),
        industry_keywords=industry_keywords,
        macro_keywords=_collect_macro_keywords(keyword_rules),
    )


class QueryPlanBuilder:
    def build(self, request: NewsDataPackRequest) -> QueryPlan:
        return build_query_plan(request)


def span_from_text(text: str | None, keyword: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    normalized_keyword = _normalize_non_empty_str(keyword)
    if normalized_keyword is None:
        return None
    match_index = text.find(normalized_keyword)
    if match_index < 0:
        return None
    return text[match_index : match_index + len(normalized_keyword)]


def _contains_keyword_in_item(item: RawNewsItem, keyword: str) -> str | None:
    title_span = span_from_text(item.title, keyword)
    if title_span is not None:
        return title_span
    return span_from_text(item.summary, keyword)


def _is_announcement_subject_line(line: str) -> bool:
    normalized_line = _normalize_non_empty_str(line)
    if normalized_line is None:
        return False
    lowered = normalized_line.lower()
    return any(lowered.startswith(prefix) for prefix in _ANNOUNCEMENT_AUDIT_SUBJECT_PREFIXES)


def _contains_keyword_in_announcement_non_subject_summary(item: RawNewsItem, keyword: str) -> str | None:
    if not isinstance(item.summary, str):
        return None
    for line in item.summary.splitlines():
        if _is_announcement_subject_line(line):
            continue
        evidence_span = span_from_text(line, keyword)
        if evidence_span is not None:
            return evidence_span
    return None


def _contains_keyword_in_primary_text(item: RawNewsItem, keyword: str) -> str | None:
    title_span = span_from_text(item.title, keyword)
    if title_span is not None:
        return title_span
    if _is_announcement_item(item):
        return _contains_keyword_in_announcement_non_subject_summary(item, keyword)
    return span_from_text(item.summary, keyword)


def _contains_keyword_in_announcement_subject(item: RawNewsItem, keyword: str) -> str | None:
    if not _is_announcement_item(item):
        return None
    if not isinstance(item.summary, str):
        return None
    for line in item.summary.splitlines():
        normalized_line = _normalize_non_empty_str(line)
        if normalized_line is None:
            continue
        lowered = normalized_line.lower()
        for prefix in _ANNOUNCEMENT_AUDIT_SUBJECT_PREFIXES:
            if not lowered.startswith(prefix):
                continue
            subject_value = _normalize_non_empty_str(normalized_line[len(prefix) :])
            if subject_value is None:
                continue
            evidence_span = span_from_text(subject_value, keyword)
            if evidence_span is not None:
                return evidence_span
    return None


def _is_announcement_item(item: RawNewsItem) -> bool:
    data_source = item.data_source.lower()
    return any(hint in data_source for hint in _ANNOUNCEMENT_SOURCE_HINTS)


def _company_or_announcement_bucket(item: RawNewsItem) -> str:
    if _is_announcement_item(item):
        return "announcements"
    return "company_news"


def _iter_alias_keywords(query_plan: QueryPlan) -> list[str]:
    excluded_keywords: set[str] = {query_plan.ticker, query_plan.exchange_ticker}
    normalized_company_name = _normalize_non_empty_str(query_plan.company_name)
    if normalized_company_name is not None:
        excluded_keywords.add(normalized_company_name)

    alias_keywords: list[str] = []
    for keyword in query_plan.company_keywords:
        if keyword in excluded_keywords:
            continue
        alias_keywords.append(keyword)
    return alias_keywords


class MatchEngine:
    def classify(self, item: RawNewsItem, query_plan: QueryPlan) -> MatchResult:
        with trace_span("news_data_pack.match"):
            for keyword in (query_plan.ticker, query_plan.exchange_ticker):
                evidence_span = _contains_keyword_in_primary_text(item, keyword)
                if evidence_span is None:
                    continue
                return _record_match_observability(
                    MatchResult(
                        match_type="ticker_exact",
                        match_evidence_span=evidence_span,
                        match_confidence="high",
                        bucket=_company_or_announcement_bucket(item),
                        matched_keywords=[keyword],
                    )
                )

            company_name = _normalize_non_empty_str(query_plan.company_name)
            if company_name is not None:
                evidence_span = _contains_keyword_in_primary_text(item, company_name)
                if evidence_span is not None:
                    return _record_match_observability(
                        MatchResult(
                            match_type="company_full_name",
                            match_evidence_span=evidence_span,
                            match_confidence="high",
                            bucket=_company_or_announcement_bucket(item),
                            matched_keywords=[company_name],
                        )
                    )

            for keyword in _iter_alias_keywords(query_plan):
                evidence_span = _contains_keyword_in_primary_text(item, keyword)
                if evidence_span is None:
                    continue
                return _record_match_observability(
                    MatchResult(
                        match_type="approved_alias",
                        match_evidence_span=evidence_span,
                        match_confidence="medium",
                        bucket=_company_or_announcement_bucket(item),
                        matched_keywords=[keyword],
                    )
                )

            if _is_announcement_item(item):
                for keyword in query_plan.company_keywords:
                    evidence_span = _contains_keyword_in_announcement_subject(item, keyword)
                    if evidence_span is None:
                        continue
                    return _record_match_observability(
                        MatchResult(
                            match_type="announcement_subject",
                            match_evidence_span=evidence_span,
                            match_confidence="high",
                            bucket="announcements",
                            matched_keywords=[keyword],
                        )
                    )

            for keyword in query_plan.industry_keywords:
                evidence_span = _contains_keyword_in_item(item, keyword)
                if evidence_span is None:
                    continue
                return _record_match_observability(
                    MatchResult(
                        match_type="industry_keyword",
                        match_evidence_span=evidence_span,
                        match_confidence="medium",
                        bucket="industry_news",
                        matched_keywords=[keyword],
                    )
                )

            for keyword in query_plan.macro_keywords:
                evidence_span = _contains_keyword_in_item(item, keyword)
                if evidence_span is None:
                    continue
                return _record_match_observability(
                    MatchResult(
                        match_type="macro_keyword",
                        match_evidence_span=evidence_span,
                        match_confidence="low",
                        bucket="policy_macro_news",
                        matched_keywords=[keyword],
                    )
                )

            return _record_match_observability(
                MatchResult(
                    match_type="unknown",
                    match_evidence_span=None,
                    match_confidence="low",
                    bucket="rejected",
                    matched_keywords=[],
                )
            )


def _record_match_observability(result: MatchResult) -> MatchResult:
    record_metric("news_match_bucket_total", labels={"bucket": result.bucket}, value=1)
    record_metric("news_match_type_total", labels={"match_type": result.match_type}, value=1)
    if result.bucket == "rejected":
        record_metric("news_match_rejected_total", labels=None, value=1)
    return result
