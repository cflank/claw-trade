from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Any

import pytest

from claw_trade.ui_backend.scheduler_service import SchedulerService, UiServiceError
from claw_trade.ui_backend.scheduled_work_runner import ScheduledWorkRunner, ScheduledWorkRunnerError
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.selection.models import SelectionMarket, SelectionProfile
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
    assert queue_calls[0][1] == f"scheduled-report:{schedule.scheduledReportId}:2025-05-19:2026-05-19:2026-05-19"
    assert queue_calls[0][0]["source"] == "scheduled"
    assert response["status"] == "ok"
    assert response["task"].source == "scheduled"
    assert response["task"].task_id == "task-1"
    saved = store.get_scheduled_report(schedule.scheduledReportId)
    assert saved is not None
    assert saved.state == "active"
    assert saved.last_run_task_id == "task-1"
    assert saved.last_cron_run_id == "cron-run-1"
    assert saved.next_run_at == "2026-05-19T13:30:00Z"


def test_scheduled_report_wake_dedupes_different_cron_run_ids_for_same_window() -> None:
    queue_calls: list[tuple[dict[str, Any], str]] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        queue_calls.append((task, request_id))
        return {
            "taskId": f"task-{len(queue_calls)}",
            "instrumentCode": task["instrumentCode"],
            "instrumentName": task["instrumentName"],
            "market": task["market"],
            "status": "queued",
        }

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {
            "runningTask": {
                "taskId": "task-1",
                "instrumentCode": "AAPL",
                "instrumentName": "AAPL",
                "market": "US",
                "status": "running",
            },
            "queuedTasks": [],
            "queuedCount": 0,
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

    first = runner.handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-1"}
    )
    second = runner.handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-2"}
    )

    assert len(queue_calls) == 1
    assert queue_calls[0][1] == f"scheduled-report:{schedule.scheduledReportId}:2025-05-19:2026-05-19:2026-05-19"
    assert first["task"].task_id == "task-1"
    assert second["task"].task_id == "task-1"
    assert second["cronRunId"] == "cron-run-2"
    saved = store.get_scheduled_report(schedule.scheduledReportId)
    assert saved is not None
    assert saved.last_run_task_id == "task-1"
    assert saved.last_cron_run_id == "cron-run-1"


def test_scheduled_report_duplicate_wake_without_live_task_does_not_fabricate_task() -> None:
    queue_calls: list[tuple[dict[str, Any], str]] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        queue_calls.append((task, request_id))
        return {
            "taskId": f"task-{len(queue_calls)}",
            "instrumentCode": task["instrumentCode"],
            "instrumentName": task["instrumentName"],
            "market": task["market"],
            "status": "queued",
        }

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
    runner = ScheduledWorkRunner(scheduler_service=service)

    runner.handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-1"}
    )
    response = runner.handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-2"}
    )

    assert len(queue_calls) == 1
    assert response["deduped"] is True
    assert response["skipped"] is True
    assert response["lastRunTaskId"] == "task-1"
    assert "task" not in response


def test_scheduled_report_wake_skips_existing_duplicate_schedule_row() -> None:
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
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [], "queuedCount": 0, "maxQueueSize": 10},
        store=store,
        now_provider=_fixed_now,
    )
    original_dto = service.create_scheduled_report(
        request_id="create-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    original = store.get_scheduled_report(original_dto.scheduledReportId)
    assert original is not None
    store.save_scheduled_report(
        replace(
            original,
            id="schedule-99",
            time_of_day="09:31",
            created_at="2026-05-20T12:00:00Z",
            updated_at="2026-05-20T12:00:00Z",
        )
    )

    response = ScheduledWorkRunner(scheduler_service=service).handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": original_dto.scheduledReportId, "cronRunId": "cron-run-99"}
    )

    assert queue_calls == []
    assert response["deduped"] is True
    assert response["skipped"] is True
    assert response["canonicalScheduledReportId"] == "schedule-99"


