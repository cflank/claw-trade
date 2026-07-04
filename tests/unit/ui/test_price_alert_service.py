from __future__ import annotations

from datetime import UTC, datetime
from threading import Barrier, Lock, Thread
from time import sleep
from types import SimpleNamespace

import pytest
from claw_trade.ui_backend.price_alert_service import PriceAlertService, UiServiceError
from claw_trade.ui_backend.scheduled_work_store import (
    InMemoryScheduledWorkStore,
    JsonScheduledWorkStore,
)
from claw_trade.ui_contracts.enums import MarketProfile

from claw_trade.ui_backend import data_source_runtime_checks as ui_runtime_checks


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def test_create_price_alert_only_supports_threshold_and_percent_change() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 70000, "percent_change": 3.0},
        now_provider=_fixed_now,
    )
    threshold = service.create_price_alert(
        request_id="req-threshold",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 69000},
        notification={"channel": "in_app", "enabled": True},
    )
    percent = service.create_price_alert(
        request_id="req-percent",
        instrument_code="ETH",
        market=MarketProfile.CRYPTO,
        condition={"type": "percent_change", "operator": "up_by", "value": 5, "window": "24h"},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    assert threshold.priceAlertId == "alert-1"
    assert percent.priceAlertId == "alert-2"
    assert threshold.condition.type == "price_threshold"
    assert percent.condition.type == "percent_change"

    with pytest.raises(UiServiceError, match="价格阈值或涨跌幅"):
        service.create_price_alert(
            request_id="req-invalid-type",
            instrument_code="BTC",
            market=MarketProfile.CRYPTO,
            condition={"type": "macd_cross", "operator": "above", "value": 1},
        )


@pytest.mark.parametrize(
    ("raw_code", "market", "expected_code"),
    (
        ("SH600519", MarketProfile.CN_A, "600519.SH"),
        ("AAPL.US", MarketProfile.US, "AAPL"),
        ("AR", MarketProfile.CRYPTO, "AR/USDT"),
    ),
)
def test_create_price_alert_normalizes_market_specific_codes(
    raw_code: str,
    market: MarketProfile,
    expected_code: str,
) -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        now_provider=_fixed_now,
    )

    created = service.create_price_alert(
        request_id=f"req-{expected_code}",
        instrument_code=raw_code,
        market=market,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    assert created.instrumentCode == expected_code
    assert created.market == market.value


def test_create_price_alert_rejects_hk_until_strategy_approved() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        now_provider=_fixed_now,
    )

    with pytest.raises(UiServiceError) as exc:
        service.create_price_alert(
            request_id="req-hk",
            instrument_code="HK00700",
            market=MarketProfile.HK,
            condition={"type": "price_threshold", "operator": "above", "value": 1},
        )

    assert exc.value.code == "INVALID_INPUT"
    assert "暂不支持港股价格提醒" in exc.value.message


def test_create_price_alert_preserves_wechat_reply_target_from_notification() -> None:
    store = InMemoryScheduledWorkStore()
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        store=store,
        now_provider=_fixed_now,
    )

    created = service.create_price_alert(
        request_id="req-wechat-target",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
        notification={
            "channel": "wechat_clawbot",
            "enabled": True,
            "target": "sender-codex",
            "accountId": "account-codex",
        },
    )

    stored = store.get_price_alert(created.priceAlertId)

    assert stored is not None
    assert stored.notification == {
        "channel": "wechat_clawbot",
        "enabled": True,
        "target": "sender-codex",
        "accountId": "account-codex",
    }


