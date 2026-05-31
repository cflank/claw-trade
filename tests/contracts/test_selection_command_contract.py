from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.store import SelectionRunStore
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        _ = request
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, text, request_id)
        return {"text": "fallback"}


def _controller() -> ChatController:
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    return ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
        selection_controller=SelectionController(store=SelectionRunStore()),
    )


def test_select_command_is_not_report_command() -> None:
    controller = _controller()

    select_result = controller.send_chat_message(
        request_id="sel-10-contract-select",
        context_id="ctx-select",
        text="/select",
    )
    assert "selection" in select_result
    assert select_result["selection"]["code"] == "unavailable"
    assert "confirmationCard" not in select_result

    report_result = controller.send_chat_message(
        request_id="sel-10-contract-report",
        context_id="ctx-report",
        text="/report 600519.SH",
    )
    assert "selection" not in report_result
    assert "confirmationCard" in report_result
    assert report_result["confirmationCard"]["instrumentCode"] == "600519.SH"


def test_select_command_uses_single_worker_minimal_policy_in_evidence() -> None:
    controller = _controller()

    result = controller.send_chat_message(
        request_id="sel-10-contract-policy",
        context_id="ctx-policy",
        text="/select",
    )

    evidence_path = Path(result["selection"]["evidencePath"])
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["entry_point"] == "select_command"
    assert payload["system_context_policy"] == "single_worker_minimal"