def test_scheduled_work_runner_keeps_latest_data_maintenance_evidence() -> None:
    class Runner:
        def run(self, *, market: str, job_kind: str, cron_run_id: str | None, maintenance_job_id: str | None) -> object:
            assert market == "CN_A"
            assert job_kind == "eod"
            assert cron_run_id == "cron-run-1"
            return SimpleNamespace(job_id=maintenance_job_id or "job-1", status="succeeded")

    runner = ScheduledWorkRunner(data_maintenance_runner=Runner())

    response = runner.handle_wake(
        {
            "kind": "data_maintenance",
            "market": "CN_A",
            "jobKind": "eod",
            "cronRunId": "cron-run-1",
            "maintenanceJobId": "job-cn-a-eod",
        }
    )

    latest = runner.latest_results_for_user()["items"]
    assert response["maintenanceJobId"] == "job-cn-a-eod"
    assert latest == [response]


def test_scheduled_report_same_cron_run_id_dedupes_after_next_slot_boundary() -> None:
    queue_calls: list[tuple[dict[str, Any], str]] = []
    store = InMemoryScheduledWorkStore()
    clock = {"now": datetime(2026, 5, 19, 12, 0, tzinfo=UTC)}

    def enqueue(task: dict[str, Any], request_id: str) -> dict[str, Any]:
        queue_calls.append((task, request_id))
        return {
            "taskId": f"task-{len(queue_calls)}",
            "instrumentCode": task["instrumentCode"],
            "instrumentName": task["instrumentName"],
            "market": task["market"],
            "status": "queued",
        }

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [], "queuedCount": 0, "maxQueueSize": 10},
        store=store,
        now_provider=lambda: clock["now"],
    )
    schedule = service.create_scheduled_report(
        request_id="create-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    runner = ScheduledWorkRunner(scheduler_service=service)

    runner.handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-1"}
    )
    clock["now"] = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
    response = runner.handle_wake(
        {"kind": "scheduled_report", "scheduledReportId": schedule.scheduledReportId, "cronRunId": "cron-run-1"}
    )

    assert len(queue_calls) == 1
    assert response["deduped"] is True
    assert response["skipped"] is True
    assert response["lastRunTaskId"] == "task-1"
    assert "task" not in response


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


def test_scheduled_report_wake_uses_stable_schedule_window_request_id_when_missing() -> None:
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

    assert request_ids == [f"scheduled-report:{schedule.scheduledReportId}:2025-05-19:2026-05-19:2026-05-19"]


def test_selection_and_maintenance_wakes_fail_closed_without_runner() -> None:
    runner = ScheduledWorkRunner()

    for kind in ("selection_data_refresh", "data_maintenance"):
        with pytest.raises(ScheduledWorkRunnerError) as exc:
            runner.handle_wake({"kind": kind, "cronRunId": "cron-run-1"})

        assert exc.value.code == "INVALID_INPUT"


class FakeSelectionRefreshRunner:
    def __init__(self, result: dict[str, str] | None = None) -> None:
        self.reasons: list[str] = []
        self.calls: list[dict[str, object]] = []
        self.result = result or {"status": "completed", "runId": "selection-run-1"}

    def run_automatic_refresh_once(
        self,
        *,
        reason: str,
        market: SelectionMarket = SelectionMarket.CN_A,
        profile: SelectionProfile | None = None,
        force_refresh: bool = False,
    ) -> dict[str, str]:
        self.reasons.append(reason)
        self.calls.append(
            {
                "reason": reason,
                "market": market,
                "profile": profile,
                "force_refresh": force_refresh,
            }
        )
        return self.result


def test_selection_data_refresh_wake_dispatches_to_refresh_service() -> None:
    refresh = FakeSelectionRefreshRunner()
    runner = ScheduledWorkRunner(selection_data_refresh_runner=refresh)

    response = runner.handle_wake(
        {"kind": "selection_data_refresh", "reason": "cron_refresh", "cronRunId": "cron-run-1"}
    )

    assert refresh.reasons == ["cron_refresh"]
    assert response["kind"] == "selection_data_refresh"
    assert response["status"] == "completed"
    assert response["result"]["status"] == "completed"
    assert response["reason"] == "cron_refresh"


