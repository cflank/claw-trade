from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from time import sleep
from types import SimpleNamespace

import pytest

from claw_trade.data_gateway.execution import ProviderResultCache
from claw_trade.data_gateway.execution.gate import ExecutionGate
from claw_trade.data_gateway.models import (
    DataGap,
    DataResult,
    DataResultStatus,
    FetchResult,
    GapReason,
    GateDecision,
    IngestResult,
    Market,
    ResultRefs,
)
from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.needs import DataNeed, DataNeedGap, NeedPlan, NeedPriority, ProviderCallSpec
from claw_trade.data_gateway.public_api import PublicDataRequest
from claw_trade.data_gateway.report_evidence import _internal_need_for_public_request
from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.reports.data_need_bridge import (
    _NeedResultRefs,
    _crypto_lens_payload,
    _data_need_model_visible_text,
    _data_need_status,
    _latest_value_summaries,
    _market_chart_payload,
    _payload_need_satisfied,
    _row_summary,
    _should_build_crypto_lens,
    _should_build_market_chart,
    run_claw_request_data,
    run_report_data_prefetch,
)
from claw_trade.reports.data_evidence_summary import summarize_report_data_need_results, summarize_report_prefetch_manifest


def _internal_need_from_public_request(value: object) -> DataNeed:
    if isinstance(value, DataNeed):
        return value
    if isinstance(value, PublicDataRequest):
        return _internal_need_for_public_request(value)
    raise TypeError(f"expected PublicDataRequest or DataNeed, got {type(value)!r}")


def _request_payload_from_need(need: DataNeed) -> dict[str, object]:
    suffix = str(need.api_id or "").strip().lower().rsplit(".", 1)[-1]
    item = {
        "daily_bar": "日线",
        "financial_metric": "财务指标",
        "macro_news": "宏观新闻",
        "cvd": "主动买卖量差",
        "technical_indicators": "技术指标",
    }.get(suffix, suffix or "请求数据")
    return {
        "item": item,
        "market": need.market.value,
        "instrument": need.instrument,
        "purpose": need.purpose,
    }


def _domain_from_need(need: DataNeed) -> str:
    text = f"{str(need.api_id or '').lower()} {need.purpose.lower()}"
    if any(token in text for token in ("news", "filing", "announcement")):
        return "news"
    if any(token in text for token in ("social", "sentiment", "interaction")):
        return "social"
    if any(token in text for token in ("fundamental", "financial", "valuation", "statement", "metric")):
        return "fundamental"
    return "market"


def test_data_need_model_visible_text_hides_execution_layer_gap_details() -> None:
    need = DataNeed(
        need_id="need-1",
        api_id="crypto.daily_bar",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.REQUIRED,
        requested_by_worker="market_analyst",
        purpose="market_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.gaps = (
        DataGap.by_reason(
            "granularity_mismatch",
            request_id="request-1",
            market=Market.CRYPTO,
            data_type="market_price",
            granularity="daily",
            message="provider granularity daily does not match requested event",
            as_of=datetime(2026, 6, 12, tzinfo=UTC),
        ),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "partial"},),
    )

    assert "数据粒度不匹配" not in text
    assert "provider granularity" not in text
    assert "requested event" not in text


def test_data_need_model_visible_text_includes_rows_not_only_ref_counts() -> None:
    need = DataNeed(
        need_id="need-cn-a-market",
        api_id="cn_a.daily_bar",
        market=Market.CN_A,
        instrument="600519.SH",
        priority=NeedPriority.NORMAL,
        requested_by_worker="market_analyst",
        purpose="market_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:daily:1",)
    refs.raw_refs = ("raw:daily:1",)
    refs.attempt_refs = ("attempt:daily:1",)
    result = DataResult(
        request_id="data_need:call:market:0:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-10",
                "open": 1400.0,
                "high": 1420.0,
                "low": 1390.0,
                "close": 1410.0,
                "volume": 1200000,
                "amount": 1680000000,
                "amount_unit": "CNY",
                "symbol_id": "600519.SH",
                "source_raw_refs": ("raw:daily:1",),
            },
        ),
        dataset_refs=("dataset:daily:1",),
        raw_refs=("raw:daily:1",),
        attempt_refs=("attempt:daily:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
    )

    assert "已返回的数据摘要" in text
    assert "日线行情" in text
    assert "收盘价 1410.0" in text
    assert "成交量 1200000" in text
    assert "结构化引用" not in text
    for token in ("source_raw_refs", "provider_lineage", "daily_bar", "date="):
        assert token not in text


