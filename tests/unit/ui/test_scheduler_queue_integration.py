from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

import pytest
from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.scheduler_service import SchedulerService, UiServiceError
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


class _FakeCronGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        self.calls.append({"method": "cron.add", "params": payload})
        return {"jobId": payload["name"]}

    def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "cron.run", "params": {"jobId": job_id, "idempotencyKey": idempotency_key}})
        return {"runId": "cron-run-1"}


def test_run_scheduled_report_now_goes_through_queue_and_is_idempotent() -> None:
    queue_calls: list[tuple[dict[str, object], str]] = []

    def enqueue(task: dict[str, object], request_id: str) -> dict[str, object]:
        queue_calls.append((task, request_id))
        return {
            "taskId": "task-1",
            "instrumentCode": task["instrumentCode"],
            "instrumentName": task.get("instrumentName"),
            "market": task["market"],
            "status": "queued",
        }

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [{"taskId": "task-1", "instrumentCode": "AAPL", "market": "US", "status": "queued"}], "queuedCount": 1, "maxQueueSize": 10},
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    first = service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=schedule.scheduledReportId)
    second = service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=schedule.scheduledReportId)

    assert len(queue_calls) == 1
    assert queue_calls[0][0]["source"] == "scheduled"
    assert queue_calls[0][0]["startDate"] == "2026-05-19"
    assert queue_calls[0][0]["endDate"] == "2026-05-19"
    assert queue_calls[0][0]["currentDate"] == "2026-05-19"
    assert queue_calls[0][0]["workflowSettings"]["defaultProfile"] == "US"
    assert first["task"].task_id == "task-1"
    assert first["queueSnapshot"].queued_count == 1
    assert second["task"].task_id == first["task"].task_id


def test_run_scheduled_report_now_in_cron_mode_does_not_enqueue_or_return_fake_task() -> None:
    queue_calls: list[tuple[dict[str, object], str]] = []
    gateway = _FakeCronGateway()

    def enqueue(task: dict[str, object], request_id: str) -> dict[str, object]:
        queue_calls.append((task, request_id))
        return {"taskId": "task-1", "instrumentCode": "AAPL", "market": "US", "status": "queued"}

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [], "queuedCount": 0, "maxQueueSize": 10},
        cron_adapter=OpenClawCronAdapter(gateway),
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    result = service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=schedule.scheduledReportId)

    assert queue_calls == []
    assert [call["method"] for call in gateway.calls] == ["cron.add", "cron.run"]
    assert "task" not in result
    assert result["scheduledReport"].scheduledReportId == schedule.scheduledReportId
    assert result["triggered"] is True
    assert result["cronRunId"] == "cron-run-1"
    assert result["queueSnapshot"].queued_count == 0


def test_tick_scheduled_reports_handles_queue_failures_without_bypassing_queue_boundary() -> None:
    queue_calls = 0

    def enqueue(_task: dict[str, object], _request_id: str) -> dict[str, object]:
        nonlocal queue_calls
        queue_calls += 1
        raise UiServiceError("QUEUE_FULL", "报告队列已满，请稍后再试。")

    store = InMemoryScheduledWorkStore()
    service = SchedulerService(
        enqueue_report_task=enqueue,
        now_provider=_fixed_now,
        store=store,
        allow_local_tick_for_tests=True,
    )
    schedule = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="09:30",
    )
    report = store.get_scheduled_report(schedule.scheduledReportId)
    assert report is not None
    report.next_run_at = "2026-05-19T11:59:00Z"
    store.save_scheduled_report(report)

    result = service.tick_scheduled_reports(now="2026-05-19T12:00:00Z")

    assert queue_calls == 1
    assert result["results"][0].enqueued is False
    assert result["results"][0].message == "定时报告暂未进入队列。"
    assert service.get_scheduled_report(schedule.scheduledReportId).state == "active"
