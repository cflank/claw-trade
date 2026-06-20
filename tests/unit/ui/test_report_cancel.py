from __future__ import annotations

from dataclasses import dataclass

from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeState:
    status: str = "frontline_running"


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled_runs: list[str] = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeState:
        return _FakeState()

    def cancel_run(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        return True


def _task_input(code: str) -> dict[str, object]:
    return {
        "instrumentCode": code,
        "instrumentName": code,
        "market": "US",
        "companyName": code,
        "currencySymbol": "$",
        "startDate": "2026-05-01",
        "endDate": "2026-05-19",
        "currentDate": "2026-05-19",
        "workflowSettings": {
            "maxDebateRounds": 1,
            "maxRiskDiscussRounds": 1,
            "frontlineExecutionMode": "parallel",
            "defaultProfile": "US",
            "defaultMarket": "US",
            "defaultCurrency": "USD",
            "defaultCurrencySymbol": "$",
        },
    }


def test_cancel_queued_task_succeeds() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queued = queue.enqueue_report_task(request_id="r2", task_input=_task_input("TSLA"), source="manual")
    cancelled = queue.cancel_report_task(request_id="c1", task_id=queued["task"]["taskId"])
    assert cancelled["task"]["status"] == "cancelled"


def test_cancel_running_task_stops_workflow_and_hides_active_task() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    running = queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    task_id = running["task"]["taskId"]
    cancelled = queue.cancel_report_task(request_id="c2", task_id=task_id)

    assert cancelled["task"]["status"] == "cancelled"
    assert cancelled["queueSnapshot"]["runningTask"] is None
    assert cancelled["message"] == "已停止报告任务。"
    assert runner.cancelled_runs == ["run-1"]
    task = queue.get_task_for_testing(task_id)
    assert task is not None
    assert task.status.value == "cancelled"
    assert task_id not in queue.right_rail_active_task_ids()
