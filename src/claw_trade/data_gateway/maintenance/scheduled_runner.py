from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from claw_trade.data_gateway.maintenance.incremental import IncrementalGapPlanner, run_daily_incremental
from claw_trade.data_gateway.maintenance.jobs import MaintenanceJob, MaintenanceJobRepository
from claw_trade.data_gateway.models import DataResultStatus, Market
from claw_trade.data_gateway.public_api import PublicDataRequest
from claw_trade.data_gateway.warehouse.repository import DatasetRecord, DatasetRepository

_SUPPORTED_JOBS: dict[tuple[str, str], tuple[str, str]] = {
    ("CN_A", "eod"): ("daily_incremental", "daily_bar"),
    ("HK", "eod"): ("daily_incremental", "daily_bar"),
    ("US", "eod"): ("daily_incremental", "daily_bar"),
    ("CRYPTO", "kline-refresh"): ("daily_incremental", "daily_bar"),
}
_REQUESTED_BY = "openclaw_cron"


class DataMaintenanceApi(Protocol):
    def request_data(self, requests: Any) -> Any: ...


@dataclass(frozen=True)
class DailyBarMaintenanceGap:
    market: str
    instrument: str
    missing_day: date

    @property
    def gap_id(self) -> str:
        return f"{self.market}:daily_bar:{self.instrument}:{self.missing_day.isoformat()}"

    def to_public_data_request(self, *, consumer: str, consumer_id: str) -> PublicDataRequest:
        return PublicDataRequest(
            request_id=f"maintenance:{self.gap_id}",
            item="daily_bar",
            market=Market(self.market),
            instrument=self.instrument,
            time_range_start=self.missing_day,
            time_range_end=self.missing_day,
            granularity="daily",
            requested_by_worker=_REQUESTED_BY,
            purpose="scheduled_data_maintenance",
            deadline_at=datetime.now(tz=UTC) + timedelta(minutes=30),
            consumer=consumer,
        )


class ScheduledDataMaintenanceRunner:
    def __init__(
        self,
        *,
        data_api: DataMaintenanceApi,
        job_repository: MaintenanceJobRepository,
        dataset_repository: DatasetRepository,
        incremental_planner: IncrementalGapPlanner | None = None,
        now_provider: Any | None = None,
    ) -> None:
        self._data_api = data_api
        self._job_repository = job_repository
        self._dataset_repository = dataset_repository
        self._incremental_planner = incremental_planner
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))

    def run(
        self,
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None = None,
        maintenance_job_id: str | None = None,
    ) -> MaintenanceJob:
        normalized_market = market.strip().upper()
        normalized_job_kind = job_kind.strip()
        job_type, dataset_scope = self._job_config(market=normalized_market, job_kind=normalized_job_kind)
        as_of = self._now_provider()
        job = MaintenanceJob(
            job_id=self._job_id(
                market=normalized_market,
                job_kind=normalized_job_kind,
                cron_run_id=cron_run_id,
                maintenance_job_id=maintenance_job_id,
                as_of=as_of,
            ),
            job_type=job_type,
            market=normalized_market,
            dataset_scope=dataset_scope,
            requested_by=_REQUESTED_BY,
            scheduled_at=as_of,
        )
        if self._incremental_planner is not None:
            return run_daily_incremental(
                job,
                data_api=_FailClosedDataApi(self._data_api),
                repo=self._job_repository,
                incremental_planner=self._incremental_planner,
                as_of=as_of,
            )
        try:
            gaps = self._plan_daily_bar_gaps(market=normalized_market, dataset_scope=dataset_scope, as_of=as_of)
        except ValueError as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(tz=UTC)
            self._job_repository.save(job)
            raise
        return run_daily_incremental(
            job,
            data_api=_FailClosedDataApi(self._data_api),
            repo=self._job_repository,
            gaps=gaps,
            as_of=as_of,
        )

    @staticmethod
    def _job_config(*, market: str, job_kind: str) -> tuple[str, str]:
        try:
            return _SUPPORTED_JOBS[(market, job_kind)]
        except KeyError as exc:
            raise ValueError(f"unsupported data maintenance job: {market}:{job_kind}") from exc

    @staticmethod
    def _job_id(
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None,
        maintenance_job_id: str | None,
        as_of: datetime | date,
    ) -> str:
        explicit = str(maintenance_job_id or "").strip()
        if explicit:
            return explicit
        run_id = str(cron_run_id or "").strip()
        if not run_id or run_id == "auto":
            day = as_of.date() if isinstance(as_of, datetime) else as_of
            run_id = day.isoformat()
        return f"data-maintenance:{market}:{job_kind}:{run_id}"

    def _plan_daily_bar_gaps(
        self,
        *,
        market: str,
        dataset_scope: str,
        as_of: datetime | date,
    ) -> tuple[DailyBarMaintenanceGap, ...]:
        latest_by_scope: dict[tuple[str | None, str | None], DatasetRecord] = {}
        # ponytail: O(n) metadata scan is fine for the first cron bridge; replace with an aggregate query if maintenance scope grows.
        for record in self._dataset_repository.iter_normalized(
            dataset=dataset_scope,
            market=market,
            symbol_id=None,
            universe_ref=None,
            include_row=False,
        ):
            key = (record.symbol_id, record.universe_ref)
            latest = latest_by_scope.get(key)
            if latest is None or _is_later(record.period_end, latest.period_end):
                latest_by_scope[key] = record
        if not latest_by_scope:
            raise ValueError(f"{market} daily_bar maintenance scope is empty in data layer")
        as_of_day = as_of.date() if isinstance(as_of, datetime) else as_of
        gaps: list[DailyBarMaintenanceGap] = []
        for record in latest_by_scope.values():
            latest_day = _date_or_none(record.period_end)
            if latest_day is None:
                continue
            missing_day = latest_day + timedelta(days=1)
            if missing_day > as_of_day:
                continue
            instrument = str(record.symbol_id or record.universe_ref or "").strip()
            if not instrument:
                continue
            gaps.append(DailyBarMaintenanceGap(market=market, instrument=instrument, missing_day=missing_day))
        return tuple(gaps)


def _date_or_none(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_later(left: Any, right: Any) -> bool:
    left_day = _date_or_none(left)
    right_day = _date_or_none(right)
    if left_day is None:
        return False
    if right_day is None:
        return True
    return left_day > right_day


class _FailClosedDataApi:
    def __init__(self, data_api: DataMaintenanceApi) -> None:
        self._data_api = data_api

    def request_data(self, requests: Any) -> Any:
        results = list(self._data_api.request_data(requests))
        for result in results:
            status = _result_status(result)
            if status != DataResultStatus.READY.value:
                raise RuntimeError(
                    f"scheduled maintenance DataAPI returned non-ready status: "
                    f"request_id={_result_request_id(result)} status={status}"
                )
        return results


def _result_status(result: Any) -> str:
    status = getattr(result, "status", None)
    if isinstance(result, dict):
        status = result.get("status", status)
    value = getattr(status, "value", status)
    return str(value or "").strip().lower()


def _result_request_id(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("request_id") or result.get("requestId") or "unknown")
    return str(getattr(result, "request_id", "") or "unknown")
