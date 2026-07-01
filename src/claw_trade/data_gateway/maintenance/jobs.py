from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Mapping, MutableMapping, Protocol
from uuid import uuid4

from claw_trade.data_gateway.warehouse.repository import DatasetRepository

JobStatus = str
JobType = str

_LEGACY_AUDIT_CURSOR_KEYS: tuple[str, ...] = (
    "audit_request_ids",
    "audit_dataset_refs",
    "audit_raw_refs",
    "audit_attempt_refs",
    "audit_gap_summary",
)

_EVENT_CURSOR_KEYS: tuple[str, ...] = (
    "input_kind",
    "input_total",
    "input_range",
    "index",
    "last_input_id",
    "last_request_id",
    "last_input_range",
    "last_output_refs",
    "last_gap_summary",
    "coverage_start",
    "coverage_end",
    "audit_manifest_ref",
    "audit_manifest_hash",
    "audit_counts",
    "manifest_ref",
    "manifest_hash",
)
_AUDIT_REF_INLINE_LIMIT = 1000
_AUDIT_REF_SAMPLE_LIMIT = 50


class JobAlreadyRunningError(RuntimeError):
    """Raised when a non-expired running job is started again."""


class JobInvariantError(RuntimeError):
    """Raised when job state is invalid."""


@dataclass(slots=True)
class MaintenanceEvent:
    status: JobStatus
    at: datetime
    cursor: dict[str, Any]
    stats: dict[str, int]
    error: str | None = None


@dataclass(slots=True)
class MaintenanceContext:
    job_id: str
    job_type: JobType
    market: str
    dataset_scope: str
    cursor: Mapping[str, Any]


@dataclass(slots=True)
class MaintenanceJob:
    job_id: str
    job_type: JobType
    market: str
    dataset_scope: str
    status: JobStatus = "pending"
    cursor: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    requested_by: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lock_owner: str | None = None
    lock_expires_at: datetime | None = None
    next_run_at: datetime | None = None
    expire_at: datetime | None = None
    events: list[MaintenanceEvent] = field(default_factory=list)


class MaintenanceJobRepository(Protocol):
    def get(self, job_id: str) -> MaintenanceJob | None: ...

    def save(self, job: MaintenanceJob) -> None: ...

    def save_dataset_manifest(self, manifest: Mapping[str, Any]) -> str: ...

    def get_dataset_manifest(self, manifest_ref: str) -> dict[str, Any] | None: ...

    def list_dataset_manifests(self) -> tuple[dict[str, Any], ...]: ...


@dataclass(slots=True)
class InMemoryMaintenanceJobRepository:
    _jobs: MutableMapping[str, MaintenanceJob] = field(default_factory=dict)
    _dataset_manifests: MutableMapping[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, job_id: str) -> MaintenanceJob | None:
        return self._jobs.get(job_id)

    def save(self, job: MaintenanceJob) -> None:
        self._jobs[job.job_id] = job

    def save_dataset_manifest(self, manifest: Mapping[str, Any]) -> str:
        doc = dict(manifest)
        manifest_ref = str(doc.get("manifest_ref") or f"manifest:{uuid4().hex[:12]}")
        doc["manifest_ref"] = manifest_ref
        self._dataset_manifests[manifest_ref] = doc
        return manifest_ref

    def get_dataset_manifest(self, manifest_ref: str) -> dict[str, Any] | None:
        doc = self._dataset_manifests.get(manifest_ref)
        return None if doc is None else dict(doc)

    def list_dataset_manifests(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._dataset_manifests.values())


@dataclass(slots=True)
class CollectionMaintenanceJobRepository:
    repository: DatasetRepository

    def get(self, job_id: str) -> MaintenanceJob | None:
        doc = self.repository.get_maintenance_job(job_id)
        if doc is None:
            return None
        return _job_from_doc(doc)

    def save(self, job: MaintenanceJob) -> None:
        self.repository.save_maintenance_job(job.job_id, _job_to_doc(job))

    def save_dataset_manifest(self, manifest: Mapping[str, Any]) -> str:
        return self.repository.write_dataset_manifest(manifest)

    def get_dataset_manifest(self, manifest_ref: str) -> dict[str, Any] | None:
        return self.repository.get_dataset_manifest(manifest_ref)

    def list_dataset_manifests(self) -> tuple[dict[str, Any], ...]:
        return self.repository.list_dataset_manifests()


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def begin_job(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    lock_owner: str,
    lock_ttl: timedelta = timedelta(minutes=30),
    now: datetime | None = None,
) -> tuple[MaintenanceJob, bool]:
    current = repo.get(job.job_id) or job
    ts = now or utcnow()
    if current.status == "succeeded":
        return current, False
    if current.status == "running" and current.lock_expires_at and current.lock_expires_at > ts:
        raise JobAlreadyRunningError(current.job_id)

    current.status = "running"
    current.error = None
    current.started_at = current.started_at or ts
    current.finished_at = None
    current.lock_owner = lock_owner
    current.lock_expires_at = ts + lock_ttl
    append_event(current, status="running", at=ts)
    repo.save(current)
    return current, True


