from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.maintenance import (
    CollectionMaintenanceJobRepository,
    InMemoryMaintenanceJobRepository,
    MaintenanceJob,
    run_daily_incremental,
    run_repair,
    run_seed_import,
)
from claw_trade.data_gateway.models import DataResultStatus
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


class MemorySeedIngestRunner:
    def __init__(self, pipeline: IngestPipeline, *, fail_on_seed_id: str | None = None) -> None:
        self._pipeline = pipeline
        self.fail_on_seed_id = fail_on_seed_id
        self.calls: list[tuple[str, object]] = []

    def ingest_seed(self, seed_input: dict[str, object], context: object) -> object:
        seed_id = str(seed_input.get("seed_id", "unknown"))
        self.calls.append((seed_id, context))
        if self.fail_on_seed_id == seed_id:
            raise RuntimeError(f"seed failed: {seed_id}")
        return self._pipeline.ingest_seed(seed_input, context)


class MemoryDataAPI:
    def __init__(self, *, status: str = "ready", gaps: tuple[object, ...] = ()) -> None:
        self.status = status
        self.gaps = gaps
        self.requests: list[object] = []
        self.request_batches: list[tuple[object, ...]] = []
        self.get_data_calls = 0

    def get_data(self, request: object) -> object:
        self.get_data_calls += 1
        return self._record_request(request)

    def request_data(self, requests: tuple[object, ...]) -> list[object]:
        self.request_batches.append(tuple(requests))
        return [self._record_request(request) for request in requests]

    def _record_request(self, request: object) -> object:
        self.requests.append(request)
        request_id = len(self.requests)
        return SimpleNamespace(
            dataset_refs=(f"dataset:{request_id}",),
            raw_refs=(),
            attempt_refs=(f"attempt:{request_id}",),
            gaps=self.gaps,
            status=self.status,
            request_id=f"request-{request_id}",
        )


@dataclass(slots=True)
class Gap:
    gap_id: str


@dataclass(slots=True)
class Slice:
    slice_id: str


@dataclass(slots=True)
class MemoryIncrementalPlanner:
    gaps: list[Gap]
    calls: list[tuple[str, str]]

    def find_gaps(self, *, market: str, dataset_scope: str, as_of: object) -> list[Gap]:
        _ = as_of
        self.calls.append((market, dataset_scope))
        return list(self.gaps)


def _new_ingest_pipeline() -> IngestPipeline:
    return IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )


def _seed_input(seed_id: str, close: float) -> dict[str, object]:
    return {
        "seed_id": seed_id,
        "provider_id": "maintenance_seed_loader",
        "endpoint_id": "seed_daily_bar",
        "payload": [
            {
                "dataset": "daily_bar",
                "symbol_id": f"{seed_id}.SZ",
                "exchange": "SZSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "close": close,
            }
        ],
        "required_fields": ("close",),
    }


def test_seed_import_updates_job_status_cursor_and_stats() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:seed:cn_a:daily_bar:1",
        job_type="seed_import",
        market="CN_A",
        dataset_scope="daily_bar",
    )
    pipeline = MemorySeedIngestRunner(_new_ingest_pipeline())

    result = run_seed_import(
        job,
        seed_inputs=[_seed_input("seed-a", 10.1), _seed_input("seed-b", 10.2)],
        ingest_pipeline=pipeline,
        repo=repo,
    )

    assert result.status == "succeeded"
    assert result.cursor["index"] == 2
    assert result.stats["dataset_refs"] == 2
    assert result.stats["raw_refs"] == 2
    assert result.stats["attempt_refs"] == 2
    assert result.stats["status_partial"] == 2
    assert result.stats["gap_field_missing"] == 2
    assert result.cursor["input_range"] == {"start_index": 0, "end_index": 1}
    assert result.cursor["audit_counts"]["dataset_refs"] == 2
    assert "audit_dataset_refs" not in result.cursor
    assert result.cursor["last_output_refs"]["attempt_refs"]
    manifests = repo.list_dataset_manifests()
    assert len(manifests) == 1
    assert manifests[0]["source_summary"].startswith("maintenance:seed_import:CN_A:daily_bar:")
    assert manifests[0]["manifest_ref"] == result.cursor["audit_manifest_ref"]
    assert tuple(manifests[0]["dataset_refs"]) == tuple(manifests[0]["output_refs"]["dataset_refs"])
    assert manifests[0]["gap_summary"] == manifests[0]["gaps"]
    assert [item[0] for item in pipeline.calls] == ["seed-a", "seed-b"]
    assert [event.status for event in result.events][-1] == "succeeded"


