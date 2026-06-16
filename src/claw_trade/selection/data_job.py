from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable, Mapping

from claw_trade.data_gateway.refs import (
    is_normalized_dataset_ref,
    normalize_legacy_normalized_dataset_ref,
)
from claw_trade.data_gateway.selection_integrity import validate_selection_columnar_manifest_ref
from claw_trade.selection.artifacts import SelectionFileArtifactBackend
from claw_trade.selection.candidate_cache import (
    ApprovedCandidateCache,
    CandidateCacheError,
    approve_candidate_cache,
    build_candidate_cache,
)
from claw_trade.selection.engine import (
    ApprovedSelectionStrategy,
    FilteredUniverse,
    ScoringResult,
    SelectionEngineError,
    StrategyRule,
    run_hard_filters,
    score_candidates,
)
from claw_trade.selection.features import (
    FeatureSnapshot,
    SelectionFeatureError,
    SelectionNormalizedInputs,
    build_feature_snapshot,
    normalize_selection_inputs,
)
from claw_trade.selection.models import (
    CandidateCacheManifest,
    DataGapRef,
    DataGapSeverity,
    SelectionBatchScope,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionDataNeedAudit,
    SelectionRunPlan,
)
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore
from claw_trade.selection.strategy_config import (
    CN_A_SELECTION_V1_STRATEGY_CONFIG_VERSION,
    CN_A_SELECTION_V1_WEIGHT_VERSION,
)


@dataclass(frozen=True)
class SelectionDataNeedResult:
    data_need_audit: SelectionDataNeedAudit
    attempt_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    data_gaps: tuple[DataGapRef, ...] = ()
    warehouse_check_ref: str | None = None
    columnar_manifest_ref: str | None = None
    columnar_manifest_sha256: str | None = None


@dataclass(frozen=True)
class SelectionDataFetchProgress:
    label: str
    completed: int
    total: int


@dataclass(frozen=True)
class SelectionDataJobExecution:
    record: SelectionDataRunRecord
    provider_attempt_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    feature_snapshot_ref: str | None
    score_ref: str | None
    top20_tickers: tuple[str, ...]
    evidence_path: Path


class SelectionDataJobStepError(ValueError):
    def __init__(self, code: str, reason: str, *, data_gaps: tuple[DataGapRef, ...] = ()) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.data_gaps = data_gaps


class _SelectionPlanSupportStatus(StrEnum):
    SUPPORTED = "supported"
    TARGET_DESIGN = "target_design"


class _SelectionWarehouseStatus(StrEnum):
    FRESH = "fresh"
    MISSING = "missing"


@dataclass(frozen=True)
class _SelectionDataPlanGap:
    gap_id: str
    requirement_id: str
    domain: str
    reason: str
    severity: DataGapSeverity
    worker_visible_text: str
    market: SelectionMarket
    data_type: str
    field_path: str | None
    next_action: str

    @property
    def root_cause(self) -> str:
        return self.worker_visible_text


@dataclass(frozen=True)
class _SelectionWarehouseCheck:
    check_id: str
    status: _SelectionWarehouseStatus
    should_call_provider: bool
    data_gaps: tuple[_SelectionDataPlanGap, ...] = ()


@dataclass(frozen=True)
class _SelectionDataPlan:
    plan_id: str
    support_status: _SelectionPlanSupportStatus
    requirement_batch: Mapping[str, object]
    warehouse_checks: tuple[_SelectionWarehouseCheck, ...]
    provider_call_specs: tuple[Mapping[str, object], ...]
    data_gap_ids: tuple[str, ...]
    store_contract: Mapping[str, object]


