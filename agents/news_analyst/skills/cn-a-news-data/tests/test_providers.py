from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
from typing import cast

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import providers as providers_module  # noqa: E402
from models import ProviderQuery, QueryPlan  # noqa: E402
from providers import (  # noqa: E402
    AkshareNewsCctvProvider,
    AkshareStockInfoGlobalClsProvider,
    AkshareStockInfoGlobalEmProvider,
    AkshareStockNewsEmProvider,
    NewsProvider,
    ProviderFetchResultFactory,
    TushareAnnouncementsProvider,
    build_provider_query,
    build_raw_news_item,
)


class _LocalProvider(NewsProvider):
    name = "local"
    endpoint = "stock_news_em"
    priority = "P0"

    def __init__(self, raw_table: object) -> None:
        self._raw_table = raw_table

    def _call_remote(self, query: ProviderQuery) -> object:
        return self._raw_table

    def _normalize_rows(self, raw_table: object, query: ProviderQuery):
        rows = cast(list[dict[str, str]], raw_table)
        fetch_time = self.default_source_fetch_time()
        return [
            build_raw_news_item(
                endpoint=query.endpoint,
                raw_id=row.get("raw_id"),
                title=row["title"],
                summary=None,
                source=None,
                publish_time=row.get("publish_time"),
                url=row.get("url"),
                data_source="akshare.stock_news_em",
                raw_payload_ref=None,
                source_fetch_time=fetch_time,
            )
            for row in rows
        ]


class _TimeoutProvider(NewsProvider):
    name = "local"
    endpoint = "stock_news_em"
    priority = "P0"

    def _call_remote(self, query: ProviderQuery) -> object:
        raise TimeoutError("request timed out")

    def _normalize_rows(self, raw_table: object, query: ProviderQuery):
        raise RuntimeError("should not run")


class _StockNewsEmCallSwap:
    def __init__(self, replacement):
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = providers_module.ak.stock_news_em
        providers_module.ak.stock_news_em = self._replacement

    def __exit__(self, exc_type, exc, tb):
        providers_module.ak.stock_news_em = self._original


class _StockInfoGlobalClsCallSwap:
    def __init__(self, replacement):
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = providers_module.ak.stock_info_global_cls
        providers_module.ak.stock_info_global_cls = self._replacement

    def __exit__(self, exc_type, exc, tb):
        providers_module.ak.stock_info_global_cls = self._original


class _StockInfoGlobalEmCallSwap:
    def __init__(self, replacement):
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = providers_module.ak.stock_info_global_em
        providers_module.ak.stock_info_global_em = self._replacement

    def __exit__(self, exc_type, exc, tb):
        providers_module.ak.stock_info_global_em = self._original


class _NewsCctvCallSwap:
    def __init__(self, replacement):
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = providers_module.ak.news_cctv
        providers_module.ak.news_cctv = self._replacement

    def __exit__(self, exc_type, exc, tb):
        providers_module.ak.news_cctv = self._original


class _TokenCarrier:
    def __init__(self, tushare_token: str | None) -> None:
        self.tushare_token = tushare_token


def _query() -> ProviderQuery:
    return ProviderQuery(
        endpoint="stock_news_em",
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        keywords=["茅台"],
        start_date="2026-05-01",
        end_date="2026-05-07",
    )


def test_result_factory_marks_empty_table_as_no_result() -> None:
    result = ProviderFetchResultFactory.empty(
        provider="akshare",
        endpoint="stock_news_em",
        query="symbol=600519",
        elapsed_ms=12,
    )
    assert result.ok is False
    assert result.attempt.empty_reason == "no_result"
    assert result.attempt.raw_count == 0


def test_error_conversion_maps_timeout_without_cancelled() -> None:
    result = ProviderFetchResultFactory.from_exception(
        provider="akshare",
        endpoint="stock_news_em",
        query="symbol=600519",
        elapsed_ms=99,
        error=TimeoutError("remote timeout"),
    )
    assert result.ok is False
    assert result.attempt.empty_reason == "timeout"
    assert result.attempt.cancelled is False


