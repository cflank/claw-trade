from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.news_matcher import (  # noqa: E402
    DeduplicateByTitleUrlTime,
    classify_news_item,
    classify_news_items,
    deduplicate_by_title_url_time,
    resolve_news_target_profile,
)


def test_t_news_001_classify_title_with_600519_into_company_news() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "aliases": ["茅台"],
        }
    )
    item = classify_news_item(
        {
            "title": "600519 盘后公告发布",
            "summary": "公司披露季度经营情况",
            "source": "财联社",
            "publish_time": "2026-05-08T10:00:00",
            "url": "https://example.com/news/600519",
            "provider": "akshare",
            "endpoint": "stock_news_em",
            "raw_payload_ref": "viking://raw/news-1.json",
        },
        profile,
    )

    assert profile.ticker == "600519.SH"
    assert item["bucket"] == "company_news"
    assert item["match_type"] == "code"
    assert item["match_evidence_span"] == "600519"


def test_t_news_001_industry_keyword_only_must_not_enter_company_news() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519.SH",
            "company_name": "贵州茅台",
            "industry": "白酒",
        }
    )
    item = classify_news_item(
        {
            "title": "白酒行业景气度回升",
            "summary": "行业库存去化持续推进",
            "source": "财联社",
            "publish_time": "2026-05-08T10:00:00",
            "url": "https://example.com/news/industry",
            "provider": "akshare",
            "endpoint": "stock_info_global_cls",
        },
        profile,
    )

    assert item["bucket"] != "company_news"
    assert item["bucket"] == "industry_macro"
    assert item["match_type"] == "industry_keyword"


def test_t_news_001_missing_match_evidence_span_is_counted_as_rejected() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519",
            "company_name": "贵州茅台",
            "industry": "白酒",
        }
    )
    buckets = classify_news_items(
        [
            {
                "title": "公司公告：经营数据更新",
                "summary": "该条没有目标公司硬匹配",
                "source": "财联社",
                "publish_time": "2026-05-08T10:00:00",
                "url": "https://example.com/news/no-match",
                "provider": "akshare",
                "endpoint": "stock_news_em",
            }
        ],
        profile,
    )

    assert buckets["company_news"] == []
    assert buckets["rejected_count"] == 1


def test_t_news_001_deduplicate_by_title_url_publish_time_keeps_one() -> None:
    duplicated = [
        {
            "title": "600519 盘后公告发布",
            "url": "https://example.com/news/dup",
            "publish_time": "2026-05-08T10:00:00",
            "bucket": "company_news",
        },
        {
            "title": "600519 盘后公告发布",
            "url": "https://example.com/news/dup",
            "publish_time": "2026-05-08T10:00:00",
            "bucket": "company_news",
        },
    ]

    assert len(deduplicate_by_title_url_time(duplicated)) == 1
    assert len(DeduplicateByTitleUrlTime().deduplicate(duplicated)) == 1


def test_t_news_001_search_provider_summary_without_url_must_not_be_accepted_company_news() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519.SH",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "aliases": ["茅台"],
        }
    )
    item = classify_news_item(
        {
            "title": "贵州茅台迎来新品发布",
            "summary": "搜索摘要文本，不带原文链接",
            "source": "search-api",
            "publish_time": "2026-05-08T10:00:00",
            "url": None,
            "provider": "bocha",
            "endpoint": "cn_web_search",
        },
        profile,
    )
    buckets = classify_news_items([item], profile)

    assert item["bucket"] == "rejected"
    assert buckets["company_news"] == []
    assert buckets["rejected_count"] == 1


def test_t_news_001_search_provider_summary_with_url_and_no_title_target_must_not_enter_company_news() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519.SH",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "aliases": ["茅台"],
        }
    )
    item = classify_news_item(
        {
            "title": "消费板块出现资金轮动",
            "summary": "搜索摘要出现贵州茅台字样但无标题硬命中",
            "source": "search-api",
            "publish_time": "2026-05-08T10:00:00",
            "url": "https://example.com/search/hit",
            "provider": "bocha",
            "endpoint": "cn_web_search",
        },
        profile,
    )
    buckets = classify_news_items([item], profile)

    assert item["bucket"] == "rejected"
    assert buckets["company_news"] == []
    assert buckets["rejected_count"] == 1


def test_t_news_002_structured_approved_aliases_allow_company_news_but_ref_strings_are_not_aliases() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "approved_aliases": ["茅台股份"],
            "approved_artifact_refs": ["viking://runs/1/artifacts/contains-飞天茅台"],
        }
    )
    matched = classify_news_item(
        {
            "title": "茅台股份发布新品进展",
            "summary": "公司动态",
            "source": "财联社",
            "publish_time": "2026-05-08T10:10:00",
            "url": "https://example.com/news/alias-hit",
            "provider": "akshare",
            "endpoint": "stock_news_em",
        },
        profile,
    )
    not_matched = classify_news_item(
        {
            "title": "飞天茅台线下活动",
            "summary": "仅出现在 artifact ref 字符串，不在结构化 alias",
            "source": "财联社",
            "publish_time": "2026-05-08T10:11:00",
            "url": "https://example.com/news/ref-only",
            "provider": "akshare",
            "endpoint": "stock_news_em",
        },
        profile,
    )

    assert matched["bucket"] == "company_news"
    assert matched["match_type"] == "approved_alias"
    assert not_matched["bucket"] == "rejected"
    assert profile.approved_aliases == ["茅台股份"]


def test_t_news_002_aliases_only_must_not_be_treated_as_approved_alias_for_company_news() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "aliases": ["茅台"],
            "approved_aliases": [],
        }
    )
    item = classify_news_item(
        {
            "title": "茅台发布渠道策略调整",
            "summary": "公司动态",
            "source": "财联社",
            "publish_time": "2026-05-08T10:10:00",
            "url": "https://example.com/news/alias-only",
            "provider": "akshare",
            "endpoint": "stock_news_em",
        },
        profile,
    )

    assert profile.approved_aliases == []
    assert item["bucket"] == "rejected"


def test_t_news_002_news_id_includes_provider() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519",
            "company_name": "贵州茅台",
            "industry": "白酒",
        }
    )
    common_fields = {
        "title": "白酒行业观察",
        "summary": "宏观背景",
        "source": "资讯源",
        "publish_time": "2026-05-08T10:00:00",
        "url": "https://example.com/news/same",
        "endpoint": "stock_info_global_cls",
    }
    akshare_item = classify_news_item({**common_fields, "provider": "akshare"}, profile)
    bocha_item = classify_news_item({**common_fields, "provider": "bocha"}, profile)

    assert akshare_item["news_id"] != bocha_item["news_id"]


def test_t_news_001_output_is_json_serializable() -> None:
    profile = resolve_news_target_profile(
        {
            "ticker": "600519",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "aliases": ["茅台"],
            "approved_aliases": ["茅台股份"],
            "approved_artifact_refs": ["viking://runs/1/artifacts/news-profile"],
        }
    )
    buckets = classify_news_items(
        [
            {
                "title": "600519 盘后公告发布",
                "summary": "公司披露季度经营情况",
                "source": "财联社",
                "publish_time": "2026-05-08T10:00:00",
                "url": "https://example.com/news/600519",
                "provider": "akshare",
                "endpoint": "stock_news_em",
                "raw_payload_ref": "viking://raw/news-serializable.json",
            }
        ],
        profile,
    )

    serialized = json.dumps(buckets, ensure_ascii=False)
    assert "company_news" in serialized
    assert profile.approved_aliases == ["茅台股份"]