def test_seed_import_is_idempotent_after_success() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:seed:cn_a:daily_bar:2",
        job_type="seed_import",
        market="CN_A",
        dataset_scope="daily_bar",
    )
    first_pipeline = MemorySeedIngestRunner(_new_ingest_pipeline())
    run_seed_import(job, seed_inputs=[_seed_input("seed-a", 10.1)], ingest_pipeline=first_pipeline, repo=repo)

    second_pipeline = MemorySeedIngestRunner(_new_ingest_pipeline())
    result = run_seed_import(job, seed_inputs=[_seed_input("seed-a", 10.1)], ingest_pipeline=second_pipeline, repo=repo)

    assert result.status == "succeeded"
    assert second_pipeline.calls == []


def test_seed_import_resume_with_cursor_after_failure() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:seed:cn_a:daily_bar:3",
        job_type="seed_import",
        market="CN_A",
        dataset_scope="daily_bar",
    )
    broken_pipeline = MemorySeedIngestRunner(_new_ingest_pipeline(), fail_on_seed_id="seed-b")
    with pytest.raises(RuntimeError):
        run_seed_import(
            job,
            seed_inputs=[_seed_input("seed-a", 10.1), _seed_input("seed-b", 10.2), _seed_input("seed-c", 10.3)],
            ingest_pipeline=broken_pipeline,
            repo=repo,
        )

    failed_job = repo.get(job.job_id)
    assert failed_job is not None
    assert failed_job.status == "failed"
    assert failed_job.cursor["index"] == 1
    assert "audit_dataset_refs" not in failed_job.cursor

    resumed_pipeline = MemorySeedIngestRunner(_new_ingest_pipeline())
    resumed = run_seed_import(
        job,
        seed_inputs=[_seed_input("seed-a", 10.1), _seed_input("seed-b", 10.2), _seed_input("seed-c", 10.3)],
        ingest_pipeline=resumed_pipeline,
        repo=repo,
    )
    assert resumed.status == "succeeded"
    assert [item[0] for item in resumed_pipeline.calls] == ["seed-b", "seed-c"]
    assert resumed.cursor["index"] == 3
    manifests = repo.list_dataset_manifests()
    assert len(manifests) == 1
    assert len(manifests[0]["output_refs"]["dataset_refs"]) == 3


