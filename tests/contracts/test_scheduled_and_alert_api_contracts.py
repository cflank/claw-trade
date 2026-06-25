from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Mapping

from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.ui_contracts.api_contracts import validate_ui_api_response
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

    def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "cron.run", "params": {"jobId": job_id, "idempotencyKey": idempotency_key}})
        return {"runId": "cron-run-1"}

    def cron_remove(self, *, job_id: str) -> dict[str, Any]:
        self.calls.append({"method": "cron.remove", "params": {"jobId": job_id}})
        return {"removed": True}


def _scheduler() -> SchedulerService:
    return SchedulerService(
        enqueue_report_task=lambda task, _request_id: {
            "taskId": "task-1",
            "instrumentCode": task["instrumentCode"],
            "instrumentName": task.get("instrumentName"),
            "market": task["market"],
            "status": "queued",
        },
        queue_snapshot_provider=lambda: {
            "runningTask": None,
            "queuedTasks": [{"taskId": "task-1", "instrumentCode": "AAPL", "market": "US", "status": "queued"}],
            "queuedCount": 1,
            "maxQueueSize": 10,
        },
        now_provider=_fixed_now,
    )


def _cron_scheduler(gateway: _FakeCronGateway) -> SchedulerService:
    def enqueue(_task: dict[str, object], _request_id: str) -> dict[str, object]:
        raise AssertionError("cron run-now must not enqueue directly")

    return SchedulerService(
        enqueue_report_task=enqueue,
        queue_snapshot_provider=lambda: {
            "runningTask": None,
            "queuedTasks": [],
            "queuedCount": 0,
            "maxQueueSize": 10,
        },
        store=InMemoryScheduledWorkStore(),
        cron_adapter=OpenClawCronAdapter(gateway),
        now_provider=_fixed_now,
    )


def _alert_service() -> PriceAlertService:
    return PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.3,
            "percent_change_24h": 6.3,
        },
        notifier=lambda _text, _notification: None,
        now_provider=_fixed_now,
    )


def test_scheduled_report_api_contracts_return_user_dto_and_safe_payload() -> None:
    service = _scheduler()
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    created_payload = to_user_payload(created)
    validate_ui_api_response("createScheduledReport", created_payload)
    assert tuple(created_payload) == (
        "scheduledReportId",
        "instrumentCode",
        "instrumentName",
        "market",
        "frequency",
        "timeOfDay",
        "weekday",
        "notification",
        "state",
        "nextRunAt",
    )

    paused_payload = to_user_payload(
        service.pause_scheduled_report(request_id="req-pause", scheduled_report_id=created.scheduledReportId)
    )
    validate_ui_api_response("pauseScheduledReport", paused_payload)
    resumed_payload = to_user_payload(
        service.resume_scheduled_report(request_id="req-resume", scheduled_report_id=created.scheduledReportId)
    )
    validate_ui_api_response("resumeScheduledReport", resumed_payload)

    run_now_payload = service.run_scheduled_report_now(
        request_id="req-run-now",
        scheduled_report_id=created.scheduledReportId,
    )
    validate_ui_api_response(
        "runScheduledReportNow",
        {
            "task": to_user_payload(run_now_payload["task"]),
            "queueSnapshot": to_user_payload(run_now_payload["queueSnapshot"]),
        },
    )

    deleted_payload = service.delete_scheduled_report(request_id="req-delete", scheduled_report_id=created.scheduledReportId)
    validate_ui_api_response("deleteScheduledReport", deleted_payload)


def test_scheduled_report_list_exposes_cron_and_wake_evidence_without_run_id() -> None:
    gateway = _FakeCronGateway()
    service = _cron_scheduler(gateway)
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )
    service.run_scheduled_report_now(request_id="req-run-now", scheduled_report_id=created.scheduledReportId)

    payload = service.list_scheduled_reports_for_user()

    validate_ui_api_response("listScheduledReports", payload)
    assert payload["items"][0]["cronJobId"] == "scheduled-report:schedule-1"
    assert payload["items"][0]["lastCronRunId"] == "cron-run-1"
    assert payload["items"][0]["lastRunTaskId"] is None