def test_selection_data_refresh_wake_defaults_reason() -> None:
    refresh = FakeSelectionRefreshRunner()

    ScheduledWorkRunner(selection_data_refresh_runner=refresh).handle_wake({"kind": "selection_data_refresh"})

    assert refresh.reasons == ["scheduled_data_refresh"]


def test_selection_data_refresh_wake_uses_data_maintenance_when_configured() -> None:
    refresh = FakeSelectionRefreshRunner()
    maintenance = FakeDataMaintenanceRunner()
    runner = ScheduledWorkRunner(selection_data_refresh_runner=refresh, data_maintenance_runner=maintenance)

    response = runner.handle_wake({"kind": "selection_data_refresh", "cronRunId": "cron-run-1"})

    assert response["status"] == "ok"
    assert [(call["market"], call["job_kind"]) for call in maintenance.calls] == [
        ("CN_A", "eod"),
        ("CRYPTO", "kline-refresh"),
    ]
    assert [(call["market"], call["profile"], call["force_refresh"]) for call in refresh.calls] == [
        (SelectionMarket.CN_A, SelectionProfile.CN_A, False),
        (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO, False),
    ]


def test_selection_data_refresh_wake_reports_selection_failure_after_data_maintenance() -> None:
    refresh = FakeSelectionRefreshRunner(
        {
            "status": "failed",
            "selection_run_id": "sel-failed",
            "reason": "data_maintenance_completed:trade_date_resolution_failed",
            "error_code": "RuntimeError",
        }
    )
    maintenance = FakeDataMaintenanceRunner()
    runner = ScheduledWorkRunner(selection_data_refresh_runner=refresh, data_maintenance_runner=maintenance)

    with pytest.raises(ScheduledWorkRunnerError) as exc:
        runner.handle_wake({"kind": "selection_data_refresh", "cronRunId": "cron-run-1"})

    assert exc.value.code == "SELECTION_DATA_REFRESH_FAILED"
    latest = runner.latest_results_for_user()["items"][-1]
    assert latest["kind"] == "selection_data_refresh"
    assert latest["status"] == "error"
    assert latest["errors"][0]["code"] == "SELECTION_DATA_REFRESH_FAILED"


def test_selection_data_refresh_wake_preserves_already_running_status() -> None:
    refresh = FakeSelectionRefreshRunner(
        {"status": "already_running", "selection_run_id": "selection-run-1", "reason": "scheduled_data_refresh"}
    )

    response = ScheduledWorkRunner(selection_data_refresh_runner=refresh).handle_wake(
        {"kind": "selection_data_refresh", "reason": "scheduled_data_refresh"}
    )

    assert response["status"] == "already_running"
    assert response["result"]["status"] == "already_running"


def test_selection_data_refresh_wake_failed_result_raises_explicit_error() -> None:
    refresh = FakeSelectionRefreshRunner(
        {
            "status": "failed",
            "selection_run_id": "",
            "reason": "scheduled_data_refresh:trade_date_resolution_failed",
            "error_code": "RuntimeError",
        }
    )

    with pytest.raises(ScheduledWorkRunnerError) as exc:
        ScheduledWorkRunner(selection_data_refresh_runner=refresh).handle_wake({"kind": "selection_data_refresh"})

    assert exc.value.code == "SELECTION_DATA_REFRESH_FAILED"
    assert exc.value.message == (
        "selection_data_refresh failed: RuntimeError (scheduled_data_refresh:trade_date_resolution_failed)"
    )


def test_selection_data_refresh_blocks_when_license_denied() -> None:
    def deny() -> None:
        raise PermissionError("设备授权已失效，请在授权页修复后重试。")

    refresh = FakeSelectionRefreshRunner()
    runner = ScheduledWorkRunner(selection_data_refresh_runner=refresh, data_refresh_permission_checker=deny)

    with pytest.raises(ScheduledWorkRunnerError, match="设备授权已失效") as exc:
        runner.handle_wake({"kind": "selection_data_refresh", "reason": "scheduled_data_refresh"})

    assert exc.value.code == "LICENSE_BLOCKED"
    assert refresh.reasons == []


class FakeDataMaintenanceRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.job = type("Job", (), {"job_id": "job-1", "status": "succeeded", "error": None, "cursor": {}, "stats": {}})()

    def run(
        self,
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None,
        maintenance_job_id: str | None,
    ) -> object:
        self.calls.append(
            {
                "market": market,
                "job_kind": job_kind,
                "cron_run_id": cron_run_id,
                "maintenance_job_id": maintenance_job_id,
            }
        )
        self.job.job_id = maintenance_job_id or "job-1"
        return self.job


def test_data_maintenance_wake_dispatches_to_runner_with_payload_fields() -> None:
    maintenance = FakeDataMaintenanceRunner()
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance)

    response = runner.handle_wake(
        {
            "kind": "data_maintenance",
            "market": "CRYPTO",
            "jobKind": "kline-refresh",
            "cronRunId": "cron-run-1",
            "maintenanceJobId": "job-crypto",
        }
    )

    assert maintenance.calls == [
        {
            "market": "CRYPTO",
            "job_kind": "kline-refresh",
            "cron_run_id": "cron-run-1",
            "maintenance_job_id": "job-crypto",
        }
    ]
    assert response["status"] == "ok"
    assert response["maintenanceJobId"] == "job-crypto"


def test_data_maintenance_wake_refreshes_selection_cache_after_raw_data_update() -> None:
    maintenance = FakeDataMaintenanceRunner()
    maintenance.job.cursor = {"input_total": 1}
    maintenance.job.stats = {"dataset_refs": 1, "remote_success": 1}
    refresh = FakeSelectionRefreshRunner()
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance, selection_data_refresh_runner=refresh)

    response = runner.handle_wake(
        {
            "kind": "data_maintenance",
            "market": "CRYPTO",
            "jobKind": "kline-refresh",
            "cronRunId": "cron-run-1",
        }
    )

    assert refresh.calls == [
        {
            "reason": "data_maintenance_completed",
            "market": SelectionMarket.CRYPTO,
            "profile": SelectionProfile.CRYPTO,
            "force_refresh": True,
        }
    ]
    assert response["selectionRefresh"]["status"] == "completed"
    assert response["inputTotal"] == 1


def test_data_maintenance_wake_forces_selection_refresh_when_dataset_refs_are_produced() -> None:
    maintenance = FakeDataMaintenanceRunner()
    maintenance.job.cursor = {"input_total": 1}
    maintenance.job.stats = {"dataset_refs": 1}
    refresh = FakeSelectionRefreshRunner()
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance, selection_data_refresh_runner=refresh)

    runner.handle_wake({"kind": "data_maintenance", "market": "CN_A", "jobKind": "eod"})

    assert refresh.calls == [
        {
            "reason": "data_maintenance_completed",
            "market": SelectionMarket.CN_A,
            "profile": SelectionProfile.CN_A,
            "force_refresh": True,
        }
    ]


def test_data_maintenance_wake_reports_selection_refresh_failure_separately() -> None:
    maintenance = FakeDataMaintenanceRunner()
    maintenance.job.cursor = {"input_total": 1}
    maintenance.job.stats = {"remote_success": 1}
    refresh = FakeSelectionRefreshRunner(
        {
            "status": "failed",
            "selection_run_id": "sel-failed",
            "reason": "data_maintenance_completed:trade_date_resolution_failed",
            "error_code": "RuntimeError",
        }
    )
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance, selection_data_refresh_runner=refresh)

    with pytest.raises(ScheduledWorkRunnerError) as exc:
        runner.handle_wake({"kind": "data_maintenance", "market": "CN_A", "jobKind": "eod"})

    assert exc.value.code == "SELECTION_DATA_REFRESH_FAILED"
    assert "RuntimeError" in exc.value.message
    latest = runner.latest_results_for_user()["items"][0]
    assert latest["maintenanceStatus"] == "succeeded"
    assert latest["status"] == "selection_refresh_failed"
    assert latest["selectionRefresh"]["status"] == "failed"


