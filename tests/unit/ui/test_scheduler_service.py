from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest
from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.scheduler_service import SchedulerService, UiServiceError
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import to_user_payload


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


class _FakeCronGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        self.calls.append({"method": "cron.add", "params": payload})
        return {"jobId": payload["name"]}

    def cron_update(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "cron.update", "params": dict(params)})
        return {"updated": True}

    def cron_remove(self, *, job_id: str) -> dict[str, Any]:
        self.calls.append({"method": "cron.remove", "params": {"jobId": job_id}})
        return {"removed": True}

    def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "cron.run", "params": {"jobId": job_id, "idempotencyKey": idempotency_key}})
        return {"runId": "cron-run-1"}

    def cron_list(self, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append({"method": "cron.list", "params": dict(params or {})})
        return []

    def cron_status(self, *, job_id: str) -> dict[str, Any]:
        self.calls.append({"method": "cron.status", "params": {"jobId": job_id}})
        return {"jobId": job_id, "status": "active"}

    def cron_runs(self, *, job_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"jobId": job_id}
        if limit is not None:
            params["limit"] = limit
        self.calls.append({"method": "cron.runs", "params": params})
        return []


def test_create_scheduled_report_registers_openclaw_cron_job() -> None:
    store = InMemoryScheduledWorkStore()
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )

    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    assert created.scheduledReportId == "schedule-1"
    assert fake_gateway.calls[0]["method"] == "cron.add"
    params = fake_gateway.calls[0]["params"]
    assert params["agentId"] == "scheduled_report_runner"
    assert params["schedule"] == {"kind": "cron", "expr": "30 9 * * *", "tz": "America/New_York", "staggerMs": 0}
    assert params["payload"] == {
        "kind": "toolCall",
        "toolName": "claw-trade-scheduled-work-wake",
        "input": {
            "kind": "scheduled_report",
            "scheduledReportId": "schedule-1",
            "cronRunId": "auto",
        },
    }
    saved = store.get_scheduled_report("schedule-1")
    assert saved is not None
    assert saved.openclaw_cron_job_id == "scheduled-report:schedule-1"


def test_create_scheduled_crypto_selection_registers_beijing_cron_job() -> None:
    store = InMemoryScheduledWorkStore()
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )

    created = service.create_scheduled_selection_for_user(
        request_id="req-create-selection",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="08:00",
        notification={"channel": "wechat_clawbot", "enabled": True},
    )["scheduledReport"]

    params = fake_gateway.calls[0]["params"]
    assert params["schedule"] == {"kind": "cron", "expr": "0 8 * * *", "tz": "Asia/Shanghai", "staggerMs": 0}
    assert params["payload"] == {
        "kind": "toolCall",
        "toolName": "claw-trade-scheduled-work-wake",
        "input": {
            "kind": "scheduled_selection",
            "scheduledReportId": created.scheduledReportId,
            "cronRunId": "auto",
        },
    }
    saved = store.get_scheduled_report(created.scheduledReportId)
    assert saved is not None
    assert saved.task_kind == "selection"
    assert saved.instrument_code == "/select 2"
    assert saved.next_run_at == "2026-05-20T00:00:00Z"

    triggered = service.run_scheduled_report_now(
        request_id="req-run-selection",
        scheduled_report_id=created.scheduledReportId,
    )
    assert triggered["cronRunId"] == "cron-run-1"
    assert store.get_scheduled_report(created.scheduledReportId).last_cron_run_id is None


def test_wechat_schedule_enqueues_logical_recipient_and_drops_old_account_route() -> None:
    enqueued: list[dict[str, object]] = []
    service = SchedulerService(
        enqueue_report_task=lambda task, _request: enqueued.append(task) or {
            "taskId": "task-1",
            "instrumentCode": task["instrumentCode"],
            "market": task["market"],
            "status": "queued",
        },
        store=InMemoryScheduledWorkStore(),
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="req-wechat-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
        notification={
            "channel": "wechat_clawbot",
            "enabled": True,
            "target": "old-sender",
            "accountId": "old-account",
        },
    )

    service.run_scheduled_report_now(request_id="req-run-wechat", scheduled_report_id=schedule.scheduledReportId)

    assert enqueued[0]["notification"] == {
        "channel": "wechat_clawbot",
        "enabled": True,
        "recipientKey": "wechat_primary",
    }