class SelectionDataJob:
    def __init__(
        self,
        *,
        store: SelectionRunStore,
        provider_fetch_batch: Callable[..., SelectionDataNeedResult],
        strategy_config_loader: Callable[[str], ApprovedSelectionStrategy | None],
        now_fn: Callable[[], datetime] | None = None,
        evidence_root: Path | None = None,
        candidate_cache_backend: SelectionFileArtifactBackend | None = None,
    ) -> None:
        self._store = store
        self._provider_fetch_batch = provider_fetch_batch
        self._strategy_config_loader = strategy_config_loader
        self._now_fn = now_fn or _utc_now
        self._evidence_root = evidence_root or Path("runs/selection")
        self._candidate_cache_backend = candidate_cache_backend or SelectionFileArtifactBackend(
            root=self._evidence_root / "artifacts"
        )

    def run(self, plan: SelectionRunPlan) -> SelectionDataJobExecution:
        provider_attempt_refs: tuple[str, ...] = ()
        normalized_refs: tuple[str, ...] = ()
        warehouse_check_ref: str | None = None
        columnar_manifest_ref: str | None = None
        columnar_manifest_sha256: str | None = None
        feature_snapshot: FeatureSnapshot | None = None
        scoring: ScoringResult | None = None
        strategy: ApprovedSelectionStrategy | None = None
        approved_cache: ApprovedCandidateCache | None = None
        select_data_plan: Any | None = None
        select_data_plan_ref: str | None = None
        all_data_gaps: list[DataGapRef] = []
        self._save_status(plan, status=SelectionDataRunStatus.LEASE_PENDING)
        lease_id = f"lease://{plan.selection_run_id}"
        self._save_status(plan, status=SelectionDataRunStatus.RUNNING, lease_id=lease_id)
        try:
            select_data_plan = build_selection_data_plan(plan=plan, provider_result=None)
            select_data_plan_ref = select_data_plan.plan_id
            initial_blocker_gaps = _select_plan_blocker_gap_refs(select_data_plan)
            if initial_blocker_gaps and not any(check.should_call_provider for check in select_data_plan.warehouse_checks):
                raise SelectionDataJobStepError(
                    "selection_warehouse_unavailable",
                    "selection warehouse 缺失且没有批准的 provider 补取计划",
                    data_gaps=initial_blocker_gaps,
                )
            self._save_status(plan, status=SelectionDataRunStatus.FETCHING_DATA, lease_id=lease_id)
            provider_result = self._call_provider_fetch_batch(
                plan,
                progress_callback=lambda progress: self._save_status(
                    plan,
                    status=SelectionDataRunStatus.FETCHING_DATA,
                    lease_id=lease_id,
                    progress_label=progress.label,
                    progress_completed=progress.completed,
                    progress_total=progress.total,
                ),
            )
            self._validate_provider_result(plan, provider_result)
            provider_attempt_refs = provider_result.attempt_refs
            normalized_refs = provider_result.normalized_refs
            select_data_plan = build_selection_data_plan(plan=plan, provider_result=provider_result)
            select_data_plan_ref = select_data_plan.plan_id
            warehouse_check_ref = provider_result.warehouse_check_ref
            columnar_manifest_ref = provider_result.columnar_manifest_ref
            columnar_manifest_sha256 = provider_result.columnar_manifest_sha256
            all_data_gaps.extend(provider_result.data_gaps)

            self._save_status(plan, status=SelectionDataRunStatus.NORMALIZING_INPUTS, lease_id=lease_id)
            normalized_inputs = normalize_selection_inputs(
                plan=plan,
                raw_rows=provider_result.rows,
                normalized_refs=provider_result.normalized_refs,
                attempt_refs=provider_result.attempt_refs,
                upstream_gaps=provider_result.data_gaps,
            )
            provider_result = None

            self._save_status(plan, status=SelectionDataRunStatus.BUILDING_FEATURES, lease_id=lease_id)
            feature_snapshot = build_feature_snapshot(plan=plan, inputs=normalized_inputs)

            self._save_status(plan, status=SelectionDataRunStatus.FILTERING_AND_SCORING, lease_id=lease_id)
            strategy = self._load_approved_strategy(plan.approved_strategy_config_ref)
            strategy, strategy_gap_refs = _strategy_with_available_optional_variants(
                plan=plan,
                snapshot=feature_snapshot,
                strategy=strategy,
            )
            all_data_gaps.extend(strategy_gap_refs)
            filtered = run_hard_filters(plan=plan, snapshot=feature_snapshot, strategy=strategy)
            scoring = score_candidates(plan=plan, filtered=filtered, strategy=strategy)
            feature_snapshot = _top20_feature_snapshot(feature_snapshot, scoring=scoring)
            normalized_inputs = SelectionNormalizedInputs(
                normalized_refs=normalized_refs,
                rows=(),
                data_gaps=normalized_inputs.data_gaps,
            )
            filtered = FilteredUniverse(rows=(), decisions=())

            self._save_status(plan, status=SelectionDataRunStatus.BUILDING_CANDIDATE_CACHE, lease_id=lease_id)
            draft = build_candidate_cache(
                plan=plan,
                inputs=normalized_inputs,
                filtered=filtered,
                scoring=scoring,
                provider_attempt_refs=provider_attempt_refs,
                data_gaps=tuple(all_data_gaps),
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref,
                score_ref=scoring.score_ref,
                stable_top20_rule=strategy.stable_top20_rule,
            )

            self._save_status(plan, status=SelectionDataRunStatus.APPROVING_CANDIDATE_CACHE, lease_id=lease_id)
            approved_cache = approve_candidate_cache(
                plan=plan,
                draft=draft,
                artifact_backend=self._candidate_cache_backend,
                now_fn=self._now_fn,
                candidate_scores_ref=scoring.score_ref,
                stable_top20_rule=strategy.stable_top20_rule,
            )

            completed_at = _isoformat(self._now_fn())
            completed = SelectionDataRun(
                selection_run_id=plan.selection_run_id,
                status=SelectionDataRunStatus.COMPLETED,
                lease_id=lease_id,
                normalized_refs=normalized_refs,
                provider_attempt_refs=provider_attempt_refs,
                select_data_plan_ref=select_data_plan_ref,
                warehouse_check_ref=warehouse_check_ref,
                columnar_manifest_ref=columnar_manifest_ref,
                columnar_manifest_sha256=columnar_manifest_sha256,
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref,
                candidate_cache_ref=approved_cache.candidate_cache_ref,
                data_gaps=tuple(all_data_gaps),
                started_at=self._started_at_for(plan, fallback=completed_at),
                completed_at=completed_at,
            )
            record = SelectionDataRunRecord(
                run_plan=plan,
                data_run=completed,
                manifest=approved_cache.manifest,
            )
            self._store.save_data_run_record(record)
            evidence_path = self._write_evidence(
                plan=plan,
                data_run=completed,
                manifest=approved_cache.manifest,
                provider_attempt_refs=provider_attempt_refs,
                normalized_refs=normalized_refs,
                select_data_plan=select_data_plan,
                feature_snapshot=feature_snapshot,
                scoring=scoring,
                strategy=strategy,
                data_gaps=tuple(all_data_gaps),
                verification_log_refs=approved_cache.verification_log_refs,
                failure_code=None,
            )
            return SelectionDataJobExecution(
                record=record,
                provider_attempt_refs=provider_attempt_refs,
                normalized_refs=normalized_refs,
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref,
                score_ref=scoring.score_ref,
                top20_tickers=tuple(item.ticker for item in scoring.top20),
                evidence_path=evidence_path,
            )
        except (SelectionFeatureError, SelectionEngineError, SelectionDataJobStepError, CandidateCacheError) as raw_exc:
            exc = raw_exc
            if isinstance(raw_exc, CandidateCacheError):
                exc = SelectionDataJobStepError(raw_exc.code, raw_exc.reason)
            all_data_gaps.extend(exc.data_gaps)
            if _is_no_candidate_outcome(raw_exc):
                completed_at = _isoformat(self._now_fn())
                no_candidate_run = SelectionDataRun(
                    selection_run_id=plan.selection_run_id,
                    status=SelectionDataRunStatus.NO_CANDIDATE,
                    lease_id=lease_id,
                    normalized_refs=normalized_refs,
                    provider_attempt_refs=provider_attempt_refs,
                    select_data_plan_ref=select_data_plan_ref,
                    warehouse_check_ref=warehouse_check_ref,
                    columnar_manifest_ref=columnar_manifest_ref,
                    columnar_manifest_sha256=columnar_manifest_sha256,
                    feature_snapshot_ref=feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
                    data_gaps=tuple(all_data_gaps),
                    started_at=self._started_at_for(plan, fallback=completed_at),
                    completed_at=completed_at,
                )
                record = SelectionDataRunRecord(
                    run_plan=plan,
                    data_run=no_candidate_run,
                    manifest=None,
                )
                self._store.save_data_run_record(record)
                evidence_path = self._write_evidence(
                    plan=plan,
                    data_run=no_candidate_run,
                    manifest=None,
                    provider_attempt_refs=provider_attempt_refs,
                    normalized_refs=normalized_refs,
                    select_data_plan=select_data_plan,
                    feature_snapshot=feature_snapshot,
                    scoring=scoring,
                    strategy=strategy,
                    data_gaps=tuple(all_data_gaps),
                    verification_log_refs=(),
                    failure_code=None,
                )
                return SelectionDataJobExecution(
                    record=record,
                    provider_attempt_refs=provider_attempt_refs,
                    normalized_refs=normalized_refs,
                    feature_snapshot_ref=feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
                    score_ref=scoring.score_ref if scoring else None,
                    top20_tickers=(),
                    evidence_path=evidence_path,
                )
            failed_at = _isoformat(self._now_fn())
            failed_run = SelectionDataRun(
                selection_run_id=plan.selection_run_id,
                status=SelectionDataRunStatus.FAILED,
                lease_id=lease_id,
                normalized_refs=normalized_refs,
                provider_attempt_refs=provider_attempt_refs,
                select_data_plan_ref=select_data_plan_ref,
                warehouse_check_ref=warehouse_check_ref,
                columnar_manifest_ref=columnar_manifest_ref,
                columnar_manifest_sha256=columnar_manifest_sha256,
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
                data_gaps=tuple(all_data_gaps),
                started_at=self._started_at_for(plan, fallback=failed_at),
                failed_at=failed_at,
                failure_code=exc.code,
                failure_reason=exc.reason,
            )
            record = SelectionDataRunRecord(
                run_plan=plan,
                data_run=failed_run,
                manifest=approved_cache.manifest if approved_cache is not None else None,
            )
            self._store.save_data_run_record(record)
            evidence_path = self._write_evidence(
                plan=plan,
                data_run=failed_run,
                manifest=approved_cache.manifest if approved_cache is not None else None,
                provider_attempt_refs=provider_attempt_refs,
                normalized_refs=normalized_refs,
                select_data_plan=select_data_plan,
                feature_snapshot=feature_snapshot,
                scoring=scoring,
                strategy=strategy,
                data_gaps=tuple(all_data_gaps),
                verification_log_refs=approved_cache.verification_log_refs if approved_cache is not None else (),
                failure_code=exc.code,
            )
            return SelectionDataJobExecution(
                record=record,
                provider_attempt_refs=provider_attempt_refs,
                normalized_refs=normalized_refs,
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
                score_ref=scoring.score_ref if scoring else None,
                top20_tickers=tuple(item.ticker for item in scoring.top20) if scoring else (),
                evidence_path=evidence_path,
            )

    def _save_status(
        self,
        plan: SelectionRunPlan,
        *,
        status: SelectionDataRunStatus,
        lease_id: str | None = None,
        progress_label: str | None = None,
        progress_completed: int | None = None,
        progress_total: int | None = None,
    ) -> None:
        existing = self._store.load_data_run_record(plan.selection_run_id)
        started_at = (
            existing.data_run.started_at
            if existing is not None and existing.data_run.started_at is not None
            else (_isoformat(self._now_fn()) if status != SelectionDataRunStatus.LEASE_PENDING else None)
        )
        data_run = SelectionDataRun(
            selection_run_id=plan.selection_run_id,
            status=status,
            lease_id=lease_id,
            started_at=started_at,
            progress_label=progress_label,
            progress_completed=progress_completed,
            progress_total=progress_total,
        )
        record = SelectionDataRunRecord(
            run_plan=plan,
            data_run=data_run,
            manifest=None,
        )
        self._store.save_data_run_record(record)

    def _call_provider_fetch_batch(
        self,
        plan: SelectionRunPlan,
        *,
        progress_callback: Callable[[SelectionDataFetchProgress], None],
    ) -> SelectionDataNeedResult:
        if _supports_progress_callback(self._provider_fetch_batch):
            return self._provider_fetch_batch(plan, progress_callback=progress_callback)
        return self._provider_fetch_batch(plan)

    def _started_at_for(self, plan: SelectionRunPlan, *, fallback: str) -> str:
        existing = self._store.load_data_run_record(plan.selection_run_id)
        if existing is not None and existing.data_run.started_at is not None:
            return existing.data_run.started_at
        return fallback

    def _validate_provider_result(self, plan: SelectionRunPlan, provider_result: SelectionDataNeedResult) -> None:
        batch_plan = provider_result.data_need_audit
        if batch_plan.plan_id != plan.data_need_audit_ref:
            raise SelectionDataJobStepError(
                "data_need_audit_missing",
                "data need audit ref 与 run plan 不一致",
            )
        if batch_plan.scope != SelectionBatchScope.SELECTION_BATCH:
            raise SelectionDataJobStepError(
                "data_need_audit_missing",
                "data need audit scope 非 selection_batch",
            )
        if batch_plan.market != plan.market or batch_plan.profile != plan.profile:
            raise SelectionDataJobStepError(
                "data_need_audit_missing",
                "data need audit 市场或 profile 不匹配",
            )
        if batch_plan.trade_date != plan.trade_date:
            raise SelectionDataJobStepError(
                "data_need_audit_missing",
                "data need audit trade_date 不匹配",
            )
        blocker_gaps = tuple(gap for gap in provider_result.data_gaps if gap.severity == DataGapSeverity.BLOCKER)
        if not provider_result.attempt_refs and blocker_gaps:
            first_gap = blocker_gaps[0]
            raise SelectionDataJobStepError(
                first_gap.gap_code,
                first_gap.reader_message,
                data_gaps=blocker_gaps,
            )
        if not provider_result.attempt_refs:
            raise SelectionDataJobStepError(
                "provider_evidence_failed",
                "provider attempts 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-provider-attempts-missing",
                        domain="selection",
                        gap_code="provider_attempt_refs_missing",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=("attempt://missing",),
                        reader_message="provider 调用缺少 attempts 证据，任务失败且不 fallback。",
                    ),
                ),
            )
        if not provider_result.normalized_refs:
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection warehouse normalized refs 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-warehouse-normalized-refs-missing",
                        domain="selection",
                        gap_code="selection_warehouse_normalized_refs_missing",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股仓库检查缺少标准化数据引用，不能生成可用 /select run。",
                    ),
                ),
            )
        if provider_result.warehouse_check_ref is None or not provider_result.warehouse_check_ref.strip():
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection warehouse_check_ref 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-warehouse-check-ref-missing",
                        domain="selection",
                        gap_code="selection_warehouse_check_ref_missing",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股仓库检查缺少 warehouse_check_ref，不能把本批次作为 /select 可用 run。",
                    ),
                ),
            )
        if provider_result.columnar_manifest_ref is None or not provider_result.columnar_manifest_ref.strip():
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection columnar manifest 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-columnar-manifest-missing",
                        domain="selection",
                        gap_code="selection_columnar_manifest_missing",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股列式仓库 manifest 缺失，不能把本批次作为 /select 可用 run。",
                    ),
                ),
            )
        if provider_result.columnar_manifest_sha256 is None or not provider_result.columnar_manifest_sha256.strip():
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection columnar manifest hash 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-columnar-manifest-hash-missing",
                        domain="selection",
                        gap_code="selection_columnar_manifest_hash_missing",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股列式仓库 manifest hash 缺失，不能把本批次作为 /select 可用 run。",
                    ),
                ),
            )
        if not validate_selection_columnar_manifest_ref(
            provider_result.columnar_manifest_ref,
            expected_sha256=provider_result.columnar_manifest_sha256,
        ):
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection columnar manifest 校验失败",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-columnar-manifest-invalid",
                        domain="selection",
                        gap_code="selection_columnar_manifest_invalid",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股列式仓库 manifest 或 Parquet 文件校验失败，不能把本批次作为 /select 可用 run。",
                    ),
                ),
            )
        invalid_normalized_refs = tuple(
            ref for ref in provider_result.normalized_refs if not _is_unified_normalized_ref(ref)
        )
        if invalid_normalized_refs:
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection warehouse normalized refs 不是数据层标准化引用",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-warehouse-normalized-refs-invalid",
                        domain="selection",
                        gap_code="selection_warehouse_normalized_refs_invalid",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股仓库检查发现 normalized refs 不是数据层标准化引用，不能用本地/旧快路径结果满足 /select。",
                        source_metadata={"invalid_refs": invalid_normalized_refs[:20]},
                    ),
                ),
            )

    def _load_approved_strategy(self, approved_strategy_config_ref: str) -> ApprovedSelectionStrategy:
        try:
            strategy = self._strategy_config_loader(approved_strategy_config_ref)
        except ValueError as exc:
            raise SelectionDataJobStepError(
                "strategy_config_unapproved",
                str(exc),
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{approved_strategy_config_ref}-invalid",
                        domain="selection",
                        gap_code="strategy_config_unapproved",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=("attempt://strategy-config",),
                        reader_message="approved strategy config 非法，按 fail closed 失败。",
                    ),
                ),
            ) from exc
        if strategy is None:
            raise SelectionDataJobStepError(
                "strategy_config_unapproved",
                "approved strategy config 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{approved_strategy_config_ref}-missing",
                        domain="selection",
                        gap_code="strategy_config_unapproved",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=("attempt://strategy-config",),
                        reader_message="缺少 approved strategy config/weights/stable_top20_rule，按 fail closed 失败。",
                    ),
                ),
            )
        if strategy.config_ref != approved_strategy_config_ref:
            raise SelectionDataJobStepError(
                "strategy_config_unapproved",
                "strategy config ref 不一致",
            )
        return strategy

    def _write_evidence(
        self,
        *,
        plan: SelectionRunPlan,
        data_run: SelectionDataRun,
        manifest: CandidateCacheManifest | None,
        provider_attempt_refs: tuple[str, ...],
        normalized_refs: tuple[str, ...],
        select_data_plan: Any | None,
        feature_snapshot: FeatureSnapshot | None,
        scoring: ScoringResult | None,
        strategy: ApprovedSelectionStrategy | None,
        data_gaps: tuple[DataGapRef, ...],
        verification_log_refs: tuple[str, ...],
        failure_code: str | None,
    ) -> Path:
        payload = {
            "selection_run_id": plan.selection_run_id,
            "market": plan.market.value,
            "profile": plan.profile.value,
            "trade_date": plan.trade_date,
            "lookback_trading_days": plan.lookback_trading_days,
            "universe_scope": plan.universe_scope,
            "data_need_audit_ref": plan.data_need_audit_ref,
            "approved_strategy_config_ref": plan.approved_strategy_config_ref,
            "strategy_config_version": CN_A_SELECTION_V1_STRATEGY_CONFIG_VERSION,
            "weight_version": CN_A_SELECTION_V1_WEIGHT_VERSION,
            "strategy_variants": [
                {
                    "variant_id": item.name,
                    "source": item.source,
                    "required_fields": list(item.required_fields),
                }
                for item in (strategy.strategy_set if strategy is not None else ())
            ],
            "disabled_strategy_variants": _disabled_strategy_variants(data_gaps),
            "trigger_source": plan.trigger_source.value,
            "supersedes_run_id": plan.supersedes_run_id,
            "status": data_run.status.value,
            "failure_code": failure_code,
            "started_at": data_run.started_at,
            "completed_at": data_run.completed_at,
            "failed_at": data_run.failed_at,
            "candidate_cache_stage": _candidate_cache_stage(data_run.status, manifest),
            "candidate_cache_ref": (
                {
                    "material_id": data_run.candidate_cache_ref.material_id,
                    "l1_uri": data_run.candidate_cache_ref.l1_uri,
                    "content_sha256": data_run.candidate_cache_ref.content_sha256,
                    "manifest_ref": data_run.candidate_cache_ref.manifest_ref,
                    "approved_at": data_run.candidate_cache_ref.approved_at,
                    "expires_at": data_run.candidate_cache_ref.expires_at,
                    "cache_summary_ref": data_run.candidate_cache_ref.cache_summary_ref,
                }
                if data_run.candidate_cache_ref is not None
                else None
            ),
            "candidate_cache_manifest": (
                {
                    "schema_version": manifest.schema_version,
                    "selection_run_id": manifest.selection_run_id,
                    "market": manifest.market.value,
                    "profile": manifest.profile.value,
                    "trade_date": manifest.trade_date,
                    "stage": manifest.stage,
                    "target": manifest.target,
                    "candidate_count": manifest.candidate_count,
                    "cache_body_sha256": manifest.cache_body_sha256,
                    "readback_status": manifest.readback_status.value,
                    "source_lineage_refs": list(manifest.source_lineage_refs),
                    "strategy_config_ref": manifest.strategy_config_ref,
                    "strategy_config_version": manifest.strategy_config_version,
                    "weight_version": manifest.weight_version,
                    "candidate_scores_ref": manifest.candidate_scores_ref,
                    "stable_top20_rule": dict(manifest.stable_top20_rule or {}),
                }
                if manifest is not None
                else None
            ),
            "candidate_cache_verification_log_refs": list(verification_log_refs),
            "provider_attempt_refs": list(provider_attempt_refs),
            "normalized_refs": list(normalized_refs),
            "select_data_plan_ref": data_run.select_data_plan_ref,
            "select_data_plan": selection_data_plan_snapshot(select_data_plan)
            if select_data_plan is not None
            else None,
            "warehouse_check_ref": data_run.warehouse_check_ref,
            "columnar_manifest_ref": data_run.columnar_manifest_ref,
            "columnar_manifest_sha256": data_run.columnar_manifest_sha256,
            "feature_snapshot_ref": feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
            "feature_snapshot": _feature_snapshot_payload(feature_snapshot),
            "score_ref": scoring.score_ref if scoring else None,
            "per_strategy_raw_hits": _per_strategy_raw_hits(strategy=strategy, scoring=scoring),
            "top20": [
                {
                    "ticker": item.ticker,
                    "company_name": item.company_name,
                    "score": item.score,
                    "strategy_hits": list(item.strategy_hits),
                    "strategy_hit_count": item.feature_values.get("strategy_hit_count"),
                    "strategy_variant_count": item.feature_values.get("strategy_variant_count"),
                    "strategy_missing_field_count": item.feature_values.get("strategy_missing_field_count"),
                    "strategy_required_field_count": item.feature_values.get("strategy_required_field_count"),
                    "strategy_hit_coverage_score": item.feature_values.get("strategy_hit_coverage_score"),
                    "strategy_inner_strength_score": item.feature_values.get("strategy_inner_strength_score"),
                    "rps_trend_score": item.feature_values.get("rps_trend_score"),
                    "liquidity_tradability_score": item.feature_values.get("liquidity_tradability_score"),
                    "industry_theme_score": item.feature_values.get("industry_theme_score"),
                    "evidence_completeness_score": item.feature_values.get("evidence_completeness_score"),
                    "risk_penalty_score": item.feature_values.get("risk_penalty_score"),
                    "data_gap_penalty_score": item.feature_values.get("data_gap_penalty_score"),
                    "tie_break_fields": {
                        "strategy_hit_count": item.feature_values.get("strategy_hit_count"),
                        "liquidity_tradability_score": item.feature_values.get("liquidity_tradability_score"),
                        "rps_trend_score": item.feature_values.get("rps_trend_score"),
                        "amount": item.feature_values.get("amount"),
                        "data_gap_penalty_score": item.feature_values.get("data_gap_penalty_score"),
                        "risk_penalty_score": item.feature_values.get("risk_penalty_score"),
                        "ticker": item.ticker,
                    },
                }
                for item in (scoring.top20 if scoring else ())
            ],
            "data_gaps": [
                {
                    "gap_id": gap.gap_id,
                    "gap_code": gap.gap_code,
                    "severity": gap.severity.value,
                    "attempt_refs": list(gap.attempt_refs),
                    "reader_message": gap.reader_message,
                    "source_metadata": dict(gap.source_metadata or {}),
                }
                for gap in data_gaps
            ],
        }
        day_dir = self._evidence_root / plan.trade_date
        day_dir.mkdir(parents=True, exist_ok=True)
        output = day_dir / f"{plan.selection_run_id}.json"
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output


