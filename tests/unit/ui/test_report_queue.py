from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
from claw_trade.ui_backend.report_queue import QueueError, ReportTaskQueue
from claw_trade.ui_backend.task_costs import TaskCostSnapshot
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"
    completed_workers: tuple[str, ...] = ()
    run_dir: Path | None = None


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.state = _FakeWorkflowState()
        self.requests = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.requests.append(request)
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return self.state


def _task_input(code: str, market: str = "US", source_profile: str = "US") -> dict[str, object]:
    return {
        "instrumentCode": code,
        "instrumentName": code,
        "market": market,
        "companyName": code,
        "currencySymbol": "$",
        "startDate": "2026-05-01",
        "endDate": "2026-05-19",
        "currentDate": "2026-05-19",
        "workflowSettings": {
            "maxDebateRounds": 1,
            "maxRiskDiscussRounds": 1,
            "frontlineExecutionMode": "parallel",
            "defaultProfile": source_profile,
            "defaultMarket": market,
            "defaultCurrency": "USD",
            "defaultCurrencySymbol": "$",
        },
    }


def test_serial_queue_only_one_running() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    first = queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    second = queue.enqueue_report_task(request_id="r2", task_input=_task_input("TSLA"), source="manual")
    assert first["task"]["status"] == "running"
    assert second["task"]["status"] == "queued"
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"]["instrumentCode"] == "AAPL"
    assert snapshot["queuedCount"] == 1


def test_report_queue_carries_origin_context_into_workflow_request() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    queue.enqueue_report_task(
        request_id="r-origin",
        task_input=_task_input("BTC", market="CRYPTO", source_profile="CRYPTO"),
        source="manual",
        origin_context_id="wechat_clawbot:account-1:sender-1",
    )

    assert runner.requests[0].ui_origin_context_id == "wechat_clawbot:account-1:sender-1"


def test_report_queue_blocks_enqueue_when_license_denied() -> None:
    def deny() -> None:
        raise PermissionError("设备授权已失效，请在授权页修复后重试。")

    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner), report_permission_checker=deny)

    with pytest.raises(QueueError, match="设备授权已失效") as exc:
        queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")

    assert exc.value.code == "LICENSE_BLOCKED"
    assert runner.calls == 0


def test_report_queue_blocks_start_when_license_denied_after_queued() -> None:
    allowed = True

    def check() -> None:
        if not allowed:
            raise PermissionError("设备授权已失效，请在授权页修复后重试。")

    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner), report_permission_checker=check)
    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queue.get_report_queue_snapshot_for_user()
    queued = queue.enqueue_report_task(request_id="r2", task_input=_task_input("MSFT"), source="manual")
    allowed = False
    queue.handle_report_failed("task-1", RuntimeError("workflow failed"))

    task = queue.get_task_for_testing(queued["task"]["taskId"])
    assert runner.calls == 1
    assert task is not None
    assert task.status.value == "failed"
    assert task.failure is not None
    assert task.failure.code == "LICENSE_BLOCKED"


def test_queue_limit_default_10() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()), queue_limit=10)
    queue.enqueue_report_task(request_id="r1", task_input=_task_input("RUN1"), source="manual")
    for index in range(2, 12):
        if index < 12:
            queue.enqueue_report_task(
                request_id=f"r{index}",
                task_input=_task_input(f"T{index}"),
                source="scheduled",
            )
    with pytest.raises(QueueError) as exc:
        queue.enqueue_report_task(request_id="r12", task_input=_task_input("T12"), source="scheduled")
    assert exc.value.code == "QUEUE_FULL"


def test_manual_priority_does_not_interrupt_running() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queue.enqueue_report_task(request_id="r2", task_input=_task_input("MSFT"), source="scheduled")
    queue.enqueue_report_task(request_id="r3", task_input=_task_input("TSLA"), source="manual")
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"]["instrumentCode"] == "AAPL"
    queued = snapshot["queuedTasks"]
    assert queued[0]["instrumentCode"] == "TSLA"
    assert queued[1]["instrumentCode"] == "MSFT"


def test_duplicate_task_reuses_existing_queued_task() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    first = queue.enqueue_report_task(request_id="r2", task_input=_task_input("TSLA"), source="scheduled")
    second = queue.enqueue_report_task(request_id="r3", task_input=_task_input("TSLA"), source="manual")
    assert first["task"]["taskId"] == second["task"]["taskId"]
    assert second["deduped"] is True


def test_running_snapshot_includes_workflow_progress() -> None:
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="risk_debate_running", completed_workers=("market_analyst",))
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("BTC", "CRYPTO", "CRYPTO"), source="manual")

    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"]["progress"]["stageLabel"] == "风险辩论中"
    assert snapshot["runningTask"]["progress"]["percent"] == 86
    assert "市场分析师" in snapshot["runningTask"]["progress"]["completedRoleLabels"]


def test_running_snapshot_derives_worker_status_from_run_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_call_result(run_dir, "frontline", "market_analyst", "succeeded")
    _write_call_result(run_dir, "portfolio_decision", "portfolio_manager", "succeeded")
    _write_call(run_dir, "final_report", "report_polisher")
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="final_report_running", completed_workers=(), run_dir=run_dir)
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("BTC", "CRYPTO", "CRYPTO"), source="manual")

    progress = queue.get_report_queue_snapshot_for_user()["runningTask"]["progress"]
    assert progress["roleLabel"] == "报告整理员"
    assert "市场分析师" in progress["completedRoleLabels"]
    assert "投资组合经理" in progress["completedRoleLabels"]
    assert "市场分析师" not in progress["waitingRoleLabels"]
    assert "政策分析师" not in str(progress)
    assert "游资资金跟踪员" not in str(progress)
    assert "限售筹码观察员" not in str(progress)
    assert "报告整理员：执行中" in progress["workerStatusLabels"]
    assert "投资组合经理：已完成" in progress["workerStatusLabels"]