def test_price_alerts_persist_across_service_reconstruction(tmp_path) -> None:
    store_path = tmp_path / ".ui-scheduled-work.json"
    first_service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        store=JsonScheduledWorkStore(store_path),
        now_provider=_fixed_now,
    )
    created = first_service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    second_service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        store=JsonScheduledWorkStore(store_path),
        now_provider=_fixed_now,
    )
    reloaded = second_service.get_price_alert(created.priceAlertId)
    next_alert = second_service.create_price_alert(
        request_id="req-next",
        instrument_code="ETH",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    assert reloaded.priceAlertId == "alert-1"
    assert reloaded.instrumentCode == created.instrumentCode
    assert next_alert.priceAlertId == "alert-2"


def test_concurrent_price_alert_creation_reuses_matching_alert_and_provisions_one_scan_bucket_cron() -> None:
    class SlowCronAdapter:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._lock = Lock()

        def add_job(self, *, name: str, **_kwargs: object) -> SimpleNamespace:
            sleep(0.05)
            with self._lock:
                self.calls.append(name)
                job_id = f"job-{len(self.calls)}"
            return SimpleNamespace(openclaw_cron_job_id=job_id)

    store = InMemoryScheduledWorkStore()
    cron_adapter = SlowCronAdapter()
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        store=store,
        cron_adapter=cron_adapter,  # type: ignore[arg-type]
        now_provider=_fixed_now,
    )
    barrier = Barrier(2)
    created: list[str] = []

    def create(request_id: str) -> None:
        barrier.wait(timeout=2)
        dto = service.create_price_alert(
            request_id=request_id,
            instrument_code="BTC",
            market=MarketProfile.CRYPTO,
            condition={"type": "price_threshold", "operator": "above", "value": 1},
        )
        created.append(dto.priceAlertId)

    threads = [Thread(target=create, args=(f"req-{idx}",)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(created) == ["alert-1", "alert-1"]
    assert cron_adapter.calls == []
    bucket = store.get_scan_bucket("CRYPTO:3m")
    assert bucket is not None
    assert bucket.openclaw_cron_job_id is None


def test_runtime_quote_provider_fails_closed_without_evidence_chain() -> None:
    provider = ui_runtime_checks.build_price_alert_quote_provider(env={}, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="evidence_chain_unavailable"):
        provider("BTC", MarketProfile.CRYPTO)


def test_runtime_quote_provider_fails_closed_without_data_api_quote_path() -> None:
    provider = ui_runtime_checks.build_price_alert_quote_provider(
        env={},
        now_provider=_fixed_now,
        evidence_helper=object(),
        cache_store=object(),  # type: ignore[arg-type]
        rate_limit_store=object(),  # type: ignore[arg-type]
        single_flight=object(),  # type: ignore[arg-type]
        attempt_store=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="price_alert_quote_unavailable"):
        provider("BTC", MarketProfile.CRYPTO)


def test_run_price_alert_now_triggers_and_closes_by_default() -> None:
    sent: list[str] = []
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 71000, "percent_change": 6.2, "percent_change_24h": 6.2},
        notifier=lambda text, _notification: sent.append(text),
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    first = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    second = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)

    assert first["triggered"] is True
    assert first["alert"].state == "closed"
    listed = service.list_price_alerts_for_user()["items"]
    assert len(listed) == 1
    assert listed[0]["priceAlertId"] == alert.priceAlertId
    assert listed[0]["state"] == "closed"
    assert len(sent) == 1
    assert second["triggered"] is True
    assert len(sent) == 1
    recreated = service.create_price_alert(
        request_id="req-create-again",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    assert recreated.priceAlertId != alert.priceAlertId
    assert recreated.state == "active"


def test_price_threshold_uses_window_extrema_when_available() -> None:
    assert PriceAlertService.condition_is_triggered(
        condition={"type": "price_threshold", "operator": "below", "value": 59770},
        quote={"current_price": 59828.99, "window_low": 59769.0, "percent_change": 0},
    )
    assert PriceAlertService.condition_is_triggered(
        condition={"type": "price_threshold", "operator": "above", "value": 60360},
        quote={"current_price": 59828.99, "window_high": 60361.0, "percent_change": 0},
    )


def test_triggered_price_alert_records_channel_notification_success() -> None:
    store = InMemoryScheduledWorkStore()
    sent: list[dict[str, object]] = []
    in_app: list[tuple[str, str]] = []
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.2,
            "quote_timestamp": "2026-05-19T12:00:00Z",
        },
        store=store,
        notifier=lambda text, notification: sent.append({"text": text, "notification": notification})
        or {"sent": True, "messageId": "m-1"},
        in_app_notifier=lambda alert_id, text: in_app.append((alert_id, text)),
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )

    payload = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    stored = store.get_price_alert(alert.priceAlertId)

    assert payload["triggered"] is True
    assert sent[0]["notification"]["dedupeKey"] == "alert-1:1:2026-05-19T12:00:00Z:price_threshold:above"
    assert in_app == [("alert-1", "BTC 已触发价格提醒，当前价格 71000.00。")]
    assert stored is not None
    assert stored.last_notification_result == {
        "channel": "wechat_clawbot",
        "delivered": True,
        "dedupe_key": "alert-1:1:2026-05-19T12:00:00Z:price_threshold:above",
        "sent": True,
        "messageId": "m-1",
    }


