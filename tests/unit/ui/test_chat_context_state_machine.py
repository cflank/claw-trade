from __future__ import annotations

from claw_trade.ui_backend.chat_context import create_normal_chat_context, switch_chat_context, to_chat_context_for_user_payload
from claw_trade.ui_contracts.enums import ChatContextKind


def test_chat_context_switch_and_lock_hidden_from_user_payload() -> None:
    context = create_normal_chat_context("ctx-1")
    following = switch_chat_context(
        context,
        kind=ChatContextKind.TASK_FOLLOWING,
        active_task_id="task-1",
        locked_workflow_run_id="run-secret",
    )
    payload = to_chat_context_for_user_payload(following, workflow_running=True)
    assert payload["contextId"] == "ctx-1"
    assert payload["kind"] == "task_following"
    assert payload["activeTaskId"] == "task-1"
    assert "workflowRunning" not in payload
    assert "lockedWorkflowRunId" not in payload


def test_switch_back_to_normal_clears_task_and_report_binding() -> None:
    context = create_normal_chat_context("ctx-2")
    reading = switch_chat_context(context, kind=ChatContextKind.REPORT_READING, active_report_id="report-1")
    normal = switch_chat_context(reading, kind=ChatContextKind.NORMAL_CHAT)
    payload = to_chat_context_for_user_payload(normal)
    assert payload["kind"] == "normal_chat"
    assert "activeTaskId" not in payload
    assert "activeReportId" not in payload