def test_error_conversion_maps_permission_and_sanitizes_error() -> None:
    result = ProviderFetchResultFactory.from_exception(
        provider="tushare",
        endpoint="anns_d",
        query="ts_code=600519.SH",
        elapsed_ms=33,
        error=PermissionError("permission denied token=secret-123"),
    )
    assert result.ok is False
    assert result.attempt.empty_reason == "permission_missing"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_build_raw_news_item_derives_raw_id_deterministically() -> None:
    first = build_raw_news_item(
        endpoint="stock_news_em",
        raw_id=None,
        title="公司公告",
        summary=None,
        source="来源",
        publish_time="2026-05-07 10:00:00",
        url="https://example.com/a",
        data_source="akshare.stock_news_em",
        raw_payload_ref=None,
        source_fetch_time="2026-05-07T10:30:00+08:00",
    )
    second = build_raw_news_item(
        endpoint="stock_news_em",
        raw_id=None,
        title="公司公告",
        summary=None,
        source="来源",
        publish_time="2026-05-07 10:00:00",
        url="https://example.com/a",
        data_source="akshare.stock_news_em",
        raw_payload_ref=None,
        source_fetch_time="2026-05-07T10:30:00+08:00",
    )
    changed = build_raw_news_item(
        endpoint="stock_news_em",
        raw_id=None,
        title="公司公告",
        summary=None,
        source="来源",
        publish_time="2026-05-07 10:00:00",
        url="https://example.com/b",
        data_source="akshare.stock_news_em",
        raw_payload_ref=None,
        source_fetch_time="2026-05-07T10:30:00+08:00",
    )
    assert first.raw_id == second.raw_id
    assert first.raw_id != changed.raw_id


def test_news_provider_fetch_converts_internal_timeout_error_to_failed_result() -> None:
    provider = _TimeoutProvider()
    result = provider.fetch(_query())
    assert result.ok is False
    assert result.attempt.empty_reason == "timeout"
    assert result.attempt.cancelled is False


def test_news_provider_fetch_empty_list_becomes_no_result() -> None:
    provider = _LocalProvider(raw_table=[])
    result = provider.fetch(_query())
    assert result.ok is False
    assert result.attempt.empty_reason == "no_result"
    assert result.attempt.raw_count == 0


def test_build_provider_query_uses_plan_fields_and_keyword_cleanup() -> None:
    plan = QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519"],
        industry_keywords=["白酒"],
        macro_keywords=["消费"],
    )
    query = build_provider_query(
        endpoint="stock_info_global_em",
        query_plan=plan,
        keywords=[" 茅台 ", "茅台", "", "白酒"],
    )
    assert query.endpoint == "stock_info_global_em"
    assert query.ticker == "600519"
    assert query.exchange_ticker == "600519.SH"
    assert query.start_date == "2026-05-01"
    assert query.end_date == "2026-05-07"
    assert query.keywords == ["茅台", "白酒"]


def test_akshare_stock_news_em_normalize_rows_maps_fields() -> None:
    provider = AkshareStockNewsEmProvider()
    raw_table = pd.DataFrame(
        [
            {
                "新闻标题": "贵州茅台回应市场传闻",
                "新闻内容": "公司经营正常",
                "发布时间": "2026-05-07 09:30:00",
                "文章来源": "证券时报",
                "新闻链接": "https://news.example.com/a?token=abc",
                "关键词": "白酒",
            },
            {
                "新闻标题": "行业观点更新",
                "新闻内容": "板块估值修复",
                "发布时间": "2026-05-07 10:00:00",
                "文章来源": "上证报",
                "新闻链接": "javascript:alert(1)",
                "关键词": "消费",
            },
        ]
    )

    result = provider._normalize_rows(raw_table, _query())
    assert len(result) == 2
    assert result[0].title == "贵州茅台回应市场传闻"
    assert result[0].summary is not None
    assert "公司经营正常" in result[0].summary
    assert "关键词：白酒" in result[0].summary
    assert result[0].publish_time == "2026-05-07 09:30:00"
    assert result[0].source == "证券时报"
    assert result[0].url == "https://news.example.com/a?token=%2A%2A%2A"
    assert result[0].data_source == "akshare.stock_news_em"
    assert result[1].url is None


def test_akshare_stock_news_em_fetch_empty_table_returns_no_result() -> None:
    provider = AkshareStockNewsEmProvider()

    def _return_empty(symbol: str):
        assert symbol == "600519"
        return pd.DataFrame()

    with _StockNewsEmCallSwap(_return_empty):
        result = provider.fetch(_query())

    assert result.ok is False
    assert result.attempt.empty_reason == "no_result"
    assert result.attempt.raw_count == 0