def test_legacy_in_app_schedule_is_not_migrated_to_wechat() -> None:
    enqueued: list[dict[str, object]] = []
    service = SchedulerService(
        enqueue_report_task=lambda task, _request: enqueued.append(task) or {
            "taskId": "task-1",
            "instrumentCode": task["instrumentCode"],
            "market": task["market"],
            "status": "queued",
        },
        store=InMemoryScheduledWorkStore(),
        now_provider=_fixed_now,
    )
    schedule = service.create_scheduled_report(
        request_id="req-in-app-schedule",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
        notification={"channel": "in_app", "enabled": True},
    )

    service.run_scheduled_report_now(request_id="req-run-in-app", scheduled_report_id=schedule.scheduledReportId)

    assert enqueued[0]["notification"] == {"channel": "in_app", "enabled": True}


def test_create_weekly_scheduled_report_maps_python_weekday_to_cron_weekday() -> None:
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=InMemoryScheduledWorkStore(),
        now_provider=_fixed_now,
    )

    service.create_scheduled_report(
        request_id="req-weekly",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="weekly",
        time_of_day="09:30",
        weekday=0,
    )

    assert fake_gateway.calls[0]["params"]["schedule"] == {
        "kind": "cron",
        "expr": "30 9 * * 1",
        "tz": "America/New_York",
        "staggerMs": 0,
    }


def test_pause_resume_delete_scheduled_report_syncs_openclaw_cron_job() -> None:
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=InMemoryScheduledWorkStore(),
        now_provider=_fixed_now,
    )
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    service.pause_scheduled_report(request_id="req-pause", scheduled_report_id=created.scheduledReportId)
    service.resume_scheduled_report(request_id="req-resume", scheduled_report_id=created.scheduledReportId)
    service.delete_scheduled_report(request_id="req-delete", scheduled_report_id=created.scheduledReportId)

    assert [call["method"] for call in fake_gateway.calls] == ["cron.add", "cron.update", "cron.update", "cron.remove"]
    assert fake_gateway.calls[1]["params"] == {"jobId": "scheduled-report:schedule-1", "enabled": False}
    assert fake_gateway.calls[2]["params"] == {"jobId": "scheduled-report:schedule-1", "enabled": True}
    assert fake_gateway.calls[3]["params"] == {"jobId": "scheduled-report:schedule-1"}


def test_delete_scheduled_report_deletes_collapsed_duplicates() -> None:
    fake_gateway = _FakeCronGateway()
    store = InMemoryScheduledWorkStore()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    first = store.get_scheduled_report(created.scheduledReportId)
    assert first is not None
    store.save_scheduled_report(
        replace(
            first,
            id="schedule-2",
            openclaw_cron_job_id="scheduled-report:schedule-2",
            created_at="2026-05-19T12:01:00Z",
            updated_at="2026-05-19T12:01:00Z",
        )
    )

    service.delete_scheduled_report(request_id="req-delete", scheduled_report_id="schedule-2")

    assert store.get_scheduled_report("schedule-1").state == "deleted"
    assert store.get_scheduled_report("schedule-2").state == "deleted"
    assert [call for call in fake_gateway.calls if call["method"] == "cron.remove"] == [
        {"method": "cron.remove", "params": {"jobId": "scheduled-report:schedule-1"}},
        {"method": "cron.remove", "params": {"jobId": "scheduled-report:schedule-2"}},
    ]


def test_delete_scheduled_report_deletes_hidden_sync_failed_duplicate() -> None:
    store = InMemoryScheduledWorkStore()
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {}, store=store, now_provider=_fixed_now)
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    first = store.get_scheduled_report(created.scheduledReportId)
    assert first is not None
    store.save_scheduled_report(
        replace(
            first,
            id="schedule-failed",
            state="sync_failed",
            openclaw_cron_job_id=None,
            created_at="2026-05-19T12:01:00Z",
            updated_at="2026-05-19T12:01:00Z",
        )
    )

    service.delete_scheduled_report(request_id="req-delete", scheduled_report_id=created.scheduledReportId)

    assert store.get_scheduled_report("schedule-1").state == "deleted"
    assert store.get_scheduled_report("schedule-failed").state == "deleted"


