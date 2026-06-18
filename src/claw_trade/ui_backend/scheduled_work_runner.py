from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


class ScheduledWorkRunnerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScheduledWorkRunner:
    def __init__(
        self,
        *,
        price_alert_scan_service: Any | None = None,
        scheduler_service: Any | None = None,
        selection_data_refresh_runner: Any | None = None,
        data_maintenance_runner: Any | None = None,
    ) -> None:
        self._price_alert_scan_service = price_alert_scan_service
        self._scheduler_service = scheduler_service
        self._selection_data_refresh_runner = selection_data_refresh_runner
        self._data_maintenance_runner = data_maintenance_runner

    def handle_wake(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        if kind == "price_alert_scan":
            return self._handle_price_alert_scan(payload, kind=kind)
        if kind == "scheduled_report":
            return self._handle_scheduled_report(payload, kind=kind)
        if kind == "selection_data_refresh":
            return self._handle_later_runner(
                self._selection_data_refresh_runner,
                payload,
                "selection_data_refresh 暂未配置执行器。",
            )
        if kind == "data_maintenance":
            return self._handle_later_runner(
                self._data_maintenance_runner,
                payload,
                "data_maintenance 暂未配置执行器。",
            )
        raise ScheduledWorkRunnerError("INVALID_INPUT", "不支持的定时任务唤醒类型。")

    def _handle_price_alert_scan(self, payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        if self._price_alert_scan_service is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "价格提醒扫描执行器未配置。")
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

    def _handle_scheduled_report(self, payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        if self._scheduler_service is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "定时报告执行器未配置。")
        scheduled_report_id = str(payload.get("scheduledReportId") or "").strip()
        cron_run_id = str(payload.get("cronRunId") or "").strip()
        if not scheduled_report_id or not cron_run_id:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "定时报告唤醒缺少 scheduledReportId 或 cronRunId。")
        request_id = str(payload.get("requestId") or "").strip()
        if not request_id:
            request_id = f"scheduled-report:{scheduled_report_id}:{cron_run_id}"
        result = self._scheduler_service.handle_scheduled_report_cron_wake(
            request_id=request_id,
            scheduled_report_id=scheduled_report_id,
            cron_run_id=cron_run_id,
        )
        return {"kind": kind, "scheduledReportId": scheduled_report_id, "cronRunId": cron_run_id, "status": "ok", **result}

    @staticmethod
    def _handle_later_runner(runner: Any | None, payload: Mapping[str, Any], unsupported_message: str) -> dict[str, Any]:
        if runner is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", unsupported_message)
        return dict(runner.handle_wake(payload))