def _is_no_candidate_outcome(exc: Exception) -> bool:
    if isinstance(exc, CandidateCacheError):
        return exc.code == "candidate_cache_top20_count_invalid"
    if not isinstance(exc, SelectionEngineError):
        return False
    return any(gap.gap_code in {"filtered_universe_empty", "scoring_rows_empty"} for gap in exc.data_gaps)


def build_selection_data_plan(
    *,
    plan: SelectionRunPlan,
    provider_result: SelectionDataNeedResult | None = None,
) -> _SelectionDataPlan:
    plan_id = f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}"
    requirement_id = f"{plan.selection_run_id}:selection:{plan.trade_date}"
    requirement_batch: Mapping[str, object] = {
        "request_kind": "select",
        "selection_run_id": plan.selection_run_id,
        "merged_requirements": (
            {
                "requirement_id": requirement_id,
                "market": plan.market.value,
                "profile": plan.profile.value,
                "trade_date": plan.trade_date,
                "lookback_trading_days": plan.lookback_trading_days,
                "universe_scope": plan.universe_scope,
                "granularity": "daily",
                "coverage_groups": ("universe", "daily", "fundamental"),
                "field_set": ("strategy_signal_myhhub_volume_rise", "private_placement_days_since", "amount"),
                "target_ref_type": "dataset://normalized",
            },
        ),
    }
    store_contract = {
        "no_select_data_plans_collection": True,
        "normalized_ref_type": "dataset://normalized",
    }
    if provider_result is not None:
        gaps = tuple(
            _plan_gap_from_data_gap(plan=plan, requirement_id=requirement_id, gap=gap)
            for gap in provider_result.data_gaps
        )
        has_warehouse_refs = bool(provider_result.normalized_refs) and bool(
            provider_result.warehouse_check_ref and provider_result.warehouse_check_ref.strip()
        )
        has_blocker_gaps = any(gap.severity == DataGapSeverity.BLOCKER for gap in gaps)
        status = _SelectionWarehouseStatus.FRESH if has_warehouse_refs and not has_blocker_gaps else _SelectionWarehouseStatus.MISSING
        return _SelectionDataPlan(
            plan_id=plan_id,
            support_status=_SelectionPlanSupportStatus.SUPPORTED,
            requirement_batch=requirement_batch,
            warehouse_checks=(
                _SelectionWarehouseCheck(
                    check_id=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}",
                    status=status,
                    should_call_provider=False,
                    data_gaps=gaps,
                ),
            ),
            provider_call_specs=()
            if status == _SelectionWarehouseStatus.FRESH
            else (
                {
                    "data_need_audit_ref": plan.data_need_audit_ref,
                    "scope": SelectionBatchScope.SELECTION_BATCH.value,
                    "market": plan.market.value,
                    "profile": plan.profile.value,
                    "coverage_group": "cn_a_selection_batch",
                    "data_type": "cn_a_select_features",
                    "params": {
                        "lookback_trading_days": plan.lookback_trading_days,
                        "universe_scope": plan.universe_scope,
                    },
                },
            ),
            data_gap_ids=tuple(gap.gap_id for gap in gaps),
            store_contract=store_contract,
        )

    if plan.market == SelectionMarket.CRYPTO:
        gap = _SelectionDataPlanGap(
            gap_id=f"{plan.selection_run_id}:select:mongo_missing",
            requirement_id=requirement_id,
            domain="selection",
            reason="mongo_missing",
            severity=DataGapSeverity.BLOCKER,
            worker_visible_text="CRYPTO /select 历史包尚未批准进入标准化数据层，不能走旧 select plan 或本地文件入口。",
            market=plan.market,
            data_type="selection_history",
            field_path=None,
            next_action="approve_crypto_selection_history_ingest",
        )
        return _SelectionDataPlan(
            plan_id=plan_id,
            support_status=_SelectionPlanSupportStatus.TARGET_DESIGN,
            requirement_batch=requirement_batch,
            warehouse_checks=(
                _SelectionWarehouseCheck(
                    check_id=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}",
                    status=_SelectionWarehouseStatus.MISSING,
                    should_call_provider=False,
                    data_gaps=(gap,),
                ),
            ),
            provider_call_specs=(),
            data_gap_ids=(gap.gap_id,),
            store_contract=store_contract,
        )

    return _SelectionDataPlan(
        plan_id=plan_id,
        support_status=_SelectionPlanSupportStatus.SUPPORTED,
        requirement_batch=requirement_batch,
        warehouse_checks=(
            _SelectionWarehouseCheck(
                check_id=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}",
                status=_SelectionWarehouseStatus.MISSING,
                should_call_provider=True,
                data_gaps=(),
            ),
        ),
        provider_call_specs=(
            {
                "data_need_audit_ref": plan.data_need_audit_ref,
                "scope": SelectionBatchScope.SELECTION_BATCH.value,
                "market": plan.market.value,
                "profile": plan.profile.value,
                "coverage_group": "cn_a_selection_batch",
                "data_type": "cn_a_select_features",
                "params": {
                    "lookback_trading_days": plan.lookback_trading_days,
                    "universe_scope": plan.universe_scope,
                },
            },
        ),
        data_gap_ids=(),
        store_contract=store_contract,
    )