def test_crypto_daily_bar_visible_text_includes_crypto_lens_local_indicators(tmp_path) -> None:
    rows = []
    for index in range(180):
        close = 60000.0 + index * 37.0 + (index % 9) * 120.0
        rows.append(
            {
                "date": f"2026-01-{(index % 28) + 1:02d}",
                "open": close - 80.0,
                "high": close + 180.0,
                "low": close - 220.0,
                "close": close,
                "volume": 1000.0 + index,
                "symbol_id": "BTCUSDT",
                "base_asset": "BTC",
                "quote_asset": "USDT",
            }
        )
    result = DataResult(
        request_id="data_need:call:market:0:daily_bar",
        status=DataResultStatus.READY,
        rows=tuple(rows),
        dataset_refs=("dataset:btc:daily",),
        raw_refs=("raw:btc:daily",),
        attempt_refs=("attempt:btc:daily",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    assert _should_build_crypto_lens(market=Market.CRYPTO, data_results=(result,))
    crypto_lens = _crypto_lens_payload(
        tool_input={
            "ticker": "BTC/USDT",
            "currency": "USDT",
            "current_date": "2026-06-15",
            "start_date": "2026-01-01",
            "end_date": "2026-06-15",
        },
        runtime_context={"run_id": "run-test", "call_id": "call-test", "evidence_root": str(tmp_path)},
        results=(result,),
    )

    text = _data_need_model_visible_text(
        request={"item": "日线", "market": "CRYPTO", "instrument": "BTC/USDT", "purpose": "market_report"},
        need_domain="market",
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=_NeedResultRefs(),
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        crypto_lens_payload=crypto_lens,
        crypto_base_asset="BTC",
        need_satisfied=True,
    )

    assert crypto_lens["crypto_lens_status"] in {"ready", "partial"}
    assert "CryptoLens 指标材料" in text
    assert "| 维加斯通道 | 可分析 |" in text
    assert "| 布林带 | 可分析 |" in text
    assert "| RSI | 可分析 |" in text
    assert "| MACD | 可分析 |" in text
    assert "| TD 9/13 | 可分析 |" in text
    assert "| 谐波形态 | 可分析 |" in text
    assert "| 清算地图 | 缺失/不可用 |" not in text
    assert "| OI/多空比 | 缺失/不可用 |" not in text
    assert "| 链上 | 缺失/不可用 |" not in text
    assert "市场数据结果没有生成结构化指标分析" not in text


def test_data_need_model_visible_text_promotes_latest_funding_rate_reading() -> None:
    need = DataNeed(
        need_id="need-btc-funding",
        api_id="crypto.funding_rate",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.REQUIRED,
        requested_by_worker="market_analyst",
        purpose="derivatives_crowding",
        deadline_at=datetime(2026, 6, 15, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:funding:1",)
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "timestamp": "2026-06-15T08:00:00Z",
                "funding_rate": 0.0031,
                "funding_rate_unit": "%",
                "exchange": "Binance",
                "source": "Coinglass",
                "granularity": "hourly",
            },
            {
                "timestamp": "2026-06-15T12:00:00Z",
                "funding_rate": 0.003678,
                "funding_rate_unit": "%",
                "exchange": "Binance",
                "source": "Coinglass",
                "granularity": "hourly",
            },
        ),
        dataset_refs=("dataset:funding:1",),
        raw_refs=("raw:funding:1",),
        attempt_refs=("attempt:funding:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="ready",
        planned_count=2,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "最新读数：资金费率 0.003678 %" in text
    assert "时间 2026-06-15T12:00:0" in text
    assert "来源 Coinglass" in text
    assert "粒度 hourly" in text


def test_latest_value_summary_is_structured_for_successful_funding_rate() -> None:
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "timestamp": "2026-06-15T08:00:00Z",
                "funding_rate": 0.0031,
                "funding_rate_unit": "%",
                "source": "Coinglass",
                "granularity": "hourly",
            },
            {
                "timestamp": "2026-06-15T12:00:00Z",
                "funding_rate": 0.003678,
                "funding_rate_unit": "%",
                "source": "Coinglass",
                "granularity": "hourly",
            },
        ),
        dataset_refs=("dataset:funding:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    summary = _latest_value_summaries((result,))

    assert summary == (
        {
            "label": "资金费率",
            "value": 0.003678,
            "unit_or_scope": "%",
            "time": "2026-06-15T12:00:00",
            "source": "Coinglass",
            "granularity": "hourly",
            "sample_range": ("2026-06-15", "2026-06-15"),
            "request_id": "data_need:call:market:0:crypto_derivative_metric",
            "dataset": "crypto_derivative_metric",
        },
    )


def test_crypto_visible_text_hides_internal_aggregated_source_ids() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:funding:1",)
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "timestamp": "2026-06-15T12:00:00Z",
                "funding_rate": -0.000499,
                "funding_rate_unit": "percent",
                "source": "COINGLASS_AGGREGATED",
                "granularity": "hourly",
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=("raw:funding:1",),
        attempt_refs=("attempt:funding:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "资金费率"},
        need_domain="market",
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )
    summary = _latest_value_summaries((result,))

    assert "衍生品与链上聚合数据源" in text
    assert "COINGLASS_AGGREGATED" not in text
    assert summary[0]["source"] == "衍生品与链上聚合数据源"


def test_data_need_model_visible_text_expands_long_short_ratio_components() -> None:
    need = DataNeed(
        need_id="need-btc-long-short",
        api_id="crypto.long_short_ratio",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.REQUIRED,
        requested_by_worker="market_analyst",
        purpose="derivatives_positioning",
        deadline_at=datetime(2026, 6, 15, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:long-short:1",)
    rows = (
        {
            "timestamp": "2026-06-15T12:00:00Z",
            "long_short_ratio": 1.2,
            "metric": "global_account_long_short_ratio",
            "source": "Coinglass",
            "granularity": "hourly",
        },
        {
            "timestamp": "2026-06-15T12:00:00Z",
            "long_short_ratio": 1.5,
            "metric": "top_account_long_short_ratio",
            "source": "Coinglass",
            "granularity": "hourly",
        },
        {
            "timestamp": "2026-06-15T12:00:00Z",
            "long_short_ratio": 1.7,
            "metric": "top_position_long_short_ratio",
            "source": "Coinglass",
            "granularity": "hourly",
        },
    )
    results = tuple(
        DataResult(
            request_id=f"data_need:call:market:{index}:crypto_derivative_metric",
            status=DataResultStatus.READY,
            rows=(row,),
            dataset_refs=("dataset:long-short:1",),
            raw_refs=("raw:long-short:1",),
            attempt_refs=("attempt:long-short:1",),
            as_of=datetime(2026, 6, 15, tzinfo=UTC),
        )
        for index, row in enumerate(rows)
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="ready",
        planned_count=6,
        scheduled_count=3,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=results,
        need_satisfied=True,
    )
    summary = _latest_value_summaries(results)

    assert "全局账户多空比 1.2" in text
    assert "大户账户多空比 1.5" in text
    assert "大户持仓多空比 1.7" in text
    assert "口径 大户账户多空比" in text
    assert {item["label"] for item in summary} >= {"全局账户多空比", "大户账户多空比", "大户持仓多空比"}


def test_data_need_model_visible_text_expands_social_metrics() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:social:1",)
    refs.raw_refs = ("raw:social:1",)
    refs.attempt_refs = ("attempt:social:1",)
    result = DataResult(
        request_id="data_need:call:social:0:social_signal",
        status=DataResultStatus.READY,
        rows=(
            {
                "period_start": "2026-06-15",
                "source": "stock_hot_rank_latest_em",
                "metrics": {"rank": 18, "heat": 3271, "rank_change": -3},
                "symbol_id": "600519.SH",
            },
        ),
        dataset_refs=("dataset:social:1",),
        raw_refs=("raw:social:1",),
        attempt_refs=("attempt:social:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CN_A", "instrument": "600519.SH", "item": "社交情绪"},
        need_domain="social",
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "舆情/互动线索已返回" in text
    assert "rank 18" in text
    assert "heat 3271" in text


def test_crypto_order_book_visible_text_exposes_l2_depth() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:order-book:1",)
    result = DataResult(
        request_id="data_need:call:market:0:order_book_snapshot",
        status=DataResultStatus.READY,
        rows=(
            {
                "dataset": "order_book_snapshot",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "timestamp": "2026-06-15T12:00:00Z",
                "bid_price": 66100.0,
                "bid_size": 1.25,
                "ask_price": 66101.0,
                "ask_size": 0.8,
                "bid_levels": 5,
                "ask_levels": 5,
                "source": "Binance",
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=("raw:order-book:1",),
        attempt_refs=("attempt:order-book:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "盘口"},
        need_domain="market",
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "盘口快照" in text
    assert "盘口快照：1 个快照，含买盘 5 档，卖盘 5 档；这不是单点买一/卖一。" in text
    assert "买一价 66100.0" in text
    assert "卖一价 66101.0" in text
    assert "买盘档数 5" in text
    assert "卖盘档数 5" in text


def test_crypto_derivative_visible_text_explains_cvd_stats_and_oi_quote_structure() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:derivative:1",)
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "timestamp": "2026-06-15T10:00:00Z",
                "open_interest": 100.0,
                "open_interest_unit": "USD",
                "cvd": -20.0,
                "taker_volume_unit": "USD",
                "granularity": "hourly",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "source": "Coinglass",
            },
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "timestamp": "2026-06-15T11:00:00Z",
                "open_interest": 110.0,
                "open_interest_unit": "USD",
                "cvd": -10.0,
                "taker_volume_unit": "USD",
                "granularity": "hourly",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "source": "Coinglass",
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=("raw:derivative:1",),
        attempt_refs=("attempt:derivative:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "CVD"},
        need_domain="market",
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "CVD 最新值 -10.0 USD" in text
    assert "样本变化 10.0 USD" in text
    assert "小时级样本" in text
    assert "OI 计价结构：U 本位/稳定币计价" in text


def test_event_calendar_visible_text_classifies_event_impact() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:event:1",)
    result = DataResult(
        request_id="data_need:call:news:0:event_calendar",
        status=DataResultStatus.READY,
        rows=(
            {
                "dataset": "event_calendar",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "event_date": "2026-06-15",
                "event_type": "economic_calendar",
                "title": "US CPI",
                "source": "US",
                "importance_level": 3,
            },
            {
                "dataset": "event_calendar",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "event_date": "2026-06-15",
                "event_type": "project_release",
                "title": "Bitcoin Core 31.0",
                "source": "GitHub",
                "importance_level": 1,
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=("raw:event:1",),
        attempt_refs=("attempt:event:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "事件日历"},
        need_domain="news",
        status="ready",
        planned_count=2,
        scheduled_count=2,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "事件类型分布" in text
    assert "宏观经济日历 1 条" in text
    assert "项目/协议事件 1 条" in text
    assert "高影响事件 1 条" in text


def test_crypto_social_visible_text_includes_fear_greed_scale() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:social:1",)
    result = DataResult(
        request_id="data_need:call:social:0:social_signal",
        status=DataResultStatus.READY,
        rows=(
            {
                "dataset": "social_signal",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "timestamp": "2026-06-15T00:00:00Z",
                "source": "Alternative.me Fear & Greed",
                "score": 22.0,
                "sentiment": "Extreme Fear",
                "score_min": 0,
                "score_max": 100,
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=("raw:social:1",),
        attempt_refs=("attempt:social:1",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "社交情绪"},
        need_domain="social",
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "分数范围 0-100" in text
    assert "分数越高代表越贪婪/乐观" in text
    assert "不是社交平台原文样本" not in text


def test_data_need_model_visible_text_shows_northbound_and_margin_fields() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:northbound:1", "dataset:margin:1")
    refs.raw_refs = ("raw:northbound:1", "raw:margin:1")
    refs.attempt_refs = ("attempt:northbound:1", "attempt:margin:1")
    northbound_result = DataResult(
        request_id="data_need:call:hot_money:0:northbound_flow",
        status=DataResultStatus.READY,
        rows=(
            {
                "timestamp": "2026-06-12T14:59:00+08:00",
                "hgt_net": 1.2,
                "sgt_net": -0.4,
                "northbound_net": 0.8,
                "amount_unit": "CNY 亿元",
                "source": "ths_hsgt_api",
            },
        ),
        dataset_refs=("dataset:northbound:1",),
        raw_refs=("raw:northbound:1",),
        attempt_refs=("attempt:northbound:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    margin_result = DataResult(
        request_id="data_need:call:hot_money:1:margin_trading",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-12",
                "financing_balance": 1000.0,
                "margin_balance": 1200.0,
                "security_lending_volume": 30.0,
            },
        ),
        dataset_refs=("dataset:margin:1",),
        raw_refs=("raw:margin:1",),
        attempt_refs=("attempt:margin:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CN_A", "instrument": "600519.SH", "item": "北向资金"},
        need_domain="market",
        status="ready",
        planned_count=2,
        scheduled_count=2,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(northbound_result, margin_result),
        need_satisfied=True,
    )

    assert "北向资金" in text
    assert "沪股通净流入 1.2 CNY 亿元" in text
    assert "北向合计净流入 0.8 CNY 亿元" in text
    assert "融资融券" in text
    assert "融资余额 1000.0" in text
    assert "融资融券余额 1200.0" in text


def test_market_chart_payload_uses_best_daily_bar_instead_of_first(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.reports.data_need_bridge as data_need_bridge

    selected_dates: list[str] = []

    def fake_analyze_market_frame(frame, *, ticker):  # type: ignore[no-untyped-def]
        del ticker
        selected_dates.extend(str(value) for value in frame["date"].tolist())
        return SimpleNamespace(chart_frame=frame, summary="ok", indicators={"ma20": 1}, warnings=())

    def fake_render_market_charts(frame, *, ticker, output_dir):  # type: ignore[no-untyped-def]
        del frame, ticker
        return (output_dir / "chart.png",)

    monkeypatch.setattr(data_need_bridge.market_indicators, "analyze_market_frame", fake_analyze_market_frame)
    monkeypatch.setattr(data_need_bridge.market_charts, "render_market_charts", fake_render_market_charts)
    stale_short = DataResult(
        request_id="data_need:call:market:0:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-05-27",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
            },
        ),
        dataset_refs=("dataset:stale",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    fresh_full = DataResult(
        request_id="data_need:call:market:1:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-11",
                "open": 12.0,
                "high": 13.0,
                "low": 11.0,
                "close": 12.5,
                "volume": 1200.0,
            },
            {
                "date": "2026-06-12",
                "open": 13.0,
                "high": 14.0,
                "low": 12.0,
                "close": 13.5,
                "volume": 1300.0,
            },
        ),
        dataset_refs=("dataset:fresh",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    payload = _market_chart_payload(
        tool_input={"ticker": "600519.SH"},
        runtime_context={"evidence_root": str(tmp_path), "run_id": "run", "call_id": "call"},
        results=(stale_short, fresh_full),
    )

    assert payload["chart_status"] == "ready"
    assert selected_dates == ["2026-06-11", "2026-06-12"]


def test_market_chart_is_only_requested_for_daily_bar_results() -> None:
    lockup_result = DataResult(
        request_id="data_need:call:market:0:lockup_event",
        status=DataResultStatus.READY,
        rows=(),
        dataset_refs=("dataset:lockup",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    daily_result = DataResult(
        request_id="data_need:call:market:0:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-12",
                "open": 13.0,
                "high": 14.0,
                "low": 12.0,
                "close": 13.5,
                "volume": 1300.0,
            },
        ),
        dataset_refs=("dataset:daily",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    assert not _should_build_market_chart(need_domain="market", data_results=(lockup_result,))
    assert _should_build_market_chart(need_domain="market", data_results=(daily_result,))
    assert not _should_build_market_chart(need_domain="news", data_results=(daily_result,))


def test_cninfo_official_filing_summary_is_not_called_media_search() -> None:
    summary = _row_summary(
        {
            "source": "cninfo",
            "title": "暂停买卖",
            "body_ref": "raw:filing:1",
            "published_at": "2026-06-15",
        },
        domain="news",
        dataset="official_filing",
    )

    assert "官方公告材料已返回" in summary
    assert "正文索引已返回" in summary
    assert "媒体/搜索线索" not in summary


def test_financial_metric_summary_exposes_ratio_values() -> None:
    need = DataNeed(
        need_id="need-cn-a-financial",
        api_id="financial_metric",
        market=Market.CN_A,
        instrument="600519.SH",
        priority=NeedPriority.NORMAL,
        requested_by_worker="fundamental_analyst",
        purpose="fundamental_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:financial_metric:1",)
    refs.raw_refs = ("raw:financial_metric:1",)
    refs.attempt_refs = ("attempt:financial_metric:1",)
    result = DataResult(
        request_id="data_need:call:fundamental:0:financial_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "period": "2026-03-31",
                "roe": 31.2,
                "roa": 8.6,
                "gross_margin": 91.7,
                "debt_ratio": 12.1,
                "eps": 21.68,
                "revenue": 54702912385.23,
                "net_income": 27242512886.45,
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=refs.raw_refs,
        attempt_refs=refs.attempt_refs,
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "净资产收益率 31.2" in text
    assert "资产收益率 8.6" in text
    assert "毛利率 91.7" in text
    assert "负债率 12.1" in text
    assert "每股收益 21.68" in text


def test_data_need_writes_compact_audit_evidence(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.reports.data_need_bridge as data_need_bridge

    result = DataResult(
        request_id="data_need:call:fundamental:0:financial_metric",
        status=DataResultStatus.READY,
        rows=(
            {"period": "2025-12-31", "roe": 30.1, "revenue": 100.0},
            {
                "period": "2026-03-31",
                "roe": 31.2,
                "revenue": 120.0,
                "source_raw_refs": ("raw:hidden",),
                "provider_lineage": {"provider": "hidden"},
                "summary": "x" * 700,
            },
        ),
        dataset_refs=("dataset:financial_metric:1",),
        raw_refs=("raw:financial_metric:1",),
        attempt_refs=("attempt:financial_metric:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    def fake_request(tool_input, runtime_context):  # type: ignore[no-untyped-def]
        return {
            "ok": True,
            "status": "ready",
            "need_satisfied": True,
            "request": {
                "item": tool_input["item"],
                "market": tool_input["market"],
                "instrument": tool_input["instrument"],
            },
            "planned_calls_count": 1,
            "scheduled_calls_count": 1,
            "refs": {
                "dataset_refs": ("dataset:financial_metric:1",),
                "raw_refs": ("raw:financial_metric:1",),
                "attempt_refs": ("attempt:financial_metric:1",),
                "gaps": (),
            },
            "data_results": (result.model_dump(mode="json"),),
            "gaps": (),
            "provider_attempts_summary": (
                {
                    "attempt_refs": ("attempt:financial_metric:1",),
                    "provider_id": "cn_a_eastmoney_market_data",
                    "catalog_endpoint_id": "eastmoney.securities.financial_analysis_indicator",
                    "status": "success",
                    "remote_attempted": True,
                    "remote_success": True,
                    "http_audit_status": "managed_http_observed",
                    "error_code": None,
                    "error_message": None,
                },
            ),
            "merge_evidence": (),
            "rate_limit_evidence": (),
        }

    monkeypatch.setattr(data_need_bridge, "run_data_need_tool_request", fake_request)

    payload = run_claw_request_data(
        {
            "item": "财务指标",
            "purpose": "fundamental_report",
            "instrument": "600519.SH",
            "market": "CN_A",
        },
        {
            "worker_id": "fundamental_analyst",
            "run_id": "run-audit",
            "call_id": "call-audit",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )

    evidence_path = tmp_path / "data-layer" / "data-need-results" / "run-audit" / "call-audit" / "result.json"
    assert payload["data_need_evidence_paths"] == (str(evidence_path),)
    audit = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert audit["data_attempts_summary"][0]["provider_id"] == "cn_a_eastmoney_market_data"
    assert audit["data_attempts_summary"][0]["catalog_endpoint_id"] == "eastmoney.securities.financial_analysis_indicator"
    assert audit["data_attempts_summary"][0]["attempt_refs"] == ["attempt:financial_metric:1"]
    assert audit["data_attempts_summary"][0]["remote_attempted"] is True
    assert audit["data_attempts_summary"][0]["remote_success"] is True
    assert audit["data_attempts_summary"][0]["http_audit_status"] == "managed_http_observed"
    assert audit["data_results"][0]["row_count"] == 2
    assert audit["data_results"][0]["sample_rows"][0]["period"] == "2026-03-31"
    assert "source_raw_refs" not in audit["data_results"][0]["sample_rows"][0]
    assert "provider_lineage" not in audit["data_results"][0]["sample_rows"][0]
    assert audit["data_results"][0]["sample_rows"][0]["summary"].endswith("...")
    assert len(audit["data_results"][0]["sample_rows"][0]["summary"]) <= 500
    assert "净资产收益率 31.2" in audit["model_visible_text"]


def test_same_worker_repeated_ready_data_need_reuses_prior_tool_result(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.reports.data_need_bridge as data_need_bridge

    result = DataResult(
        request_id="data_need:call:social:0:sentiment",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-12",
                "sentiment": "neutral",
                "sample_count": 12,
            },
        ),
        dataset_refs=("dataset:social:1",),
        raw_refs=("raw:social:1",),
        attempt_refs=("attempt:social:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    calls: list[dict[str, object]] = []

    def fake_request(tool_input, runtime_context):  # type: ignore[no-untyped-def]
        calls.append(dict(tool_input))
        return {
            "ok": True,
            "status": "ready",
            "need_satisfied": True,
            "request": {
                **dict(tool_input),
                "time_range_start": "2025-06-12",
                "time_range_end": "2026-06-12",
            },
            "need_domain": "social",
            "planned_calls_count": 1,
            "scheduled_calls_count": 1,
            "refs": {
                "dataset_refs": ("dataset:social:1",),
                "raw_refs": ("raw:social:1",),
                "attempt_refs": ("attempt:social:1",),
                "gaps": (),
            },
            "data_results": (result.model_dump(mode="json"),),
            "gaps": (),
            "provider_attempts_summary": (
                {
                    "attempt_refs": ("attempt:social:1",),
                    "provider_id": "cn_a_social_source",
                    "catalog_endpoint_id": "cn_a.social_sentiment",
                    "status": "success",
                    "remote_attempted": True,
                    "remote_success": True,
                },
            ),
            "merge_evidence": (),
            "rate_limit_evidence": (),
        }

    monkeypatch.setattr(data_need_bridge, "run_data_need_tool_request", fake_request)

    request = {
        "item": "社交情绪",
        "purpose": "social_report",
        "instrument": "600519.SH",
        "market": "CN_A",
    }
    first = run_claw_request_data(
        request,
        {
            "worker_id": "social_analyst",
            "run_id": "run-cache",
            "call_id": "worker-call-1__tool-call_a",
            "worker_call_id": "worker-call-1",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )
    second = run_claw_request_data(
        request,
        {
            "worker_id": "social_analyst",
            "run_id": "run-cache",
            "call_id": "worker-call-1__tool-call_b",
            "worker_call_id": "worker-call-1",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )

    assert first["status"] == "ready"
    assert second["status"] == "ready"
    assert len(calls) == 1
    assert second["cache_scope"] == "same_worker_call"
    assert second["cache_reused_from"].endswith("/worker-call-1__tool-call_a/result.json")
    audit_path = tmp_path / "data-layer" / "data-need-results" / "run-cache" / "worker-call-1__tool-call_b" / "result.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["cache_reused_from"].endswith("/worker-call-1__tool-call_a/result.json")
    assert audit["tool_input"]["item"] == "社交情绪"
    assert audit["request"]["item"] == "社交情绪"


def test_concurrent_same_worker_same_business_need_uses_file_lock_cache(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.reports.data_need_bridge as data_need_bridge

    result = DataResult(
        request_id="data_need:call:policy:0:macro_news",
        status=DataResultStatus.READY,
        rows=({"published_at": "2026-06-12", "title": "宏观政策线索", "source": "official"},),
        dataset_refs=("dataset:macro:1",),
        raw_refs=("raw:macro:1",),
        attempt_refs=("attempt:macro:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    calls: list[str] = []

    def fake_request(tool_input, runtime_context):  # type: ignore[no-untyped-def]
        calls.append(str(runtime_context["call_id"]))
        sleep(0.1)
        return {
            "ok": True,
            "status": "ready",
            "need_satisfied": True,
            "request": dict(tool_input),
            "need_domain": "news",
            "planned_calls_count": 1,
            "scheduled_calls_count": 1,
            "refs": {
                "dataset_refs": ("dataset:macro:1",),
                "raw_refs": ("raw:macro:1",),
                "attempt_refs": ("attempt:macro:1",),
                "gaps": (),
            },
            "data_results": (result.model_dump(mode="json"),),
            "gaps": (),
            "provider_attempts_summary": (
                {
                    "attempt_refs": ("attempt:macro:1",),
                    "provider_id": "official_api_tushare",
                    "catalog_endpoint_id": "tushare.raw_news",
                    "status": "success",
                    "remote_attempted": True,
                    "remote_success": True,
                },
            ),
            "merge_evidence": (),
            "rate_limit_evidence": (),
        }

    monkeypatch.setattr(data_need_bridge, "run_data_need_tool_request", fake_request)

    request = {
        "item": "宏观",
        "purpose": "policy_report",
        "instrument": "600519.SH",
        "market": "CN_A",
    }
    outputs: list[dict[str, object]] = []

    def worker(call_suffix: str) -> None:
        outputs.append(
            run_claw_request_data(
                request,
                {
                    "worker_id": "policy_analyst",
                    "run_id": "run-cache-concurrent",
                    "call_id": f"worker-call-1__tool-call_{call_suffix}",
                    "worker_call_id": "worker-call-1",
                    "current_date": "2026-06-12",
                    "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
                    "evidence_root": str(tmp_path),
                },
            )
        )

    threads = [threading.Thread(target=worker, args=(suffix,)) for suffix in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert {payload["status"] for payload in outputs} == {"ready"}
    assert any(payload.get("cache_scope") == "same_worker_call" for payload in outputs)


def test_same_worker_unsatisfied_variant_reuses_prior_ready_same_business_need(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.reports.data_need_bridge as data_need_bridge

    ready_result = DataResult(
        request_id="data_need:call:fundamental:0:valuation",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-12",
                "pe_ttm": 24.5,
                "pb": 8.1,
            },
        ),
        dataset_refs=("dataset:valuation:ready",),
        raw_refs=("raw:valuation:ready",),
        attempt_refs=("attempt:valuation:ready",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    calls: list[dict[str, object]] = []

    def fake_request(tool_input, runtime_context):  # type: ignore[no-untyped-def]
        calls.append(dict(tool_input))
        if isinstance(tool_input.get("time_range"), dict):
            return {
                "ok": True,
                "status": "missing",
                "need_satisfied": False,
                "request": dict(tool_input),
                "need_domain": "fundamental",
                "planned_calls_count": 1,
                "scheduled_calls_count": 1,
                "refs": {"dataset_refs": (), "raw_refs": (), "attempt_refs": ("attempt:valuation:missing",), "gaps": ()},
                "data_results": (),
                "gaps": ({"reason": GapReason.PROVIDER_EMPTY.value},),
                "provider_attempts_summary": (
                    {
                        "attempt_refs": ("attempt:valuation:missing",),
                        "provider_id": "cn_a_valuation_source",
                        "catalog_endpoint_id": "cn_a.valuation",
                        "status": "empty",
                        "remote_attempted": True,
                        "remote_success": False,
                    },
                ),
                "merge_evidence": (),
                "rate_limit_evidence": (),
            }
        return {
            "ok": True,
            "status": "ready",
            "need_satisfied": True,
            "request": dict(tool_input),
            "need_domain": "fundamental",
            "planned_calls_count": 1,
            "scheduled_calls_count": 1,
            "refs": {
                "dataset_refs": ("dataset:valuation:ready",),
                "raw_refs": ("raw:valuation:ready",),
                "attempt_refs": ("attempt:valuation:ready",),
                "gaps": (),
            },
            "data_results": (ready_result.model_dump(mode="json"),),
            "gaps": (),
            "provider_attempts_summary": (
                {
                    "attempt_refs": ("attempt:valuation:ready",),
                    "provider_id": "cn_a_valuation_source",
                    "catalog_endpoint_id": "cn_a.valuation",
                    "status": "success",
                    "remote_attempted": True,
                    "remote_success": True,
                },
            ),
            "merge_evidence": (),
            "rate_limit_evidence": (),
        }

    monkeypatch.setattr(data_need_bridge, "run_data_need_tool_request", fake_request)

    base_request = {
        "item": "估值",
        "purpose": "fundamental_report",
        "instrument": "600519.SH",
        "market": "CN_A",
    }
    first = run_claw_request_data(
        base_request,
        {
            "worker_id": "fundamental_analyst",
            "run_id": "run-cache",
            "call_id": "worker-call-1__tool-call_a",
            "worker_call_id": "worker-call-1",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )
    second = run_claw_request_data(
        {**base_request, "time_range": {"lookback_days": 365}},
        {
            "worker_id": "fundamental_analyst",
            "run_id": "run-cache",
            "call_id": "worker-call-1__tool-call_b",
            "worker_call_id": "worker-call-1",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )

    assert first["status"] == "ready"
    assert second["status"] == "ready"
    assert len(calls) == 1
    assert second["cache_scope"] == "same_worker_call_business_need"
    assert second["cache_reused_from"].endswith("/worker-call-1__tool-call_a/result.json")
    audit_path = tmp_path / "data-layer" / "data-need-results" / "run-cache" / "worker-call-1__tool-call_b" / "result.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["cache_scope"] == "same_worker_call_business_need"


def test_same_worker_repeated_missing_data_need_reuses_terminal_result(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.reports.data_need_bridge as data_need_bridge

    calls: list[dict[str, object]] = []

    def fake_request(tool_input, runtime_context):  # type: ignore[no-untyped-def]
        calls.append(dict(tool_input))
        return {
            "ok": True,
            "status": "missing",
            "need_satisfied": False,
            "request": dict(tool_input),
            "need_domain": "news",
            "planned_calls_count": 2,
            "scheduled_calls_count": 2,
            "refs": {
                "dataset_refs": (),
                "raw_refs": (),
                "attempt_refs": ("attempt:news:missing",),
                "gaps": (),
            },
            "data_results": (),
            "gaps": ({"reason": GapReason.PROVIDER_ERROR.value},),
            "provider_attempts_summary": (
                {
                    "attempt_refs": ("attempt:news:missing",),
                    "provider_id": "cn_a_news_source",
                    "catalog_endpoint_id": "cn_a.news",
                    "status": "error",
                    "remote_attempted": True,
                    "remote_success": False,
                },
            ),
            "merge_evidence": (),
            "rate_limit_evidence": (),
        }

    monkeypatch.setattr(data_need_bridge, "run_data_need_tool_request", fake_request)

    request = {
        "item": "宏观",
        "purpose": "news_report",
        "instrument": "600519.SH",
        "market": "CN_A",
        "time_range": {"lookback_days": 90},
    }
    first = run_claw_request_data(
        request,
        {
            "worker_id": "news_analyst",
            "run_id": "run-cache",
            "call_id": "worker-call-1__tool-call_a",
            "worker_call_id": "worker-call-1",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )
    second = run_claw_request_data(
        {**request, "time_range": {"lookback_days": 30}},
        {
            "worker_id": "news_analyst",
            "run_id": "run-cache",
            "call_id": "worker-call-1__tool-call_b",
            "worker_call_id": "worker-call-1",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, tzinfo=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )

    assert first["status"] == "missing"
    assert second["status"] == "missing"
    assert len(calls) == 1
    assert second["cache_scope"] == "same_worker_call_business_need_terminal_result"
    assert second["cache_reused_from"].endswith("/worker-call-1__tool-call_a/result.json")


def test_data_need_model_visible_text_omits_candidate_gaps_when_need_satisfied() -> None:
    need = DataNeed(
        need_id="need-crypto-market",
        api_id="crypto.daily_bar",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.NORMAL,
        requested_by_worker="market_analyst",
        purpose="market_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:daily:1",)
    refs.raw_refs = ("raw:daily:1",)
    refs.attempt_refs = ("attempt:daily:1",)
    refs.gaps = (
        DataGap.by_reason(
            GapReason.DATE_RANGE_MISSING,
            request_id="data_need:call:market:0:intraday_bar",
            market=Market.CRYPTO,
            data_type="intraday_bar",
            granularity="hourly",
            symbol_id="BTC/USDT",
            as_of=datetime(2026, 6, 12, tzinfo=UTC),
        ),
        DataGap.by_reason(
            GapReason.CREDENTIAL_MISSING,
            request_id="data_need:call:market:1:macro_news",
            market=Market.CRYPTO,
            data_type="macro_news",
            granularity="event",
            symbol_id="BTC/USDT",
            as_of=datetime(2026, 6, 12, tzinfo=UTC),
        ),
        DataGap.by_reason(
            GapReason.PROVIDER_ERROR,
            request_id="data_need:call:market:2:crypto_derivative_metric",
            market=Market.CRYPTO,
            data_type="crypto_derivative_metric",
            granularity="realtime",
            symbol_id="BTC/USDT",
            as_of=datetime(2026, 6, 12, tzinfo=UTC),
        ),
    )
    result = DataResult(
        request_id="data_need:call:market:1:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-12",
                "open": 63500.0,
                "high": 64500.0,
                "low": 63000.0,
                "close": 64000.0,
                "volume": 1234.0,
                "symbol_id": "BTCUSDT",
            },
        ),
        dataset_refs=("dataset:daily:1",),
        raw_refs=("raw:daily:1",),
        attempt_refs=("attempt:daily:1",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="ready",
        planned_count=3,
        scheduled_count=3,
        refs=refs,
        gaps=refs.gaps,
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "数据缺口" not in text
    assert "补充说明" not in text
    assert "未覆盖完整分析区间" not in text
    assert "数据源凭证缺失" not in text
    assert "数据源调用失败" not in text
    assert "候选尝试限制" not in text


def test_data_need_model_visible_text_hides_failed_candidate_when_clean_result_satisfies() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:metric:partial", "dataset:metric:ready")
    refs.raw_refs = ("raw:metric:partial", "raw:metric:ready")
    refs.attempt_refs = ("attempt:tushare", "attempt:eastmoney")
    gap = DataGap.by_reason(
        GapReason.FIELD_MISSING,
        request_id="data_need:call:fundamental:0:financial_metric",
        market=Market.CN_A,
        data_type="financial_metric",
        granularity="quarterly",
        required_fields=("debt_ratio",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    partial_candidate = DataResult(
        request_id="data_need:call:fundamental:0:financial_metric",
        status=DataResultStatus.PARTIAL,
        rows=({"period": "2026-03-31", "roe": 30.1},),
        dataset_refs=("dataset:metric:partial",),
        raw_refs=("raw:metric:partial",),
        attempt_refs=("attempt:tushare",),
        gaps=(gap,),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    clean_fallback = DataResult(
        request_id="data_need:call:fundamental:1:financial_metric",
        status=DataResultStatus.READY,
        rows=({"period": "2026-03-31", "roe": 30.1, "debt_ratio": 12.0},),
        dataset_refs=("dataset:metric:ready",),
        raw_refs=("raw:metric:ready",),
        attempt_refs=("attempt:eastmoney",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CN_A", "instrument": "600519.SH", "item": "财务指标"},
        need_domain="fundamental",
        status="ready",
        planned_count=2,
        scheduled_count=2,
        refs=refs,
        gaps=(gap,),
        attempts=({"status": "partial"}, {"status": "success"}),
        data_results=(partial_candidate, clean_fallback),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "已经可用于正文" in text
    assert "数据缺口" not in text
    assert "补充说明" not in text
    assert "必需字段缺失" not in text


def test_data_need_model_visible_text_hides_non_material_candidate_gap_when_rows_are_usable() -> None:
    gap = DataGap.by_reason(
        GapReason.CREDENTIAL_MISSING,
        request_id="data_need:call:market:0:crypto_derivative_metric",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="hourly",
        symbol_id="BTC/USDT",
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.PARTIAL,
        rows=(
            {
                "timestamp": "2026-06-12T00:00:00Z",
                "symbol_id": "BTC/USDT",
                "liquidation_price": 49935.58,
                "liquidation_size": 3816866.4,
            },
        ),
        dataset_refs=("dataset:liquidation_heatmap:coinglass",),
        raw_refs=("raw:liquidation_heatmap:coinglass",),
        attempt_refs=("attempt:coinglass", "attempt:glassnode"),
        gaps=(gap,),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "清算地图"},
        need_domain="market",
        status="ready",
        planned_count=2,
        scheduled_count=2,
        refs=_NeedResultRefs(),
        gaps=(gap,),
        attempts=({"status": "success"}, {"status": "permission_denied"}),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "已经可用于正文" in text
    assert "数据缺口" not in text
    assert "接口凭证缺失" not in text
    assert "缺口证据" not in text


def test_data_need_model_visible_text_treats_crypto_derivative_date_range_gap_as_usable_window() -> None:
    gap = DataGap.by_reason(
        GapReason.DATE_RANGE_MISSING,
        request_id="data_need:call:market:0:crypto_derivative_metric",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="hourly",
        symbol_id="BTC/USDT",
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.PARTIAL,
        rows=(
            {
                "dataset": "crypto_derivative_metric",
                "timestamp": "2026-06-12T00:00:00Z",
                "symbol_id": "BTC/USDT",
                "open_interest": 1462000000.0,
            },
        ),
        dataset_refs=("dataset:open_interest:coinglass",),
        raw_refs=("raw:open_interest:coinglass",),
        attempt_refs=("attempt:coinglass",),
        gaps=(gap,),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "OI"},
        need_domain="market",
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=_NeedResultRefs(),
        gaps=(gap,),
        attempts=({"status": "partial"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "已经可用于正文" in text
    assert "数据缺口" not in text
    assert "未覆盖完整分析区间" not in text


def test_data_need_model_visible_text_treats_macro_series_date_range_gap_as_usable_series() -> None:
    gap = DataGap.by_reason(
        GapReason.DATE_RANGE_MISSING,
        request_id="data_need:call:market:0:macro_series",
        market=Market.CRYPTO,
        data_type="macro_series",
        granularity="monthly",
        symbol_id="BTC/USDT",
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    result = DataResult(
        request_id="data_need:call:market:0:macro_series",
        status=DataResultStatus.PARTIAL,
        rows=(
            {
                "dataset": "macro_series",
                "series_id": "FEDFUNDS",
                "date": "2026-05-01",
                "value": 4.33,
                "region": "US",
                "unit": "FRED",
            },
        ),
        dataset_refs=("dataset:macro_series:fred",),
        raw_refs=("raw:macro_series:fred",),
        attempt_refs=("attempt:fred",),
        gaps=(gap,),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request={"market": "CRYPTO", "instrument": "BTC/USDT", "item": "宏观"},
        need_domain="market",
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=_NeedResultRefs(),
        gaps=(gap,),
        attempts=({"status": "partial"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "已经可用于正文" in text
    assert "最新读数：宏观序列 FEDFUNDS 4.33 FRED" in text
    assert "来源 FRED/US" in text
    assert "序列 FEDFUNDS" in text
    assert "地区 US" in text
    assert "数据缺口" not in text
    assert "未覆盖完整分析区间" not in text


def test_data_need_model_visible_text_allows_empty_lockup_event_as_no_records_found() -> None:
    text = _data_need_model_visible_text(
        request={"market": "CN_A", "instrument": "600519.SH", "item": "解禁"},
        need_domain="market",
        status="ready",
        planned_count=2,
        scheduled_count=2,
        refs=_NeedResultRefs(),
        gaps=(),
        attempts=(
            {"status": "empty", "remote_attempted": True, "error_code": "provider_empty"},
            {"status": "empty", "remote_attempted": True, "error_code": "provider_empty"},
        ),
        data_results=(),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "查询范围内未发现该类事件记录" in text
    assert "不要重复请求同一数据需求" in text
    assert "未返回事件记录" not in text
    assert "数据缺口" not in text
    assert "不得写具体价格" not in text


def test_payload_need_satisfied_infers_from_formal_rows_without_material_gap() -> None:
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.READY,
        rows=({"timestamp": "2026-06-12T00:00:00Z", "symbol_id": "BTC/USDT", "cvd": -2500.0},),
        dataset_refs=("dataset:raw-only",),
        raw_refs=("raw:cvd",),
        attempt_refs=("attempt:cvd",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    assert _payload_need_satisfied(payload={}, data_results=(result,), status="ready")
    assert _payload_need_satisfied(payload={"need_satisfied": True}, data_results=(result,), status="ready")


def test_crypto_profile_summary_includes_supply_metadata() -> None:
    summary = _row_summary(
        {
            "timestamp": "2026-06-16T00:00:00Z",
            "name": "Bitcoin",
            "symbol": "btc",
            "market_cap_rank": 1,
            "circulating_supply": 20043234.849,
            "total_supply": 20043234.849,
            "max_supply": 21000000,
            "supply_unit": "BTC",
            "description": "Bitcoin has a fixed supply cap of 21 million coins and programmatic halvings.",
        },
        domain="fundamental",
        dataset="company_profile",
    )

    assert "流通供应量 20043234.849 BTC" in summary
    assert "总供应量 20043234.849 BTC" in summary
    assert "最大供应量 21000000 BTC" in summary


def test_unsatisfied_rows_are_not_presented_as_body_usable() -> None:
    need = DataNeed(
        need_id="need-macro",
        api_id="macro_news",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.NORMAL,
        requested_by_worker="news_analyst",
        purpose="news_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:macro_news:discovery",)
    refs.raw_refs = ("raw:macro_news",)
    refs.attempt_refs = ("attempt:macro_news",)
    result = DataResult(
        request_id="data_need:call:news:0:macro_news",
        status=DataResultStatus.READY,
        rows=(
            {
                "title": "Bitcoin macro search clue",
                "source_roles": ("discovery",),
                "can_be_formal_fact_source": False,
            },
        ),
        dataset_refs=refs.dataset_refs,
        raw_refs=refs.raw_refs,
        attempt_refs=refs.attempt_refs,
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=False,
    )

    assert "已经可用于正文" not in text
    assert "没有满足当前数据需求" not in text
    assert "不要当成正文可引用结论" not in text
    assert "媒体/搜索线索已返回" in text
    assert "不能单独作为正式事实源" in text
    assert "不要重复请求同一数据需求" in text


def test_data_need_model_visible_text_keeps_current_result_gap_visible() -> None:
    need = DataNeed(
        need_id="need-cvd",
        api_id="cvd",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.NORMAL,
        requested_by_worker="market_analyst",
        purpose="market_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    gap = DataGap.by_reason(
        GapReason.FIELD_MISSING,
        request_id="data_need:call:market:0:crypto_derivative_metric",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="hourly",
        symbol_id="BTC/USDT",
        required_fields=("cvd",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:cvd:partial",)
    refs.raw_refs = ("raw:cvd",)
    refs.attempt_refs = ("attempt:cvd",)
    refs.gaps = (gap,)
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.PARTIAL,
        rows=({"timestamp": "2026-06-12T00:00:00Z", "cvd": -2500.0, "symbol_id": "BTC/USDT"},),
        dataset_refs=refs.dataset_refs,
        raw_refs=refs.raw_refs,
        attempt_refs=refs.attempt_refs,
        gaps=(gap,),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=refs.gaps,
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "状态：" not in text
    assert "本次数据需求已经完成" in text
    assert "报告只引用已返回的可引用材料" in text
    assert "只有部分可用于正文" not in text
    assert "数据缺口" not in text
    assert "必需字段缺失" not in text
    assert "候选尝试限制" not in text


def test_data_need_model_visible_text_hides_resolver_mapping_machine_reason() -> None:
    need = DataNeed(
        need_id="need-technical",
        api_id="technical_indicators",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        priority=NeedPriority.NORMAL,
        requested_by_worker="market_analyst",
        purpose="market_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="missing",
        planned_count=0,
        scheduled_count=0,
        refs=_NeedResultRefs(),
        gaps=(
            DataNeedGap(
                need_id="need-technical",
                reason=GapReason.CATALOG_MATCH_MISSING,
                human_readable="catalog match missing",
            ),
        ),
        attempts=(),
    )

    assert "项目数据项未绑定可调用接口" not in text
    assert "catalog match missing" not in text
    assert "catalog_match_missing" not in text
    assert text == ""
    assert "只引用已批准上游材料中的已有事实" not in text
    assert "材料外内容直接跳过" not in text


def test_data_need_model_visible_text_for_raw_refs_only_forbids_model_memory_fill() -> None:
    need = DataNeed(
        need_id="need-fundamental",
        api_id="financial_metric",
        market=Market.CN_A,
        instrument="600519.SH",
        priority=NeedPriority.NORMAL,
        requested_by_worker="fundamental_analyst",
        purpose="fundamental_report",
        deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    refs = _NeedResultRefs()
    refs.raw_refs = ("raw:tushare:fina_indicator",)
    refs.attempt_refs = ("attempt:tushare:fina_indicator",)

    text = _data_need_model_visible_text(
        request=_request_payload_from_need(need),
        need_domain=_domain_from_need(need),
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(),
    )

    assert "不要重复请求同一数据需求" in text
    assert "没有返回可直接引用的结构化数值或明细行" not in text
    assert "缺口" not in text
    assert "不得用模型记忆" in text
    assert "营收、利润、ROE、PE/PB、目标价" in text


def test_data_need_without_data_refs_is_missing_even_when_attempts_exist() -> None:
    refs = _NeedResultRefs()
    refs.attempt_refs = ("attempt:cn_a:market:1",)

    status = _data_need_status(
        refs=refs,
        gap_count=1,
        attempts=({"status": "success"},),
    )

    assert status == "missing"


def test_data_need_status_does_not_mark_refs_only_as_ready() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:some-ref",)

    status = _data_need_status(refs=refs, gap_count=0, attempts=())

    assert status == "partial"


def test_report_data_prefetch_is_disabled_and_writes_manifest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    payload = run_report_data_prefetch(object(), run_id="run-prefetch", evidence_root=tmp_path)

    assert payload["ok"] is False
    assert payload["reason"] == "report_prefetch_disabled_use_data_need"
    manifest_path = tmp_path / "data-layer" / "report-prefetch.json"
    assert manifest_path.exists()
    summary = summarize_report_prefetch_manifest(manifest_path, ticker="600519.SH", company_name="贵州茅台")
    assert "已返回数据读数" in summary
    assert "材料外内容直接跳过" not in summary
    assert "缺失" not in summary


def test_report_data_evidence_summary_reads_data_need_results(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result_path = tmp_path / "data-layer" / "data-need-results" / "run-need" / "call-financial" / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "provider_attempts_summary": [
                    {
                        "remote_attempted": True,
                        "remote_success": True,
                        "gap_reasons": [],
                    }
                ],
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:financial_metric",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "period": "2026-03-31",
                                "roe": 31.2,
                                "roa": 8.6,
                                "gross_margin": 91.7,
                                "debt_ratio": 12.1,
                                "eps": 21.68,
                            }
                        ],
                    },
                    {
                        "request_id": "data_need:call:news:1:macro_news",
                        "status": "missing",
                        "sample_rows": [],
                        "gaps": [{"reason": "catalog_match_missing"}],
                    },
                        {
                            "request_id": "data_need:call:flow:2:capital_flow",
                            "status": "ready",
                            "sample_rows": [
                                {
                                    "date": "2026-06-12",
                                    "amount": 175700000.0,
                                }
                            ],
                        },
                        {
                            "request_id": "data_need:call:flow:3:northbound_flow",
                            "status": "ready",
                            "sample_rows": [
                                {
                                    "date": "2026-06-12",
                                    "hgt_net": 1.2,
                                    "sgt_net": -0.4,
                                    "northbound_net": 0.8,
                                    "amount_unit": "CNY 亿元",
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        encoding="utf-8",
    )
    missing_path = tmp_path / "data-layer" / "data-need-results" / "run-need" / "call-sector" / "result.json"
    missing_path.parent.mkdir(parents=True, exist_ok=True)
    missing_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "missing",
                "request": {"item": "板块"},
                "gaps": [{"reason": "permission_denied"}],
                "provider_attempts_summary": [
                    {
                        "remote_attempted": True,
                        "remote_success": False,
                        "gap_reasons": ["permission_denied"],
                    }
                ],
                "data_results": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_report_data_need_results(
        tmp_path / "data-layer" / "data-need-results" / "run-need",
        ticker="600519.SH",
        company_name="贵州茅台",
    )

    assert "已返回数据读数" in summary
    assert "材料外内容直接跳过" not in summary
    assert "数据源调用证据" not in summary
    assert "候选尝试" not in summary
    assert "财务指标最新记录" in summary
    assert "ROE 31.2" in summary
    assert "毛利率 91.7" in summary
    assert "EPS/PE口径" in summary
    assert "可以把季度EPS年化作为前瞻情景推演" in summary
    assert "不能冒充静态PE、TTM PE或已确认全年EPS" in summary
    assert "不得直接用季度EPS计算全年、TTM或静态PE" not in summary
    assert "北向资金口径" in summary
    assert "市场/通道级背景" in summary
    assert "不等同于 贵州茅台 个股北向净买入或净卖出" in summary
    assert "不得写成该股被北向资金、外资或聪明钱单独增减持" in summary
    assert "板块缺失（接口权限不足）" not in summary
    assert "数据缺口" not in summary
    assert "缺失" not in summary
    assert "未取得" not in summary


def test_report_data_evidence_summary_normalizes_cny_10k_market_cap_for_margin_ratio(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result_path = tmp_path / "data-layer" / "data-need-results" / "run-need" / "call-valuation" / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:valuation_metric",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "date": "2026-06-18",
                                "pe": 77.89,
                                "market_cap": 2463945.1208,
                                "market_cap_unit": "CNY_10K",
                            }
                        ],
                    },
                    {
                        "request_id": "data_need:call:hot_money:1:margin_trading",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "date": "2026-06-17",
                                "financing_balance": 1090578848.0,
                                "margin_balance": 1090578848.0,
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_report_data_need_results(
        tmp_path / "data-layer" / "data-need-results" / "run-need",
        ticker="300319.SZ",
        company_name="麦捷科技",
    )

    assert "市值 2463945.1208 CNY_10K（约246.39亿元）" in summary
    assert "融资余额 1090578848.0 CNY（约10.91亿元）" in summary
    assert "融资余额/市值约 4.43%" in summary
    assert "44%" not in summary


@pytest.mark.parametrize("gap_reason", ["field_missing", "credential_missing", "provider_error"])
def test_report_data_evidence_summary_does_not_report_gap_when_dataset_has_clean_fallback(
    tmp_path,
    gap_reason: str,
) -> None:  # type: ignore[no-untyped-def]
    result_path = tmp_path / "data-layer" / "data-need-results" / "run-need" / "call-macro" / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "data_results": [
                    {
                        "request_id": "data_need:call:news:0:macro_news",
                        "status": "partial",
                        "sample_rows": [{"source": "Coinglass"}],
                        "gaps": [{"reason": gap_reason}],
                    },
                    {
                        "request_id": "data_need:call:news:1:macro_news",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "published_at": "2026-06-12T12:00:00Z",
                                "title": "Fed liquidity update",
                                "source": "Google News",
                            }
                        ],
                        "gaps": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_report_data_need_results(
        tmp_path / "data-layer" / "data-need-results" / "run-need",
        ticker="BTC/USDT",
        company_name="Bitcoin",
    )

    assert "宏观口径" not in summary
    assert "数据缺口" not in summary
    assert "必需字段缺失" not in summary
    assert "接口凭证缺失" not in summary


def test_report_data_evidence_summary_suppresses_earlier_worker_gap_when_same_public_item_later_succeeds(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "data-layer" / "data-need-results" / "run-need"
    missing_path = root / "call-market-ahr999" / "result.json"
    missing_path.parent.mkdir(parents=True, exist_ok=True)
    missing_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "missing",
                "request": {
                    "request_id": "run:market:data:crypto.ahr999",
                    "item": "AHR999",
                    "market": "CRYPTO",
                },
                "gaps": [{"reason": "provider_error"}],
                "data_results": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ready_path = root / "call-fundamental-ahr999" / "result.json"
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "request": {
                    "request_id": "run:fundamental:data:crypto.ahr999",
                    "item": "AHR999",
                    "market": "CRYPTO",
                },
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:crypto_onchain_metric",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-15T00:00:00Z",
                                "metric": "ahr999",
                                "ahr999": 0.3475,
                                "value": 0.3475,
                            }
                        ],
                        "gaps": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_report_data_need_results(
        root,
        ticker="BTC/USDT",
        company_name="Bitcoin",
    )

    assert "AHR999缺失" not in summary
    assert "仍需披露的数据缺口" not in summary


def test_report_data_evidence_summary_lists_crypto_success_readings_and_overrides_old_gaps(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "data-layer" / "data-need-results" / "run-need"
    result_path = root / "call-market-derivatives" / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "partial",
                "request": {
                    "request_id": "run:market:data:crypto.liquidation_heatmap",
                    "item": "清算地图",
                    "market": "CRYPTO",
                },
                "gaps": [{"reason": "credential_missing"}],
                "data_results": [
                    {
                        "request_id": "data_need:call:market:0:crypto_derivative_metric",
                        "status": "ready",
                        "row_count": 4500,
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-15T12:00:00Z",
                                "funding_rate": -0.004,
                                "funding_rate_unit": "percent",
                                "long_short_ratio": 1.32,
                                "liquidation_price": 49935.58,
                                "liquidation_size": 3816866.4,
                                "cvd": -6727312982.0,
                                "taker_buy_volume": 77790918.0,
                                "taker_sell_volume": 181207681.0,
                                "taker_volume_unit": "USD",
                            }
                        ],
                        "gaps": [],
                    }
                ],
                "latest_value_summary": [
                    {
                        "request_id": "data_need:call:market:0:crypto_derivative_metric",
                        "label": "资金费率",
                        "value": -0.004,
                        "unit_or_scope": "percent",
                        "time": "2026-06-15T12:00:00Z",
                        "source": "COINGLASS_AGGREGATED",
                        "granularity": "hourly",
                        "sample_range": ["2025-12-10", "2026-06-15"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    profile_path = root / "call-fundamental-profile" / "result.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "request": {
                    "request_id": "run:fundamental:data:crypto.company_profile",
                    "item": "项目资料",
                    "market": "CRYPTO",
                },
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:company_profile",
                        "status": "ready",
                        "row_count": 1,
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-15T12:00:00Z",
                                "name": "Bitcoin",
                                "circulating_supply": 20043375.0,
                                "total_supply": 20043375.0,
                                "max_supply": 21000000.0,
                                "supply_unit": "BTC",
                            }
                        ],
                        "gaps": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    etf_path = root / "call-fundamental-etf" / "result.json"
    etf_path.parent.mkdir(parents=True, exist_ok=True)
    etf_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "request": {
                    "request_id": "run:fundamental:data:crypto.etf_flow",
                    "item": "ETF资金流",
                    "market": "CRYPTO",
                },
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:crypto_derivative_metric",
                        "status": "ready",
                        "row_count": 6923,
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-15T00:00:00Z",
                                "etf_flow_usd": 66400000.0,
                                "etf_flow_usd_unit": "USD",
                            }
                        ],
                        "gaps": [],
                    }
                ],
                "latest_value_summary": [
                    {
                        "request_id": "data_need:call:fundamental:0:crypto_derivative_metric",
                        "label": "ETF资金流",
                        "value": 66400000.0,
                        "unit_or_scope": "USD",
                        "time": "2026-06-15T00:00:00Z",
                        "source": "COINGLASS_AGGREGATED",
                        "granularity": "daily",
                        "sample_range": ["2024-01-11", "2026-06-15"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    onchain_path = root / "call-fundamental-onchain" / "result.json"
    onchain_path.parent.mkdir(parents=True, exist_ok=True)
    onchain_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "request": {
                    "request_id": "run:fundamental:data:crypto.ahr999",
                    "item": "AHR999",
                    "market": "CRYPTO",
                },
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:crypto_onchain_metric",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-15T12:00:00Z",
                                "net_inflow": 50086209.0,
                            },
                            {
                                "timestamp": "2026-06-15T00:00:00Z",
                                "ahr999": 0.3475,
                                "value_unit": "dimensionless",
                            }
                        ],
                        "gaps": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_report_data_need_results(
        root,
        ticker="BTC/USDT",
        company_name="Bitcoin",
    )

    assert "已返回数据读数" in summary
    assert "材料外内容直接跳过" not in summary
    assert "资金费率 -0.004 percent" in summary
    assert "样本 4500 行" in summary
    assert "粒度 hourly" in summary
    assert "覆盖 2025-12-10 至 2026-06-15" in summary
    assert "多空比 1.32" in summary
    assert "清算热图价格点 49935.58" in summary
    assert "CVD -6727312982.0 USD" in summary
    assert "ETF资金流 66400000.0 USD" in summary
    assert "样本 6923 行" in summary
    assert "覆盖 2024-01-11 至 2026-06-15" in summary
    assert "项目资料可用：流通供应量 20043375.0 BTC，总供应量 20043375.0 BTC，最大供应量 21000000.0 BTC" in summary
    assert "交易所净流量 50086209.0" in summary
    assert "AHR999 0.3475 dimensionless" in summary
    assert "接口凭证缺失" not in summary


def test_report_data_evidence_summary_suppresses_conflicting_crypto_valuation_identity(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "data-layer" / "data-need-results" / "run-need"
    market_path = root / "call-market-daily" / "result.json"
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "data_results": [
                    {
                        "request_id": "data_need:call:market:0:daily_bar",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "date": "2026-06-20",
                                "symbol_id": "ALLO/USDT",
                                "close": 0.3968,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    valuation_path = root / "call-fundamental-valuation" / "result.json"
    valuation_path.parent.mkdir(parents=True, exist_ok=True)
    valuation_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:0:valuation_metric",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-20T13:26:44Z",
                                "symbol_id": "ALLO/USDT",
                                "price": 0.0012925,
                                "price_unit": "USD",
                                "market_cap": 2327958.0,
                                "market_cap_unit": "USD",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    profile_path = root / "call-fundamental-profile" / "result.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "data_need_tool_evidence.v1",
                "status": "ready",
                "data_results": [
                    {
                        "request_id": "data_need:call:fundamental:1:company_profile",
                        "status": "ready",
                        "sample_rows": [
                            {
                                "timestamp": "2026-06-20T13:26:44Z",
                                "symbol_id": "ALLO/USDT",
                                "circulating_supply": 1800000000.0,
                                "total_supply": 10000000000.0,
                                "max_supply": 10000000000.0,
                                "supply_unit": "ALLO",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_report_data_need_results(
        root,
        ticker="ALLO/USDT",
        company_name="Allora",
    )

    assert "已返回数据读数" in summary
    assert "实时估值/行情快照" not in summary
    assert "0.0012925" not in summary
    assert "2327958.0" not in summary
    assert "项目资料可用" not in summary
    assert "1800000000.0" not in summary


def test_cn_a_market_need_refreshes_remote_when_local_seed_is_stale(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    now = datetime.now(tz=UTC)
    gap = DataGap.by_reason(
        GapReason.DATE_RANGE_MISSING,
        request_id="data_need:warehouse:market:0:daily_bar",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        symbol_id="600519.SH",
        as_of=now,
    )
    local_result = DataResult(
        request_id="data_need:warehouse:market:0:daily_bar",
        status=DataResultStatus.PARTIAL,
        rows=(
            {"date": "2025-06-13", "open": 1500.0, "high": 1520.0, "low": 1490.0, "close": 1510.0, "volume": 1200000},
            {"date": "2026-05-27", "open": 1420.0, "high": 1438.0, "low": 1412.0, "close": 1430.0, "volume": 1100000},
        ),
        dataset_refs=("dataset:factory-seed:daily_bar:600519.SH",),
        raw_refs=(),
        attempt_refs=("attempt:factory-seed",),
        gaps=(gap,),
        as_of=now,
    )
    requested: list[object] = []

    def read_warehouse_batch(requests):  # type: ignore[no-untyped-def]
        requested.extend(requests)
        assert requests[0].freshness_policy == "warehouse_only"
        assert requests[0].data_type == "daily_bar"
        return [local_result]

    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "daily_bar",
                        "symbol_id": "600519.SH",
                        "date": "2026-06-13",
                        "open": 1450.0,
                        "high": 1460.0,
                        "low": 1440.0,
                        "close": 1455.0,
                        "volume": 1300000,
                    }
                ]
            },
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:{batch.batch_id}",),
            raw_refs=(f"raw:{batch.batch_id}",),
            attempt_refs=(f"attempt:{batch.batch_id}",),
            gaps=(),
            remote_success=True,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(read_warehouse_batch=read_warehouse_batch, execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        return NeedPlan(
            plan_id="plan-cn-a-seed",
            needs=(need,),
            planned_calls=(_provider_call("call-remote", need),),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2025-06-13", "end": "2026-06-13"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run-seed",
            "call_id": "call-seed",
            "current_date": "2026-06-13",
            "current_time": now.isoformat(),
        },
    )

    assert requested
    assert payload["ok"] is True
    assert payload["status"] == "partial"
    assert payload["planned_calls_count"] == 1
    assert payload["scheduled_calls_count"] == 1
    assert fetched == ["call-remote"]
    assert "dataset:factory-seed:daily_bar:600519.SH" in payload["normalized_refs"]
    assert "dataset:call-remote" in payload["normalized_refs"]
    assert "attempt:factory-seed" in payload["attempt_refs"]
    assert "attempt:call-remote" in payload["attempt_refs"]
    assert payload["gaps"][0]["reason"] == GapReason.DATE_RANGE_MISSING.value
    assert payload["data_results"][0]["request_id"] == "data_need:warehouse:market:0:daily_bar"
    assert payload["data_results"][1]["rows"][0]["date"] == "2026-06-13"
    assert "未覆盖完整分析区间" not in payload["model_visible_text"]
    assert "报告只引用已返回的可引用材料" in payload["model_visible_text"]
    assert "进入调度" not in payload["model_visible_text"]


def test_data_need_returns_budget_gap_instead_of_spawning_provider_after_deadline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    fetch_engine = SimpleNamespace(fetch=lambda batch: (_ for _ in ()).throw(AssertionError("provider should not run")))
    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=fetch_engine,
        ingest=SimpleNamespace(ingest=lambda result, batch: IngestResult.failed(GapReason.PROVIDER_ERROR, batch_id=batch.batch_id)),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-late", need)
        return NeedPlan(
            plan_id="plan-late",
            needs=(need,),
            planned_calls=(call,),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "1")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "missing"
    assert payload["readable_summary"] == payload["model_visible_text"]
    assert payload["provider_attempts_summary"] == []
    assert payload["gaps"][0]["reason"] == GapReason.RATE_LIMITED_BY_TOOL_BUDGET.value
    assert payload["model_visible_text"] == ""
    assert "数据结果：" not in payload["model_visible_text"]
    assert "任务预算内无法等待限流窗口" not in payload["model_visible_text"]
    assert "报告只引用已返回的可引用材料" not in payload["model_visible_text"]
    assert "材料外内容直接跳过" not in payload["model_visible_text"]


def test_data_need_writes_trace_before_execution_gate_wait(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    class Gate:
        def __init__(self) -> None:
            self.entered_batches: list[str] = []

        def enter(self, batch):  # type: ignore[no-untyped-def]
            self.entered_batches.append(batch.batch_id)
            return GateDecision.rate_limited(None, "rate_limited_by_tool_budget")

    gate = Gate()
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: RateLimitPolicy(window_seconds=60, max_requests=None)),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=SimpleNamespace(fetch=lambda batch: (_ for _ in ()).throw(AssertionError("fetch should not run"))),
        ingest=SimpleNamespace(record_gate_result=_record_gate_result),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        return NeedPlan(
            plan_id="plan-gate-trace",
            needs=(need,),
            planned_calls=(_provider_call("call-gate-trace", need),),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run-gate-trace",
            "call_id": "call-gate-trace",
            "evidence_root": str(tmp_path),
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    events = _data_need_trace_events(tmp_path, run_id="run-gate-trace", call_id="call-gate-trace")

    assert payload["ok"] is True
    assert gate.entered_batches == ["call-gate-trace"]
    assert [event["event"] for event in events] == ["execution_gate_enter"]
    assert events[0]["provider_id"] == "official_api_slow"
    assert events[0]["catalog_endpoint_id"] == "tushare.daily"
    assert events[0]["single_flight_key"] == "batch:call-gate-trace"


def test_data_need_writes_trace_before_provider_fetch(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    def fetch(batch):  # type: ignore[no-untyped-def]
        return FetchResult.from_error(batch, status="error", error=RuntimeError("provider failed"))

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(),
            raw_refs=(),
            attempt_refs=("attempt:fetch-error",),
            gaps=(DataGap.by_reason(GapReason.PROVIDER_ERROR, request_id=batch.batch_id, market=Market.CN_A, data_type=batch.data_type),),
            remote_success=False,
        )

    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: RateLimitPolicy(window_seconds=60, max_requests=None)),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        return NeedPlan(
            plan_id="plan-fetch-trace",
            needs=(need,),
            planned_calls=(_provider_call("call-fetch-trace", need),),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run-fetch-trace",
            "call_id": "call-fetch-trace",
            "evidence_root": str(tmp_path),
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    events = _data_need_trace_events(tmp_path, run_id="run-fetch-trace", call_id="call-fetch-trace")

    assert payload["ok"] is True
    assert [event["event"] for event in events] == ["execution_gate_enter", "provider_fetch_start"]
    assert events[1]["provider_id"] == "official_api_slow"
    assert events[1]["catalog_endpoint_id"] == "tushare.daily"


def test_data_need_gate_wait_timeout_advances_to_next_candidate_with_attempt_evidence(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    class Gate:
        def __init__(self) -> None:
            self.entered_batches: list[str] = []

        def enter(self, batch):  # type: ignore[no-untyped-def]
            self.entered_batches.append(batch.batch_id)
            if batch.batch_id == "call-wait-timeout":
                return GateDecision.rate_limited(None, "rate_limited_by_tool_budget")
            return GateDecision.owner("owner-token")

        def publish_shared_result(self, single_flight_key, owner_token, ingest, *, batch=None):  # type: ignore[no-untyped-def]
            return True

        def wait_after_rate_limited_fetch(self, batch, fetch_result):  # type: ignore[no-untyped-def]
            return False

        def mark_cooldown_after_fetch(self, batch, fetch_result):  # type: ignore[no-untyped-def]
            return None

    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "daily_bar",
                        "symbol_id": "600519.SH",
                        "date": "2026-06-12",
                        "open": 1490.0,
                        "high": 1510.0,
                        "low": 1480.0,
                        "close": 1500.0,
                        "volume": 1000000.0,
                    }
                ]
            },
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:{batch.batch_id}",),
            raw_refs=(f"raw:{batch.batch_id}",),
            attempt_refs=(f"attempt:{batch.batch_id}",),
            gaps=(),
            remote_success=True,
        )

    gate = Gate()
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: RateLimitPolicy(window_seconds=60, max_requests=None)),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest, record_gate_result=_record_gate_result),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        wait_call = _provider_call("call-wait-timeout", need)
        fallback_call = _provider_call("call-after-timeout", need).model_copy(
            update={
                "provider_id": "cn_a_fast_fallback",
                "official_path_or_api_name": "daily_fallback",
                "rate_limit_bucket": "ratelimit:fast",
                "batch_key": "batch:call-after-timeout",
            }
        )
        return NeedPlan(
            plan_id="plan-gate-timeout-fallback",
            needs=(need,),
            planned_calls=(wait_call, fallback_call),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run-gate-timeout",
            "call_id": "call-gate-timeout",
            "evidence_root": str(tmp_path),
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    events = _data_need_trace_events(tmp_path, run_id="run-gate-timeout", call_id="call-gate-timeout")

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert gate.entered_batches == ["call-wait-timeout", "call-after-timeout"]
    assert fetched == ["call-after-timeout"]
    assert [item["provider_id"] for item in payload["provider_attempts_summary"]] == [
        "official_api_slow",
        "cn_a_fast_fallback",
    ]
    assert payload["provider_attempts_summary"][0]["status"] == "rate_limited"
    assert payload["provider_attempts_summary"][0]["error_message"] == "rate_limited_by_tool_budget"
    assert [event["event"] for event in events] == [
        "execution_gate_enter",
        "execution_gate_enter",
        "provider_fetch_start",
    ]
    assert events[0]["provider_call_id"] == "call-wait-timeout"
    assert events[1]["provider_call_id"] == "call-after-timeout"


def test_data_need_provider_timeout_becomes_formal_attempt_not_subprocess_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    def slow_fetch(batch):  # type: ignore[no-untyped-def]
        sleep(2)
        return FetchResult.from_success(batch, payload={"rows": []})

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(),
            raw_refs=(),
            attempt_refs=("attempt:slow",),
            gaps=(DataGap.by_reason(GapReason.PROVIDER_ERROR, request_id=batch.batch_id, market=Market.CN_A, data_type=batch.data_type),),
            remote_success=False,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=slow_fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-slow", need)
        return NeedPlan(
            plan_id="plan-slow",
            needs=(need,),
            planned_calls=(call,),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "3")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["provider_attempts_summary"][0]["status"] == "error"
    assert payload["attempt_refs"] == ("attempt:slow",)
    assert payload["gaps"][0]["reason"] == GapReason.PROVIDER_ERROR.value


def test_data_need_provider_timeout_does_not_block_fallback_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        if batch.batch_id == "call-slow":
            sleep(2)
            return FetchResult.from_success(batch, payload={"rows": []})
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "daily_bar",
                        "symbol_id": "600519.SH",
                        "date": "2026-06-12",
                        "open": 1450.0,
                        "high": 1460.0,
                        "low": 1440.0,
                        "close": 1455.0,
                        "volume": 1300000,
                    }
                ]
            },
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        if str(getattr(result, "status", "")) == "error":
            return IngestResult.from_refs(
                batch_id=batch.batch_id,
                dataset_refs=(),
                raw_refs=(),
                attempt_refs=(f"attempt:{batch.batch_id}",),
                gaps=(DataGap.by_reason(GapReason.PROVIDER_ERROR, request_id=batch.batch_id, market=Market.CN_A, data_type=batch.data_type),),
                remote_success=False,
            )
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:{batch.batch_id}",),
            raw_refs=(f"raw:{batch.batch_id}",),
            attempt_refs=(f"attempt:{batch.batch_id}",),
            gaps=(),
            remote_success=True,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        slow_call = _provider_call("call-slow", need)
        fast_call = _provider_call("call-fast", need).model_copy(
            update={
                "provider_id": "cn_a_fast_fallback",
                "official_path_or_api_name": "daily_fallback",
                "rate_limit_bucket": "ratelimit:fast",
                "batch_key": "batch:call-fast",
            }
        )
        return NeedPlan(
            plan_id="plan-timeout-fallback",
            needs=(need,),
            planned_calls=(slow_call, fast_call),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "10")
    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_PROVIDER_CALL_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert fetched == ["call-slow", "call-fast"]
    assert [item["provider_id"] for item in payload["provider_attempts_summary"]] == [
        "official_api_slow",
        "cn_a_fast_fallback",
    ]
    assert payload["data_results"][0]["rows"][0]["close"] == 1455.0


def test_composition_data_need_continues_after_first_component_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.endpoint_id)
        series_id = str(batch.params["provider_call_spec"]["params"]["series_id"])
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "macro_series",
                        "market": "CRYPTO",
                        "symbol_id": "BTC/USDT",
                        "date": "2026-05-01",
                        "period_start": "2026-05-01",
                        "period_end": "2026-05-01",
                        "series_id": series_id,
                        "value": 3.63,
                        "unit": "FRED",
                    }
                ]
            },
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        series_id = str(batch.params["provider_call_spec"]["params"]["series_id"])
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:macro:{series_id}",),
            raw_refs=(f"raw:macro:{series_id}",),
            attempt_refs=(f"attempt:macro:{series_id}",),
            gaps=(),
            remote_success=True,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "宏观",
            "purpose": "market_report",
            "instrument": "BTC/USDT",
            "market": "CRYPTO",
            "time_range": {"start": "2026-05-01", "end": "2026-06-15"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-15",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert {
        "fred.series_fedfunds",
        "fred.series_dgs10",
        "fred.series_walcl",
        "fred.series_m2sl",
        "fred.series_cpiaucsl",
    } <= set(fetched)


def test_crypto_long_short_stops_after_primary_ratio_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        call_spec = batch.params["provider_call_spec"]
        endpoint_id = str(call_spec["catalog_endpoint_id"])
        fetched.append(endpoint_id)
        if endpoint_id == "coinglass.raw_bitfinex_margin_long_short":
            return FetchResult.from_error(batch, status="error", error=RuntimeError("symbol_not_supported"))
        metric = {
            "coinglass.futures_long_short_ratio": "global_account_long_short_ratio",
            "coinglass.raw_futures_top_long_short_account_ratio_history": "top_account_long_short_ratio",
            "coinglass.raw_futures_top_long_short_position_ratio_history": "top_position_long_short_ratio",
        }.get(endpoint_id, endpoint_id.rsplit(".", 1)[-1])
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "crypto_derivative_metric",
                        "market": "CRYPTO",
                        "symbol_id": "BTC/USDT",
                        "timestamp": "2026-06-15T12:00:00Z",
                        "period_start": "2026-06-15",
                        "period_end": "2026-06-15",
                        "long_short_ratio": 1.23,
                        "metric": metric,
                    }
                ]
            },
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        endpoint_id = str(batch.params["provider_call_spec"]["catalog_endpoint_id"])
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:long-short:{endpoint_id}",),
            raw_refs=(f"raw:long-short:{endpoint_id}",),
            attempt_refs=(f"attempt:long-short:{endpoint_id}",),
            gaps=(),
            remote_success=True,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "多空比",
            "purpose": "market_report",
            "instrument": "BTC/USDT",
            "market": "CRYPTO",
            "time_range": {"start": "2026-05-01", "end": "2026-06-15"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-15",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert fetched == ["coinglass.futures_long_short_ratio"]
    assert "binance.futures_long_short_ratio" not in set(fetched)


def test_tushare_provider_timer_is_capped_below_global_provider_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_PROVIDER_CALL_TIMEOUT_SECONDS", "12")
    deadline_at = datetime.now(tz=UTC) + timedelta(seconds=60)

    assert report_evidence._provider_call_timeout_seconds(deadline_at, provider_id="official_api_tushare") <= 6.0
    assert report_evidence._provider_call_timeout_seconds(deadline_at, provider_id="cn_a_eastmoney_market_data") > 6.0
    assert (
        report_evidence._provider_call_timeout_seconds(
            deadline_at,
            provider_id="official_api_coinglass",
            catalog_endpoint_id="coinglass.futures_liquidation_heatmap",
        )
        >= 55.0
    )
    assert (
        report_evidence._provider_call_timeout_seconds(
            deadline_at,
            provider_id="official_api_coinglass",
            catalog_endpoint_id="coinglass.futures_liquidation_heatmap",
            fallback_available=True,
        )
        >= 55.0
    )
    assert (
        report_evidence._provider_call_timeout_seconds(
            deadline_at,
            provider_id="official_api_coinglass",
            catalog_endpoint_id="coinglass.bitcoin_ahr999",
        )
        >= 55.0
    )


def test_data_need_scheduler_uses_runtime_rate_limiter_before_fetch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    repository = DatasetRepository()
    rate_limiter = RateLimiter(repository, now_fn=lambda: now)
    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=1)
    assert rate_limiter.reserve("ratelimit:slow", rate_limit_policy).allowed
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=lambda batch: (_ for _ in ()).throw(AssertionError("fetch should be skipped by scheduler"))),
        ingest=SimpleNamespace(ingest=lambda result, batch: IngestResult.failed(GapReason.PROVIDER_ERROR, batch_id=batch.batch_id)),
        rate_limiter=rate_limiter,
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-window-full", need).model_copy(
            update={"deadline_at": datetime(2026, 6, 12, 12, 0, 30, tzinfo=UTC)}
        )
        return NeedPlan(
            plan_id="plan-window-full",
            needs=(need,),
            planned_calls=(call,),
            created_at=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {"item": "日线", "purpose": "market_report", "instrument": "600519.SH", "market": "CN_A"},
        {
            "worker_id": "market_analyst",
            "run_id": "run-window-full",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime(2026, 6, 12, 12, 0, tzinfo=UTC).isoformat(),
        },
    )

    assert payload["status"] == "missing"
    assert payload["scheduled_calls_count"] == 0
    assert payload["gaps"][0]["reason"] == GapReason.RATE_LIMITED_BY_TOOL_BUDGET.value


def test_data_need_stops_after_first_structured_ready_result(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "daily_bar",
                        "symbol_id": "600519.SH",
                        "date": "2026-06-12",
                        "open": 1490.0,
                        "high": 1510.0,
                        "low": 1480.0,
                        "close": 1500.0,
                        "volume": 1000000.0,
                    }
                ]
            },
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:{batch.batch_id}",),
            raw_refs=(f"raw:{batch.batch_id}",),
            attempt_refs=(f"attempt:{batch.batch_id}",),
            gaps=(),
            remote_success=True,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(ingest=ingest),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        return NeedPlan(
            plan_id="plan-ready",
            needs=(need,),
            planned_calls=(_provider_call("call-ready", need), _provider_call("call-fallback", need)),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["status"] == "ready"
    assert fetched == ["call-ready"]
    assert payload["attempt_refs"] == ("attempt:call-ready",)


def test_data_need_cache_hit_uses_execution_gate_and_skips_fetch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    repository = DatasetRepository()
    repository.insert_normalized(
        {
            "dataset_ref": "dataset:cache",
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "granularity": "daily",
            "period_start": datetime(2026, 6, 12, tzinfo=UTC),
            "period_end": datetime(2026, 6, 12, tzinfo=UTC),
            "exchange": "SSE",
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "base_asset": None,
            "quote_asset": None,
            "provider_lineage": {"provider": "unit"},
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
            "row": {
                "date": "2026-06-12",
                "open": 1490.0,
                "high": 1510.0,
                "low": 1480.0,
                "close": 1500.0,
                "volume": 1000000.0,
            },
        }
    )
    gate = _CacheHitExecutionGate()
    fetch_engine = SimpleNamespace(fetch=lambda batch: (_ for _ in ()).throw(AssertionError("cache hit should not fetch")))
    ingest = SimpleNamespace(record_gate_result=_record_gate_result)
    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=fetch_engine,
        ingest=ingest,
        repository=repository,
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        return NeedPlan(
            plan_id="plan-cache-hit",
            needs=(need,),
            planned_calls=(_provider_call("call-cache-hit", need),),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["normalized_refs"] == ("dataset:cache",)
    assert payload["provider_attempts_summary"][0]["gate_kind"] == "cache_hit"
    assert gate.entered_batches == ("call-cache-hit",)


def test_same_semantic_data_need_uses_run_scoped_gate_cache_across_workers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    repository = DatasetRepository()
    gate = ExecutionGate(
        cache=ProviderResultCache(repository),
        rate_limiter=RateLimiter(repository),
        single_flight=SingleFlight(repository),
    )
    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "daily_bar",
                        "symbol_id": "600519.SH",
                        "date": "2026-06-12",
                        "open": 1490.0,
                        "high": 1510.0,
                        "low": 1480.0,
                        "close": 1500.0,
                        "volume": 1000000.0,
                    }
                ]
            },
            row_count=1,
        )

    def ingest_fetch(result, batch):  # type: ignore[no-untyped-def]
        repository.insert_normalized(
            {
                "dataset_ref": "dataset:shared",
                "dataset": "daily_bar",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "daily",
                "period_start": datetime(2026, 6, 12, tzinfo=UTC),
                "period_end": datetime(2026, 6, 12, tzinfo=UTC),
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider": "unit"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
                "row": {
                    "date": "2026-06-12",
                    "open": 1490.0,
                    "high": 1510.0,
                    "low": 1480.0,
                    "close": 1500.0,
                    "volume": 1000000.0,
                },
            }
        )
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=("dataset:shared",),
            raw_refs=("raw:shared",),
            attempt_refs=("attempt:owner",),
            gaps=(),
            remote_success=True,
            cache_key=batch.cache_key,
            cache_fresh_until=datetime.now(tz=UTC) + timedelta(seconds=60),
            cache_stale_until=datetime.now(tz=UTC) + timedelta(seconds=300),
        )

    ingest = SimpleNamespace(ingest=ingest_fetch, record_gate_result=_record_gate_result)
    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=10)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=ingest,
        repository=repository,
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        return NeedPlan(
            plan_id="plan-shared",
            needs=(need,),
            planned_calls=(_provider_call("call-shared", need),),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    first = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run-shared",
            "call_id": "call-a",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )
    second = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "fundamental_analyst",
            "run_id": "run-shared",
            "call_id": "call-b",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert first["status"] == "ready"
    assert second["status"] == "ready"
    assert fetched == ["call-shared"]
    assert second["normalized_refs"] == ("dataset:shared",)
    assert second["provider_attempts_summary"][0]["gate_kind"] == "cache_hit"


def test_raw_only_data_need_cache_hit_preserves_parser_missing_gap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    repository = DatasetRepository()
    gate = ExecutionGate(
        cache=ProviderResultCache(repository),
        rate_limiter=RateLimiter(repository),
        single_flight=SingleFlight(repository),
    )
    fetched: list[str] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        return FetchResult.from_success(batch, payload={"rows": [{"raw_payload": {"ok": True}}]}, row_count=1)

    def ingest_fetch(_result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(),
            raw_refs=("raw:only",),
            attempt_refs=("attempt:raw-owner",),
            gaps=(
                DataGap.by_reason(
                    GapReason.PARSER_MISSING,
                    request_id=batch.batch_id,
                    market=Market.CN_A,
                    data_type=batch.data_type,
                    granularity=batch.granularity,
                    evidence_refs=("raw:only",),
                ),
            ),
            remote_success=True,
            cache_key=batch.cache_key,
            cache_fresh_until=datetime.now(tz=UTC) + timedelta(seconds=60),
            cache_stale_until=datetime.now(tz=UTC) + timedelta(seconds=300),
        )

    ingest = SimpleNamespace(ingest=ingest_fetch, record_gate_result=_record_gate_result)
    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=10)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=ingest,
        rate_limiter=gate.rate_limiter,
        repository=repository,
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-raw-only", need).model_copy(
            update={
                "parser_status": "parser_missing",
                "batch_key": "batch:raw-only",
            }
        )
        return NeedPlan(plan_id="plan-raw-only", needs=(need,), planned_calls=(call,), created_at=datetime.now(tz=UTC))

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    request = {"item": "财务指标", "purpose": "fundamental_report", "instrument": "600519.SH", "market": "CN_A"}
    first = run_claw_request_data(
        request,
        {
            "worker_id": "fundamental_analyst",
            "run_id": "run-raw",
            "call_id": "call-a",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )
    second = run_claw_request_data(
        request,
        {
            "worker_id": "market_analyst",
            "run_id": "run-raw",
            "call_id": "call-b",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert first["status"] == "partial"
    assert second["status"] == "partial"
    assert fetched == ["call-raw-only"]
    assert second["raw_refs"] == ("raw:only",)
    assert second["provider_attempts_summary"][0]["gate_kind"] == "cache_hit"
    assert second["gaps"][0]["reason"] == GapReason.PARSER_MISSING.value


def test_concurrent_same_semantic_data_need_uses_single_flight_shared_result(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    repository = DatasetRepository()
    gate = ExecutionGate(
        cache=ProviderResultCache(repository),
        rate_limiter=RateLimiter(repository),
        single_flight=SingleFlight(repository),
    )
    owner_started = threading.Event()
    release_owner = threading.Event()
    fetched: list[str] = []
    payloads: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        owner_started.set()
        assert release_owner.wait(timeout=2)
        return FetchResult.from_success(
            batch,
            payload={
                "rows": [
                    {
                        "dataset": "daily_bar",
                        "symbol_id": "600519.SH",
                        "date": "2026-06-12",
                        "open": 1490.0,
                        "high": 1510.0,
                        "low": 1480.0,
                        "close": 1500.0,
                        "volume": 1000000.0,
                    }
                ]
            },
            row_count=1,
        )

    def ingest_fetch(_result, batch):  # type: ignore[no-untyped-def]
        repository.insert_normalized(
            {
                "dataset_ref": "dataset:single-flight",
                "dataset": "daily_bar",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "daily",
                "period_start": datetime(2026, 6, 12, tzinfo=UTC),
                "period_end": datetime(2026, 6, 12, tzinfo=UTC),
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider": "unit"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
                "row": {
                    "date": "2026-06-12",
                    "open": 1490.0,
                    "high": 1510.0,
                    "low": 1480.0,
                    "close": 1500.0,
                    "volume": 1000000.0,
                },
            }
        )
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=("dataset:single-flight",),
            raw_refs=("raw:single-flight",),
            attempt_refs=("attempt:owner",),
            gaps=(),
            remote_success=True,
            cache_key=batch.cache_key,
            cache_fresh_until=datetime.now(tz=UTC) + timedelta(seconds=60),
            cache_stale_until=datetime.now(tz=UTC) + timedelta(seconds=300),
        )

    ingest = SimpleNamespace(ingest=ingest_fetch, record_gate_result=_record_gate_result)
    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=10)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=ingest,
        rate_limiter=gate.rate_limiter,
        repository=repository,
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-concurrent", need).model_copy(update={"batch_key": "batch:concurrent"})
        return NeedPlan(plan_id="plan-concurrent", needs=(need,), planned_calls=(call,), created_at=datetime.now(tz=UTC))

    def run(worker_id: str, call_id: str) -> None:
        try:
            payloads.append(
                run_claw_request_data(
                    {
                        "item": "日线",
                        "purpose": "market_report",
                        "instrument": "600519.SH",
                        "market": "CN_A",
                        "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
                    },
                    {
                        "worker_id": worker_id,
                        "run_id": "run-concurrent",
                        "call_id": call_id,
                        "current_date": "2026-06-12",
                        "current_time": datetime.now(tz=UTC).isoformat(),
                    },
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    monkeypatch.setenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", "60")
    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    owner = threading.Thread(target=run, args=("market_analyst", "call-a"))
    waiter = threading.Thread(target=run, args=("fundamental_analyst", "call-b"))
    owner.start()
    assert owner_started.wait(timeout=2)
    waiter.start()
    sleep(0.05)
    release_owner.set()
    owner.join(timeout=2)
    waiter.join(timeout=2)

    assert errors == []
    assert len(payloads) == 2
    assert fetched == ["call-concurrent"]
    assert {payload["status"] for payload in payloads} == {"ready"}
    gate_kinds = [payload["provider_attempts_summary"][0].get("gate_kind") for payload in payloads]
    assert "shared_result" in gate_kinds


class _OwnerExecutionGate:
    def enter(self, batch):  # type: ignore[no-untyped-def]
        return GateDecision.owner("owner-token")

    def publish_shared_result(self, single_flight_key, owner_token, ingest, *, batch=None):  # type: ignore[no-untyped-def]
        return True

    def wait_after_rate_limited_fetch(self, batch, fetch_result):  # type: ignore[no-untyped-def]
        return False

    def mark_cooldown_after_fetch(self, batch, fetch_result):  # type: ignore[no-untyped-def]
        return None


class _CacheHitExecutionGate(_OwnerExecutionGate):
    def __init__(self) -> None:
        self.entered_batches: tuple[str, ...] = ()

    def enter(self, batch):  # type: ignore[no-untyped-def]
        self.entered_batches = (*self.entered_batches, batch.batch_id)
        return GateDecision.cache_hit(
            ResultRefs(
                dataset_refs=("dataset:cache",),
                raw_refs=("raw:cache",),
                attempt_refs=("attempt:source",),
            )
        )


def _data_need_trace_events(root, *, run_id: str, call_id: str):  # type: ignore[no-untyped-def]
    path = root / "data-layer" / "data-need-trace" / run_id / call_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record_gate_result(batch, gate):  # type: ignore[no-untyped-def]
    gaps = ()
    if tuple(gate.refs.raw_refs) and not tuple(gate.refs.dataset_refs) and str(getattr(batch, "parser_status", "")) in {"raw_only", "parser_missing"}:
        gaps = (
            DataGap.by_reason(
                GapReason.PARSER_MISSING,
                request_id=batch.batch_id,
                market=Market.CN_A,
                data_type=batch.data_type,
                granularity=batch.granularity,
                evidence_refs=tuple(gate.refs.raw_refs),
            ),
        )
    return IngestResult(
        batch_id=batch.batch_id,
        status="non_remote_recorded",
        dataset_refs=tuple(gate.refs.dataset_refs),
        raw_refs=tuple(gate.refs.raw_refs),
        attempt_refs=("attempt:gate",),
        gaps=gaps,
        remote_success=False,
        cache_key=batch.cache_key,
    )


def _provider_call(call_id: str, need: DataNeed) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_id=call_id,
        provider_id="official_api_slow",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        params={"symbol": need.instrument},
        auth_scope="none",
        rate_limit_bucket="ratelimit:slow",
        http_visibility="managed_http",
        parser_status="normalized",
        batch_key=f"batch:{call_id}",
        official_doc_ref="test",
        deadline_at=need.deadline_at,
        need_ids=(need.need_id,),
    )
