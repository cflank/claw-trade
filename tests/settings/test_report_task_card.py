from __future__ import annotations

from dataclasses import dataclass

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
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
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, request_id)
        return {"text": f"chat:{text}"}


def _controller() -> ChatController:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    return ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )


def test_report_command_builds_three_field_confirmation_card() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-card-1", context_id="ctx-1", text="/report TSLA")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：TSLA", "名称：TSLA", "市场：US"]
    assert card["instrumentCode"] == "TSLA"
    assert card["instrumentName"] == "TSLA"
    assert card["market"] == "US"
    card_text = "\n".join(card["summaryLines"])
    for forbidden in ("报告方案", "计价单位", "profile", "默认币种", "worker", "debate", "risk"):
        assert forbidden not in card_text


def test_non_report_chat_does_not_enter_report_workflow() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-chat-1", context_id="ctx-1", text="帮我做一份 BTC 报告")
    assert "confirmationCard" not in result
    assert result["assistantReply"] == "chat:帮我做一份 BTC 报告"
    assert result["context"]["kind"] == "normal_chat"


def test_confirm_with_symbol_override_reidentifies_name_and_market() -> None:
    controller = _controller()
    send = controller.send_chat_message(request_id="s06-card-2", context_id="ctx-1", text="/report BTC")
    card = send["confirmationCard"]
    confirmed = controller.confirm_intent_draft(
        request_id="s06-confirm-1",
        draft_id=card["draftId"],
        decision="confirm",
        overrides={"instrumentCode": "TSLA"},
    )
    task = confirmed["task"]
    assert task["instrumentCode"] == "TSLA"
    assert task["market"] == "US"
    assert task["companyName"] == "TSLA"


def test_confirm_rejects_symbol_market_mismatch() -> None:
    controller = _controller()
    send = controller.send_chat_message(request_id="s06-card-3", context_id="ctx-1", text="/report TSLA")
    card = send["confirmationCard"]
    rejected = controller.confirm_intent_draft(
        request_id="s06-confirm-2",
        draft_id=card["draftId"],
        decision="confirm",
        overrides={"market": "CRYPTO"},
    )
    assert rejected["error"]["code"] == "INVALID_INPUT"
    assert "不匹配" in rejected["error"]["message"]
