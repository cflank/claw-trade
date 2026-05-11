from __future__ import annotations

from models import KeywordObservationResult, KeywordRule, NewsItem, RawNewsItem
from observability import record_metric, trace_span


class KeywordObservationExtractor:
    def extract(self, item: RawNewsItem | NewsItem, rules: list[KeywordRule]) -> KeywordObservationResult:
        with trace_span("news_data_pack.keyword"):
            if not rules:
                return KeywordObservationResult(
                    matched_terms=[],
                    keyword_categories=[],
                    method="keyword_match",
                    is_sentiment_judgment=False,
                )

            title = item.title if isinstance(item.title, str) else ""
            summary = item.summary if isinstance(item.summary, str) else ""
            text = "\n".join(part for part in (title, summary) if part).strip()
            if not text:
                return KeywordObservationResult(
                    matched_terms=[],
                    keyword_categories=[],
                    method="keyword_match",
                    is_sentiment_judgment=False,
                )

            matched_terms: list[str] = []
            keyword_categories: list[str] = []
            seen_terms: set[str] = set()
            seen_categories: set[str] = set()

            for rule in rules:
                if rule.term not in text:
                    continue
                if rule.term not in seen_terms:
                    matched_terms.append(rule.term)
                    seen_terms.add(rule.term)
                if rule.category not in seen_categories:
                    keyword_categories.append(rule.category)
                    seen_categories.add(rule.category)

            for category in keyword_categories:
                record_metric("news_keyword_category_total", labels={"category": category}, value=1)

            return KeywordObservationResult(
                matched_terms=matched_terms,
                keyword_categories=keyword_categories,
                method="keyword_match",
                is_sentiment_judgment=False,
            )
