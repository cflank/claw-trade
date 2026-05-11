from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dedup import Deduplicator, SortTrimProcessor, title_similarity  # noqa: E402
from models import KeywordObservations, NewsDataPackRequest, NewsItem  # noqa: E402


def _build_news_item(
    news_id: str,
    *,
    title: str,
    bucket: str = "company_news",
    source: str | None = "来源A",
    publish_time: str | None = "2026-05-07 10:00:00",
    url: str | None = None,
    merged_from: list[str] | None = None,
    evidence_gap: str | None = None,
) -> NewsItem:
    return NewsItem(
        news_id=news_id,
        title=title,
        summary="摘要",
        source=source,
        publish_time=publish_time,
        url=url,
        data_source="akshare.stock_news_em",
        matched_keywords=["600519"],
        match_type="ticker_exact",
        match_evidence_span="600519",
        match_confidence="high",
        bucket=bucket,
        keyword_observations=KeywordObservations(
            matched_terms=[],
            keyword_categories=[],
            method="keyword_match",
            is_sentiment_judgment=False,
        ),
        source_fetch_time="2026-05-07T10:01:00+08:00",
        content_hash=f"hash-{news_id}",
        is_primary_source=True,
        merged_from=[] if merged_from is None else merged_from,
        evidence_gap=evidence_gap,
    )


def _build_request(
    *,
    start_date: str = "2026-05-01",
    end_date: str = "2026-05-07",
) -> NewsDataPackRequest:
    return NewsDataPackRequest(
        ticker="600519",
        exchange_ticker="SH600519",
        market="CN_A",
        company_name="贵州茅台",
        industry="白酒",
        start_date=start_date,
        end_date=end_date,
        approved_aliases=["茅台"],
        approved_historical_names=[],
        profile_missing_fields=[],
    )


def test_deduplicate_merges_same_url_with_url_exact_reason_and_keeps_merged_from() -> None:
    first = _build_news_item(
        "raw-1",
        title="贵州茅台渠道反馈更新",
        url="HTTPS://Example.com/news/1?a=1&b=2",
        merged_from=["legacy-1"],
    )
    second = _build_news_item(
        "raw-2",
        title="无关标题",
        url="https://example.com/news/1?b=2&a=1",
        merged_from=["legacy-2"],
    )

    deduped, stats = Deduplicator().deduplicate([first, second])

    assert [item.news_id for item in deduped] == ["raw-1"]
    assert deduped[0].merged_from == ["legacy-1", "raw-2", "legacy-2"]
    assert stats.removed_count == 1
    assert stats.merged_raw_ids == ["raw-2"]
    assert stats.merge_reasons == ["url_exact"]


def test_deduplicate_merges_normalized_title_with_title_exact_reason() -> None:
    first = _build_news_item("raw-1", title="贵州茅台 发布经营简报", source="来源A", publish_time="2026-05-07")
    second = _build_news_item("raw-2", title="  贵州茅台 发布经营简报  ", source="来源B", publish_time="2026-05-06")

    deduped, stats = Deduplicator().deduplicate([first, second])

    assert [item.news_id for item in deduped] == ["raw-1"]
    assert deduped[0].merged_from == ["raw-2"]
    assert stats.removed_count == 1
    assert stats.merged_raw_ids == ["raw-2"]
    assert stats.merge_reasons == ["title_exact"]


def test_deduplicate_merges_title_source_time_bucket_with_title_source_time_reason() -> None:
    first = _build_news_item("raw-1", title="贵州茅台 发布公告", source="中证网", publish_time="2026-05-07 10:00:00")
    second = _build_news_item("raw-2", title="贵州茅台发布公告", source="中证网", publish_time="2026-05-07T11:30:00+08:00")

    deduped, stats = Deduplicator().deduplicate([first, second])

    assert [item.news_id for item in deduped] == ["raw-1"]
    assert deduped[0].merged_from == ["raw-2"]
    assert stats.removed_count == 1
    assert stats.merged_raw_ids == ["raw-2"]
    assert stats.merge_reasons == ["title_source_time"]


