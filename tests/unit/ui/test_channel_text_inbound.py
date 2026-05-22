from __future__ import annotations

from dataclasses import dataclass

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.channel_text_inbound import ChannelTextInboundController, ChannelTextMessage
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
        _ = run_id
        return _FakeState()


class _FailChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str):  # type: ignore[no-untyped-def]
        _ = (context_id, text, request_id)
        raise AssertionError("channel text bridge must not use normal chat")


def _controller() -> tuple[ChannelTextInboundController, _FakeRunner]:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FailChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    return ChannelTextInboundController(chat_controller), runner


def _message(request_id: str, text: str, *, sender_id: str = "sender-1") -> ChannelTextMessage:
    return ChannelTextMessage(
        request_id=request_id,
        channel_kind="wechat_clawbot",
        account_id="account-1",
        sender_id=sender_id,
        text=text,
        message_id=f"m-{request_id}",
    )


def test_ordinary_wechat_text_is_not_handled_by_report_workflow() -> None:
    controller, runner = _controller()
    result = controller.handle_message(_message("r-1", "你好"))
    assert result == {"handled": False}
    assert runner.calls == 0


def test_report_message_returns_confirmation_without_starting_workflow() -> None:
    controller, runner = _controller()
    result = controller.handle_message(_message("r-2", "/report TSLA"))
    assert result["handled"] is True
    assert result["state"] == "awaiting_confirmation"
    assert "标的：TSLA" in result["replyText"]
    assert "回复“确认”" in result["replyText"]
    assert runner.calls == 0


def test_confirm_reply_starts_existing_report_workflow() -> None:
    controller, runner = _controller()
    controller.handle_message(_message("r-3", "/report TSLA"))
    result = controller.handle_message(_message("r-4", "确认"))
    assert result["handled"] is True
    assert result["state"] == "confirmed"
    assert "报告任务已提交" in result["replyText"]
    assert runner.calls == 1
    assert runner.requests[0].entry_point.value == "report_command"


def test_cancel_reply_does_not_start_workflow() -> None:
    controller, runner = _controller()
    controller.handle_message(_message("r-5", "/report TSLA"))
    result = controller.handle_message(_message("r-6", "取消"))
    assert result == {"handled": True, "replyText": "已取消。", "state": "cancelled"}
    assert runner.calls == 0
