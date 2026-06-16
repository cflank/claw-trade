from __future__ import annotations

from datetime import UTC, date, datetime

from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.models import DataGap, DataRequest, GapReason, GapSeverity, Market


def _request(**overrides: object) -> DataRequest:
    payload = {
        "request_id": "req-qp-1",
        "market": Market.CN_A,
        "symbol_id": " 600519.sh ",
        "timezone": " Asia/Shanghai ",
        "calendar": " CN_A_SSE_SZSE ",
        "data_type": "daily_bar",
        "granularity": "DAILY",
        "fields": (" close ", "open", "close"),
        "date_range_start": date(2026, 5, 1),
        "date_range_end": date(2026, 5, 31),
        "freshness_policy": "trading_day",
        "consumer": "report",
        "consumer_id": "market_analyst",
        "as_of": datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return DataRequest.model_validate(payload)


def test_query_planner_normalizes_symbol_market_calendar_timezone_and_dates() -> None:
    planner = QueryPlanner()
    plan = planner.validate_and_normalize(_request())
    req = plan.normalized_requests[0]
    assert req.market == Market.CN_A
    assert req.symbol_id == "600519.SH"
    assert req.calendar == "CN_A_SSE_SZSE"
    assert req.timezone == "Asia/Shanghai"
    assert req.granularity == "daily"
    assert req.date_range_start == date(2026, 5, 1)
    assert req.date_range_end == date(2026, 5, 31)


def test_query_planner_builds_warehouse_checks_required_coverage_and_expected_outputs() -> None:
    planner = QueryPlanner()
    plan = planner.validate_and_normalize(_request())
    assert len(plan.warehouse_checks) == 1
    check = plan.warehouse_checks[0]
    assert check.request_id == "req-qp-1"
    assert check.fields == ("close", "open")
    assert plan.required_coverage.request_ids == ("req-qp-1",)
    assert plan.required_coverage.required_fields_by_request["req-qp-1"] == ("close", "open")
    assert plan.expected_outputs == ("daily_bar",)


def test_query_planner_scopes_crypto_bar_warehouse_checks_to_spot_universe() -> None:
    planner = QueryPlanner()
    bar_plan = planner.validate_and_normalize(
        _request(
            market=Market.CRYPTO,
            symbol_id="btcusdt",
            timezone="UTC",
            calendar="CRYPTO_24_7",
            data_type="intraday_bar",
            granularity="1h",
            fields=("open", "close"),
            base_asset="BTC",
            quote_asset="USDT",
        )
    )
    derivative_plan = planner.validate_and_normalize(
        _request(
            market=Market.CRYPTO,
            symbol_id="btcusdt",
            timezone="UTC",
            calendar="CRYPTO_24_7",
            data_type="crypto_derivative_metric",
            granularity="1h",
            fields=("open_interest",),
            base_asset="BTC",
            quote_asset="USDT",
        )
    )

    assert bar_plan.warehouse_checks[0].universe_ref == "binance_spot_all_symbols"
    assert derivative_plan.warehouse_checks[0].universe_ref is None


def test_query_planner_does_not_generate_remote_execution_fields() -> None:
    planner = QueryPlanner()
    plan = planner.validate_and_normalize(_request())
    assert not hasattr(plan, "provider_candidates")
    assert tuple(plan.normalized_requests)


def test_query_plan_can_resolve_request_for_gap() -> None:
    planner = QueryPlanner()
    plan = planner.validate_and_normalize(_request(request_id="req-qp-gap"))
    gap = DataGap(
        gap_id="gap:req-qp-gap",
        request_id="req-qp-gap",
        severity=GapSeverity.BLOCKER,
        reason=GapReason.WAREHOUSE_MISSING,
        market=Market.CN_A,
        symbol_id="600519.SH",
        data_type="daily_bar",
        granularity="daily",
        human_readable="warehouse_missing",
        as_of=datetime(2026, 5, 31, 9, 5, tzinfo=UTC),
    )
    resolved = plan.request_for_gap(gap)
    assert resolved.request_id == "req-qp-gap"