def selection_data_plan_snapshot(select_data_plan: _SelectionDataPlan) -> Mapping[str, object]:
    return {
        "schema_version": "selection_data_plan.v1",
        "plan_id": select_data_plan.plan_id,
        "select_data_plan": {
            "support_status": select_data_plan.support_status,
            "data_gap_ids": list(select_data_plan.data_gap_ids),
        },
        "requirement_batch": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in select_data_plan.requirement_batch.items()
        },
        "warehouse_checks": [
            {
                "check_id": check.check_id,
                "status": check.status.value,
                "should_call_provider": check.should_call_provider,
                "data_gaps": [_selection_plan_gap_snapshot(gap) for gap in check.data_gaps],
            }
            for check in select_data_plan.warehouse_checks
        ],
        "provider_call_specs": [dict(item) for item in select_data_plan.provider_call_specs],
        "data_gap_ids": list(select_data_plan.data_gap_ids),
        "store_contract": dict(select_data_plan.store_contract),
    }


def _plan_gap_from_data_gap(
    *,
    plan: SelectionRunPlan,
    requirement_id: str,
    gap: DataGapRef,
) -> _SelectionDataPlanGap:
    return _SelectionDataPlanGap(
        gap_id=gap.gap_id,
        requirement_id=requirement_id,
        domain=gap.domain,
        reason=gap.gap_code,
        severity=gap.severity,
        worker_visible_text=gap.reader_message,
        market=plan.market,
        data_type=str(gap.source_metadata.get("data_type", "selection_history")) if gap.source_metadata else "selection_history",
        field_path=str(gap.source_metadata.get("field")) if gap.source_metadata and gap.source_metadata.get("field") else None,
        next_action=str(gap.source_metadata.get("next_action", "inspect_data_gap")) if gap.source_metadata else "inspect_data_gap",
    )