def test_scheduled_report_create_reuses_matching_existing_schedule() -> None:
    service = _scheduler()

    first = service.create_scheduled_report(
        request_id="req-create-1",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="08:00",
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    second = service.create_scheduled_report(
        request_id="req-create-2",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="08:00",
        notification={"channel": "wechat_clawbot", "enabled": True},
    )

    payload = service.list_scheduled_reports_for_user()

    assert second.scheduledReportId == first.scheduledReportId
    assert [item["scheduledReportId"] for item in payload["items"]] == [first.scheduledReportId]


def test_scheduled_report_list_dedupes_existing_matching_rows() -> None:
    store = InMemoryScheduledWorkStore()
    service = SchedulerService(
        enqueue_report_task=lambda _task, _request_id: {},
        store=store,
        now_provider=_fixed_now,
    )
    created = service.create_scheduled_report(
        request_id="req-create-1",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="08:00",
    )
    original = store.get_scheduled_report(created.scheduledReportId)
    assert original is not None
    store.save_scheduled_report(
        replace(
            original,
            id="schedule-99",
            state="active",
            created_at="2026-05-20T12:00:00Z",
            updated_at="2026-05-20T12:00:00Z",
        )
    )

    payload = service.list_scheduled_reports_for_user()

    assert [item["scheduledReportId"] for item in payload["items"]] == ["schedule-99"]


def test_scheduled_report_list_hides_deleted_and_sync_failed_rows() -> None:
    gateway = _FakeCronGateway()
    service = _cron_scheduler(gateway)
    active = service.create_scheduled_report(
        request_id="req-active",
        instrument_code="AAPL",
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
    service.delete_scheduled_report(request_id="req-delete", scheduled_report_id=deleted.scheduledReportId)

    payload = service.list_scheduled_reports_for_user()

    assert [item["scheduledReportId"] for item in payload["items"]] == [active.scheduledReportId]


def test_cron_scheduled_report_run_now_contract_returns_trigger_without_fake_task() -> None:
    gateway = _FakeCronGateway()
    service = _cron_scheduler(gateway)
    created = service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
    )

    run_now_payload = service.run_scheduled_report_now(
        request_id="req-run-now",
        scheduled_report_id=created.scheduledReportId,
    )
    serialized = {
        "scheduledReport": to_user_payload(run_now_payload["scheduledReport"]),
        "triggered": run_now_payload["triggered"],
        "cronRunId": run_now_payload["cronRunId"],
        "queueSnapshot": to_user_payload(run_now_payload["queueSnapshot"]),
    }

    validate_ui_api_response("runScheduledReportNow", serialized)
    assert "task" not in run_now_payload
    assert serialized == {
        "scheduledReport": to_user_payload(created),
        "triggered": True,
        "cronRunId": "cron-run-1",
        "queueSnapshot": {
            "runningTask": None,
            "queuedTasks": [],
            "lastTerminalTask": None,
            "queueLimit": 10,
            "queuedCount": 0,
            "isFull": False,
        },
    }
    assert [call["method"] for call in gateway.calls] == ["cron.add", "cron.run"]


def test_scheduled_report_cron_uses_user_local_timezone() -> None:
    gateway = _FakeCronGateway()
    service = _cron_scheduler(gateway)

    service.create_scheduled_report(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="08:00",
    )

    assert gateway.calls[0]["params"]["schedule"] == {
        "kind": "cron",
        "expr": "0 8 * * *",
        "tz": "America/New_York",
        "staggerMs": 0,
    }


def test_price_alert_api_contracts_return_user_dto_and_safe_payload() -> None:
    service = _alert_service()
    created = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    created_payload = to_user_payload(created)
    validate_ui_api_response("createPriceAlert", created_payload)
    assert tuple(created_payload) == (
        "priceAlertId",
        "instrumentCode",
        "instrumentName",
        "market",
        "condition",
        "notification",
        "state",
        "lastCheckedAt",
        "triggeredAt",
        "lastErrorMessage",
    )

    paused_payload = to_user_payload(service.pause_price_alert(request_id="req-pause", price_alert_id=created.priceAlertId))
    validate_ui_api_response("pausePriceAlert", paused_payload)
    resumed_payload = to_user_payload(service.resume_price_alert(request_id="req-resume", price_alert_id=created.priceAlertId))
    validate_ui_api_response("resumePriceAlert", resumed_payload)

    run_now_payload = service.run_price_alert_now(request_id="req-run-now", price_alert_id=created.priceAlertId)
    validate_ui_api_response(
        "runPriceAlertNow",
        {"alert": to_user_payload(run_now_payload["alert"]), "triggered": run_now_payload["triggered"], "message": run_now_payload.get("message")},
    )

    deleted_payload = service.delete_price_alert(request_id="req-delete", price_alert_id=created.priceAlertId)
    validate_ui_api_response("deletePriceAlert", deleted_payload)


def test_price_alert_list_exposes_scan_bucket_and_real_quote_snapshot() -> None:
    service = _alert_service()
    created = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 90000},
    )

    service.run_price_alert_now(request_id="req-check", price_alert_id=created.priceAlertId)
    payload = service.list_price_alerts_for_user()
    buckets = service.list_price_alert_scan_buckets_for_user()

    validate_ui_api_response("listPriceAlerts", payload)
    assert payload["items"][0]["scanBucket"] == "CRYPTO:3m"
    assert payload["items"][0]["lastQuote"]["currentPrice"] == 71000
    assert payload["items"][0]["lastCheckedAt"] == "2026-05-19T12:00:00Z"
    assert buckets["items"][0]["bucketKey"] == "CRYPTO:3m"


