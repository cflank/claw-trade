from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.scheduled_work_store import PriceAlert, ScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile

_CN_A_TIMEZONE = ZoneInfo("Asia/Shanghai")


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


def _is_cn_a_trading_time(now_utc: datetime) -> bool:
    local = now_utc.astimezone(_CN_A_TIMEZONE)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return (9 * 60 + 30 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)
