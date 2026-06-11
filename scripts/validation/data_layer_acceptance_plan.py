from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlanStatus = Literal["planned", "verified", "out_of_scope"]


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
class ScopeNote:
    note_id: str
    source: str
    detail: str


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
    scope_notes: tuple[ScopeNote, ...]


def final_acceptance_plan() -> FinalAcceptancePlan:
    """Return the T14 final acceptance plan without executing acceptance."""

    return FinalAcceptancePlan(
        task_id="T14",
        status="planned",
        batches=(
            AcceptanceBatch(
                batch_id="focused-unit-contract",
                status="planned",
                purpose="Run the current DLT unit and contract checks before broader integration.",
                required_commands=(
                    "uv run pytest tests/unit/data_gateway/test_models.py "
                    "tests/contracts/test_data_api_contract.py "
                    "tests/contracts/test_data_layer_module_boundaries.py "
                    "tests/unit/data_gateway/test_data_service_flow.py "
                    "tests/unit/data_gateway/test_query_planner.py "
                    "tests/contracts/test_data_service_ten_step_flow.py",
                    "uv run pytest tests/contracts/test_provider_capabilities.py "
                    "tests/unit/data_gateway/test_provider_registry.py "
                    "tests/unit/data_gateway/test_provider_selector.py "
                    "tests/unit/data_gateway/test_ingest_pipeline.py "
                    "tests/contracts/test_evidence_chain.py",
                ),
                required_evidence=(
                    "explicit failure-state assertions",
                    "current DLT contract output",
                    "status redline checks for non-network branches versus remote_success",
                ),
            ),
            AcceptanceBatch(
                batch_id="scoped-integration",
                status="planned",
                purpose="Run scoped data-gateway integration only after focused checks are green.",
                required_commands=(
                    "uv run pytest tests/integration/data_gateway/test_real_component_flow.py",
                    "uv run pytest --collect-only tests/unit/data_gateway tests/integration/data_gateway tests/contracts",
                ),
                required_evidence=(
                    "Mongo evidence references",
                    "dataset manifest references",
                    "collection boundary audit",
                ),
            ),
            AcceptanceBatch(
                batch_id="authenticity-audit",
                status="planned",
                purpose="Audit success semantics and old data-layer path isolation.",
                required_commands=(
                    'rg -n "stub|mock|fake|fallback|placeholder|capture-only|capture_only" '
                    "src/claw_trade/data_gateway tests/unit/data_gateway tests/integration/data_gateway tests/contracts",
                    'rg -n "data_gateway_bak|OpenViking|worker pack|reader brief|report plan|select plan|material writer" '
                    "src/claw_trade/data_gateway tests/unit/data_gateway tests/integration/data_gateway tests/contracts",
                    'rg -n "remote_success|cache_hit|shared_result|rate_limited|cached_empty|cooldown_skipped|sdk_http_unknown|evidence_write_failed" '
                    "src/claw_trade/data_gateway tests/unit/data_gateway tests/integration/data_gateway tests/contracts",
                ),
                required_evidence=(
                    "no production provider path uses capture-only or legacy success",
                    "negative tests isolate old paths without importing them",
                    "non-remote statuses remain distinct from remote_success",
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
                status="out_of_scope",
                entrypoint="/report",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused /report proof command>",
                required_evidence=(
                    "provider payload when OpenClaw runtime behavior is in scope",
                    "workflow runtime proof outside data-layer-only acceptance",
                    "attempt/raw/normalized/http evidence references",
                ),
            ),
            RuntimeProofSlot(
                slot_id="a-share-select-command",
                status="out_of_scope",
                entrypoint="A-share /select",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused A-share /select proof command>",
                required_evidence=(
                    "warehouse marker",
                    "normalized_datasets references",
                    "selection command proof outside data-layer-only acceptance",
                ),
            ),
            RuntimeProofSlot(
                slot_id="ui-provider-display",
                status="out_of_scope",
                entrypoint="UI provider display",
                runtime_command_template="scripts/start-control-runtime.sh -- <focused UI provider display proof command>",
                required_evidence=(
                    "real browser screenshot for UI-facing acceptance",
                    "provider display decision inputs",
                    "UI proof outside data-layer-only acceptance",
                ),
            ),
            RuntimeProofSlot(
                slot_id="crypto-daily-bar-incremental",
                status="verified",
                entrypoint="CRYPTO daily_bar maintenance incremental",
                runtime_command_template="uv run python <audited CRYPTO daily incremental runner>",
                required_evidence=(
                    "Binance Public Data / REST Kline source",
                    "job:daily_incremental:crypto:daily_bar:2026-05:attempt-006",
                    "latest normalized daily_bar date 2026-05-31",
                    "dataset manifest and maintenance job references",
                ),
            ),
            RuntimeProofSlot(
                slot_id="cn-a-live-provider",
                status="planned",
                entrypoint="CN_A daily_bar provider",
                runtime_command_template="uv run python <audited CN_A provider smoke>",
                required_evidence=(
                    "Tushare credential and endpoint read from Mongo settings",
                    "managed_http observation",
                    "no environment credential read",
                ),
            ),
            RuntimeProofSlot(
                slot_id="us-live-provider",
                status="out_of_scope",
                entrypoint="US daily_bar provider",
                runtime_command_template="uv run python <audited US provider smoke>",
                required_evidence=(
                    "Yahoo Finance public daily-bar source is available without paid credentials",
                    "Finnhub paid US enhancement must return credential_missing until configured",
                ),
            ),
            RuntimeProofSlot(
                slot_id="hk-live-provider",
                status="out_of_scope",
                entrypoint="HK daily_bar provider",
                runtime_command_template="uv run python <audited HK provider smoke>",
                required_evidence=(
                    "requires configured data_source:longport in Mongo settings",
                    "must return credential_missing until configured",
                ),
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
            "legacy data path success appears",
            "runtime preflight fails",
        ),
        audit_checks=(
            "context-aware authenticity keyword scan on data-layer paths",
            "new data layer cutover contract output",
            "evidence-chain audit from attempt to raw, normalized, and HTTP evidence where applicable",
            "Mongo collection audit limited to docs/数据层详细设计.md §11 approved collections",
            "OpenViking role audit limited to material refs, lineage, readback, and summary",
            "status audit: cache_hit/shared_result/rate_limited/cached_empty/cooldown_skipped are not remote_success",
        ),
        allowed_mongo_collections=(
            "normalized_datasets",
            "raw_payloads",
            "provider_attempts",
            "provider_rate_limits",
            "single_flight_calls",
            "provider_result_cache",
            "dataset_manifests",
            "maintenance_jobs",
        ),
        openviking_allowed_roles=(
            "materials",
            "lineage",
            "readback",
            "summary",
        ),
        blockers=(),
        scope_notes=(
            ScopeNote(
                note_id="crypto-source",
                source="docs/加密币选币讨论纪要.md",
                detail=(
                    "CRYPTO daily OHLCV uses Binance Public Data / REST Kline as primary source; "
                    "CoinGecko Pro is an enhanced settings source for metadata/market data, not the primary K-line source."
                ),
            ),
            ScopeNote(
                note_id="report-select-boundary",
                source="docs/数据层详细设计.md",
                detail="Report/select workflow proof is a workflow acceptance item; data layer only returns data, gaps, and evidence.",
            ),
        ),
    )
