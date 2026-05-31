from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.channel_text_inbound import (
    ChannelReplyTarget,
    ChannelTextInboundController,
    ChannelTextMessage,
)
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


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def chat_send(self, *, context_id: str, text: str, request_id: str):  # type: ignore[no-untyped-def]
        self.calls.append({"contextId": context_id, "text": text, "requestId": request_id})
        return {"text": f"echo:{text}"}


def _controller() -> tuple[ChannelTextInboundController, _FakeRunner, _FakeChatTransport]:
    runner = _FakeRunner()
    chat_transport = _FakeChatTransport()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(chat_transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    return ChannelTextInboundController(chat_controller), runner, chat_transport


def _message(request_id: str, text: str, *, sender_id: str = "sender-1") -> ChannelTextMessage:
    return ChannelTextMessage(
        request_id=request_id,
        channel_kind="wechat_clawbot",
        account_id="account-1",
        sender_id=sender_id,
        text=text,
        message_id=f"m-{request_id}",
    )


def test_ordinary_wechat_text_uses_normal_chat_without_report_workflow() -> None:
    controller, runner, chat_transport = _controller()
    result = controller.handle_message(_message("r-1", "你好"))
    assert result == {"handled": True, "replyText": "echo:你好", "state": "replied"}
    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["context"]["contextId"] == "wechat_clawbot:account-1:sender-1"
    assert snapshot["context"]["title"] == "微信聊天"
    assert [item["text"] for item in snapshot["messages"]] == ["你好", "echo:你好"]
    assert runner.calls == 0
    assert chat_transport.calls == [
        {
            "contextId": "wechat_clawbot:account-1:sender-1",
            "text": "你好",
            "requestId": "r-1",
        }
    ]


def test_report_message_returns_confirmation_without_starting_workflow() -> None:
    controller, runner, _ = _controller()
    result = controller.handle_message(_message("r-2", "/report TSLA"))
    assert result["handled"] is True
    assert result["state"] == "awaiting_confirmation"
    assert "标的：TSLA" in result["replyText"]
    assert "回复“确认”" in result["replyText"]
    snapshot = controller.latest_conversation_snapshot()
    assert [item["kind"] for item in snapshot["messages"]] == ["plain", "confirmation_card"]
    assert snapshot["messages"][0]["text"] == "/report TSLA"
    assert snapshot["messages"][1]["cardId"] == "card-draft-1"
    assert snapshot["confirmationCards"]["card-draft-1"]["title"] == "请确认是否创建完整报告"
    assert runner.calls == 0


def test_confirm_reply_starts_existing_report_workflow() -> None:
    controller, runner, _ = _controller()
    controller.handle_message(_message("r-3", "/report TSLA"))
    result = controller.handle_message(_message("r-4", "确认"))
    assert result["handled"] is True
    assert result["state"] == "confirmed"
    assert "报告任务已提交" in result["replyText"]
    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["context"]["kind"] == "task_following"
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["确认", "报告任务已启动，正在生成。"]
    assert runner.calls == 1
    assert runner.requests[0].entry_point.value == "report_command"


def test_cancel_reply_does_not_start_workflow() -> None:
    controller, runner, _ = _controller()
    controller.handle_message(_message("r-5", "/report TSLA"))
    result = controller.handle_message(_message("r-6", "取消"))
    assert result == {"handled": True, "replyText": "已取消。", "state": "cancelled"}
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["取消", "已取消。"]
    assert runner.calls == 0


def test_wechat_report_completion_appends_completed_card_to_same_conversation(tmp_path: Path) -> None:
    runner = _FakeRunner()
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    def load_completed_state(run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            status="completed",
            run_id=run_id,
            run_dir=run_dir,
            updated_at="2026-05-25T15:15:34Z",
        )

    runner.load_state = load_completed_state  # type: ignore[method-assign]
    chat_ref: dict[str, ChatController] = {}

    def on_completed(task, workflow_state):  # type: ignore[no-untyped-def]
        assert task.origin_context_id == "wechat_clawbot:account-1:sender-1"
        chat_ref["controller"].append_report_completed_message(
            context_id=task.origin_context_id,
            report_id=workflow_state.run_id,
            task_id=task.task_id,
            text="报告已完成，可查看完整内容。",
        )

    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        completed_report_writer=on_completed,
    )
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    chat_ref["controller"] = chat_controller
    full_report_requests: list[tuple[str, str, ChannelReplyTarget]] = []

    def request_full_report(report_id: str, request_id: str, target: ChannelReplyTarget) -> dict[str, object]:
        full_report_requests.append((report_id, request_id, target))
        return {"sent": True, "userMessage": "完整报告已发送。"}

    controller = ChannelTextInboundController(
        chat_controller,
        request_full_report_file=request_full_report,
    )

    controller.handle_message(_message("r-7", "/report TSLA"))
    controller.handle_message(_message("r-8", "确认"))
    queue.get_report_queue_snapshot_for_user()

    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["context"]["kind"] == "report_reading"
    assert snapshot["context"]["activeReportId"] == "run-1"
    completed = snapshot["messages"][-1]
    assert completed["kind"] == "report_completed"
    assert completed["reportId"] == "run-1"
    assert completed["text"] == "报告已完成，可查看完整内容。"

    full = controller.handle_message(_message("r-9", "发送完整报告"))
    assert full == {"handled": True, "replyText": "完整报告已发送。", "state": "sent"}
    assert [(report_id, target.sender_id, target.account_id) for report_id, _, target in full_report_requests] == [
        ("run-1", "sender-1", "account-1")
    ]
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["发送完整报告", "完整报告已发送。"]
