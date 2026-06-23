from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Callable

from claw_trade.instruments.resolver import resolve_instrument_identity
from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore, PriceAlert, ScheduledWorkStore
from claw_trade.ui_backend.scheduled_work_store import PriceAlertScanBucket
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import PriceAlertForUser, to_price_alert_for_user
from claw_trade.workflow.report_request_factory import report_display_name

_PRICE_ALERT_SCAN_INTERVAL_MS = 180_000
_PRICE_ALERT_SCAN_FREQUENCY = "3m"
_PRICE_ALERT_SCAN_AGENT_ID = "price_alert_scan_worker"


class UiServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PriceAlertService:
    def __init__(
        self,
        *,
        quote_provider: Callable[[str, MarketProfile], dict[str, Any]],
        store: ScheduledWorkStore | None = None,
        cron_adapter: OpenClawCronAdapter | None = None,
        notifier: Callable[[str, dict[str, Any]], Any] | None = None,
        in_app_notifier: Callable[[str, str], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._quote_provider = quote_provider
        self._store = store or InMemoryScheduledWorkStore()
        self._cron_adapter = cron_adapter
        self._notifier = notifier or (lambda _text, _notification: None)
        self._in_app_notifier = in_app_notifier or (lambda _alert_id, _text: None)
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._idempotency: dict[str, Any] = {}
        self._seq = self._highest_alert_seq()
        self._mutation_lock = Lock()

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
        with self._mutation_lock:
            cached = self._idempotency.get(request_id)
            if cached is not None:
                return cached
            market_value = self._as_market_profile(market)
            identity = resolve_instrument_identity(instrument_code, market_hint=market_value.value)
            market_value = MarketProfile(identity.profile)
            if market_value == MarketProfile.HK:
                raise UiServiceError("INVALID_INPUT", "第一版暂不支持港股价格提醒。")
            normalized_condition = self._normalize_condition(condition)
            now_iso = self._now_iso()
            scan_bucket = f"{market_value.value}:{_PRICE_ALERT_SCAN_FREQUENCY}"
            self._ensure_scan_bucket(scan_bucket, market=market_value, now_iso=now_iso)
            item = PriceAlert(
                id=self._next_alert_id(),
                instrument_code=identity.ticker,
                instrument_name=instrument_name or report_display_name(identity.ticker, identity.profile),
                market=market_value,
                condition=normalized_condition,
                condition_version=1,
                notification=self._normalize_notification(notification),
                state="active",
                scan_bucket=scan_bucket,
                last_checked_at=None,
                triggered_at=None,
                last_error_message=None,
                last_quote_evidence_ref=None,
                last_scan_run_id=None,
                notification_dedupe_key=None,
                last_notification_result=None,
                created_at=now_iso,
                updated_at=now_iso,
            )
            self._store.save_price_alert(item)
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
        self._store.save_price_alert(item)
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
        self._store.save_price_alert(item)
        dto = to_price_alert_for_user(item)
        self._idempotency[request_id] = dto
        return dto

    def delete_price_alert(self, *, request_id: str, price_alert_id: str) -> dict[str, Any]:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._store.get_price_alert(price_alert_id)
        if item is None:
            raise UiServiceError("ALERT_NOT_FOUND", "价格提醒不存在。")
        item.state = "deleted"
        item.updated_at = self._now_iso()
        self._store.save_price_alert(item)
        payload = {"deleted": True, "priceAlertId": item.id}
        self._idempotency[request_id] = payload
        return payload

    def run_price_alert_now(self, *, request_id: str, price_alert_id: str) -> dict[str, Any]:
        return self.evaluate_price_alert(price_alert_id=price_alert_id, request_id=request_id)

    def disable_openclaw_scan_crons(self) -> dict[str, Any]:
        if self._cron_adapter is None:
            return {"disabledJobIds": [], "clearedBucketKeys": [], "errors": []}

        disabled_job_ids: list[str] = []
        cleared_bucket_keys: list[str] = []
        errors: list[str] = []
        seen_job_ids: set[str] = set()

        try:
            jobs = _cron_jobs_from_payload(self._cron_adapter.list_jobs({"namePrefix": "price-alert-scan"}))
        except Exception as exc:  # noqa: BLE001
            jobs = []
            errors.append(str(exc) or type(exc).__name__)

        for job in jobs:
            job_id = _cron_job_id(job)
            if not job_id or job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            try:
                self._cron_adapter.update_job(job_id=job_id, patch={"enabled": False})
                disabled_job_ids.append(job_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc) or type(exc).__name__)

        for bucket in self._store.list_scan_buckets():
            job_id = bucket.openclaw_cron_job_id
            if job_id and job_id not in seen_job_ids:
                seen_job_ids.add(job_id)
                try:
                    self._cron_adapter.update_job(job_id=job_id, patch={"enabled": False})
                    disabled_job_ids.append(job_id)
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc) or type(exc).__name__)
                    continue
            if job_id:
                bucket.openclaw_cron_job_id = None
                bucket.updated_at = self._now_iso()
                self._store.save_scan_bucket(bucket)
                cleared_bucket_keys.append(bucket.bucket_key)

        return {"disabledJobIds": disabled_job_ids, "clearedBucketKeys": cleared_bucket_keys, "errors": errors}

    def evaluate_price_alert(self, *, price_alert_id: str, request_id: str | None = None) -> dict[str, Any]:
        if request_id:
            cached = self._idempotency.get(request_id)
            if cached is not None:
                return cached
        item = self._store.get_price_alert(price_alert_id)
        if item is None or item.state in {"deleted", "closed"}:
            raise UiServiceError("ALERT_NOT_FOUND", "价格提醒不存在。")
        if item.state == "paused":
            payload = {"alert": to_price_alert_for_user(item), "triggered": False, "message": "提醒已暂停，暂不检查。"}
            if request_id:
                self._idempotency[request_id] = payload
            return payload

        item.state = "checking"
        item.updated_at = self._now_iso()
        self._store.save_price_alert(item)
        try:
            quote = self._quote_provider(item.instrument_code, item.market)
            triggered = self._is_triggered(condition=item.condition, quote=quote)
            now_iso = self._now_iso()
            item.last_quote_evidence_ref = self._quote_evidence_ref(quote)
            if not triggered:
                item.state = "active"
                item.last_checked_at = now_iso
                item.last_error_message = None
                item.updated_at = now_iso
                self._store.save_price_alert(item)
                payload = {"alert": to_price_alert_for_user(item), "triggered": False}
            else:
                message = self._render_triggered_message(item, quote)
                notification_result = self._deliver_notification(item, message=message, quote=quote)
                item.state = "closed"
                item.triggered_at = now_iso
                item.last_checked_at = now_iso
                item.last_error_message = None
                item.notification_dedupe_key = str(notification_result["dedupe_key"])
                item.last_notification_result = notification_result
                item.updated_at = now_iso
                self._store.save_price_alert(item)
                payload = {"alert": to_price_alert_for_user(item), "triggered": True, "message": message}
        except UiServiceError:
            raise
        except Exception as exc:
            item.state = "error"
            item.last_checked_at = self._now_iso()
            item.last_error_message = "价格提醒检查失败，请稍后重试。"
            item.updated_at = self._now_iso()
            self._store.save_price_alert(item)
            raise UiServiceError("DATASOURCE_TEST_FAILED", "价格提醒检查失败，请稍后重试。") from exc

        if request_id:
            self._idempotency[request_id] = payload
        return payload

    def get_price_alert(self, price_alert_id: str) -> PriceAlertForUser:
        return to_price_alert_for_user(self._get_alert_or_raise(price_alert_id))

    def _get_alert_or_raise(self, price_alert_id: str) -> PriceAlert:
        item = self._store.get_price_alert(price_alert_id)
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
        return PriceAlertService.condition_is_triggered(condition=condition, quote=quote)

    @staticmethod
    def condition_is_triggered(*, condition: dict[str, Any], quote: dict[str, Any]) -> bool:
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

    @staticmethod
    def _quote_evidence_ref(quote: dict[str, Any]) -> str | None:
        value = quote.get("evidence_ref")
        return None if value is None else str(value)

    def _deliver_notification(self, item: PriceAlert, *, message: str, quote: dict[str, Any]) -> dict[str, Any]:
        dedupe_key = self._notification_dedupe_key(item, quote=quote)
        if item.notification_dedupe_key == dedupe_key and item.last_notification_result is not None:
            return dict(item.last_notification_result)
        channel = str(item.notification.get("channel") or "in_app")
        enabled = bool(item.notification.get("enabled", True))
        if not enabled or channel == "in_app":
            self._in_app_notifier(item.id, message)
            return {"channel": "in_app", "delivered": True, "dedupe_key": dedupe_key}
        try:
            result = self._notifier(message, {**item.notification, "dedupeKey": dedupe_key})
        except Exception:
            self._in_app_notifier(item.id, message)
            return {
                "channel": "in_app",
                "delivered": True,
                "fallback_from": channel,
                "channel_delivered": False,
                "dedupe_key": dedupe_key,
            }
        if isinstance(result, dict):
            sent = bool(result.get("sent", result.get("ok", True)))
            if not sent:
                self._in_app_notifier(item.id, message)
                return {
                    "channel": "in_app",
                    "delivered": True,
                    "fallback_from": channel,
                    "channel_delivered": False,
                    "dedupe_key": dedupe_key,
                }
            return {"channel": channel, "delivered": True, "dedupe_key": dedupe_key, **result}
        return {"channel": channel, "delivered": True, "dedupe_key": dedupe_key}

    @staticmethod
    def _notification_dedupe_key(item: PriceAlert, *, quote: dict[str, Any]) -> str:
        quote_timestamp = str(quote.get("quote_timestamp") or item.last_checked_at or "unknown")
        trigger_side = f"{item.condition.get('type')}:{item.condition.get('operator')}"
        return f"{item.id}:{item.condition_version}:{quote_timestamp}:{trigger_side}"

    def _ensure_scan_bucket(self, bucket_key: str, *, market: MarketProfile, now_iso: str) -> None:
        bucket = self._store.get_scan_bucket(bucket_key)
        if bucket is None:
            enabled = market in {MarketProfile.CRYPTO, MarketProfile.CN_A}
            bucket = PriceAlertScanBucket(
                bucket_key=bucket_key,
                market=market,
                frequency=_PRICE_ALERT_SCAN_FREQUENCY,
                enabled=enabled,
                openclaw_cron_job_id=None,
                last_scan_run_id=None,
                last_scan_summary=None,
                last_error_message=None,
                skipped_reason=None if enabled else "quote_calendar_live_evidence_required",
                created_at=now_iso,
                updated_at=now_iso,
            )
        self._store.save_scan_bucket(bucket)

    def _highest_alert_seq(self) -> int:
        highest = 0
        for alert in self._store.list_price_alerts():
            prefix, sep, suffix = alert.id.partition("-")
            if prefix != "alert" or sep != "-":
                continue
            try:
                highest = max(highest, int(suffix))
            except ValueError:
                continue
        return highest

    def _next_alert_id(self) -> str:
        self._seq += 1
        return f"alert-{self._seq}"

    @staticmethod
    def _scan_cron_message(bucket_key: str) -> str:
        return (
            "Call `claw-trade-scheduled-work-wake` exactly once with this JSON payload and no other tool calls:\n"
            f'{{"kind":"price_alert_scan","bucketKey":"{bucket_key}","cronRunId":"auto"}}\n'
            "Do not compare prices, write investment commentary, or fabricate quote results."
        )

    def _now_iso(self) -> str:
        value = self._now_provider().astimezone(UTC).replace(microsecond=0)
        return value.isoformat().replace("+00:00", "Z")


def _cron_jobs_from_payload(payload: Any) -> list[Mapping[str, Any] | str]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping) or isinstance(item, str)]
    if isinstance(payload, Mapping):
        for key in ("items", "jobs", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping) or isinstance(item, str)]
        nested = payload.get("result")
        if isinstance(nested, Mapping) or isinstance(nested, list):
            return _cron_jobs_from_payload(nested)
    return []


def _cron_job_id(job: Mapping[str, Any] | str) -> str | None:
    if isinstance(job, str):
        return job.strip() or None
    for key in ("jobId", "id", "openclawCronJobId", "name"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
