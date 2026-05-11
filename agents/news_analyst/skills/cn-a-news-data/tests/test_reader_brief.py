from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from models import (  # noqa: E402
    BriefInput,
    KeywordObservations,
    NewsItem,
    ProviderAttempt,
    Quality,
    QueryPlan,
)
from reader_brief import ReaderBriefBuilder  # noqa: E402


def _build_query_plan() -> QueryPlan:
    return QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519", "贵州茅台"],
        industry_keywords=["白酒"],
        macro_keywords=["消费"],
    )


def _build_quality(
    *,
    status: str = "complete",
    company_count: int = 1,
    industry_count: int = 0,
    macro_count: int = 0,
    total_raw_count: int = 6,
    after_dedup_count: int = 4,
    accepted_count: int = 2,
    missing_fields: list[str] | None = None,
    directional_judgment_allowed: bool = True,
    warnings: list[str] | None = None,
) -> Quality:
    return Quality(
        status=status,  # type: ignore[arg-type]
        company_direct_news_count=company_count,
        industry_background_count=industry_count,
        policy_macro_count=macro_count,
        total_raw_count=total_raw_count,
        after_dedup_count=after_dedup_count,
        accepted_count=accepted_count,
        missing_fields=[] if missing_fields is None else missing_fields,
        directional_judgment_allowed=directional_judgment_allowed,
        warnings=[] if warnings is None else warnings,
    )


def _build_attempt(
    *,
    provider: str,
    endpoint: str,
    ok: bool,
    raw_count: int,
    accepted_count: int = 0,
    empty_reason: str | None = None,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=provider,
        endpoint=endpoint,
        query=f"provider={provider};endpoint={endpoint}",
        ok=ok,
        elapsed_ms=10,
        raw_count=raw_count,
        accepted_count=accepted_count,
        empty_reason=empty_reason,
        error=None if ok else "provider_error",
        cancelled=False,
    )


def _build_item(
    *,
    index: int,
    title: str | None = None,
    publish_time: str | None = "2026-05-07 10:00:00",
    source: str | None = "来源A",
    match_type: str = "ticker_exact",
    match_evidence_span: str = "600519",
    evidence_gap: str | None = None,
) -> NewsItem:
    actual_title = title if title is not None else f"新闻标题-{index}"
    return NewsItem(
        news_id=f"news-{index}",
        title=actual_title,
        summary="摘要",
        source=source,
        publish_time=publish_time,
        url=f"https://example.com/{index}",
        data_source="akshare.stock_news_em",
        matched_keywords=["600519"],
        match_type=match_type,  # type: ignore[arg-type]
        match_evidence_span=match_evidence_span,
        match_confidence="high",
        bucket="company_news",
        keyword_observations=KeywordObservations(
            matched_terms=[],
            keyword_categories=[],
            method="keyword_match",
            is_sentiment_judgment=False,
        ),
        source_fetch_time="2026-05-07T10:01:00+08:00",
        content_hash=f"hash-{index}",
        is_primary_source=True,
        merged_from=[],
        evidence_gap=evidence_gap,
    )


def test_build_complete_contains_required_counts_and_provider_lines() -> None:
    brief_input = BriefInput(
        quality=_build_quality(
            status="complete",
            company_count=2,
            industry_count=1,
            macro_count=1,
            total_raw_count=12,
            after_dedup_count=8,
            accepted_count=4,
        ),
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=7, accepted_count=3),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="timeout",
            ),
        ],
        items=[_build_item(index=1)],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert "本资料包覆盖 2026-05-01 至 2026-05-07。" in brief
    assert "共获取 12 条原始新闻，去重后 8 条，接受 4 条。" in brief
    assert "公司直连新闻 2 条，行业背景 1 条，政策/宏观背景 1 条。" in brief
    assert "akshare.stock_news_em 返回 7 条，接受 3 条。" in brief
    assert "akshare.stock_info_global_cls 未形成可用结果，原因：timeout。" in brief


def test_build_partial_includes_directional_limit_statement() -> None:
    brief_input = BriefInput(
        quality=_build_quality(
            status="partial",
            company_count=0,
            industry_count=1,
            macro_count=1,
            total_raw_count=6,
            after_dedup_count=4,
            accepted_count=2,
            directional_judgment_allowed=False,
        ),
        provider_attempts=[_build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=6, accepted_count=2)],
        items=[_build_item(index=1)],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert "不可用于公司方向性新闻判断" in brief