def mark_job_succeeded(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    now: datetime | None = None,
    expire_after: timedelta = timedelta(days=90),
) -> MaintenanceJob:
    ts = now or utcnow()
    ensure_running(job)
    job.status = "succeeded"
    job.finished_at = ts
    job.expire_at = ts + expire_after
    job.lock_owner = None
    job.lock_expires_at = None
    append_event(job, status="succeeded", at=ts)
    repo.save(job)
    return job


def mark_job_failed(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    error: str,
    now: datetime | None = None,
) -> MaintenanceJob:
    ts = now or utcnow()
    if job.status != "running":
        raise JobInvariantError(f"{job.job_id} is not running")
    job.status = "failed"
    job.error = error
    job.finished_at = ts
    job.lock_owner = None
    job.lock_expires_at = None
    append_event(job, status="failed", at=ts, error=error)
    repo.save(job)
    return job


def update_cursor(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    cursor_patch: Mapping[str, Any],
    *,
    now: datetime | None = None,
    record_event: bool = True,
) -> MaintenanceJob:
    ensure_running(job)
    job.cursor.update(dict(cursor_patch))
    if record_event:
        append_event(job, status="running", at=now or utcnow())
    repo.save(job)
    return job


def add_stats(job: MaintenanceJob, **increments: int) -> None:
    for key, value in increments.items():
        if not value:
            continue
        job.stats[key] = int(job.stats.get(key, 0) + value)


def add_result_stats(job: MaintenanceJob, result: Any) -> None:
    add_stats(
        job,
        dataset_refs=count_ref_like(getattr(result, "dataset_refs", ())),
        raw_refs=count_ref_like(getattr(result, "raw_refs", ())),
        attempt_refs=count_ref_like(getattr(result, "attempt_refs", ())),
        gaps=count_ref_like(getattr(result, "gaps", ())),
        remote_success=1 if _result_remote_success(result) else 0,
    )
    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)
    if isinstance(status_value, str) and status_value:
        add_stats(job, **{f"status_{status_value}": 1})
    for reason in _gap_reasons(result):
        add_stats(job, **{f"gap_{reason}": 1})


def init_audit_cursor(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    input_kind: str,
    input_total: int,
    now: datetime | None = None,
) -> MaintenanceJob:
    ensure_running(job)
    ts = now or utcnow()
    state = _load_audit_state(repo, job)
    manifest_ref, manifest_hash = _write_audit_manifest(
        repo,
        job,
        state=state,
        status="maintenance_running",
        now=ts,
    )
    _drop_legacy_audit_cursor(job)
    patch = {
        "input_kind": input_kind,
        "input_total": int(input_total),
        "input_range": {
            "start_index": 0,
            "end_index": (input_total - 1) if input_total > 0 else None,
        },
        "audit_manifest_ref": manifest_ref,
        "audit_manifest_hash": manifest_hash,
        "audit_counts": _audit_counts(state),
    }
    return update_cursor(repo, job, patch, now=ts)


