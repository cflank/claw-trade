from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from typing import Callable
from zoneinfo import ZoneInfo

from claw_trade.data_gateway.selection_api import resolve_crypto_selection_trade_date_for_scheduler
from claw_trade.selection.data_job import SelectionDataJobExecution
from claw_trade.selection.models import (
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionDataNeedAudit,
    SelectionRunPlan,
    SelectionTriggerSource,
    SelectRequest,
)
from claw_trade.selection.scheduler import (
    SelectionScheduleContext,
    SelectionSchedulingError,
    schedule_selection_job,
)
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore


_ACTIVE_DATA_RUN_STALE_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class SelectionDataRefreshResult:
    status: str
    selection_run_id: str | None
    trade_date: str | None
    reason: str
    error_code: str | None = None


class SelectionDataRefreshService:
    """Starts the background selection data job when `/select` finds no usable candidate cache."""

    def __init__(
        self,
        *,
        store: SelectionRunStore,
        run_data_job: Callable[[SelectionRunPlan], SelectionDataJobExecution],
        resolve_closed_trade_date: Callable[[str | None], str],
        load_approved_strategy_config_ref: Callable[[SelectionMarket, SelectionProfile], str | None],
        build_data_need_audit: Callable[..., SelectionDataNeedAudit],
        run_data_check: Callable[[SelectionRunPlan], object] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        data_refresh_permission_checker: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._run_data_job = run_data_job
        self._resolve_closed_trade_date = resolve_closed_trade_date
        self._load_approved_strategy_config_ref = load_approved_strategy_config_ref
        self._build_data_need_audit = build_data_need_audit
        self._run_data_check = run_data_check or run_data_job
        self._now_fn = now_fn or _utc_now
        self._run_id_factory = run_id_factory
        self._data_refresh_permission_checker = data_refresh_permission_checker
        self._lock = Lock()
        self._auto_refresh_stop = Event()
        self._auto_refresh_thread: Thread | None = None
        self._auto_refresh_key: tuple[SelectionMarket, SelectionProfile, str] | None = None

    def request_refresh(
        self,
        *,
        request: SelectRequest,
        unavailable_code: object,
        select_workflow_run_id: str,
        force_refresh: bool = False,
    ) -> SelectionDataRefreshResult:
        blocked = self._assert_data_refresh_allowed()
        if blocked is not None:
            return blocked
        reason = str(getattr(unavailable_code, "value", unavailable_code))
        try:
            trade_date = self._resolve_trade_date_for_market(request.market, request.trade_date)
        except Exception as exc:  # noqa: BLE001
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=None,
                trade_date=request.trade_date,
                reason=f"{reason}:trade_date_resolution_failed",
                error_code=type(exc).__name__,
            )

        with self._lock:
            if not force_refresh:
                existing = self._store.load_latest_completed_selection_run(
                    market=request.market,
                    profile=request.profile,
                    trade_date=trade_date,
                    now=self._now_fn(),
                )
                if existing.is_available and existing.run is not None:
                    return SelectionDataRefreshResult(
                        status="completed",
                        selection_run_id=existing.run.run_plan.selection_run_id,
                        trade_date=trade_date,
                        reason=f"{reason}:candidate_cache_valid",
                    )
            active = self._store.load_active_data_run_record(
                market=request.market,
                profile=request.profile,
                trade_date=trade_date,
            )
            if active is not None and self._mark_stale_active_record_failed(active):
                active = None
            if active is not None:
                return SelectionDataRefreshResult(
                    status="already_running",
                    selection_run_id=active.run_plan.selection_run_id,
                    trade_date=trade_date,
                    reason=reason,
                )
            try:
                plan = schedule_selection_job(
                    context=SelectionScheduleContext(
                        market=request.market,
                        profile=request.profile,
                        trade_date=trade_date,
                        trigger_source=SelectionTriggerSource.SCHEDULED,
                    ),
                    resolve_closed_trade_date=self._resolver_for_market(request.market),
                    has_active_job=lambda market, profile, date_value: self._store.has_active_data_run(
                        market=market,
                        profile=profile,
                        trade_date=date_value,
                    ),
                    load_approved_strategy_config_ref=self._load_approved_strategy_config_ref,
                    build_data_need_audit=self._build_data_need_audit,
                    run_id_factory=self._run_id_factory,
                )
            except SelectionSchedulingError as exc:
                return SelectionDataRefreshResult(
                    status="failed",
                    selection_run_id=None,
                    trade_date=trade_date,
                    reason=reason,
                    error_code=exc.code,
                )

            self._store.save_data_run_record(
                SelectionDataRunRecord(
                    run_plan=plan,
                    data_run=SelectionDataRun(
                        selection_run_id=plan.selection_run_id,
                        status=SelectionDataRunStatus.PLANNED,
                        lease_id=f"select-refresh://{select_workflow_run_id}",
                        started_at=self._now_fn().isoformat(),
                        updated_at=self._now_fn().isoformat(),
                    ),
                    manifest=None,
                )
            )

            thread = Thread(
                target=self._run_job_and_record_failure,
                args=(plan,),
                daemon=True,
                name=f"selection-refresh-{plan.selection_run_id}",
            )
            thread.start()
            return SelectionDataRefreshResult(
                status="started",
                selection_run_id=plan.selection_run_id,
                trade_date=trade_date,
                reason=reason,
            )

    def start_automatic_refresh_scheduler(self) -> None:
        with self._lock:
            if self._auto_refresh_thread is not None and self._auto_refresh_thread.is_alive():
                return
            self._auto_refresh_stop.clear()
            self._auto_refresh_thread = Thread(
                target=self._automatic_refresh_loop,
                daemon=True,
                name="selection-auto-refresh-scheduler",
            )
            self._auto_refresh_thread.start()

    def stop_automatic_refresh_scheduler(self, *, timeout_seconds: float = 1.0) -> None:
        self._auto_refresh_stop.set()
        thread = self._auto_refresh_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_seconds)

    def run_automatic_refresh_once(
        self,
        *,
        reason: str,
        market: SelectionMarket = SelectionMarket.CN_A,
        profile: SelectionProfile | None = None,
        force_refresh: bool = False,
    ) -> SelectionDataRefreshResult:
        blocked = self._assert_data_refresh_allowed()
        if blocked is not None:
            return blocked
        if profile is None:
            profile = SelectionProfile.CRYPTO if market == SelectionMarket.CRYPTO else SelectionProfile.CN_A
        try:
            trade_date = self._resolve_trade_date_for_market(market, None)
        except Exception as exc:  # noqa: BLE001
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=None,
                trade_date=None,
                reason=f"{reason}:trade_date_resolution_failed",
                error_code=type(exc).__name__,
            )

        auto_refresh_key = (market, profile, trade_date)
        with self._lock:
            if not force_refresh:
                existing = self._store.load_latest_completed_selection_run(
                    market=market,
                    profile=profile,
                    trade_date=trade_date,
                    now=self._now_fn(),
                )
                if existing.is_available and existing.run is not None:
                    return SelectionDataRefreshResult(
                        status="completed",
                        selection_run_id=existing.run.run_plan.selection_run_id,
                        trade_date=trade_date,
                        reason=f"{reason}:candidate_cache_valid",
                    )
            if self._auto_refresh_key == auto_refresh_key:
                return SelectionDataRefreshResult(
                    status="already_running",
                    selection_run_id=None,
                    trade_date=trade_date,
                    reason=reason,
                )
            active = self._store.load_active_data_run_record(
                market=market,
                profile=profile,
                trade_date=trade_date,
            )
            if active is not None and self._mark_stale_active_record_failed(active):
                active = None
            if active is not None:
                return SelectionDataRefreshResult(
                    status="already_running",
                    selection_run_id=None,
                    trade_date=trade_date,
                    reason=reason,
                )
            try:
                plan = schedule_selection_job(
                    context=SelectionScheduleContext(
                        market=market,
                        profile=profile,
                        trade_date=trade_date,
                        trigger_source=SelectionTriggerSource.SCHEDULED,
                    ),
                    resolve_closed_trade_date=self._resolver_for_market(market),
                    has_active_job=lambda market, profile, date_value: self._store.has_active_data_run(
                        market=market,
                        profile=profile,
                        trade_date=date_value,
                    ),
                    load_approved_strategy_config_ref=self._load_approved_strategy_config_ref,
                    build_data_need_audit=self._build_data_need_audit,
                    run_id_factory=self._run_id_factory,
                )
            except SelectionSchedulingError as exc:
                return SelectionDataRefreshResult(
                    status="failed",
                    selection_run_id=None,
                    trade_date=trade_date,
                    reason=reason,
                    error_code=exc.code,
                )
            self._store.save_data_run_record(
                SelectionDataRunRecord(
                    run_plan=plan,
                    data_run=SelectionDataRun(
                        selection_run_id=plan.selection_run_id,
                        status=SelectionDataRunStatus.PLANNED,
                        lease_id=f"auto-refresh://{reason}/{market.value}",
                        started_at=self._now_fn().isoformat(),
                        updated_at=self._now_fn().isoformat(),
                    ),
                    manifest=None,
                )
            )
            self._auto_refresh_key = auto_refresh_key

        try:
            execution = self._run_data_check(plan)
        except Exception as exc:  # noqa: BLE001
            failed_at = self._now_fn().isoformat()
            self._store.save_data_run_record(
                SelectionDataRunRecord(
                    run_plan=plan,
                    data_run=SelectionDataRun(
                        selection_run_id=plan.selection_run_id,
                        status=SelectionDataRunStatus.FAILED,
                        lease_id=f"auto-refresh://{reason}/{market.value}",
                        started_at=failed_at,
                        updated_at=failed_at,
                        failed_at=failed_at,
                        failure_code="selection_auto_refresh_failed",
                        failure_reason=f"{type(exc).__name__}: {exc}",
                    ),
                    manifest=None,
                )
            )
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=plan.selection_run_id,
                trade_date=trade_date,
                reason=reason,
                error_code=type(exc).__name__,
            )
        finally:
            with self._lock:
                if self._auto_refresh_key == auto_refresh_key:
                    self._auto_refresh_key = None

        record = getattr(execution, "record", None)
        if not isinstance(record, SelectionDataRunRecord):
            record = self._store.load_data_run_record(plan.selection_run_id)
        return self._refresh_result_from_data_run_record(
            record=record,
            plan=plan,
            trade_date=trade_date,
            reason=reason,
        )

    def latest_progress_for_user(
        self,
        *,
        market: SelectionMarket = SelectionMarket.CN_A,
        profile: SelectionProfile = SelectionProfile.CN_A,
        trade_date: str | None = None,
        include_terminal: bool = True,
    ) -> dict[str, object]:
        if trade_date is None:
            record = self._store.load_latest_active_data_run_record(market=market, profile=profile)
            if record is None:
                if not include_terminal:
                    return {"selectionProgress": None}
                record = self._store.load_latest_any_data_run_record(market=market, profile=profile)
            if record is None:
                return {"selectionProgress": None}
            if self._mark_stale_active_record_failed(record):
                if not include_terminal:
                    return {"selectionProgress": None}
                record = self._store.load_latest_any_data_run_record(market=market, profile=profile)
                if record is None:
                    return {"selectionProgress": None}
            display_trade_date = self._canonical_trade_date_for_record(record)
            if (
                record.data_run.status == SelectionDataRunStatus.FAILED
                and display_trade_date != record.run_plan.trade_date
                and self._has_valid_completed_run_for_trade_date(
                    market=record.run_plan.market,
                    profile=record.run_plan.profile,
                    trade_date=display_trade_date,
                )
            ):
                return {"selectionProgress": None}
            if self._has_valid_completed_run_for_record(record):
                return {"selectionProgress": None}
            return {
                "selectionProgress": _data_run_progress_for_user(
                    record.data_run,
                    trade_date=display_trade_date,
                )
            }
        try:
            resolved_trade_date = self._resolve_trade_date_for_market(market, trade_date)
        except Exception:  # noqa: BLE001
            return {"selectionProgress": None}
        record = self._store.load_active_data_run_record(
            market=market,
            profile=profile,
            trade_date=resolved_trade_date,
        )
        if record is None:
            if not include_terminal:
                return {"selectionProgress": None}
            record = self._store.load_latest_data_run_record(
                market=market,
                profile=profile,
                trade_date=resolved_trade_date,
            )
        if record is None:
            return {"selectionProgress": None}
        if self._mark_stale_active_record_failed(record):
            if not include_terminal:
                return {"selectionProgress": None}
            record = self._store.load_latest_data_run_record(
                market=market,
                profile=profile,
                trade_date=resolved_trade_date,
            )
            if record is None:
                return {"selectionProgress": None}
        if self._has_valid_completed_run_for_record(record):
            return {"selectionProgress": None}
        return {"selectionProgress": _data_run_progress_for_user(record.data_run, trade_date=resolved_trade_date)}

    def cancel_refresh(self, *, selection_run_id: str) -> bool:
        record = self._store.load_data_run_record(selection_run_id)
        if record is None:
            return False
        if record.data_run.status in {
            SelectionDataRunStatus.NO_CANDIDATE,
            SelectionDataRunStatus.COMPLETED,
            SelectionDataRunStatus.FAILED,
        }:
            return record.data_run.failure_code == "selection_refresh_cancelled"
        cancelled = replace(
            record.data_run,
            status=SelectionDataRunStatus.FAILED,
            failed_at=self._now_fn().isoformat(),
            updated_at=self._now_fn().isoformat(),
            failure_code="selection_refresh_cancelled",
            failure_reason="用户取消了本次选股数据刷新。",
        )
        self._store.save_data_run_record(replace(record, data_run=cancelled, manifest=None))
        return True

    def _has_valid_completed_run_for_record(self, record: SelectionDataRunRecord) -> bool:
        if record.data_run.status == SelectionDataRunStatus.COMPLETED:
            return True
        if record.data_run.status != SelectionDataRunStatus.FAILED:
            return False
        return self._has_valid_completed_run_for_trade_date(
            market=record.run_plan.market,
            profile=record.run_plan.profile,
            trade_date=record.run_plan.trade_date,
        )

    def _has_valid_completed_run_for_trade_date(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
        trade_date: str,
    ) -> bool:
        completed = self._store.load_latest_completed_selection_run(
            market=market,
            profile=profile,
            trade_date=trade_date,
            now=self._now_fn(),
        )
        return completed.is_available and completed.run is not None

    def _refresh_result_from_data_run_record(
        self,
        *,
        record: SelectionDataRunRecord | None,
        plan: SelectionRunPlan,
        trade_date: str,
        reason: str,
    ) -> SelectionDataRefreshResult:
        if record is None:
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=plan.selection_run_id,
                trade_date=trade_date,
                reason=f"{reason}:data_run_record_missing",
                error_code="selection_data_run_record_missing",
            )
        status = record.data_run.status
        if status == SelectionDataRunStatus.COMPLETED:
            return SelectionDataRefreshResult(
                status="completed",
                selection_run_id=plan.selection_run_id,
                trade_date=trade_date,
                reason=reason,
            )
        if status == SelectionDataRunStatus.NO_CANDIDATE:
            return SelectionDataRefreshResult(
                status="no_candidate",
                selection_run_id=plan.selection_run_id,
                trade_date=trade_date,
                reason=reason,
            )
        if status == SelectionDataRunStatus.FAILED:
            failure_reason = record.data_run.failure_reason or reason
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=plan.selection_run_id,
                trade_date=trade_date,
                reason=failure_reason,
                error_code=record.data_run.failure_code or "selection_data_run_failed",
            )
        return SelectionDataRefreshResult(
            status="failed",
            selection_run_id=plan.selection_run_id,
            trade_date=trade_date,
            reason=f"{reason}:data_run_not_terminal:{status.value}",
            error_code="selection_data_run_not_terminal",
        )

    def _canonical_trade_date_for_record(self, record: SelectionDataRunRecord) -> str:
        try:
            return self._resolve_trade_date_for_market(record.run_plan.market, record.run_plan.trade_date)
        except Exception:  # noqa: BLE001
            return record.run_plan.trade_date

    def _resolve_trade_date_for_market(self, market: SelectionMarket, trade_date: str | None) -> str:
        if market == SelectionMarket.CRYPTO:
            return resolve_crypto_selection_trade_date_for_scheduler(trade_date)
        return self._resolve_closed_trade_date(trade_date)

    def _resolver_for_market(self, market: SelectionMarket) -> Callable[[str | None], str]:
        if market == SelectionMarket.CRYPTO:
            return resolve_crypto_selection_trade_date_for_scheduler
        return self._resolve_closed_trade_date

    def _assert_data_refresh_allowed(self) -> SelectionDataRefreshResult | None:
        if self._data_refresh_permission_checker is None:
            return None
        try:
            self._data_refresh_permission_checker()
        except PermissionError as exc:
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=None,
                trade_date=None,
                reason=str(exc),
                error_code="license_blocked",
            )
        return None

    def _mark_stale_active_record_failed(self, record: SelectionDataRunRecord) -> bool:
        if record.data_run.status not in {
            SelectionDataRunStatus.PLANNED,
            SelectionDataRunStatus.LEASE_PENDING,
            SelectionDataRunStatus.RUNNING,
            SelectionDataRunStatus.FETCHING_DATA,
            SelectionDataRunStatus.NORMALIZING_INPUTS,
            SelectionDataRunStatus.BUILDING_FEATURES,
            SelectionDataRunStatus.FILTERING_AND_SCORING,
            SelectionDataRunStatus.BUILDING_CANDIDATE_CACHE,
            SelectionDataRunStatus.APPROVING_CANDIDATE_CACHE,
        }:
            return False
        last_update = _parse_data_run_timestamp(record.data_run.updated_at)
        if last_update is None:
            return False
        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if now.astimezone(UTC) - last_update <= _ACTIVE_DATA_RUN_STALE_AFTER:
            return False
        failed_at = now.isoformat()
        failed = replace(
            record.data_run,
            status=SelectionDataRunStatus.FAILED,
            updated_at=failed_at,
            failed_at=failed_at,
            failure_code="selection_data_run_interrupted",
            failure_reason=(
                "补数据任务超过 15 分钟没有进度写入；按中断任务处理。"
                "请重新发送 /select 启动新的补数据。"
            ),
        )
        self._store.save_data_run_record(replace(record, data_run=failed, manifest=None))
        return True

    def _run_job_and_record_failure(self, plan: SelectionRunPlan) -> None:
        try:
            self._run_data_job(plan)
        except Exception as exc:  # noqa: BLE001
            failed_at = self._now_fn().isoformat()
            self._store.save_data_run_record(
                SelectionDataRunRecord(
                    run_plan=plan,
                    data_run=SelectionDataRun(
                        selection_run_id=plan.selection_run_id,
                        status=SelectionDataRunStatus.FAILED,
                        lease_id=f"lease://{plan.selection_run_id}",
                        started_at=failed_at,
                        updated_at=failed_at,
                        failed_at=failed_at,
                        failure_code="selection_refresh_job_failed",
                        failure_reason=f"{type(exc).__name__}: {exc}",
                    ),
                    manifest=None,
                )
            )

    def _automatic_refresh_loop(self) -> None:
        self._run_automatic_refresh_batch(reason="startup_data_check")
        while not self._auto_refresh_stop.wait(_seconds_until_next_daily_refresh(self._now_fn())):
            self._run_automatic_refresh_batch(reason="daily_1600_data_check")

    def _run_automatic_refresh_batch(self, *, reason: str) -> None:
        for market, profile in (
            (SelectionMarket.CN_A, SelectionProfile.CN_A),
            (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO),
        ):
            self.run_automatic_refresh_once(reason=reason, market=market, profile=profile)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _seconds_until_next_daily_refresh(now: datetime) -> float:
    shanghai_now = _to_shanghai(now)
    target = shanghai_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if shanghai_now >= target:
        target = target + timedelta(days=1)
    return max(0.0, (target - shanghai_now).total_seconds())


