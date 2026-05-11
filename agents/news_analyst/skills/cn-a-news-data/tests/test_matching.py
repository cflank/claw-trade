from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from matching import MatchEngine, QueryPlanBuilder, build_query_plan, span_from_text  # noqa: E402
from models import NewsDataPackRequest, QueryPlan, RawNewsItem  # noqa: E402


def _write_keyword_rules(path: Path) -> str:
    payload = """
version: "1"
rules:
  - term: "稳增长"
    category: "政策"
    markets: ["CN_A"]
    enabled: true
  - term: "消费复苏"
    category: "宏观消费"
    markets: ["CN_A"]
    enabled: true
  - term: "业绩快报"
    category: "财报"
    markets: ["CN_A"]
    enabled: true
  - term: "稳增长"
    category: "政策"
    markets: ["CN_A"]
    enabled: true
  - term: "逆周期调节"
    category: "政策"
    markets: ["CN_A"]
    enabled: false
"""
    path.write_text(payload.strip() + "\n", encoding="utf-8")
    return str(path)


def _write_alias_rules(path: Path) -> str:
    payload = """
version: "1"
aliases: []
conflicts:
  - alias: "平安"
    blocked_terms: []
"""
    path.write_text(payload.strip() + "\n", encoding="utf-8")
    return str(path)


def _build_request(*, industry: str | None = "白酒") -> NewsDataPackRequest:
    return NewsDataPackRequest(
        ticker="600519",
        exchange_ticker="600519.SH",
        market="CN_A",
        company_name="贵州茅台",
        industry=industry,
        start_date="2026-05-01",
        end_date="2026-05-07",
        approved_aliases=["茅台", "平安", "茅台", "600519"],
        approved_historical_names=["贵州茅台酒股份", "平安", " "],
        profile_missing_fields=[],
    )


def test_query_plan_builder_includes_required_company_keywords_and_filters_blacklisted_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyword_rules_path = _write_keyword_rules(tmp_path / "keyword_categories.yaml")
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml")
    monkeypatch.setenv("CN_A_NEWS_KEYWORD_RULES_PATH", keyword_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)

    plan = QueryPlanBuilder().build(_build_request())

    assert plan.company_keywords == ["600519", "600519.SH", "贵州茅台", "茅台", "贵州茅台酒股份"]
    assert "平安" not in plan.company_keywords


def test_build_query_plan_returns_empty_industry_keywords_when_industry_is_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyword_rules_path = _write_keyword_rules(tmp_path / "keyword_categories.yaml")
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml")
    monkeypatch.setenv("CN_A_NEWS_KEYWORD_RULES_PATH", keyword_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)

    plan = build_query_plan(_build_request(industry=" "))

    assert plan.industry_keywords == []


def test_build_query_plan_reads_cn_a_macro_keywords_from_validated_config_and_keeps_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyword_rules_path = _write_keyword_rules(tmp_path / "keyword_categories.yaml")
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml")
    monkeypatch.setenv("CN_A_NEWS_KEYWORD_RULES_PATH", keyword_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)

    plan = build_query_plan(_build_request())

    assert plan.macro_keywords == ["稳增长", "消费复苏"]


def test_build_query_plan_is_deterministic_for_same_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyword_rules_path = _write_keyword_rules(tmp_path / "keyword_categories.yaml")
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml")
    monkeypatch.setenv("CN_A_NEWS_KEYWORD_RULES_PATH", keyword_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)

    request = _build_request()
    first = build_query_plan(request)
    second = build_query_plan(request)

    assert first == second


def _build_query_plan_for_match() -> QueryPlan:
    return QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519", "600519.SH", "贵州茅台", "茅台", "贵州茅台酒股份"],
        industry_keywords=["白酒"],
        macro_keywords=["稳增长", "消费复苏"],
    )


def _build_raw_item(
    *,
    title: str,
    summary: str | None = None,
    data_source: str = "akshare.stock_news_em",
) -> RawNewsItem:
    return RawNewsItem(
        raw_id="raw-1",
        title=title,
        summary=summary,
        source="src",
        publish_time="2026-05-06T10:00:00Z",
        url="https://example.com/news/1",
        data_source=data_source,
        raw_payload_ref=None,
        source_fetch_time="2026-05-06T10:05:00Z",
    )


