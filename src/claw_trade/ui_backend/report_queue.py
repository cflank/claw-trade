from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claw_trade.ui_backend.error_translator import (
    UserFacingFailure,
    translate_internal_error_for_user,
)
from claw_trade.ui_backend.progress_mapper import map_workflow_progress_to_ui_state
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.ui_contracts.enums import ReportTaskStatus
from claw_trade.workflow.models import RunStatus
from claw_trade.workflow.workers import all_worker_ids


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


class ReportTaskQueue:
    def __init__(
        self,
        bridge: ReportWorkflowBridge,
        *,
        queue_limit: int = 10,
        completed_report_writer: Callable[[ReportTask, Any], None] | None = None,
    ) -> None:
        self._bridge = bridge
        self._queue_limit = queue_limit
        self._completed_report_writer = completed_report_writer
        self._tasks: dict[str, ReportTask] = {}
        self._enqueue_idempotency: dict[str, dict[str, Any]] = {}
        self._cancel_idempotency: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        self._right_rail_active: set[str] = set()
        self._last_terminal_task_id: str | None = None

    def enqueue_report_task(
        self,
        *,
        request_id: str,
        task_input: dict[str, Any],
        source: str,
        origin_context_id: str | None = None,
    ) -> dict[str, Any]:
        if request_id in self._enqueue_idempotency:
            return self._enqueue_idempotency[request_id]
        dedupe_key = self._build_dedupe_key(task_input)
        existing = self._find_existing_queued(dedupe_key)
        if existing:
            if source == "manual" and existing.source == "scheduled":
                existing.priority = 100
            payload = {"task": self.to_report_task_for_user(existing), "deduped": True}
            payload["queueSnapshot"] = self.get_report_queue_snapshot_for_user()
            self._enqueue_idempotency[request_id] = payload
            return payload
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
        self.start_next_report_task_if_idle()
        queue_snapshot = self.get_report_queue_snapshot_for_user()
        payload = {"task": self.to_report_task_for_user(task), "deduped": False}
        payload["queueSnapshot"] = queue_snapshot
        self._enqueue_idempotency[request_id] = payload
        return payload

    def start_next_report_task_if_idle(self) -> ReportTask | None:
        if self._running_task() is not None:
            return None
        queued = self._queued_tasks()
        if not queued:
            return None
        queued.sort(key=lambda item: (-item.priority, item.created_at))
        task = queued[0]
        try:
            run = self._bridge.create_workflow_run(
                {
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
            )
            task.status = ReportTaskStatus.RUNNING
            task.run_id = run.run_id
            task.started_at = _now_iso()
            task.queue_position = None
        except Exception as exc:
            self.handle_report_failed(task.task_id, exc)
        self._refresh_queue_positions()
        return task

    def cancel_report_task(self, *, request_id: str, task_id: str) -> dict[str, Any]:
        if request_id in self._cancel_idempotency:
            return self._cancel_idempotency[request_id]
        task = self._tasks.get(task_id)
        if task is None:
            raise QueueError("TASK_NOT_FOUND", "invalid_input", "没有找到对应任务，请刷新后重试。")
        if task.status == ReportTaskStatus.CANCELLED:
            payload = {
                "task": self.to_report_task_for_user(task),
                "queueSnapshot": self.get_report_queue_snapshot_for_user(),
                "message": "任务已取消。",
            }
            self._cancel_idempotency[request_id] = payload
            return payload
        if task.status == ReportTaskStatus.RUNNING:
            if not task.run_id or not self._bridge.cancel_workflow_run(task.run_id):
                raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "报告正在生成，当前运行时不支持停止。")
            task.status = ReportTaskStatus.CANCELLED
            task.finished_at = _now_iso()
            self._right_rail_active.discard(task.task_id)
            self._last_terminal_task_id = task.task_id
            self._refresh_queue_positions()
            payload = {
                "task": self.to_report_task_for_user(task),
                "queueSnapshot": self.get_report_queue_snapshot_for_user(),
                "message": "已停止报告任务。",
            }
            self._cancel_idempotency[request_id] = payload
            self.start_next_report_task_if_idle()
            return payload
        if task.status != ReportTaskStatus.QUEUED:
            raise QueueError("TASK_NOT_CANCELLABLE", "conflict", "当前状态不支持取消。")
        task.status = ReportTaskStatus.CANCELLED
        task.finished_at = _now_iso()
        self._right_rail_active.discard(task.task_id)
        self._last_terminal_task_id = task.task_id
        self._refresh_queue_positions()
        payload = {
            "task": self.to_report_task_for_user(task),
            "queueSnapshot": self.get_report_queue_snapshot_for_user(),
            "message": "已取消排队任务。",
        }
        self._cancel_idempotency[request_id] = payload
        self.start_next_report_task_if_idle()
        return payload

    def handle_report_failed(self, task_id: str, error: Exception | str) -> ReportTask | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        failure = translate_internal_error_for_user(error)
        task.status = ReportTaskStatus.FAILED
        task.failure = failure
        task.finished_at = _now_iso()
        self._right_rail_active.discard(task.task_id)
        self._last_terminal_task_id = task.task_id
        self._refresh_queue_positions()
        self.start_next_report_task_if_idle()
        return task

    def list_saved_reports_for_user(self) -> list[dict[str, Any]]:
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

    def refresh_running_task_status(self) -> ReportTask | None:
        task = self._running_task()
        if task is None or not task.run_id:
            return task
        try:
            workflow_state = self._bridge.load_workflow_state(task.run_id)
        except Exception:
            return task

        task.progress = map_workflow_progress_to_ui_state(
            workflow_state,
            _worker_progress_from_run_evidence(workflow_state),
        )
        status = _workflow_status_value(workflow_state)
        if status == RunStatus.COMPLETED.value:
            if self._completed_report_writer is not None and not task.completed_report_saved:
                try:
                    self._completed_report_writer(task, workflow_state)
                    task.completed_report_saved = True
                except Exception as exc:
                    self.handle_report_failed(task.task_id, exc)
                    return task
            task.status = ReportTaskStatus.SUCCEEDED
            task.finished_at = _now_iso()
            self._right_rail_active.discard(task.task_id)
            self._last_terminal_task_id = task.task_id
            self.start_next_report_task_if_idle()
        elif status == RunStatus.FAILED.value:
            reason = _read_state_value(workflow_state, "failure_reason", default="workflow_failed")
            self.handle_report_failed(task.task_id, str(reason or "workflow_failed"))
        elif status == RunStatus.CANCELLED.value:
            task.status = ReportTaskStatus.CANCELLED
            task.finished_at = _now_iso()
            self._right_rail_active.discard(task.task_id)
            self._last_terminal_task_id = task.task_id
            self.start_next_report_task_if_idle()
        return task

    def get_task_for_testing(self, task_id: str) -> ReportTask | None:
        return self._tasks.get(task_id)

    def set_task_origin_context(self, task_id: str, context_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.origin_context_id = context_id

    def right_rail_active_task_ids(self) -> set[str]:
        return set(self._right_rail_active)

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


def _worker_progress_from_run_evidence(workflow_state: dict[str, Any] | Any) -> dict[str, Any]:
    worker_order = all_worker_ids()
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