def test_run_scheduled_report_now_uses_openclaw_cron_when_job_exists() -> None:
    enqueue_calls: list[tuple[dict[str, object], str]] = []
    fake_gateway = _FakeCronGateway()
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, object], request_id: str) -> dict[str, object]:
        enqueue_calls.append((task, request_id))
        return {"taskId": "task-1", "instrumentCode": "AAPL", "market": "US", "status": "queued"}

    service = SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {"runningTask": None, "queuedTasks": [], "queuedCount": 0, "maxQueueSize": 10},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    result = service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=created.scheduledReportId)

    assert enqueue_calls == []
    assert fake_gateway.calls[-1] == {
        "method": "cron.run",
        "params": {"jobId": "scheduled-report:schedule-1", "idempotencyKey": "req-run-now"},
    }
    assert "task" not in result
    assert result["scheduledReport"].scheduledReportId == created.scheduledReportId
    assert result["triggered"] is True
    assert result["cronRunId"] == "cron-run-1"
    assert result["queueSnapshot"].queued_count == 0
    saved = store.get_scheduled_report(created.scheduledReportId)
    assert saved is not None
    assert saved.last_cron_run_id == "cron-run-1"


def test_run_scheduled_report_now_rejects_paused_cron_schedule_without_cron_run() -> None:
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=InMemoryScheduledWorkStore(),
        now_provider=_fixed_now,
    )
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    service.pause_scheduled_report(request_id="req-pause", scheduled_report_id=created.scheduledReportId)

    with pytest.raises(UiServiceError) as exc:
        service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=created.scheduledReportId)

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "定时报告已暂停。"
    assert [call["method"] for call in fake_gateway.calls] == ["cron.add", "cron.update"]


def test_run_scheduled_report_now_rejects_already_triggered_cron_window_without_cron_run() -> None:
    fake_gateway = _FakeCronGateway()
    store = InMemoryScheduledWorkStore()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    internal = store.get_scheduled_report(created.scheduledReportId)
    assert internal is not None
    internal.last_run_task_id = "task-1"
    store.save_scheduled_report(internal)

    with pytest.raises(UiServiceError) as exc:
        service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=created.scheduledReportId)

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "定时报告当前计划窗口已触发。"
    assert [call["method"] for call in fake_gateway.calls] == ["cron.add"]


def test_cron_wake_enqueues_report_with_default_report_lookback() -> None:
    queue_calls: list[tuple[str, dict[str, object]]] = []
    store = InMemoryScheduledWorkStore()

    def enqueue(task: dict[str, object], request_id: str) -> dict[str, object]:
        queue_calls.append((request_id, task))
        return {"taskId": "task-1", "instrumentCode": task["instrumentCode"], "market": task["market"], "status": "queued"}

    service = SchedulerService(enqueue_report_task=enqueue, store=store, now_provider=_fixed_now)
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="SOL/USDT",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="09:30",
    )

    service.handle_scheduled_report_cron_wake(
        request_id="req-cron-wake",
        scheduled_report_id=created.scheduledReportId,
        cron_run_id="cron-run-1",
    )

    assert len(queue_calls) == 1
    request_id, queued_task = queue_calls[0]
    assert request_id == "scheduled-report:schedule-1:2025-05-19:2026-05-19:2026-05-19"
    assert queued_task["instrumentCode"] == "SOL/USDT"
    assert queued_task["market"] == "CRYPTO"
    assert queued_task["startDate"] == "2025-05-19"
    assert queued_task["endDate"] == "2026-05-19"
    assert queued_task["currentDate"] == "2026-05-19"
    assert queued_task["currencySymbol"] == "USDT"


def test_cron_provision_failure_state_stays_internal() -> None:
    class FailingCronGateway(_FakeCronGateway):
        def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
            self.calls.append({"method": "cron.add", "params": dict(params)})
            raise RuntimeError("cron unavailable")

    store = InMemoryScheduledWorkStore()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(FailingCronGateway()),
        store=store,
        now_provider=_fixed_now,
    )

    with pytest.raises(UiServiceError) as exc:
        service.create_scheduled_report(
            request_id="req-create",
            instrument_code="AAPL",
            market=MarketProfile.US,
            frequency="daily",
            time_of_day="09:30",
        )

    assert exc.value.code == "CRON_PROVISION_FAILED"
    internal = store.get_scheduled_report("schedule-1")
    assert internal is not None
    assert internal.state == "sync_failed"
    with pytest.raises(UiServiceError) as public_exc:
        service.get_scheduled_report("schedule-1")
    assert public_exc.value.code == "SCHEDULE_NOT_FOUND"


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


