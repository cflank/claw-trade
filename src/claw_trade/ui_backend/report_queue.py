from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread
from time import sleep
from typing import Any

from claw_trade.production import paths
from claw_trade.production.host_locks import hold_host_lock
from claw_trade.ui_backend.error_translator import (
    UserFacingFailure,
    translate_internal_error_for_user,
)
from claw_trade.ui_backend.progress_mapper import map_workflow_progress_to_ui_state
from claw_trade.ui_backend.task_costs import (
    TaskCostEstimate,
    TaskCostSnapshot,
    TaskTokenCostSummary,
    append_task_cost_line,
    collect_token_cost_summary_from_path,
    estimate_task_cost,
)
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge, WorkflowRunStartPending
from claw_trade.ui_contracts.enums import ReportTaskStatus
from claw_trade.workflow.models import RunStatus
from claw_trade.workflow.workers import all_worker_ids, stage_plans_for_market


_LOGGER = logging.getLogger(__name__)


class QueueError(RuntimeError):
    def __init__(self, code: str, category: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.user_message = message


@dataclass
class ReportTask:
    task_id: str
    source: str
    status: ReportTaskStatus
    instrument_code: str
    instrument_name: str | None
    market: str
    company_name: str
    currency_symbol: str
    start_date: str
    end_date: str
    current_date: str
    workflow_settings: dict[str, Any]
    dedupe_key: str
    priority: int
    created_at: str
    run_id: str | None = None
    queue_position: int | None = None
    failure: UserFacingFailure | None = None
    started_at: str | None = None
    finished_at: str | None = None
    progress: dict[str, Any] | None = None
    completed_report_saved: bool = False
    origin_context_id: str | None = None
    selection_context_ref: str | None = None
    selection_stage_marker: str | None = None
    cost_start_snapshot: TaskCostSnapshot | None = None
    cost_estimate: TaskCostEstimate | None = None
    _report_lock: AbstractContextManager[int] | None = field(default=None, repr=False)
    _report_lock_fd: int | None = field(default=None, repr=False)
    _workflow_start_attempt_id: str | None = field(default=None, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)
    _cancel_dispatched: bool = field(default=False, repr=False)
    _terminalization_claimed: bool = field(default=False, repr=False)


class ReportTaskQueue:
    def __init__(
        self,
        bridge: ReportWorkflowBridge,
        *,
        queue_limit: int = 10,
        completed_report_writer: Callable[[ReportTask, Any], None] | None = None,
        failed_report_writer: Callable[[ReportTask], None] | None = None,
        report_permission_checker: Callable[[], None] | None = None,
        task_cost_snapshot_provider: Callable[[], TaskCostSnapshot] | None = None,
        report_lock_path: Path | None = None,
        report_lock_opener: Callable[[Path], AbstractContextManager[int]] | None = None,
    ) -> None:
        self._bridge = bridge
        self._queue_limit = queue_limit
        self._completed_report_writer = completed_report_writer
        self._failed_report_writer = failed_report_writer
        self._report_permission_checker = report_permission_checker
        self._task_cost_snapshot_provider = task_cost_snapshot_provider
        self._report_lock_path = report_lock_path or paths.REPORT_ACTIVE_LOCK_PATH
        self._report_lock_opener = report_lock_opener or _open_report_lock
        self._tasks: dict[str, ReportTask] = {}
        self._enqueue_idempotency: dict[str, dict[str, Any]] = {}
        self._cancel_idempotency: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        self._right_rail_active: set[str] = set()
        self._last_terminal_task_id: str | None = None
        self._terminal_watchers: set[str] = set()
        self._state_lock = Lock()
        self._cost_lock = Lock()

    def enqueue_report_task(
        self,
        *,
        request_id: str,
        task_input: dict[str, Any],
        source: str,
        origin_context_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_report_allowed()
        with self._state_lock:
            if request_id in self._enqueue_idempotency:
                return self._enqueue_idempotency[request_id]
            dedupe_key = self._build_dedupe_key(task_input)
            existing = self._find_existing_queued(dedupe_key)
            if existing:
                if source == "manual" and existing.source == "scheduled":
                    existing.priority = 100
                task_payload = self.to_report_task_for_user(existing)
            else:
                task_payload = None
            if task_payload is None:
                queued_count = len(self._queued_tasks())
                if queued_count >= self._queue_limit:
                    raise QueueError("QUEUE_FULL", "queue_full", "报告队列已满，请稍后再试。")
                self._sequence += 1
                now = _now_iso()
                task = ReportTask(
                    task_id=f"task-{self._sequence}",
                    source=source,
                    status=ReportTaskStatus.QUEUED,
                    instrument_code=str(task_input["instrumentCode"]),
                    instrument_name=task_input.get("instrumentName"),
                    market=str(task_input["market"]),
                    company_name=str(task_input.get("companyName") or task_input["instrumentCode"]),
                    currency_symbol=str(task_input.get("currencySymbol") or ""),
                    start_date=str(task_input["startDate"]),
                    end_date=str(task_input["endDate"]),
                    current_date=str(task_input["currentDate"]),
                    workflow_settings=dict(task_input["workflowSettings"]),
                    dedupe_key=dedupe_key,
                    priority=100 if source == "manual" else 10,
                    created_at=now,
                    origin_context_id=origin_context_id,
                    selection_context_ref=_optional_task_text(task_input.get("selectionContextRef")),
                    selection_stage_marker=_optional_task_text(task_input.get("selectionStageMarker")),
                )
                self._tasks[task.task_id] = task
                self._right_rail_active.add(task.task_id)
                self._refresh_queue_positions()
        if task_payload is not None:
            payload = {"task": task_payload, "deduped": True}
            payload["queueSnapshot"] = self.get_report_queue_snapshot_for_user()
            with self._state_lock:
                self._enqueue_idempotency[request_id] = payload
            return payload
        self.start_next_report_task_if_idle()
        queue_snapshot = self.get_report_queue_snapshot_for_user()
        with self._state_lock:
            payload = {"task": self.to_report_task_for_user(task), "deduped": False}
            payload["queueSnapshot"] = queue_snapshot
            self._enqueue_idempotency[request_id] = payload
            return payload

    def start_next_report_task_if_idle(self) -> ReportTask | None:
        with self._state_lock:
            if self._running_task() is not None:
                return None
            queued = self._queued_tasks()
            if not queued:
                return None
            queued.sort(key=lambda item: (-item.priority, item.created_at))
            task = queued[0]
        try:
            self._assert_report_allowed()
        except QueueError as exc:
            self.handle_report_failed(task.task_id, exc)
            return task
        try:
            report_lock = self._report_lock_opener(self._report_lock_path)
            report_lock_fd = report_lock.__enter__()
        except Exception as exc:
            self.handle_report_failed(
                task.task_id,
                QueueError(
                    "REPORT_LOCK_UNAVAILABLE",
                    "assistant_unavailable",
                    f"报告执行锁不可用，任务未启动：{exc}",
                ),
            )
            return task
        with self._state_lock:
            current = self._tasks.get(task.task_id)
            if (
                current is not task
                or task.status != ReportTaskStatus.QUEUED
                or task._terminalization_claimed
            ):
                report_lock.__exit__(None, None, None)
                return task
            task._report_lock = report_lock
            task._report_lock_fd = report_lock_fd
            task.status = ReportTaskStatus.RUNNING
            task.queue_position = None
            task.started_at = _now_iso()
            self._refresh_queue_positions()
            task_input = {
                "instrumentCode": task.instrument_code,
                "instrumentName": task.instrument_name,
                "market": task.market,
                "companyName": task.company_name,
                "currencySymbol": task.currency_symbol,
                "startDate": task.start_date,
                "endDate": task.end_date,
                "currentDate": task.current_date,
                "workflowSettings": dict(task.workflow_settings),
            }
            if task.origin_context_id:
                task_input["originContextId"] = task.origin_context_id
        cost_start_snapshot = self._capture_task_cost_snapshot()
        if cost_start_snapshot is not None:
            with self._state_lock:
                current = self._tasks.get(task.task_id)
                if current is task:
                    task.cost_start_snapshot = cost_start_snapshot
        try:
            run = self._bridge.create_workflow_run(task_input)
        except WorkflowRunStartPending as exc:
            with self._state_lock:
                current = self._tasks.get(task.task_id)
                if current is task:
                    task._workflow_start_attempt_id = exc.attempt_id
            self._ensure_terminal_watcher(task)
            return task
        except Exception as exc:
            self.handle_report_failed(task.task_id, exc)
            return task
        with self._state_lock:
            current = self._tasks.get(task.task_id)
            if current is task:
                task.run_id = run.run_id
        self._ensure_terminal_watcher(task)
        return task

    def cancel_report_task(self, *, request_id: str, task_id: str) -> dict[str, Any]:
        with self._state_lock:
            pending_start = self._tasks.get(task_id)
            should_refresh_start = bool(
                pending_start is not None
                and pending_start.status == ReportTaskStatus.RUNNING
                and not pending_start.run_id
                and pending_start._workflow_start_attempt_id
            )
        if should_refresh_start:
            self.refresh_running_task_status()
        with self._state_lock:
            if request_id in self._cancel_idempotency:
                return self._cancel_idempotency[request_id]
            task = self._tasks.get(task_id)
            if task is None:
                raise QueueError("TASK_NOT_FOUND", "invalid_input", "没有找到对应任务，请刷新后重试。")
            if task._terminalization_claimed and task.status in {
                ReportTaskStatus.QUEUED,
                ReportTaskStatus.RUNNING,
            }:
                raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "报告任务正在结束，请稍后刷新。")
            if task.status == ReportTaskStatus.CANCELLED:
                task_payload = self.to_report_task_for_user(task)
                message = "任务已取消。"
                needs_start_next = False
            elif task.status == ReportTaskStatus.RUNNING:
                run_id = task.run_id
                task._cancel_requested = True
                task._cancel_dispatched = bool(run_id)
                task_payload = None
                message = "已停止报告任务。"
                needs_start_next = True
            elif task.status != ReportTaskStatus.QUEUED:
                raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "当前状态不支持取消。")
            else:
                run_id = None
                task._terminalization_claimed = True
                task.status = ReportTaskStatus.CANCELLED
                task.finished_at = _now_iso()
                self._right_rail_active.discard(task.task_id)
                self._last_terminal_task_id = task.task_id
                self._refresh_queue_positions()
                task.cost_estimate = estimate_task_cost(
                    None,
                    None,
                    token_summary=TaskTokenCostSummary.no_model_call(),
                )
                task_payload = self.to_report_task_for_user(task)
                message = append_task_cost_line("已取消排队任务。", task.cost_estimate)
                needs_start_next = True
        if task_payload is None:
            if not run_id:
                self._ensure_terminal_watcher(task)
                raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "取消请求已记录，任务启动后将立即停止。")
            if not run_id or not self._bridge.cancel_workflow_run(run_id):
                if run_id:
                    self._ensure_terminal_watcher(task)
                raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "取消请求已发出，报告仍在停止中。")
            self.refresh_running_task_status()
            with self._state_lock:
                still_running = task.status == ReportTaskStatus.RUNNING
            if still_running:
                self._ensure_terminal_watcher(task)
                raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "取消请求已发出，报告仍在停止中。")
            with self._state_lock:
                task_payload = self.to_report_task_for_user(task)
                if task.status == ReportTaskStatus.CANCELLED:
                    message = append_task_cost_line(message, task.cost_estimate)
                elif task.status == ReportTaskStatus.SUCCEEDED:
                    message = "报告已在取消完成前生成。"
                else:
                    message = "报告任务已结束。"
                needs_start_next = False
        payload = {
            "task": task_payload,
            "queueSnapshot": self.get_report_queue_snapshot_for_user(),
            "message": message,
        }
        with self._state_lock:
            self._cancel_idempotency[request_id] = payload
        if needs_start_next:
            self.start_next_report_task_if_idle()
        return payload

    def _ensure_terminal_watcher(self, task: ReportTask) -> None:
        if not self._bridge.supports_workflow_run_exit_wait():
            return
        with self._state_lock:
            if task.task_id in self._terminal_watchers:
                return
            self._terminal_watchers.add(task.task_id)

        def _finish_after_exit() -> None:
            try:
                run_id: str | None = None
                wait_attempted = False
                while True:
                    with self._state_lock:
                        if task.status != ReportTaskStatus.RUNNING:
                            return
                        current_run_id = task.run_id
                    if current_run_id != run_id:
                        run_id = current_run_id
                        wait_attempted = False
                    if run_id is None:
                        try:
                            self.refresh_running_task_status(expected_task_id=task.task_id)
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.warning("report start refresh failed for %s: %s", task.task_id, exc)
                        sleep(0.25)
                        continue
                    if not wait_attempted:
                        wait_attempted = True
                        try:
                            if not self._bridge.wait_for_workflow_run_exit(run_id):
                                _LOGGER.warning("workflow exit wait failed for report task %s", task.task_id)
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.warning("workflow exit wait failed for %s: %s", task.task_id, exc)
                    try:
                        self.refresh_running_task_status(expected_task_id=task.task_id)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning("report terminal refresh failed for %s: %s", task.task_id, exc)
                    with self._state_lock:
                        if task.status != ReportTaskStatus.RUNNING:
                            return
                    sleep(0.25)
            finally:
                with self._state_lock:
                    self._terminal_watchers.discard(task.task_id)

        Thread(
            target=_finish_after_exit,
            daemon=True,
            name=f"claw-trade-report-terminal-{task.task_id}",
        ).start()

    def handle_report_failed(
        self,
        task_id: str,
        error: Exception | str,
        *,
        run_dir: Path | None = None,
    ) -> ReportTask | None:
        with self._state_lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task._terminalization_claimed or task.status not in {
                ReportTaskStatus.QUEUED,
                ReportTaskStatus.RUNNING,
            }:
                return task
            task._terminalization_claimed = True
        self._finish_claimed_failure(task, error, run_dir=run_dir)
        return task

    def list_saved_reports_for_user(self) -> list[dict[str, Any]]:
        with self._state_lock:
            reports: list[dict[str, Any]] = []
            for task in self._tasks.values():
                if task.status == ReportTaskStatus.SUCCEEDED:
                    reports.append(
                        {
                            "id": task.task_id,
                            "instrumentCode": task.instrument_code,
                            "instrumentName": task.instrument_name,
                            "market": task.market,
                            "title": f"{task.instrument_code} 报告",
                            "generatedAt": task.finished_at or task.created_at,
                            "summarySnippet": "完整结论请查看报告正文。",
                        }
                    )
            return reports

    def get_report_queue_snapshot_for_user(self) -> dict[str, Any]:
        self.refresh_running_task_status()
        with self._state_lock:
            running = self._running_task()
            queued = sorted(self._queued_tasks(), key=lambda item: item.queue_position or 0)
            return {
                "runningTask": self.to_report_task_for_user(running) if running else None,
                "queuedTasks": [self.to_report_task_for_user(item) for item in queued],
                "lastTerminalTask": self.to_report_task_for_user(self._last_terminal_task()),
                "queueLimit": self._queue_limit,
                "queuedCount": len(queued),
                "isFull": len(queued) >= self._queue_limit,
            }

    def to_report_task_for_user(self, task: ReportTask | None) -> dict[str, Any] | None:
        if task is None:
            return None
        payload: dict[str, Any] = {
            "taskId": task.task_id,
            "source": task.source,
            "status": task.status.value,
            "statusLabel": _status_label(task.status),
            "instrumentCode": task.instrument_code,
            "instrumentName": task.instrument_name,
            "market": task.market,
            "companyName": task.company_name,
            "currencySymbol": task.currency_symbol,
            "startDate": task.start_date,
            "endDate": task.end_date,
            "currentDate": task.current_date,
            "queuePosition": task.queue_position,
            "createdAt": task.created_at,
            "startedAt": task.started_at,
            "finishedAt": task.finished_at,
        }
        if task.progress is not None:
            payload["progress"] = task.progress
        if task.selection_context_ref is not None:
            payload["selectionContextRef"] = task.selection_context_ref
        if task.selection_stage_marker is not None:
            payload["selectionStageMarker"] = task.selection_stage_marker
        if task.status == ReportTaskStatus.SUCCEEDED and task.run_id:
            payload["reportId"] = task.run_id
        if task.failure is not None:
            payload["failure"] = {
                "code": task.failure.code,
                "message": task.failure.user_message,
                "severity": task.failure.severity.value,
            }
        return payload

    def refresh_running_task_status(self, *, expected_task_id: str | None = None) -> ReportTask | None:
        with self._state_lock:
            task = self._running_task()
            if task is None:
                return task
            if expected_task_id is not None and task.task_id != expected_task_id:
                return None
            if not task.run_id:
                attempt_id = task._workflow_start_attempt_id
                if not attempt_id:
                    return task
            else:
                attempt_id = None
            run_id = task.run_id
            market = task.market
        if attempt_id is not None:
            try:
                attempt = self._bridge.poll_workflow_start_attempt(attempt_id)
            except Exception:
                return task
            if attempt.run_id:
                with self._state_lock:
                    if task.status != ReportTaskStatus.RUNNING:
                        return task
                    task.run_id = attempt.run_id
                    task._workflow_start_attempt_id = None
                    run_id = attempt.run_id
            elif not attempt.has_exited:
                return task
            else:
                with self._state_lock:
                    task._workflow_start_attempt_id = None
                return self.handle_report_failed(
                    task.task_id,
                    RuntimeError(attempt.error or "workflow_start_timeout"),
                )
        if not run_id:
            return task
        self._dispatch_requested_cancel(task, run_id)
        try:
            workflow_state = self._bridge.load_workflow_state(run_id)
        except Exception:
            return task

        progress = map_workflow_progress_to_ui_state(
            workflow_state,
            _worker_progress_from_run_evidence(workflow_state, market=market),
        )
        status = _workflow_status_value(workflow_state)
        with self._state_lock:
            cancel_requested = task._cancel_requested
        if status == RunStatus.COMPLETED.value and not cancel_requested:
            if not self._claim_running_terminalization(task):
                return task
            with self._state_lock:
                should_write = self._completed_report_writer is not None and not task.completed_report_saved
                if should_write:
                    task.completed_report_saved = True
            try:
                self._finish_task_cost_estimate(task, run_dir=_run_dir_from_workflow_state(workflow_state))
                if should_write:
                    self._completed_report_writer(task, workflow_state)
            except Exception as exc:
                self._finish_claimed_failure(
                    task,
                    exc,
                    run_dir=_run_dir_from_workflow_state(workflow_state),
                )
                return task
            with self._state_lock:
                task.progress = progress
                task.status = ReportTaskStatus.SUCCEEDED
                task.finished_at = _now_iso()
                self._right_rail_active.discard(task.task_id)
                self._last_terminal_task_id = task.task_id
            self._release_report_lock(task)
            self.start_next_report_task_if_idle()
        elif cancel_requested or status == RunStatus.CANCELLED.value:
            if not task.run_id or not self._bridge.workflow_run_has_exited(task.run_id):
                with self._state_lock:
                    task.progress = progress
                return task
            if not self._claim_running_terminalization(task):
                return task
            try:
                self._finish_task_cost_estimate(task, run_dir=_run_dir_from_workflow_state(workflow_state))
            finally:
                with self._state_lock:
                    task.progress = progress
                    task.status = ReportTaskStatus.CANCELLED
                    task.finished_at = _now_iso()
                    self._right_rail_active.discard(task.task_id)
                    self._last_terminal_task_id = task.task_id
                self._release_report_lock(task)
                self.start_next_report_task_if_idle()
        elif status == RunStatus.FAILED.value:
            reason = _read_state_value(workflow_state, "failure_reason", default="workflow_failed")
            reason = _workflow_failure_reason_with_evidence(workflow_state, str(reason or "workflow_failed"))
            self.handle_report_failed(
                task.task_id,
                reason,
                run_dir=_run_dir_from_workflow_state(workflow_state),
            )
        else:
            with self._state_lock:
                task.progress = progress
        return task

    def _dispatch_requested_cancel(self, task: ReportTask, run_id: str) -> None:
        with self._state_lock:
            if not task._cancel_requested or task._cancel_dispatched:
                return
            task._cancel_dispatched = True
        try:
            self._bridge.cancel_workflow_run(run_id)
        except Exception:
            with self._state_lock:
                task._cancel_dispatched = False
            raise

    def get_task_for_testing(self, task_id: str) -> ReportTask | None:
        with self._state_lock:
            return self._tasks.get(task_id)

    def set_task_origin_context(self, task_id: str, context_id: str) -> None:
        with self._state_lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.origin_context_id = context_id

    def right_rail_active_task_ids(self) -> set[str]:
        with self._state_lock:
            return set(self._right_rail_active)

    def protected_run_ids_for_cleanup(self) -> set[str]:
        protected_statuses = {
            ReportTaskStatus.QUEUED,
            ReportTaskStatus.RUNNING,
            ReportTaskStatus.SAVING_REPORT,
            ReportTaskStatus.PDF_EXPORTING,
        }
        with self._state_lock:
            return {
                task.run_id
                for task in self._tasks.values()
                if task.run_id and task.status in protected_statuses
            }

    def _assert_report_allowed(self) -> None:
        if self._report_permission_checker is None:
            return
        try:
            self._report_permission_checker()
        except PermissionError as exc:
            raise QueueError("LICENSE_BLOCKED", "license_blocked", str(exc)) from exc

    def _capture_task_cost_snapshot(self) -> TaskCostSnapshot | None:
        if self._task_cost_snapshot_provider is None:
            return None
        try:
            return self._task_cost_snapshot_provider()
        except Exception:
            return TaskCostSnapshot.unavailable(reason="snapshot_failed")

    def _finish_task_cost_estimate(self, task: ReportTask, *, run_dir: Path | None = None) -> None:
        token_summary = collect_token_cost_summary_from_path(run_dir)
        if self._task_cost_snapshot_provider is None and token_summary is None:
            return
        with self._cost_lock:
            if task.cost_estimate is not None:
                return
            finish_snapshot = (
                self._capture_task_cost_snapshot()
                if self._task_cost_snapshot_provider is not None
                else None
            )
            task.cost_estimate = estimate_task_cost(
                task.cost_start_snapshot,
                finish_snapshot,
                token_summary=token_summary,
            )

    def _release_report_lock(self, task: ReportTask) -> None:
        with self._state_lock:
            report_lock = task._report_lock
            task._report_lock = None
            task._report_lock_fd = None
        if report_lock is not None:
            report_lock.__exit__(None, None, None)

    def _claim_running_terminalization(self, task: ReportTask) -> bool:
        with self._state_lock:
            if (
                self._tasks.get(task.task_id) is not task
                or task.status != ReportTaskStatus.RUNNING
                or task._terminalization_claimed
            ):
                return False
            task._terminalization_claimed = True
            return True

    def _finish_claimed_failure(
        self,
        task: ReportTask,
        error: Exception | str,
        *,
        run_dir: Path | None = None,
    ) -> None:
        category = error.category if isinstance(error, QueueError) else None
        if isinstance(error, QueueError) and error.code == "REPORT_LOCK_UNAVAILABLE":
            failure = UserFacingFailure(
                code="ASSISTANT_UNAVAILABLE",
                user_message=error.user_message,
            )
        else:
            failure = translate_internal_error_for_user(error, category=category)
        with self._state_lock:
            task.failure = failure
        try:
            self._finish_task_cost_estimate(task, run_dir=run_dir)
            if self._failed_report_writer is not None:
                try:
                    self._failed_report_writer(task)
                except Exception:
                    pass
        finally:
            with self._state_lock:
                task.status = ReportTaskStatus.FAILED
                task.finished_at = _now_iso()
                self._right_rail_active.discard(task.task_id)
                self._last_terminal_task_id = task.task_id
                self._refresh_queue_positions()
            self._release_report_lock(task)
            self.start_next_report_task_if_idle()

    def _find_existing_queued(self, dedupe_key: str) -> ReportTask | None:
        for task in self._tasks.values():
            if task.status == ReportTaskStatus.QUEUED and task.dedupe_key == dedupe_key:
                return task
        return None

    def _running_task(self) -> ReportTask | None:
        for task in self._tasks.values():
            if task.status == ReportTaskStatus.RUNNING:
                return task
        return None

    def _queued_tasks(self) -> list[ReportTask]:
        return [task for task in self._tasks.values() if task.status == ReportTaskStatus.QUEUED]

    def _last_terminal_task(self) -> ReportTask | None:
        if not self._last_terminal_task_id:
            return None
        task = self._tasks.get(self._last_terminal_task_id)
        if task is None or task.status in {ReportTaskStatus.QUEUED, ReportTaskStatus.RUNNING}:
            return None
        return task

    def _refresh_queue_positions(self) -> None:
        queued = sorted(self._queued_tasks(), key=lambda item: (-item.priority, item.created_at))
        for index, task in enumerate(queued, start=1):
            task.queue_position = index
        for task in self._tasks.values():
            if task.status != ReportTaskStatus.QUEUED:
                task.queue_position = None

    @staticmethod
    def _build_dedupe_key(task_input: dict[str, Any]) -> str:
        return ":".join(
            [
                str(task_input["instrumentCode"]),
                str(task_input["market"]),
                str(task_input["startDate"]),
                str(task_input["endDate"]),
                str(task_input["workflowSettings"]["defaultProfile"]),
            ]
        )


