from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from typing import Callable

from claw_trade.selection.data_job import SelectionDataJobExecution
from claw_trade.selection.models import (
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
    SelectionTriggerSource,
    SelectRequest,
)
from claw_trade.selection.scheduler import SelectionScheduleContext, SelectionSchedulingError, schedule_selection_job
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore


@dataclass(frozen=True)
class SelectionDataRefreshResult:
    status: str
    selection_run_id: str | None
    trade_date: str | None
    reason: str
    error_code: str | None = None


class SelectionDataRefreshService:
    """Starts the background selection data job when `/select` finds no usable pack."""

    def __init__(
        self,
        *,
        store: SelectionRunStore,
        run_data_job: Callable[[SelectionRunPlan], SelectionDataJobExecution],
        resolve_closed_trade_date: Callable[[str | None], str],
        load_approved_strategy_config_ref: Callable[[SelectionMarket, SelectionProfile], str | None],
        build_provider_batch_plan: Callable[..., SelectionProviderBatchPlan],
        now_fn: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._run_data_job = run_data_job
        self._resolve_closed_trade_date = resolve_closed_trade_date
        self._load_approved_strategy_config_ref = load_approved_strategy_config_ref
        self._build_provider_batch_plan = build_provider_batch_plan
        self._now_fn = now_fn or _utc_now
        self._run_id_factory = run_id_factory
        self._lock = Lock()

    def request_refresh(
        self,
        *,
        request: SelectRequest,
        unavailable_code: object,
        select_workflow_run_id: str,
    ) -> SelectionDataRefreshResult:
        reason = str(getattr(unavailable_code, "value", unavailable_code))
        try:
            trade_date = self._resolve_closed_trade_date(request.trade_date)
        except Exception as exc:  # noqa: BLE001
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=None,
                trade_date=request.trade_date,
                reason=f"{reason}:trade_date_resolution_failed",
                error_code=type(exc).__name__,
            )

        with self._lock:
            active = self._store.load_active_data_run_record(
                market=request.market,
                profile=request.profile,
                trade_date=trade_date,
            )
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
                        trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
                    ),
                    resolve_closed_trade_date=self._resolve_closed_trade_date,
                    has_active_job=lambda market, profile, date_value: self._store.has_active_data_run(
                        market=market,
                        profile=profile,
                        trade_date=date_value,
                    ),
                    load_approved_strategy_config_ref=self._load_approved_strategy_config_ref,
                    build_provider_batch_plan=self._build_provider_batch_plan,
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
                        failed_at=failed_at,
                        failure_code="selection_refresh_job_failed",
                        failure_reason=f"{type(exc).__name__}: {exc}",
                    ),
                    manifest=None,
                )
            )


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