def test_triggered_price_alert_falls_back_to_in_app_when_channel_unavailable() -> None:
    store = InMemoryScheduledWorkStore()
    in_app: list[tuple[str, str]] = []
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.2,
            "quote_timestamp": "2026-05-19T12:00:00Z",
        },
        store=store,
        notifier=lambda _text, _notification: {"sent": False, "reason": "missing_wechat_target"},
        in_app_notifier=lambda alert_id, text: in_app.append((alert_id, text)),
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )

    service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    stored = store.get_price_alert(alert.priceAlertId)

    assert len(in_app) == 1
    assert stored is not None
    assert stored.state == "closed"
    assert stored.last_notification_result == {
        "channel": "in_app",
        "delivered": True,
        "fallback_from": "wechat_clawbot",
        "channel_delivered": False,
        "channel_error": "missing_wechat_target",
        "dedupe_key": "alert-1:1:2026-05-19T12:00:00Z:price_threshold:above",
    }


def test_triggered_price_alert_records_default_channel_error_when_channel_returns_false() -> None:
    store = InMemoryScheduledWorkStore()
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.2,
            "quote_timestamp": "2026-05-19T12:00:00Z",
        },
        store=store,
        notifier=lambda _text, _notification: {"sent": False},
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )

    service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    stored = store.get_price_alert(alert.priceAlertId)

    assert stored is not None
    assert stored.last_notification_result == {
        "channel": "in_app",
        "delivered": True,
        "fallback_from": "wechat_clawbot",
        "channel_delivered": False,
        "channel_error": "wechat_send_failed",
        "dedupe_key": "alert-1:1:2026-05-19T12:00:00Z:price_threshold:above",
    }


def test_triggered_price_alert_records_channel_error_when_channel_notifier_raises() -> None:
    store = InMemoryScheduledWorkStore()

    def _raise_notifier(_text: str, _notification: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("send failed")

    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.2,
            "quote_timestamp": "2026-05-19T12:00:00Z",
        },
        store=store,
        notifier=_raise_notifier,
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )

    service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    stored = store.get_price_alert(alert.priceAlertId)

    assert stored is not None
    assert stored.last_notification_result == {
        "channel": "in_app",
        "delivered": True,
        "fallback_from": "wechat_clawbot",
        "channel_delivered": False,
        "channel_error": "wechat_send_failed",
        "dedupe_key": "alert-1:1:2026-05-19T12:00:00Z:price_threshold:above",
    }


def test_price_alert_notification_dedupe_prevents_duplicate_delivery() -> None:
    store = InMemoryScheduledWorkStore()
    sent: list[str] = []
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.2,
            "quote_timestamp": "2026-05-19T12:00:00Z",
        },
        store=store,
        notifier=lambda text, _notification: sent.append(text) or {"sent": True, "messageId": "m-1"},
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    service.run_price_alert_now(request_id="req-run-1", price_alert_id=alert.priceAlertId)
    stored = store.get_price_alert(alert.priceAlertId)
    assert stored is not None
    stored.state = "active"
    store.save_price_alert(stored)

    service.run_price_alert_now(request_id="req-run-2", price_alert_id=alert.priceAlertId)

    assert len(sent) == 1


def test_triggered_price_alert_does_not_create_report_confirmation_card() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {
            "current_price": 71000,
            "percent_change": 6.2,
            "quote_timestamp": "2026-05-19T12:00:00Z",
        },
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
    )

    payload = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)

    assert "confirmationCard" not in payload
    assert "reportTask" not in payload
    assert "scheduledReport" not in payload


def test_paused_alert_skip_checking() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 100, "percent_change": 1},
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        condition={"type": "price_threshold", "operator": "above", "value": 120},
    )
    service.pause_price_alert(request_id="req-pause", price_alert_id=alert.priceAlertId)
    payload = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    assert payload["triggered"] is False
    assert payload["message"] == "提醒已暂停，暂不检查。"
    assert payload["alert"].state == "paused"


def test_run_price_alert_now_returns_message_when_not_triggered() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 100, "percent_change": 1},
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        condition={"type": "price_threshold", "operator": "above", "value": 120},
    )

    payload = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)

    assert payload["triggered"] is False
    assert payload["message"] == "已检查 AAPL，当前价格 100.00，未触发提醒。"


def test_quote_provider_failure_sets_error_state() -> None:
    def raise_quote(_instrument: str, _market: MarketProfile) -> dict[str, float]:
        raise RuntimeError("network down")

    service = PriceAlertService(quote_provider=raise_quote, now_provider=_fixed_now)
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="TSLA",
        market=MarketProfile.US,
        condition={"type": "price_threshold", "operator": "below", "value": 200},
    )
    with pytest.raises(UiServiceError) as exc:
        service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    assert exc.value.code == "DATASOURCE_TEST_FAILED"
    assert service.get_price_alert(alert.priceAlertId).state == "error"
