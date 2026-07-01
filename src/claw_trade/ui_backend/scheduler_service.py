from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.instruments.resolver import resolve_instrument_identity
from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore, ScheduledReport, ScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.scope_guard import (
    FirstVersionScopeError,
    assert_schedule_frequency_supported,
)
from claw_trade.ui_contracts.user_dto import (
    ReportQueueSnapshotForUser,
    ReportTaskForUser,
    ScheduledReportForUser,
    to_report_queue_snapshot_for_user,
    to_report_task_for_user,
    to_scheduled_report_for_user,
)
from claw_trade.workflow.report_request_factory import build_report_run_request, report_display_name

_SCHEDULED_REPORT_AGENT_ID = "scheduled_report_runner"
_SCHEDULED_WORK_TOOL = "claw-trade-scheduled-work-wake"
_SCHEDULE_TIMEZONE = "America/New_York"
_VISIBLE_SCHEDULE_STATES = {"active", "paused", "due", "enqueued"}
_COLLAPSIBLE_SCHEDULE_STATES = _VISIBLE_SCHEDULE_STATES | {"sync_failed"}
_DAILY_DUPLICATE_WINDOW_MINUTES = 4 * 60


class UiServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TickItemResult:
    scheduledReportId: str
    enqueued: bool
    taskId: str | None = None
    message: str | None = None


