from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.controller import SelectCommandCode, SelectCommandResult
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
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        return _FakeWorkflowState()


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls = 0

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        self.calls += 1
        return {"text": f"echo:{text}"}


@dataclass
class _FakeSelectionController:
    calls: int = 0

    def handle_select_command(self, *, raw_text: str, request_id: str, user_id: str | None = None) -> SelectCommandResult:
        self.calls += 1
        return SelectCommandResult(
            code=SelectCommandCode.COMPLETED,
            chat_text="`/select` 测试结果",
            select_workflow_run_id="select-test-run",
            evidence_path=Path("runs/selection/workflows/select-test-run/evidence.json"),
        )


def _build_controller(
    *,
    selection_controller: _FakeSelectionController | None = None,
) -> tuple[ChatController, _FakeChatTransport, _FakeWorkflowRunner]:
    transport = _FakeChatTransport()
    workflow_runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(workflow_runner))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
        selection_controller=selection_controller,
    )
    return controller, transport, workflow_runner


def test_normal_chat_passthrough_openclaw() -> None:
    controller, transport, workflow_runner = _build_controller()
    result = controller.send_chat_message(request_id="req-1", context_id="ctx-1", text="你好")
    assert "error" not in result
    assert result["assistantReply"] == "echo:你好"
    assert transport.calls == 1
    assert workflow_runner.calls == 0
    assert result["messages"][-1]["actor"] == "assistant"
    assert "messageId" in result["messages"][-1]


def test_report_intent_only_builds_confirmation_card() -> None:
    controller, transport, workflow_runner = _build_controller()
    result = controller.send_chat_message(request_id="req-2", context_id="ctx-2", text="/report BTC")
    assert "error" not in result
    assert "confirmationCard" in result
    assert result["context"]["kind"] == "intent_confirming"
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_natural_language_report_intent_still_builds_confirmation_card() -> None:
    controller, transport, workflow_runner = _build_controller()
    result = controller.send_chat_message(request_id="req-2b", context_id="ctx-2b", text="请给我 BTC 报告")
    assert "error" not in result
    assert "confirmationCard" in result
    assert result["context"]["kind"] == "intent_confirming"
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_select_command_is_routed_to_selection_before_report_intent() -> None:
    selection = _FakeSelectionController()
    controller, transport, workflow_runner = _build_controller(selection_controller=selection)
    result = controller.send_chat_message(request_id="req-select", context_id="ctx-select", text="/select")
    assert "error" not in result
    assert result["selection"]["code"] == "completed"
    assert selection.calls == 1
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_create_intent_draft_hourly_rejected() -> None:
    controller, _, _ = _build_controller()
    result = controller.create_intent_draft(
        request_id="req-3",
        source_message_id="m-1",
        text="每小时给我 BTC 报告",
    )
    assert result["error"]["code"] == "INVALID_INPUT"


def test_confirm_scheduled_report_and_price_alert_use_real_services() -> None:
    controller, _, _ = _build_controller()

    schedule_draft = controller.create_intent_draft(
        request_id="req-sd-draft",
        source_message_id="m-sd",
        text="每天 08:00 给我 BTC 报告",
    )
    schedule_confirm = controller.confirm_intent_draft(
        request_id="req-sd-confirm",
        draft_id=schedule_draft["draft"]["draftId"],
        decision="confirm",
    )
    assert schedule_confirm["status"] == "confirmed"
    scheduled = schedule_confirm["scheduledReport"]
    assert scheduled.scheduledReportId.startswith("schedule-")
    assert scheduled.instrumentCode == "BTC"
    assert not isinstance(scheduled, dict)

    alert_draft = controller.create_intent_draft(
        request_id="req-pa-draft",
        source_message_id="m-pa",
        text="BTC 高于 70000 提醒我",
    )
    alert_confirm = controller.confirm_intent_draft(
        request_id="req-pa-confirm",
        draft_id=alert_draft["draft"]["draftId"],
        decision="confirm",
    )
    assert alert_confirm["status"] == "confirmed"
    alert = alert_confirm["priceAlert"]
    assert alert.priceAlertId.startswith("alert-")
    assert alert.condition.operator == "above"
    assert not isinstance(alert, dict)
