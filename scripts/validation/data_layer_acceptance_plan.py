from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlanStatus = Literal["planned", "blocked"]


@dataclass(frozen=True)
class AcceptanceBatch:
    batch_id: str
    status: PlanStatus
    purpose: str
    required_commands: tuple[str, ...]
    required_evidence: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeProofSlot:
    slot_id: str
    status: PlanStatus
    entrypoint: str
    runtime_command_template: str
    required_evidence: tuple[str, ...]
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class AcceptanceBlocker:
    blocker_id: str
    source: str
    reason: str
    stop_condition: str


@dataclass(frozen=True)
class FinalAcceptancePlan:
    task_id: str
    status: PlanStatus
    batches: tuple[AcceptanceBatch, ...]
    runtime_preflight: tuple[str, ...]
    runtime_proof_slots: tuple[RuntimeProofSlot, ...]
    collect_first_rule: str
    early_stop_exceptions: tuple[str, ...]
    audit_checks: tuple[str, ...]
    allowed_mongo_collections: tuple[str, ...]
    openviking_allowed_roles: tuple[str, ...]
    blockers: tuple[AcceptanceBlocker, ...]


def final_acceptance_plan() -> FinalAcceptancePlan:
    """Return the T14 final acceptance plan without executing acceptance."""

    return FinalAcceptancePlan(
        task_id="T14",
        status="blocked",
        batches=(
            AcceptanceBatch(
                batch_id="focused-unit-contract",
                status="planned",
                purpose="Run focused unit and contract checks before broader integration.",
                required_commands=(
                    "uv run pytest tests/contracts/test_data_layer_models.py "
                    "tests/contracts/test_data_layer_failure_semantics.py "
                    "tests/contracts/test_data_layer_gate_semantics.py",
                    "uv run pytest tests/contracts/test_report_data_plan_cutover.py "
                    "tests/contracts/test_select_warehouse_cutover.py "
                    "tests/contracts/test_old_new_cutover.py",
                ),
                required_evidence=(
                    "explicit failure-state assertions",
                    "cutover contract output",
                    "status redline checks for non-network branches versus remote_success",
                ),
            ),
            AcceptanceBatch(
                batch_id="scoped-integration",
                status="planned",
                purpose="Run scoped integration only after focused checks are green.",
                required_commands=(
                    "uv run pytest tests/integration/data_gateway/test_pack_runtime_report_plan.py",
                    "uv run pytest tests/integration/selection/test_data_job_pipeline.py "
                    "tests/integration/selection/test_select_command_chat_flow.py",
                ),
                required_evidence=(
                    "Mongo evidence references",
                    "selection evidence file references",
                    "OpenViking material lineage/readback references when materialized",
                ),
            ),
        ),
        runtime_preflight=(
            "Read fixed runtime guidance from memory before any live run.",
            "Check existing runtime.env, OpenViking 1933 health, and OpenClaw gateway 18789 health.",
            "If restart is required, use scripts/start-control-runtime.sh and print the AGENTS 12.1 preflight table.",
            "For clean runtime proof, execute scripts/start-control-runtime.sh -- <command> after preflight passes.",
        ),
        runtime_proof_slots=(
            RuntimeProofSlot(
                slot_id="report-command",
                status="planned",
                entrypoint="/report",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused /report proof command>",
                required_evidence=(
                    "provider payload when OpenClaw runtime behavior is in scope",
                    "run provider plan snapshot",
                    "attempt/raw/normalized/http evidence references",
                    "worker-visible pack material references",
                ),
            ),
            RuntimeProofSlot(
                slot_id="a-share-select-command",
                status="planned",
                entrypoint="A-share /select",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused A-share /select proof command>",
                required_evidence=(
                    "warehouse marker",
                    "openbb_normalized references",
                    "selection evidence file references",
                    "cutover proof that legacy completed evidence was not accepted",
                ),
            ),
            RuntimeProofSlot(
                slot_id="ui-provider-display",
                status="planned",
                entrypoint="UI provider display",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused UI provider display proof command>",
                required_evidence=(
                    "real browser screenshot for UI-facing acceptance",
                    "provider display decision inputs",
                    "main-chain evidence proving configured provider changes runtime data",
                ),
            ),
            RuntimeProofSlot(
                slot_id="price-alert-data-entry",
                status="blocked",
                entrypoint="price alert probe",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused price alert data proof command>",
                required_evidence=(
                    "data requirement through the shared data entry",
                    "attempt/evidence references",
                    "gap or alert result without old-path fallback",
                ),
                blocked_by=("DG-GEN-002",),
            ),
            RuntimeProofSlot(
                slot_id="crypto-history-warehouse",
                status="blocked",
                entrypoint="Crypto history warehouse",
                runtime_command_template="scripts/start-control-runtime.sh -- <approved Crypto history proof command>",
                required_evidence=(
                    "approved universe/range",
                    "manifest hash",
                    "raw/normalized/attempt/http evidence references",
                    "OpenViking audit/readback references",
                ),
                blocked_by=("T9B",),
            ),
        ),
        collect_first_rule=(
            "For an approved final batch, continue through recoverable item failures and collect "
            "failure reason, terminal stage, evidence path, logs, root component guess, and fix group."
        ),
        early_stop_exceptions=(
            "data authenticity is untrustworthy",
            "architecture boundary drift appears",
            "PM authority or worker conclusion ownership is at risk",
            "provider evidence chain is untrustworthy",
            "old path fallback appears",
            "runtime preflight fails",
        ),
        audit_checks=(
            "project prohibited-success keyword scan on changed and acceptance paths",
            "old-new cutover contract output",
            "evidence-chain audit from attempt to raw, normalized, and HTTP evidence where applicable",
            "Mongo collection audit limited to approved openbb collections",
            "OpenViking role audit limited to material refs, lineage, readback, and summary",
            "status audit: cache_hit/shared_result/rate_limited/cached_empty/cooldown_skipped are not remote_success",
        ),
        allowed_mongo_collections=(
            "openbb_provider_manifests",
            "openbb_provider_validation_receipts",
            "openbb_run_provider_plans",
            "openbb_provider_attempts",
            "openbb_provider_http_evidence",
            "openbb_raw_payloads",
            "openbb_normalized",
            "openbb_cache_entries",
            "openbb_rate_limits",
            "openbb_single_flight_calls",
        ),
        openviking_allowed_roles=(
            "materials",
            "lineage",
            "readback",
            "summary",
        ),
        blockers=(
            AcceptanceBlocker(
                blocker_id="T9B",
                source="docs/data-layer task list T9B",
                reason=(
                    "Crypto history warehouse remains blocked until human approval defines "
                    "universe, source/exchange, history range, interval, and license boundary."
                ),
                stop_condition="Do not run Crypto history live proof or mark Crypto warehouse ready.",
            ),
            AcceptanceBlocker(
                blocker_id="DG-GEN-002",
                source="docs/data-layer task list DG-GEN-002",
                reason=(
                    "Price alert and UI probe do not yet have complete proof through the shared "
                    "data entry with attempts/evidence/gaps."
                ),
                stop_condition="Do not claim unified data entry acceptance until price alert and UI probe have main-chain proof.",
            ),
            AcceptanceBlocker(
                blocker_id="DATAREQ-SELECTPLAN",
                source="docs/data-layer task list §5.1/§5.18/§5.19",
                reason=(
                    "Report/select planning still lacks full granular DataRequirement and "
                    "SelectDataPlan evidence chain coverage."
                ),
                stop_condition="Do not treat domain-pack-only planning as complete DataRequirement/SelectDataPlan coverage.",
            ),
        ),
    )