def test_daily_incremental_uses_public_requests_and_maintenance_consumer() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:inc:us:daily_bar:1",
        job_type="daily_incremental",
        market="US",
        dataset_scope="daily_bar",
    )
    api = MemoryDataAPI()
    seen: list[tuple[str, str]] = []

    def request_from_gap(gap: Gap, *, consumer: str, consumer_id: str) -> dict[str, str]:
        seen.append((consumer, consumer_id))
        return {
            "request_id": f"request-gap-{gap.gap_id}",
            "api_id": "us.daily_bar",
            "gap_id": gap.gap_id,
            "consumer": consumer,
            "consumer_id": consumer_id,
            "params": {"required_fields": ("close", "volume")},
            "date_range_start": "2026-05-01",
            "date_range_end": "2026-05-31",
        }

    result = run_daily_incremental(
        job,
        data_api=api,
        repo=repo,
        gaps=[Gap("g1"), Gap("g2")],
        request_from_gap=request_from_gap,
    )

    assert result.status == "succeeded"
    assert len(api.requests) == 2
    assert api.get_data_calls == 0
    assert len(api.request_batches) == 1
    assert len(api.request_batches[0]) == 2
    assert seen == [("maintenance", job.job_id), ("maintenance", job.job_id)]
    assert result.cursor["index"] == 2
    assert result.cursor["last_request_id"] == "request-2"
    assert result.cursor["last_input_range"] == {"start": "2026-05-01", "end": "2026-05-31"}
    manifests = repo.list_dataset_manifests()
    assert len(manifests) == 1
    assert tuple(manifests[0]["request_ids"]) == ("request-1", "request-2")
    assert manifests[0]["required_fields"] == ("close", "volume")
    assert result.cursor["audit_counts"]["required_fields"] == 2
    assert tuple(manifests[0]["attempt_refs"]) == ("attempt:1", "attempt:2")
    assert tuple(manifests[0]["output_refs"]["attempt_refs"]) == ("attempt:1", "attempt:2")
    assert manifests[0]["gap_summary"] == ()
    assert result.stats["attempt_refs"] == 2


def test_maintenance_cursor_stores_manifest_pointer_not_unbounded_refs() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:inc:us:daily_bar:cursor-size",
        job_type="daily_incremental",
        market="US",
        dataset_scope="daily_bar",
    )
    api = MemoryDataAPI()
    gaps = [Gap(f"g{i}") for i in range(25)]

    result = run_daily_incremental(
        job,
        data_api=api,
        repo=repo,
        gaps=gaps,
        request_from_gap=lambda gap, *, consumer, consumer_id: {
            "request_id": f"request-{gap.gap_id}",
            "api_id": "us.daily_bar",
            "consumer": consumer,
            "consumer_id": consumer_id,
            "params": {"required_fields": ("close",)},
        },
    )

    legacy_keys = {"audit_dataset_refs", "audit_raw_refs", "audit_attempt_refs", "audit_gap_summary"}
    assert not legacy_keys.intersection(result.cursor)
    assert result.cursor["audit_counts"]["dataset_refs"] == 25
    assert result.cursor["audit_counts"]["attempt_refs"] == 25
    assert result.cursor["last_output_refs"]["attempt_refs"] == ["attempt:25"]
    for event in result.events:
        assert not legacy_keys.intersection(event.cursor)
    manifests = repo.list_dataset_manifests()
    assert len(manifests) == 1
    assert len(manifests[0]["output_refs"]["dataset_refs"]) == 25
    assert len(manifests[0]["output_refs"]["attempt_refs"]) == 25


def test_maintenance_stats_use_enum_status_value() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:inc:crypto:daily_bar:enum-status",
        job_type="daily_incremental",
        market="CRYPTO",
        dataset_scope="daily_bar",
    )
    api = MemoryDataAPI(status=DataResultStatus.PARTIAL)

    run_daily_incremental(
        job,
        data_api=api,
        repo=repo,
        gaps=[Gap("g1")],
        request_from_gap=lambda gap, *, consumer, consumer_id: {
            "request_id": f"request-{gap.gap_id}",
            "api_id": "crypto.daily_bar",
            "consumer": consumer,
            "consumer_id": consumer_id,
        },
    )

    saved = repo.get(job.job_id)
    assert saved is not None
    assert saved.stats["status_partial"] == 1
    assert "status_DataResultStatus.PARTIAL" not in saved.stats


