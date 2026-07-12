from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.production import host_locks, paths
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.ui_contracts.enums import ChatContextKind


@pytest.fixture(autouse=True)
def installed_report_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_lock = tmp_path / "report-active.lock"
    report_lock.touch()
    report_lock.chmod(0o660)
    monkeypatch.setattr(paths, "REPORT_ACTIVE_LOCK_PATH", report_lock)
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid(), os.getgid()))


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        return {"text": "ok"}


def _controller() -> tuple[ChatController, ReportTaskQueue]:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    return controller, queue


def test_running_task_mutation_does_not_change_existing_run_request() -> None:
    controller, queue = _controller()
    task_payload = queue.enqueue_report_task(
        request_id="create-1",
        task_input={
            "instrumentCode": "AAPL",
            "instrumentName": "Apple",
            "market": "US",
            "companyName": "Apple",
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
        },
        source="manual",
    )
    task_id = task_payload["task"]["taskId"]
    task_before = queue.get_task_for_testing(task_id)
    assert task_before is not None
    run_id_before = task_before.run_id
    controller.switch_chat_context(
        context_id="ctx-task",
        kind=ChatContextKind.TASK_FOLLOWING,
        active_task_id=task_id,
        locked_workflow_run_id=run_id_before,
    )
    result = controller.send_chat_message(
        request_id="mut-1",
        context_id="ctx-task",
        text="把风险部分再加上美元流动性影响",
    )
    assert "confirmationCard" in result
    task_after = queue.get_task_for_testing(task_id)
    assert task_after is not None
    assert task_after.run_id == run_id_before