def test_failed_snapshot_uses_tool_call_error_from_collect_first_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    _write_collect_first_tool_failure(run_dir)
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="failed", run_dir=run_dir)
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("BTC", "CRYPTO", "CRYPTO"), source="manual")

    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"] is None
    terminal = snapshot["lastTerminalTask"]
    assert terminal["status"] == "failed"
    assert terminal["failure"]["message"] == "报告数据请求超时，请稍后重试。"
    assert "助手服务暂不可用" not in terminal["failure"]["message"]


def test_completed_workflow_marks_task_succeeded_and_calls_writer() -> None:
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="completed")
    saved: list[str] = []
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        completed_report_writer=lambda task, _state: saved.append(task.task_id),
    )

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")

    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"] is None
    assert saved == ["task-1"]
    assert queue.get_task_for_testing("task-1").status.value == "succeeded"


def test_completed_workflow_records_balance_delta_cost_estimate() -> None:
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="completed")
    snapshots = iter(
        [
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("20.00")),
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("19.42")),
        ]
    )
    writer_costs: list[Decimal | None] = []
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        completed_report_writer=lambda task, _state: writer_costs.append(
            task.cost_estimate.estimated_cost if task.cost_estimate else None
        ),
        task_cost_snapshot_provider=lambda: next(snapshots),
    )

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queue.get_report_queue_snapshot_for_user()

    task = queue.get_task_for_testing("task-1")
    assert task is not None
    assert task.cost_estimate is not None
    assert task.cost_estimate.estimated_cost == Decimal("0.58")
    assert writer_costs == [Decimal("0.58")]


def test_completed_workflow_records_token_cost_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    call_dir = run_dir / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    (call_dir / "openclaw-result.json").write_text(
        '{"provider":"deepseek","model":"deepseek-chat","usage":{"cacheRead":1000000,"input":1000000,"output":1000000}}\n',
        encoding="utf-8",
    )
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="completed", run_dir=run_dir)
    writer_costs: list[Decimal | None] = []
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        completed_report_writer=lambda task, _state: writer_costs.append(
            task.cost_estimate.token_summary.total_cost
            if task.cost_estimate and task.cost_estimate.token_summary
            else None
        ),
    )

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queue.get_report_queue_snapshot_for_user()

    task = queue.get_task_for_testing("task-1")
    assert task is not None
    assert task.cost_estimate is not None
    assert task.cost_estimate.token_summary is not None
    assert task.cost_estimate.token_summary.total_tokens == 3_000_000
    assert writer_costs == [Decimal("3.02")]


def test_completed_workflow_records_missing_token_usage_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    runner = _FakeRunner()
    runner.state = _FakeWorkflowState(status="completed", run_dir=run_dir)
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queue.get_report_queue_snapshot_for_user()

    task = queue.get_task_for_testing("task-1")
    assert task is not None
    assert task.cost_estimate is not None
    assert task.cost_estimate.token_summary is not None
    assert task.cost_estimate.token_summary.reason == "usage_file_missing"


def test_failed_report_writer_is_called_after_failure() -> None:
    runner = _FakeRunner()
    notified: list[str] = []
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        failed_report_writer=lambda task: notified.append(task.task_id),
    )
    payload = queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")

    queue.handle_report_failed(payload["task"]["taskId"], RuntimeError("workflow failed"))

    assert notified == [payload["task"]["taskId"]]


def _write_collect_first_tool_failure(run_dir: Path) -> None:
    tool_calls_path = run_dir / "calls" / "call-1" / "tool-calls.json"
    tool_calls_path.parent.mkdir(parents=True, exist_ok=True)
    tool_calls_path.write_text(
        json.dumps(
            {
                "source": "model_tool_events",
                "status": "recorded",
                "calls": [
                    {
                        "tool_call_id": "call_09",
                        "tool_name": "claw_request_data",
                        "action": "invoke",
                        "status": "error",
                        "error": "数据工具执行超时",
                        "result_sha256": "s" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "collect-first-frontline-t00.json").write_text(
        json.dumps(
            {
                "collect_first_compliance": {
                    "failures_collected": [
                        {
                            "worker_id": "market_analyst",
                            "category": "tool_calls",
                            "reason": "CRYPTO frontline worker 必需数据工具调用失败: claw_request_data",
                            "evidence_paths": [str(tool_calls_path)],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _write_call_result(run_dir: Path, stage: str, worker_id: str, status: str) -> None:
    call_dir = _write_call(run_dir, stage, worker_id)
    (call_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "call_id": call_dir.name,
                "worker_id": worker_id,
                "stage": stage,
                "status": status,
            }
        ),
        encoding="utf-8",
    )


def _write_call(run_dir: Path, stage: str, worker_id: str) -> Path:
    call_id = f"run-1-{stage}-t00-{worker_id}-20260520T120000000000Z-test"
    call_dir = run_dir / "calls" / call_id
    call_dir.mkdir(parents=True, exist_ok=True)
    (call_dir / "call.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "call_id": call_id,
                "worker_id": worker_id,
                "stage": stage,
            }
        ),
        encoding="utf-8",
    )
    return call_dir