def audit_result(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    index: int,
    input_id: str,
    request: Any | None,
    result: Any,
    now: datetime | None = None,
) -> MaintenanceJob:
    ensure_running(job)
    dataset_refs = _refs(getattr(result, "dataset_refs", ()))
    raw_refs = _refs(getattr(result, "raw_refs", ()))
    attempt_refs = _refs(getattr(result, "attempt_refs", ()))
    gap_summary = _summarize_gaps(result)
    request_id = _request_id(result, request=request)
    required_fields = _required_fields_from_request(request)
    present_fields = _present_fields_from_result(result)

    input_range = _request_range(request)
    coverage_start = _coalesce_range_value(job.cursor.get("coverage_start"), input_range.get("start"))
    coverage_end = _coalesce_range_value(job.cursor.get("coverage_end"), input_range.get("end"), prefer_later=True)
    state = _append_audit_state(
        _load_audit_state(repo, job),
        request_id=request_id,
        dataset_refs=dataset_refs,
        raw_refs=raw_refs,
        attempt_refs=attempt_refs,
        required_fields=required_fields,
        present_fields=present_fields,
        gap_summary=gap_summary,
    )
    manifest_ref, manifest_hash = _write_audit_manifest(
        repo,
        job,
        state=state,
        status="maintenance_running",
        now=now or utcnow(),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    _drop_legacy_audit_cursor(job)

    patch = {
        "index": index + 1,
        "last_input_id": input_id,
        "last_request_id": request_id,
        "last_input_range": input_range,
        "last_output_refs": {
            "dataset_refs": _compact_ref_values(dataset_refs),
            "raw_refs": _compact_ref_values(raw_refs),
            "attempt_refs": _compact_ref_values(attempt_refs),
        },
        "last_gap_summary": list(gap_summary),
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "audit_manifest_ref": manifest_ref,
        "audit_manifest_hash": manifest_hash,
        "audit_counts": _audit_counts(state),
    }
    return update_cursor(repo, job, patch, now=now, record_event=False)


def write_success_manifest(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    now: datetime | None = None,
) -> str:
    ensure_running(job)
    ts = now or utcnow()
    state = _load_audit_state(repo, job)
    input_total = int(job.cursor.get("input_total", 0) or 0)
    if input_total > 0 and not (
        state["dataset_refs"] or state["raw_refs"] or state["attempt_refs"] or state["gaps"]
    ):
        raise JobInvariantError("successful maintenance manifest requires output refs or gap summary")
    manifest_ref, manifest_hash = _write_audit_manifest(
        repo,
        job,
        state=state,
        status="maintenance_succeeded",
        now=ts,
    )
    _drop_legacy_audit_cursor(job)
    update_cursor(
        repo,
        job,
        {
            "manifest_ref": manifest_ref,
            "manifest_hash": manifest_hash,
            "audit_manifest_ref": manifest_ref,
            "audit_manifest_hash": manifest_hash,
            "audit_counts": _audit_counts(state),
        },
        now=ts,
    )
    return manifest_ref


def _load_audit_state(repo: MaintenanceJobRepository, job: MaintenanceJob) -> dict[str, tuple[Any, ...]]:
    manifest_ref = job.cursor.get("audit_manifest_ref") or job.cursor.get("manifest_ref")
    manifest: Mapping[str, Any] | None = None
    if manifest_ref:
        getter = getattr(repo, "get_dataset_manifest", None)
        if callable(getter):
            manifest = getter(str(manifest_ref))
    if manifest is not None:
        output_refs = manifest.get("output_refs", {})
        if not isinstance(output_refs, Mapping):
            output_refs = {}
        dataset_refs = _tuple_values(manifest.get("dataset_refs", output_refs.get("dataset_refs", ())))
        raw_refs = _tuple_values(manifest.get("raw_refs", output_refs.get("raw_refs", ())))
        attempt_refs = _tuple_values(manifest.get("attempt_refs", output_refs.get("attempt_refs", ())))
        request_ids = _tuple_values(manifest.get("request_ids", ()))
        required_fields = _tuple_values(manifest.get("required_fields", ()))
        present_fields = _tuple_values(manifest.get("present_fields", ()))
        gaps = _tuple_mapping_values(manifest.get("gap_summary", manifest.get("gaps", ())))
        return {
            "request_ids": request_ids,
            "dataset_refs": dataset_refs,
            "raw_refs": raw_refs,
            "attempt_refs": attempt_refs,
            "required_fields": required_fields,
            "present_fields": present_fields,
            "gaps": gaps,
            "_counts": {
                "request_ids": _stored_count(manifest, "request_id_count", request_ids),
                "dataset_refs": _stored_count(manifest, "dataset_ref_count", dataset_refs),
                "raw_refs": _stored_count(manifest, "raw_ref_count", raw_refs),
                "attempt_refs": _stored_count(manifest, "attempt_ref_count", attempt_refs),
                "required_fields": len(required_fields),
                "present_fields": len(present_fields),
                "gaps": len(gaps),
            },
            "_hashes": {
                "dataset_refs": str(manifest.get("dataset_refs_sha256") or output_refs.get("dataset_refs_sha256") or ""),
                "raw_refs": str(manifest.get("raw_refs_sha256") or output_refs.get("raw_refs_sha256") or ""),
                "attempt_refs": str(manifest.get("attempt_refs_sha256") or output_refs.get("attempt_refs_sha256") or ""),
            },
            "_hash_algorithms": {
                "dataset_refs": str(
                    manifest.get("dataset_refs_hash_algorithm") or output_refs.get("dataset_refs_hash_algorithm") or ""
                ),
                "raw_refs": str(manifest.get("raw_refs_hash_algorithm") or output_refs.get("raw_refs_hash_algorithm") or ""),
                "attempt_refs": str(
                    manifest.get("attempt_refs_hash_algorithm") or output_refs.get("attempt_refs_hash_algorithm") or ""
                ),
            },
        }
    return {
        "request_ids": _tuple_values(_cursor_list(job.cursor, "audit_request_ids")),
        "dataset_refs": _tuple_values(_cursor_list(job.cursor, "audit_dataset_refs")),
        "raw_refs": _tuple_values(_cursor_list(job.cursor, "audit_raw_refs")),
        "attempt_refs": _tuple_values(_cursor_list(job.cursor, "audit_attempt_refs")),
        "required_fields": _tuple_values(_cursor_list(job.cursor, "audit_required_fields")),
        "present_fields": _tuple_values(_cursor_list(job.cursor, "audit_present_fields")),
        "gaps": _tuple_mapping_values(_cursor_list(job.cursor, "audit_gap_summary")),
    }


def _append_audit_state(
    state: Mapping[str, tuple[Any, ...]],
    *,
    request_id: str,
    dataset_refs: tuple[str, ...],
    raw_refs: tuple[str, ...],
    attempt_refs: tuple[str, ...],
    required_fields: tuple[str, ...],
    present_fields: tuple[str, ...],
    gap_summary: tuple[dict[str, Any], ...],
) -> dict[str, tuple[Any, ...]]:
    request_ids = state.get("request_ids", ())
    if request_id == "unknown":
        next_request_ids = tuple(str(item) for item in request_ids)
    else:
        next_request_ids = _append_unique(tuple(str(item) for item in request_ids), request_id)
    dataset_ref_values = _append_sample_values(state, "dataset_refs", dataset_refs)
    raw_ref_values = _append_sample_values(state, "raw_refs", raw_refs)
    attempt_ref_values = _append_sample_values(state, "attempt_refs", attempt_refs)
    required_field_values = _append_unique(tuple(str(item) for item in state.get("required_fields", ())), *required_fields)
    present_field_values = _append_unique(tuple(str(item) for item in state.get("present_fields", ())), *present_fields)
    gap_values = _append_gap_summary(tuple(state.get("gaps", ())), gap_summary)
    hashes = {
        "dataset_refs": _next_ref_hash(state, "dataset_refs", dataset_ref_values, dataset_refs),
        "raw_refs": _next_ref_hash(state, "raw_refs", raw_ref_values, raw_refs),
        "attempt_refs": _next_ref_hash(state, "attempt_refs", attempt_ref_values, attempt_refs),
    }
    return {
        "request_ids": next_request_ids,
        "dataset_refs": dataset_ref_values,
        "raw_refs": raw_ref_values,
        "attempt_refs": attempt_ref_values,
        "required_fields": required_field_values,
        "present_fields": present_field_values,
        "gaps": gap_values,
        "_counts": {
            "request_ids": _next_ref_count(state, "request_ids", next_request_ids, (request_id,) if request_id != "unknown" else ()),
            "dataset_refs": _next_ref_count(state, "dataset_refs", dataset_ref_values, dataset_refs),
            "raw_refs": _next_ref_count(state, "raw_refs", raw_ref_values, raw_refs),
            "attempt_refs": _next_ref_count(state, "attempt_refs", attempt_ref_values, attempt_refs),
            "required_fields": len(required_field_values),
            "present_fields": len(present_field_values),
            "gaps": len(gap_values),
        },
        "_hashes": {key: value["sha256"] for key, value in hashes.items()},
        "_hash_algorithms": {key: value["algorithm"] for key, value in hashes.items()},
    }


def _write_audit_manifest(
    repo: MaintenanceJobRepository,
    job: MaintenanceJob,
    *,
    state: Mapping[str, tuple[Any, ...]],
    status: str,
    now: datetime,
    coverage_start: Any | None = None,
    coverage_end: Any | None = None,
) -> tuple[str, str]:
    request_ids = tuple(str(item) for item in state.get("request_ids", ()) if str(item).strip())
    if not request_ids and status == "maintenance_succeeded":
        request_ids = (f"maintenance:{job.job_id}",)
    dataset_refs = tuple(str(item) for item in state.get("dataset_refs", ()))
    raw_refs = tuple(str(item) for item in state.get("raw_refs", ()))
    attempt_refs = tuple(str(item) for item in state.get("attempt_refs", ()))
    counts = _audit_counts(state)
    dataset_ref_count = counts["dataset_refs"]
    raw_ref_count = counts["raw_refs"]
    attempt_ref_count = counts["attempt_refs"]
    dataset_refs_inline = _inline_ref_values(dataset_refs)
    raw_refs_inline = _inline_ref_values(raw_refs)
    attempt_refs_inline = _inline_ref_values(attempt_refs)
    dataset_refs_hash = _state_ref_hash(state, "dataset_refs", dataset_refs)
    raw_refs_hash = _state_ref_hash(state, "raw_refs", raw_refs)
    attempt_refs_hash = _state_ref_hash(state, "attempt_refs", attempt_refs)
    dataset_refs_hash_algorithm = _state_ref_hash_algorithm(state, "dataset_refs", dataset_refs)
    raw_refs_hash_algorithm = _state_ref_hash_algorithm(state, "raw_refs", raw_refs)
    attempt_refs_hash_algorithm = _state_ref_hash_algorithm(state, "attempt_refs", attempt_refs)
    required_fields = tuple(str(item) for item in state.get("required_fields", ()))
    present_fields = tuple(str(item) for item in state.get("present_fields", ()))
    gaps = tuple(dict(item) for item in state.get("gaps", ()) if isinstance(item, Mapping))
    coverage_window = {
        "start": coverage_start if coverage_start is not None else job.cursor.get("coverage_start"),
        "end": coverage_end if coverage_end is not None else job.cursor.get("coverage_end"),
    }
    source_summary = f"maintenance:{job.job_type}:{job.market}:{job.dataset_scope}:{job.job_id}"
    requirement_fingerprint = _hash_payload(
        {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "market": job.market,
            "dataset_scope": job.dataset_scope,
            "input_kind": job.cursor.get("input_kind"),
            "required_fields": required_fields,
            "coverage_window": coverage_window,
        }
    )
    manifest_ref = str(
        job.cursor.get("audit_manifest_ref")
        or job.cursor.get("manifest_ref")
        or f"manifest:maintenance:{sha256(job.job_id.encode('utf-8')).hexdigest()[:16]}"
    )
    doc = {
        "manifest_ref": manifest_ref,
        "request_ids": request_ids,
        "request_id_count": len(request_ids),
        "dataset_refs": dataset_refs_inline,
        "dataset_ref_count": dataset_ref_count,
        "dataset_ref_count_semantics": "observed_refs",
        "dataset_refs_sha256": dataset_refs_hash,
        "dataset_refs_hash_algorithm": dataset_refs_hash_algorithm,
        "dataset_refs_truncated": dataset_ref_count > len(dataset_refs_inline),
        "requirement_fingerprint": requirement_fingerprint,
        "coverage_window": coverage_window,
        "required_fields": required_fields,
        "present_fields": present_fields,
        "freshness_snapshot": {
            "status": status,
            "as_of": now,
            "job_id": job.job_id,
            "stats": dict(job.stats),
        },
        "source_summary": source_summary,
        "raw_refs": raw_refs_inline,
        "raw_ref_count": raw_ref_count,
        "raw_ref_count_semantics": "observed_refs",
        "raw_refs_sha256": raw_refs_hash,
        "raw_refs_hash_algorithm": raw_refs_hash_algorithm,
        "raw_refs_truncated": raw_ref_count > len(raw_refs_inline),
        "attempt_refs": attempt_refs_inline,
        "attempt_ref_count": attempt_ref_count,
        "attempt_ref_count_semantics": "observed_refs",
        "attempt_refs_sha256": attempt_refs_hash,
        "attempt_refs_hash_algorithm": attempt_refs_hash_algorithm,
        "attempt_refs_truncated": attempt_ref_count > len(attempt_refs_inline),
        "output_refs": {
            "dataset_refs": dataset_refs_inline,
            "dataset_ref_count": dataset_ref_count,
            "dataset_ref_count_semantics": "observed_refs",
            "dataset_refs_sha256": dataset_refs_hash,
            "dataset_refs_hash_algorithm": dataset_refs_hash_algorithm,
            "dataset_refs_truncated": dataset_ref_count > len(dataset_refs_inline),
            "raw_refs": raw_refs_inline,
            "raw_ref_count": raw_ref_count,
            "raw_ref_count_semantics": "observed_refs",
            "raw_refs_sha256": raw_refs_hash,
            "raw_refs_hash_algorithm": raw_refs_hash_algorithm,
            "raw_refs_truncated": raw_ref_count > len(raw_refs_inline),
            "attempt_refs": attempt_refs_inline,
            "attempt_ref_count": attempt_ref_count,
            "attempt_ref_count_semantics": "observed_refs",
            "attempt_refs_sha256": attempt_refs_hash,
            "attempt_refs_hash_algorithm": attempt_refs_hash_algorithm,
            "attempt_refs_truncated": attempt_ref_count > len(attempt_refs_inline),
        },
        "gaps": gaps,
        "gap_summary": gaps,
        "input_range": job.cursor.get("input_range"),
        "input_kind": job.cursor.get("input_kind"),
        "input_total": job.cursor.get("input_total"),
        "audit_counts": _audit_counts(state),
        "created_at": now,
        "updated_at": now,
    }
    doc["manifest_hash"] = _hash_payload(
        {
            "request_ids": request_ids,
            "output_refs": doc["output_refs"],
            "output_ref_counts": {
                "dataset_refs": dataset_ref_count,
                "raw_refs": raw_ref_count,
                "attempt_refs": attempt_ref_count,
            },
            "output_ref_hashes": {
                "dataset_refs": dataset_refs_hash,
                "raw_refs": raw_refs_hash,
                "attempt_refs": attempt_refs_hash,
            },
            "gap_summary": gaps,
            "requirement_fingerprint": requirement_fingerprint,
            "status": status,
        }
    )
    saved_ref = repo.save_dataset_manifest(doc)
    return saved_ref, str(doc["manifest_hash"])


def _audit_counts(state: Mapping[str, tuple[Any, ...]]) -> dict[str, int]:
    counts = state.get("_counts")
    if isinstance(counts, Mapping):
        return {
            "request_ids": int(counts.get("request_ids", 0) or 0),
            "dataset_refs": int(counts.get("dataset_refs", 0) or 0),
            "raw_refs": int(counts.get("raw_refs", 0) or 0),
            "attempt_refs": int(counts.get("attempt_refs", 0) or 0),
            "required_fields": int(counts.get("required_fields", 0) or 0),
            "present_fields": int(counts.get("present_fields", 0) or 0),
            "gaps": int(counts.get("gaps", 0) or 0),
        }
    return {
        "request_ids": len(state.get("request_ids", ())),
        "dataset_refs": len(state.get("dataset_refs", ())),
        "raw_refs": len(state.get("raw_refs", ())),
        "attempt_refs": len(state.get("attempt_refs", ())),
        "required_fields": len(state.get("required_fields", ())),
        "present_fields": len(state.get("present_fields", ())),
        "gaps": len(state.get("gaps", ())),
    }


def _drop_legacy_audit_cursor(job: MaintenanceJob) -> None:
    for key in _LEGACY_AUDIT_CURSOR_KEYS:
        job.cursor.pop(key, None)


def _tuple_values(raw: Any) -> tuple[Any, ...]:
    if raw is None:
        return ()
    if isinstance(raw, tuple):
        return raw
    if isinstance(raw, list):
        return tuple(raw)
    if isinstance(raw, str):
        return (raw,)
    try:
        return tuple(raw)
    except TypeError:
        return (raw,)


def _tuple_mapping_values(raw: Any) -> tuple[dict[str, Any], ...]:
    values: list[dict[str, Any]] = []
    for item in _tuple_values(raw):
        if isinstance(item, Mapping):
            values.append(dict(item))
    return tuple(values)


def count_ref_like(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return 1
    if isinstance(value, Mapping):
        return len(value)
    try:
        return len(value)
    except TypeError:
        return 1


def _gap_reasons(result: Any) -> tuple[str, ...]:
    values: list[str] = []
    for gap in _coerce_gaps(result):
        reason = gap.get("reason")
        if reason is None:
            continue
        values.append(str(_plain_value(reason)))
    return tuple(values)


def _result_remote_success(result: Any) -> bool:
    freshness = getattr(result, "freshness", None)
    if isinstance(result, Mapping):
        freshness = result.get("freshness", freshness)
    if isinstance(freshness, Mapping):
        return bool(freshness.get("remote_success"))
    return bool(getattr(result, "remote_success", False))


def _refs(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Mapping):
        return tuple(str(item) for item in raw.values())
    return tuple(str(item) for item in raw)


def _stored_count(manifest: Mapping[str, Any], key: str, values: tuple[Any, ...]) -> int:
    raw = manifest.get(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return len(values)


def _append_sample_values(
    state: Mapping[str, tuple[Any, ...]],
    key: str,
    incoming: tuple[Any, ...],
) -> tuple[str, ...]:
    values = _append_unique(tuple(str(item) for item in state.get(key, ())), *incoming)
    if len(values) <= _AUDIT_REF_SAMPLE_LIMIT:
        return values
    return tuple(values[:_AUDIT_REF_SAMPLE_LIMIT])


def _next_ref_count(
    state: Mapping[str, tuple[Any, ...]],
    key: str,
    values: tuple[Any, ...],
    incoming: tuple[Any, ...],
) -> int:
    counts = state.get("_counts")
    previous = None
    if isinstance(counts, Mapping):
        try:
            previous = int(counts.get(key, 0) or 0)
        except (TypeError, ValueError):
            previous = None
    previous_values = tuple(state.get(key, ()))
    if previous is not None:
        return previous + len(incoming)
    return len(values)


def _next_ref_hash(
    state: Mapping[str, tuple[Any, ...]],
    key: str,
    values: tuple[str, ...],
    incoming: tuple[str, ...],
) -> dict[str, str]:
    hashes = state.get("_hashes")
    algorithms = state.get("_hash_algorithms")
    previous_hash = ""
    previous_algorithm = ""
    if isinstance(hashes, Mapping):
        previous_hash = str(hashes.get(key) or "")
    if isinstance(algorithms, Mapping):
        previous_algorithm = str(algorithms.get(key) or "")
    if incoming and previous_hash:
        return {
            "sha256": _hash_payload({"previous": previous_hash, "incoming": list(incoming)}),
            "algorithm": "rolling-sha256-v1",
        }
    if incoming:
        return {
            "sha256": _hash_ref_values(incoming),
            "algorithm": "observed-batch-sha256-v1",
        }
    if previous_hash:
        return {
            "sha256": previous_hash,
            "algorithm": previous_algorithm or "unknown",
        }
    return {
        "sha256": _hash_ref_values(values),
        "algorithm": "inline-sha256-v1",
    }


def _state_ref_hash(state: Mapping[str, tuple[Any, ...]], key: str, refs: tuple[str, ...]) -> str:
    hashes = state.get("_hashes")
    if isinstance(hashes, Mapping):
        value = str(hashes.get(key) or "")
        if value:
            return value
    return _hash_ref_values(refs)


def _state_ref_hash_algorithm(state: Mapping[str, tuple[Any, ...]], key: str, refs: tuple[str, ...]) -> str:
    algorithms = state.get("_hash_algorithms")
    if isinstance(algorithms, Mapping):
        value = str(algorithms.get(key) or "")
        if value:
            return value
    return "inline-sha256-v1"


def _inline_ref_values(refs: tuple[str, ...]) -> list[str]:
    if len(refs) <= _AUDIT_REF_INLINE_LIMIT:
        return list(refs)
    return list(refs[:_AUDIT_REF_SAMPLE_LIMIT])


def _compact_ref_values(refs: tuple[str, ...]) -> list[str] | dict[str, Any]:
    inline = _inline_ref_values(refs)
    if len(inline) == len(refs):
        return inline
    return {
        "count": len(refs),
        "sample": inline,
        "sha256": _hash_ref_values(refs),
        "truncated": True,
    }


def _hash_ref_values(refs: tuple[str, ...]) -> str:
    return _hash_payload({"refs": list(refs)})


def _request_id(result: Any, *, request: Any | None) -> str:
    candidate = getattr(result, "request_id", None)
    if candidate is None and isinstance(result, Mapping):
        candidate = result.get("request_id")
    if candidate is None and request is not None:
        if isinstance(request, Mapping):
            candidate = request.get("request_id")
        else:
            candidate = getattr(request, "request_id", None)
    if candidate is None:
        return "unknown"
    return str(candidate)


def _required_fields_from_request(request: Any | None) -> tuple[str, ...]:
    if request is None:
        return ()
    raw: Any
    if isinstance(request, Mapping):
        params = request.get("params")
        raw = request.get("required_fields", ())
        if not raw and isinstance(params, Mapping):
            raw = params.get("required_fields", ())
    else:
        params = getattr(request, "params", None)
        raw = getattr(request, "required_fields", ())
        if not raw and isinstance(params, Mapping):
            raw = params.get("required_fields", ())
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(item) for item in raw)


def _present_fields_from_result(result: Any) -> tuple[str, ...]:
    rows = getattr(result, "rows", ())
    if isinstance(result, Mapping) and "rows" in result:
        rows = result.get("rows")
    if not rows:
        return ()
    keys: set[str] = set()
    for row in rows:
        if isinstance(row, Mapping):
            keys.update(str(item) for item in row.keys())
    return tuple(sorted(keys))


def _request_range(request: Any | None) -> dict[str, Any]:
    if request is None:
        return {"start": None, "end": None}
    if isinstance(request, Mapping):
        start = request.get("date_range_start")
        if start is None:
            start = request.get("time_range_start")
        end = request.get("date_range_end")
        if end is None:
            end = request.get("time_range_end")
    else:
        start = getattr(request, "date_range_start", None)
        if start is None:
            start = getattr(request, "time_range_start", None)
        end = getattr(request, "date_range_end", None)
        if end is None:
            end = getattr(request, "time_range_end", None)
    return {"start": _stringify_range_value(start), "end": _stringify_range_value(end)}


def _coalesce_range_value(existing: Any, incoming: Any, *, prefer_later: bool = False) -> Any:
    if incoming in (None, ""):
        return existing
    if existing in (None, ""):
        return incoming
    existing_text = str(existing)
    incoming_text = str(incoming)
    if prefer_later:
        return max(existing_text, incoming_text)
    return min(existing_text, incoming_text)


def _stringify_range_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _append_unique(existing: tuple[str, ...], *values: str) -> tuple[str, ...]:
    merged = list(existing)
    seen = set(existing)
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        merged.append(text)
        seen.add(text)
    return tuple(merged)


def _append_gap_summary(existing: tuple[Any, ...], gaps: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in existing:
        if not isinstance(item, Mapping):
            continue
        normalized_item = _plain_gap_summary(item)
        normalized = {k: normalized_item[k] for k in ("gap_id", "reason", "severity", "request_id") if k in normalized_item}
        key = json.dumps(normalized, sort_keys=True, ensure_ascii=True)
        if key in seen:
            continue
        merged.append(normalized_item)
        seen.add(key)
    for item in gaps:
        normalized_item = _plain_gap_summary(item)
        normalized = {k: normalized_item[k] for k in ("gap_id", "reason", "severity", "request_id") if k in normalized_item}
        key = json.dumps(normalized, sort_keys=True, ensure_ascii=True)
        if key in seen:
            continue
        merged.append(normalized_item)
        seen.add(key)
    return tuple(merged)


def _plain_gap_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(item)
    for key in ("reason", "severity"):
        if key in output:
            output[key] = str(_plain_value(output[key]))
    return output


def _cursor_list(cursor: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    value = cursor.get(key, ())
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _summarize_gaps(result: Any) -> tuple[dict[str, Any], ...]:
    summary: list[dict[str, Any]] = []
    for gap in _coerce_gaps(result):
        summary.append(
            {
                "gap_id": str(gap.get("gap_id", "unknown_gap")),
                "reason": str(_plain_value(gap.get("reason", "unknown_reason"))),
                "severity": str(_plain_value(gap.get("severity", "unknown_severity"))),
                "request_id": str(gap.get("request_id", "unknown")),
                "evidence_refs": tuple(str(item) for item in gap.get("evidence_refs", ())),
            }
        )
    return tuple(summary)


def _plain_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _coerce_gaps(result: Any) -> tuple[dict[str, Any], ...]:
    raw = getattr(result, "gaps", ())
    if isinstance(result, Mapping):
        raw = result.get("gaps", raw)
    output: list[dict[str, Any]] = []
    for item in raw or ():
        if isinstance(item, Mapping):
            output.append(dict(item))
            continue
        reason = getattr(item, "reason", None)
        severity = getattr(item, "severity", None)
        output.append(
            {
                "gap_id": getattr(item, "gap_id", "unknown_gap"),
                "reason": getattr(reason, "value", reason),
                "severity": getattr(severity, "value", severity),
                "request_id": getattr(item, "request_id", "unknown"),
                "evidence_refs": tuple(getattr(item, "evidence_refs", ()) or ()),
            }
        )
    return tuple(output)


def _hash_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


def resume_index(job: MaintenanceJob) -> int:
    raw = job.cursor.get("index", 0)
    if not isinstance(raw, int):
        raise JobInvariantError("cursor.index must be int")
    if raw < 0:
        raise JobInvariantError("cursor.index must be >= 0")
    return raw


def append_event(
    job: MaintenanceJob,
    *,
    status: JobStatus,
    at: datetime,
    error: str | None = None,
) -> None:
    job.events.append(
        MaintenanceEvent(
            status=status,
            at=at,
            cursor=_compact_cursor_for_event(job.cursor),
            stats=dict(job.stats),
            error=error,
        )
    )


def ensure_running(job: MaintenanceJob) -> None:
    if job.status != "running":
        raise JobInvariantError(f"{job.job_id} is not running")


def _compact_cursor_for_event(cursor: Mapping[str, Any]) -> dict[str, Any]:
    return {key: cursor[key] for key in _EVENT_CURSOR_KEYS if key in cursor}


def _job_to_doc(job: MaintenanceJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "market": job.market,
        "dataset_scope": job.dataset_scope,
        "status": job.status,
        "cursor": dict(job.cursor),
        "stats": dict(job.stats),
        "error": job.error,
        "requested_by": job.requested_by,
        "scheduled_at": job.scheduled_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "lock_owner": job.lock_owner,
        "lock_expires_at": job.lock_expires_at,
        "next_run_at": job.next_run_at,
        "expire_at": job.expire_at,
        "events": tuple(
            {
                "status": event.status,
                "at": event.at,
                "cursor": dict(event.cursor),
                "stats": dict(event.stats),
                "error": event.error,
            }
            for event in job.events
        ),
    }


def _job_from_doc(doc: Mapping[str, Any]) -> MaintenanceJob:
    events: list[MaintenanceEvent] = []
    for item in tuple(doc.get("events", ())):
        if not isinstance(item, Mapping):
            continue
        at = item.get("at")
        if not isinstance(at, datetime):
            continue
        events.append(
            MaintenanceEvent(
                status=str(item.get("status", "running")),
                at=at,
                cursor=dict(item.get("cursor", {}) or {}),
                stats={k: int(v) for k, v in dict(item.get("stats", {}) or {}).items()},
                error=str(item.get("error")) if item.get("error") is not None else None,
            )
        )
    return MaintenanceJob(
        job_id=str(doc.get("job_id", "")),
        job_type=str(doc.get("job_type", "")),
        market=str(doc.get("market", "")),
        dataset_scope=str(doc.get("dataset_scope", "")),
        status=str(doc.get("status", "pending")),
        cursor=dict(doc.get("cursor", {}) or {}),
        stats={k: int(v) for k, v in dict(doc.get("stats", {}) or {}).items()},
        error=str(doc.get("error")) if doc.get("error") is not None else None,
        requested_by=str(doc.get("requested_by")) if doc.get("requested_by") is not None else None,
        scheduled_at=doc.get("scheduled_at"),
        started_at=doc.get("started_at"),
        finished_at=doc.get("finished_at"),
        lock_owner=str(doc.get("lock_owner")) if doc.get("lock_owner") is not None else None,
        lock_expires_at=doc.get("lock_expires_at"),
        next_run_at=doc.get("next_run_at"),
        expire_at=doc.get("expire_at"),
        events=events,
    )