class SchedulerService:
    def __init__(
        self,
        *,
        enqueue_report_task: Callable[[dict[str, Any], str], dict[str, Any]],
        queue_snapshot_provider: Callable[[], dict[str, Any]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        store: ScheduledWorkStore | None = None,
        cron_adapter: OpenClawCronAdapter | None = None,
        user_id_provider: Callable[[], str] | None = None,
        allow_local_tick_for_tests: bool = False,
    ) -> None:
        self._enqueue_report_task = enqueue_report_task
        self._queue_snapshot_provider = queue_snapshot_provider or (lambda: {"runningTask": None, "queuedTasks": []})
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._store = store or InMemoryScheduledWorkStore()
        self._cron_adapter = cron_adapter
        self._user_id_provider = user_id_provider or (lambda: "")
        self._allow_local_tick_for_tests = allow_local_tick_for_tests
        self._idempotency: dict[str, Any] = {}
        self._seq = self._highest_schedule_seq()

    def create_scheduled_report(
        self,
        *,
        request_id: str,
        instrument_code: str,
        market: MarketProfile | str,
        frequency: str,
        time_of_day: str,
        weekday: int | None = None,
        notification: dict[str, Any] | None = None,
        instrument_name: str | None = None,
        workflow_settings: dict[str, Any] | None = None,
    ) -> ScheduledReportForUser:
        payload = self.create_scheduled_report_for_user(
            request_id=request_id,
            instrument_code=instrument_code,
            market=market,
            frequency=frequency,
            time_of_day=time_of_day,
            weekday=weekday,
            notification=notification,
            instrument_name=instrument_name,
            workflow_settings=workflow_settings,
        )
        report = payload["scheduledReport"]
        if isinstance(report, ScheduledReportForUser):
            return report
        return to_scheduled_report_for_user(report)

    def create_scheduled_report_for_user(
        self,
        *,
        request_id: str,
        instrument_code: str,
        market: MarketProfile | str,
        frequency: str,
        time_of_day: str,
        weekday: int | None = None,
        notification: dict[str, Any] | None = None,
        instrument_name: str | None = None,
        workflow_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            if isinstance(cached, Mapping) and "scheduledReport" in cached:
                return dict(cached)
            return {"scheduledReport": cached}
        try:
            assert_schedule_frequency_supported(frequency)
        except FirstVersionScopeError as exc:
            raise UiServiceError("INVALID_INPUT", "当前只支持每天或每周生成完整报告。请选择每天或每周。") from exc
        time_text = self._normalize_time_of_day(time_of_day)
        market_value = self._as_market_profile(market)
        if frequency == "weekly":
            if weekday is None or weekday < 0 or weekday > 6:
                raise UiServiceError("INVALID_INPUT", "每周计划需要 weekday(0-6)。")
        identity = resolve_instrument_identity(instrument_code, market_hint=market_value.value)
        market_value = MarketProfile(identity.profile)
        normalized_notification = self._normalize_notification(notification)
        normalized_workflow_settings = self._normalize_workflow_settings(workflow_settings, market_value)
        existing = self._find_matching_scheduled_report(
            instrument_code=identity.ticker,
            market=market_value,
            frequency=frequency,
            time_of_day=time_text,
            weekday=weekday,
            notification=normalized_notification,
            workflow_settings=normalized_workflow_settings,
        )
        if existing is not None:
            self._delete_collapsed_schedule_duplicates(existing)
            dto = to_scheduled_report_for_user(existing)
            payload = {"scheduledReport": dto}
            self._idempotency[request_id] = payload
            return payload
        nearby = self._find_nearby_daily_scheduled_report(
            instrument_code=identity.ticker,
            market=market_value,
            frequency=frequency,
            time_of_day=time_text,
            weekday=weekday,
            notification=normalized_notification,
            workflow_settings=normalized_workflow_settings,
        )
        if nearby is not None:
            previous_time = nearby.time_of_day
            nearby.time_of_day = time_text
            nearby.weekday = weekday
            nearby.notification = normalized_notification
            nearby.workflow_settings = normalized_workflow_settings
            nearby.instrument_name = instrument_name or nearby.instrument_name or report_display_name(identity.ticker, identity.profile)
            nearby.next_run_at = self.compute_next_run_at(
                frequency=frequency,
                time_of_day=time_text,
                weekday=weekday,
                after=self._now_provider(),
                timezone_name=_SCHEDULE_TIMEZONE,
            )
            nearby.state = "active"
            nearby.sync_error_message = None
            nearby.updated_at = self._now_iso()
            if self._cron_adapter is not None and nearby.openclaw_cron_job_id:
                try:
                    self._cron_adapter.update_job(
                        job_id=nearby.openclaw_cron_job_id,
                        patch={
                            "name": self._cron_job_name(nearby),
                            "schedule": self._cron_schedule(nearby),
                            "payload": self._scheduled_report_cron_payload(nearby.id),
                            "enabled": True,
                        },
                    )
                except Exception as exc:
                    raise UiServiceError("CRON_PROVISION_FAILED", "定时报告更新失败，OpenClaw cron 未配置成功。") from exc
            self._store.save_scheduled_report(nearby)
            self._delete_collapsed_schedule_duplicates(nearby)
            dto = to_scheduled_report_for_user(nearby)
            payload = {
                "scheduledReport": dto,
                "message": f"已替换相近的定时报告：原时间 {previous_time}，新时间 {time_text}。",
                "replacedScheduledReportId": nearby.id,
            }
            self._idempotency[request_id] = payload
            return payload
        next_run_at = self.compute_next_run_at(
            frequency=frequency,
            time_of_day=time_text,
            weekday=weekday,
            after=self._now_provider(),
            timezone_name=_SCHEDULE_TIMEZONE,
        )
        now_iso = self._now_iso()
        item = ScheduledReport(
            id=self._next_schedule_id(),
            instrument_code=identity.ticker,
            instrument_name=instrument_name or report_display_name(identity.ticker, identity.profile),
            market=market_value,
            frequency=frequency,
            time_of_day=time_text,
            weekday=weekday,
            notification=normalized_notification,
            workflow_settings=normalized_workflow_settings,
            start_date=now_iso[:10],
            end_date=now_iso[:10],
            current_date=now_iso[:10],
            state="active",
            next_run_at=next_run_at,
            last_run_task_id=None,
            openclaw_cron_job_id=None,
            last_cron_run_id=None,
            sync_error_message=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        self._store.save_scheduled_report(item)
        if self._cron_adapter is not None:
            try:
                job = self._cron_adapter.add_job(
                    name=self._cron_job_name(item),
                    schedule=self._cron_schedule(item),
                    agent_id=_SCHEDULED_REPORT_AGENT_ID,
                    payload=self._scheduled_report_cron_payload(item.id),
                    session_target="isolated",
                    wake_mode="now",
                    delivery={"mode": "none"},
                    enabled=True,
                )
            except Exception as exc:
                item.state = "sync_failed"
                item.sync_error_message = "OpenClaw cron 创建失败。"
                item.updated_at = self._now_iso()
                self._store.save_scheduled_report(item)
                raise UiServiceError("CRON_PROVISION_FAILED", "定时报告创建失败，OpenClaw cron 未配置成功。") from exc
            item.openclaw_cron_job_id = job.openclaw_cron_job_id
            item.sync_error_message = None
            item.updated_at = self._now_iso()
            self._store.save_scheduled_report(item)
        self._delete_collapsed_schedule_duplicates(item)
        dto = to_scheduled_report_for_user(item)
        payload = {"scheduledReport": dto}
        self._idempotency[request_id] = payload
        return payload

    def pause_scheduled_report(self, *, request_id: str, scheduled_report_id: str) -> ScheduledReportForUser:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._get_schedule_or_raise(scheduled_report_id)
        if item.state == "paused":
            dto = to_scheduled_report_for_user(item)
            self._idempotency[request_id] = dto
            return dto
        if item.state not in {"active", "due", "enqueued"}:
            raise UiServiceError("INVALID_INPUT", "当前状态不能暂停。")
        self._update_cron_enabled(item, enabled=False)
        item.state = "paused"
        item.updated_at = self._now_iso()
        item.sync_error_message = None
        self._store.save_scheduled_report(item)
        dto = to_scheduled_report_for_user(item)
        self._idempotency[request_id] = dto
        return dto

    def resume_scheduled_report(self, *, request_id: str, scheduled_report_id: str) -> ScheduledReportForUser:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._get_schedule_or_raise(scheduled_report_id)
        if item.state == "active":
            dto = to_scheduled_report_for_user(item)
            self._idempotency[request_id] = dto
            return dto
        if item.state != "paused":
            raise UiServiceError("INVALID_INPUT", "当前状态不能恢复。")
        self._update_cron_enabled(item, enabled=True)
        item.state = "active"
        item.next_run_at = self.compute_next_run_at(
            frequency=item.frequency,
            time_of_day=item.time_of_day,
            weekday=item.weekday,
            after=self._now_provider(),
            timezone_name=_SCHEDULE_TIMEZONE,
        )
        item.updated_at = self._now_iso()
        item.sync_error_message = None
        self._store.save_scheduled_report(item)
        dto = to_scheduled_report_for_user(item)
        self._idempotency[request_id] = dto
        return dto

    def delete_scheduled_report(self, *, request_id: str, scheduled_report_id: str) -> dict[str, Any]:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._store.get_scheduled_report(scheduled_report_id)
        if item is None:
            raise UiServiceError("SCHEDULE_NOT_FOUND", "定时报告不存在。")
        targets = [other for other in self._ordered_collapsible_schedules() if self._schedules_collapse(item, other)] or [item]
        for target in targets:
            self._delete_schedule_item(target)
        payload = {"deleted": True, "scheduledReportId": item.id}
        self._idempotency[request_id] = payload
        return payload

    def run_scheduled_report_now(
        self,
        *,
        request_id: str,
        scheduled_report_id: str,
    ) -> dict[str, Any]:
        cached = self._idempotency.get(request_id)
        if cached is not None:
            return cached
        item = self._get_schedule_or_raise(scheduled_report_id)
        if item.state == "deleted":
            raise UiServiceError("SCHEDULE_NOT_FOUND", "定时报告不存在。")
        if item.state not in {"active", "paused", "due", "enqueued"}:
            raise UiServiceError("INVALID_INPUT", "当前状态不能立即执行。")

        if self._cron_adapter is not None:
            if item.state == "paused":
                raise UiServiceError("INVALID_INPUT", "定时报告已暂停。")
            if item.last_run_task_id and self._is_duplicate_scheduled_report_window(item):
                raise UiServiceError("INVALID_INPUT", "定时报告当前计划窗口已触发。")
            if not item.openclaw_cron_job_id:
                raise UiServiceError("CRON_PROVISION_FAILED", "定时报告 OpenClaw cron 尚未配置。")
            result = self._cron_adapter.run_job(job_id=item.openclaw_cron_job_id, idempotency_key=request_id)
            item.last_cron_run_id = self._cron_run_id_from_result(result)
            item.updated_at = self._now_iso()
            item.sync_error_message = None
            self._store.save_scheduled_report(item)
            snapshot = self._queue_snapshot_provider()
            payload = {
                "scheduledReport": to_scheduled_report_for_user(item),
                "triggered": True,
                "cronRunId": item.last_cron_run_id,
                "queueSnapshot": self._queue_snapshot_for_user(snapshot),
            }
            self._idempotency[request_id] = payload
            return payload

        task = self._enqueue_report_task(self._build_task_input(item), request_id)
        task_payload = self._task_for_user_payload(task=task, schedule=item)
        item.last_run_task_id = self._task_id_from_queue_task(task_payload)
        item.updated_at = self._now_iso()
        self._store.save_scheduled_report(item)
        snapshot = self._queue_snapshot_provider()
        payload = {
            "task": to_report_task_for_user(task_payload),
            "queueSnapshot": self._queue_snapshot_for_user(snapshot),
        }
        self._idempotency[request_id] = payload
        return payload

    def handle_scheduled_report_cron_wake(
        self,
        *,
        request_id: str,
        scheduled_report_id: str,
        cron_run_id: str,
    ) -> dict[str, Any]:
        item = self._get_schedule_or_raise(scheduled_report_id)
        if item.state == "deleted":
            raise UiServiceError("SCHEDULE_NOT_FOUND", "定时报告不存在。")
        if item.state == "paused":
            raise UiServiceError("INVALID_INPUT", "定时报告已暂停。")

        canonical = self._canonical_visible_schedule(item)
        if canonical is not None and canonical.id != item.id:
            snapshot = self._queue_snapshot_provider()
            payload = {
                "queueSnapshot": self._queue_snapshot_for_user(snapshot),
                "deduped": True,
                "skipped": True,
                "canonicalScheduledReportId": canonical.id,
            }
            self._idempotency[request_id] = payload
            return payload

        if item.last_run_task_id and self._is_duplicate_scheduled_report_wake(item, cron_run_id=cron_run_id):
            snapshot = self._queue_snapshot_provider()
            payload: dict[str, Any] = {
                "queueSnapshot": self._queue_snapshot_for_user(snapshot),
                "deduped": True,
                "skipped": True,
                "lastRunTaskId": item.last_run_task_id,
            }
            task = self._last_scheduled_report_task_for_user(snapshot=snapshot, schedule=item)
            if task is not None:
                payload["task"] = task
            self._idempotency[request_id] = payload
            return payload

        task_input = self._build_task_input(item)
        stable_request_id = self._scheduled_report_window_request_id(item, task_input)
        cached = self._idempotency.get(stable_request_id)
        if cached is not None:
            self._idempotency[request_id] = cached
            return cached

        enqueue_result = self._enqueue_report_task(task_input, stable_request_id)
        task_source, snapshot_source = self._scheduled_report_enqueue_parts(enqueue_result)
        task_for_user = self._task_for_user_from_enqueue_result(task_source, schedule=item)
        item.last_run_task_id = task_for_user.task_id
        item.last_cron_run_id = cron_run_id
        item.start_date = str(task_input["startDate"])
        item.end_date = str(task_input["endDate"])
        item.current_date = str(task_input["currentDate"])
        item.next_run_at = self.compute_next_run_at(
            frequency=item.frequency,
            time_of_day=item.time_of_day,
            weekday=item.weekday,
            after=self._now_provider(),
            timezone_name=_SCHEDULE_TIMEZONE,
        )
        item.state = "active"
        item.updated_at = self._now_iso()
        item.sync_error_message = None
        self._store.save_scheduled_report(item)
        snapshot = snapshot_source if snapshot_source is not None else self._queue_snapshot_provider()
        payload: dict[str, ReportTaskForUser | ReportQueueSnapshotForUser] = {
            "task": task_for_user,
            "queueSnapshot": self._queue_snapshot_for_user(snapshot),
        }
        self._idempotency[request_id] = payload
        self._idempotency[stable_request_id] = payload
        return payload

    def tick_scheduled_reports(self, *, now: datetime | str | None = None) -> dict[str, tuple[TickItemResult, ...]]:
        if not self._allow_local_tick_for_tests:
            raise UiServiceError("INVALID_INPUT", "本地 tick 仅允许测试。")
        now_dt = self._as_datetime(now) if now is not None else self._now_provider()
        results: list[TickItemResult] = []
        for item in self._due_items(now_dt):
            item.state = "due"
            item.updated_at = self._now_iso()
            self._store.save_scheduled_report(item)
            request_id = f"tick:{item.id}:{item.next_run_at}"
            try:
                task = self._enqueue_report_task(self._build_task_input(item), request_id)
                task_payload = self._task_for_user_payload(task=task, schedule=item)
                item.last_run_task_id = self._task_id_from_queue_task(task_payload)
                item.state = "active"
                item.next_run_at = self.compute_next_run_at(
                    frequency=item.frequency,
                    time_of_day=item.time_of_day,
                    weekday=item.weekday,
                    after=now_dt,
                    timezone_name=_SCHEDULE_TIMEZONE,
                )
                item.updated_at = self._now_iso()
                self._store.save_scheduled_report(item)
                results.append(TickItemResult(scheduledReportId=item.id, enqueued=True, taskId=item.last_run_task_id))
            except Exception:
                item.state = "active"
                item.updated_at = self._now_iso()
                self._store.save_scheduled_report(item)
                results.append(TickItemResult(scheduledReportId=item.id, enqueued=False, message="定时报告暂未进入队列。"))
        return {"results": tuple(results)}

    def get_scheduled_report(self, scheduled_report_id: str) -> ScheduledReportForUser:
        return to_scheduled_report_for_user(self._get_schedule_or_raise(scheduled_report_id))

    def list_scheduled_reports_for_user(self) -> dict[str, Any]:
        return {"items": [self._scheduled_report_detail_for_user(item) for item in self._deduped_visible_schedules()]}

    @staticmethod
    def compute_next_run_at(
        *,
        frequency: str,
        time_of_day: str,
        weekday: int | None,
        after: datetime,
        timezone_name: str = _SCHEDULE_TIMEZONE,
    ) -> str:
        zone = ZoneInfo(timezone_name)
        base = after.astimezone(zone).replace(second=0, microsecond=0)
        hour, minute = SchedulerService._split_time_of_day(time_of_day)
        if frequency == "daily":
            candidate = base.replace(hour=hour, minute=minute)
            if candidate <= base:
                candidate += timedelta(days=1)
            return SchedulerService._to_iso_z(candidate.astimezone(UTC))
        if frequency == "weekly":
            if weekday is None:
                raise UiServiceError("INVALID_INPUT", "每周计划需要 weekday(0-6)。")
            delta_days = (weekday - base.weekday()) % 7
            candidate = base + timedelta(days=delta_days)
            candidate = candidate.replace(hour=hour, minute=minute)
            if candidate <= base:
                candidate += timedelta(days=7)
            return SchedulerService._to_iso_z(candidate.astimezone(UTC))
        raise UiServiceError("INVALID_INPUT", "当前只支持每天或每周生成完整报告。")

    def _find_matching_scheduled_report(
        self,
        *,
        instrument_code: str,
        market: MarketProfile,
        frequency: str,
        time_of_day: str,
        weekday: int | None,
        notification: dict[str, Any],
        workflow_settings: dict[str, Any],
    ) -> ScheduledReport | None:
        candidate: ScheduledReport | None = None
        for item in self._ordered_visible_schedules():
            if self._schedule_matches(
                item,
                instrument_code=instrument_code,
                market=market,
                frequency=frequency,
                time_of_day=time_of_day,
                weekday=weekday,
                notification=notification,
                workflow_settings=workflow_settings,
            ) and (candidate is None or self._schedule_is_newer(item, candidate)):
                candidate = item
        return candidate

    def _find_nearby_daily_scheduled_report(
        self,
        *,
        instrument_code: str,
        market: MarketProfile,
        frequency: str,
        time_of_day: str,
        weekday: int | None,
        notification: dict[str, Any],
        workflow_settings: dict[str, Any],
    ) -> ScheduledReport | None:
        candidate: ScheduledReport | None = None
        for item in self._ordered_visible_schedules():
            if self._schedule_matches_daily_duplicate_window(
                item,
                instrument_code=instrument_code,
                market=market,
                frequency=frequency,
                time_of_day=time_of_day,
                weekday=weekday,
                notification=notification,
                workflow_settings=workflow_settings,
            ) and (candidate is None or self._schedule_is_newer(item, candidate)):
                candidate = item
        return candidate

    def _deduped_visible_schedules(self) -> list[ScheduledReport]:
        out: list[ScheduledReport] = []
        for item in self._ordered_visible_schedules():
            duplicate_index = next((idx for idx, kept in enumerate(out) if self._schedules_collapse(kept, item)), None)
            if duplicate_index is None:
                out.append(item)
            elif self._schedule_is_newer(item, out[duplicate_index]):
                out[duplicate_index] = item
        return out

    def _canonical_visible_schedule(self, item: ScheduledReport) -> ScheduledReport | None:
        candidate: ScheduledReport | None = None
        for other in self._ordered_visible_schedules():
            if self._schedules_collapse(item, other) and (candidate is None or self._schedule_is_newer(other, candidate)):
                candidate = other
        return candidate

    def _delete_collapsed_schedule_duplicates(self, canonical: ScheduledReport) -> None:
        for item in self._ordered_collapsible_schedules():
            if item.id != canonical.id and self._schedules_collapse(canonical, item):
                self._delete_schedule_item(item)

    def _delete_schedule_item(self, item: ScheduledReport) -> None:
        self._remove_cron_job(item)
        item.state = "deleted"
        item.next_run_at = None
        item.updated_at = self._now_iso()
        item.sync_error_message = None
        self._store.save_scheduled_report(item)

    def _ordered_visible_schedules(self) -> list[ScheduledReport]:
        return self._ordered_schedules(states=_VISIBLE_SCHEDULE_STATES)

    def _ordered_collapsible_schedules(self) -> list[ScheduledReport]:
        return self._ordered_schedules(states=_COLLAPSIBLE_SCHEDULE_STATES)

    def _ordered_schedules(self, *, states: set[str]) -> list[ScheduledReport]:
        priority = {"active": 0, "due": 0, "enqueued": 0, "paused": 1}
        return sorted(
            self._store.list_scheduled_reports(states=states),
            key=lambda item: (priority.get(item.state, 9), item.created_at, item.id),
        )

    @staticmethod
    def _schedule_matches(
        item: ScheduledReport,
        *,
        instrument_code: str,
        market: MarketProfile,
        frequency: str,
        time_of_day: str,
        weekday: int | None,
        notification: dict[str, Any],
        workflow_settings: dict[str, Any],
    ) -> bool:
        return (
            item.instrument_code == instrument_code
            and item.market == market
            and item.frequency == frequency
            and item.time_of_day == time_of_day
            and item.weekday == weekday
            and item.notification == notification
            and item.workflow_settings == workflow_settings
        )

    def _schedule_matches_daily_duplicate_window(
        self,
        item: ScheduledReport,
        *,
        instrument_code: str,
        market: MarketProfile,
        frequency: str,
        time_of_day: str,
        weekday: int | None,
        notification: dict[str, Any],
        workflow_settings: dict[str, Any],
    ) -> bool:
        if not self._schedule_matches_identity_except_time(
            item,
            instrument_code=instrument_code,
            market=market,
            frequency=frequency,
            weekday=weekday,
            notification=notification,
            workflow_settings=workflow_settings,
        ):
            return False
        return self._time_of_day_distance_minutes(item.time_of_day, time_of_day) <= _DAILY_DUPLICATE_WINDOW_MINUTES

    def _schedules_collapse(self, left: ScheduledReport, right: ScheduledReport) -> bool:
        if self._schedule_matches(
            left,
            instrument_code=right.instrument_code,
            market=right.market,
            frequency=right.frequency,
            time_of_day=right.time_of_day,
            weekday=right.weekday,
            notification=right.notification,
            workflow_settings=right.workflow_settings,
        ):
            return True
        if not self._schedule_matches_identity_except_time(
            left,
            instrument_code=right.instrument_code,
            market=right.market,
            frequency=right.frequency,
            weekday=right.weekday,
            notification=right.notification,
            workflow_settings=right.workflow_settings,
        ):
            return False
        if left.time_of_day == right.time_of_day:
            return True
        return self._time_of_day_distance_minutes(left.time_of_day, right.time_of_day) <= _DAILY_DUPLICATE_WINDOW_MINUTES

    @staticmethod
    def _schedule_matches_identity_except_time(
        item: ScheduledReport,
        *,
        instrument_code: str,
        market: MarketProfile,
        frequency: str,
        weekday: int | None,
        notification: dict[str, Any],
        workflow_settings: dict[str, Any],
    ) -> bool:
        if item.frequency != "daily" or frequency != "daily":
            return False
        return (
            item.instrument_code == instrument_code
            and item.market == market
            and item.weekday == weekday
            and item.notification == notification
            and item.workflow_settings == workflow_settings
        )

    @staticmethod
    def _schedule_is_newer(left: ScheduledReport, right: ScheduledReport) -> bool:
        return (left.updated_at, left.created_at, left.id) > (right.updated_at, right.created_at, right.id)

    @staticmethod
    def _time_of_day_distance_minutes(left: str, right: str) -> int:
        left_hour, left_minute = SchedulerService._split_time_of_day(left)
        right_hour, right_minute = SchedulerService._split_time_of_day(right)
        diff = abs((left_hour * 60 + left_minute) - (right_hour * 60 + right_minute))
        return min(diff, 24 * 60 - diff)

    def _get_schedule_or_raise(self, scheduled_report_id: str) -> ScheduledReport:
        item = self._store.get_scheduled_report(scheduled_report_id)
        if item is None or item.state in {"deleted", "sync_failed"}:
            raise UiServiceError("SCHEDULE_NOT_FOUND", "定时报告不存在。")
        return item

    def _scheduled_report_detail_for_user(self, item: ScheduledReport) -> dict[str, Any]:
        dto = to_scheduled_report_for_user(item)
        return {
            "scheduledReportId": dto.scheduledReportId,
            "instrumentCode": dto.instrumentCode,
            "instrumentName": dto.instrumentName,
            "market": dto.market.value,
            "frequency": dto.frequency,
            "timeOfDay": dto.timeOfDay,
            "weekday": dto.weekday,
            "notification": {"channel": dto.notification.channel, "enabled": dto.notification.enabled},
            "state": dto.state,
            "nextRunAt": dto.nextRunAt,
            "lastRunTaskId": item.last_run_task_id,
            "cronJobId": item.openclaw_cron_job_id,
            "lastCronRunId": item.last_cron_run_id,
            "syncErrorMessage": item.sync_error_message,
            "updatedAt": item.updated_at,
        }

    def _build_task_input(self, item: ScheduledReport) -> dict[str, Any]:
        now_date = self._now_iso()[:10]
        request = build_report_run_request(
            ticker=item.instrument_code,
            company_name=item.instrument_name,
            market=item.market.value,
            current_date=now_date,
            settings=_report_settings_from_payload(item.workflow_settings),
        )
        return {
            "source": "scheduled",
            "instrumentCode": request.ticker,
            "instrumentName": request.company_name,
            "market": request.market,
            "companyName": request.company_name,
            "currencySymbol": request.currency_symbol,
            "startDate": request.start_date,
            "endDate": request.end_date,
            "currentDate": request.current_date,
            "workflowSettings": _workflow_settings_from_request(request),
        }

    def _task_for_user_payload(self, *, task: dict[str, Any], schedule: ScheduledReport) -> dict[str, Any]:
        payload = dict(task)
        now_iso = self._now_iso()
        payload.setdefault("id", payload.get("taskId") or payload.get("reportTaskId") or f"task-{schedule.id}")
        payload.setdefault("taskId", payload["id"])
        payload.setdefault("source", "scheduled")
        payload.setdefault("status", "queued")
        payload.setdefault("statusLabel", "排队中")
        payload.setdefault("instrumentCode", schedule.instrument_code)
        payload.setdefault("instrumentName", schedule.instrument_name)
        payload.setdefault("market", schedule.market.value)
        payload.setdefault("companyName", schedule.instrument_name or schedule.instrument_code)
        payload.setdefault("currencySymbol", str(schedule.workflow_settings.get("defaultCurrencySymbol", "$")))
        payload.setdefault("startDate", now_iso[:10])
        payload.setdefault("endDate", now_iso[:10])
        payload.setdefault("currentDate", now_iso[:10])
        payload.setdefault("queuePosition", 1)
        payload.setdefault("progress", None)
        payload.setdefault("reportId", None)
        payload.setdefault("failure", None)
        payload.setdefault("createdAt", now_iso)
        payload.setdefault("startedAt", None)
        payload.setdefault("finishedAt", None)
        return payload

    def _scheduled_report_enqueue_parts(self, enqueue_result: Any) -> tuple[Any, Any | None]:
        if isinstance(enqueue_result, Mapping) and "task" in enqueue_result:
            return enqueue_result["task"], enqueue_result.get("queueSnapshot")
        return enqueue_result, None

    def _task_for_user_from_enqueue_result(self, task: Any, *, schedule: ScheduledReport) -> ReportTaskForUser:
        if isinstance(task, Mapping):
            return to_report_task_for_user(self._task_for_user_payload(task=dict(task), schedule=schedule))
        return to_report_task_for_user(task)

    def _scheduled_report_window_request_id(self, item: ScheduledReport, task_input: Mapping[str, Any]) -> str:
        return (
            f"scheduled-report:{item.id}:"
            f"{task_input['startDate']}:{task_input['endDate']}:{task_input['currentDate']}"
        )

    def _is_duplicate_scheduled_report_window(self, item: ScheduledReport) -> bool:
        if not item.next_run_at:
            return False
        try:
            next_run_at = self._as_datetime(item.next_run_at)
        except Exception:
            return False
        return self._now_provider().astimezone(UTC) < next_run_at

    def _is_duplicate_scheduled_report_wake(self, item: ScheduledReport, *, cron_run_id: str) -> bool:
        if item.last_cron_run_id and cron_run_id and item.last_cron_run_id == cron_run_id:
            return True
        return self._is_duplicate_scheduled_report_window(item)

    def _last_scheduled_report_task_for_user(
        self,
        *,
        snapshot: Any,
        schedule: ScheduledReport,
    ) -> ReportTaskForUser | None:
        task_id = schedule.last_run_task_id
        if isinstance(snapshot, Mapping) and task_id:
            for candidate in (snapshot.get("runningTask"), snapshot.get("running_task")):
                found = self._snapshot_task_by_id(candidate, task_id)
                if found is not None:
                    return to_report_task_for_user(self._task_for_user_payload(task=dict(found), schedule=schedule))
            for key in ("queuedTasks", "queued_tasks"):
                queued = snapshot.get(key)
                if isinstance(queued, list):
                    for candidate in queued:
                        found = self._snapshot_task_by_id(candidate, task_id)
                        if found is not None:
                            return to_report_task_for_user(self._task_for_user_payload(task=dict(found), schedule=schedule))
        return None

    @staticmethod
    def _snapshot_task_by_id(candidate: Any, task_id: str) -> Mapping[str, Any] | None:
        if not isinstance(candidate, Mapping):
            return None
        for key in ("taskId", "reportTaskId", "id"):
            if str(candidate.get(key) or "") == task_id:
                return candidate
        return None

    def _queue_snapshot_for_user(self, snapshot: Any) -> ReportQueueSnapshotForUser:
        if not isinstance(snapshot, Mapping):
            return to_report_queue_snapshot_for_user(snapshot)
        normalized = dict(snapshot)
        queued = normalized.get("queuedTasks") or normalized.get("queued_tasks") or []
        normalized["queuedTasks"] = [self._normalize_snapshot_task(item) for item in queued]
        running = normalized.get("runningTask") or normalized.get("running_task")
        normalized["runningTask"] = self._normalize_snapshot_task(running) if running else None
        return to_report_queue_snapshot_for_user(normalized)

    def _normalize_snapshot_task(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = dict(task)
        now_iso = self._now_iso()
        payload.setdefault("id", payload.get("taskId") or payload.get("reportTaskId") or "task-snapshot")
        payload.setdefault("taskId", payload["id"])
        payload.setdefault("source", "scheduled")
        payload.setdefault("status", "queued")
        payload.setdefault("statusLabel", "排队中")
        payload.setdefault("instrumentCode", "UNKNOWN")
        payload.setdefault("instrumentName", None)
        payload.setdefault("market", MarketProfile.US.value)
        payload.setdefault("companyName", payload["instrumentCode"])
        payload.setdefault("currencySymbol", "$")
        payload.setdefault("startDate", now_iso[:10])
        payload.setdefault("endDate", now_iso[:10])
        payload.setdefault("currentDate", now_iso[:10])
        payload.setdefault("queuePosition", 1)
        payload.setdefault("progress", None)
        payload.setdefault("reportId", None)
        payload.setdefault("failure", None)
        payload.setdefault("createdAt", now_iso)
        payload.setdefault("startedAt", None)
        payload.setdefault("finishedAt", None)
        return payload

    def _due_items(self, now: datetime) -> tuple[ScheduledReport, ...]:
        out: list[ScheduledReport] = []
        for item in self._store.list_scheduled_reports(states={"active"}):
            if item.state != "active":
                continue
            if not item.next_run_at:
                continue
            due_at = self._as_datetime(item.next_run_at)
            if due_at <= now.astimezone(UTC):
                out.append(item)
        return tuple(out)

    def _update_cron_enabled(self, item: ScheduledReport, *, enabled: bool) -> None:
        if self._cron_adapter is None or not item.openclaw_cron_job_id:
            return
        try:
            self._cron_adapter.update_job(job_id=item.openclaw_cron_job_id, patch={"enabled": enabled})
        except Exception as exc:
            item.sync_error_message = "OpenClaw cron 同步失败。"
            item.updated_at = self._now_iso()
            self._store.save_scheduled_report(item)
            raise UiServiceError("CRON_SYNC_FAILED", "定时报告同步 OpenClaw cron 失败。") from exc

    def _remove_cron_job(self, item: ScheduledReport) -> None:
        if self._cron_adapter is None or not item.openclaw_cron_job_id:
            return
        try:
            self._cron_adapter.remove_job(job_id=item.openclaw_cron_job_id)
        except Exception as exc:
            item.sync_error_message = "OpenClaw cron 同步失败。"
            item.updated_at = self._now_iso()
            self._store.save_scheduled_report(item)
            raise UiServiceError("CRON_SYNC_FAILED", "定时报告同步 OpenClaw cron 失败。") from exc

    def _cron_job_name(self, item: ScheduledReport) -> str:
        user_id = str(self._user_id_provider() or "").strip()
        if user_id:
            return f"scheduled-report:{user_id}:{item.id}"
        return f"scheduled-report:{item.id}"

    def _cron_schedule(self, item: ScheduledReport) -> dict[str, Any]:
        hour, minute = self._split_time_of_day(item.time_of_day)
        if item.frequency == "daily":
            expr = f"{minute} {hour} * * *"
        elif item.frequency == "weekly":
            if item.weekday is None:
                raise UiServiceError("INVALID_INPUT", "每周计划需要 weekday(0-6)。")
            cron_weekday = 0 if item.weekday == 6 else item.weekday + 1
            expr = f"{minute} {hour} * * {cron_weekday}"
        else:
            raise UiServiceError("INVALID_INPUT", "当前只支持每天或每周生成完整报告。")
        return {"kind": "cron", "expr": expr, "tz": _SCHEDULE_TIMEZONE, "staggerMs": 0}

    @staticmethod
    def _scheduled_report_cron_payload(scheduled_report_id: str) -> dict[str, Any]:
        return {
            "kind": "toolCall",
            "toolName": _SCHEDULED_WORK_TOOL,
            "input": {
                "kind": "scheduled_report",
                "scheduledReportId": scheduled_report_id,
                "cronRunId": "auto",
            },
        }

    def _normalize_notification(self, notification: dict[str, Any] | None) -> dict[str, Any]:
        source = notification or {}
        channel = str(source.get("channel", "in_app")).strip() or "in_app"
        enabled = bool(source.get("enabled", True))
        return {"channel": channel, "enabled": enabled}

    @staticmethod
    def _normalize_workflow_settings(
        workflow_settings: dict[str, Any] | None,
        market: MarketProfile,
    ) -> dict[str, Any]:
        source = dict(workflow_settings or {})
        symbol = "¥" if market == MarketProfile.CN_A else "$"
        currency = "CNY" if market == MarketProfile.CN_A else "USD"
        profile = market.value
        return {
            "maxDebateRounds": int(source.get("maxDebateRounds", 1)),
            "maxRiskDiscussRounds": int(source.get("maxRiskDiscussRounds", 1)),
            "frontlineExecutionMode": str(source.get("frontlineExecutionMode", "parallel")),
            "defaultProfile": str(source.get("defaultProfile", profile)),
            "defaultMarket": str(source.get("defaultMarket", market.value)),
            "defaultCurrency": str(source.get("defaultCurrency", currency)),
            "defaultCurrencySymbol": str(source.get("defaultCurrencySymbol", symbol)),
        }

    @staticmethod
    def _normalize_time_of_day(value: str) -> str:
        hour, minute = SchedulerService._split_time_of_day(value)
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _split_time_of_day(value: str) -> tuple[int, int]:
        text = value.strip()
        parts = text.split(":")
        if len(parts) != 2:
            raise UiServiceError("INVALID_INPUT", "时间格式应为 HH:MM。")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise UiServiceError("INVALID_INPUT", "时间格式应为 HH:MM。") from exc
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise UiServiceError("INVALID_INPUT", "时间格式应为 HH:MM。")
        return hour, minute

    def _as_market_profile(self, market: MarketProfile | str) -> MarketProfile:
        if isinstance(market, MarketProfile):
            return market
        text = str(market).strip()
        for candidate in MarketProfile:
            if candidate.value == text:
                return candidate
        raise UiServiceError("INVALID_INPUT", f"不支持的市场: {market}")

    def _next_schedule_id(self) -> str:
        self._seq += 1
        return f"schedule-{self._seq}"

    def _highest_schedule_seq(self) -> int:
        highest = 0
        for report in self._store.list_scheduled_reports():
            prefix, sep, suffix = report.id.partition("-")
            if prefix != "schedule" or sep != "-":
                continue
            try:
                highest = max(highest, int(suffix))
            except ValueError:
                continue
        return highest

    @staticmethod
    def _task_id_from_queue_task(task: dict[str, Any]) -> str:
        for key in ("reportTaskId", "taskId", "id"):
            value = task.get(key)
            if value:
                return str(value)
        raise UiServiceError("ASSISTANT_UNAVAILABLE", "队列任务缺少 task id。")

    @staticmethod
    def _cron_run_id_from_result(result: Any) -> str | None:
        if isinstance(result, str) and result.strip():
            return result.strip()
        if isinstance(result, dict):
            for key in ("runId", "id", "cronRunId"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _now_iso(self) -> str:
        return self._to_iso_z(self._now_provider())

    @staticmethod
    def _to_iso_z(value: datetime) -> str:
        normalized = value.astimezone(UTC).replace(microsecond=0)
        return normalized.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _as_datetime(raw: datetime | str) -> datetime:
        if isinstance(raw, datetime):
            return raw.astimezone(UTC)
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone(UTC)


def _report_settings_from_payload(payload: Mapping[str, Any]) -> ReportWorkflowSettings:
    return ReportWorkflowSettings(
        max_debate_rounds=int(payload.get("maxDebateRounds", 1)),
        max_risk_discuss_rounds=int(payload.get("maxRiskDiscussRounds", 1)),
        frontline_execution_mode=str(payload.get("frontlineExecutionMode", "parallel")),
        default_profile=str(payload.get("defaultProfile", "CN_A")),
        default_market=str(payload.get("defaultMarket", "CN_A")),
        default_currency=str(payload.get("defaultCurrency", "CNY")),
        default_currency_symbol=str(payload.get("defaultCurrencySymbol", "\u00a5")),
    )


def _workflow_settings_from_request(request: Any) -> dict[str, Any]:
    return {
        "maxDebateRounds": request.max_debate_rounds,
        "maxRiskDiscussRounds": request.max_risk_discuss_rounds,
        "frontlineExecutionMode": request.frontline_execution_mode,
        "defaultProfile": request.profile,
        "defaultMarket": request.market,
        "defaultCurrency": request.currency,
        "defaultCurrencySymbol": request.currency_symbol,
    }