def test_classify_ticker_match_returns_company_news_with_exact_evidence() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="600519 今日成交放量", summary="市场关注度提升")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "ticker_exact"
    assert result.match_evidence_span == "600519"
    assert result.match_confidence == "high"
    assert result.bucket == "company_news"
    assert result.matched_keywords == ["600519"]


def test_classify_ticker_match_has_priority_over_exchange_ticker_match() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="盘中异动", summary="600519.SH 相关交易活跃")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "ticker_exact"
    assert result.match_evidence_span == "600519"
    assert result.match_confidence == "high"
    assert result.bucket == "company_news"
    assert result.matched_keywords == ["600519"]


def test_classify_company_full_name_match_returns_company_news() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="贵州茅台发布经营简报", summary="披露阶段性经营情况")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "company_full_name"
    assert result.match_evidence_span == "贵州茅台"
    assert result.match_confidence == "high"
    assert result.bucket == "company_news"
    assert result.matched_keywords == ["贵州茅台"]


def test_classify_approved_alias_match_returns_company_news() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="茅台渠道反馈更新", summary="终端动销有跟踪")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "approved_alias"
    assert result.match_evidence_span == "茅台"
    assert result.match_confidence == "medium"
    assert result.bucket == "company_news"
    assert result.matched_keywords == ["茅台"]


def test_classify_announcement_source_ticker_match_enters_announcements_bucket() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(
        title="600519 关于董事会决议公告",
        summary="公司披露董事会审议事项",
        data_source="tushare.anns_d",
    )

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "ticker_exact"
    assert result.match_evidence_span == "600519"
    assert result.match_confidence == "high"
    assert result.bucket == "announcements"
    assert result.matched_keywords == ["600519"]


def test_classify_announcement_subject_from_tushare_audit_summary_is_reachable() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(
        title="关于董事会决议公告",
        summary="可审计字段:\n- ts_code: 600519.SH\n- name: 贵州茅台",
        data_source="tushare.anns_d",
    )

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "announcement_subject"
    assert result.match_evidence_span == "600519"
    assert result.match_confidence == "high"
    assert result.bucket == "announcements"
    assert result.matched_keywords == ["600519"]


def test_classify_announcement_regular_text_still_has_higher_priority_than_subject_fields() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(
        title="公告摘要",
        summary="贵州茅台披露董事会审议事项\n可审计字段:\n- ts_code: 123456.SH\n- name: 无关主体",
        data_source="tushare.anns_d",
    )

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "company_full_name"
    assert result.match_evidence_span == "贵州茅台"
    assert result.match_confidence == "high"
    assert result.bucket == "announcements"
    assert result.matched_keywords == ["贵州茅台"]


def test_classify_industry_keyword_match_goes_to_industry_news_only() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="渠道数据更新", summary="白酒板块阶段性承压")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "industry_keyword"
    assert result.match_evidence_span == "白酒"
    assert result.match_confidence == "medium"
    assert result.bucket == "industry_news"
    assert result.bucket != "company_news"
    assert result.matched_keywords == ["白酒"]


def test_classify_macro_keyword_match_goes_to_policy_macro_news() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="稳增长政策继续推进", summary="多部门发布配套安排")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "macro_keyword"
    assert result.match_evidence_span == "稳增长"
    assert result.match_confidence == "low"
    assert result.bucket == "policy_macro_news"
    assert result.matched_keywords == ["稳增长"]


def test_classify_returns_rejected_when_no_keyword_matches() -> None:
    plan = _build_query_plan_for_match()
    item = _build_raw_item(title="港口物流动态跟踪", summary="沿海运价波动")

    result = MatchEngine().classify(item, plan)

    assert result.match_type == "unknown"
    assert result.match_evidence_span is None
    assert result.match_confidence == "low"
    assert result.bucket == "rejected"
    assert result.matched_keywords == []


def test_span_from_text_returns_contiguous_original_fragment() -> None:
    text = "摘要：白酒板块阶段性承压。"
    span = span_from_text(text, "白酒")

    assert span == "白酒"
    assert span in text
