from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from claw_trade.ui_backend.price_alert_scan_service import PriceAlertScanService
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore, PriceAlert, PriceAlertScanBucket
from claw_trade.ui_contracts.enums import MarketProfile


def _utc_now() -> datetime:
    return datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _cn_a_open_now() -> datetime:
    return datetime(2026, 6, 17, 2, 0, tzinfo=UTC)


def _alert(
    alert_id: str,
    *,
    instrument_code: str = "BTC/USDT",
    market: MarketProfile = MarketProfile.CRYPTO,
    bucket_key: str = "CRYPTO:3m",
    condition: dict[str, Any] | None = None,
) -> PriceAlert:
    return PriceAlert(
        id=alert_id,
        instrument_code=instrument_code,
        instrument_name=instrument_code,
        market=market,
        condition=condition or {"type": "price_threshold", "operator": "above", "value": 90.0, "window": None},
        condition_version=1,
        notification={"channel": "in_app", "enabled": True},
        state="active",
        scan_bucket=bucket_key,
        last_checked_at=None,
        triggered_at=None,
        last_error_message=None,
        last_quote_evidence_ref=None,
        last_scan_run_id=None,
        notification_dedupe_key=None,
        last_notification_result=None,
        created_at="2026-06-17T12:00:00Z",
        updated_at="2026-06-17T12:00:00Z",
    )


def _bucket(
    bucket_key: str = "CRYPTO:3m",
    *,
    market: MarketProfile = MarketProfile.CRYPTO,
    enabled: bool = True,
    skipped_reason: str | None = None,
) -> PriceAlertScanBucket:
    return PriceAlertScanBucket(
        bucket_key=bucket_key,
        market=market,
        frequency="3m",
        enabled=enabled,
        openclaw_cron_job_id=f"price-alert-scan:{bucket_key}" if enabled else None,
        last_scan_run_id=None,
        last_scan_summary=None,
        last_error_message=None,
        skipped_reason=skipped_reason,
        created_at="2026-06-17T12:00:00Z",
        updated_at="2026-06-17T12:00:00Z",
    )


def test_scan_bucket_groups_same_instrument_and_updates_triggered_alerts() -> None:
    store = InMemoryScheduledWorkStore()
    store.save_scan_bucket(_bucket())
    store.save_price_alert(_alert("alert-1"))
    store.save_price_alert(
        _alert("alert-2", condition={"type": "price_threshold", "operator": "above", "value": 110.0, "window": None})
    )
    quote_calls: list[tuple[str, MarketProfile]] = []

    def quote_provider(instrument_code: str, market: MarketProfile) -> dict[str, Any]:
        quote_calls.append((instrument_code, market))
        return {"current_price": 100.0, "percent_change": 1.0, "quote_timestamp": "2026-06-17T12:00:00Z", "evidence_ref": "quote://btc"}

    service = PriceAlertScanService(store=store, quote_provider=quote_provider, now_provider=_utc_now)

    summary = service.scan_bucket("CRYPTO:3m", cron_run_id="cron-run-1")

    assert quote_calls == [("BTC/USDT", MarketProfile.CRYPTO)]
    assert summary.active_alert_count == 2
    assert summary.grouped_quote_count == 1
    assert summary.triggered_alert_count == 1
    assert summary.failed_alert_count == 0
    assert summary.quote_evidence_refs == ("quote://btc",)
    assert store.get_price_alert("alert-1").state == "closed"  # type: ignore[union-attr]
    assert store.get_price_alert("alert-1").last_quote_evidence_ref == "quote://btc"  # type: ignore[union-attr]
    assert store.get_price_alert("alert-2").state == "active"  # type: ignore[union-attr]
    assert store.get_scan_bucket("CRYPTO:3m").last_scan_run_id == summary.scan_run_id  # type: ignore[union-attr]


