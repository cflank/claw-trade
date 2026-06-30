from __future__ import annotations

from dataclasses import dataclass

import pytest
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeState:
    status: str = "frontline_running"


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeState:
        return _FakeState()


def _task_input() -> dict[str, object]:
    return {
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
    }


def test_failed_task_not_in_history_and_not_kept_in_right_rail() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    payload = queue.enqueue_report_task(request_id="r1", task_input=_task_input(), source="manual")
    task_id = payload["task"]["taskId"]
    failed = queue.handle_report_failed(task_id, RuntimeError("workflow_failed"))
    assert failed is not None
    assert failed.status.value == "failed"
    assert queue.list_saved_reports_for_user() == []
    assert task_id not in queue.right_rail_active_task_ids()
    assert failed.failure is not None
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["lastTerminalTask"]["taskId"] == task_id
    assert snapshot["lastTerminalTask"]["status"] == "failed"


def test_export_asset_failure_is_visible_as_report_export_failure() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    payload = queue.enqueue_report_task(request_id="r1", task_input=_task_input(), source="manual")
    task_id = payload["task"]["taskId"]

    failed = queue.handle_report_failed(task_id, "export_report_assets: 报告导出失败：未找到可复制的图表资产")

    assert failed is not None
    assert failed.failure is not None
    assert failed.failure.code == "REPORT_EXPORT_FAILED"
    assert "报告导出失败" in failed.failure.user_message
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["lastTerminalTask"]["failure"]["code"] == "REPORT_EXPORT_FAILED"


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        (
            "openclaw_runtime: provider returned insufficient_quota",
            "报告模型调用失败，模型账户额度不足或计费异常，请到服务商后台处理后重新测试。",
        ),
        (
            "openclaw_runtime: 429 rate limit exceeded",
            "报告模型调用失败，被服务商限流，请稍后重试或降低并发。",
        ),
        (
            "openclaw_runtime: API key expired",
            "报告模型调用失败，API Key 已过期，请到设置更新后重新测试。",
        ),
    ],
)
def test_llm_runtime_failure_is_visible_as_plain_task_message(reason: str, message: str) -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    payload = queue.enqueue_report_task(request_id=f"r-{reason}", task_input=_task_input(), source="manual")
    task_id = payload["task"]["taskId"]

    failed = queue.handle_report_failed(task_id, reason)

    assert failed is not None
    assert failed.failure is not None
    assert failed.failure.code == "ASSISTANT_UNAVAILABLE"
    assert failed.failure.user_message == message
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["lastTerminalTask"]["failure"]["message"] == message


def test_final_report_structure_failure_is_visible_as_report_export_failure() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    payload = queue.enqueue_report_task(request_id="r1", task_input=_task_input(), source="manual")
    task_id = payload["task"]["taskId"]

    failed = queue.handle_report_failed(
        task_id,
        "stage_batch: final_report_structure: workers=['report_polisher']; "
        "reasons=['report_polisher 非首段禁止 H1 标题']",
    )

    assert failed is not None
    assert failed.failure is not None
    assert failed.failure.code == "REPORT_EXPORT_FAILED"
    assert "助手服务暂不可用" not in failed.failure.user_message
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["lastTerminalTask"]["failure"]["code"] == "REPORT_EXPORT_FAILED"