def test_daily_scheduled_report_within_four_hours_replaces_existing_and_updates_cron() -> None:
    store = InMemoryScheduledWorkStore()
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )
    first = service.create_scheduled_report(
        request_id="req-first",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="23:59",
    )

    replaced = service.create_scheduled_report_for_user(
        request_id="req-replace",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="23:58",
    )

    assert replaced["scheduledReport"].scheduledReportId == first.scheduledReportId
    assert replaced["scheduledReport"].timeOfDay == "23:58"
    assert replaced["replacedScheduledReportId"] == first.scheduledReportId
    assert "已替换" in replaced["message"]
    assert [item["scheduledReportId"] for item in service.list_scheduled_reports_for_user()["items"]] == [
        first.scheduledReportId
    ]
    assert fake_gateway.calls[-1]["method"] == "cron.update"
    assert fake_gateway.calls[-1]["params"]["schedule"] == {
        "kind": "cron",
        "expr": "58 23 * * *",
        "tz": "America/New_York",
        "staggerMs": 0,
    }


def test_daily_scheduled_report_replacement_deletes_older_window_duplicates() -> None:
    store = InMemoryScheduledWorkStore()
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )
    first = service.create_scheduled_report(
        request_id="req-first",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="23:59",
    )
    old = store.get_scheduled_report(first.scheduledReportId)
    assert old is not None
    store.save_scheduled_report(
        replace(
            old,
            id="schedule-2",
            openclaw_cron_job_id="scheduled-report:schedule-2",
            created_at="2026-05-19T12:01:00Z",
            updated_at="2026-05-19T12:01:00Z",
        )
    )

    replaced = service.create_scheduled_report_for_user(
        request_id="req-replace",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="23:58",
    )

    assert replaced["scheduledReport"].scheduledReportId == "schedule-2"
    assert store.get_scheduled_report("schedule-1").state == "deleted"
    kept = store.get_scheduled_report("schedule-2")
    assert kept.state == "active"
    assert kept.time_of_day == "23:58"
    assert [item["scheduledReportId"] for item in service.list_scheduled_reports_for_user()["items"]] == ["schedule-2"]
    assert [call for call in fake_gateway.calls if call["method"] in {"cron.update", "cron.remove"}] == [
        {
            "method": "cron.update",
            "params": {
                "jobId": "scheduled-report:schedule-2",
                "name": "scheduled-report:schedule-2",
                "schedule": {"kind": "cron", "expr": "58 23 * * *", "tz": "America/New_York", "staggerMs": 0},
                "payload": {
                    "kind": "toolCall",
                    "toolName": "claw-trade-scheduled-work-wake",
                    "input": {"kind": "scheduled_report", "scheduledReportId": "schedule-2", "cronRunId": "auto"},
                },
                "enabled": True,
            },
        },
        {"method": "cron.remove", "params": {"jobId": "scheduled-report:schedule-1"}},
    ]


