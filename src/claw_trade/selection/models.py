from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from claw_trade.workflow.models import RunRequest, WorkflowEntryPoint

DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION = "cn_a.selection_strategy.v1"
DEFAULT_SELECTION_WEIGHT_VERSION = "cn_a.selection_weights.v1"


def _require_enum(name: str, value: object, enum_type: type[StrEnum]) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{name} must be {enum_type.__name__}")


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _require_iso_date(name: str, value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO date") from exc


def _require_iso_timestamp(name: str, value: str) -> None:
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO timestamp") from exc


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64 or not all(ch in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError(f"{name} must be 64-char hex sha256")


class SelectionMarket(StrEnum):
    CN_A = "CN_A"
    CRYPTO = "CRYPTO"
    HK = "HK"
    US = "US"


class SelectionProfile(StrEnum):
    CN_A = "CN_A"
    CRYPTO = "CRYPTO"
    HK = "HK"
    US = "US"


class SelectionSystemContextPolicy(StrEnum):
    SINGLE_WORKER_MINIMAL = "single_worker_minimal"


class SelectionTriggerSource(StrEnum):
    SCHEDULED = "scheduled"
    SELECT_COMMAND_REFRESH = "select_command_refresh"
    MANUAL_BACKFILL = "manual_backfill"
    MANUAL_RERUN = "manual_rerun"


class SelectionDataRunStatus(StrEnum):
    PLANNED = "planned"
    LEASE_PENDING = "lease_pending"
    RUNNING = "running"
    FETCHING_DATA = "fetching_data"
    NORMALIZING_INPUTS = "normalizing_inputs"
    BUILDING_FEATURES = "building_features"
    FILTERING_AND_SCORING = "filtering_and_scoring"
    BUILDING_CANDIDATE_CACHE = "building_candidate_cache"
    APPROVING_CANDIDATE_CACHE = "approving_candidate_cache"
    NO_CANDIDATE = "no_candidate"
    COMPLETED = "completed"
    FAILED = "failed"


class SelectionWorkflowStatus(StrEnum):
    RECEIVED = "received"
    RESOLVING_REQUEST = "resolving_request"
    LOADING_COMPLETED_SELECTION_RUN = "loading_completed_selection_run"
    VALIDATING_CANDIDATE_CACHE = "validating_candidate_cache"
    SELECT_RUN_CREATED = "select_run_created"
    STRATEGIST_RUNNING = "strategist_running"
    STRATEGIST_APPROVED = "strategist_approved"
    SKEPTIC_RUNNING = "skeptic_running"
    SKEPTIC_APPROVED = "skeptic_approved"
    MANAGER_RUNNING = "manager_running"
    MANAGER_APPROVED = "manager_approved"
    PORTFOLIO_MANAGER_RUNNING = "portfolio_manager_running"
    COMPLETED = "completed"
    WAITING_REPORT_CONFIRMATION = "waiting_report_confirmation"
    REPORT_HANDOFF_STARTED = "report_handoff_started"
    MARKET_STRATEGY_UNAPPROVED = "market_strategy_unapproved"
    NO_COMPLETED_SELECTION_RUN = "no_completed_selection_run"
    NO_CANDIDATE_SELECTION_RUN = "no_candidate_selection_run"
    STALE_SELECTION_RUN = "stale_selection_run"
    CANDIDATE_CACHE_NOT_APPROVED = "candidate_cache_not_approved"
    CANDIDATE_CACHE_HASH_MISMATCH = "candidate_cache_hash_mismatch"
    CANDIDATE_CACHE_INTEGRITY_FAILED = "candidate_cache_integrity_failed"
    CANDIDATE_CACHE_LINEAGE_INCOMPLETE = "candidate_cache_lineage_incomplete"
    SELECTION_WAREHOUSE_CHECK_MISSING = "selection_warehouse_check_missing"
    SELECT_MARKET_UNSUPPORTED = "select_market_unsupported"
    CRYPTO_SELECT_HISTORY_MISSING = "crypto_select_history_missing"
    TOOL_SCHEMA_VIOLATION = "tool_schema_violation"
    WORKER_RUNTIME_FAILED = "worker_runtime_failed"
    ARTIFACT_APPROVAL_FAILED = "artifact_approval_failed"
    SELECTION_RESULT_INVALID = "selection_result_invalid"
    FAILED = "failed"


class SelectionWorkerId(StrEnum):
    STRATEGIST = "selection_strategist"
    SKEPTIC = "selection_skeptic"
    MANAGER = "selection_manager"
    PORTFOLIO_MANAGER = "selection_portfolio_manager"


class SelectionStage(StrEnum):
    SELECTION_REVIEW = "selection_review"
    SELECTION_DECISION = "selection_decision"
    SELECTION_PORTFOLIO_DECISION = "selection_portfolio_decision"
    SELECTION_REPORT_HANDOFF = "selection_report_handoff"


class SelectionConfirmationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REPORT_HANDOFF_STARTED = "report_handoff_started"
    REPORT_HANDOFF_FAILED = "report_handoff_failed"


class SelectionBatchScope(StrEnum):
    SELECTION_BATCH = "selection_batch"


class DataGapSeverity(StrEnum):
    WARN = "warn"
    BLOCKER = "blocker"


class CandidateCacheReadbackStatus(StrEnum):
    VERIFIED = "verified"


class ApprovedSelectionStrategyStatus(StrEnum):
    APPROVED = "approved"


DATA_RUN_TERMINAL_STATUSES = frozenset(
    {
        SelectionDataRunStatus.NO_CANDIDATE,
        SelectionDataRunStatus.COMPLETED,
        SelectionDataRunStatus.FAILED,
    }
)

WORKFLOW_FAILURE_STATUSES = frozenset(
    {
        SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED,
        SelectionWorkflowStatus.NO_COMPLETED_SELECTION_RUN,
        SelectionWorkflowStatus.NO_CANDIDATE_SELECTION_RUN,
        SelectionWorkflowStatus.STALE_SELECTION_RUN,
        SelectionWorkflowStatus.CANDIDATE_CACHE_NOT_APPROVED,
        SelectionWorkflowStatus.CANDIDATE_CACHE_HASH_MISMATCH,
        SelectionWorkflowStatus.CANDIDATE_CACHE_INTEGRITY_FAILED,
        SelectionWorkflowStatus.CANDIDATE_CACHE_LINEAGE_INCOMPLETE,
        SelectionWorkflowStatus.SELECTION_WAREHOUSE_CHECK_MISSING,
        SelectionWorkflowStatus.SELECT_MARKET_UNSUPPORTED,
        SelectionWorkflowStatus.CRYPTO_SELECT_HISTORY_MISSING,
        SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION,
        SelectionWorkflowStatus.WORKER_RUNTIME_FAILED,
        SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED,
        SelectionWorkflowStatus.SELECTION_RESULT_INVALID,
        SelectionWorkflowStatus.FAILED,
    }
)

WORKFLOW_TERMINAL_STATUSES = frozenset(
    {
        SelectionWorkflowStatus.REPORT_HANDOFF_STARTED,
        *WORKFLOW_FAILURE_STATUSES,
    }
)

DISPATCH_STAGES = frozenset(
    {
        SelectionStage.SELECTION_REVIEW,
        SelectionStage.SELECTION_DECISION,
        SelectionStage.SELECTION_PORTFOLIO_DECISION,
    }
)

WORKFLOW_DECISION_REQUIRED_STATUSES = frozenset(
    {
        SelectionWorkflowStatus.COMPLETED,
        SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION,
        SelectionWorkflowStatus.REPORT_HANDOFF_STARTED,
    }
)

_DISPATCH_RUNTIME_VAR_KEYS = frozenset(
    {
        "market",
        "profile",
        "trade_date",
        "selection_run_id",
        "select_workflow_run_id",
    }
)


@dataclass(frozen=True)
class SelectRequest:
    request_id: str
    market: SelectionMarket
    profile: SelectionProfile
    trade_date: str | None
    user_id: str | None
    created_at: str
    force_refresh: bool = False
    entry_point: WorkflowEntryPoint = WorkflowEntryPoint.SELECT_COMMAND
    system_context_policy: SelectionSystemContextPolicy = SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL

    def __post_init__(self) -> None:
        _require_non_empty("request_id", self.request_id)
        _require_enum("market", self.market, SelectionMarket)
        _require_enum("profile", self.profile, SelectionProfile)
        if self.trade_date is not None:
            _require_iso_date("trade_date", self.trade_date)
        _require_iso_timestamp("created_at", self.created_at)
        if self.entry_point != WorkflowEntryPoint.SELECT_COMMAND:
            raise ValueError("SelectRequest.entry_point must be select_command")
        _require_enum("system_context_policy", self.system_context_policy, SelectionSystemContextPolicy)


@dataclass(frozen=True)
class SelectionDataNeedAudit:
    plan_id: str
    scope: SelectionBatchScope
    market: SelectionMarket
    profile: SelectionProfile
    trade_date: str
    lookback_trading_days: int
    universe_scope: str
    coverage_groups: tuple[str, ...]
    ttl_policy_ref: str
    lineage_root_ref: str

    def __post_init__(self) -> None:
        _require_non_empty("plan_id", self.plan_id)
        _require_enum("scope", self.scope, SelectionBatchScope)
        _require_enum("market", self.market, SelectionMarket)
        _require_enum("profile", self.profile, SelectionProfile)
        _require_iso_date("trade_date", self.trade_date)
        if self.lookback_trading_days <= 0:
            raise ValueError("lookback_trading_days must be > 0")
        _require_non_empty("universe_scope", self.universe_scope)
        if not self.coverage_groups:
            raise ValueError("coverage_groups must be non-empty")
        _require_non_empty("ttl_policy_ref", self.ttl_policy_ref)
        _require_non_empty("lineage_root_ref", self.lineage_root_ref)


@dataclass(frozen=True)
class SelectionRunPlan:
    selection_run_id: str
    market: SelectionMarket
    profile: SelectionProfile
    trade_date: str
    lookback_trading_days: int
    universe_scope: str
    data_need_audit_ref: str
    approved_strategy_config_ref: str
    trigger_source: SelectionTriggerSource
    supersedes_run_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("selection_run_id", self.selection_run_id)
        _require_enum("market", self.market, SelectionMarket)
        _require_enum("profile", self.profile, SelectionProfile)
        _require_iso_date("trade_date", self.trade_date)
        if self.lookback_trading_days <= 0:
            raise ValueError("lookback_trading_days must be > 0")
        _require_non_empty("universe_scope", self.universe_scope)
        _require_non_empty("data_need_audit_ref", self.data_need_audit_ref)
        _require_non_empty("approved_strategy_config_ref", self.approved_strategy_config_ref)
        _require_enum("trigger_source", self.trigger_source, SelectionTriggerSource)
        if self.supersedes_run_id is not None and self.trigger_source != SelectionTriggerSource.MANUAL_RERUN:
            raise ValueError("supersedes_run_id is only valid for manual_rerun")


@dataclass(frozen=True)
class CandidateCacheRef:
    selection_run_id: str
    material_id: str
    l1_uri: str
    content_sha256: str
    manifest_ref: str
    approved_at: str
    expires_at: str
    cache_summary_ref: str

    def __post_init__(self) -> None:
        _require_non_empty("selection_run_id", self.selection_run_id)
        _require_non_empty("material_id", self.material_id)
        _require_non_empty("l1_uri", self.l1_uri)
        _require_sha256("content_sha256", self.content_sha256)
        _require_non_empty("manifest_ref", self.manifest_ref)
        _require_iso_timestamp("approved_at", self.approved_at)
        _require_iso_timestamp("expires_at", self.expires_at)
        _require_non_empty("cache_summary_ref", self.cache_summary_ref)


@dataclass(frozen=True)
class DataGapRef:
    gap_id: str
    domain: str
    gap_code: str
    severity: DataGapSeverity
    attempt_refs: tuple[str, ...]
    reader_message: str
    source_metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _require_non_empty("gap_id", self.gap_id)
        _require_non_empty("domain", self.domain)
        _require_non_empty("gap_code", self.gap_code)
        _require_enum("severity", self.severity, DataGapSeverity)
        if not self.attempt_refs:
            raise ValueError("attempt_refs must be non-empty")
        _require_non_empty("reader_message", self.reader_message)


@dataclass(frozen=True)
class CandidateFactRow:
    rank: int
    ticker: str
    company_name: str
    industry: str | None
    feature_values: Mapping[str, float | int | str | None]
    strategy_hits: tuple[str, ...]
    risk_flags: tuple[str, ...]
    data_quality: str
    source_summary: str
    total_score: float | None = None
    component_scores: Mapping[str, float | int | str | None] | None = None
    strategy_sources: tuple[str, ...] = ()
    strategy_variants: tuple[str, ...] = ()
    hit_fields: Mapping[str, float | int | str | None] | None = None
    actual_metric_values: Mapping[str, float | int | str | None] | None = None
    risk_penalty: float | None = None
    data_gap_penalty: float | None = None
    tie_break_fields: Mapping[str, float | int | str | None] | None = None
    strategy_config_version: str = DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION
    weight_version: str = DEFAULT_SELECTION_WEIGHT_VERSION

    def __post_init__(self) -> None:
        if self.rank <= 0 or self.rank > 20:
            raise ValueError("rank must be in [1, 20]")
        _require_non_empty("ticker", self.ticker)
        _require_non_empty("company_name", self.company_name)
        if not self.feature_values:
            raise ValueError("feature_values must be non-empty")
        _require_non_empty("data_quality", self.data_quality)
        _require_non_empty("source_summary", self.source_summary)
        _require_non_empty("strategy_config_version", self.strategy_config_version)
        _require_non_empty("weight_version", self.weight_version)


@dataclass(frozen=True)
class CandidateCacheSummary:
    summary_md: str
    candidates: tuple[CandidateFactRow, ...]
    data_quality_summary: str
    source_summary: str

    def __post_init__(self) -> None:
        _require_non_empty("summary_md", self.summary_md)
        if not self.candidates:
            raise ValueError("candidates must be non-empty")
        if len(self.candidates) > 20:
            raise ValueError("candidates cannot exceed top20")
        ranks = {row.rank for row in self.candidates}
        if len(ranks) != len(self.candidates):
            raise ValueError("candidate ranks must be unique")
        _require_non_empty("data_quality_summary", self.data_quality_summary)
        _require_non_empty("source_summary", self.source_summary)


@dataclass(frozen=True)
class CandidateCacheManifest:
    schema_version: str
    selection_run_id: str
    market: SelectionMarket
    profile: SelectionProfile
    trade_date: str
    candidate_count: int
    source_lineage_refs: tuple[str, ...]
    cache_body_sha256: str
    strategy_config_ref: str
    readback_status: CandidateCacheReadbackStatus
    strategy_config_version: str = DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION
    weight_version: str = DEFAULT_SELECTION_WEIGHT_VERSION
    candidate_scores_ref: str | None = None
    stable_top20_rule: Mapping[str, object] | None = None
    stage: str = "approving_candidate_cache"
    target: str = "candidate_cache"

    def __post_init__(self) -> None:
        _require_non_empty("schema_version", self.schema_version)
        _require_non_empty("selection_run_id", self.selection_run_id)
        _require_non_empty("stage", self.stage)
        _require_non_empty("target", self.target)
        _require_enum("market", self.market, SelectionMarket)
        _require_enum("profile", self.profile, SelectionProfile)
        _require_iso_date("trade_date", self.trade_date)
        if self.candidate_count < 1 or self.candidate_count > 20:
            raise ValueError("candidate_count must be in [1, 20]")
        if not self.source_lineage_refs:
            raise ValueError("source_lineage_refs must be non-empty")
        _require_sha256("cache_body_sha256", self.cache_body_sha256)
        _require_non_empty("strategy_config_ref", self.strategy_config_ref)
        _require_enum("readback_status", self.readback_status, CandidateCacheReadbackStatus)
        _require_non_empty("strategy_config_version", self.strategy_config_version)
        _require_non_empty("weight_version", self.weight_version)
        if self.candidate_scores_ref is not None:
            _require_non_empty("candidate_scores_ref", self.candidate_scores_ref)


@dataclass(frozen=True)
class SelectionWorkerDispatch:
    dispatch_id: str
    select_workflow_run_id: str
    worker_id: SelectionWorkerId
    stage: SelectionStage
    allowed_tools: tuple[str, ...]
    prompt_runtime_vars: Mapping[str, str]
    model_visible_materials: tuple[str, ...]
    evidence_dir: Path
    provider_payload_ref: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("dispatch_id", self.dispatch_id)
        _require_non_empty("select_workflow_run_id", self.select_workflow_run_id)
        _require_enum("worker_id", self.worker_id, SelectionWorkerId)
        _require_enum("stage", self.stage, SelectionStage)
        if self.stage not in DISPATCH_STAGES:
            raise ValueError("dispatch stage cannot be selection_report_handoff")
        missing_runtime_keys = [key for key in _DISPATCH_RUNTIME_VAR_KEYS if key not in self.prompt_runtime_vars]
        if missing_runtime_keys:
            raise ValueError(f"prompt_runtime_vars missing keys: {', '.join(sorted(missing_runtime_keys))}")
        if not self.model_visible_materials:
            raise ValueError("model_visible_materials must be non-empty")


@dataclass(frozen=True)
class SelectionWorkerArtifact:
    artifact_id: str
    worker_id: SelectionWorkerId
    stage: SelectionStage
    l1_text: str
    material_id: str
    l1_uri: str
    l1_sha256: str
    approval_status: str
    provider_payload_ref: str

    def __post_init__(self) -> None:
        _require_non_empty("artifact_id", self.artifact_id)
        _require_enum("worker_id", self.worker_id, SelectionWorkerId)
        _require_enum("stage", self.stage, SelectionStage)
        if self.stage not in DISPATCH_STAGES:
            raise ValueError("artifact stage cannot be selection_report_handoff")
        _require_non_empty("l1_text", self.l1_text)
        _require_non_empty("material_id", self.material_id)
        _require_non_empty("l1_uri", self.l1_uri)
        _require_sha256("l1_sha256", self.l1_sha256)
        if self.approval_status != "approved":
            raise ValueError("approval_status must be approved")
        _require_non_empty("provider_payload_ref", self.provider_payload_ref)


@dataclass(frozen=True)
class DecisionTicker:
    ticker: str
    company_name: str
    rationale_excerpt: str

    def __post_init__(self) -> None:
        _require_non_empty("ticker", self.ticker)
        _require_non_empty("company_name", self.company_name)
        _require_non_empty("rationale_excerpt", self.rationale_excerpt)


@dataclass(frozen=True)
class SelectionDecision:
    select_workflow_run_id: str
    enter_report: tuple[DecisionTicker, ...]
    watch: tuple[DecisionTicker, ...]
    reject: tuple[DecisionTicker, ...]
    report_questions: Mapping[str, tuple[str, ...]] | None
    source_summary: Mapping[str, str] | None
    approved_material_id: str

    def __post_init__(self) -> None:
        _require_non_empty("select_workflow_run_id", self.select_workflow_run_id)
        if len(self.enter_report) > 3:
            raise ValueError("enter_report supports 0-3 tickers")
        _require_non_empty("approved_material_id", self.approved_material_id)
        all_tickers = [item.ticker for item in (*self.enter_report, *self.watch, *self.reject)]
        if len(all_tickers) != len(set(all_tickers)):
            raise ValueError("decision tickers must be unique across enter/watch/reject")
        if self.report_questions is not None:
            for ticker, questions in self.report_questions.items():
                _require_non_empty("report_questions ticker", ticker)
                if not questions:
                    raise ValueError("report_questions values must be non-empty tuples")


@dataclass(frozen=True)
class SelectionConfirmation:
    confirmation_id: str
    idempotency_key: str
    report_handoff_dedupe_key: str
    select_workflow_run_id: str
    ticker: str
    status: SelectionConfirmationStatus
    report_task_id: str | None = None
    report_run_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("confirmation_id", self.confirmation_id)
        _require_non_empty("idempotency_key", self.idempotency_key)
        _require_non_empty("report_handoff_dedupe_key", self.report_handoff_dedupe_key)
        _require_non_empty("select_workflow_run_id", self.select_workflow_run_id)
        _require_non_empty("ticker", self.ticker)
        _require_enum("status", self.status, SelectionConfirmationStatus)


@dataclass(frozen=True)
class ReportHandoffRequest:
    confirmation_id: str
    ticker: str
    company_name: str
    market: SelectionMarket
    profile: SelectionProfile
    current_date: str
    selection_context_ref: str
    selection_stage_marker: SelectionStage
    report_request: RunRequest

    def __post_init__(self) -> None:
        _require_non_empty("confirmation_id", self.confirmation_id)
        _require_non_empty("ticker", self.ticker)
        _require_non_empty("company_name", self.company_name)
        _require_enum("market", self.market, SelectionMarket)
        _require_enum("profile", self.profile, SelectionProfile)
        _require_iso_date("current_date", self.current_date)
        _require_non_empty("selection_context_ref", self.selection_context_ref)
        _require_enum("selection_stage_marker", self.selection_stage_marker, SelectionStage)
        if self.selection_stage_marker != SelectionStage.SELECTION_REPORT_HANDOFF:
            raise ValueError("selection_stage_marker must be selection_report_handoff")
        if not isinstance(self.report_request, RunRequest):
            raise ValueError("report_request must be RunRequest")
        if self.report_request.entry_point != WorkflowEntryPoint.REPORT_COMMAND:
            raise ValueError("report_request.entry_point must be report_command")


@dataclass(frozen=True)
class ApprovedSelectionStrategyConfig:
    config_ref: str
    market: SelectionMarket
    profile: SelectionProfile
    effective_trade_date: str
    filter_set_ref: str
    strategy_set_ref: str
    scoring_weight_ref: str
    status: ApprovedSelectionStrategyStatus

    def __post_init__(self) -> None:
        _require_non_empty("config_ref", self.config_ref)
        _require_enum("market", self.market, SelectionMarket)
        _require_enum("profile", self.profile, SelectionProfile)
        _require_iso_date("effective_trade_date", self.effective_trade_date)
        _require_non_empty("filter_set_ref", self.filter_set_ref)
        _require_non_empty("strategy_set_ref", self.strategy_set_ref)
        _require_non_empty("scoring_weight_ref", self.scoring_weight_ref)
        _require_enum("status", self.status, ApprovedSelectionStrategyStatus)


@dataclass(frozen=True)
class SelectionDataRun:
    selection_run_id: str
    status: SelectionDataRunStatus
    lease_id: str | None = None
    universe_snapshot_ref: str | None = None
    normalized_refs: tuple[str, ...] = ()
    provider_attempt_refs: tuple[str, ...] = ()
    select_data_plan_ref: str | None = None
    warehouse_check_ref: str | None = None
    columnar_manifest_ref: str | None = None
    columnar_manifest_sha256: str | None = None
    feature_snapshot_ref: str | None = None
    candidate_cache_ref: CandidateCacheRef | None = None
    data_gaps: tuple[DataGapRef, ...] = ()
    progress_label: str | None = None
    progress_completed: int | None = None
    progress_total: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("selection_run_id", self.selection_run_id)
        _require_enum("status", self.status, SelectionDataRunStatus)
        if self.started_at is not None:
            _require_iso_timestamp("started_at", self.started_at)
        if self.updated_at is not None:
            _require_iso_timestamp("updated_at", self.updated_at)
        if self.completed_at is not None:
            _require_iso_timestamp("completed_at", self.completed_at)
        if self.failed_at is not None:
            _require_iso_timestamp("failed_at", self.failed_at)
        if self.columnar_manifest_sha256 is not None:
            _require_sha256("columnar_manifest_sha256", self.columnar_manifest_sha256)
        if self.progress_completed is not None and self.progress_completed < 0:
            raise ValueError("progress_completed must be non-negative")
        if self.progress_total is not None and self.progress_total < 0:
            raise ValueError("progress_total must be non-negative")
        if (
            self.progress_completed is not None
            and self.progress_total is not None
            and self.progress_completed > self.progress_total
        ):
            raise ValueError("progress_completed cannot exceed progress_total")
        if self.status == SelectionDataRunStatus.RUNNING and self.lease_id is None:
            raise ValueError("lease_id is required when status is running")
        if self.status == SelectionDataRunStatus.COMPLETED:
            if self.candidate_cache_ref is None:
                raise ValueError("candidate_cache_ref is required when status is completed")
            if self.completed_at is None:
                raise ValueError("completed_at is required when status is completed")
            if self.failure_code is not None or self.failure_reason is not None:
                raise ValueError("completed run cannot include failure fields")
        if self.status == SelectionDataRunStatus.NO_CANDIDATE:
            if self.candidate_cache_ref is not None:
                raise ValueError("no_candidate run cannot include candidate_cache_ref")
            if self.completed_at is None:
                raise ValueError("completed_at is required when status is no_candidate")
            if self.failure_code is not None or self.failure_reason is not None:
                raise ValueError("no_candidate run cannot include failure fields")
        if self.status == SelectionDataRunStatus.FAILED:
            if self.failed_at is None:
                raise ValueError("failed_at is required when status is failed")
            if not self.failure_code or not self.failure_reason:
                raise ValueError("failed status requires failure_code and failure_reason")


@dataclass(frozen=True)
class SelectionWorkflowRun:
    select_workflow_run_id: str
    selection_run_id: str
    status: SelectionWorkflowStatus
    request: SelectRequest
    candidate_cache_ref: CandidateCacheRef
    created_at: str
    updated_at: str
    dispatches: tuple[SelectionWorkerDispatch, ...] = ()
    worker_artifacts: tuple[SelectionWorkerArtifact, ...] = ()
    decision: SelectionDecision | None = None

    def __post_init__(self) -> None:
        _require_non_empty("select_workflow_run_id", self.select_workflow_run_id)
        _require_non_empty("selection_run_id", self.selection_run_id)
        _require_enum("status", self.status, SelectionWorkflowStatus)
        if self.request.entry_point != WorkflowEntryPoint.SELECT_COMMAND:
            raise ValueError("SelectionWorkflowRun requires select_command request")
        if self.selection_run_id != self.candidate_cache_ref.selection_run_id:
            raise ValueError("selection_run_id must match candidate_cache_ref.selection_run_id")
        _require_non_empty("created_at", self.created_at)
        _require_iso_timestamp("created_at", self.created_at)
        _require_non_empty("updated_at", self.updated_at)
        _require_iso_timestamp("updated_at", self.updated_at)
        for dispatch in self.dispatches:
            if dispatch.select_workflow_run_id != self.select_workflow_run_id:
                raise ValueError("dispatch.select_workflow_run_id must match workflow run id")
        if self.status in WORKFLOW_DECISION_REQUIRED_STATUSES and self.decision is None:
            raise ValueError("decision is required for completed/confirmation states")
        if self.decision is not None and self.decision.select_workflow_run_id != self.select_workflow_run_id:
            raise ValueError("decision.select_workflow_run_id must match workflow run id")


DATA_RUN_STATE_TRANSITIONS: dict[SelectionDataRunStatus, frozenset[SelectionDataRunStatus]] = {
    SelectionDataRunStatus.PLANNED: frozenset(
        {
            SelectionDataRunStatus.LEASE_PENDING,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.LEASE_PENDING: frozenset(
        {
            SelectionDataRunStatus.RUNNING,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.RUNNING: frozenset(
        {
            SelectionDataRunStatus.FETCHING_DATA,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.FETCHING_DATA: frozenset(
        {
            SelectionDataRunStatus.NORMALIZING_INPUTS,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.NORMALIZING_INPUTS: frozenset(
        {
            SelectionDataRunStatus.BUILDING_FEATURES,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.BUILDING_FEATURES: frozenset(
        {
            SelectionDataRunStatus.FILTERING_AND_SCORING,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.FILTERING_AND_SCORING: frozenset(
        {
            SelectionDataRunStatus.NO_CANDIDATE,
            SelectionDataRunStatus.BUILDING_CANDIDATE_CACHE,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.BUILDING_CANDIDATE_CACHE: frozenset(
        {
            SelectionDataRunStatus.NO_CANDIDATE,
            SelectionDataRunStatus.APPROVING_CANDIDATE_CACHE,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.APPROVING_CANDIDATE_CACHE: frozenset(
        {
            SelectionDataRunStatus.COMPLETED,
            SelectionDataRunStatus.FAILED,
        }
    ),
    SelectionDataRunStatus.NO_CANDIDATE: frozenset(),
    SelectionDataRunStatus.COMPLETED: frozenset(),
    SelectionDataRunStatus.FAILED: frozenset(),
}


WORKFLOW_STATE_TRANSITIONS: dict[SelectionWorkflowStatus, frozenset[SelectionWorkflowStatus]] = {
    SelectionWorkflowStatus.RECEIVED: frozenset(
        {
            SelectionWorkflowStatus.RESOLVING_REQUEST,
            SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED,
        }
    ),
    SelectionWorkflowStatus.RESOLVING_REQUEST: frozenset(
        {
            SelectionWorkflowStatus.LOADING_COMPLETED_SELECTION_RUN,
            SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED,
        }
    ),
    SelectionWorkflowStatus.LOADING_COMPLETED_SELECTION_RUN: frozenset(
        {
            SelectionWorkflowStatus.VALIDATING_CANDIDATE_CACHE,
            SelectionWorkflowStatus.NO_COMPLETED_SELECTION_RUN,
            SelectionWorkflowStatus.NO_CANDIDATE_SELECTION_RUN,
            SelectionWorkflowStatus.STALE_SELECTION_RUN,
            SelectionWorkflowStatus.SELECTION_WAREHOUSE_CHECK_MISSING,
            SelectionWorkflowStatus.SELECT_MARKET_UNSUPPORTED,
            SelectionWorkflowStatus.CRYPTO_SELECT_HISTORY_MISSING,
        }
    ),
    SelectionWorkflowStatus.VALIDATING_CANDIDATE_CACHE: frozenset(
        {
            SelectionWorkflowStatus.SELECT_RUN_CREATED,
            SelectionWorkflowStatus.CANDIDATE_CACHE_NOT_APPROVED,
            SelectionWorkflowStatus.CANDIDATE_CACHE_HASH_MISMATCH,
            SelectionWorkflowStatus.CANDIDATE_CACHE_INTEGRITY_FAILED,
            SelectionWorkflowStatus.CANDIDATE_CACHE_LINEAGE_INCOMPLETE,
        }
    ),
    SelectionWorkflowStatus.SELECT_RUN_CREATED: frozenset(
        {
            SelectionWorkflowStatus.STRATEGIST_RUNNING,
            SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION,
        }
    ),
    SelectionWorkflowStatus.STRATEGIST_RUNNING: frozenset(
        {
            SelectionWorkflowStatus.STRATEGIST_APPROVED,
            SelectionWorkflowStatus.WORKER_RUNTIME_FAILED,
            SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED,
        }
    ),
    SelectionWorkflowStatus.STRATEGIST_APPROVED: frozenset(
        {
            SelectionWorkflowStatus.SKEPTIC_RUNNING,
            SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION,
        }
    ),
    SelectionWorkflowStatus.SKEPTIC_RUNNING: frozenset(
        {
            SelectionWorkflowStatus.SKEPTIC_APPROVED,
            SelectionWorkflowStatus.WORKER_RUNTIME_FAILED,
            SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED,
        }
    ),
    SelectionWorkflowStatus.SKEPTIC_APPROVED: frozenset(
        {
            SelectionWorkflowStatus.MANAGER_RUNNING,
            SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION,
        }
    ),
    SelectionWorkflowStatus.MANAGER_RUNNING: frozenset(
        {
            SelectionWorkflowStatus.MANAGER_APPROVED,
            SelectionWorkflowStatus.WORKER_RUNTIME_FAILED,
            SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED,
        }
    ),
    SelectionWorkflowStatus.MANAGER_APPROVED: frozenset(
        {
            SelectionWorkflowStatus.PORTFOLIO_MANAGER_RUNNING,
            SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION,
        }
    ),
    SelectionWorkflowStatus.PORTFOLIO_MANAGER_RUNNING: frozenset(
        {
            SelectionWorkflowStatus.COMPLETED,
            SelectionWorkflowStatus.WORKER_RUNTIME_FAILED,
            SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED,
            SelectionWorkflowStatus.SELECTION_RESULT_INVALID,
        }
    ),
    SelectionWorkflowStatus.COMPLETED: frozenset(
        {
            SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION,
            SelectionWorkflowStatus.SELECTION_RESULT_INVALID,
        }
    ),
    SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION: frozenset(
        {
            SelectionWorkflowStatus.REPORT_HANDOFF_STARTED,
            SelectionWorkflowStatus.FAILED,
        }
    ),
    SelectionWorkflowStatus.REPORT_HANDOFF_STARTED: frozenset(),
    SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED: frozenset(),
    SelectionWorkflowStatus.NO_COMPLETED_SELECTION_RUN: frozenset(),
    SelectionWorkflowStatus.NO_CANDIDATE_SELECTION_RUN: frozenset(),
    SelectionWorkflowStatus.STALE_SELECTION_RUN: frozenset(),
    SelectionWorkflowStatus.CANDIDATE_CACHE_NOT_APPROVED: frozenset(),
    SelectionWorkflowStatus.CANDIDATE_CACHE_HASH_MISMATCH: frozenset(),
    SelectionWorkflowStatus.CANDIDATE_CACHE_INTEGRITY_FAILED: frozenset(),
    SelectionWorkflowStatus.CANDIDATE_CACHE_LINEAGE_INCOMPLETE: frozenset(),
    SelectionWorkflowStatus.SELECTION_WAREHOUSE_CHECK_MISSING: frozenset(),
    SelectionWorkflowStatus.SELECT_MARKET_UNSUPPORTED: frozenset(),
    SelectionWorkflowStatus.CRYPTO_SELECT_HISTORY_MISSING: frozenset(),
    SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION: frozenset(),
    SelectionWorkflowStatus.WORKER_RUNTIME_FAILED: frozenset(),
    SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED: frozenset(),
    SelectionWorkflowStatus.SELECTION_RESULT_INVALID: frozenset(),
    SelectionWorkflowStatus.FAILED: frozenset(),
}


def is_data_run_terminal(status: SelectionDataRunStatus) -> bool:
    return status in DATA_RUN_TERMINAL_STATUSES


def is_workflow_failure(status: SelectionWorkflowStatus) -> bool:
    return status in WORKFLOW_FAILURE_STATUSES


def is_workflow_terminal(status: SelectionWorkflowStatus) -> bool:
    return status in WORKFLOW_TERMINAL_STATUSES


def can_transition_data_run(
    from_status: SelectionDataRunStatus,
    to_status: SelectionDataRunStatus,
) -> bool:
    _require_enum("from_status", from_status, SelectionDataRunStatus)
    _require_enum("to_status", to_status, SelectionDataRunStatus)
    return to_status in DATA_RUN_STATE_TRANSITIONS[from_status]


def can_transition_workflow(
    from_status: SelectionWorkflowStatus,
    to_status: SelectionWorkflowStatus,
) -> bool:
    _require_enum("from_status", from_status, SelectionWorkflowStatus)
    _require_enum("to_status", to_status, SelectionWorkflowStatus)
    return to_status in WORKFLOW_STATE_TRANSITIONS[from_status]


def transition_data_run_status(
    from_status: SelectionDataRunStatus,
    to_status: SelectionDataRunStatus,
) -> SelectionDataRunStatus:
    if not can_transition_data_run(from_status=from_status, to_status=to_status):
        raise ValueError(f"invalid selection data run transition: {from_status.value} -> {to_status.value}")
    return to_status


def transition_workflow_status(
    from_status: SelectionWorkflowStatus,
    to_status: SelectionWorkflowStatus,
) -> SelectionWorkflowStatus:
    if not can_transition_workflow(from_status=from_status, to_status=to_status):
        raise ValueError(f"invalid selection workflow transition: {from_status.value} -> {to_status.value}")
    return to_status