def test_data_maintenance_wake_checks_selection_cache_after_noop_raw_data_run() -> None:
    maintenance = FakeDataMaintenanceRunner()
    maintenance.job.cursor = {"input_total": 0}
    refresh = FakeSelectionRefreshRunner()
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance, selection_data_refresh_runner=refresh)

    runner.handle_wake({"kind": "data_maintenance", "market": "CN_A", "jobKind": "eod"})

    assert refresh.calls == [
        {
            "reason": "data_maintenance_completed",
            "market": SelectionMarket.CN_A,
            "profile": SelectionProfile.CN_A,
            "force_refresh": False,
        }
    ]


def test_data_maintenance_same_market_job_runs_serially() -> None:
    maintenance = BlockingDataMaintenanceRunner()
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance)
    responses: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def call_runner(cron_run_id: str) -> None:
        try:
            responses.append(
                runner.handle_wake(
                    {
                        "kind": "data_maintenance",
                        "market": "CRYPTO",
                        "jobKind": "kline-refresh",
                        "cronRunId": cron_run_id,
                    }
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = Thread(target=call_runner, args=("startup-1",))
    second = Thread(target=call_runner, args=("startup-2",))
    first.start()
    assert maintenance.first_entered.wait(timeout=1.0)

    second.start()
    assert not maintenance.second_entered.wait(timeout=0.1)
    maintenance.release_first.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(responses) == 2
    assert maintenance.max_active == 1
    assert [call["cron_run_id"] for call in maintenance.calls] == ["startup-1", "startup-2"]


def test_data_maintenance_blocks_when_license_denied() -> None:
    def deny() -> None:
        raise PermissionError("设备授权已失效，请在授权页修复后重试。")

    maintenance = FakeDataMaintenanceRunner()
    runner = ScheduledWorkRunner(data_maintenance_runner=maintenance, data_refresh_permission_checker=deny)

    with pytest.raises(ScheduledWorkRunnerError, match="设备授权已失效") as exc:
        runner.handle_wake({"kind": "data_maintenance", "market": "CN_A", "jobKind": "kline-refresh"})

    assert exc.value.code == "LICENSE_BLOCKED"
    assert maintenance.calls == []


def test_data_maintenance_failures_preserve_error_code() -> None:
    class FailingDataMaintenanceRunner:
        def run(self, **_kwargs: object) -> object:
            raise ValueError("CRYPTO daily_bar maintenance scope is empty in data layer")

    runner = ScheduledWorkRunner(data_maintenance_runner=FailingDataMaintenanceRunner())

    with pytest.raises(ScheduledWorkRunnerError) as exc:
        runner.handle_wake({"kind": "data_maintenance", "market": "CRYPTO", "jobKind": "kline-refresh"})

    assert exc.value.code == "DATA_MAINTENANCE_FAILED"
    assert exc.value.message == "CRYPTO daily_bar maintenance scope is empty in data layer"


class BlockingDataMaintenanceRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.first_entered = Event()
        self.second_entered = Event()
        self.release_first = Event()
        self.max_active = 0
        self._active = 0
        self._lock = Lock()

    def run(
        self,
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None,
        maintenance_job_id: str | None,
    ) -> object:
        with self._lock:
            call_index = len(self.calls)
            self.calls.append(
                {
                    "market": market,
                    "job_kind": job_kind,
                    "cron_run_id": cron_run_id,
                    "maintenance_job_id": maintenance_job_id,
                }
            )
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            if call_index == 0:
                self.first_entered.set()
            else:
                self.second_entered.set()
        try:
            if call_index == 0:
                assert self.release_first.wait(timeout=1.0)
            return type("Job", (), {"job_id": maintenance_job_id or f"job-{cron_run_id}", "status": "succeeded", "error": None})()
        finally:
            with self._lock:
                self._active -= 1