def _to_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo("Asia/Shanghai"))


def _parse_data_run_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_DATA_RUN_STAGE_UI: dict[SelectionDataRunStatus, tuple[str, str, int]] = {
    SelectionDataRunStatus.PLANNED: ("准备选股数据", "已创建选股数据刷新任务，等待数据刷新启动。", 5),
    SelectionDataRunStatus.LEASE_PENDING: ("准备执行刷新", "正在准备执行选股数据刷新。", 10),
    SelectionDataRunStatus.RUNNING: ("启动数据刷新", "选股数据刷新已启动。", 15),
    SelectionDataRunStatus.FETCHING_DATA: ("补齐行情数据", "正在读取本地仓库并补齐缺失行情。", 35),
    SelectionDataRunStatus.NORMALIZING_INPUTS: ("标准化输入", "正在整理选股所需的行情、因子和身份字段。", 50),
    SelectionDataRunStatus.BUILDING_FEATURES: ("构建特征", "正在生成选股策略使用的特征快照。", 65),
    SelectionDataRunStatus.FILTERING_AND_SCORING: ("过滤并打分", "正在执行硬过滤、策略命中和候选打分。", 78),
    SelectionDataRunStatus.BUILDING_CANDIDATE_CACHE: ("生成候选缓存", "正在生成 top20 候选事实缓存。", 88),
    SelectionDataRunStatus.APPROVING_CANDIDATE_CACHE: ("审批候选缓存", "正在校验候选缓存证据和读回完整性。", 95),
    SelectionDataRunStatus.NO_CANDIDATE: ("未产出候选", "本轮补数据完成，但没有可进入选股的候选。", 100),
    SelectionDataRunStatus.COMPLETED: ("数据已准备", "当前交易日选股数据和候选缓存已准备完成。", 100),
    SelectionDataRunStatus.FAILED: ("数据刷新失败", "选股数据刷新失败，请查看失败原因。", 100),
}

