from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claw_trade.ui_backend.scheduler_service import SchedulerService, UiServiceError
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import to_user_payload


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def test_create_scheduled_report_supports_daily_and_weekly_with_request_id_idempotency() -> None:
    enqueue_calls: list[tuple[dict[str, object], str]] = []

    def enqueue(task: dict[str, object], request_id: str) -> dict[str, object]:
        enqueue_calls.append((task, request_id))
        return {"taskId": "task-1", "instrumentCode": "AAPL", "market": "US", "status": "queued"}

    service = SchedulerService(enqueue_report_task=enqueue, now_provider=_fixed_now)
    daily = service.create_scheduled_report(
        request_id="req-daily",
        instrument_code="aapl",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
        notification={"channel": "in_app", "enabled": True},
    )
    assert daily.scheduledReportId == "schedule-1"
    assert daily.instrumentCode == "AAPL"
    assert daily.timeOfDay == "09:30"
    payload = to_user_payload(daily)

    duplicate = service.create_scheduled_report(
        request_id="req-daily",
        instrument_code="msft",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="10:00",
    )
    assert duplicate.scheduledReportId == daily.scheduledReportId
    assert duplicate.instrumentCode == "AAPL"
    assert not enqueue_calls

    weekly = service.create_scheduled_report(
        request_id="req-weekly",
        instrument_code="tsla",
        market=MarketProfile.US,
        frequency="weekly",
        time_of_day="08:15",
        weekday=2,
    )
    assert weekly.scheduledReportId == "schedule-2"
    assert weekly.weekday == 2


def test_create_scheduled_report_rejects_hourly() -> None:
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {})
    with pytest.raises(UiServiceError, match="每天或每周"):
        service.create_scheduled_report(
            request_id="req-hourly",
            instrument_code="BTC",
            market=MarketProfile.CRYPTO,
            frequency="hourly",
            time_of_day="10:00",
        )


def test_pause_resume_delete_state_transitions() -> None:
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {})
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="600519",
        market=MarketProfile.CN_A,
        frequency="daily",
        time_of_day="08:00",
    )
    paused = service.pause_scheduled_report(request_id="req-pause", scheduled_report_id=created.scheduledReportId)
    assert paused.state == "paused"

    resumed = service.resume_scheduled_report(request_id="req-resume", scheduled_report_id=created.scheduledReportId)
    assert resumed.state == "active"
    assert resumed.nextRunAt is not None

    deleted = service.delete_scheduled_report(request_id="req-delete", scheduled_report_id=created.scheduledReportId)
    assert deleted == {"deleted": True, "scheduledReportId": created.scheduledReportId}

    with pytest.raises(UiServiceError) as exc:
        service.pause_scheduled_report(request_id="req-pause-2", scheduled_report_id=created.scheduledReportId)
    assert exc.value.code == "SCHEDULE_NOT_FOUND"


def test_tick_scheduled_reports_skips_paused_and_deleted_items() -> None:
    queue_calls: list[tuple[str, dict[str, object]]] = []

    def enqueue(task: dict[str, object], request_id: str) -> dict[str, object]:
        queue_calls.append((request_id, task))
        return {"taskId": f"task-{len(queue_calls)}", "instrumentCode": task["instrumentCode"], "market": "US", "status": "queued"}

    service = SchedulerService(enqueue_report_task=enqueue, now_provider=_fixed_now)
    active = service.create_scheduled_report(
        request_id="req-active",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    paused = service.create_scheduled_report(
        request_id="req-paused",
        instrument_code="MSFT",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    deleted = service.create_scheduled_report(
        request_id="req-deleted",
        instrument_code="TSLA",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    service.pause_scheduled_report(request_id="req-paused-state", scheduled_report_id=paused.scheduledReportId)
    service.delete_scheduled_report(request_id="req-deleted-state", scheduled_report_id=deleted.scheduledReportId)

    service._items[active.scheduledReportId].next_run_at = "2026-05-19T11:59:00Z"
    service._items[paused.scheduledReportId].next_run_at = "2026-05-19T11:59:00Z"
    result = service.tick_scheduled_reports(now="2026-05-19T12:00:00Z")

    assert len(queue_calls) == 1
    _, queued_task = queue_calls[0]
    assert queued_task["startDate"] == "2026-05-19"
    assert queued_task["endDate"] == "2026-05-19"
    assert queued_task["currentDate"] == "2026-05-19"
    assert queued_task["workflowSettings"]["defaultProfile"] == "US"
    assert result["results"][0].scheduledReportId == active.scheduledReportId
    assert result["results"][0].enqueued is True
