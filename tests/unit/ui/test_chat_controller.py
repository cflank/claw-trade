from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.controller import SelectCommandCode, SelectCommandResult
from claw_trade.ui_backend.chat_controller import ChatController, _format_confirmed_message
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.task_costs import TaskCostSnapshot
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
        self.delay_seconds = 0.0

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        self.calls += 1
        return {"text": f"echo:{text}"}


@dataclass
class _FakeSelectionController:
    calls: int = 0
    code: SelectCommandCode = SelectCommandCode.COMPLETED
    chat_text: str = "`/select` 测试结果"
    evidence_path: Path = Path("runs/selection/workflows/select-test-run/evidence.json")

    def handle_select_command(self, *, raw_text: str, request_id: str, user_id: str | None = None) -> SelectCommandResult:
        self.calls += 1
        return SelectCommandResult(
            code=self.code,
            chat_text=self.chat_text,
            select_workflow_run_id="select-test-run",
            evidence_path=self.evidence_path,
            failure_reason="selection_result_invalid:test" if self.code == SelectCommandCode.FAILED else None,
        )


def _build_controller(
    *,
    selection_controller: _FakeSelectionController | None = None,
    task_cost_snapshots: list[TaskCostSnapshot] | None = None,
) -> tuple[ChatController, _FakeChatTransport, _FakeWorkflowRunner]:
    transport = _FakeChatTransport()
    workflow_runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(workflow_runner))
    snapshot_provider = None
    if task_cost_snapshots is not None:
        snapshots = iter(task_cost_snapshots)
        snapshot_provider = lambda: next(snapshots)
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
        selection_controller=selection_controller,
        task_cost_snapshot_provider=snapshot_provider,
    )
    return controller, transport, workflow_runner


def _wait_for_latest_assistant_text(controller: ChatController, context_id: str, expected: str) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        messages = controller.get_chat_session(context_id=context_id)["messages"]
        if messages and messages[-1]["actor"] == "assistant" and messages[-1]["text"] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"latest assistant text did not become {expected!r}")


def test_normal_chat_passthrough_openclaw() -> None:
    controller, transport, workflow_runner = _build_controller()
    transport.delay_seconds = 0.05
    result = controller.send_chat_message(request_id="req-1", context_id="ctx-1", text="你好")
    assert "error" not in result
    assert result["assistantReply"] == "echo:你好"
    assert workflow_runner.calls == 0
    assert result["messages"][-1]["actor"] == "assistant"
    assert result["messages"][-1]["text"] == "echo:你好"
    assert "messageId" in result["messages"][-1]
    assert transport.calls == 1


def test_clear_chat_session_removes_messages_without_calling_runtime() -> None:
    controller, transport, workflow_runner = _build_controller()
    controller.send_chat_message(request_id="req-clear-seed", context_id="ctx-clear", text="你好")
    _wait_for_latest_assistant_text(controller, "ctx-clear", "echo:你好")

    result = controller.clear_chat_session(context_id="ctx-clear")

    assert result["context"]["contextId"] == "ctx-clear"
    assert result["context"]["kind"] == "normal_chat"
    assert result["messages"] == []
    assert transport.calls == 1
    assert workflow_runner.calls == 0


def test_report_intent_only_builds_confirmation_card() -> None:
    controller, transport, workflow_runner = _build_controller()
    result = controller.send_chat_message(request_id="req-2", context_id="ctx-2", text="/report BTC")
    assert "error" not in result
    assert "confirmationCard" in result
    assert result["context"]["kind"] == "intent_confirming"
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_price_alert_intent_builds_confirmation_card_from_chat() -> None:
    controller, transport, workflow_runner = _build_controller()

    result = controller.send_chat_message(request_id="req-alert", context_id="ctx-alert", text="BTC 高于 70000 提醒我")

    assert "error" not in result
    assert result["confirmationCard"]["title"] == "请确认是否创建价格提醒"
    assert result["context"]["kind"] == "intent_confirming"
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_report_completion_message_is_idempotent_for_same_task() -> None:
    controller, _, _ = _build_controller()

    controller.append_report_completed_message(
        context_id="ctx-completed",
        report_id="run-1",
        task_id="task-1",
        text="报告已完成。",
    )
    result = controller.append_report_completed_message(
        context_id="ctx-completed",
        report_id="run-1",
        task_id="task-1",
        text="报告已完成。",
    )

    completed = [message for message in result["messages"] if message["kind"] == "report_completed"]
    assert len(completed) == 1


