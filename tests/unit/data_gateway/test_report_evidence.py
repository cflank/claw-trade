from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy
from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, GapReason, Market
from claw_trade.data_gateway.needs import DataNeed, NeedPriority, ProviderCallSpec
from claw_trade.data_gateway.report_evidence import (
    _NeedResultRefs,
    _basic_data_need_model_visible_text,
    _build_public_data_request,
    _data_need_status,
    _data_result_satisfies_need,
    _deadline_at,
    _empty_event_attempts_satisfy_need,
    _internal_need_for_public_request,
    _official_api_result_shape,
    _provider_call_batch,
    _structured_provider_call_batch,
)


def _context() -> dict[str, str]:
    return {
        "worker_id": "market_analyst",
        "run_id": "run",
        "call_id": "call",
        "current_date": "2026-06-12",
        "current_time": datetime(2026, 6, 12, 12, 0, tzinfo=UTC).isoformat(),
    }


def _need(**overrides: object) -> DataNeed:
    base = {
        "need_id": "need-1",
        "api_id": "crypto.funding_rate",
        "market": Market.CRYPTO,
        "instrument": "BTC/USDT",
        "time_range_start": date(2026, 6, 1),
        "time_range_end": date(2026, 6, 12),
        "granularity": "realtime",
        "priority": NeedPriority.NORMAL,
        "requested_by_worker": "market_analyst",
        "purpose": "market_report",
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "consumer": "report",
    }
    return DataNeed.model_validate({**base, **overrides})


def _call(**overrides: object) -> ProviderCallSpec:
    base = {
        "call_id": "call:coinglass-funding",
        "method": "GET",
        "public_api_id": "crypto.funding_rate",
        "provider_id": "official_api_coinglass",
        "catalog_endpoint_id": "coinglass.futures_funding_rate",
        "official_path_or_api_name": "/api/futures/funding-rate/oi-weight-history",
        "params": {"symbol": "BTC", "interval": "1d"},
        "auth_scope": "coinglass_header",
        "rate_limit_bucket": "ratelimit:coinglass",
        "http_visibility": "managed_http",
        "parser_status": "normalized",
        "batch_key": "batch:coinglass-funding",
        "official_doc_ref": "https://docs.coinglass.com/reference/oi-weight-ohlc-history.md",
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "need_ids": ("need-1",),
    }
    return ProviderCallSpec.model_validate({**base, **overrides})


def test_public_builder_preserves_crypto_quote_asset_and_internalizes_api_id() -> None:
    explicit_pair = _build_public_data_request(
        tool_input={"item": "日线", "purpose": "market_report", "instrument": "BTC/USDC", "market": "CRYPTO"},
        runtime_context=_context(),
    )
    compact_pair = _build_public_data_request(
        tool_input={"item": "日线", "purpose": "market_report", "instrument": "BTCUSDC", "market": "CRYPTO"},
        runtime_context=_context(),
    )
    default_pair = _build_public_data_request(
        tool_input={"item": "日线", "purpose": "market_report", "instrument": "BTC", "market": "CRYPTO"},
        runtime_context=_context(),
    )

    assert explicit_pair.instrument == "BTC/USDC"
    assert compact_pair.instrument == "BTC/USDC"
    assert default_pair.instrument == "BTC/USDT"
    assert default_pair.item == "日线"
    assert default_pair.api_id == "crypto.daily_bar"
    assert "api_id" not in default_pair.model_dump(mode="python")

    internal_need = _internal_need_for_public_request(default_pair)
    assert internal_need.api_id == "crypto.daily_bar"
    assert internal_need.instrument == "BTC/USDT"


@pytest.mark.parametrize("forbidden_key", ["api_id", "provider", "path", "api_name", "url", "headers", "token", "fields"])
def test_public_builder_rejects_execution_details(forbidden_key: str) -> None:
    tool_input = {"item": "日线", "purpose": "market_report", "instrument": "600519.SH", "market": "CN_A", forbidden_key: "leak"}

    with pytest.raises(ValueError):
        _build_public_data_request(tool_input=tool_input, runtime_context=_context())


