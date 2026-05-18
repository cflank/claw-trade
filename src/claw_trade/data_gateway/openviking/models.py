from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


RelationKind = Literal[
    "final_report_claim_to_pm_l1",
    "pm_l1_to_worker_l1",
    "worker_l1_to_l2_evidence",
    "l2_evidence_to_pack_audit",
    "pack_audit_to_provider_attempt",
    "provider_attempt_to_raw_payload",
    "provider_attempt_to_normalized_result",
    "provider_attempt_to_cache_receipt",
    "worker_l1_to_chart_asset",
    "worker_l1_to_data_gap",
]

HealthStatus = Literal["ok", "degraded", "blocked", "unavailable"]


@dataclass(frozen=True)
class FinalReportClaim:
    claim_id: str
    text: str
    section: str
    pm_material_id: str
    worker_material_ids: tuple[str, ...]
    chart_ids: tuple[str, ...] = ()
    gap_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenVikingTree:
    root_uri: str
    node_count: int
    nodes: tuple[Mapping[str, str], ...]
    status: HealthStatus = "ok"
    root_cause: str | None = None


@dataclass(frozen=True)
class OpenVikingGrepResult:
    root_uri: str
    pattern: str
    matches: tuple[Mapping[str, str], ...]
    status: HealthStatus = "ok"
    root_cause: str | None = None


@dataclass(frozen=True)
class OpenVikingGlobResult:
    root_uri: str
    pattern: str
    matches: tuple[str, ...]
    status: HealthStatus = "ok"
    root_cause: str | None = None


@dataclass(frozen=True)
class OpenVikingFindResult:
    run_id: str
    query: str
    matches: tuple[Mapping[str, str], ...]
    usable_as_investment_fact: bool = False
    status: HealthStatus = "ok"
    root_cause: str | None = None


@dataclass(frozen=True)
class OpenVikingRelation:
    from_uri: str
    to_uri: str
    kind: RelationKind
    run_id: str
    stage: str
    worker_id: str | None
    call_id: str | None
    created_at: str
    evidence_hash: str | None
    note: str


@dataclass(frozen=True)
class EvidenceBundleReceipt:
    run_id: str
    bundle_uri: str
    bundle_path: str
    sha256: str
    size_bytes: int
    portability_status: Literal["complete_portable", "metadata_verified", "blocked"]
    raw_payload_policy: Literal["included", "redacted", "external_store_required", "blocked"]
    external_store_refs: tuple[str, ...]
    exported_at: str
    imported_run_id: str | None = None
    import_status: HealthStatus | None = None
    root_cause: str | None = None


@dataclass(frozen=True)
class ContextIndexReceipt:
    uri: str
    index_level: Literal["L0", "L1", "semantic"]
    status: HealthStatus
    vectorized: bool
    searchable_by_control_plane: bool
    visible_to_worker: bool
    reason: str | None = None


@dataclass(frozen=True)
class EngineeringMemoryRecord:
    run_id: str
    event_id: str
    category: Literal["runtime", "debug", "task_retro", "operator_note"]
    text: str
    created_at: str
    visible_to_worker: bool = False
    usable_as_investment_fact: bool = False


@dataclass(frozen=True)
class OpenVikingRuntimeHealth:
    status: HealthStatus
    metrics_status: HealthStatus
    observer_status: HealthStatus
    lock_status: HealthStatus
    recovery_status: HealthStatus
    queue_status: HealthStatus
    checked_at: str
    root_cause: str | None
    raw_refs: tuple[str, ...]
