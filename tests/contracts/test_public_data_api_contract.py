from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from claw_trade.data_gateway.models import DataResult, DataResultStatus
from claw_trade.data_gateway.planner.call_planner import plan_public_data_requests
from claw_trade.data_gateway.public_api import PublicDataRequest, public_result_satisfies_contract, validate_public_request


def _request(*, consumer: str = "report") -> PublicDataRequest:
    return PublicDataRequest(
        request_id=f"request-{consumer}",
        item="日线",
        market="CN_A",
        instrument="600519.SH",
        granularity="daily",
        requested_by_worker="market_analyst",
        purpose="market_report",
        deadline_at=datetime(2026, 5, 31, tzinfo=UTC),
        consumer=consumer,
    )


def test_public_request_rejects_provider_execution_details() -> None:
    payload = {"item": "日线", "params": {"api_name": "daily"}}
    result = validate_public_request(payload)

    assert result.ok is False
    assert result.reason == "provider_execution_detail_rejected"


def test_public_request_rejects_freeform_params_even_when_empty() -> None:
    payload = {
        "item": "日线",
        "market": "CN_A",
        "instrument": "600519.SH",
        "purpose": "market_report",
        "params": {},
    }
    result = validate_public_request(payload)

    assert result.ok is False
    assert result.reason in {"provider_execution_detail_rejected", "invalid_public_request"}
    with pytest.raises(ValidationError):
        PublicDataRequest.model_validate(payload)


def test_public_request_rejects_unknown_public_field() -> None:
    payload = {
        "item": "日线",
        "market": "CN_A",
        "instrument": "600519.SH",
        "purpose": "market_report",
        "unknown_public_field": "x",
    }
    result = validate_public_request(payload)

    assert result.ok is False
    assert result.reason == "invalid_public_request"
    with pytest.raises(ValidationError):
        PublicDataRequest.model_validate(payload)


def test_public_request_rejects_top_level_secret_keys_before_planning() -> None:
    payload = {"item": "日线", "api_key": "x", "params": {}}
    result = validate_public_request(payload)

    assert result.ok is False
    assert result.reason == "provider_execution_detail_rejected"


def test_public_request_rejects_internal_api_input() -> None:
    payload = {"item": "日线", "api_id": "cn_a.daily_bar", "purpose": "market_report"}
    result = validate_public_request(payload)

    assert result.ok is False
    assert result.reason == "provider_execution_detail_rejected"


def test_same_public_api_candidates_across_consumers() -> None:
    consumers = ("report", "select", "ui_probe", "maintenance")
    candidates = [
        tuple((call.provider_id, call.catalog_endpoint_id) for call in plan_public_data_requests([_request(consumer=consumer)]).planned_calls)
        for consumer in consumers
    ]

    assert len(set(candidates)) == 1


def test_public_planner_derives_internal_api_id_from_business_item() -> None:
    plan = plan_public_data_requests([_request()])

    assert plan.planned_calls
    assert {call.public_api_id for call in plan.planned_calls} == {"cn_a.daily_bar"}
    assert not plan.skipped_needs


def test_public_result_contract_requires_payload_fields_not_only_dataset_ref() -> None:
    request = PublicDataRequest(
        request_id="request-sector-flow",
        item="板块资金",
        market="CN_A",
        instrument="600519.SH",
        granularity="event",
        requested_by_worker="hot_money_tracker",
        purpose="hot_money_report",
        deadline_at=datetime(2026, 6, 14, tzinfo=UTC),
        consumer="report",
    )
    result = DataResult(
        request_id=request.request_id,
        status=DataResultStatus.READY,
        rows=({"sector_name": "白酒", "timestamp": "2026-06-14T00:00:00Z"},),
        dataset_refs=("dataset:sector_snapshot:CN_A:industry-only",),
        as_of=datetime(2026, 6, 14, tzinfo=UTC),
    )

    assert public_result_satisfies_contract(request, result) is False
