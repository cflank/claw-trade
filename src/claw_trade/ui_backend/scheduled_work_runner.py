from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Mapping


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
        data_refresh_permission_checker: Callable[[], None] | None = None,
    ) -> None:
        self._price_alert_scan_service = price_alert_scan_service
        self._scheduler_service = scheduler_service
        self._selection_data_refresh_runner = selection_data_refresh_runner
        self._data_maintenance_runner = data_maintenance_runner
        self._data_refresh_permission_checker = data_refresh_permission_checker
        self._latest_results: dict[str, dict[str, Any]] = {}

    def handle_wake(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        if kind == "price_alert_scan":
            return self._handle_price_alert_scan(payload, kind=kind)
        if kind == "scheduled_report":
            return self._handle_scheduled_report(payload, kind=kind)
        if kind == "selection_data_refresh":
            return self._handle_selection_data_refresh(payload, kind=kind)
        if kind == "data_maintenance":
            return self._handle_data_maintenance(payload, kind=kind)
        raise ScheduledWorkRunnerError("INVALID_INPUT", "不支持的定时任务唤醒类型。")

    def latest_results_for_user(self) -> dict[str, Any]:
        return {"items": list(self._latest_results.values())}

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
            result = {
                "kind": kind,
                "bucketKey": bucket_key,
                "cronRunId": cron_run_id,
                "status": "error",
                "error": {"message": str(exc) or type(exc).__name__},
            }
            self._latest_results[kind] = result
            return result
        result = {
            "kind": kind,
            "bucketKey": bucket_key,
            "cronRunId": cron_run_id,
            "status": "ok",
            "summary": asdict(summary) if is_dataclass(summary) else dict(summary),
        }
        self._latest_results[kind] = result
        return result

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
        payload_out = {"kind": kind, "scheduledReportId": scheduled_report_id, "cronRunId": cron_run_id, "status": "ok", **result}
        self._latest_results[f"{kind}:{scheduled_report_id}"] = _scrub_runtime_objects(payload_out)
        return payload_out

    def _handle_selection_data_refresh(self, payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        if self._selection_data_refresh_runner is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "selection_data_refresh 暂未配置执行器。")
        self._assert_data_refresh_allowed()
        reason = str(payload.get("reason") or "").strip() or "scheduled_data_refresh"
        result = self._selection_data_refresh_runner.run_automatic_refresh_once(reason=reason)
        result_payload = _plain_mapping(result)
        result_status = str(result_payload.get("status") or "").strip() or "unknown"
        result_out = {
            "kind": kind,
            "reason": reason,
            "cronRunId": str(payload.get("cronRunId") or ""),
            "status": result_status,
            "result": result_payload,
        }
        self._latest_results[kind] = result_out
        if result_status == "failed":
            raise ScheduledWorkRunnerError("SELECTION_DATA_REFRESH_FAILED", _selection_refresh_failure_message(result_payload))
        return result_out

    def _handle_data_maintenance(self, payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        if self._data_maintenance_runner is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "data_maintenance 暂未配置执行器。")
        self._assert_data_refresh_allowed()
        market = str(payload.get("market") or "").strip()
        job_kind = str(payload.get("jobKind") or "").strip()
        cron_run_id = str(payload.get("cronRunId") or "").strip() or None
        maintenance_job_id = str(payload.get("maintenanceJobId") or "").strip() or None
        if not market or not job_kind:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "data_maintenance 唤醒缺少 market 或 jobKind。")
        try:
            job = self._data_maintenance_runner.run(
                market=market,
                job_kind=job_kind,
                cron_run_id=cron_run_id,
                maintenance_job_id=maintenance_job_id,
            )
        except Exception as exc:
            self._latest_results[f"{kind}:{market}:{job_kind}"] = {
                "kind": kind,
                "market": market,
                "jobKind": job_kind,
                "cronRunId": cron_run_id or "",
                "maintenanceJobId": maintenance_job_id or "",
                "status": "error",
                "error": {"message": str(exc) or type(exc).__name__},
            }
            raise ScheduledWorkRunnerError("DATA_MAINTENANCE_FAILED", str(exc) or type(exc).__name__) from exc
        status = str(getattr(job, "status", "") or "")
        if status and status != "succeeded":
            message = str(getattr(job, "error", "") or f"data maintenance ended with status {status}")
            self._latest_results[f"{kind}:{market}:{job_kind}"] = {
                "kind": kind,
                "market": market,
                "jobKind": job_kind,
                "cronRunId": cron_run_id or "",
                "maintenanceJobId": str(getattr(job, "job_id", maintenance_job_id or "")),
                "status": status,
                "error": {"message": message},
            }
            raise ScheduledWorkRunnerError("DATA_MAINTENANCE_FAILED", message)
        result_out = {
            "kind": kind,
            "market": market,
            "jobKind": job_kind,
            "cronRunId": cron_run_id or "",
            "maintenanceJobId": str(getattr(job, "job_id", maintenance_job_id or "")),
            "status": "ok",
            "maintenanceStatus": status or "succeeded",
        }
        self._latest_results[f"{kind}:{market}:{job_kind}"] = result_out
        return result_out

    def _assert_data_refresh_allowed(self) -> None:
        if self._data_refresh_permission_checker is None:
            return
        try:
            self._data_refresh_permission_checker()
        except PermissionError as exc:
            raise ScheduledWorkRunnerError("LICENSE_BLOCKED", str(exc)) from exc


def _plain_mapping(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {"status": str(getattr(value, "status", "") or "")}


def _scrub_runtime_objects(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if is_dataclass(value):
            out[str(key)] = asdict(value)
        elif isinstance(value, Mapping):
            out[str(key)] = dict(value)
        else:
            out[str(key)] = value
    return out


def _selection_refresh_failure_message(result: Mapping[str, Any]) -> str:
    error_code = str(result.get("error_code") or "").strip()
    reason = str(result.get("reason") or "").strip()
    if error_code and reason:
        return f"selection_data_refresh failed: {error_code} ({reason})"
    if error_code:
        return f"selection_data_refresh failed: {error_code}"
    if reason:
        return f"selection_data_refresh failed: {reason}"
    return "selection_data_refresh failed"
