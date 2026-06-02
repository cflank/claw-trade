from __future__ import annotations

from typing import Any, Callable, Iterable

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


def run_repair(
    job: MaintenanceJob,
    *,
    data_api: Any,
    repo: MaintenanceJobRepository,
    slices: Iterable[Any] | None = None,
    repair_planner: Any | None = None,
    request_from_slice: Callable[..., Any] | None = None,
    lock_owner: str = "maintenance.repair",
) -> MaintenanceJob:
    current, started = begin_job(repo, job, lock_owner=lock_owner)
    if not started:
        return current

    repair_slices = list(slices if slices is not None else _find_repair_slices(job, repair_planner))
    start = resume_index(current)
    init_audit_cursor(repo, current, input_kind="repair_slice", input_total=len(repair_slices))
    try:
        planned: list[tuple[int, Any, Any]] = []
        for index in range(start, len(repair_slices)):
            slice_item = repair_slices[index]
            planned.append(
                (index, slice_item, _build_request_from_slice(slice_item, current, request_from_slice=request_from_slice))
            )
        requests = tuple(request for _, _, request in planned)
        if requests and not callable(getattr(data_api, "get_data_batch", None)):
            raise TypeError("maintenance repair requires DataAPI.get_data_batch")
        results = list(data_api.get_data_batch(requests)) if requests else []
        if len(results) != len(planned):
            raise JobInvariantError("DataAPI.get_data_batch returned mismatched result count")
        for (index, slice_item, request), result in zip(planned, results, strict=True):
            add_result_stats(current, result)
            audit_result(
                repo,
                current,
                index=index,
                input_id=_slice_id(slice_item),
                request=request,
                result=result,
            )
        write_success_manifest(repo, current)
        return mark_job_succeeded(repo, current)
    except Exception as exc:
        mark_job_failed(repo, current, error=str(exc))
        raise


def _find_repair_slices(job: MaintenanceJob, repair_planner: Any | None) -> Iterable[Any]:
    if repair_planner is None:
        raise ValueError("repair_planner is required when slices is not provided")
    return repair_planner.find_slices(job)


def _build_request_from_slice(
    slice_item: Any,
    job: MaintenanceJob,
    *,
    request_from_slice: Callable[..., Any] | None,
) -> Any:
    if request_from_slice is not None:
        return request_from_slice(slice_item, consumer="maintenance", consumer_id=job.job_id)
    if hasattr(slice_item, "to_data_request"):
        return slice_item.to_data_request(consumer="maintenance", consumer_id=job.job_id)
    raise TypeError("missing authorized DataRequest builder for repair slice")


def _slice_id(slice_item: Any) -> str:
    value = getattr(slice_item, "slice_id", None)
    return str(value) if value is not None else "unknown"
