from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Iterable, Protocol

from .jobs import (
    JobInvariantError,
    MaintenanceJob,
    MaintenanceJobRepository,
    add_result_stats,
    audit_result,
    begin_job,
    init_audit_cursor,
    mark_job_failed,
    mark_job_succeeded,
    resume_index,
    write_success_manifest,
)


class IncrementalGapPlanner(Protocol):
    def find_gaps(
        self,
        *,
        market: str,
        dataset_scope: str,
        as_of: date | datetime | None,
    ) -> Iterable[Any]: ...


def run_daily_incremental(
    job: MaintenanceJob,
    *,
    data_api: Any,
    repo: MaintenanceJobRepository,
    gaps: Iterable[Any] | None = None,
    incremental_planner: IncrementalGapPlanner | None = None,
    as_of: date | datetime | None = None,
    request_from_gap: Callable[..., Any] | None = None,
    lock_owner: str = "maintenance.incremental",
) -> MaintenanceJob:
    current, started = begin_job(repo, job, lock_owner=lock_owner)
    if not started:
        return current

    incremental_gaps = list(
        gaps
        if gaps is not None
        else _find_incremental_gaps(job, incremental_planner=incremental_planner, as_of=as_of)
    )
    start = resume_index(current)
    init_audit_cursor(repo, current, input_kind="incremental_gap", input_total=len(incremental_gaps))
    try:
        planned: list[tuple[int, Any, Any]] = []
        for index in range(start, len(incremental_gaps)):
            gap = incremental_gaps[index]
            planned.append((index, gap, _build_request_from_gap(gap, current, request_from_gap=request_from_gap)))
        requests = tuple(request for _, _, request in planned)
        if requests and not callable(getattr(data_api, "request_data", None)):
            raise TypeError("maintenance daily incremental requires DataAPI.request_data")
        results = list(data_api.request_data(requests)) if requests else []
        if len(results) != len(planned):
            raise JobInvariantError("DataAPI.request_data returned mismatched result count")
        for (index, gap, request), result in zip(planned, results, strict=True):
            add_result_stats(current, result)
            audit_result(
                repo,
                current,
                index=index,
                input_id=_gap_id(gap),
                request=request,
                result=result,
            )
        write_success_manifest(repo, current)
        return mark_job_succeeded(repo, current)
    except Exception as exc:
        mark_job_failed(repo, current, error=str(exc))
        raise


def _find_incremental_gaps(
    job: MaintenanceJob,
    *,
    incremental_planner: IncrementalGapPlanner | None,
    as_of: date | datetime | None,
) -> Iterable[Any]:
    if incremental_planner is None:
        raise ValueError("incremental_planner is required when gaps is not provided")
    return incremental_planner.find_gaps(
        market=job.market,
        dataset_scope=job.dataset_scope,
        as_of=as_of,
    )


def _build_request_from_gap(
    gap: Any,
    job: MaintenanceJob,
    *,
    request_from_gap: Callable[..., Any] | None,
) -> Any:
    if request_from_gap is not None:
        return request_from_gap(gap, consumer="maintenance", consumer_id=job.job_id)
    if hasattr(gap, "to_public_data_request"):
        return gap.to_public_data_request(consumer="maintenance", consumer_id=job.job_id)
    raise TypeError("missing authorized PublicDataRequest builder for incremental gap")


def _gap_id(gap: Any) -> str:
    value = getattr(gap, "gap_id", None)
    return str(value) if value is not None else "unknown"
