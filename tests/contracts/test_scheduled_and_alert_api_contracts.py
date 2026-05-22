from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_contracts.api_contracts import validate_ui_api_response
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import to_user_payload


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


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
