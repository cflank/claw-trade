from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


class ScheduledWorkRunnerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScheduledWorkRunner:
    def __init__(self, *, price_alert_scan_service: Any) -> None:
        self._price_alert_scan_service = price_alert_scan_service

    def handle_wake(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        if kind != "price_alert_scan":
            raise ScheduledWorkRunnerError("INVALID_INPUT", "不支持的定时任务唤醒类型。")
        bucket_key = str(payload.get("bucketKey") or "").strip()
        cron_run_id = str(payload.get("cronRunId") or "").strip()
        if not bucket_key or not cron_run_id:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "价格提醒扫描唤醒缺少 bucketKey 或 cronRunId。")
        try:
            summary = self._price_alert_scan_service.scan_bucket(bucket_key, cron_run_id=cron_run_id)
        except Exception as exc:
            return {
                "kind": kind,
                "bucketKey": bucket_key,
                "cronRunId": cron_run_id,
                "status": "error",
                "error": {"message": str(exc) or type(exc).__name__},
            }
        return {
            "kind": kind,
            "bucketKey": bucket_key,
            "cronRunId": cron_run_id,
            "status": "ok",
            "summary": asdict(summary) if is_dataclass(summary) else dict(summary),
        }