def test_deduplicate_merges_similar_titles_above_default_threshold() -> None:
    first = _build_news_item(
        "raw-1",
        title="贵州茅台发布2026年第一季度经营数据公告，渠道库存保持稳定",
        source="来源A",
        publish_time="2026-05-07 09:00:00",
    )
    second = _build_news_item(
        "raw-2",
        title="贵州茅台发布2026年第一季度经营数据公告，渠道库存维持稳定",
        source="来源B",
        publish_time="2026-05-08 09:00:00",
    )

    assert title_similarity(first.title, second.title) > 0.92

    deduped, stats = Deduplicator().deduplicate([first, second])

    assert [item.news_id for item in deduped] == ["raw-1"]
    assert deduped[0].merged_from == ["raw-2"]
    assert stats.removed_count == 1
    assert stats.merged_raw_ids == ["raw-2"]
    assert stats.merge_reasons == ["title_similarity"]


def test_deduplicate_keeps_items_when_title_similarity_below_threshold() -> None:
    first = _build_news_item(
        "raw-1",
        title="贵州茅台发布2026年第一季度经营数据公告，渠道库存保持稳定",
        source="来源A",
        publish_time="2026-05-07 09:00:00",
    )
    second = _build_news_item(
        "raw-2",
        title="贵州茅台发布2026年第一季度经营数据公告，渠道库存明显下降",
        source="来源B",
        publish_time="2026-05-08 09:00:00",
    )

    assert title_similarity(first.title, second.title) < 0.92

    deduped, stats = Deduplicator().deduplicate([first, second])

    assert [item.news_id for item in deduped] == ["raw-1", "raw-2"]
    assert stats.removed_count == 0
    assert stats.merged_raw_ids == []
    assert stats.merge_reasons == []


def test_deduplicate_keeps_company_news_when_background_url_duplicate_appears_first() -> None:
    background = _build_news_item(
        "raw-bg",
        title="行业渠道库存观察",
        bucket="industry_news",
        url="https://example.com/news/dup-1",
    )
    company = _build_news_item(
        "raw-company",
        title="贵州茅台渠道库存观察",
        bucket="company_news",
        url="https://example.com/news/dup-1",
    )

    deduped, stats = Deduplicator().deduplicate([background, company])

    assert [item.news_id for item in deduped] == ["raw-bg", "raw-company"]
    assert stats.removed_count == 0
    assert stats.merged_raw_ids == []
    assert stats.merge_reasons == []


def test_deduplicate_keeps_company_news_when_background_title_duplicate_appears_first() -> None:
    background = _build_news_item(
        "raw-bg",
        title="贵州茅台发布经营简报",
        bucket="policy_macro_news",
        source="来源A",
        publish_time="2026-05-07",
    )
    company = _build_news_item(
        "raw-company",
        title="  贵州茅台发布经营简报  ",
        bucket="company_news",
        source="来源B",
        publish_time="2026-05-08",
    )

    deduped, stats = Deduplicator().deduplicate([background, company])

    assert [item.news_id for item in deduped] == ["raw-bg", "raw-company"]
    assert stats.removed_count == 0
    assert stats.merged_raw_ids == []
    assert stats.merge_reasons == []


def test_deduplicate_keeps_company_first_semantics_when_background_duplicate_comes_later() -> None:
    company = _build_news_item(
        "raw-company",
        title="贵州茅台公告",
        bucket="company_news",
        url="https://example.com/news/keep-company",
    )
    background = _build_news_item(
        "raw-bg",
        title="行业摘要",
        bucket="industry_news",
        url="https://example.com/news/keep-company",
    )

    deduped, stats = Deduplicator().deduplicate([company, background])

    assert [item.news_id for item in deduped] == ["raw-company"]
    assert deduped[0].merged_from == ["raw-bg"]
    assert stats.removed_count == 1
    assert stats.merged_raw_ids == ["raw-bg"]
    assert stats.merge_reasons == ["url_exact"]