def test_official_api_batch_shape_uses_catalog_output_contract() -> None:
    call = _call(
        call_id="call:coinglass-oi",
        public_api_id="crypto.open_interest",
        catalog_endpoint_id="coinglass.futures_open_interest",
        official_path_or_api_name="/api/futures/open-interest/exchange-list",
        params={"symbol": "BTC"},
        parser_status="parser_missing",
        batch_key="batch:coinglass-oi",
        official_doc_ref="https://docs.coinglass.com/reference/oi-exchange-list.md",
    )
    need = _need(need_id="need-oi", api_id="crypto.open_interest")

    data_type, granularity, fields = _official_api_result_shape(call, need)

    assert data_type == "crypto_derivative_metric"
    assert granularity == "realtime"
    assert {"open_interest", "timestamp", "symbol_id"} <= set(fields)


def test_crypto_onchain_event_endpoint_uses_event_granularity_for_daily_parent_need() -> None:
    call = _call(
        call_id="call:coinglass-whale-transfer",
        public_api_id="crypto.onchain_metric",
        catalog_endpoint_id="coinglass.onchain_whale_transfer",
        official_path_or_api_name="/api/chain/v2/whale-transfer",
        params={"symbol": "BTC"},
        batch_key="batch:coinglass-whale-transfer",
        official_doc_ref="https://docs.coinglass.com/reference/whale-transfer.md",
    )
    need = _need(need_id="need-onchain", api_id="crypto.onchain_metric", granularity="daily")

    batch = _provider_call_batch(call=call, need=need, policy=RateLimitPolicy(window_seconds=60, max_requests=None))

    assert batch.data_type == "crypto_onchain_metric"
    assert batch.granularity == "event"
    assert {"value", "timestamp"} <= set(batch.fields_union)
    assert "whale_transfer" in set(batch.capability_fields)


def test_crypto_liquidation_heatmap_size_satisfies_public_contract() -> None:
    need = _need(
        need_id="need-heatmap",
        api_id="crypto.liquidation_heatmap",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
    )
    result = DataResult(
        request_id="data_need:call:market:0:crypto_derivative_metric",
        status=DataResultStatus.READY,
        rows=(
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTC/USDT",
                "timestamp": "2026-06-15T12:00:00Z",
                "liquidation_price": 49935.58,
                "liquidation_size": 3816866.4,
            },
        ),
        dataset_refs=("dataset:liquidation-heatmap",),
        raw_refs=("raw:liquidation-heatmap",),
        attempt_refs=("attempt:coinglass",),
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
    )

    assert _data_result_satisfies_need(
        need=need,
        data_result=result,
        data_type="crypto_derivative_metric",
        required_fields=("liquidation_price", "liquidation_value"),
        public_api_id="crypto.liquidation_heatmap",
    )


def test_pairs_market_snapshot_is_not_bound_to_specific_derivative_metrics() -> None:
    call = _call(
        catalog_endpoint_id="coinglass.futures_pairs_markets",
        official_path_or_api_name="/api/futures/pairs-markets",
        params={"exchange": "Binance", "symbol": "BTC"},
    )
    need = _need(api_id="crypto.funding_rate")

    batch = _provider_call_batch(call=call, need=need, policy=RateLimitPolicy(window_seconds=60, max_requests=None))

    assert batch.data_type == "official_api_response"
    assert "funding_rate" not in set(batch.fields_union)
    assert "open_interest" not in set(batch.capability_fields)


def test_selection_universe_refresh_batch_carries_universe_ref_for_warehouse_scope() -> None:
    need = _need(
        need_id="sel:selection:universe_refresh:1:all_a_shares:daily_bar",
        api_id="cn_a.daily_bar",
        market=Market.CN_A,
        instrument="all_a_shares",
        granularity="daily",
        consumer="select",
        purpose="selection_data_refresh",
    )
    call = _call(
        call_id="call:tushare-daily-all",
        public_api_id="cn_a.daily_bar",
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        params={"trade_date": "20260630"},
        auth_scope="tushare_token",
        rate_limit_bucket="ratelimit:tushare",
        batch_key="batch:tushare-daily-all",
        official_doc_ref="https://tushare.pro/document/2?doc_id=27",
        need_ids=(need.need_id,),
    )

    batch = _provider_call_batch(call=call, need=need, policy=RateLimitPolicy(window_seconds=60, max_requests=None))

    assert batch.universe_ref == "all_a_shares"