def test_price_alert_create_reuses_matching_existing_alert() -> None:
    service = _alert_service()

    first = service.create_price_alert(
        request_id="req-create-1",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    second = service.create_price_alert(
        request_id="req-create-2",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )

    payload = service.list_price_alerts_for_user()

    assert second.priceAlertId == first.priceAlertId
    assert [item["priceAlertId"] for item in payload["items"]] == [first.priceAlertId]


def test_price_alert_list_dedupes_existing_matching_rows() -> None:
    store = InMemoryScheduledWorkStore()
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.3,
            "percent_change_24h": 6.3,
        },
        store=store,
        now_provider=_fixed_now,
    )
    created = service.create_price_alert(
        request_id="req-create-1",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
    )
    original = store.get_price_alert(created.priceAlertId)
    assert original is not None
    store.save_price_alert(
        replace(
            original,
            id="alert-99",
            state="error",
            created_at="2026-05-20T12:00:00Z",
            updated_at="2026-05-20T12:00:00Z",
        )
    )

    payload = service.list_price_alerts_for_user()

    assert [item["priceAlertId"] for item in payload["items"]] == [created.priceAlertId]


def test_price_alert_list_hides_closed_and_deleted_rows() -> None:
    service = _alert_service()
    closed = service.create_price_alert(
        request_id="req-closed",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
    )
    active = service.create_price_alert(
        request_id="req-active",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 90000},
    )
    deleted = service.create_price_alert(
        request_id="req-deleted",
        instrument_code="ETH",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 90000},
    )

    service.run_price_alert_now(request_id="req-check", price_alert_id=closed.priceAlertId)
    service.delete_price_alert(request_id="req-delete", price_alert_id=deleted.priceAlertId)

    payload = service.list_price_alerts_for_user()

    assert [item["priceAlertId"] for item in payload["items"]] == [active.priceAlertId]