def test_scan_bucket_records_quote_failure_on_alert() -> None:
    store = InMemoryScheduledWorkStore()
    store.save_scan_bucket(_bucket())
    store.save_price_alert(_alert("alert-1"))

    def quote_provider(_instrument_code: str, _market: MarketProfile) -> dict[str, Any]:
        raise RuntimeError("quote down")

    service = PriceAlertScanService(store=store, quote_provider=quote_provider, now_provider=_utc_now)

    summary = service.scan_bucket("CRYPTO:3m", cron_run_id="cron-run-1")

    alert = store.get_price_alert("alert-1")
    assert summary.failed_alert_count == 1
    assert alert is not None
    assert alert.state == "error"
    assert alert.last_error_message == "价格提醒检查失败，请稍后重试。"


def test_cn_a_scan_skips_outside_trading_hours_without_quote_request() -> None:
    store = InMemoryScheduledWorkStore()
    store.save_scan_bucket(_bucket("CN_A:3m", market=MarketProfile.CN_A))
    store.save_price_alert(_alert("alert-1", instrument_code="600519.SH", market=MarketProfile.CN_A, bucket_key="CN_A:3m"))
    quote_calls: list[str] = []
    service = PriceAlertScanService(
        store=store,
        quote_provider=lambda instrument, _market: quote_calls.append(instrument) or {},
        now_provider=_utc_now,
    )

    summary = service.scan_bucket("CN_A:3m", cron_run_id="cron-run-1")

    assert quote_calls == []
    assert summary.skipped_alert_count == 1
    assert store.get_price_alert("alert-1").state == "active"  # type: ignore[union-attr]


def test_cn_a_scan_fetches_quotes_inside_trading_hours() -> None:
    store = InMemoryScheduledWorkStore()
    store.save_scan_bucket(_bucket("CN_A:3m", market=MarketProfile.CN_A))
    store.save_price_alert(_alert("alert-1", instrument_code="600519.SH", market=MarketProfile.CN_A, bucket_key="CN_A:3m"))
    quote_calls: list[str] = []
    service = PriceAlertScanService(
        store=store,
        quote_provider=lambda instrument, _market: quote_calls.append(instrument)
        or {"current_price": 100.0, "percent_change": 1.0, "evidence_ref": "quote://cn-a"},
        now_provider=_cn_a_open_now,
    )

    summary = service.scan_bucket("CN_A:3m", cron_run_id="cron-run-1")

    assert quote_calls == ["600519.SH"]
    assert summary.grouped_quote_count == 1


def test_us_disabled_bucket_is_skipped_until_enabled() -> None:
    store = InMemoryScheduledWorkStore()
    store.save_scan_bucket(
        _bucket("US:3m", market=MarketProfile.US, enabled=False, skipped_reason="quote_calendar_live_evidence_required")
    )
    store.save_price_alert(_alert("alert-1", instrument_code="AAPL", market=MarketProfile.US, bucket_key="US:3m"))
    quote_calls: list[str] = []
    service = PriceAlertScanService(
        store=store,
        quote_provider=lambda instrument, _market: quote_calls.append(instrument) or {},
        now_provider=_utc_now,
    )

    summary = service.scan_bucket("US:3m", cron_run_id="cron-run-1")

    assert quote_calls == []
    assert summary.skipped_alert_count == 1
    assert summary.last_error_message is None


def test_crypto_scan_does_not_market_skip() -> None:
    store = InMemoryScheduledWorkStore()
    store.save_scan_bucket(_bucket())
    store.save_price_alert(_alert("alert-1"))
    quote_calls: list[str] = []
    service = PriceAlertScanService(
        store=store,
        quote_provider=lambda instrument, _market: quote_calls.append(instrument)
        or {"current_price": 100.0, "percent_change": 1.0, "evidence_ref": "quote://btc"},
        now_provider=_utc_now,
    )

    summary = service.scan_bucket("CRYPTO:3m", cron_run_id="cron-run-1")

    assert quote_calls == ["BTC/USDT"]
    assert summary.skipped_alert_count == 0