def test_selection_single_symbol_batch_does_not_carry_universe_ref() -> None:
    need = _need(
        need_id="sel:selection:1:600519.SH:daily_bar",
        api_id="cn_a.daily_bar",
        market=Market.CN_A,
        instrument="600519.SH",
        granularity="daily",
        consumer="select",
        purpose="selection_data_refresh",
    )
    call = _call(
        call_id="call:tushare-daily-single",
        public_api_id="cn_a.daily_bar",
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        params={"ts_code": "600519.SH", "trade_date": "20260630"},
        auth_scope="tushare_token",
        rate_limit_bucket="ratelimit:tushare",
        batch_key="batch:tushare-daily-single",
        official_doc_ref="https://tushare.pro/document/2?doc_id=27",
        need_ids=(need.need_id,),
    )

    batch = _provider_call_batch(call=call, need=need, policy=RateLimitPolicy(window_seconds=60, max_requests=None))

    assert batch.universe_ref is None


def test_structured_data_need_batch_does_not_require_provider_capability_declaration() -> None:
    call = _call(
        call_id="call:google-news",
        public_api_id="cn_a.company_news",
        provider_id="cn_a_google_news",
        catalog_endpoint_id="google_news.cn_a_company_news",
        official_path_or_api_name="/rss/search",
        params={"q": "贵州茅台 股票 新闻"},
        auth_scope="none",
        rate_limit_bucket="ratelimit:google_news",
        http_visibility="managed_http",
        parser_status="raw_only",
        batch_key="batch:google-news",
        official_doc_ref="https://news.google.com/rss",
    )
    need = _need(
        need_id="need-news",
        api_id="cn_a.company_news",
        market=Market.CN_A,
        instrument="600519.SH",
        granularity="event",
        purpose="news_report",
    )

    batch = _structured_provider_call_batch(call=call, need=need, policy=RateLimitPolicy(window_seconds=60, max_requests=None), runtime=None)

    assert batch.endpoint_id == "company_news"
    assert batch.data_type == "company_news"
    assert batch.granularity == "event"
    assert {"title", "published_at", "source"} <= set(batch.fields_union)
    assert {"summary", "url"} <= set(batch.capability_fields)


def test_basic_model_visible_text_uses_business_label_and_hides_execution_details() -> None:
    refs = _NeedResultRefs()
    text = _basic_data_need_model_visible_text(
        need=_need(api_id="crypto.funding_rate"),
        status="partial",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "partial"},),
    )

    assert "资金费率" in text
    forbidden_tokens = ("计划数据接口", "进入调度", "尝试记录", "候选尝试", "可执行接口", "provider", "api_id")
    for token in forbidden_tokens:
        assert token not in text


def test_basic_model_visible_text_does_not_show_internal_dataset_name() -> None:
    refs = _NeedResultRefs()
    result = DataResult(
        request_id="data:daily",
        status=DataResultStatus.READY,
        rows=({"date": "2026-06-10", "close": 1410.0, "volume": 1200000, "symbol_id": "600519.SH"},),
        dataset_refs=("dataset:daily:1",),
        raw_refs=("raw:daily:1",),
        attempt_refs=("attempt:daily:1",),
        as_of=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
    )

    text = _basic_data_need_model_visible_text(
        need=_need(api_id="cn_a.daily_bar", market=Market.CN_A, instrument="600519.SH", granularity="daily"),
        status="ready",
        planned_count=1,
        scheduled_count=1,
        refs=refs,
        gaps=(),
        attempts=({"status": "success"},),
        data_results=(result,),
        need_satisfied=True,
    )

    assert "日线" in text
    assert "daily_bar" not in text
    assert "api_id" not in text


def test_cn_a_daily_bar_satisfies_when_only_current_trading_day_is_missing() -> None:
    rows = [
        {
            "date": "2025-03-24",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
        },
        {
            "date": "2026-04-30",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
        }
    ]
    rows.extend(
        {
            "date": f"2026-05-{day:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
        }
        for day in range(1, 32)
    )
    rows.extend(
        {
            "date": f"2026-06-{day:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
        }
        for day in range(1, 13)
    )
    result = DataResult(
        request_id="data_need:call:market:0:daily_bar",
        status=DataResultStatus.READY,
        rows=tuple(rows),
        dataset_refs=("dataset:daily",),
        raw_refs=("raw:daily",),
        attempt_refs=("attempt:daily",),
        as_of=datetime(2026, 6, 15, 3, 0, tzinfo=UTC),
    )

    assert _data_result_satisfies_need(
        need=_need(
            api_id="cn_a.daily_bar",
            market=Market.CN_A,
            instrument="600519.SH",
            time_range_start=date(2025, 3, 22),
            time_range_end=date(2026, 6, 15),
            granularity="daily",
        ),
        data_result=result,
        data_type="daily_bar",
        required_fields=(),
        public_api_id="cn_a.daily_bar",
    )
    assert not _data_result_satisfies_need(
        need=_need(
            api_id="cn_a.daily_bar",
            market=Market.CN_A,
            instrument="600519.SH",
            time_range_start=date(2025, 3, 22),
            time_range_end=date(2026, 6, 17),
            granularity="daily",
        ),
        data_result=result,
        data_type="daily_bar",
        required_fields=(),
        public_api_id="cn_a.daily_bar",
    )