def test_natural_language_report_intent_stays_normal_chat_without_report_command() -> None:
    controller, transport, workflow_runner = _build_controller()
    result = controller.send_chat_message(request_id="req-2b", context_id="ctx-2b", text="请给我 BTC 报告")
    assert "error" not in result
    assert "confirmationCard" not in result
    assert result["assistantReply"] == "echo:请给我 BTC 报告"
    assert result["context"]["kind"] == "normal_chat"
    assert workflow_runner.calls == 0
    assert transport.calls == 1


def test_select_command_is_routed_to_selection_before_report_intent() -> None:
    selection = _FakeSelectionController()
    controller, transport, workflow_runner = _build_controller(selection_controller=selection)
    result = controller.send_chat_message(request_id="req-select", context_id="ctx-select", text="/select")
    assert "error" not in result
    assert result["selection"]["code"] == "completed"
    assert selection.calls == 1
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_select_command_appends_balance_delta_cost_estimate_when_enabled() -> None:
    selection = _FakeSelectionController()
    controller, _, _ = _build_controller(
        selection_controller=selection,
        task_cost_snapshots=[
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("100.00")),
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("99.97")),
        ],
    )

    result = controller.send_chat_message(request_id="req-select-cost", context_id="ctx-select-cost", text="/select")

    assert result["messages"][-1]["text"] == (
        "`/select` 测试结果\n"
        "费用统计：Token 总数：未知；任务前余额：¥100.00；任务后余额：¥99.97；本次消费：¥0.03"
    )


