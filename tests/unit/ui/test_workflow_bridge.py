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