def test_satisfied_need_status_stays_ready_even_when_fallback_candidate_has_gap() -> None:
    refs = _NeedResultRefs()
    refs.dataset_refs = ("dataset:ready",)
    refs.raw_refs = ("raw:ready",)
    refs.attempt_refs = ("attempt:ready", "attempt:failed-candidate")
    refs.gaps = (
        DataGap.by_reason(
            GapReason.FIELD_MISSING,
            request_id="request-candidate",
            market=Market.CN_A,
            data_type="financial_metric",
            granularity="quarterly",
            required_fields=("debt_ratio",),
            as_of=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        ),
    )

    status = _data_need_status(
        refs=refs,
        gap_count=1,
        attempts=({"status": "success"}, {"status": "partial"}),
        need_satisfied=True,
        has_result_gaps=True,
    )

    assert status == "ready"


def test_lockup_empty_remote_attempts_can_satisfy_no_event_need() -> None:
    need = _need(api_id="cn_a.lockup_event", market=Market.CN_A, instrument="600519.SH", granularity="event")

    assert _empty_event_attempts_satisfy_need(
        need=need,
        attempts=(
            {"status": "empty", "remote_attempted": True, "error_code": "provider_empty"},
            {"status": "empty", "remote_attempted": True, "error_code": "provider_empty"},
        ),
        data_results=(),
    )


def test_sector_snapshot_result_satisfies_sector_flow_need() -> None:
    result = DataResult(
        request_id="data:sector-flow",
        status=DataResultStatus.READY,
        rows=(
            {
                "symbol_id": "600519.SH",
                "sector_name": "白酒",
                "main_net": 123.0,
                "amount_unit": "CNY",
                "timestamp": datetime(2026, 6, 12, tzinfo=UTC),
            },
        ),
        dataset_refs=("dataset:sector:1",),
        raw_refs=("raw:sector:1",),
        attempt_refs=("attempt:sector:1",),
        as_of=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
    )

    assert _data_result_satisfies_need(
        need=_need(api_id="cn_a.sector_flow", market=Market.CN_A, instrument="600519.SH", granularity="event"),
        data_result=result,
        data_type="sector_snapshot",
        required_fields=("sector_name", "main_net"),
    )


def test_exact_public_api_binding_satisfies_different_dataset_name() -> None:
    result = DataResult(
        request_id="data:macro-event",
        status=DataResultStatus.READY,
        rows=(
            {
                "symbol_id": "AAPL",
                "event_date": date(2026, 6, 12),
                "title": "FOMC calendar",
                "source": "fred",
            },
        ),
        dataset_refs=("dataset:macro:1",),
        raw_refs=("raw:macro:1",),
        attempt_refs=("attempt:macro:1",),
        as_of=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
    )
    need = _need(api_id="us.macro_news", market=Market.US, instrument="AAPL", granularity="event")

    assert not _data_result_satisfies_need(
        need=need,
        data_result=result,
        data_type="event_calendar",
        required_fields=("title", "event_date"),
    )
    assert _data_result_satisfies_need(
        need=need,
        data_result=result,
        data_type="event_calendar",
        required_fields=("title", "event_date"),
        public_api_id="us.macro_news",
    )


def test_data_need_default_deadline_can_cross_one_sliding_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", raising=False)
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    deadline = _deadline_at({"current_time": now.isoformat()})

    assert (deadline - now).total_seconds() >= 60


def test_provider_call_batch_carries_internal_call_spec_only_inside_audit_payload() -> None:
    batch = _provider_call_batch(call=_call(), need=_need(), policy=RateLimitPolicy(window_seconds=60, max_requests=10))

    assert batch.params["provider_call_spec"]["public_api_id"] == "crypto.funding_rate"
    assert batch.params["provider_call_spec"]["provider_id"] == "official_api_coinglass"
