from __future__ import annotations

from typing import Any, Iterable

from .jobs import (
    MaintenanceContext,
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


def run_seed_import(
    job: MaintenanceJob,
    *,
    seed_inputs: Iterable[Any],
    ingest_pipeline: Any,
    repo: MaintenanceJobRepository,
    lock_owner: str = "maintenance.seed",
) -> MaintenanceJob:
    current, started = begin_job(repo, job, lock_owner=lock_owner)
    if not started:
        return current

    inputs = list(seed_inputs)
    start = resume_index(current)
    init_audit_cursor(repo, current, input_kind="seed_input", input_total=len(inputs))
    try:
        for index in range(start, len(inputs)):
            seed_input = inputs[index]
            context = MaintenanceContext(
                job_id=current.job_id,
                job_type=current.job_type,
                market=current.market,
                dataset_scope=current.dataset_scope,
                cursor=dict(current.cursor),
            )
            result = ingest_pipeline.ingest_seed(seed_input, context)
            add_result_stats(current, result)
            audit_result(
                repo,
                current,
                index=index,
                input_id=_seed_id(seed_input),
                request=None,
                result=result,
            )
        write_success_manifest(repo, current)
        return mark_job_succeeded(repo, current)
    except Exception as exc:
        mark_job_failed(repo, current, error=str(exc))
        raise


def _seed_id(seed_input: Any) -> str:
    if isinstance(seed_input, dict):
        value = seed_input.get("seed_id")
    else:
        value = getattr(seed_input, "seed_id", None)
    return str(value) if value is not None else "unknown_seed"
