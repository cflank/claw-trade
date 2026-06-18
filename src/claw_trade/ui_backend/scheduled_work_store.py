from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from claw_trade.ui_contracts.enums import MarketProfile


@dataclass
class PriceAlert:
    id: str
    instrument_code: str
    instrument_name: str | None
    market: MarketProfile
    condition: dict[str, Any]
    condition_version: int
    notification: dict[str, Any]
    state: str
    scan_bucket: str
    last_checked_at: str | None
    triggered_at: str | None
    last_error_message: str | None
    last_quote_evidence_ref: str | None
    last_scan_run_id: str | None
    notification_dedupe_key: str | None
    last_notification_result: dict[str, Any] | None
    created_at: str
    updated_at: str


@dataclass
class PriceAlertScanBucket:
    bucket_key: str
    market: MarketProfile
    frequency: str
    enabled: bool
    openclaw_cron_job_id: str | None
    last_scan_run_id: str | None
    last_scan_summary: dict[str, Any] | None
    last_error_message: str | None
    skipped_reason: str | None
    created_at: str
    updated_at: str


class ScheduledWorkStore(Protocol):
    def save_price_alert(self, alert: PriceAlert) -> None: ...

    def get_price_alert(self, alert_id: str) -> PriceAlert | None: ...

    def list_price_alerts(self, *, market: MarketProfile | None = None, states: set[str] | None = None) -> list[PriceAlert]: ...

    def save_scan_bucket(self, bucket: PriceAlertScanBucket) -> None: ...

    def get_scan_bucket(self, bucket_key: str) -> PriceAlertScanBucket | None: ...


class InMemoryScheduledWorkStore:
    def __init__(self) -> None:
        self._price_alerts: dict[str, PriceAlert] = {}
        self._scan_buckets: dict[str, PriceAlertScanBucket] = {}

    def save_price_alert(self, alert: PriceAlert) -> None:
        self._price_alerts[alert.id] = replace(alert)

    def get_price_alert(self, alert_id: str) -> PriceAlert | None:
        alert = self._price_alerts.get(alert_id)
        return replace(alert) if alert is not None else None

    def list_price_alerts(self, *, market: MarketProfile | None = None, states: set[str] | None = None) -> list[PriceAlert]:
        alerts = list(self._price_alerts.values())
        if market is not None:
            alerts = [alert for alert in alerts if alert.market == market]
        if states is not None:
            alerts = [alert for alert in alerts if alert.state in states]
        return [replace(alert) for alert in alerts]

    def save_scan_bucket(self, bucket: PriceAlertScanBucket) -> None:
        self._scan_buckets[bucket.bucket_key] = replace(bucket)

    def get_scan_bucket(self, bucket_key: str) -> PriceAlertScanBucket | None:
        bucket = self._scan_buckets.get(bucket_key)
        return replace(bucket) if bucket is not None else None


class JsonScheduledWorkStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def save_price_alert(self, alert: PriceAlert) -> None:
        payload = self._load()
        payload["price_alerts"][alert.id] = _price_alert_to_payload(alert)
        self._save(payload)

    def get_price_alert(self, alert_id: str) -> PriceAlert | None:
        payload = self._load()
        raw = payload["price_alerts"].get(alert_id)
        if raw is None:
            return None
        return _price_alert_from_payload(raw)

    def list_price_alerts(self, *, market: MarketProfile | None = None, states: set[str] | None = None) -> list[PriceAlert]:
        payload = self._load()
        alerts = [_price_alert_from_payload(raw) for raw in payload["price_alerts"].values()]
        if market is not None:
            alerts = [alert for alert in alerts if alert.market == market]
        if states is not None:
            alerts = [alert for alert in alerts if alert.state in states]
        return alerts

    def save_scan_bucket(self, bucket: PriceAlertScanBucket) -> None:
        payload = self._load()
        payload["scan_buckets"][bucket.bucket_key] = _scan_bucket_to_payload(bucket)
        self._save(payload)

    def get_scan_bucket(self, bucket_key: str) -> PriceAlertScanBucket | None:
        payload = self._load()
        raw = payload["scan_buckets"].get(bucket_key)
        if raw is None:
            return None
        return _scan_bucket_from_payload(raw)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return _empty_payload()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("root is not an object")
            price_alerts = raw.get("price_alerts")
            scan_buckets = raw.get("scan_buckets")
            if not isinstance(price_alerts, dict) or not isinstance(scan_buckets, dict):
                raise TypeError("missing scheduled work collections")
            return {"price_alerts": price_alerts, "scan_buckets": scan_buckets}
        except Exception as exc:
            raise RuntimeError("scheduled_work_store_load_failed") from exc

    def _save(self, payload: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            json.dump(payload, tmp, ensure_ascii=False, sort_keys=True)
            tmp.write("\n")
        tmp_path.replace(self._path)


def _empty_payload() -> dict[str, dict[str, Any]]:
    return {"price_alerts": {}, "scan_buckets": {}}


def _price_alert_to_payload(alert: PriceAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "instrument_code": alert.instrument_code,
        "instrument_name": alert.instrument_name,
        "market": alert.market.value,
        "condition": alert.condition,
        "condition_version": alert.condition_version,
        "notification": alert.notification,
        "state": alert.state,
        "scan_bucket": alert.scan_bucket,
        "last_checked_at": alert.last_checked_at,
        "triggered_at": alert.triggered_at,
        "last_error_message": alert.last_error_message,
        "last_quote_evidence_ref": alert.last_quote_evidence_ref,
        "last_scan_run_id": alert.last_scan_run_id,
        "notification_dedupe_key": alert.notification_dedupe_key,
        "last_notification_result": alert.last_notification_result,
        "created_at": alert.created_at,
        "updated_at": alert.updated_at,
    }


def _price_alert_from_payload(raw: dict[str, Any]) -> PriceAlert:
    return PriceAlert(
        id=str(raw["id"]),
        instrument_code=str(raw["instrument_code"]),
        instrument_name=None if raw.get("instrument_name") is None else str(raw["instrument_name"]),
        market=MarketProfile(str(raw["market"])),
        condition=dict(raw["condition"]),
        condition_version=int(raw["condition_version"]),
        notification=dict(raw["notification"]),
        state=str(raw["state"]),
        scan_bucket=str(raw["scan_bucket"]),
        last_checked_at=None if raw.get("last_checked_at") is None else str(raw["last_checked_at"]),
        triggered_at=None if raw.get("triggered_at") is None else str(raw["triggered_at"]),
        last_error_message=None if raw.get("last_error_message") is None else str(raw["last_error_message"]),
        last_quote_evidence_ref=None if raw.get("last_quote_evidence_ref") is None else str(raw["last_quote_evidence_ref"]),
        last_scan_run_id=None if raw.get("last_scan_run_id") is None else str(raw["last_scan_run_id"]),
        notification_dedupe_key=None if raw.get("notification_dedupe_key") is None else str(raw["notification_dedupe_key"]),
        last_notification_result=None if raw.get("last_notification_result") is None else dict(raw["last_notification_result"]),
        created_at=str(raw["created_at"]),
        updated_at=str(raw["updated_at"]),
    )


def _scan_bucket_to_payload(bucket: PriceAlertScanBucket) -> dict[str, Any]:
    return {
        "bucket_key": bucket.bucket_key,
        "market": bucket.market.value,
        "frequency": bucket.frequency,
        "enabled": bucket.enabled,
        "openclaw_cron_job_id": bucket.openclaw_cron_job_id,
        "last_scan_run_id": bucket.last_scan_run_id,
        "last_scan_summary": bucket.last_scan_summary,
        "last_error_message": bucket.last_error_message,
        "skipped_reason": bucket.skipped_reason,
        "created_at": bucket.created_at,
        "updated_at": bucket.updated_at,
    }


def _scan_bucket_from_payload(raw: dict[str, Any]) -> PriceAlertScanBucket:
    return PriceAlertScanBucket(
        bucket_key=str(raw["bucket_key"]),
        market=MarketProfile(str(raw["market"])),
        frequency=str(raw["frequency"]),
        enabled=bool(raw["enabled"]),
        openclaw_cron_job_id=None if raw.get("openclaw_cron_job_id") is None else str(raw["openclaw_cron_job_id"]),
        last_scan_run_id=None if raw.get("last_scan_run_id") is None else str(raw["last_scan_run_id"]),
        last_scan_summary=None if raw.get("last_scan_summary") is None else dict(raw["last_scan_summary"]),
        last_error_message=None if raw.get("last_error_message") is None else str(raw["last_error_message"]),
        skipped_reason=None if raw.get("skipped_reason") is None else str(raw["skipped_reason"]),
        created_at=str(raw["created_at"]),
        updated_at=str(raw["updated_at"]),
    )