def _selection_plan_gap_snapshot(gap: _SelectionDataPlanGap) -> Mapping[str, object]:
    return {
        "gap_id": gap.gap_id,
        "requirement_id": gap.requirement_id,
        "domain": gap.domain,
        "reason": gap.reason,
        "severity": gap.severity.value,
        "worker_visible_text": gap.worker_visible_text,
        "market": gap.market.value,
        "data_type": gap.data_type,
        "field_path": gap.field_path,
        "next_action": gap.next_action,
    }


def _select_plan_blocker_gap_refs(select_data_plan: Any) -> tuple[DataGapRef, ...]:
    refs: list[DataGapRef] = []
    for check in select_data_plan.warehouse_checks:
        for gap in check.data_gaps:
            if gap.severity != DataGapSeverity.BLOCKER:
                continue
            refs.append(
                DataGapRef(
                    gap_id=gap.gap_id,
                    domain=gap.domain,
                    gap_code=gap.reason,
                    severity=DataGapSeverity.BLOCKER,
                    attempt_refs=(select_data_plan.plan_id,),
                    reader_message=gap.worker_visible_text,
                    source_metadata={
                        "requirement_id": gap.requirement_id,
                        "market": gap.market.value,
                        "data_type": gap.data_type,
                        "field_path": gap.field_path,
                        "next_action": gap.next_action,
                    },
                )
            )
    return tuple(refs)