_DATA_RUN_ORDER = tuple(_DATA_RUN_STAGE_UI.keys())


def _data_run_progress_for_user(data_run: SelectionDataRun, *, trade_date: str) -> dict[str, object]:
    stage_label, current_action, percent = _DATA_RUN_STAGE_UI[data_run.status]
    progress_completed = data_run.progress_completed
    progress_total = data_run.progress_total
    has_progress = progress_completed is not None and progress_total is not None and progress_total > 0
    if data_run.status == SelectionDataRunStatus.FETCHING_DATA and has_progress:
        percent = min(49, max(35, 35 + int(14 * progress_completed / progress_total)))
        progress_label = data_run.progress_label or current_action
        current_action = f"{progress_label}：已完成 {progress_completed}/{progress_total}。"
    completed_labels = [
        _DATA_RUN_STAGE_UI[status][0]
        for status in _DATA_RUN_ORDER
        if _DATA_RUN_STAGE_UI[status][2] < percent
        and status != data_run.status
        and status not in {SelectionDataRunStatus.NO_CANDIDATE, SelectionDataRunStatus.FAILED}
    ]
    waiting_labels = [
        _DATA_RUN_STAGE_UI[status][0]
        for status in _DATA_RUN_ORDER
        if _DATA_RUN_STAGE_UI[status][2] > percent
        and status not in {
            SelectionDataRunStatus.NO_CANDIDATE,
            SelectionDataRunStatus.COMPLETED,
            SelectionDataRunStatus.FAILED,
        }
    ]
    if data_run.status == SelectionDataRunStatus.FAILED:
        status = "failed"
        status_label = "补数据失败"
    elif data_run.status == SelectionDataRunStatus.NO_CANDIDATE:
        status = "failed"
        status_label = "无候选"
    elif data_run.status == SelectionDataRunStatus.COMPLETED:
        status = "completed"
        status_label = "数据已准备"
    else:
        status = "running"
        status_label = "选股数据准备中"
    if data_run.failure_reason:
        worker_status_labels = [f"失败原因：{_failure_reason_for_user(data_run)}"]
    elif data_run.status == SelectionDataRunStatus.FAILED:
        worker_status_labels = ["失败原因：未记录，查看任务证据。"]
    elif has_progress:
        worker_status_labels = [f"已完成 {progress_completed}/{progress_total}"]
    else:
        worker_status_labels = []
    return {
        "kind": "data_refresh",
        "status": status,
        "statusLabel": status_label,
        "command": f"/select {trade_date}",
        "stageLabel": stage_label,
        "currentAction": current_action,
        "percent": percent,
        "workerStatusLabels": worker_status_labels,
        "completedRoleLabels": completed_labels,
        "waitingRoleLabels": waiting_labels,
        "startedAt": data_run.started_at or data_run.completed_at or data_run.failed_at or "",
        "finishedAt": data_run.completed_at or data_run.failed_at,
        "workflowRunId": data_run.selection_run_id,
    }


def _failure_reason_for_user(data_run: SelectionDataRun) -> str:
    reason = (data_run.failure_reason or "").strip()
    if data_run.failure_code == "provider_evidence_failed" or reason == "provider attempts 缺失":
        return "数据源没有返回可核验的调用记录。"
    return reason
