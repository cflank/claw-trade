from __future__ import annotations

import pytest

from claw_trade.ui_backend.scheduled_work_store import (
    JsonScheduledWorkStore,
    PriceAlert,
    PriceAlertScanBucket,
    ScheduledReport,
)
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import to_price_alert_for_user, to_user_payload


def _alert(alert_id: str, *, market: MarketProfile = MarketProfile.CRYPTO, state: str = "active") -> PriceAlert:
    return PriceAlert(
        id=alert_id,
        instrument_code="BTC/USDT",
        instrument_name="BTC",
        market=market,
        condition={"type": "price_threshold", "operator": "above", "value": 70000.0, "window": None},
        condition_version=1,
        notification={"channel": "in_app", "enabled": True},
        state=state,
        scan_bucket=f"{market.value}:3m",
        last_checked_at=None,
        triggered_at=None,
        last_error_message=None,
        last_quote_evidence_ref="evidence://quote/BTC",
        last_scan_run_id="scan-1",
        notification_dedupe_key="alert-1:scan-1",
        last_notification_result={"channel": "in_app", "delivered": True},
        created_at="2026-05-19T12:00:00Z",
        updated_at="2026-05-19T12:00:00Z",
    )


def _bucket() -> PriceAlertScanBucket:
    return PriceAlertScanBucket(
        bucket_key="CRYPTO:3m",
        market=MarketProfile.CRYPTO,
        frequency="3m",
        enabled=True,
        openclaw_cron_job_id="price-alert-scan:CRYPTO:3m",
        last_scan_run_id="scan-1",
        last_scan_summary={"checked": 2, "triggered": 1},
        last_error_message=None,
        skipped_reason=None,
        created_at="2026-05-19T12:00:00Z",
        updated_at="2026-05-19T12:03:00Z",
    )


def _scheduled_report(report_id: str = "schedule-1", *, state: str = "active") -> ScheduledReport:
    return ScheduledReport(
        id=report_id,
        instrument_code="AAPL",
        instrument_name="Apple Inc.",
        market=MarketProfile.US,
        frequency="daily",
        time_of_day="09:30",
        weekday=None,
        notification={"channel": "in_app", "enabled": True},
        workflow_settings={
            "maxDebateRounds": 1,
            "maxRiskDiscussRounds": 1,
            "frontlineExecutionMode": "parallel",
            "defaultProfile": "US",
            "defaultMarket": "US",
            "defaultCurrency": "USD",
            "defaultCurrencySymbol": "$",
        },
        start_date="2026-05-19",
        end_date="2026-05-19",
        current_date="2026-05-19",
        state=state,
        next_run_at="2026-05-20T09:30:00Z",
        last_run_task_id=None,
        openclaw_cron_job_id="scheduled-report:schedule-1",
        last_cron_run_id=None,
        sync_error_message=None,
        created_at="2026-05-19T12:00:00Z",
        updated_at="2026-05-19T12:00:00Z",
    )


def test_scheduled_reports_persist_across_store_instances(tmp_path) -> None:
    report = _scheduled_report()
    store = JsonScheduledWorkStore(tmp_path / "scheduled.json")
    store.save_scheduled_report(report)

    second = JsonScheduledWorkStore(tmp_path / "scheduled.json")

    assert second.get_scheduled_report(report.id) == report
    assert second.list_scheduled_reports(states={"active"}) == [report]


def test_json_store_loads_old_payload_without_scheduled_reports(tmp_path) -> None:
    store_path = tmp_path / ".ui-scheduled-work.json"
    store_path.write_text('{"price_alerts": {}, "scan_buckets": {}}\n', encoding="utf-8")
    store = JsonScheduledWorkStore(store_path)

    assert store.list_scheduled_reports() == []

    report = _scheduled_report()
    store.save_scheduled_report(report)

    assert JsonScheduledWorkStore(store_path).get_scheduled_report(report.id) == report


def test_json_store_saves_loads_and_filters_price_alerts(tmp_path) -> None:
    store = JsonScheduledWorkStore(tmp_path / ".ui-scheduled-work.json")
    store.save_price_alert(_alert("alert-1", market=MarketProfile.CRYPTO))
    store.save_price_alert(_alert("alert-2", market=MarketProfile.CN_A))
    store.save_price_alert(_alert("alert-3", market=MarketProfile.CRYPTO, state="closed"))

    reloaded = JsonScheduledWorkStore(tmp_path / ".ui-scheduled-work.json")

    assert reloaded.get_price_alert("alert-1") == _alert("alert-1", market=MarketProfile.CRYPTO)
    assert [alert.id for alert in reloaded.list_price_alerts(market=MarketProfile.CRYPTO)] == ["alert-1", "alert-3"]
    assert [alert.id for alert in reloaded.list_price_alerts(market=MarketProfile.CRYPTO, states={"active"})] == ["alert-1"]


def test_json_store_saves_scan_bucket_and_summary(tmp_path) -> None:
    store = JsonScheduledWorkStore(tmp_path / ".ui-scheduled-work.json")
    bucket = _bucket()

    store.save_scan_bucket(bucket)
    reloaded = JsonScheduledWorkStore(tmp_path / ".ui-scheduled-work.json")

    assert reloaded.get_scan_bucket("CRYPTO:3m") == bucket


def test_json_store_parse_failure_is_fail_closed(tmp_path) -> None:
    store_path = tmp_path / ".ui-scheduled-work.json"
    store_path.write_text("{not-json", encoding="utf-8")
    store = JsonScheduledWorkStore(store_path)

    with pytest.raises(RuntimeError, match="scheduled_work_store_load_failed"):
        store.list_price_alerts()


def test_price_alert_user_dto_excludes_internal_store_fields() -> None:
    payload = to_user_payload(to_price_alert_for_user(_alert("alert-1")))

    assert payload == {
        "priceAlertId": "alert-1",
        "instrumentCode": "BTC/USDT",
        "instrumentName": "BTC",
        "market": "CRYPTO",
        "condition": {"type": "price_threshold", "operator": "above", "value": 70000.0, "window": None},
        "notification": {"channel": "in_app", "enabled": True},
        "state": "active",
        "lastCheckedAt": None,
        "triggeredAt": None,
        "lastErrorMessage": None,
    }