_OPTIONAL_STRATEGY_VARIANTS = frozenset({("Sequoia-X", "sequoia_private_placement")})


def _strategy_with_available_optional_variants(
    *,
    plan: SelectionRunPlan,
    snapshot: FeatureSnapshot,
    strategy: ApprovedSelectionStrategy,
) -> tuple[ApprovedSelectionStrategy, tuple[DataGapRef, ...]]:
    missing_by_rule = _missing_strategy_fields_by_rule(snapshot=snapshot, strategy=strategy)
    if not missing_by_rule:
        return strategy, ()

    blocker_missing: dict[str, tuple[str, ...]] = {}
    disabled_rules: list[StrategyRule] = []
    warning_gaps: list[DataGapRef] = []
    for rule in strategy.strategy_set:
        missing = missing_by_rule.get((rule.source, rule.name))
        if not missing:
            continue
        if _is_optional_strategy_rule(rule):
            disabled_rules.append(rule)
            warning_gaps.append(
                _optional_strategy_variant_gap(
                    plan=plan,
                    snapshot=snapshot,
                    rule=rule,
                    missing=missing,
                )
            )
            continue
        for field, tickers in missing.items():
            blocker_missing[field] = _dedup_tickers((*blocker_missing.get(field, ()), *tickers))

    if blocker_missing:
        raise SelectionDataJobStepError(
            "selection_strategy_fields_missing",
            "approved strategy 必需字段未闭合",
            data_gaps=_strategy_field_blocker_gaps(
                plan=plan,
                snapshot=snapshot,
                strategy=strategy,
                missing=blocker_missing,
            ),
        )

    if not disabled_rules:
        return strategy, ()
    disabled_keys = {(rule.source, rule.name) for rule in disabled_rules}
    active_rules = tuple(
        rule
        for rule in strategy.strategy_set
        if (rule.source, rule.name) not in disabled_keys
    )
    if not active_rules:
        raise SelectionDataJobStepError(
            "selection_strategy_fields_missing",
            "本轮没有可参与评分的策略变体",
            data_gaps=(
                DataGapRef(
                    gap_id=f"{plan.selection_run_id}-strategy-variants-unavailable",
                    domain="selection",
                    gap_code="selection_strategy_variants_unavailable",
                    severity=DataGapSeverity.BLOCKER,
                    attempt_refs=(snapshot.feature_snapshot_ref,),
                    reader_message="全部策略变体缺少本轮必需字段，不能生成 candidate cache。",
                ),
            ),
        )
    return replace(strategy, strategy_set=active_rules), tuple(warning_gaps)


