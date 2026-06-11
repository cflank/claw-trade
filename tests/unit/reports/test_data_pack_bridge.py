from __future__ import annotations

import json
import multiprocessing as mp
import time
from datetime import UTC, date, datetime

from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, Market
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.reports.data_pack_bridge import (
    _build_requests,
    _filter_crypto_market_bars,
    _model_visible_text,
    _report_prefetch_domain_timeout_seconds,
    _validate_report_data_results,
    run_frontline_data_pack,
    run_report_data_prefetch,
)
from claw_trade.reports.data_evidence_summary import summarize_report_prefetch_manifest
from claw_trade.workflow.models import RunRequest, WorkflowEntryPoint


def test_model_visible_text_hides_machine_fields_from_worker_prompt() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run:call:news:1:company_news",
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap.by_reason(
                    "date_range_missing",
                    request_id="run:call:news:1:company_news",
                    market=Market.CRYPTO,
                    data_type="company_news",
                    granularity="event",
                    message="normalized rows do not expose period_start/period_end",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_news_pack"},
        market=Market.CRYPTO,
        domain="news",
        status="missing",
        results=results,
    )

    assert "未覆盖完整分析区间" in text
    assert "来源尝试记录" in text
    for token in (
        "claw_get_",
        "dataset_refs",
        "raw_refs",
        "attempt_refs",
        "date_range_missing",
        "warehouse_missing",
        "readiness",
        "data_gaps",
        "provider_attempts",
        "period_start",
        "period_end",
    ):
        assert token not in text


def test_report_prefetch_domain_timeout_is_disabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", raising=False)

    assert _report_prefetch_domain_timeout_seconds() == 0.0


def test_crypto_social_model_visible_text_includes_score_and_sentiment() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    result = DataResult(
        request_id="run:call:social:1:social_signal",
        status=DataResultStatus.READY,
        rows=(
            {
                "source": "Alternative.me Fear & Greed",
                "timestamp": "2026-06-09T00:00:00Z",
                "score": 10.0,
                "sentiment": "Extreme Fear",
                "symbol_id": "BTCUSDT",
            },
        ),
        dataset_refs=("dataset:social_signal:CRYPTO:fixture",),
        attempt_refs=("attempt:sentiment",),
        as_of=as_of,
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_social_pack"},
        market=Market.CRYPTO,
        domain="social",
        status="ready",
        results=(result,),
    )

    assert "分数 10.0" in text
    assert "情绪 Extreme Fear" in text


def test_crypto_market_domain_timeout_text_does_not_claim_all_sources_unavailable() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    result = DataResult(
        request_id="run:call:market:1:daily_bar",
        status=DataResultStatus.ERROR,
        gaps=(
            DataGap.by_reason(
                "provider_error",
                request_id="run:call:market:1:daily_bar",
                market=Market.CRYPTO,
                data_type="daily_bar",
                granularity="daily",
                message="report_prefetch_domain_timeout:market:0.2s",
                as_of=as_of,
            ),
        ),
        as_of=as_of,
    )

    text = _model_visible_text(
        tool_input={"ticker": "BNB", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_market_pack"},
        market=Market.CRYPTO,
        domain="market",
        status="error",
        results=(result,),
    )

    assert "本资料域因预取超时" in text
    assert "不等于其它资料域没有调用外部来源" in text
    assert "全局外部来源没有尝试" in text
    assert "不能代表其它资料域或全局外部来源调用情况" in text
    assert "未形成可引用的数据集、原始或元数据引用、来源尝试记录" not in text
    assert "0 个来源尝试记录" not in text


def test_crypto_google_news_model_visible_text_hides_search_titles() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    result = DataResult(
        request_id="run:call:news:1:macro_news",
        status=DataResultStatus.READY,
        rows=(
            {
                "title": "Bitcoin drops as macro pressure rises",
                "published_at": "2026-06-09T00:00:00Z",
                "source": "Google News",
                "summary": "Search result summary",
                "url": "https://example.com/news",
                "symbol_id": "BTCUSDT",
            },
        ),
        dataset_refs=("dataset:macro_news:CRYPTO:fixture",),
        attempt_refs=("attempt:news",),
        as_of=as_of,
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_news_pack"},
        market=Market.CRYPTO,
        domain="news",
        status="ready",
        results=(result,),
    )

    assert "来源 媒体聚合搜索线索" in text
    assert "公开搜索线索，不能单独作为正式事实源" in text
    assert "Bitcoin drops as macro pressure rises" not in text
    assert "Search result summary" not in text


def test_cn_a_fundamental_model_visible_text_includes_values_and_statement_basis() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    statement = DataResult(
        request_id="run:call:fundamental:1:financial_statement",
        status=DataResultStatus.READY,
        rows=(
            {
                "period": "2026-03-31",
                "period_start": "2026-03-31",
                "period_end": "2026-03-31",
                "revenue": 35277000000.0,
                "net_income": 14523000000.0,
                "assets": 6033962000000.0,
                "liabilities": 5489879000000.0,
                "cash_flow": 37802000000.0,
                "revenue_basis": "period_cumulative",
                "net_income_basis": "period_cumulative",
                "cash_flow_basis": "period_cumulative",
                "assets_basis": "period_end_point_in_time",
                "liabilities_basis": "period_end_point_in_time",
            },
        ),
        dataset_refs=("dataset:financial_statement:CN_A:fixture",),
        attempt_refs=("attempt:statement",),
        as_of=as_of,
    )
    valuation = DataResult(
        request_id="run:call:fundamental:3:valuation_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "period_start": "2026-06-09",
                "period_end": "2026-06-09",
                "pe": 5.0662,
                "pb": 0.4654,
                "ps": 1.6432,
                "market_cap": 21598786.9544,
                "market_cap_unit": "CNY_10K",
            },
        ),
        dataset_refs=("dataset:valuation_metric:CN_A:fixture",),
        attempt_refs=("attempt:valuation",),
        as_of=as_of,
    )

    text = _model_visible_text(
        tool_input={"ticker": "000001.SZ", "market": "CN_A"},
        runtime_context={"tool_name": "claw_get_fundamental_pack"},
        market=Market.CN_A,
        domain="fundamental",
        status="partial",
        results=(statement, valuation),
    )

    assert "收入 35277000000.0" in text
    assert "净利润 14523000000.0" in text
    assert "资产 6033962000000.0" in text
    assert "现金流 37802000000.0" in text
    assert "PE" not in text
    assert "市盈率 5.0662" in text
    assert "市净率 0.4654" in text
    assert "报告期累计流量" in text
    assert "不得把累计数直接写成单季度" in text


def test_report_prefetch_summary_preserves_run3_style_gaps_and_available_data(tmp_path) -> None:  # type: ignore[no-untyped-def]
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    payload = {
        "ok": True,
        "schema_version": "report_data_prefetch.v1",
        "run_id": "run-prefetch",
        "market": "CN_A",
        "status": "partial",
        "domain_statuses": (
            {"domain": "market", "status": "partial"},
            {"domain": "fundamental", "status": "partial"},
            {"domain": "news", "status": "partial"},
            {"domain": "social", "status": "partial"},
        ),
        "data_results": [
            DataResult(
                request_id="run:call:market:2:intraday_bar",
                status=DataResultStatus.MISSING,
                gaps=(
                    DataGap.by_reason(
                        "provider_error",
                        request_id="run:call:market:2:intraday_bar",
                        market=Market.CN_A,
                        data_type="intraday_bar",
                        granularity="intraday",
                        message="timeout",
                        as_of=as_of,
                    ),
                ),
                as_of=as_of,
            ).model_dump(mode="json"),
            DataResult(
                request_id="run:call:market:4:order_book_snapshot",
                status=DataResultStatus.MISSING,
                gaps=(
                    DataGap.by_reason(
                        "provider_error",
                        request_id="run:call:market:4:order_book_snapshot",
                        market=Market.CN_A,
                        data_type="order_book_snapshot",
                        granularity="realtime",
                        message="timeout",
                        as_of=as_of,
                    ),
                ),
                as_of=as_of,
            ).model_dump(mode="json"),
            DataResult(
                request_id="run:call:market:5:capital_flow",
                status=DataResultStatus.READY,
                rows=({"date": "2026-06-08", "main_net": 5425.84, "amount_unit": "CNY_10K"},),
                dataset_refs=("dataset:capital_flow:CN_A:fixture",),
                as_of=as_of,
            ).model_dump(mode="json"),
            DataResult(
                request_id="run:call:social:1:social_signal",
                status=DataResultStatus.READY,
                rows=({"source": "irm_qa_sz", "timestamp": "2026-05-27", "question": "问", "answer": "答"},),
                dataset_refs=("dataset:social_signal:CN_A:fixture",),
                as_of=as_of,
            ).model_dump(mode="json"),
        ],
    }
    manifest_path = tmp_path / "report-prefetch.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")

    summary = summarize_report_prefetch_manifest(manifest_path, ticker="000001.SZ", company_name="平安银行")

    assert "日内分时数据缺失" in summary
    assert "盘口快照缺失" in summary
    assert "资金流数据可用" in summary
    assert "互动问答原文 1 行" in summary
    assert "data_results" not in summary
    assert "dataset_refs" not in summary


