from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeState:
    status: str = "frontline_running"


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.requests.append(request)
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeState:
        return _FakeState()


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls = 0

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        self.calls += 1
        return {"text": "ok"}


def _controller() -> tuple[ChatController, _FakeChatTransport, _FakeRunner]:
    transport = _FakeChatTransport()
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    return controller, transport, runner


def test_normal_chat_never_starts_report_workflow() -> None:
    controller, transport, runner = _controller()
    result = controller.send_chat_message(request_id="n-1", context_id="ctx-1", text="你好")
    assert "error" not in result
    assert transport.calls == 1
    assert runner.calls == 0


def test_report_request_only_creates_confirmation_card_before_confirm() -> None:
    controller, transport, runner = _controller()
    result = controller.send_chat_message(request_id="n-2", context_id="ctx-2", text="/report BTC")
    assert "confirmationCard" in result
    assert transport.calls == 0
    assert runner.calls == 0


def test_confirm_creates_workflow_with_report_command_entry_point() -> None:
    controller, _, runner = _controller()
    draft_result = controller.create_intent_draft(
        request_id="d-1",
        source_message_id="m-1",
        text="/report BTC",
    )
    draft_id = draft_result["draft"]["draftId"]
    confirm_result = controller.confirm_intent_draft(
        request_id="c-1",
        draft_id=draft_id,
        decision="confirm",
    )
    assert confirm_result["status"] == "confirmed"
    assert runner.calls == 1
    request = runner.requests[0]
    assert request.entry_point.value == "report_command"
    assert request.ticker == "BTC"
    assert request.company_name == "Bitcoin"
    assert request.market == "CRYPTO"
    assert request.profile == "CRYPTO"
    assert request.currency == "USDT"
    assert request.currency_symbol == "USDT"
    assert request.current_date == request.end_date
    assert date.fromisoformat(request.end_date) - date.fromisoformat(request.start_date) == timedelta(days=365)
