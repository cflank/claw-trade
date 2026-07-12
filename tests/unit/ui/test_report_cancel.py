from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

import pytest
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.task_costs import TaskCostSnapshot
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@contextmanager
def _test_report_lock(path: Path):  # type: ignore[no-untyped-def]
    fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


@pytest.fixture(autouse=True)
def _inject_report_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("claw_trade.ui_backend.report_queue._open_report_lock", _test_report_lock)
    monkeypatch.setattr("claw_trade.production.paths.REPORT_ACTIVE_LOCK_PATH", tmp_path / "report-active.lock")


@dataclass
class _FakeState:
    status: str = "frontline_running"


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled_runs: list[str] = []
        self.state = _FakeState()

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeState:
        return self.state

    def cancel_run(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        self.state = _FakeState(status="cancelled")
        return True

    def run_has_exited(self, run_id: str) -> bool:
        return True


class _StillRunningAfterCancelRunner(_FakeRunner):
    def cancel_run(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        return False


class _CancelledStateRunner(_StillRunningAfterCancelRunner):
    exited = False

    def load_state(self, run_id: str) -> _FakeState:
        return _FakeState(status="cancelled")

    def run_has_exited(self, run_id: str) -> bool:
        return self.exited


class _WaitableCancelledStateRunner(_CancelledStateRunner):
    def __init__(self) -> None:
        super().__init__()
        self.exit_event = Event()
        self.wait_started = Event()
        self.wait_calls = 0
        self.load_failures_remaining = 0

    def load_state(self, run_id: str) -> _FakeState:
        if self.load_failures_remaining:
            self.load_failures_remaining -= 1
            raise OSError("transient state read failure")
        return _FakeState(status="cancelled")

    def run_has_exited(self, run_id: str) -> bool:
        return self.exit_event.is_set()

    def wait_run_exit(self, run_id: str) -> bool:
        self.wait_calls += 1
        self.wait_started.set()
        self.exit_event.wait()
        return True


class _CompletesDuringCancelRunner(_FakeRunner):
    def cancel_run(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        self.state = _FakeState(status="completed")
        return True


def _task_input(code: str) -> dict[str, object]:
    return {
        "instrumentCode": code,
        "instrumentName": code,
        "market": "US",
        "companyName": code,
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


def test_cancel_queued_task_succeeds() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    queued = queue.enqueue_report_task(request_id="r2", task_input=_task_input("TSLA"), source="manual")
    cancelled = queue.cancel_report_task(request_id="c1", task_id=queued["task"]["taskId"])
    assert cancelled["task"]["status"] == "cancelled"
    assert cancelled["message"] == (
        "已取消排队任务。\n"
        "费用统计：Token 总数：0；任务前余额：未知；任务后余额：未知；本次消费：¥0"
    )


def test_cancel_running_task_stops_workflow_and_hides_active_task() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    running = queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")
    task_id = running["task"]["taskId"]
    cancelled = queue.cancel_report_task(request_id="c2", task_id=task_id)

    assert cancelled["task"]["status"] == "cancelled"
    assert cancelled["queueSnapshot"]["runningTask"] is None
    assert cancelled["message"] == "已停止报告任务。"
    assert runner.cancelled_runs == ["run-1"]
    task = queue.get_task_for_testing(task_id)
    assert task is not None
    assert task.status.value == "cancelled"
    assert task_id not in queue.right_rail_active_task_ids()


def test_cancel_race_preserves_naturally_completed_report() -> None:
    runner = _CompletesDuringCancelRunner()
    saved: list[str] = []
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        completed_report_writer=lambda task, _state: saved.append(task.task_id),
    )
    running = queue.enqueue_report_task(request_id="r-race", task_input=_task_input("AAPL"), source="manual")

    result = queue.cancel_report_task(request_id="c-race", task_id=running["task"]["taskId"])

    assert result["task"]["status"] == "succeeded"
    assert result["message"] == "报告已在取消完成前生成。"
    assert saved == [running["task"]["taskId"]]


def test_cancel_running_task_keeps_running_and_locked_until_thread_exits(tmp_path: Path) -> None:
    runner = _StillRunningAfterCancelRunner()
    lock_path = tmp_path / "report-active-running.lock"
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        report_lock_path=lock_path,
        report_lock_opener=_test_report_lock,
    )
    running = queue.enqueue_report_task(request_id="r-running", task_input=_task_input("AAPL"), source="manual")
    queue.enqueue_report_task(request_id="r-next", task_input=_task_input("TSLA"), source="manual")

    with pytest.raises(Exception, match="仍在停止中"):
        queue.cancel_report_task(request_id="c-running", task_id=running["task"]["taskId"])

    task = queue.get_task_for_testing(running["task"]["taskId"])
    assert task is not None
    assert task.status.value == "running"
    assert runner.calls == 1
    fd = os.open(lock_path, os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_cancelled_workflow_state_releases_only_after_thread_exit(tmp_path: Path) -> None:
    runner = _CancelledStateRunner()
    lock_path = tmp_path / "cancelled-report-active.lock"
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        report_lock_path=lock_path,
        report_lock_opener=_test_report_lock,
    )
    running = queue.enqueue_report_task(
        request_id="r-cancelled-state",
        task_input=_task_input("AAPL"),
        source="manual",
    )

    assert queue.get_report_queue_snapshot_for_user()["runningTask"] is not None
    runner.exited = True
    snapshot = queue.get_report_queue_snapshot_for_user()

    assert snapshot["runningTask"] is None
    assert snapshot["lastTerminalTask"]["status"] == "cancelled"
    assert queue.get_task_for_testing(running["task"]["taskId"]).status.value == "cancelled"
    fd = os.open(lock_path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(fd)


def test_cancelled_workflow_releases_after_thread_exit_without_snapshot_poll(tmp_path: Path) -> None:
    runner = _WaitableCancelledStateRunner()
    lock_path = tmp_path / "cancelled-report-active-watcher.lock"
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        report_lock_path=lock_path,
        report_lock_opener=_test_report_lock,
    )
    running = queue.enqueue_report_task(
        request_id="r-cancelled-watcher",
        task_input=_task_input("AAPL"),
        source="manual",
    )
    original_refresh = queue.refresh_running_task_status
    refresh_failures_remaining = 1

    def flaky_refresh():  # type: ignore[no-untyped-def]
        nonlocal refresh_failures_remaining
        if refresh_failures_remaining:
            refresh_failures_remaining -= 1
            raise OSError("transient refresh failure")
        return original_refresh()

    queue.refresh_running_task_status = flaky_refresh  # type: ignore[method-assign]
    with pytest.raises(Exception, match="仍在停止中"):
        queue.cancel_report_task(request_id="c-cancelled-watcher", task_id=running["task"]["taskId"])
    assert runner.wait_started.wait(timeout=1)
    with pytest.raises(Exception, match="仍在停止中"):
        queue.cancel_report_task(request_id="c-cancelled-watcher-2", task_id=running["task"]["taskId"])
    assert runner.wait_calls == 1
    runner.load_failures_remaining = 1

    fd = os.open(lock_path, os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)

    runner.exit_event.set()
    deadline = time.monotonic() + 2
    while queue.get_task_for_testing(running["task"]["taskId"]).status.value != "cancelled":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    while True:
        fd = os.open(lock_path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        finally:
            os.close(fd)
    assert runner.calls == 1


def test_concurrent_cancelled_refresh_releases_once_and_starts_next_once(tmp_path: Path) -> None:
    runner = _CancelledStateRunner()
    finish_entered = Event()
    finish_release = Event()
    finish_calls: list[str] = []
    release_count: list[int] = []
    lock_path = tmp_path / "concurrent-cancelled.lock"

    @contextmanager
    def counting_lock(path: Path):  # type: ignore[no-untyped-def]
        with _test_report_lock(path) as fd:
            try:
                yield fd
            finally:
                release_count.append(1)

    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        report_lock_path=lock_path,
        report_lock_opener=counting_lock,
    )
    first = queue.enqueue_report_task(request_id="cancel-first", task_input=_task_input("AAPL"), source="manual")
    second = queue.enqueue_report_task(request_id="cancel-second", task_input=_task_input("TSLA"), source="manual")

    def finish_cost(task, *, run_dir=None):  # type: ignore[no-untyped-def]
        finish_calls.append(task.task_id)
        finish_entered.set()
        finish_release.wait()

    queue._finish_task_cost_estimate = finish_cost  # type: ignore[method-assign]  # noqa: SLF001
    runner.exited = True
    first_refresh = Thread(target=queue.refresh_running_task_status)
    first_refresh.start()
    assert finish_entered.wait(timeout=1)

    queue.refresh_running_task_status()
    assert release_count == []
    assert runner.calls == 1

    finish_release.set()
    first_refresh.join(timeout=1)
    assert not first_refresh.is_alive()
    assert finish_calls == [first["task"]["taskId"]]
    assert release_count == [1]
    assert runner.calls == 2
    assert queue.get_task_for_testing(first["task"]["taskId"]).status.value == "cancelled"
    assert queue.get_task_for_testing(second["task"]["taskId"]).status.value == "running"


def test_cancel_running_task_appends_cost_estimate_when_available() -> None:
    runner = _FakeRunner()
    snapshots = iter(
        [
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("10.00")),
            TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("9.99")),
        ]
    )
    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        task_cost_snapshot_provider=lambda: next(snapshots),
    )
    running = queue.enqueue_report_task(request_id="r1", task_input=_task_input("AAPL"), source="manual")

    cancelled = queue.cancel_report_task(request_id="c-cost", task_id=running["task"]["taskId"])

    assert cancelled["message"] == (
        "已停止报告任务。\n"
        "费用统计：Token 总数：未知；任务前余额：¥10.00；任务后余额：¥9.99；本次消费：¥0.01"
    )