def test_report_data_prefetch_batches_frontline_requests_by_domain(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[object, ...]] = []

    class _Api:
        def get_data_batch(self, requests):
            calls.append(tuple(requests))
            return [
                DataResult(
                    request_id=request.request_id,
                    status=DataResultStatus.READY,
                    dataset_refs=(f"dataset:{request.data_type}:US:{request.request_id}",),
                    attempt_refs=(f"attempt:{request.request_id}",),
                    as_of=datetime(2026, 6, 2, tzinfo=UTC),
                )
                for request in requests
            ]

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", lambda: _Api())
    monkeypatch.setenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "0")
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-06-02",
        start_date="2025-06-02",
        end_date="2026-06-02",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )

    payload = run_report_data_prefetch(request, run_id="run-prefetch", evidence_root=tmp_path)

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["domains"] == ("market", "fundamental", "news", "social")
    assert len(calls) == 4
    assert [item["domain"] for item in payload["domain_statuses"]] == ["market", "fundamental", "news", "social"]
    request_ids = [request.request_id for call in calls for request in call]
    assert any(":market:" in item for item in request_ids)
    assert any(":fundamental:" in item for item in request_ids)
    assert any(":news:" in item for item in request_ids)
    assert any(":social:" in item for item in request_ids)
    assert (tmp_path / "data-layer" / "report-prefetch.json").exists()


def test_report_data_prefetch_includes_hk_social_domain(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[object, ...]] = []

    class _Api:
        def get_data_batch(self, requests):
            calls.append(tuple(requests))
            return [
                DataResult(
                    request_id=request.request_id,
                    status=DataResultStatus.READY,
                    dataset_refs=(f"dataset:{request.data_type}:HK:{request.request_id}",),
                    attempt_refs=(f"attempt:{request.request_id}",),
                    as_of=datetime(2026, 6, 2, tzinfo=UTC),
                )
                for request in requests
            ]

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", lambda: _Api())
    monkeypatch.setenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "0")
    request = RunRequest(
        ticker="00700",
        company_name="Tencent",
        market="HK",
        profile="HK",
        currency="HKD",
        currency_symbol="HK$",
        current_date="2026-06-02",
        start_date="2025-06-02",
        end_date="2026-06-02",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )

    payload = run_report_data_prefetch(request, run_id="run-hk-prefetch", evidence_root=tmp_path)

    assert payload["ok"] is True
    assert payload["domains"] == ("market", "fundamental", "news", "social")
    assert "social" in payload["domains"]
    assert len(calls) == 4
    requests = tuple(request for call in calls for request in call)
    assert any(request.data_type == "social_signal" for request in requests)
    assert any(":social:" in request.request_id for request in requests)


def test_report_data_prefetch_writes_partial_manifest_when_one_domain_errors(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    class _Api:
        def get_data_batch(self, requests):
            if any(":market:" in request.request_id for request in requests):
                raise RuntimeError("market source failed")
            return [
                DataResult(
                    request_id=request.request_id,
                    status=DataResultStatus.READY,
                    dataset_refs=(f"dataset:{request.data_type}:US:{request.request_id}",),
                    attempt_refs=(f"attempt:{request.request_id}",),
                    as_of=datetime(2026, 6, 2, tzinfo=UTC),
                )
                for request in requests
            ]

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", lambda: _Api())
    monkeypatch.setenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "0")
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-06-02",
        start_date="2025-06-02",
        end_date="2026-06-02",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )

    payload = run_report_data_prefetch(request, run_id="run-prefetch-partial", evidence_root=tmp_path)

    assert payload["ok"] is True
    assert payload["status"] == "partial"
    assert payload["domain_statuses"][0]["domain"] == "market"
    assert payload["domain_statuses"][0]["status"] == "error"
    assert payload["domain_statuses"][1]["status"] == "ready"
    assert any(
        result["status"] == "error"
        and result["gaps"][0]["human_readable"].startswith("report_prefetch_domain_error:market:")
        for result in payload["data_results"]
    )
    assert (tmp_path / "data-layer" / "report-prefetch.json").exists()


def test_report_data_prefetch_times_out_one_domain_and_keeps_other_domains(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    class _Api:
        def get_data_batch(self, requests):
            if any(":market:" in request.request_id for request in requests):
                time.sleep(2)
            return [
                DataResult(
                    request_id=request.request_id,
                    status=DataResultStatus.READY,
                    dataset_refs=(f"dataset:{request.data_type}:US:{request.request_id}",),
                    attempt_refs=(f"attempt:{request.request_id}",),
                    as_of=datetime(2026, 6, 2, tzinfo=UTC),
                )
                for request in requests
            ]

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", lambda: _Api())
    monkeypatch.setenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "0.2")
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-06-02",
        start_date="2025-06-02",
        end_date="2026-06-02",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )

    payload = run_report_data_prefetch(request, run_id="run-prefetch-timeout", evidence_root=tmp_path)

    assert payload["ok"] is True
    assert payload["status"] == "partial"
    assert payload["domain_statuses"][0]["domain"] == "market"
    assert payload["domain_statuses"][0]["timed_out"] is True
    assert payload["domain_statuses"][1]["status"] == "ready"
    assert any(
        result["status"] == "error"
        and result["gaps"][0]["human_readable"] == "report_prefetch_domain_timeout:market:0.2s"
        for result in payload["data_results"]
    )


def test_report_data_prefetch_timeout_keeps_completed_requests_in_same_domain(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    if "fork" not in mp.get_all_start_methods():
        return

    class _Api:
        def get_data_batch(self, requests):
            request = tuple(requests)[0]
            if request.data_type == "quote_snapshot":
                time.sleep(2)
            return [
                DataResult(
                    request_id=request.request_id,
                    status=DataResultStatus.READY,
                    rows=({"date": "2026-06-02", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},),
                    dataset_refs=(f"dataset:{request.data_type}:US:{request.request_id}",),
                    attempt_refs=(f"attempt:{request.request_id}",),
                    as_of=datetime(2026, 6, 2, tzinfo=UTC),
                )
            ]

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", lambda: _Api())
    monkeypatch.setenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "0.2")
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-06-02",
        start_date="2025-06-02",
        end_date="2026-06-02",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )

    payload = run_report_data_prefetch(request, run_id="run-prefetch-partial-timeout", evidence_root=tmp_path)

    market_status = payload["domain_statuses"][0]
    assert market_status["domain"] == "market"
    assert market_status["status"] == "partial"
    assert market_status["timed_out"] is True
    market_results = [item for item in payload["data_results"] if ":market:" in item["request_id"]]
    assert any(item["status"] == "ready" and ":daily_bar" in item["request_id"] for item in market_results)
    assert any(
        item["status"] == "error"
        and ":quote_snapshot" in item["request_id"]
        and item["gaps"][0]["human_readable"] == "report_prefetch_domain_timeout:market:0.2s"
        for item in market_results
    )