def test_select_command_appends_token_cost_summary(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "select-test-run"
    dispatch_dir = workflow_dir / "dispatches" / "dispatch-1"
    dispatch_dir.mkdir(parents=True)
    (dispatch_dir / "openclaw-result.json").write_text(
        '{"provider":"deepseek","model":"deepseek-chat","usage":{"cacheRead":1000000,"input":1000000,"output":1000000}}\n',
        encoding="utf-8",
    )
    selection = _FakeSelectionController(evidence_path=workflow_dir / "evidence.json")
    controller, _, _ = _build_controller(selection_controller=selection)

    result = controller.send_chat_message(
        request_id="req-select-token-cost",
        context_id="ctx-select-token-cost",
        text="/select",
    )

    text = result["messages"][-1]["text"]
    assert text == (
        "`/select` 测试结果\n"
        "费用统计：Token 总数：3,000,000；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )


def test_select_command_reports_missing_token_usage_evidence(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "select-test-run"
    workflow_dir.mkdir()
    selection = _FakeSelectionController(evidence_path=workflow_dir / "evidence.json")
    controller, _, _ = _build_controller(selection_controller=selection)

    result = controller.send_chat_message(
        request_id="req-select-missing-token-cost",
        context_id="ctx-select-missing-token-cost",
        text="/select",
    )

    assert result["messages"][-1]["text"] == (
        "`/select` 测试结果\n"
        "费用统计：Token 总数：未知；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )


def test_select_command_does_not_append_cost_for_non_terminal_result() -> None:
    selection = _FakeSelectionController(code=SelectCommandCode.DATA_REFRESH_REQUESTED, chat_text="已启动补数据。")
    controller, _, _ = _build_controller(
        selection_controller=selection,
        task_cost_snapshots=[
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("100.00")),
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("99.97")),
        ],
    )

    result = controller.send_chat_message(request_id="req-select-refresh-cost", context_id="ctx-select-refresh-cost", text="/select")

    assert result["messages"][-1]["text"] == "已启动补数据。"


def test_select_command_failed_result_uses_failed_message_kind() -> None:
    selection = _FakeSelectionController(
        code=SelectCommandCode.FAILED,
        chat_text="`/select` 失败：选股评审服务没有正常返回。本轮结果未生效。",
    )
    controller, transport, workflow_runner = _build_controller(selection_controller=selection)

    result = controller.send_chat_message(request_id="req-select-failed", context_id="ctx-select-failed", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "failed"
    assert result["messages"][-1]["kind"] == "selection_failed"
    assert result["messages"][-1]["text"] == "`/select` 失败：选股评审服务没有正常返回。本轮结果未生效。"
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_unsupported_select_market_returns_visible_message_without_chat_fallback() -> None:
    controller, transport, workflow_runner = _build_controller()

    result = controller.send_chat_message(request_id="req-select-us", context_id="ctx-select-us", text="/select US")

    assert "error" not in result
    assert result["selection"]["code"] == "unavailable"
    assert result["selection"]["unavailableCode"] == "select_market_unsupported"
    assert result["messages"][-1]["kind"] == "selection_unavailable"
    assert result["messages"][-1]["text"] == "`/select` 当前暂不支持该市场。"
    assert transport.calls == 0
    assert workflow_runner.calls == 0


def test_unsupported_select_number_returns_visible_message_without_chat_fallback() -> None:
    controller, transport, workflow_runner = _build_controller()

    result = controller.send_chat_message(request_id="req-select-3", context_id="ctx-select-3", text="/select 3")

    assert "error" not in result
    assert result["selection"]["unavailableCode"] == "select_market_unsupported"
    assert result["messages"][-1]["text"] == "`/select` 当前暂不支持该市场。"
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


def test_scheduled_report_replacement_message_is_shown_as_confirmation_text() -> None:
    assert (
        _format_confirmed_message({"scheduledReport": object(), "message": "已替换相近的定时报告：原时间 23:59，新时间 23:58。"})
        == "已替换相近的定时报告：原时间 23:59，新时间 23:58。"
    )


def test_confirmed_or_cancelled_chat_card_does_not_return_as_active_after_reload() -> None:
    controller, _, _ = _build_controller()

    first = controller.send_chat_message(request_id="req-card-1", context_id="ctx-card", text="/report TSLA")
    confirmed = controller.confirm_intent_draft_from_chat(
        request_id="req-card-2",
        context_id="ctx-card",
        draft_id=first["confirmationCard"]["draftId"],
        decision="confirm",
        text="确认",
    )
    assert confirmed["confirmationCards"]["card-draft-1"]["status"] == "confirmed"
    assert confirmed["confirmationCards"]["card-draft-1"]["actions"] == []

    second = controller.send_chat_message(request_id="req-card-3", context_id="ctx-card-2", text="/report BTC")
    cancelled = controller.confirm_intent_draft_from_chat(
        request_id="req-card-4",
        context_id="ctx-card-2",
        draft_id=second["confirmationCard"]["draftId"],
        decision="cancel",
        text="取消",
    )
    assert cancelled["confirmationCards"]["card-draft-2"]["status"] == "cancelled"
    assert cancelled["confirmationCards"]["card-draft-2"]["actions"] == []


def test_running_task_plain_message_returns_local_status_without_openclaw() -> None:
    controller, transport, runner = _build_controller()

    draft = controller.send_chat_message(
        request_id="req-running-local-1",
        context_id="ctx-running-local",
        text="/report TSLA",
    )
    controller.confirm_intent_draft_from_chat(
        request_id="req-running-local-2",
        context_id="ctx-running-local",
        draft_id=draft["confirmationCard"]["draftId"],
        decision="confirm",
        text="确认",
    )

    result = controller.send_chat_message(
        request_id="req-running-local-hi",
        context_id="ctx-running-local",
        text="hi",
    )

    assert runner.calls == 1
    assert transport.calls == 0
    assert result["assistantReply"].startswith("报告正在生成中")
    assert "18%" in result["assistantReply"]
    assert "普通聊天暂时无法处理" in result["assistantReply"]
    assert result["messages"][-1]["kind"] == "task_progress"


def test_running_task_exit_returns_to_normal_chat_without_openclaw() -> None:
    controller, transport, runner = _build_controller()

    draft = controller.send_chat_message(
        request_id="req-running-exit-1",
        context_id="ctx-running-exit",
        text="/report TSLA",
    )
    controller.confirm_intent_draft_from_chat(
        request_id="req-running-exit-2",
        context_id="ctx-running-exit",
        draft_id=draft["confirmationCard"]["draftId"],
        decision="confirm",
        text="确认",
    )

    result = controller.send_chat_message(
        request_id="req-running-exit",
        context_id="ctx-running-exit",
        text="退出",
    )

    session = controller.get_chat_session(context_id="ctx-running-exit")
    assert runner.calls == 1
    assert transport.calls == 0
    assert result["assistantReply"] == "已回到普通聊天，报告仍在后台生成，完成后会通知你。"
    assert session["context"]["kind"] == "normal_chat"