def _strategy_field_blocker_gaps(
    *,
    plan: SelectionRunPlan,
    snapshot: FeatureSnapshot,
    strategy: ApprovedSelectionStrategy,
    missing: Mapping[str, tuple[str, ...]],
) -> tuple[DataGapRef, ...]:
    return tuple(
        DataGapRef(
            gap_id=f"{plan.selection_run_id}-strategy-field-missing-{field}",
            domain="selection",
            gap_code="selection_strategy_field_missing",
            severity=DataGapSeverity.BLOCKER,
            attempt_refs=(snapshot.feature_snapshot_ref,),
            reader_message=f"策略字段缺失：{field}。字段不足时不能生成 approved candidate cache。",
            source_metadata={
                "field": field,
                "missing_ticker_count": len(tickers),
                "missing_tickers_sample": list(tickers[:20]),
                "strategy_variants": [
                    rule.name
                    for rule in strategy.strategy_set
                    if field in rule.required_fields
                ],
            },
        )
        for field, tickers in missing.items()
    )


def _optional_strategy_variant_gap(
    *,
    plan: SelectionRunPlan,
    snapshot: FeatureSnapshot,
    rule: StrategyRule,
    missing: Mapping[str, tuple[str, ...]],
) -> DataGapRef:
    missing_fields = tuple(sorted(missing))
    return DataGapRef(
        gap_id=f"{plan.selection_run_id}-strategy-variant-disabled-{rule.name}",
        domain="selection",
        gap_code="selection_strategy_variant_disabled",
        severity=DataGapSeverity.WARN,
        attempt_refs=(snapshot.feature_snapshot_ref,),
        reader_message=(
            f"策略 {rule.source}:{rule.name} 缺少本轮专用数据字段，"
            "本次禁用该策略，其它策略继续。"
        ),
        source_metadata={
            "source": rule.source,
            "variant_id": rule.name,
            "decision": "disabled_for_current_run",
            "reason": "strategy_specific_data_missing",
            "missing_fields": missing_fields,
            "not_interpreted_as_no_event": True,
            "missing_by_field": {
                field: {
                    "missing_ticker_count": len(tickers),
                    "missing_tickers_sample": list(tickers[:20]),
                }
                for field, tickers in missing.items()
            },
        },
    )


