from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claw_trade.ui_backend.price_alert_service import PriceAlertService, UiServiceError
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import to_user_payload


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
    )
    first = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    second = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)

    assert first["triggered"] is True
    assert first["alert"].state == "closed"
    assert len(sent) == 1
    assert second["triggered"] is True
    assert len(sent) == 1


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
