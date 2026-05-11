from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from keyword_observations import KeywordObservationExtractor  # noqa: E402
from models import KeywordRule, RawNewsItem  # noqa: E402


def _build_item(title: str, summary: str | None) -> RawNewsItem:
    return RawNewsItem(
        raw_id="raw-1",
        title=title,
        summary=summary,
        source="来源",
        publish_time="2026-05-07T10:00:00+08:00",
        url=None,
        data_source="akshare.stock_news_em",
        raw_payload_ref=None,
        source_fetch_time="2026-05-07T10:01:00+08:00",
    )


def test_extract_keeps_first_hit_order_and_deduplicates_terms_and_categories() -> None:
    item = _build_item(
        title="公司发布分红方案并披露公告",
        summary="公告显示本次分红方案已经董事会审议通过",
    )
    rules = [
        KeywordRule(term="分红", category="分红", markets=["CN_A"]),
        KeywordRule(term="公告", category="公告", markets=["CN_A"]),
        KeywordRule(term="分红方案", category="分红", markets=["CN_A"]),
        KeywordRule(term="分红", category="分红", markets=["CN_A"]),
    ]

    result = KeywordObservationExtractor().extract(item=item, rules=rules)

    assert result.matched_terms == ["分红", "公告", "分红方案"]
    assert result.keyword_categories == ["分红", "公告"]
    assert result.method == "keyword_match"
    assert result.is_sentiment_judgment is False


def test_extract_returns_empty_lists_when_rules_empty() -> None:
    item = _build_item(title="公司发布公告", summary="公告正文")

    result = KeywordObservationExtractor().extract(item=item, rules=[])

    assert result.matched_terms == []
    assert result.keyword_categories == []
    assert result.method == "keyword_match"
    assert result.is_sentiment_judgment is False


def test_extract_returns_empty_lists_when_text_empty() -> None:
    item = SimpleNamespace(title="   ", summary=None)
    rules = [KeywordRule(term="分红", category="分红", markets=["CN_A"])]

    result = KeywordObservationExtractor().extract(item=item, rules=rules)

    assert result.matched_terms == []
    assert result.keyword_categories == []
    assert result.method == "keyword_match"
    assert result.is_sentiment_judgment is False