def _status_label(status: ReportTaskStatus) -> str:
    mapping = {
        ReportTaskStatus.DRAFT: "草稿",
        ReportTaskStatus.QUEUED: "排队中",
        ReportTaskStatus.RUNNING: "生成中",
        ReportTaskStatus.SUCCEEDED: "已完成",
        ReportTaskStatus.FAILED: "失败",
        ReportTaskStatus.CANCELLED: "已取消",
    }
    return mapping[status]


def _optional_task_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _workflow_status_value(workflow_state: dict[str, Any] | Any) -> str:
    value = _read_state_value(workflow_state, "status", default="")
    if isinstance(value, RunStatus):
        return value.value
    if hasattr(value, "value"):
        return str(value.value)
    return str(value).strip().lower()


def _read_state_value(workflow_state: dict[str, Any] | Any, key: str, *, default: Any = None) -> Any:
    if isinstance(workflow_state, dict):
        return workflow_state.get(key, default)
    return getattr(workflow_state, key, default)


def _run_dir_from_workflow_state(workflow_state: dict[str, Any] | Any) -> Path | None:
    value = _read_state_value(workflow_state, "run_dir")
    if not value:
        return None
    return Path(str(value))


def _worker_progress_from_run_evidence(workflow_state: dict[str, Any] | Any, *, market: str) -> dict[str, Any]:
    worker_order = tuple(worker_id for plan in stage_plans_for_market(market) for worker_id in plan.workers)
    worker_statuses = _read_worker_statuses_from_calls(_read_state_value(workflow_state, "run_dir"))
    completed_workers = set(_read_state_value(workflow_state, "completed_workers", default=()) or ())
    completed_workers.update(
        worker_id for worker_id, status in worker_statuses.items() if status in {"succeeded", "completed"}
    )
    active_workers = tuple(worker_id for worker_id in worker_order if worker_statuses.get(worker_id) == "running")
    return {
        "activeWorkerIds": active_workers,
        "activeWorkerId": active_workers[0] if active_workers else None,
        "completedWorkers": tuple(worker_id for worker_id in worker_order if worker_id in completed_workers),
        "workerStatuses": {worker_id: worker_statuses.get(worker_id, "pending") for worker_id in worker_order},
        "visibleWorkerIds": worker_order,
    }


