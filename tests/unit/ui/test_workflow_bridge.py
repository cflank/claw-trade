from __future__ import annotations

from dataclasses import dataclass

from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.workflow.models import WorkflowEntryPoint


@dataclass
class _FakeState:
    status: str


class _FakeRunner:
    def __init__(self) -> None:
        self.requests = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return "run-1"

    def load_state(self, run_id: str) -> _FakeState:
        assert run_id == "run-1"
        return _FakeState(status="frontline_running")


def _task() -> dict[str, object]:
    return {
        "instrumentCode": "BTC",
        "instrumentName": "Bitcoin",
        "market": "CRYPTO",
        "companyName": "Bitcoin",
        "currencySymbol": "$",
        "startDate": "2026-05-01",
        "endDate": "2026-05-19",
        "currentDate": "2026-05-19",
        "workflowSettings": {
            "maxDebateRounds": 2,
            "maxRiskDiscussRounds": 3,
            "frontlineExecutionMode": "parallel",
            "defaultProfile": "CRYPTO",
            "defaultMarket": "CRYPTO",
            "defaultCurrency": "USD",
            "defaultCurrencySymbol": "$",
        },
    }


def test_build_run_request_uses_frozen_snapshot() -> None:
    runner = _FakeRunner()
    bridge = ReportWorkflowBridge(runner)
    request = bridge.build_run_request(_task())
    assert request.max_debate_rounds == 2
    assert request.max_risk_discuss_rounds == 3
    assert request.frontline_execution_mode == "parallel"
    assert request.profile == "CRYPTO"
    assert request.entry_point == WorkflowEntryPoint.REPORT_COMMAND


def test_create_and_poll_workflow_run() -> None:
    runner = _FakeRunner()
    bridge = ReportWorkflowBridge(runner)
    run = bridge.create_workflow_run(_task())
    assert run.run_id == "run-1"
    status = bridge.poll_workflow_run("run-1")
    assert status == {"runId": "run-1", "status": "frontline_running"}
    state = bridge.load_workflow_state("run-1")
    assert state.status == "frontline_running"


def test_build_run_request_resolves_cn_a_company_name_from_data_layer() -> None:
    runner = _FakeRunner()

    def _resolver(*, market: str, symbol_ids: tuple[str, ...]):
        assert market == "CN_A"
        assert symbol_ids == ("688017.SH",)
        return {"688017.SH": "绿的谐波"}

    bridge = ReportWorkflowBridge(runner, company_name_resolver=_resolver)
    task = _task()
    settings = dict(task["workflowSettings"])  # type: ignore[arg-type]
    settings.update(
        {
            "defaultProfile": "CN_A",
            "defaultMarket": "CN_A",
            "defaultCurrency": "CNY",
            "defaultCurrencySymbol": "\u00a5",
        }
    )
    task.update(
        {
            "instrumentCode": "688017.SH",
            "instrumentName": "688017.SH",
            "market": "CN_A",
            "companyName": "688017.SH",
            "workflowSettings": settings,
        }
    )

    request = bridge.build_run_request(task)

    assert request.ticker == "688017.SH"
    assert request.company_name == "绿的谐波"


def test_build_run_request_does_not_use_ticker_as_company_name_when_unresolved() -> None:
    runner = _FakeRunner()
    bridge = ReportWorkflowBridge(runner)
    task = _task()
    settings = dict(task["workflowSettings"])  # type: ignore[arg-type]
    settings.update(
        {
            "defaultProfile": "CN_A",
            "defaultMarket": "CN_A",
            "defaultCurrency": "CNY",
            "defaultCurrencySymbol": "\u00a5",
        }
    )
    task.update(
        {
            "instrumentCode": "688017.SH",
            "instrumentName": "688017.SH",
            "market": "CN_A",
            "companyName": "688017.SH",
            "workflowSettings": settings,
        }
    )

    request = bridge.build_run_request(task)

    assert request.ticker == "688017.SH"
    assert request.company_name == "名称未查到"