def test_report_data_prefetch_writes_large_subprocess_payload_without_deadlock(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    if "fork" not in mp.get_all_start_methods():
        return

    large_rows = tuple({"date": "2026-06-02", "value": index, "blob": "x" * 2000} for index in range(80))

    class _Api:
        def get_data_batch(self, requests):
            return [
                DataResult(
                    request_id=request.request_id,
                    status=DataResultStatus.READY,
                    rows=large_rows,
                    dataset_refs=(f"dataset:{request.data_type}:US:{request.request_id}",),
                    attempt_refs=(f"attempt:{request.request_id}",),
                    as_of=datetime(2026, 6, 2, tzinfo=UTC),
                )
                for request in requests
            ]

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", lambda: _Api())
    monkeypatch.setenv("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "5")
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-06-02",
        start_date="2025-06-02",
        end_date="2026-06-02",
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )

    payload = run_report_data_prefetch(request, run_id="run-prefetch-large-payload", evidence_root=tmp_path)

    assert payload["ok"] is True
    assert all(item["timed_out"] is False for item in payload["domain_statuses"])
    assert len(payload["data_results"]) == payload["request_count"]
    assert (tmp_path / "data-layer" / "report-prefetch.json").exists()


def test_crypto_report_requests_do_not_force_realtime_provider_data_to_history_window() -> None:
    tool_input = {
        "ticker": "SOL",
        "market": "CRYPTO",
        "currency": "USDT",
        "current_date": "2026-06-08",
        "start_date": "2025-06-01",
        "end_date": "2026-06-06",
    }
    runtime_context = {"run_id": "run", "call_id": "call", "worker_id": "report_prefetch"}

    market_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "market"},
        market=Market.CRYPTO,
        domain="market",
    )
    fundamental_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "fundamental"},
        market=Market.CRYPTO,
        domain="fundamental",
    )
    news_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "news"},
        market=Market.CRYPTO,
        domain="news",
    )
    social_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "social"},
        market=Market.CRYPTO,
        domain="social",
    )

    def _first(requests, data_type: str, granularity: str):
        return next(item for item in requests if item.data_type == data_type and item.granularity == granularity)

    def _all(requests, data_type: str, granularity: str):
        return [item for item in requests if item.data_type == data_type and item.granularity == granularity]

    assert _first(market_requests, "daily_bar", "daily").date_range_start == date(2025, 6, 1)
    assert _first(market_requests, "intraday_bar", "1h").date_range_end == date(2026, 6, 6)
    assert "volume_unit" in _first(market_requests, "daily_bar", "daily").fields
    assert "amount_unit" in _first(market_requests, "daily_bar", "daily").fields
    assert "volume_unit" in _first(market_requests, "intraday_bar", "1h").fields
    assert "amount_unit" in _first(market_requests, "intraday_bar", "1h").fields
    assert _first(market_requests, "quote_snapshot", "realtime").date_range_start is None
    assert _first(market_requests, "order_book_snapshot", "realtime").date_range_end is None
    assert _first(market_requests, "order_book_snapshot", "1h").date_range_start == date(2025, 6, 1)
    assert _first(market_requests, "crypto_derivative_metric", "realtime").date_range_start is None
    assert _first(market_requests, "crypto_derivative_metric", "1h").date_range_end == date(2026, 6, 6)
    assert _first(market_requests, "crypto_onchain_metric", "daily").date_range_start == date(2025, 6, 1)
    assert _first(market_requests, "crypto_onchain_metric", "realtime").date_range_start is None
    assert "open_interest_unit" in _first(market_requests, "crypto_derivative_metric", "realtime").fields
    assert "funding_rate_unit" in _first(market_requests, "crypto_derivative_metric", "1h").fields
    assert "value_unit" in _first(market_requests, "crypto_onchain_metric", "realtime").fields
    assert "value_unit" in _first(market_requests, "crypto_onchain_metric", "daily").fields

    valuation_realtime_requests = _all(fundamental_requests, "valuation_metric", "realtime")
    valuation_fields = valuation_realtime_requests[0].fields
    assert _first(fundamental_requests, "valuation_metric", "realtime").date_range_start is None
    assert "price_unit" in valuation_fields
    assert "market_cap_unit" in valuation_fields
    assert "volume_unit" in valuation_fields
    assert "supply_unit" in _first(fundamental_requests, "valuation_metric", "daily").fields
    assert any("fdv_unit" in item.fields for item in valuation_realtime_requests)
    assert any("total_supply" in item.fields for item in valuation_realtime_requests)
    assert _first(fundamental_requests, "defi_metric", "realtime").date_range_end is None
    assert _first(news_requests, "company_news", "event").date_range_start == date(2025, 6, 1)
    assert _first(social_requests, "social_signal", "event").date_range_start is None


def test_non_btc_crypto_report_requests_skip_btc_only_liquidation_datasets() -> None:
    market_requests = _build_requests(
        tool_input={
            "ticker": "BNB",
            "market": "CRYPTO",
            "currency": "USDT",
            "start_date": "2025-06-01",
            "end_date": "2026-06-06",
            "current_date": "2026-06-06",
        },
        runtime_context={
            "run_id": "run-crypto",
            "call_id": "report-prefetch",
            "pack_domain": "market",
            "worker_id": "market_analyst",
        },
        market=Market.CRYPTO,
        domain="market",
    )

    derivative_fields = [
        set(request.fields)
        for request in market_requests
        if request.data_type == "crypto_derivative_metric"
    ]
    assert not any({"long_liquidation", "short_liquidation", "liquidation_value"} & fields for fields in derivative_fields)
    assert not any({"liquidation_price", "liquidation_size"} & fields for fields in derivative_fields)


def test_cn_a_report_requests_do_not_force_snapshots_to_history_window() -> None:
    tool_input = {
        "ticker": "000001.SZ",
        "market": "CN_A",
        "currency": "CNY",
        "current_date": "2026-06-09",
        "start_date": "2025-06-09",
        "end_date": "2026-06-09",
    }
    runtime_context = {"run_id": "run", "call_id": "call", "worker_id": "report_prefetch"}

    market_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "market"},
        market=Market.CN_A,
        domain="market",
    )
    fundamental_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "fundamental"},
        market=Market.CN_A,
        domain="fundamental",
    )
    social_requests = _build_requests(
        tool_input=tool_input,
        runtime_context={**runtime_context, "pack_domain": "social"},
        market=Market.CN_A,
        domain="social",
    )

    def _first(requests, data_type: str, granularity: str):
        return next(item for item in requests if item.data_type == data_type and item.granularity == granularity)

    assert _first(market_requests, "daily_bar", "daily").date_range_start == date(2025, 6, 9)
    assert _first(market_requests, "daily_bar", "daily").date_range_end == date(2026, 6, 9)
    assert _first(market_requests, "intraday_bar", "intraday").date_range_start == date(2026, 6, 9)
    assert _first(market_requests, "intraday_bar", "intraday").date_range_end == date(2026, 6, 9)
    assert _first(market_requests, "quote_snapshot", "realtime").date_range_start is None
    assert _first(market_requests, "quote_snapshot", "realtime").date_range_end is None
    assert _first(market_requests, "order_book_snapshot", "realtime").date_range_start is None
    assert _first(market_requests, "sector_snapshot", "event").date_range_end is None
    assert "amount_unit" in _first(market_requests, "daily_bar", "daily").fields
    assert "amount_unit" in _first(market_requests, "quote_snapshot", "realtime").fields
    assert "amount_unit" in _first(market_requests, "capital_flow", "daily").fields
    assert "amount_unit" in _first(market_requests, "sector_snapshot", "event").fields
    statement_fields = _first(fundamental_requests, "financial_statement", "quarterly").fields
    assert "amount_unit" in statement_fields
    assert "revenue_basis" in statement_fields
    assert "net_income_basis" in statement_fields
    assert "cash_flow_basis" in statement_fields
    assert "assets_basis" in statement_fields
    assert "liabilities_basis" in statement_fields
    assert "market_cap_unit" in _first(fundamental_requests, "valuation_metric", "daily").fields
    assert "market_cap_unit" in _first(fundamental_requests, "valuation_metric", "realtime").fields
    assert _first(fundamental_requests, "valuation_metric", "realtime").date_range_start is None
    assert _first(social_requests, "social_signal", "event").date_range_start is None


