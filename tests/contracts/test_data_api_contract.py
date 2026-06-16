from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.data_gateway.api import DataAPI
from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, GapReason, GapSeverity, Market
from claw_trade.data_gateway.public_api import PublicDataRequest


class _Service:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request_data(self, requests: list[PublicDataRequest]) -> list[DataResult]:
        self.calls.append("request_data")
        return [
            DataResult(
                request_id=request.request_id,
                status=DataResultStatus.READY,
                dataset_refs=(f"dataset:{request.request_id}",),
                as_of=datetime(2026, 5, 31, tzinfo=UTC),
            )
            for request in requests
        ]

    def resolve_company_names(self, *, market: Market | str, symbol_ids: list[str] | tuple[str, ...], dataset: str = "daily_bar"):
        self.calls.append("resolve_company_names")
        assert market == Market.CN_A or market == "CN_A"
        assert dataset == "daily_bar"
        return {"600519.SH": "贵州茅台"}


def _request_payload(request_id: str) -> dict[str, object]:
    return {
        "request_id": request_id,
        "item": "日线",
        "market": "CN_A",
        "instrument": "600519.SH",
        "granularity": "daily",
        "requested_by_worker": "market_analyst",
        "purpose": "market_report",
        "deadline_at": datetime(2026, 5, 31, tzinfo=UTC),
    }


def test_data_api_exposes_only_public_request_remote_entry() -> None:
    api = DataAPI(_Service())

    assert not hasattr(api, "get_data")
    assert not hasattr(api, "get_data_batch")
    assert not hasattr(api, "get_data_needs")
    assert hasattr(api, "request_data")


def test_request_data_preserves_input_order_and_reports_invalid_request() -> None:
    service = _Service()
    api = DataAPI(service)

    results = api.request_data(
        (
            _request_payload("request-ready-1"),
            {**_request_payload("request-invalid"), "api_id": "cn_a.daily_bar"},
            _request_payload("request-ready-2"),
        )
    )

    assert [item.request_id for item in results] == ["request-ready-1", "request-invalid", "request-ready-2"]
    assert results[0].status == DataResultStatus.READY
    assert results[1].status == DataResultStatus.ERROR
    assert results[1].gaps[0].reason == GapReason.INVALID_REQUEST
    assert results[2].status == DataResultStatus.READY
    assert service.calls == ["request_data"]


def test_request_data_accepts_model_instances() -> None:
    service = _Service()
    api = DataAPI(service)
    request = PublicDataRequest.model_validate(_request_payload("request-model"))

    [result] = api.request_data((request,))

    assert result.request_id == "request-model"
    assert result.dataset_refs == ("dataset:request-model",)


def test_resolve_company_names_delegates_to_data_service() -> None:
    service = _Service()
    api = DataAPI(service)

    names = api.resolve_company_names(market="CN_A", symbol_ids=("600519.SH",))

    assert names == {"600519.SH": "贵州茅台"}
    assert service.calls == ["resolve_company_names"]


def test_invalid_public_request_result_uses_gap_contract() -> None:
    api = DataAPI(_Service())

    [result] = api.request_data(({"request_id": "bad", "market": "CN_A"},))

    assert result.status == DataResultStatus.ERROR
    assert result.gaps
    gap: DataGap = result.gaps[0]
    assert gap.severity == GapSeverity.BLOCKER
    assert gap.reason == GapReason.INVALID_REQUEST