def test_akshare_stock_news_em_fetch_missing_title_column_returns_schema_changed() -> None:
    provider = AkshareStockNewsEmProvider()

    def _return_missing_title(symbol: str):
        assert symbol == "600519"
        return pd.DataFrame(
            [
                {
                    "新闻内容": "仅有内容",
                    "发布时间": "2026-05-07 11:00:00",
                    "文章来源": "证券时报",
                    "新闻链接": "https://news.example.com/x",
                }
            ]
        )

    with _StockNewsEmCallSwap(_return_missing_title):
        result = provider.fetch(_query())

    assert result.ok is False
    assert result.attempt.empty_reason == "schema_changed"


def test_akshare_stock_news_em_fetch_provider_error_returns_sanitized_error() -> None:
    provider = AkshareStockNewsEmProvider()

    def _raise_provider_error(symbol: str):
        assert symbol == "600519"
        raise RuntimeError("remote failure token=secret-123")

    with _StockNewsEmCallSwap(_raise_provider_error):
        result = provider.fetch(_query())

    assert result.ok is False
    assert result.attempt.empty_reason == "provider_error"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_akshare_stock_info_global_cls_normalize_rows_merges_date_and_time() -> None:
    provider = AkshareStockInfoGlobalClsProvider()
    raw_table = pd.DataFrame(
        [
            {
                "标题": "茅台盘中波动",
                "内容": "市场关注白酒板块",
                "发布日期": "2026-05-07",
                "发布时间": "09:35:10",
                "来源": "财联社",
            }
        ]
    )

    result = provider._normalize_rows(
        raw_table,
        ProviderQuery(
            endpoint="stock_info_global_cls",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        ),
    )
    assert len(result) == 1
    assert result[0].publish_time == "2026-05-07 09:35:10"
    assert result[0].url is None
    assert result[0].data_source == "akshare.stock_info_global_cls"


def test_akshare_stock_info_global_cls_normalize_rows_keeps_single_time_field() -> None:
    provider = AkshareStockInfoGlobalClsProvider()
    raw_table = pd.DataFrame(
        [
            {
                "标题": "仅有发布日期",
                "内容": "只给日期",
                "发布日期": "2026-05-07",
                "来源": "财联社",
            },
            {
                "标题": "仅有发布时间",
                "内容": "只给时间",
                "发布时间": "09:35:10",
                "来源": "财联社",
            },
        ]
    )

    result = provider._normalize_rows(
        raw_table,
        ProviderQuery(
            endpoint="stock_info_global_cls",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        ),
    )

    assert len(result) == 2
    assert result[0].publish_time == "2026-05-07"
    assert result[1].publish_time == "09:35:10"


def test_akshare_stock_info_global_cls_normalize_rows_uses_default_source_when_missing() -> None:
    provider = AkshareStockInfoGlobalClsProvider()
    raw_table = pd.DataFrame(
        [
            {
                "标题": "茅台盘后公告",
                "内容": "暂无新增重大事项",
                "发布日期": "2026-05-07",
                "发布时间": "18:00:00",
            }
        ]
    )

    result = provider._normalize_rows(
        raw_table,
        ProviderQuery(
            endpoint="stock_info_global_cls",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        ),
    )
    assert len(result) == 1
    assert result[0].source == "财联社电报"


def test_akshare_stock_info_global_cls_fetch_empty_table_returns_no_result() -> None:
    provider = AkshareStockInfoGlobalClsProvider()

    def _return_empty(symbol: str):
        assert symbol == "全部"
        return pd.DataFrame()

    with _StockInfoGlobalClsCallSwap(_return_empty):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_cls",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "no_result"
    assert result.attempt.raw_count == 0


def test_akshare_stock_info_global_cls_fetch_schema_changed_when_required_columns_missing() -> None:
    provider = AkshareStockInfoGlobalClsProvider()

    def _return_schema_changed(symbol: str):
        assert symbol == "全部"
        return pd.DataFrame(
            [
                {
                    "内容": "仅有内容",
                    "发布时间": "09:12:00",
                }
            ]
        )

    with _StockInfoGlobalClsCallSwap(_return_schema_changed):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_cls",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "schema_changed"