def test_exact_scheduled_report_create_deletes_older_duplicates() -> None:
    store = InMemoryScheduledWorkStore()
    fake_gateway = _FakeCronGateway()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request: {},
        cron_adapter=OpenClawCronAdapter(fake_gateway),
        store=store,
        now_provider=_fixed_now,
    )
    first = service.create_scheduled_report(
        request_id="req-first",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    old = store.get_scheduled_report(first.scheduledReportId)
    assert old is not None
    store.save_scheduled_report(
        replace(
            old,
            id="schedule-2",
            openclaw_cron_job_id="scheduled-report:schedule-2",
            created_at="2026-05-19T12:01:00Z",
            updated_at="2026-05-19T12:01:00Z",
        )
    )

    duplicate = service.create_scheduled_report(
        request_id="req-duplicate",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    assert duplicate.scheduledReportId == "schedule-2"
    assert store.get_scheduled_report("schedule-1").state == "deleted"
    assert store.get_scheduled_report("schedule-2").state == "active"
    assert [item["scheduledReportId"] for item in service.list_scheduled_reports_for_user()["items"]] == ["schedule-2"]
    assert [call for call in fake_gateway.calls if call["method"] == "cron.remove"] == [
        {"method": "cron.remove", "params": {"jobId": "scheduled-report:schedule-1"}}
    ]


def test_create_scheduled_report_deletes_hidden_sync_failed_duplicate() -> None:
    store = InMemoryScheduledWorkStore()
    failed = SchedulerService(enqueue_report_task=lambda _task, _request: {}, store=store, now_provider=_fixed_now)
    created = failed.create_scheduled_report(
        request_id="req-seed",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    old = store.get_scheduled_report(created.scheduledReportId)
    assert old is not None
    store.save_scheduled_report(replace(old, id="schedule-failed", state="sync_failed", openclaw_cron_job_id=None))
    store.save_scheduled_report(replace(old, state="deleted"))
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {}, store=store, now_provider=_fixed_now)

    new = service.create_scheduled_report(
        request_id="req-new",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    assert new.scheduledReportId == "schedule-2"
    assert store.get_scheduled_report("schedule-failed").state == "deleted"


def test_daily_scheduled_report_more_than_four_hours_apart_stays_separate() -> None:
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {}, now_provider=_fixed_now)
    first = service.create_scheduled_report(
        request_id="req-first",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:00",
    )

    second = service.create_scheduled_report(
        request_id="req-second",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="13:01",
    )

    assert second.scheduledReportId != first.scheduledReportId
    assert [item["scheduledReportId"] for item in service.list_scheduled_reports_for_user()["items"]] == [
        first.scheduledReportId,
        second.scheduledReportId,
    ]


def test_scheduled_report_list_collapses_nearby_daily_duplicates_to_latest() -> None:
    store = InMemoryScheduledWorkStore()
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {}, store=store, now_provider=_fixed_now)
    first = service.create_scheduled_report(
        request_id="req-first",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="23:59",
    )
    original = store.get_scheduled_report(first.scheduledReportId)
    assert original is not None
    store.save_scheduled_report(
        replace(
            original,
            id="schedule-99",
            time_of_day="23:58",
            created_at="2026-05-20T12:00:00Z",
            updated_at="2026-05-20T12:00:00Z",
        )
    )

    payload = service.list_scheduled_reports_for_user()

    assert [(item["scheduledReportId"], item["timeOfDay"]) for item in payload["items"]] == [("schedule-99", "23:58")]


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


@pytest.mark.parametrize(
    ("raw_code", "market", "expected_code"),
    (
        ("SH600519", MarketProfile.CN_A, "600519.SH"),
        ("HK00700", MarketProfile.HK, "00700.HK"),
        ("AAPL.US", MarketProfile.US, "AAPL"),
        ("AR", MarketProfile.CRYPTO, "AR/USDT"),
    ),
)
def test_create_scheduled_report_normalizes_market_specific_codes(
    raw_code: str,
    market: MarketProfile,
    expected_code: str,
) -> None:
    service = SchedulerService(enqueue_report_task=lambda _task, _request: {}, now_provider=_fixed_now)

    created = service.create_scheduled_report(
        request_id=f"req-{expected_code}",
        instrument_code=raw_code,
        market=market,
        frequency="daily",
        time_of_day="09:30",
    )

    assert created.instrumentCode == expected_code
    assert created.market == market.value


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

    store = InMemoryScheduledWorkStore()
    service = SchedulerService(
        enqueue_report_task=enqueue,
        now_provider=_fixed_now,
        store=store,
        allow_local_tick_for_tests=True,
    )
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

    active_report = store.get_scheduled_report(active.scheduledReportId)
    assert active_report is not None
    active_report.next_run_at = "2026-05-19T11:59:00Z"
    store.save_scheduled_report(active_report)
    paused_report = store.get_scheduled_report(paused.scheduledReportId)
    assert paused_report is not None
    paused_report.next_run_at = "2026-05-19T11:59:00Z"
    store.save_scheduled_report(paused_report)
    result = service.tick_scheduled_reports(now="2026-05-19T12:00:00Z")

    assert len(queue_calls) == 1
    _, queued_task = queue_calls[0]
    assert queued_task["startDate"] == "2025-05-19"
    assert queued_task["endDate"] == "2026-05-19"
    assert queued_task["currentDate"] == "2026-05-19"
    assert queued_task["workflowSettings"]["defaultProfile"] == "US"
    assert result["results"][0].scheduledReportId == active.scheduledReportId
    assert result["results"][0].enqueued is True