def _read_worker_statuses_from_calls(run_dir_value: Any) -> dict[str, str]:
    if not run_dir_value:
        return {}
    run_dir = Path(run_dir_value)
    calls_dir = run_dir / "calls"
    if not calls_dir.exists():
        return {}
    statuses: dict[str, str] = {}
    known_workers = set(all_worker_ids())
    for call_dir in sorted(item for item in calls_dir.iterdir() if item.is_dir()):
        call_payload = _read_json_object(call_dir / "call.json")
        result_payload = _read_json_object(call_dir / "result.json")
        worker_id = _string_value(result_payload.get("worker_id") or call_payload.get("worker_id"))
        if worker_id not in known_workers:
            continue
        if result_payload:
            status = _string_value(result_payload.get("status")) or "failed"
        else:
            status = "running"
        statuses[worker_id] = status
    return statuses


def _workflow_failure_reason_with_evidence(workflow_state: dict[str, Any] | Any, fallback: str) -> str:
    run_dir_value = _read_state_value(workflow_state, "run_dir")
    if not run_dir_value:
        return fallback
    run_dir = Path(run_dir_value)
    reports_dir = run_dir / "reports"
    if not reports_dir.is_dir():
        return fallback
    for report_path in sorted(reports_dir.glob("collect-first-*.json")):
        payload = _read_json_object(report_path)
        compliance = payload.get("collect_first_compliance")
        if not isinstance(compliance, dict):
            continue
        failures = compliance.get("failures_collected")
        if not isinstance(failures, list):
            continue
        for item in failures:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason") or "").strip() or fallback
            category = _string_value(item.get("category"))
            if category != "tool_calls" and "claw_request_data" not in reason:
                continue
            detail = _tool_call_failure_detail(item.get("evidence_paths"), run_dir=run_dir)
            if detail and detail not in reason:
                return f"{reason}; {detail}"
            return reason
    return fallback


def _tool_call_failure_detail(value: Any, *, run_dir: Path) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        path = _resolve_evidence_path(item, run_dir=run_dir)
        if path is None or path.name != "tool-calls.json":
            continue
        payload = _read_json_object(path)
        calls = payload.get("calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            if _string_value(call.get("tool_name")) != "claw_request_data":
                continue
            if _string_value(call.get("status")) != "error":
                continue
            error = str(call.get("error") or "").strip()
            return error or "数据工具调用失败"
    return None


def _resolve_evidence_path(value: Any, *, run_dir: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    candidates = (path,) if path.is_absolute() else (path, run_dir / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_value(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    return str(value or "").strip().lower()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _open_report_lock(path: Path) -> AbstractContextManager[int]:
    return hold_host_lock(path, exclusive=False, blocking=False)
