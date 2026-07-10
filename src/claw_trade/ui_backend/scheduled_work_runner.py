from __future__ import annotations

from dataclasses import asdict, is_dataclass
from threading import Lock
from typing import Any, Callable, Mapping

from claw_trade.selection.models import SelectionMarket, SelectionProfile


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
        self._latest_results_lock = Lock()
        self._data_maintenance_locks: dict[tuple[str, str], Lock] = {}
        self._data_maintenance_locks_guard = Lock()

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
        with self._latest_results_lock:
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
            self._save_latest_result(kind, result)
            return result
        result = {
            "kind": kind,
            "bucketKey": bucket_key,
            "cronRunId": cron_run_id,
            "status": "ok",
            "summary": asdict(summary) if is_dataclass(summary) else dict(summary),
        }
        self._save_latest_result(kind, result)
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
        self._save_latest_result(f"{kind}:{scheduled_report_id}", _scrub_runtime_objects(payload_out))
        return payload_out

    def _handle_selection_data_refresh(self, payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        if self._selection_data_refresh_runner is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "selection_data_refresh 暂未配置执行器。")
        self._assert_data_refresh_allowed()
        reason = str(payload.get("reason") or "").strip() or "scheduled_data_refresh"
        if self._data_maintenance_runner is not None:
            return self._handle_selection_data_refresh_via_data_maintenance(payload, kind=kind, reason=reason)
        result = self._selection_data_refresh_runner.run_automatic_refresh_once(
            reason=reason,
            force_refresh=_truthy(payload.get("forceRefresh")),
        )
        result_payload = _plain_mapping(result)
        result_status = str(result_payload.get("status") or "").strip() or "unknown"
        result_out = {
            "kind": kind,
            "reason": reason,
            "cronRunId": str(payload.get("cronRunId") or ""),
            "status": result_status,
            "result": result_payload,
        }
        self._save_latest_result(kind, result_out)
        if result_status == "failed":
            raise ScheduledWorkRunnerError("SELECTION_DATA_REFRESH_FAILED", _selection_refresh_failure_message(result_payload))
        return result_out

    def _handle_selection_data_refresh_via_data_maintenance(
        self,
        payload: Mapping[str, Any],
        *,
        kind: str,
        reason: str,
    ) -> dict[str, Any]:
        cron_run_id = str(payload.get("cronRunId") or "")
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for market, job_kind in (("CN_A", "eod"), ("CRYPTO", "kline-refresh")):
            lock = self._data_maintenance_lock_for(market=market, job_kind=job_kind)
            with lock:
                try:
                    results.append(
                        self._run_data_maintenance(
                            kind="data_maintenance",
                            market=market,
                            job_kind=job_kind,
                            cron_run_id=cron_run_id or None,
                            maintenance_job_id=None,
                        )
                    )
                except ScheduledWorkRunnerError as exc:
                    errors.append({"market": market, "jobKind": job_kind, "code": exc.code, "message": exc.message})
        status = "error" if errors else "ok"
        result_out: dict[str, Any] = {
            "kind": kind,
            "reason": reason,
            "cronRunId": cron_run_id,
            "status": status,
            "dataMaintenance": results,
        }
        if errors:
            result_out["errors"] = errors
        self._save_latest_result(kind, result_out)
        if errors:
            raise ScheduledWorkRunnerError("SELECTION_DATA_REFRESH_FAILED", errors[0]["message"])
        return result_out

    def _handle_data_maintenance(self, payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
        if self._data_maintenance_runner is None:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "data_maintenance 暂未配置执行器。")
        self._assert_data_refresh_allowed()
        market = str(payload.get("market") or "").strip()
        job_kind = str(payload.get("jobKind") or "").strip()
        cron_run_id = str(payload.get("cronRunId") or "").strip() or None
        maintenance_job_id = str(payload.get("maintenanceJobId") or "").strip() or None
        ignore_cached_empty = _truthy(payload.get("ignoreCachedEmpty")) and _is_manual_data_maintenance_retry(cron_run_id)
        if not market or not job_kind:
            raise ScheduledWorkRunnerError("INVALID_INPUT", "data_maintenance 唤醒缺少 market 或 jobKind。")
        data_maintenance_lock = self._data_maintenance_lock_for(market=market, job_kind=job_kind)
        with data_maintenance_lock:
            return self._run_data_maintenance(
                kind=kind,
                market=market,
                job_kind=job_kind,
                cron_run_id=cron_run_id,
                maintenance_job_id=maintenance_job_id,
                ignore_cached_empty=ignore_cached_empty,
            )

    def _data_maintenance_lock_for(self, *, market: str, job_kind: str) -> Lock:
        key = (market.strip().upper(), job_kind.strip())
        with self._data_maintenance_locks_guard:
            lock = self._data_maintenance_locks.get(key)
            if lock is None:
                lock = Lock()
                self._data_maintenance_locks[key] = lock
            return lock

    def _run_data_maintenance(
        self,
        *,
        kind: str,
        market: str,
        job_kind: str,
        cron_run_id: str | None,
        maintenance_job_id: str | None,
        ignore_cached_empty: bool = False,
    ) -> dict[str, Any]:
        try:
            run_kwargs: dict[str, Any] = {
                "market": market,
                "job_kind": job_kind,
                "cron_run_id": cron_run_id,
                "maintenance_job_id": maintenance_job_id,
            }
            if ignore_cached_empty:
                run_kwargs["ignore_cached_empty"] = True
            job = self._data_maintenance_runner.run(**run_kwargs)
        except Exception as exc:
            self._save_latest_result(f"{kind}:{market}:{job_kind}", {
                "kind": kind,
                "market": market,
                "jobKind": job_kind,
                "cronRunId": cron_run_id or "",
                "maintenanceJobId": maintenance_job_id or "",
                "status": "error",
                "error": {"message": str(exc) or type(exc).__name__},
            })
            raise ScheduledWorkRunnerError("DATA_MAINTENANCE_FAILED", str(exc) or type(exc).__name__) from exc
        status = str(getattr(job, "status", "") or "")
        if status and status != "succeeded":
            message = str(getattr(job, "error", "") or f"data maintenance ended with status {status}")
            self._save_latest_result(f"{kind}:{market}:{job_kind}", {
                "kind": kind,
                "market": market,
                "jobKind": job_kind,
                "cronRunId": cron_run_id or "",
                "maintenanceJobId": str(getattr(job, "job_id", maintenance_job_id or "")),
                "status": status,
                "error": {"message": message},
            })
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
        input_total = _int_or_none(_mapping_value(getattr(job, "cursor", None), "input_total"))
        if input_total is not None:
            result_out["inputTotal"] = input_total
        selection_refresh = self._refresh_selection_after_data_maintenance(market=market, job=job)
        if selection_refresh is not None:
            result_out["selectionRefresh"] = selection_refresh
            if _selection_refresh_failed(selection_refresh):
                message = _selection_refresh_error_message(selection_refresh)
                result_out["status"] = "selection_refresh_failed"
                result_out["error"] = {"message": message}
                self._save_latest_result(f"{kind}:{market}:{job_kind}", result_out)
                raise ScheduledWorkRunnerError("SELECTION_DATA_REFRESH_FAILED", message)
        self._save_latest_result(f"{kind}:{market}:{job_kind}", result_out)
        return result_out

    def _refresh_selection_after_data_maintenance(self, *, market: str, job: Any) -> dict[str, Any] | None:
        if self._selection_data_refresh_runner is None:
            return None
        target = _selection_target_for_market(market)
        if target is None:
            return None
        selection_market, selection_profile = target
        try:
            result = self._selection_data_refresh_runner.run_automatic_refresh_once(
                reason="data_maintenance_completed",
                market=selection_market,
                profile=selection_profile,
                force_refresh=_maintenance_changed_raw_data(job),
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": {"message": str(exc) or type(exc).__name__}}
        payload = _plain_mapping(result)
        status = str(payload.get("status") or "").strip()
        if status == "failed":
            payload["error"] = {"message": _selection_refresh_failure_message(payload)}
        return payload

    def _save_latest_result(self, key: str, value: dict[str, Any]) -> None:
        with self._latest_results_lock:
            self._latest_results[key] = value

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


def _selection_refresh_failed(result: Mapping[str, Any]) -> bool:
    return str(result.get("status") or "").strip() in {"failed", "error"}


def _selection_refresh_error_message(result: Mapping[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, Mapping):
        message = str(error.get("message") or "").strip()
        if message:
            return message
    return _selection_refresh_failure_message(result)


def _selection_target_for_market(market: str) -> tuple[SelectionMarket, SelectionProfile] | None:
    normalized = market.strip().upper()
    if normalized == SelectionMarket.CN_A.value:
        return SelectionMarket.CN_A, SelectionProfile.CN_A
    if normalized == SelectionMarket.CRYPTO.value:
        return SelectionMarket.CRYPTO, SelectionProfile.CRYPTO
    return None


def _maintenance_changed_raw_data(job: Any) -> bool:
    stats = getattr(job, "stats", None)
    return any(
        (_int_or_none(_mapping_value(stats, key)) or 0) > 0
        for key in ("dataset_refs", "raw_refs", "remote_success")
    )


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _is_manual_data_maintenance_retry(cron_run_id: str | None) -> bool:
    return str(cron_run_id or "").startswith("manual-")