def test_akshare_stock_info_global_cls_fetch_provider_error_returns_sanitized_error() -> None:
    provider = AkshareStockInfoGlobalClsProvider()

    def _raise_provider_error(symbol: str):
        assert symbol == "全部"
        raise RuntimeError("service unavailable token=secret-123")

    with _StockInfoGlobalClsCallSwap(_raise_provider_error):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_cls",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "provider_error"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_akshare_stock_info_global_cls_fetch_timeout_maps_timeout_with_elapsed_and_not_cancelled() -> None:
    provider = AkshareStockInfoGlobalClsProvider()

    def _raise_timeout(symbol: str):
        assert symbol == "全部"
        raise TimeoutError("request timed out")

    with _StockInfoGlobalClsCallSwap(_raise_timeout):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_cls",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "timeout"
    assert result.attempt.elapsed_ms >= 0
    assert result.attempt.cancelled is False


def test_akshare_stock_info_global_cls_fetch_permission_missing_and_sanitized_error() -> None:
    provider = AkshareStockInfoGlobalClsProvider()

    def _raise_permission_error(symbol: str):
        assert symbol == "全部"
        raise PermissionError("permission denied token=secret-123")

    with _StockInfoGlobalClsCallSwap(_raise_permission_error):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_cls",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "permission_missing"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_akshare_stock_info_global_cls_fetch_rate_limited_and_sanitized_error() -> None:
    provider = AkshareStockInfoGlobalClsProvider()

    def _raise_rate_limited(symbol: str):
        assert symbol == "全部"
        raise RuntimeError("429 too many requests token=secret-123")

    with _StockInfoGlobalClsCallSwap(_raise_rate_limited):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_cls",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "rate_limited"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_akshare_stock_info_global_em_normalize_rows_maps_fields_and_default_source() -> None:
    provider = AkshareStockInfoGlobalEmProvider()
    raw_table = pd.DataFrame(
        [
            {
                "标题": "全球市场早报",
                "摘要": "美元指数回落",
                "发布时间": "2026-05-07 08:00:00",
                "链接": "https://em.example.com/a?token=abc",
            },
            {
                "标题": "海外债市波动",
                "内容": "长端利率上行",
                "发布时间": "2026-05-07 08:10:00",
                "来源": "东方财富",
                "链接": "javascript:alert(1)",
            },
        ]
    )
    query = ProviderQuery(
        endpoint="stock_info_global_em",
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        keywords=["茅台", "白酒"],
        start_date="2026-05-01",
        end_date="2026-05-07",
    )

    result = provider._normalize_rows(raw_table, query)
    assert len(result) == 2
    assert result[0].title == "全球市场早报"
    assert result[0].summary == "美元指数回落"
    assert result[0].publish_time == "2026-05-07 08:00:00"
    assert result[0].source == "东方财富快讯"
    assert result[0].url == "https://em.example.com/a?token=%2A%2A%2A"
    assert result[0].data_source == "akshare.stock_info_global_em"
    assert result[0].raw_id.startswith("stock_info_global_em:")
    assert result[1].summary == "长端利率上行"
    assert result[1].source == "东方财富"
    assert result[1].url is None


def test_akshare_stock_info_global_em_fetch_empty_table_returns_no_result() -> None:
    provider = AkshareStockInfoGlobalEmProvider()

    def _return_empty():
        return pd.DataFrame()

    with _StockInfoGlobalEmCallSwap(_return_empty):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_em",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "no_result"
    assert result.attempt.raw_count == 0


def test_akshare_stock_info_global_em_fetch_timeout_returns_timeout_with_non_negative_elapsed() -> None:
    provider = AkshareStockInfoGlobalEmProvider()

    def _raise_timeout():
        raise TimeoutError("request timed out")

    with _StockInfoGlobalEmCallSwap(_raise_timeout):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_em",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "timeout"
    assert result.attempt.elapsed_ms >= 0


def test_akshare_stock_info_global_em_fetch_schema_changed_when_required_columns_missing() -> None:
    provider = AkshareStockInfoGlobalEmProvider()

    def _return_schema_changed():
        return pd.DataFrame(
            [
                {
                    "摘要": "仅有摘要",
                    "发布时间": "2026-05-07 08:00:00",
                    "链接": "https://em.example.com/x",
                }
            ]
        )

    with _StockInfoGlobalEmCallSwap(_return_schema_changed):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_em",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "schema_changed"