def _is_optional_strategy_rule(rule: StrategyRule) -> bool:
    return (rule.source, rule.name) in _OPTIONAL_STRATEGY_VARIANTS


def _missing_strategy_fields_by_rule(
    *,
    snapshot: FeatureSnapshot,
    strategy: ApprovedSelectionStrategy,
) -> dict[tuple[str, str], dict[str, tuple[str, ...]]]:
    missing: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}
    for rule in strategy.strategy_set:
        required = tuple(field for field in rule.required_fields if field.strip())
        if not required:
            continue
        rule_key = (rule.source, rule.name)
        for row in snapshot.rows:
            for field in required:
                if row.feature_values.get(field) is None:
                    missing.setdefault(rule_key, {})
                    missing[rule_key].setdefault(field, ())
                    missing[rule_key][field] = (*missing[rule_key][field], row.ticker)
    return {
        rule_key: {field: _dedup_tickers(tickers) for field, tickers in sorted(field_missing.items())}
        for rule_key, field_missing in missing.items()
    }


def _dedup_tickers(tickers: tuple[str, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for ticker in tickers:
        if ticker not in seen:
            seen[ticker] = None
    return tuple(seen)


def _candidate_cache_stage(status: SelectionDataRunStatus, manifest: CandidateCacheManifest | None) -> str:
    if manifest is not None:
        return "approved"
    if status == SelectionDataRunStatus.NO_CANDIDATE:
        return "no_candidate"
    return "draft_only"


def _is_unified_normalized_ref(ref: str) -> bool:
    return is_normalized_dataset_ref(ref)


def _top20_feature_snapshot(feature_snapshot: FeatureSnapshot, *, scoring: ScoringResult) -> FeatureSnapshot:
    top20_tickers = {row.ticker for row in scoring.top20}
    if not top20_tickers:
        return FeatureSnapshot(feature_snapshot_ref=feature_snapshot.feature_snapshot_ref, rows=())
    top20_by_ticker = {row.ticker: row for row in feature_snapshot.rows if row.ticker in top20_tickers}
    return FeatureSnapshot(
        feature_snapshot_ref=feature_snapshot.feature_snapshot_ref,
        rows=tuple(top20_by_ticker[row.ticker] for row in scoring.top20 if row.ticker in top20_by_ticker),
    )


def _feature_snapshot_payload(feature_snapshot: FeatureSnapshot | None) -> list[dict[str, object]]:
    if feature_snapshot is None:
        return []
    return [
        {
            "ticker": row.ticker,
            "company_name": row.company_name,
            "industry": row.industry,
            "source_ref": normalize_legacy_normalized_dataset_ref(row.source_ref),
            "feature_values": dict(row.feature_values),
        }
        for row in feature_snapshot.rows
    ]


def _per_strategy_raw_hits(
    *,
    strategy: ApprovedSelectionStrategy | None,
    scoring: ScoringResult | None,
) -> list[dict[str, object]]:
    if strategy is None or scoring is None:
        return []
    scored_rows = scoring.all_scores or scoring.top20
    rows: list[dict[str, object]] = []
    for rule in strategy.strategy_set:
        hit_name = f"{rule.source}:{rule.name}" if rule.source else rule.name
        hit_rows = [row for row in scored_rows if hit_name in row.strategy_hits]
        rows.append(
            {
                "source": rule.source,
                "variant_id": rule.name,
                "required_fields": list(rule.required_fields),
                "hit_count_basis": "all_scores" if scoring.all_scores else "top20",
                "hit_count": len(hit_rows),
                "hits": [
                    {
                        "ticker": row.ticker,
                        "company_name": row.company_name,
                        "hit_fields": {field: row.feature_values.get(field) for field in rule.required_fields},
                    }
                    for row in hit_rows
                ],
            }
        )
    return rows


def _disabled_strategy_variants(data_gaps: tuple[DataGapRef, ...]) -> list[dict[str, object]]:
    variants: list[dict[str, object]] = []
    for gap in data_gaps:
        if gap.gap_code != "selection_strategy_variant_disabled":
            continue
        metadata = dict(gap.source_metadata or {})
        variants.append(
            {
                "source": metadata.get("source"),
                "variant_id": metadata.get("variant_id"),
                "reason": metadata.get("reason"),
                "decision": metadata.get("decision"),
                "missing_fields": list(metadata.get("missing_fields", ())),
            }
        )
    return variants


def _isoformat(value: datetime) -> str:
    utc = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc.isoformat().replace("+00:00", "Z")


def _supports_progress_callback(callback: Callable[..., object]) -> bool:
    try:
        callback_signature = signature(callback)
    except (TypeError, ValueError):
        return False
    return "progress_callback" in callback_signature.parameters or any(
        parameter.kind == Parameter.VAR_KEYWORD for parameter in callback_signature.parameters.values()
    )


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
