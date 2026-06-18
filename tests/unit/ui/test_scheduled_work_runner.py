from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from claw_trade.ui_backend.scheduler_service import SchedulerService, UiServiceError
from claw_trade.ui_backend.scheduled_work_runner import ScheduledWorkRunner, ScheduledWorkRunnerError
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def test_scheduled_report_wake_dispatches_to_scheduler_queue() -> None:
    queue_calls: list[tuple[dict[str, Any], str]] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        queue_calls.append((task, request_id))
        return {
            "taskId": "task-1",
            "instrumentCode": task["instrumentCode"],
            "instrumentName": task["instrumentName"],
            "market": task["market"],
            "status": "queued",
        }

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {
            "runningTask": None,
            "queuedTasks": [{"taskId": "task-1", "instrumentCode": "AAPL", "market": "US", "status": "queued"}],
            "queuedCount": 1,
            "maxQueueSize": 10,
        },
        store=store,
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="create-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    runner = ScheduledWorkRunner(scheduler_service=service)

    response = runner.handle_wake(
        {
            "kind": "scheduled_report",
            "scheduledReportId": schedule.scheduledReportId,
            "cronRunId": "cron-run-1",
            "requestId": "cron-request-1",
        }
    )

    assert len(queue_calls) == 1
    assert queue_calls[0][1] == "cron-request-1"
    assert queue_calls[0][0]["source"] == "scheduled"
    assert response["status"] == "ok"
    assert response["task"].source == "scheduled"
    assert response["task"].task_id == "task-1"
    saved = store.get_scheduled_report(schedule.scheduledReportId)
    assert saved is not None
    assert saved.state == "active"
    assert saved.last_run_task_id == "task-1"
    assert saved.last_cron_run_id == "cron-run-1"
    assert saved.next_run_at == "2026-05-20T09:30:00Z"


def test_scheduled_report_wake_uses_production_queue_envelope() -> None:
    queue_calls: list[tuple[dict[str, Any], str]] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        queue_calls.append((task, request_id))
        return {
            "task": {
                "taskId": "task-1",
                "instrumentCode": task["instrumentCode"],
                "instrumentName": task["instrumentName"],
                "market": task["market"],
                "status": "queued",
            },
            "deduped": False,
            "queueSnapshot": {
                "runningTask": None,
                "queuedTasks": [
                    {
                        "taskId": "task-1",
                        "instrumentCode": task["instrumentCode"],
                        "instrumentName": task["instrumentName"],
                        "market": task["market"],
                        "status": "queued",
                    }
                ],
                "queuedCount": 1,
                "maxQueueSize": 10,
            },
        }

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {
            "runningTask": None,
            "queuedTasks": [{"taskId": "stale-task", "instrumentCode": "MSFT", "market": "US", "status": "queued"}],
            "queuedCount": 99,
            "maxQueueSize": 100,
        },
        store=store,
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="create-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    response = ScheduledWorkRunner(scheduler_service=service).handle_wake(
        {
            "kind": "scheduled_report",
            "scheduledReportId": schedule.scheduledReportId,
            "cronRunId": "cron-run-1",
            "requestId": "cron-request-1",
        }
    )

    assert queue_calls[0][0]["source"] == "scheduled"
    assert response["task"].task_id == "task-1"
    assert response["queueSnapshot"].queued_count == 1
    assert response["queueSnapshot"].queued_tasks[0].task_id == "task-1"
    saved = store.get_scheduled_report(schedule.scheduledReportId)
    assert saved is not None
    assert saved.last_run_task_id == "task-1"


def test_scheduled_report_wake_rejects_paused_schedule_without_enqueueing() -> None:
    queue_calls: list[tuple[dict[str, Any], str]] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        queue_calls.append((task, request_id))
        return {"taskId": "task-1", "instrumentCode": task["instrumentCode"], "market": task["market"], "status": "queued"}

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [], "queuedCount": 0, "maxQueueSize": 10},
        store=store,
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="create-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    service.pause_scheduled_report(request_id="pause-schedule", scheduled_report_id=schedule.scheduledReportId)

    with pytest.raises(UiServiceError) as exc:
        ScheduledWorkRunner(scheduler_service=service).handle_wake(
            {
                "kind": "scheduled_report",
                "scheduledReportId": schedule.scheduledReportId,
                "cronRunId": "cron-run-1",
                "requestId": "cron-request-1",
            }
        )

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "定时报告已暂停。"
    assert queue_calls == []
    saved = store.get_scheduled_report(schedule.scheduledReportId)
    assert saved is not None
    assert saved.state == "paused"
    assert saved.last_run_task_id is None
    assert saved.last_cron_run_id is None


def test_scheduled_report_wake_derives_request_id_when_missing() -> None:
    request_ids: list[str] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        request_ids.append(request_id)
        return {"taskId": "task-1", "instrumentCode": task["instrumentCode"], "market": task["market"], "status": "queued"}

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [], "queuedCount": 0, "maxQueueSize": 10},
        store=store,
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="create-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    ScheduledWorkRunner(scheduler_service=service).handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-1"}
    )

    assert request_ids == [f"scheduled-report:{schedule.scheduledReportId}:cron-run-1"]


def test_selection_and_maintenance_wakes_fail_closed_without_runner() -> None:
    runner = ScheduledWorkRunner()

    for kind in ("selection_data_refresh", "data_maintenance"):
        with pytest.raises(ScheduledWorkRunnerError) as exc:
            runner.handle_wake({"kind": kind, "cronRunId": "cron-run-1"})

        assert exc.value.code == "INVALID_INPUT"