def test_build_failed_with_empty_items_only_contains_failure_provider_and_gaps() -> None:
    brief_input = BriefInput(
        quality=_build_quality(
            status="failed",
            company_count=0,
            industry_count=0,
            macro_count=0,
            total_raw_count=0,
            after_dedup_count=0,
            accepted_count=0,
            missing_fields=["company_name", "industry"],
            directional_judgment_allowed=False,
            warnings=["P0 数据源均失败，不能形成正式新闻判断资料包。"],
        ),
        provider_attempts=[
            _build_attempt(
                provider="akshare",
                endpoint="stock_news_em",
                ok=False,
                raw_count=0,
                empty_reason="provider_error",
            ),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="timeout",
            ),
        ],
        items=[],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert "未形成正式新闻判断材料" in brief
    assert "P0 数据源均失败" in brief
    assert "akshare.stock_news_em 未形成可用结果，原因：provider_error。" in brief
    assert "资料缺口字段：company_name, industry。" in brief
    assert "匹配原因：" not in brief
    assert "本资料包覆盖" not in brief


def test_build_writes_missing_publish_time_and_source_without_fabrication() -> None:
    brief_input = BriefInput(
        quality=_build_quality(status="complete", company_count=2, accepted_count=2),
        provider_attempts=[_build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=3, accepted_count=2)],
        items=[
            _build_item(index=1, publish_time=None, source="来源A", match_type="ticker_exact", match_evidence_span="600519"),
            _build_item(index=2, publish_time="2026-05-07 11:00:00", source=" ", match_type="approved_alias", match_evidence_span="茅台"),
        ],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert "发布时间缺失" in brief
    assert "来源字段缺失" in brief
    assert "新闻标题-1；匹配原因：ticker_exact/600519" in brief
    assert "新闻标题-2；匹配原因：approved_alias/茅台" in brief


def test_build_timeline_uses_input_order_and_caps_at_twenty_items() -> None:
    items = [_build_item(index=i, title=f"T{i:02d}") for i in range(25)]
    brief_input = BriefInput(
        quality=_build_quality(status="complete", company_count=25, total_raw_count=25, after_dedup_count=25, accepted_count=25),
        provider_attempts=[_build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=25, accepted_count=25)],
        items=items,
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert "T00" in brief
    assert "T19" in brief
    assert "T20" not in brief


def test_build_brief_length_does_not_exceed_4000() -> None:
    long_title = "长标题" * 220
    items = [_build_item(index=i, title=f"{i:02d}-{long_title}") for i in range(20)]
    brief_input = BriefInput(
        quality=_build_quality(
            status="complete",
            company_count=20,
            industry_count=0,
            macro_count=0,
            total_raw_count=200,
            after_dedup_count=180,
            accepted_count=20,
        ),
        provider_attempts=[_build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=200, accepted_count=20)],
        items=items,
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert len(brief) <= 4000
    assert "已截断" in brief


def test_build_partial_with_long_warnings_keeps_directional_limit_under_4000() -> None:
    long_warning = "过长告警" * 1200
    brief_input = BriefInput(
        quality=_build_quality(
            status="partial",
            company_count=0,
            industry_count=2,
            macro_count=1,
            total_raw_count=120,
            after_dedup_count=80,
            accepted_count=12,
            directional_judgment_allowed=False,
            warnings=[long_warning],
        ),
        provider_attempts=[_build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=120, accepted_count=12)],
        items=[_build_item(index=1)],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert len(brief) <= 4000
    assert "不可用于公司方向性新闻判断" in brief


def test_build_failed_with_long_warnings_keeps_failure_limit_under_4000() -> None:
    long_warning = "失败告警" * 1200
    brief_input = BriefInput(
        quality=_build_quality(
            status="failed",
            company_count=0,
            industry_count=0,
            macro_count=0,
            total_raw_count=0,
            after_dedup_count=0,
            accepted_count=0,
            missing_fields=["company_name", "industry"],
            directional_judgment_allowed=False,
            warnings=[long_warning],
        ),
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=False, raw_count=0, empty_reason="provider_error")
        ],
        items=[],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    assert len(brief) <= 4000
    assert "未形成正式新闻判断材料" in brief
    assert "匹配原因：" not in brief


def test_build_brief_avoids_directional_conclusion_terms_in_normal_case() -> None:
    brief_input = BriefInput(
        quality=_build_quality(status="complete", company_count=1, industry_count=0, macro_count=0, accepted_count=1),
        provider_attempts=[_build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=2, accepted_count=1)],
        items=[_build_item(index=1)],
        query_plan=_build_query_plan(),
    )

    brief = ReaderBriefBuilder().build(brief_input)

    forbidden_terms = ["买入", "卖出", "看涨", "看跌", "目标价", "增持", "减持"]
    assert all(term not in brief for term in forbidden_terms)
