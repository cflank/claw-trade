from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from claw_trade.instruments.resolver import resolve_instrument_identity
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import PriceAlertForUser, to_price_alert_for_user
from claw_trade.workflow.report_request_factory import report_display_name


class UiServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PriceAlert:
    id: str
    instrument_code: str
    instrument_name: str | None
    market: MarketProfile
    condition: dict[str, Any]
    notification: dict[str, Any]
    state: str
    last_checked_at: str | None
    triggered_at: str | None
    last_error_message: str | None
    created_at: str
    updated_at: str


class PriceAlertService:
    def __init__(
        self,
        *,
        quote_provider: Callable[[str, MarketProfile], dict[str, Any]],
        notifier: Callable[[str, dict[str, Any]], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._quote_provider = quote_provider
        self._notifier = notifier or (lambda _text, _notification: None)
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._items: dict[str, PriceAlert] = {}
        self._idempotency: dict[str, Any] = {}
        self._seq = 0

    def create_price_alert(
        self,
        *,
        request_id: str,
        instrument_code: str,
        market: MarketProfile | str,
        condition: dict[str, Any],
        notification: dict[str, Any] | None = None,
        instrument_name: str | None = None,
    ) -> PriceAlertForUser:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        market_value = self._as_market_profile(market)
        identity = resolve_instrument_identity(instrument_code, market_hint=market_value.value)
        market_value = MarketProfile(identity.profile)
        normalized_condition = self._normalize_condition(condition)
        now_iso = self._now_iso()
        item = PriceAlert(
            id=self._next_alert_id(),
            instrument_code=identity.ticker,
            instrument_name=instrument_name or report_display_name(identity.ticker, identity.profile),
            market=market_value,
            condition=normalized_condition,
            notification=self._normalize_notification(notification),
            state="active",
            last_checked_at=None,
            triggered_at=None,
            last_error_message=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        self._items[item.id] = item
        dto = to_price_alert_for_user(item)
        self._idempotency[request_id] = dto
        return dto

    def pause_price_alert(self, *, request_id: str, price_alert_id: str) -> PriceAlertForUser:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._get_alert_or_raise(price_alert_id)
        if item.state == "paused":
            dto = to_price_alert_for_user(item)
            self._idempotency[request_id] = dto
            return dto
        if item.state not in {"active", "error"}:
            raise UiServiceError("INVALID_INPUT", "当前状态不能暂停。")
        item.state = "paused"
        item.updated_at = self._now_iso()
        dto = to_price_alert_for_user(item)
        self._idempotency[request_id] = dto
        return dto

    def resume_price_alert(self, *, request_id: str, price_alert_id: str) -> PriceAlertForUser:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._get_alert_or_raise(price_alert_id)
        if item.state == "active":
            dto = to_price_alert_for_user(item)
            self._idempotency[request_id] = dto
            return dto
        if item.state != "paused":
            raise UiServiceError("INVALID_INPUT", "当前状态不能恢复。")
        item.state = "active"
        item.updated_at = self._now_iso()
        dto = to_price_alert_for_user(item)
        self._idempotency[request_id] = dto
        return dto

    def delete_price_alert(self, *, request_id: str, price_alert_id: str) -> dict[str, Any]:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._items.get(price_alert_id)
        if item is None:
            raise UiServiceError("ALERT_NOT_FOUND", "价格提醒不存在。")
        item.state = "deleted"
        item.updated_at = self._now_iso()
        payload = {"deleted": True, "priceAlertId": item.id}
        self._idempotency[request_id] = payload
        return payload

    def run_price_alert_now(self, *, request_id: str, price_alert_id: str) -> dict[str, Any]:
        return self.evaluate_price_alert(price_alert_id=price_alert_id, request_id=request_id)

    def evaluate_price_alert(self, *, price_alert_id: str, request_id: str | None = None) -> dict[str, Any]:
        if request_id:
            cached = self._idempotency.get(request_id)
            if cached is not None:
                return cached
        item = self._items.get(price_alert_id)
        if item is None or item.state in {"deleted", "closed"}:
            raise UiServiceError("ALERT_NOT_FOUND", "价格提醒不存在。")
        if item.state == "paused":
            payload = {"alert": to_price_alert_for_user(item), "triggered": False, "message": "提醒已暂停，暂不检查。"}
            if request_id:
                self._idempotency[request_id] = payload
            return payload

        item.state = "checking"
        item.updated_at = self._now_iso()
        try:
            quote = self._quote_provider(item.instrument_code, item.market)
            triggered = self._is_triggered(condition=item.condition, quote=quote)
            now_iso = self._now_iso()
            if not triggered:
                item.state = "active"
                item.last_checked_at = now_iso
                item.last_error_message = None
                item.updated_at = now_iso
                payload = {"alert": to_price_alert_for_user(item), "triggered": False}
            else:
                message = self._render_triggered_message(item, quote)
                self._notifier(message, item.notification)
                item.state = "closed"
                item.triggered_at = now_iso
                item.last_checked_at = now_iso
                item.last_error_message = None
                item.updated_at = now_iso
                payload = {"alert": to_price_alert_for_user(item), "triggered": True, "message": message}
        except UiServiceError:
            raise
        except Exception as exc:
            item.state = "error"
            item.last_checked_at = self._now_iso()
            item.last_error_message = "价格提醒检查失败，请稍后重试。"
            item.updated_at = self._now_iso()
            raise UiServiceError("DATASOURCE_TEST_FAILED", "价格提醒检查失败，请稍后重试。") from exc

        if request_id:
            self._idempotency[request_id] = payload
        return payload

    def get_price_alert(self, price_alert_id: str) -> PriceAlertForUser:
        return to_price_alert_for_user(self._get_alert_or_raise(price_alert_id))

    def _get_alert_or_raise(self, price_alert_id: str) -> PriceAlert:
        item = self._items.get(price_alert_id)
        if item is None or item.state in {"deleted", "closed"}:
            raise UiServiceError("ALERT_NOT_FOUND", "价格提醒不存在。")
        return item

    @staticmethod
    def _normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
        type_value = str(condition.get("type", "")).strip()
        operator = str(condition.get("operator", "")).strip()
        if type_value not in {"price_threshold", "percent_change"}:
            raise UiServiceError("INVALID_INPUT", "当前只支持价格阈值或涨跌幅提醒。")
        if type_value == "price_threshold" and operator not in {"above", "below"}:
            raise UiServiceError("INVALID_INPUT", "价格阈值提醒只支持 above 或 below。")
        if type_value == "percent_change" and operator not in {"up_by", "down_by"}:
            raise UiServiceError("INVALID_INPUT", "涨跌幅提醒只支持 up_by 或 down_by。")
        try:
            value = float(condition.get("value"))
        except (TypeError, ValueError) as exc:
            raise UiServiceError("INVALID_INPUT", "提醒阈值必须是数字。") from exc
        window_raw = condition.get("window")
        window = None if window_raw is None else str(window_raw).strip()
        if window not in {None, "24h", "intraday"}:
            raise UiServiceError("INVALID_INPUT", "涨跌幅窗口仅支持 24h 或 intraday。")
        return {"type": type_value, "operator": operator, "value": value, "window": window}

    @staticmethod
    def _normalize_notification(notification: dict[str, Any] | None) -> dict[str, Any]:
        source = notification or {}
        channel = str(source.get("channel", "in_app")).strip() or "in_app"
        enabled = bool(source.get("enabled", True))
        return {"channel": channel, "enabled": enabled}

    def _as_market_profile(self, market: MarketProfile | str) -> MarketProfile:
        if isinstance(market, MarketProfile):
            return market
        text = str(market).strip()
        for candidate in MarketProfile:
            if candidate.value == text:
                return candidate
        raise UiServiceError("INVALID_INPUT", f"不支持的市场: {market}")

    @staticmethod
    def _is_triggered(*, condition: dict[str, Any], quote: dict[str, Any]) -> bool:
        condition_type = condition["type"]
        operator = condition["operator"]
        value = float(condition["value"])
        if condition_type == "price_threshold":
            current_price = float(quote["current_price"])
            if operator == "above":
                return current_price >= value
            return current_price <= value

        percent_change = PriceAlertService._quote_percent_change(condition=condition, quote=quote)
        if operator == "up_by":
            return percent_change >= value
        return percent_change <= -value

    @staticmethod
    def _quote_percent_change(*, condition: dict[str, Any], quote: dict[str, Any]) -> float:
        window = condition.get("window")
        if window == "24h":
            if "percent_change_24h" in quote:
                return float(quote["percent_change_24h"])
            return float(quote["percent_change"])
        if window == "intraday":
            if "percent_change_intraday" in quote:
                return float(quote["percent_change_intraday"])
            return float(quote["percent_change"])
        return float(quote["percent_change"])

    @staticmethod
    def _render_triggered_message(item: PriceAlert, quote: dict[str, Any]) -> str:
        code = item.instrument_code
        if item.condition["type"] == "price_threshold":
            current_price = float(quote["current_price"])
            return f"{code} 已触发价格提醒，当前价格 {current_price:.2f}。"
        percent_change = PriceAlertService._quote_percent_change(condition=item.condition, quote=quote)
        return f"{code} 已触发涨跌幅提醒，当前变动 {percent_change:.2f}%。"

    def _next_alert_id(self) -> str:
        self._seq += 1
        return f"alert-{self._seq}"

    def _now_iso(self) -> str:
        value = self._now_provider().astimezone(UTC).replace(microsecond=0)
        return value.isoformat().replace("+00:00", "Z")