def test_deduplicate_title_similarity_respects_max_candidate_limit_without_dropping_input() -> None:
    first = _build_news_item("raw-1", title="甲公司公告一季度产能调整计划")
    second = _build_news_item("raw-2", title="乙公司公告一季度经营数据稳步增长")
    third = _build_news_item("raw-3", title="乙公司公告1季度经营数据稳步增长")

    assert title_similarity(second.title, third.title) > 0.92
    assert title_similarity(first.title, third.title) < 0.92

    deduped, stats = Deduplicator(title_similarity_max_candidates=1).deduplicate([first, second, third])

    assert [item.news_id for item in deduped] == ["raw-1", "raw-2", "raw-3"]
    assert stats.removed_count == 0
    assert stats.merged_raw_ids == []
    assert stats.merge_reasons == []


def test_sort_and_trim_filters_out_items_outside_closed_window() -> None:
    in_window = _build_news_item("raw-in", title="窗口内", publish_time="2026-05-05 09:00:00")
    out_window = _build_news_item("raw-out", title="窗口外", publish_time="2026-04-20 09:00:00")
    request = _build_request(start_date="2026-05-01", end_date="2026-05-07")

    output = SortTrimProcessor().sort_and_trim([in_window, out_window], request)

    assert [item.news_id for item in output] == ["raw-in"]


def test_sort_and_trim_keeps_missing_publish_time_and_appends_evidence_gap() -> None:
    missing_time = _build_news_item(
        "raw-missing",
        title="发布时间缺失新闻",
        publish_time=None,
        evidence_gap="source_missing",
    )
    request = _build_request()

    output = SortTrimProcessor().sort_and_trim([missing_time], request)

    assert [item.news_id for item in output] == ["raw-missing"]
    assert output[0].evidence_gap == "source_missing,publish_time_missing"


def test_sort_and_trim_caps_json_items_to_100_by_default() -> None:
    request = _build_request(start_date="2026-05-01", end_date="2026-05-07")
    items = [
        _build_news_item(
            f"raw-{index}",
            title=f"新闻{index}",
            publish_time=f"2026-05-07 10:{index % 60:02d}:00",
        )
        for index in range(120)
    ]

    output = SortTrimProcessor().sort_and_trim(items, request)

    assert len(output) == 100


def test_sort_and_trim_does_not_modify_request_date_window() -> None:
    request = _build_request(start_date="2026-05-01", end_date="2026-05-07")
    original_start_date = request.start_date
    original_end_date = request.end_date

    _ = SortTrimProcessor().sort_and_trim(
        [_build_news_item("raw-in", title="窗口内", publish_time="2026-05-07 09:00:00")],
        request,
    )

    assert request.start_date == original_start_date
    assert request.end_date == original_end_date


def test_sort_and_trim_orders_by_bucket_priority_then_publish_time_desc_with_missing_last() -> None:
    request = _build_request(start_date="2026-05-01", end_date="2026-05-07")
    company_new = _build_news_item("company-new", title="公司新", bucket="company_news", publish_time="2026-05-07 10:00:00")
    company_old = _build_news_item("company-old", title="公司旧", bucket="company_news", publish_time="2026-05-06 10:00:00")
    company_missing = _build_news_item("company-missing", title="公司缺失", bucket="company_news", publish_time="invalid-time")
    announcement = _build_news_item("announcement", title="公告", bucket="announcements", publish_time="2026-05-07 09:00:00")
    industry_item = _build_news_item("industry", title="行业", bucket="industry_news", publish_time="2026-05-07 08:00:00")
    macro_missing = _build_news_item("macro-missing", title="宏观缺失", bucket="policy_macro_news", publish_time=None)

    output = SortTrimProcessor().sort_and_trim(
        [company_old, macro_missing, industry_item, company_missing, announcement, company_new],
        request,
    )

    assert [item.news_id for item in output] == [
        "company-new",
        "company-old",
        "company-missing",
        "announcement",
        "industry",
        "macro-missing",
    ]
    assert output[2].evidence_gap == "publish_time_missing"
    assert output[5].evidence_gap == "publish_time_missing"
