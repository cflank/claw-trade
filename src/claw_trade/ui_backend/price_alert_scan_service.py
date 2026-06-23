from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from typing import Any
from zoneinfo import ZoneInfo

from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.scheduled_work_store import PriceAlert, ScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile

_CN_A_TIMEZONE = ZoneInfo("Asia/Shanghai")
_LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class PriceAlertScanSummary:
    scan_run_id: str
    bucket_key: str
    cron_run_id: str
    active_alert_count: int
    grouped_quote_count: int
    triggered_alert_count: int
    skipped_alert_count: int
    failed_alert_count: int
    quote_evidence_refs: tuple[str, ...]
    started_at: str
    finished_at: str
    last_error_message: str | None = None


class PriceAlertScanService:
    def __init__(
        self,
        *,
        store: ScheduledWorkStore,
        quote_provider: Callable[[str, MarketProfile], dict[str, Any]],
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._quote_provider = quote_provider
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def scan_bucket(self, bucket_key: str, *, cron_run_id: str) -> PriceAlertScanSummary:
        started_at = self._now_iso()
        scan_run_id = f"price-alert-scan:{bucket_key}:{cron_run_id}"
        bucket = self._store.get_scan_bucket(bucket_key)
        if bucket is None:
            raise RuntimeError("price_alert_scan_bucket_not_found")
        active_alerts = [
            alert for alert in self._store.list_price_alerts(market=bucket.market, states={"active", "error"}) if alert.scan_bucket == bucket_key
        ]
        if not bucket.enabled:
            summary = PriceAlertScanSummary(
                scan_run_id=scan_run_id,
                bucket_key=bucket_key,
                cron_run_id=cron_run_id,
                active_alert_count=len(active_alerts),
                grouped_quote_count=0,
                triggered_alert_count=0,
                skipped_alert_count=len(active_alerts),
                failed_alert_count=0,
                quote_evidence_refs=(),
                started_at=started_at,
                finished_at=self._now_iso(),
            )
            self._save_bucket_summary(bucket_key, summary)
            return summary
        if self._should_market_skip(bucket.market):
            summary = PriceAlertScanSummary(
                scan_run_id=scan_run_id,
                bucket_key=bucket_key,
                cron_run_id=cron_run_id,
                active_alert_count=len(active_alerts),
                grouped_quote_count=0,
                triggered_alert_count=0,
                skipped_alert_count=len(active_alerts),
                failed_alert_count=0,
                quote_evidence_refs=(),
                started_at=started_at,
                finished_at=self._now_iso(),
            )
            self._save_bucket_summary(bucket_key, summary)
            return summary

        grouped: dict[str, list[PriceAlert]] = {}
        for alert in active_alerts:
            grouped.setdefault(alert.instrument_code, []).append(alert)

        quote_evidence_refs: list[str] = []
        triggered_count = 0
        failed_count = 0
        for instrument_code, alerts in grouped.items():
            try:
                quote = self._quote_provider(instrument_code, bucket.market)
            except Exception:
                failed_count += len(alerts)
                for alert in alerts:
                    self._mark_alert_error(alert, scan_run_id=scan_run_id)
                continue
            evidence_ref = self._quote_evidence_ref(quote)
            if evidence_ref:
                quote_evidence_refs.append(evidence_ref)
            for alert in alerts:
                if self._apply_quote(alert, quote=quote, scan_run_id=scan_run_id):
                    triggered_count += 1

        summary = PriceAlertScanSummary(
            scan_run_id=scan_run_id,
            bucket_key=bucket_key,
            cron_run_id=cron_run_id,
            active_alert_count=len(active_alerts),
            grouped_quote_count=len(grouped),
            triggered_alert_count=triggered_count,
            skipped_alert_count=0,
            failed_alert_count=failed_count,
            quote_evidence_refs=tuple(dict.fromkeys(quote_evidence_refs)),
            started_at=started_at,
            finished_at=self._now_iso(),
        )
        self._save_bucket_summary(bucket_key, summary)
        return summary

    def _apply_quote(self, alert: PriceAlert, *, quote: dict[str, Any], scan_run_id: str) -> bool:
        now_iso = self._now_iso()
        evidence_ref = self._quote_evidence_ref(quote)
        triggered = PriceAlertService.condition_is_triggered(condition=alert.condition, quote=quote)
        alert.last_checked_at = now_iso
        alert.last_quote_evidence_ref = evidence_ref
        alert.last_scan_run_id = scan_run_id
        alert.last_error_message = None
        alert.updated_at = now_iso
        if triggered:
            alert.state = "closed"
            alert.triggered_at = now_iso
        else:
            alert.state = "active"
        self._store.save_price_alert(alert)
        return triggered

    def _mark_alert_error(self, alert: PriceAlert, *, scan_run_id: str) -> None:
        now_iso = self._now_iso()
        alert.state = "error"
        alert.last_checked_at = now_iso
        alert.last_scan_run_id = scan_run_id
        alert.last_error_message = "价格提醒检查失败，请稍后重试。"
        alert.updated_at = now_iso
        self._store.save_price_alert(alert)

    def _save_bucket_summary(self, bucket_key: str, summary: PriceAlertScanSummary) -> None:
        bucket = self._store.get_scan_bucket(bucket_key)
        if bucket is None:
            raise RuntimeError("price_alert_scan_bucket_not_found")
        bucket.last_scan_run_id = summary.scan_run_id
        bucket.last_scan_summary = asdict(summary)
        bucket.last_error_message = summary.last_error_message
        bucket.updated_at = summary.finished_at
        self._store.save_scan_bucket(bucket)

    def _should_market_skip(self, market: MarketProfile) -> bool:
        if market == MarketProfile.CN_A:
            return not _is_cn_a_trading_time(self._now())
        return False

    def _now(self) -> datetime:
        value = self._now_provider()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _now_iso(self) -> str:
        return self._now().replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _quote_evidence_ref(quote: dict[str, Any]) -> str | None:
        value = quote.get("evidence_ref")
        return None if value is None else str(value)


class PriceAlertScanScheduler:
    def __init__(
        self,
        *,
        store: ScheduledWorkStore,
        scan_service: PriceAlertScanService,
        legacy_cron_disabler: Callable[[], dict[str, Any]] | None = None,
        interval_seconds: float = 180.0,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._scan_service = scan_service
        self._legacy_cron_disabler = legacy_cron_disabler
        self._interval_seconds = interval_seconds
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._run_startup_maintenance()
            self._stop.clear()
            self._thread = Thread(target=self._loop, daemon=True, name="price-alert-scan-scheduler")
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_seconds)

    def run_once(self, *, reason: str = "scheduled") -> list[PriceAlertScanSummary]:
        summaries: list[PriceAlertScanSummary] = []
        for bucket in self._store.list_scan_buckets():
            if not bucket.enabled:
                continue
            summaries.append(self._scan_service.scan_bucket(bucket.bucket_key, cron_run_id=self._cron_run_id(reason)))
        return summaries

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.run_once(reason="local")
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("price alert scan scheduler failed: %s", str(exc) or type(exc).__name__)

    def _run_startup_maintenance(self) -> None:
        if self._legacy_cron_disabler is None:
            return
        try:
            result = self._legacy_cron_disabler()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("price alert legacy OpenClaw cron disable failed: %s", str(exc) or type(exc).__name__)
            return
        errors = result.get("errors") if isinstance(result, dict) else None
        if errors:
            _LOGGER.warning("price alert legacy OpenClaw cron disable errors: %s", errors)

    def _cron_run_id(self, reason: str) -> str:
        now = self._now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            now = now.replace(tzinfo=UTC)
        timestamp_ms = int(now.timestamp() * 1000)
        return f"price-alert-local:{reason}:{timestamp_ms}:{uuid.uuid4().hex}"


def _is_cn_a_trading_time(now_utc: datetime) -> bool:
    local = now_utc.astimezone(_CN_A_TIMEZONE)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return (9 * 60 + 30 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)
