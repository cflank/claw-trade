from __future__ import annotations

import json
from datetime import UTC, datetime

from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, Market
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.reports.data_pack_bridge import (
    _build_requests,
    _model_visible_text,
    _validate_report_data_results,
    run_frontline_data_pack,
    run_report_data_prefetch,
)
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


def test_report_data_prefetch_batches_all_frontline_domains_once(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
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
    assert len(calls) == 1
    request_ids = [request.request_id for request in calls[0]]
    assert any(":market:" in item for item in request_ids)
    assert any(":fundamental:" in item for item in request_ids)
    assert any(":news:" in item for item in request_ids)
    assert any(":social:" in item for item in request_ids)
    assert (tmp_path / "data-layer" / "report-prefetch.json").exists()


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
    assert "成交量 1200" in text
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
        ("defi_metric", "realtime"),
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
        ("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
    ) in request_fields
    assert (
        "order_book_snapshot",
        "1h",
        ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp"),
    ) in request_fields
    assert (
        "crypto_onchain_metric",
        "realtime",
        ("timestamp", "metric", "value", "chain"),
    ) in request_fields


def test_cn_a_market_daily_bar_request_includes_date_for_chart_payload() -> None:
    requests = _build_requests(
        tool_input={"ticker": "688017.SH", "market": "CN_A", "current_date": "2026-06-06"},
        runtime_context={"pack_domain": "market", "run_id": "run", "call_id": "call", "worker_id": "market_analyst"},
        market=Market.CN_A,
        domain="market",
    )

    daily_request = next(request for request in requests if request.data_type == "daily_bar")

    assert daily_request.fields == ("date", "open", "high", "low", "close", "volume", "amount")


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
    assert hk_social == ()


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

    assert len(requests) == 1
    assert requests[0].data_type == "social_signal"
    assert requests[0].fields == ("source", "timestamp", "score", "sentiment", "symbol_id")
    assert "mentions" not in requests[0].fields


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