def test_repair_uses_public_requests_and_cursor() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:repair:hk:filing:1",
        job_type="repair",
        market="HK",
        dataset_scope="official_filing",
    )
    gap = SimpleNamespace(
        gap_id="gap:r1",
        request_id="request-gap-r1",
        reason="warehouse_stale",
        severity="warn",
        evidence_refs=("attempt:evidence:r1",),
    )
    api = MemoryDataAPI(status="partial", gaps=(gap,))
    called: list[str] = []

    def request_from_slice(slice_item: Slice, *, consumer: str, consumer_id: str) -> dict[str, str]:
        called.append(f"{consumer}:{consumer_id}:{slice_item.slice_id}")
        return {
            "request_id": f"request-slice-{slice_item.slice_id}",
            "api_id": "hk.official_filing",
            "slice_id": slice_item.slice_id,
            "params": {"required_fields": ("title", "published_at")},
            "date_range_start": "2026-05-01",
            "date_range_end": "2026-05-30",
        }

    result = run_repair(
        job,
        data_api=api,
        repo=repo,
        slices=[Slice("s1"), Slice("s2"), Slice("s3")],
        request_from_slice=request_from_slice,
    )

    assert result.status == "succeeded"
    assert len(api.requests) == 3
    assert api.get_data_calls == 0
    assert len(api.request_batches) == 1
    assert result.cursor["index"] == 3
    assert result.stats["status_partial"] == 3
    assert result.stats["gap_warehouse_stale"] == 3
    assert result.cursor["last_gap_summary"][0]["reason"] == "warehouse_stale"
    manifests = repo.list_dataset_manifests()
    assert len(manifests) == 1
    assert manifests[0]["gaps"][0]["reason"] == "warehouse_stale"
    assert manifests[0]["gap_summary"][0]["reason"] == "warehouse_stale"
    assert called[0].startswith("maintenance:")


def test_daily_incremental_uses_injected_gap_planner_when_gaps_not_provided() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:inc:hk:news:planner",
        job_type="daily_incremental",
        market="HK",
        dataset_scope="company_news",
    )
    api = MemoryDataAPI()
    planner = MemoryIncrementalPlanner(gaps=[Gap("g1"), Gap("g2")], calls=[])

    run_daily_incremental(
        job,
        data_api=api,
        repo=repo,
        incremental_planner=planner,
        request_from_gap=lambda gap, *, consumer, consumer_id: {
            "request_id": f"request-{gap.gap_id}",
            "api_id": "hk.company_news",
            "gap_id": gap.gap_id,
            "consumer": consumer,
            "consumer_id": consumer_id,
        },
    )

    assert planner.calls == [("HK", "company_news")]
    assert len(api.requests) == 2
    assert len(api.request_batches) == 1


def test_daily_incremental_requires_planner_when_gaps_not_provided() -> None:
    repo = InMemoryMaintenanceJobRepository()
    job = MaintenanceJob(
        job_id="job:inc:crypto:quote:planner-missing",
        job_type="daily_incremental",
        market="CRYPTO",
        dataset_scope="quote_snapshot",
    )

    with pytest.raises(ValueError, match="incremental_planner is required"):
        run_daily_incremental(job, data_api=MemoryDataAPI(), repo=repo)


def test_collection_maintenance_repository_persists_to_maintenance_jobs_collection() -> None:
    dataset_repo = DatasetRepository()
    repo = CollectionMaintenanceJobRepository(repository=dataset_repo)
    job = MaintenanceJob(
        job_id="job:seed:cn_a:daily_bar:collection",
        job_type="seed_import",
        market="CN_A",
        dataset_scope="daily_bar",
    )
    repo.save(job)
    loaded = repo.get(job.job_id)
    assert loaded is not None
    assert loaded.job_id == job.job_id
    assert dataset_repo.get_maintenance_job(job.job_id) is not None


def test_collection_maintenance_repository_persists_dataset_manifest() -> None:
    dataset_repo = DatasetRepository()
    repo = CollectionMaintenanceJobRepository(repository=dataset_repo)
    manifest_ref = repo.save_dataset_manifest(
        {
            "request_ids": ("request-1",),
            "dataset_refs": ("dataset:1",),
            "attempt_refs": ("attempt:1",),
            "raw_refs": (),
            "gaps": (),
            "manifest_hash": "sha256:test",
        }
    )

    stored = dataset_repo.get_dataset_manifest(manifest_ref)
    assert stored is not None
    assert stored["manifest_ref"] == manifest_ref