def test_akshare_stock_info_global_em_fetch_provider_error_returns_sanitized_error() -> None:
    provider = AkshareStockInfoGlobalEmProvider()

    def _raise_provider_error():
        raise RuntimeError("service unavailable token=secret-123")

    with _StockInfoGlobalEmCallSwap(_raise_provider_error):
        result = provider.fetch(
            ProviderQuery(
                endpoint="stock_info_global_em",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-05-01",
                end_date="2026-05-07",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "provider_error"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_akshare_news_cctv_fetch_uses_yyyymmdd_and_maps_rows() -> None:
    provider = AkshareNewsCctvProvider()

    def _return_rows(date: str):
        assert date == "20260506"
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-06",
                    "title": "国务院常务会议部署稳就业",
                    "content": "会议强调落实存量政策并研究增量政策。",
                }
            ]
        )

    with _NewsCctvCallSwap(_return_rows):
        result = provider.fetch(
            ProviderQuery(
                endpoint="news_cctv",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-04-30",
                end_date="2026-05-06",
            )
        )

    assert result.ok is True
    assert len(result.raw_items) == 1
    assert result.raw_items[0].publish_time == "2026-05-06"
    assert result.raw_items[0].title == "国务院常务会议部署稳就业"
    assert result.raw_items[0].summary == "会议强调落实存量政策并研究增量政策。"
    assert result.raw_items[0].source == "新闻联播"
    assert result.raw_items[0].url is None
    assert result.raw_items[0].data_source == "akshare.news_cctv"


