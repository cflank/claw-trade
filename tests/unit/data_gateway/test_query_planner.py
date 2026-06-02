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


def test_query_planner_does_not_generate_provider_candidates_or_batch_plan() -> None:
    planner = QueryPlanner()
    plan = planner.validate_and_normalize(_request())
    assert not hasattr(plan, "provider_candidates")
    assert not hasattr(plan, "provider_batch_plans")


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