def test_frontline_data_pack_uses_report_prefetch_manifest_without_data_api(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    manifest_path = tmp_path / "data-layer" / "report-prefetch.json"
    manifest_path.parent.mkdir(parents=True)
    result = DataResult(
        request_id="run-prefetch:report-prefetch:news:1:company_news",
        status=DataResultStatus.READY,
        rows=(
            {
                "title": "Company update",
                "published_at": "2026-06-02",
                "source": "provider",
                "summary": "summary",
                "url": "https://example.com/news",
            },
        ),
        dataset_refs=("dataset:company_news:US:fixture",),
        attempt_refs=("attempt:news",),
        as_of=as_of,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "ok": True,
                "schema_version": "report_data_prefetch.v1",
                "run_id": "run-prefetch",
                "market": "US",
                "domains": ("market", "fundamental", "news", "social"),
                "data_results": [result.model_dump(mode="json")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _blocked_api():
        raise AssertionError("frontline data pack must consume report prefetch manifest")

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", _blocked_api)

    payload = run_frontline_data_pack(
        {"ticker": "AAPL", "market": "US", "current_date": "2026-06-02"},
        {
            "run_id": "run-prefetch",
            "tool_name": "claw_get_news_pack",
            "pack_domain": "news",
            "report_prefetch_required": True,
            "report_prefetch_manifest_path": str(manifest_path),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["data_results"][0]["request_id"] == "run-prefetch:report-prefetch:news:1:company_news"


def test_frontline_data_pack_rejects_prefetch_rows_with_wrong_dataset_type(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    manifest_path = tmp_path / "data-layer" / "report-prefetch.json"
    manifest_path.parent.mkdir(parents=True)
    result = DataResult(
        request_id="run-prefetch:report-prefetch:news:1:company_news",
        status=DataResultStatus.READY,
        rows=({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},),
        dataset_refs=("dataset:daily_bar:CN_A:wrong",),
        attempt_refs=("attempt:daily",),
        as_of=as_of,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "ok": True,
                "schema_version": "report_data_prefetch.v1",
                "run_id": "run-prefetch",
                "market": "CN_A",
                "domains": ("market", "fundamental", "news", "social"),
                "data_results": [result.model_dump(mode="json")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _blocked_api():
        raise AssertionError("frontline data pack must consume report prefetch manifest")

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", _blocked_api)

    payload = run_frontline_data_pack(
        {"ticker": "688017.SH", "market": "CN_A", "current_date": "2026-06-02"},
        {
            "run_id": "run-prefetch",
            "tool_name": "claw_get_news_pack",
            "pack_domain": "news",
            "report_prefetch_required": True,
            "report_prefetch_manifest_path": str(manifest_path),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "error"
    assert payload["data_results"][0]["status"] == "error"
    assert payload["data_results"][0]["rows"] == []
    assert payload["data_results"][0]["dataset_refs"] == []
    assert payload["data_results"][0]["gaps"][0]["reason"] == "data_integrity_failed"
    assert "资料包返回的数据类型与请求不一致" in payload["model_visible_text"]
    assert "已返回的数据摘要" not in payload["model_visible_text"]


def test_report_data_validation_rejects_mixed_good_and_bad_rows() -> None:
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    result = DataResult(
        request_id="run-prefetch:report-prefetch:news:1:company_news",
        status=DataResultStatus.READY,
        rows=(
            {
                "title": "公司公告",
                "published_at": "2026-06-02",
                "source": "provider",
                "summary": "摘要",
                "url": "https://example.com/news",
            },
            {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},
        ),
        dataset_refs=("dataset:company_news:CN_A:ok", "dataset:company_news:CN_A:bad"),
        attempt_refs=("attempt:mixed",),
        as_of=as_of,
    )

    (validated,) = _validate_report_data_results(results=(result,), market=Market.CN_A, domain="news")

    assert validated.status == DataResultStatus.ERROR
    assert validated.rows == ()
    assert validated.dataset_refs == ()
    assert validated.gaps[-1].reason.value == "data_integrity_failed"
    assert validated.gaps[-1].human_readable == "report_data_result_field_mismatch"


def test_report_data_validation_rejects_partial_schema_row() -> None:
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    result = DataResult(
        request_id="run-prefetch:report-prefetch:news:1:company_news",
        status=DataResultStatus.READY,
        rows=({"title": "公司公告", "published_at": "2026-06-02", "source": "provider"},),
        dataset_refs=("dataset:company_news:CN_A:partial",),
        attempt_refs=("attempt:partial",),
        as_of=as_of,
    )

    (validated,) = _validate_report_data_results(results=(result,), market=Market.CN_A, domain="news")

    assert validated.status == DataResultStatus.ERROR
    assert validated.rows == ()
    assert validated.dataset_refs == ()
    assert validated.gaps[-1].human_readable == "report_data_result_field_mismatch"


def test_report_data_validation_rejects_noncanonical_or_missing_dataset_refs_for_rows() -> None:
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    noncanonical = DataResult(
        request_id="run-prefetch:report-prefetch:news:1:company_news",
        status=DataResultStatus.READY,
        rows=({"title": "公司公告", "published_at": "2026-06-02", "source": "provider"},),
        dataset_refs=("dataset:news",),
        attempt_refs=("attempt:noncanonical",),
        as_of=as_of,
    )
    missing_ref = noncanonical.model_copy(update={"dataset_refs": (), "attempt_refs": ("attempt:missing-ref",)})

    validated = _validate_report_data_results(results=(noncanonical, missing_ref), market=Market.CN_A, domain="news")

    assert [item.status for item in validated] == [DataResultStatus.ERROR, DataResultStatus.ERROR]
    assert [item.rows for item in validated] == [(), ()]
    assert [item.dataset_refs for item in validated] == [(), ()]
    assert [item.gaps[-1].human_readable for item in validated] == [
        "report_data_result_dataset_mismatch",
        "report_data_result_dataset_mismatch",
    ]


def test_report_data_validation_rejects_unparseable_request_id_with_rows() -> None:
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    result = DataResult(
        request_id="bad-request-id",
        status=DataResultStatus.READY,
        rows=(
            {
                "title": "公司公告",
                "published_at": "2026-06-02",
                "source": "provider",
                "summary": "摘要",
                "url": "https://example.com/news",
            },
        ),
        dataset_refs=("dataset:company_news:CN_A:bad-request",),
        attempt_refs=("attempt:bad-request",),
        as_of=as_of,
    )

    (validated,) = _validate_report_data_results(results=(result,), market=Market.CN_A, domain="news")

    assert validated.status == DataResultStatus.ERROR
    assert validated.rows == ()
    assert validated.dataset_refs == ()
    assert validated.gaps[-1].human_readable == "report_data_result_request_id_mismatch"


def test_report_data_validation_rejects_unknown_dataset_with_rows() -> None:
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    result = DataResult(
        request_id="run-prefetch:report-prefetch:news:1:unknown_news",
        status=DataResultStatus.READY,
        rows=(
            {
                "title": "公司公告",
                "published_at": "2026-06-02",
                "source": "provider",
                "summary": "摘要",
                "url": "https://example.com/news",
            },
        ),
        dataset_refs=("dataset:unknown_news:CN_A:bad-dataset",),
        attempt_refs=("attempt:bad-dataset",),
        as_of=as_of,
    )

    (validated,) = _validate_report_data_results(results=(result,), market=Market.CN_A, domain="news")

    assert validated.status == DataResultStatus.ERROR
    assert validated.rows == ()
    assert validated.dataset_refs == ()
    assert validated.gaps[-1].human_readable == "report_data_result_unknown_dataset"


def test_frontline_data_pack_fails_when_report_prefetch_manifest_missing(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    def _blocked_api():
        raise AssertionError("missing report manifest must not fall back to DataAPI")

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", _blocked_api)

    payload = run_frontline_data_pack(
        {"ticker": "AAPL", "market": "US", "current_date": "2026-06-02"},
        {
            "run_id": "run-prefetch",
            "tool_name": "claw_get_news_pack",
            "pack_domain": "news",
            "report_prefetch_required": True,
            "report_prefetch_manifest_path": str(tmp_path / "data-layer" / "report-prefetch.json"),
        },
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "report_prefetch_manifest_missing"


def test_model_visible_text_summarizes_rows_in_chinese_labels() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run:call:market:1:daily_bar",
            status=DataResultStatus.READY,
            rows=(
                {
                    "period_start": "2026-05-31",
                    "close": 104000,
                    "volume": 1200,
                    "volume_unit": "BTC",
                    "provider_lineage": {"provider": "binance"},
                    "source_raw_refs": ("raw:1",),
                },
            ),
            dataset_refs=("dataset:1",),
            raw_refs=("raw:1",),
            attempt_refs=("attempt:1",),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_market_pack"},
        market=Market.CRYPTO,
        domain="market",
        status="ready",
        results=results,
    )

    assert "日线行情" in text
    assert "收盘价 104000" in text
    assert "成交量 1200 BTC" in text
    for token in ("daily_bar", "period_start", "close=", "volume=", "source_raw_refs", "provider_lineage"):
        assert token not in text


def test_model_visible_text_treats_news_titles_as_discovery_lines_not_facts() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run:call:news:1:company_news",
            status=DataResultStatus.READY,
            rows=(
                {
                    "published_at": "2026-06-01",
                    "title": "Institutional product flow headline should stay audit-only",
                    "source": "Google News",
                },
            ),
            dataset_refs=("dataset:news",),
            raw_refs=("raw:news",),
            attempt_refs=("attempt:news",),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_news_pack"},
        market=Market.CRYPTO,
        domain="news",
        status="ready",
        results=results,
    )

    assert "媒体/搜索线索已返回" in text
    assert "不能升级为报告事实或投资结论" in text
    assert "Institutional product flow" not in text


def test_hk_social_model_visible_text_reports_missing_discussion_signal_as_gap() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run:call:social:1:social_signal",
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap.by_reason(
                    "provider_error",
                    request_id="run:call:social:1:social_signal",
                    market=Market.HK,
                    data_type="social_signal",
                    granularity="event",
                    message="empty_result",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "00700", "market": "HK"},
        runtime_context={"tool_name": "claw_get_social_pack"},
        market=Market.HK,
        domain="social",
        status="missing",
        results=results,
    )

    assert "HK 社交资料包" in text
    assert "社交证据视为缺口" in text
    assert "完整社交情绪" not in text


def test_hk_social_model_visible_text_shows_latest_rows_even_when_provider_rows_are_unsorted() -> None:
    as_of = datetime(2026, 6, 7, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run:call:social:1:social_signal",
            status=DataResultStatus.PARTIAL,
            rows=(
                {"timestamp": "2026-06-02 04:00:00+00:00", "source": "Google News", "title": "June two"},
                {"timestamp": "2026-06-03 05:02:00+00:00", "source": "Google News", "title": "June three"},
                {"timestamp": "2026-06-01 09:58:30+00:00", "source": "Google News", "title": "June one"},
                {"timestamp": "2026-05-30 05:26:33+00:00", "source": "Google News", "title": "May thirty"},
                {"timestamp": "2026-06-05 10:25:00+00:00", "source": "Google News", "title": "June five"},
                {"timestamp": "2026-03-26 07:00:00+00:00", "source": "Google News", "title": "March old"},
                {"timestamp": "2026-05-13 07:00:00+00:00", "source": "Google News", "title": "May earnings"},
                {"timestamp": "2026-01-14 08:00:00+00:00", "source": "Google News", "title": "January stale"},
            ),
            dataset_refs=("dataset:social_signal:HK:fixture",),
            attempt_refs=("attempt:hk_google_news:social_signal_news_heat:fixture",),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "00700", "market": "HK"},
        runtime_context={"tool_name": "claw_get_social_pack"},
        market=Market.HK,
        domain="social",
        status="partial",
        results=results,
    )

    assert "时间覆盖 2026-01-14 至 2026-06-05" in text
    assert "标题 June five" in text
    assert "标题 June three" in text
    assert "标题 January stale" not in text
    assert "标题 May earnings" not in text
    assert text.index("标题 June five") < text.index("标题 May thirty")


def test_crypto_frontline_requests_use_crypto_datasets_instead_of_stock_fundamentals() -> None:
    requests = _build_requests(
        tool_input={
            "ticker": "BTC",
            "market": "CRYPTO",
            "currency": "USDT",
            "current_date": "2026-06-01",
            "start_date": "2025-06-01",
            "end_date": "2026-06-01",
        },
        runtime_context={"pack_domain": "fundamental", "run_id": "run", "call_id": "call", "worker_id": "fundamental_analyst"},
        market=Market.CRYPTO,
        domain="fundamental",
    )

    request_keys = [(request.data_type, request.granularity) for request in requests]
    assert request_keys == [
        ("valuation_metric", "realtime"),
        ("valuation_metric", "daily"),
        ("valuation_metric", "realtime"),
        ("defi_metric", "realtime"),
        ("defi_metric", "daily"),
        ("crypto_derivative_metric", "daily"),
        ("crypto_onchain_metric", "daily"),
        ("crypto_onchain_metric", "realtime"),
    ]
    datasets = [request.data_type for request in requests]
    assert "financial_statement" not in datasets
    assert "financial_metric" not in datasets
    assert all(request.base_asset == "BTC" and request.quote_asset == "USDT" for request in requests)


def test_crypto_market_pack_requests_coinglass_derivative_and_onchain_capabilities() -> None:
    requests = _build_requests(
        tool_input={
            "ticker": "BTC",
            "market": "CRYPTO",
            "currency": "USDT",
            "current_date": "2026-06-01",
            "start_date": "2025-06-01",
            "end_date": "2026-06-01",
        },
        runtime_context={"pack_domain": "market", "run_id": "run", "call_id": "call", "worker_id": "market_analyst"},
        market=Market.CRYPTO,
        domain="market",
    )

    request_fields = {(request.data_type, request.granularity, request.fields) for request in requests}
    assert (
        "crypto_derivative_metric",
        "1h",
        ("taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
    ) in request_fields
    assert (
        "crypto_derivative_metric",
        "1h",
        ("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "timestamp", "symbol_id"),
    ) in request_fields
    assert (
        "order_book_snapshot",
        "1h",
        ("bids_usd", "bids_quantity", "asks_usd", "asks_quantity", "timestamp", "symbol_id"),
    ) in request_fields
    assert (
        "crypto_onchain_metric",
        "realtime",
        ("timestamp", "metric", "value", "value_unit", "chain"),
    ) in request_fields
    assert (
        "crypto_onchain_metric",
        "daily",
        ("timestamp", "metric", "value", "value_unit", "chain"),
    ) in request_fields
    assert (
        "crypto_derivative_metric",
        "realtime",
        ("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
    ) in request_fields


def test_crypto_model_visible_text_labels_derivative_gaps_by_metric() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run-crypto:report-prefetch:market:6:crypto_derivative_metric",
            status=DataResultStatus.READY,
            rows=(
                {
                    "funding_rate": 0.0001,
                    "funding_rate_unit": "percent",
                    "timestamp": "2026-06-09T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
            ),
            dataset_refs=("dataset:funding",),
            attempt_refs=("attempt:funding",),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:8:crypto_derivative_metric",
            status=DataResultStatus.READY,
            rows=(
                {
                    "taker_buy_volume": 1200.0,
                    "taker_sell_volume": 900.0,
                    "taker_volume_unit": "USD",
                    "taker_buy_sell_ratio": 1.3333,
                    "timestamp": "2026-06-09T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
            ),
            dataset_refs=("dataset:taker",),
            attempt_refs=("attempt:taker",),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:10:crypto_derivative_metric",
            status=DataResultStatus.PARTIAL,
            rows=(
                {
                    "liquidation_price": 68000.0,
                    "liquidation_price_unit": "USDT",
                    "liquidation_size": 1200000.0,
                    "liquidation_size_unit": "USD",
                    "side": "long",
                    "timestamp": "2026-06-09T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
            ),
            gaps=(
                DataGap.by_reason(
                    "date_range_missing",
                    request_id="run-crypto:report-prefetch:market:10:crypto_derivative_metric",
                    market=Market.CRYPTO,
                    data_type="crypto_derivative_metric",
                    granularity="1h",
                    message="date_range_missing",
                    as_of=as_of,
                ),
                DataGap.by_reason(
                    "empty_result",
                    request_id="run-crypto:report-prefetch:market:10:crypto_derivative_metric",
                    market=Market.CRYPTO,
                    data_type="crypto_derivative_metric",
                    granularity="1h",
                    message="empty_result",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:11:crypto_derivative_metric",
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap.by_reason(
                    "rate_limited",
                    request_id="run-crypto:report-prefetch:market:11:crypto_derivative_metric",
                    market=Market.CRYPTO,
                    data_type="crypto_derivative_metric",
                    granularity="1h",
                    evidence_refs=("attempt:options",),
                    message="rate_limited",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:12:crypto_derivative_metric",
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap.by_reason(
                    "field_missing",
                    request_id="run-crypto:report-prefetch:market:12:crypto_derivative_metric",
                    market=Market.CRYPTO,
                    data_type="crypto_derivative_metric",
                    granularity="1h",
                    required_fields=("cvd",),
                    message="field_missing",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"pack_domain": "market", "run_id": "run-crypto", "call_id": "report-prefetch"},
        market=Market.CRYPTO,
        domain="market",
        status="partial",
        results=results,
    )

    assert "字段覆盖 资金费率" in text
    assert "样本限制：" in text
    assert "清算热力图：已有 1 行，覆盖 2026-06-09 至 2026-06-09，样本或来源限制：未覆盖完整分析区间、来源返回为空；先分析现有样本" in text
    assert "清算热力图：未覆盖完整分析区间" not in text
    assert "清算热力图：来源返回为空" not in text
    assert "没有可用数据集时" not in text
    assert "期权未平仓量/成交量：来源限流" in text
    assert "标准 CVD：标准 CVD 序列未返回；已有主动买卖量代理 1 行，覆盖 2026-06-09 至 2026-06-09，可先分析主动买卖量差额" in text
    assert "标准 CVD：必需字段缺失" not in text
    assert "衍生品数据：未覆盖完整分析区间" not in text
    assert "衍生品数据：来源限流" not in text


def test_non_btc_crypto_liquidation_is_btc_only_not_missing_gap() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run-crypto:report-prefetch:market:1:daily_bar",
            status=DataResultStatus.READY,
            rows=(
                {
                    "date": "2026-06-09",
                    "open": 600.0,
                    "high": 620.0,
                    "low": 590.0,
                    "close": 610.0,
                    "volume": 1000.0,
                    "volume_unit": "BNB",
                    "amount": 610000.0,
                    "amount_unit": "USDT",
                    "symbol_id": "BNBUSDT",
                },
            ),
            dataset_refs=("dataset:daily_bar:CRYPTO:spot:BNBUSDT",),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:10:crypto_derivative_metric",
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap.by_reason(
                    "warehouse_missing",
                    request_id="run-crypto:report-prefetch:market:10:crypto_derivative_metric",
                    market=Market.CRYPTO,
                    data_type="crypto_derivative_metric",
                    granularity="1h",
                    required_fields=("liquidation_price", "liquidation_size"),
                    message="warehouse_missing",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
    )
    crypto_lens_payload = {
        "crypto_lens_status": "ready",
        "crypto_lens_analysis": {
            "status": "partial",
            "technical_patterns": {"evidence": {}},
            "derivatives_context": {"evidence": {}},
            "liquidation_context": {"evidence": {}},
            "onchain_context": {"evidence": {}},
            "ahr999_context": {"evidence": {}},
        },
    }

    text = _model_visible_text(
        tool_input={"ticker": "BNB", "market": "CRYPTO", "currency": "USDT"},
        runtime_context={"pack_domain": "market", "run_id": "run-crypto", "call_id": "report-prefetch"},
        market=Market.CRYPTO,
        domain="market",
        status="partial",
        results=results,
        crypto_lens_payload=crypto_lens_payload,
    )

    assert "| 清算地图 | 不适用 | BNB 不写清算地图；当前清算地图按 BTC 专用口径使用 |" in text
    assert "| AHR999 | 不适用 | BNB 不写 AHR999；该指标按 BTC 估值指数使用 |" in text
    assert "清算热力图：仓库没有可用记录" not in text
    assert "清算热力图：必需字段缺失" not in text
    assert "数据缺口：" not in text


def test_btc_market_pack_does_not_claim_ahr999_globally_missing_when_domain_has_no_ahr999() -> None:
    as_of = datetime(2026, 6, 9, tzinfo=UTC)
    result = DataResult(
        request_id="run-crypto:report-prefetch:market:1:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-09",
                "close": 104000.0,
                "volume": 1200.0,
                "volume_unit": "BTC",
                "symbol_id": "BTCUSDT",
            },
        ),
        dataset_refs=("dataset:daily_bar:CRYPTO:spot:BTCUSDT",),
        as_of=as_of,
    )
    crypto_lens_payload = {
        "crypto_lens_status": "ready",
        "crypto_lens_analysis": {
            "status": "partial",
            "technical_patterns": {"evidence": {}},
            "derivatives_context": {"evidence": {}},
            "liquidation_context": {"evidence": {}},
            "onchain_context": {"evidence": {}},
            "ahr999_context": {"evidence": {}},
        },
    }

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO", "currency": "USDT"},
        runtime_context={"pack_domain": "market", "run_id": "run-crypto", "call_id": "report-prefetch"},
        market=Market.CRYPTO,
        domain="market",
        status="partial",
        results=(result,),
        crypto_lens_payload=crypto_lens_payload,
    )

    assert "| AHR999 | 由基本面/链上资料包负责 | 行情资料包未取得 AHR999；如基本面资料包有读数，以基本面资料包为准 |" in text
    assert "| AHR999 | 缺失/不可用 |" not in text


def test_crypto_lens_model_visible_text_translates_internal_statuses() -> None:
    payload = {
        "crypto_lens_status": "ready",
        "crypto_lens_analysis": {
            "status": "ready",
            "technical_patterns": {
                "evidence": {
                    "vegas": {
                        "band_position": "below_blue_band",
                        "major_trend": "major_trend_bearish",
                        "ema144": 100.0,
                        "ema169": 101.0,
                        "distance_to_blue_band_pct": -1.2,
                    },
                    "double_line_reversal": {
                        "state": "below_lines",
                        "fast_value": 98.0,
                        "slow_value": 102.0,
                        "confidence": "low",
                    },
                    "fvg": {
                        "candidates": ({"status": "partially_filled", "mid": 99.0},),
                        "open_count": 1,
                        "nearest_above": {"mid": 104.0},
                    },
                    "kd_9_3_3": {
                        "k": 19.0,
                        "d": 22.0,
                        "j": 14.0,
                        "k_state": "near_oversold",
                        "d_state": "neutral",
                    },
                    "td_sequential": {
                        "setup_direction": "bearish_reversal",
                        "setup_count": 9,
                        "countdown_count": 13,
                        "signal": "bearish_reversal_countdown_13",
                        "confidence": "medium",
                    },
                    "patterns": {
                        "amd": {
                            "status": "ready",
                            "structure": "lower_high_lower_low",
                            "phase_proxy": "markdown",
                            "buy_side_liquidity": {"price": 110.0, "time": "2026-06-01T00:00:00Z"},
                            "sell_side_liquidity": {"price": 90.0, "time": "2026-06-02T00:00:00Z"},
                        },
                        "rule_123": {"status": "pending_breakdown", "direction": "bearish", "breakout_level": 92.0},
                        "order_block": {"status": "no_recent_break_of_structure"},
                        "harmonic": {
                            "status": "no_candidate",
                            "pattern": "none",
                            "direction": "unknown",
                            "ratios": {"ab_xa": 0.5, "bc_ab": 0.6, "cd_bc": 1.2},
                        },
                        "volume_profile": {
                            "status": "ready",
                            "poc": 96.0,
                            "value_area_low": 91.0,
                            "value_area_high": 103.0,
                            "sample_count": 160,
                        },
                    },
                },
            },
            "derivatives_context": {"evidence": {}},
            "liquidation_context": {"evidence": {}},
            "onchain_context": {"evidence": {}},
            "ahr999_context": {"evidence": {}},
        },
    }

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_market_pack"},
        market=Market.CRYPTO,
        domain="market",
        status="ready",
        results=(),
        crypto_lens_payload=payload,
        chart_payload={},
    )

    assert "价格在蓝带下方" in text
    assert "价格在双线下方" in text
    assert "TD 看跌反转 13 计数完成" in text
    assert "近期没有确认的结构突破" in text
    assert "高点下移、低点下移" in text
    assert "下跌推进阶段" in text
    assert "下破待确认" in text
    assert "未匹配到有效候选" in text
    for token in (
        "technical_patterns.evidence",
        "below_blue_band",
        "below_lines",
        "bearish_reversal_countdown_13",
        "lower_high_lower_low",
        "markdown",
        "pending_breakdown",
        "no_recent_break_of_structure",
        "no_candidate",
    ):
        assert token not in text


def test_crypto_market_pack_adds_crypto_lens_indicator_coverage_from_prefetch(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    as_of = datetime(2026, 6, 2, tzinfo=UTC)
    manifest_path = tmp_path / "data-layer" / "report-prefetch.json"
    manifest_path.parent.mkdir(parents=True)
    candles = tuple(
        {
            "date": f"2026-01-{(index % 28) + 1:02d}",
            "open": 100.0 + index,
            "high": 103.0 + index,
            "low": 99.0 + index,
            "close": 101.0 + index,
            "volume": 1000.0 + index,
            "volume_unit": "BTC",
            "amount": 100000.0 + index,
            "amount_unit": "USDT",
            "symbol_id": "BTCUSDT",
            "source_market_segment": "spot",
        }
        for index in range(180)
    )
    results = [
        DataResult(
            request_id="run-crypto:report-prefetch:market:1:daily_bar",
            status=DataResultStatus.READY,
            rows=candles,
            dataset_refs=("dataset:daily_bar:CRYPTO:spot:BTCUSDT",),
            attempt_refs=("attempt:daily",),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:3:quote_snapshot",
            status=DataResultStatus.READY,
            rows=(
                {
                    "price": 281.0,
                    "change": 1.0,
                    "change_pct": 0.5,
                    "volume": 1000.0,
                    "volume_unit": "BTC",
                    "amount": 281000.0,
                    "amount_unit": "USDT",
                    "timestamp": "2026-06-02T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
            ),
            dataset_refs=("dataset:quote_snapshot:CRYPTO:BTCUSDT",),
            attempt_refs=("attempt:quote",),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:5:crypto_derivative_metric",
            status=DataResultStatus.PARTIAL,
            rows=(
                {
                    "open_interest": 1000000.0,
                    "open_interest_unit": "USD",
                    "timestamp": "2026-06-02T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
                {
                    "funding_rate": 0.0002,
                    "funding_rate_unit": "percent",
                    "timestamp": "2026-06-02T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
                {"long_short_ratio": 1.2, "timestamp": "2026-06-02T00:00:00Z", "symbol_id": "BTCUSDT"},
                {
                    "taker_buy_volume": 1200.0,
                    "taker_sell_volume": 900.0,
                    "taker_volume_unit": "USD",
                    "taker_buy_sell_ratio": 1.3333,
                    "timestamp": "2026-06-02T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
                {
                    "long_liquidation": 10000.0,
                    "short_liquidation": 5000.0,
                    "liquidation_value": 15000.0,
                    "liquidation_value_unit": "USD",
                    "timestamp": "2026-06-02T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
                {
                    "liquidation_price": 260.0,
                    "liquidation_size": 25000.0,
                    "liquidation_price_unit": "USDT",
                    "liquidation_size_unit": "USD",
                    "side": "long",
                    "timestamp": "2026-06-02T00:00:00Z",
                    "symbol_id": "BTCUSDT",
                },
            ),
            dataset_refs=("dataset:crypto_derivative_metric:CRYPTO:BTCUSDT",),
            attempt_refs=("attempt:derivatives",),
            gaps=(
                DataGap.by_reason(
                    "date_range_missing",
                    request_id="run-crypto:report-prefetch:market:5:crypto_derivative_metric",
                    market=Market.CRYPTO,
                    data_type="crypto_derivative_metric",
                    granularity="1h",
                    message="部分衍生品历史区间缺失",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
        DataResult(
            request_id="run-crypto:report-prefetch:market:12:crypto_onchain_metric",
            status=DataResultStatus.READY,
            rows=(
                {"timestamp": "2026-06-02T00:00:00Z", "metric": "spot_coin_netflow", "value": -100.0, "value_unit": "USD", "chain": "MULTI_CHAIN"},
                {"timestamp": "2026-06-02T00:00:00Z", "metric": "whale_transfer", "value": 2000000.0, "chain": "MULTI_CHAIN"},
                {"timestamp": "2026-06-02T00:00:00Z", "metric": "ahr999", "value": 1.12, "value_unit": "dimensionless", "chain": "BTC"},
            ),
            dataset_refs=("dataset:crypto_onchain_metric:CRYPTO:BTCUSDT",),
            attempt_refs=("attempt:onchain",),
            as_of=as_of,
        ),
    ]
    manifest_path.write_text(
        json.dumps(
            {
                "ok": True,
                "schema_version": "report_data_prefetch.v1",
                "run_id": "run-crypto",
                "market": "CRYPTO",
                "domains": ("market",),
                "data_results": [result.model_dump(mode="json") for result in results],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _blocked_api():
        raise AssertionError("frontline data pack must consume report prefetch manifest")

    monkeypatch.setattr("claw_trade.reports.data_pack_bridge.build_data_api_from_env", _blocked_api)

    payload = run_frontline_data_pack(
        {
            "ticker": "BTC",
            "market": "CRYPTO",
            "currency": "USDT",
            "current_date": "2026-06-02",
            "start_date": "2026-01-01",
            "end_date": "2026-06-02",
        },
        {
            "run_id": "run-crypto",
            "call_id": "report-prefetch",
            "tool_name": "claw_get_market_pack",
            "pack_domain": "market",
            "report_prefetch_required": True,
            "report_prefetch_manifest_path": str(manifest_path),
            "evidence_root": str(tmp_path),
        },
    )

    assert payload["ok"] is True
    assert payload["crypto_lens_status"] != "error"
    text = payload["model_visible_text"]
    assert "CryptoLens 指标材料" in text
    assert "not JSON serializable" not in text
    assert "本地均线位置" in text
    assert "本地 KD 状态" in text
    assert "超卖阈值为低于20" in text
    assert "指标覆盖" in text
    assert "可用于描述当前费率水平和单位；不要编造历史分位或统计结论" in text
    assert "只描述簇价格、规模、单位和样本限制；不能作为方向性价格依据" in text
    assert "BTC 估值指数读数；不要编造历史分位、定投或抄底结论" in text
    assert "可用于描述当前通道位置；不要编造目标价或回测结论" in text
    assert "可用于描述当前动量读数；不要编造反转统计或交易动作" in text
    assert "可用于描述当前计数；不要编造衰竭统计或交易动作" in text
    assert "BTC 长周期估值参考" not in text
    assert "拥挤度参考" not in text
    assert "动量过热/过冷参考" not in text
    assert "衰竭/反转计数参考" not in text
    assert "清算热力图样本 1" in text
    assert "清算热力图有效样本 1" in text
    assert "普通强平额样本 1" in text
    assert "未平仓量 1000000.0 USD（单位已明示：USD）" in text
    assert "sample_count" not in text
    for label in ("维加斯通道", "FVG", "KD", "TD 9/13", "CVD代理/主动买卖量", "资金费率", "OI/多空比", "清算地图", "链上", "AHR999"):
        assert label in text
    for local_pattern_label in ("AMD/SMC", "123 突破", "OB 订单块", "谐波形态", "交易密集带/成交量分布"):
        assert local_pattern_label in text
        pattern_line = next(line for line in text.splitlines() if line.startswith(f"| {local_pattern_label} |"))
        assert "不要编造" not in pattern_line
        assert "胜率" not in pattern_line
        assert "目标价" not in pattern_line
        assert "交易动作" not in pattern_line
        assert "概率" not in pattern_line
    assert "未覆盖/未实现" not in text
    assert "technical_patterns.evidence" not in text
    assert "lower_high_lower_low" not in text
    assert "pending_breakdown" not in text
    assert "no_recent_break_of_structure" not in text
    assert "no_candidate" not in text
    assert "bearish_reversal_countdown_13" not in text
    assert "below_lines" not in text
    assert "markdown" not in text
    assert "输出三点结构状态和突破位；只反映最近摆动结构" in text
    assert "输出候选订单块区间和结构突破位；不包含成交确认或支撑强度评级" in text
    assert "300.0" in text
    evidence_paths = payload["crypto_lens_evidence_paths"]
    assert evidence_paths
    analysis_path = tmp_path / "data-layer" / "crypto-lens" / "run-crypto" / "report-prefetch" / "analysis.json"
    assert analysis_path.exists()
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert analysis["status"] == "partial"
    assert analysis["readiness"]["status"] == "partial"
    assert analysis["readiness"]["blocking_gap_ids"] == []
    assert analysis["readiness"]["non_blocking_gap_ids"]
    assert analysis["data_gaps"][0]["reason"] == "date_range_missing"
    derivatives = analysis["derivatives_context"]["evidence"]
    assert derivatives["funding_unit"] == "percent"
    assert derivatives["oi_unit"] == "USD"
    assert derivatives["cvd_proxy_unit"] == "USD"
    liquidation = analysis["liquidation_context"]["evidence"]
    patterns = analysis["technical_patterns"]["evidence"]["patterns"]
    assert patterns["volume_profile"]["status"] == "ready"
    assert patterns["volume_profile"]["poc"] is not None
    assert patterns["amd"]["status"] in {"ready", "insufficient_swings"}
    assert patterns["rule_123"]["status"] in {"no_pattern", "pending_breakout", "confirmed_breakout", "pending_breakdown", "confirmed_breakdown", "insufficient_swings"}
    assert patterns["order_block"]["status"] in {"candidate", "no_recent_break_of_structure", "insufficient_ohlc", "no_source_candle"}
    assert patterns["harmonic"]["status"] in {"candidate", "no_candidate", "insufficient_swings", "invalid_pivot_geometry"}
    assert liquidation["largest_cluster_price"] == 260.0
    assert liquidation["largest_cluster_size"] == 25000.0
    assert liquidation["largest_cluster_price_unit"] == "USDT"
    assert liquidation["largest_cluster_size_unit"] == "USD"
    onchain = analysis["onchain_context"]["evidence"]
    assert onchain["whale_large_tx_count"] == 1
    assert onchain["whale_large_tx_count_unit"] == "count"
    ahr999 = analysis["ahr999_context"]["evidence"]
    assert ahr999["value_unit"] == "dimensionless"
    td = analysis["technical_patterns"]["evidence"]["td_sequential"]
    assert "interpretation" in td
    assert "表示当前 TD" in td["interpretation"] or td["interpretation"] == "无有效 TD setup/countdown"
    assert "潜在反弹" not in td["interpretation"]
    assert "潜在回落" not in td["interpretation"]
    assert td["setup_rule"] in {"close < close_4_bars_ago", "close > close_4_bars_ago", "none"}


def test_cn_a_market_daily_bar_request_includes_date_for_chart_payload() -> None:
    requests = _build_requests(
        tool_input={"ticker": "688017.SH", "market": "CN_A", "current_date": "2026-06-06"},
        runtime_context={"pack_domain": "market", "run_id": "run", "call_id": "call", "worker_id": "market_analyst"},
        market=Market.CN_A,
        domain="market",
    )

    daily_request = next(request for request in requests if request.data_type == "daily_bar")

    assert daily_request.fields == ("date", "open", "high", "low", "close", "volume", "amount", "amount_unit")


def test_non_crypto_pack_requests_match_market_specific_provider_capabilities() -> None:
    cn_a_fundamental = _build_requests(
        tool_input={"ticker": "600519", "market": "CN_A", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "fundamental", "run_id": "run", "call_id": "call", "worker_id": "fundamental_analyst"},
        market=Market.CN_A,
        domain="fundamental",
    )
    assert all("ev_ebitda" not in request.fields for request in cn_a_fundamental)

    us_hot_money = _build_requests(
        tool_input={"ticker": "AAPL", "market": "US", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "hot_money", "run_id": "run", "call_id": "call", "worker_id": "hot_money_tracker"},
        market=Market.US,
        domain="hot_money",
    )
    hk_social = _build_requests(
        tool_input={"ticker": "00700", "market": "HK", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "social", "run_id": "run", "call_id": "call", "worker_id": "social_analyst"},
        market=Market.HK,
        domain="social",
    )
    assert us_hot_money == ()
    assert len(hk_social) == 1
    assert hk_social[0].data_type == "social_signal"
    assert hk_social[0].fields == ("source", "timestamp", "title", "url", "symbol_id")


def test_us_macro_series_prefetch_uses_real_fred_series_ids_not_equity_symbol() -> None:
    requests = _build_requests(
        tool_input={"ticker": "AAPL", "market": "US", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "news", "run_id": "run", "call_id": "call", "worker_id": "news_analyst"},
        market=Market.US,
        domain="news",
    )

    macro_requests = [request for request in requests if request.data_type == "macro_series"]

    assert [request.symbol_id for request in macro_requests] == ["FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10"]
    assert all(request.exchange == "FRED" for request in macro_requests)
    assert all(request.calendar == "US_FED" for request in macro_requests)
    assert all(request.symbol_id != "AAPL" for request in macro_requests)


def test_crypto_news_pack_requests_real_news_capabilities() -> None:
    requests = _build_requests(
        tool_input={"ticker": "BTC", "market": "CRYPTO", "currency": "USDT", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "news", "run_id": "run", "call_id": "call", "worker_id": "news_analyst"},
        market=Market.CRYPTO,
        domain="news",
    )

    request_keys = [(request.data_type, request.granularity, request.fields) for request in requests]
    assert request_keys == [
        ("company_news", "event", ("title", "published_at", "source", "summary", "url")),
        ("macro_news", "event", ("title", "published_at", "source", "summary", "url", "region")),
    ]


def test_crypto_bare_asset_requests_usdt_pair_even_when_display_currency_is_usd() -> None:
    requests = _build_requests(
        tool_input={"ticker": "BTC", "market": "CRYPTO", "currency": "USD", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "market", "run_id": "run", "call_id": "call", "worker_id": "market_analyst"},
        market=Market.CRYPTO,
        domain="market",
    )

    assert requests[0].symbol_id == "BTCUSDT"
    assert requests[0].base_asset == "BTC"
    assert requests[0].quote_asset == "USDT"
    assert requests[0].currency == "USDT"


def test_crypto_explicit_non_usdt_pair_requests_usdt_pair() -> None:
    requests = _build_requests(
        tool_input={"ticker": "BTCUSD", "market": "CRYPTO", "currency": "USD", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "market", "run_id": "run", "call_id": "call", "worker_id": "market_analyst"},
        market=Market.CRYPTO,
        domain="market",
    )
    slash_requests = _build_requests(
        tool_input={"ticker": "ETH/BTC", "market": "CRYPTO", "currency": "BTC", "current_date": "2026-06-01"},
        runtime_context={"pack_domain": "market", "run_id": "run", "call_id": "call", "worker_id": "market_analyst"},
        market=Market.CRYPTO,
        domain="market",
    )

    assert requests[0].symbol_id == "BTCUSDT"
    assert requests[0].quote_asset == "USDT"
    assert slash_requests[0].symbol_id == "ETHUSDT"
    assert slash_requests[0].quote_asset == "USDT"


def test_crypto_market_bar_pack_prefers_spot_rows_over_futures_rows() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    result = DataResult(
        request_id="run:call:market:1:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {"date": "2026-06-01", "close": "100", "universe_ref": "binance_spot_all_symbols"},
            {"date": "2026-06-01", "close": "99", "universe_ref": "binance_usdm_all_symbols"},
        ),
        dataset_refs=(
            "dataset:daily_bar:CRYPTO:spot:BTCUSDT:daily:2026-06-01:2026-06-01",
            "dataset:daily_bar:CRYPTO:usdm_futures:BTCUSDT:daily:2026-06-01:2026-06-01",
        ),
        as_of=as_of,
    )

    filtered = _filter_crypto_market_bars(results=(result,), market=Market.CRYPTO, domain="market")

    assert filtered[0].rows == ({"date": "2026-06-01", "close": "100", "universe_ref": "binance_spot_all_symbols"},)
    assert filtered[0].dataset_refs == (
        "dataset:daily_bar:CRYPTO:spot:BTCUSDT:daily:2026-06-01:2026-06-01",
    )


def test_crypto_market_bar_pack_accepts_spot_provider_lineage_without_extra_marker() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    result = DataResult(
        request_id="run:call:market:1:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {
                "date": "2026-06-01",
                "close": "100",
                "provider_lineage": {"provider_id": "crypto_primary", "endpoint_id": "spot_daily_bar"},
            },
        ),
        dataset_refs=("dataset:daily_bar:CRYPTO:BTCUSDT:2026-06-01:2026-06-01",),
        as_of=as_of,
    )

    filtered = _filter_crypto_market_bars(results=(result,), market=Market.CRYPTO, domain="market")

    assert filtered[0].status == DataResultStatus.READY
    assert filtered[0].rows == result.rows
    assert filtered[0].dataset_refs == result.dataset_refs


def test_crypto_market_bar_pack_keeps_available_rows_when_spot_marker_missing() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    result = DataResult(
        request_id="run:call:market:1:daily_bar",
        status=DataResultStatus.READY,
        rows=(
            {"date": "2026-06-01", "close": "99", "universe_ref": "binance_usdm_all_symbols"},
        ),
        dataset_refs=(
            "dataset:daily_bar:CRYPTO:usdm_futures:BTCUSDT:daily:2026-06-01:2026-06-01",
        ),
        as_of=as_of,
    )

    filtered = _filter_crypto_market_bars(results=(result,), market=Market.CRYPTO, domain="market")

    assert filtered[0].status == DataResultStatus.READY
    assert filtered[0].rows == result.rows
    assert filtered[0].dataset_refs == result.dataset_refs


def test_market_specific_pack_requests_are_backed_by_provider_capabilities() -> None:
    capabilities = []
    for plugin in iter_minimal_market_plugins():
        provider_capabilities = plugin.capabilities()
        capabilities.extend(provider_capabilities.endpoints)

    tool_inputs = {
        Market.CN_A: {"ticker": "600519", "market": "CN_A", "current_date": "2026-06-01"},
        Market.US: {"ticker": "AAPL", "market": "US", "current_date": "2026-06-01"},
        Market.HK: {"ticker": "00700", "market": "HK", "current_date": "2026-06-01"},
        Market.CRYPTO: {"ticker": "BTC", "market": "CRYPTO", "currency": "USDT", "current_date": "2026-06-01"},
    }

    mismatches = []
    for market, tool_input in tool_inputs.items():
        for domain in ("market", "fundamental", "news", "social", "policy", "hot_money", "lockup"):
            requests = _build_requests(
                tool_input=tool_input,
                runtime_context={"pack_domain": domain, "run_id": "run", "call_id": "call", "worker_id": "worker"},
                market=market,
                domain=domain,
            )
            for request in requests:
                matched = [
                    capability
                    for capability in capabilities
                    if str(capability.market) == market.value
                    and capability.data_type == request.data_type
                    and request.granularity in capability.granularity
                    and set(request.fields).issubset(set(capability.fields))
                ]
                if not matched:
                    mismatches.append((market.value, domain, request.data_type, request.granularity, request.fields))

    assert mismatches == []


def test_crypto_social_request_matches_available_sentiment_provider_fields() -> None:
    requests = _build_requests(
        tool_input={
            "ticker": "BTC",
            "market": "CRYPTO",
            "currency": "USDT",
            "current_date": "2026-06-01",
        },
        runtime_context={"pack_domain": "social", "run_id": "run", "call_id": "call", "worker_id": "social_analyst"},
        market=Market.CRYPTO,
        domain="social",
    )

    assert [request.data_type for request in requests] == ["social_signal", "social_signal"]
    assert requests[0].fields == ("source", "timestamp", "score", "sentiment", "symbol_id")
    assert requests[1].fields == (
        "source",
        "timestamp",
        "score",
        "sentiment",
        "social_dominance",
        "num_posts",
        "interactions",
        "symbol_id",
    )
    assert all("mentions" not in request.fields for request in requests)


def test_crypto_model_visible_text_uses_crypto_labels_for_missing_fundamental_data() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    results = (
        DataResult(
            request_id="run:call:fundamental:1:valuation_metric",
            status=DataResultStatus.MISSING,
            gaps=(
                DataGap.by_reason(
                    "field_missing",
                    request_id="run:call:fundamental:1:valuation_metric",
                    market=Market.CRYPTO,
                    data_type="valuation_metric",
                    granularity="realtime",
                    required_fields=("price", "market_cap", "circulating_supply"),
                    message="field_missing",
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        ),
    )

    text = _model_visible_text(
        tool_input={"ticker": "BTC", "market": "CRYPTO"},
        runtime_context={"tool_name": "claw_get_fundamental_pack"},
        market=Market.CRYPTO,
        domain="fundamental",
        status="missing",
        results=results,
    )

    assert "币种市值与供应数据" in text
    assert "价格" in text
    assert "流通供应量" in text
    assert "财务报表" not in text
    assert "每股收益" not in text
    assert "市盈率" not in text
