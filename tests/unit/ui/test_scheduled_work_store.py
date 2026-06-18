from __future__ import annotations

import pytest

from claw_trade.ui_backend.scheduled_work_store import JsonScheduledWorkStore, PriceAlert, PriceAlertScanBucket
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