def test_akshare_news_cctv_fetch_empty_table_returns_no_result() -> None:
    provider = AkshareNewsCctvProvider()

    def _return_empty(date: str):
        assert date == "20260506"
        return pd.DataFrame()

    with _NewsCctvCallSwap(_return_empty):
        result = provider.fetch(
            ProviderQuery(
                endpoint="news_cctv",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-04-30",
                end_date="2026-05-06",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "no_result"
    assert result.attempt.raw_count == 0


def test_akshare_news_cctv_fetch_missing_required_field_returns_schema_changed() -> None:
    provider = AkshareNewsCctvProvider()

    def _return_missing_content(date: str):
        assert date == "20260506"
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-06",
                    "title": "只有标题没有正文",
                }
            ]
        )

    with _NewsCctvCallSwap(_return_missing_content):
        result = provider.fetch(
            ProviderQuery(
                endpoint="news_cctv",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-04-30",
                end_date="2026-05-06",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "schema_changed"


def test_akshare_news_cctv_fetch_timeout_returns_timeout() -> None:
    provider = AkshareNewsCctvProvider()

    def _raise_timeout(date: str):
        assert date == "20260506"
        raise TimeoutError("request timed out")

    with _NewsCctvCallSwap(_raise_timeout):
        result = provider.fetch(
            ProviderQuery(
                endpoint="news_cctv",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-04-30",
                end_date="2026-05-06",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "timeout"


def test_akshare_news_cctv_fetch_provider_error_returns_sanitized_error() -> None:
    provider = AkshareNewsCctvProvider()

    def _raise_provider_error(date: str):
        assert date == "20260506"
        raise RuntimeError("service unavailable token=secret-123")

    with _NewsCctvCallSwap(_raise_provider_error):
        result = provider.fetch(
            ProviderQuery(
                endpoint="news_cctv",
                ticker="600519",
                exchange_ticker="600519.SH",
                company_name="贵州茅台",
                keywords=[],
                start_date="2026-04-30",
                end_date="2026-05-06",
            )
        )

    assert result.ok is False
    assert result.attempt.empty_reason == "provider_error"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_tushare_anns_d_fetch_without_token_returns_not_configured_without_remote_call() -> None:
    counters = {"pro_api_called": 0}

    def _pro_api(_token: str):
        counters["pro_api_called"] += 1
        raise AssertionError("token 缺失时不应调用 pro_api")

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier(None),
        pro_api_factory=_pro_api,
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert result.ok is False
    assert result.attempt.empty_reason == "not_configured"
    assert counters["pro_api_called"] == 0


def test_tushare_anns_d_fetch_maps_fields_and_uses_expected_remote_call() -> None:
    captured: dict[str, str] = {}

    class _Client:
        def anns_d(self, *, ts_code: str, start_date: str, end_date: str):
            captured["ts_code"] = ts_code
            captured["start_date"] = start_date
            captured["end_date"] = end_date
            return pd.DataFrame(
                [
                    {
                        "ann_date": "20260507",
                        "ts_code": "600519.SH",
                        "name": "贵州茅台",
                        "title": "2026年第一季度报告",
                        "url": "https://ann.example.com/a?token=abc",
                    }
                ]
            )

    def _pro_api(token: str):
        captured["token"] = token
        return _Client()

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier("real-token-123"),
        pro_api_factory=_pro_api,
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert captured["token"] == "real-token-123"
    assert captured["ts_code"] == "600519.SH"
    assert captured["start_date"] == "20260501"
    assert captured["end_date"] == "20260507"
    assert result.ok is True
    assert len(result.raw_items) == 1
    assert result.raw_items[0].source == "tushare.anns_d"
    assert result.raw_items[0].data_source == "tushare.anns_d"
    assert result.raw_items[0].publish_time == "20260507"
    assert result.raw_items[0].summary is not None
    assert "ts_code: 600519.SH" in result.raw_items[0].summary
    assert "name: 贵州茅台" in result.raw_items[0].summary
    assert result.raw_items[0].url == "https://ann.example.com/a?token=%2A%2A%2A"


def test_tushare_anns_d_fetch_uses_rec_time_when_ann_date_missing() -> None:
    class _Client:
        def anns_d(self, *, ts_code: str, start_date: str, end_date: str):
            return pd.DataFrame(
                [
                    {
                        "ann_date": None,
                        "rec_time": "2026-05-07 18:30:00",
                        "ts_code": ts_code,
                        "name": "贵州茅台",
                        "title": "董事会决议公告",
                        "url": "https://ann.example.com/b",
                    }
                ]
            )

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier("real-token-123"),
        pro_api_factory=lambda _token: _Client(),
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert result.ok is True
    assert result.raw_items[0].publish_time == "2026-05-07 18:30:00"


def test_tushare_anns_d_fetch_permission_missing_and_error_sanitized() -> None:
    class _Client:
        def anns_d(self, *, ts_code: str, start_date: str, end_date: str):
            raise RuntimeError("积分不足 token=secret-123")

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier("real-token-123"),
        pro_api_factory=lambda _token: _Client(),
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert result.ok is False
    assert result.attempt.empty_reason == "permission_missing"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "token=***" in result.attempt.error


def test_tushare_anns_d_fetch_provider_error_and_error_sanitized() -> None:
    class _Client:
        def anns_d(self, *, ts_code: str, start_date: str, end_date: str):
            raise RuntimeError(
                "service unavailable token=secret-123 api_key=top-secret-key"
            )

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier("real-token-123"),
        pro_api_factory=lambda _token: _Client(),
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert result.ok is False
    assert result.attempt.empty_reason == "provider_error"
    assert result.attempt.error is not None
    assert "secret-123" not in result.attempt.error
    assert "top-secret-key" not in result.attempt.error
    assert "token=***" in result.attempt.error
    assert "api_key=***" in result.attempt.error


def test_tushare_anns_d_fetch_timeout_maps_timeout() -> None:
    class _Client:
        def anns_d(self, *, ts_code: str, start_date: str, end_date: str):
            raise TimeoutError("request timed out")

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier("real-token-123"),
        pro_api_factory=lambda _token: _Client(),
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert result.ok is False
    assert result.attempt.empty_reason == "timeout"


def test_tushare_anns_d_fetch_missing_title_field_maps_schema_changed() -> None:
    class _Client:
        def anns_d(self, *, ts_code: str, start_date: str, end_date: str):
            return pd.DataFrame(
                [
                    {
                        "ann_date": "20260507",
                        "ts_code": "600519.SH",
                        "name": "贵州茅台",
                        "url": "https://ann.example.com/c",
                    }
                ]
            )

    provider = TushareAnnouncementsProvider(
        token_loader=lambda: _TokenCarrier("real-token-123"),
        pro_api_factory=lambda _token: _Client(),
    )
    result = provider.fetch(
        ProviderQuery(
            endpoint="anns_d",
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            keywords=[],
            start_date="2026-05-01",
            end_date="2026-05-07",
        )
    )

    assert result.ok is False
    assert result.attempt.empty_reason == "schema_changed"
