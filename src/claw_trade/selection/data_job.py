from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from claw_trade.data_gateway.select_plan import build_select_data_plan, select_data_plan_snapshot
from claw_trade.selection.artifacts import SelectionFileArtifactBackend
from claw_trade.selection.candidate_pack import (
    ApprovedCandidatePack,
    CandidatePackError,
    approve_candidate_pack,
    build_candidate_pack,
)
from claw_trade.selection.engine import (
    ApprovedSelectionStrategy,
    ScoringResult,
    SelectionEngineError,
    run_hard_filters,
    score_candidates,
)
from claw_trade.selection.features import (
    FeatureSnapshot,
    SelectionFeatureError,
    build_feature_snapshot,
    normalize_selection_inputs,
)
from claw_trade.selection.models import (
    CandidatePackManifest,
    DataGapRef,
    DataGapSeverity,
    SelectionBatchScope,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
)
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore
from claw_trade.selection.strategy_config import (
    CN_A_SELECTION_V1_STRATEGY_CONFIG_VERSION,
    CN_A_SELECTION_V1_WEIGHT_VERSION,
)


@dataclass(frozen=True)
class SelectionProviderBatchResult:
    provider_batch_plan: SelectionProviderBatchPlan
    attempt_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    data_gaps: tuple[DataGapRef, ...] = ()
    warehouse_check_ref: str | None = None


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


class SelectionDataJob:
    def __init__(
        self,
        *,
        store: SelectionRunStore,
        provider_fetch_batch: Callable[[SelectionRunPlan], SelectionProviderBatchResult],
        strategy_config_loader: Callable[[str], ApprovedSelectionStrategy | None],
        now_fn: Callable[[], datetime] | None = None,
        evidence_root: Path | None = None,
        candidate_pack_backend: SelectionFileArtifactBackend | None = None,
    ) -> None:
        self._store = store
        self._provider_fetch_batch = provider_fetch_batch
        self._strategy_config_loader = strategy_config_loader
        self._now_fn = now_fn or _utc_now
        self._evidence_root = evidence_root or Path("runs/selection")
        self._candidate_pack_backend = candidate_pack_backend or SelectionFileArtifactBackend(
            root=self._evidence_root / "artifacts"
        )

    def run(self, plan: SelectionRunPlan) -> SelectionDataJobExecution:
        provider_attempt_refs: tuple[str, ...] = ()
        normalized_refs: tuple[str, ...] = ()
        feature_snapshot: FeatureSnapshot | None = None
        scoring: ScoringResult | None = None
        strategy: ApprovedSelectionStrategy | None = None
        approved_pack: ApprovedCandidatePack | None = None
        select_data_plan: Any | None = None
        select_data_plan_ref: str | None = None
        all_data_gaps: list[DataGapRef] = []
        self._save_status(plan, status=SelectionDataRunStatus.LEASE_PENDING)
        lease_id = f"lease://{plan.selection_run_id}"
        self._save_status(plan, status=SelectionDataRunStatus.RUNNING, lease_id=lease_id)
        try:
            select_data_plan = build_select_data_plan(plan=plan, provider_result=None)
            select_data_plan_ref = select_data_plan.plan_id
            initial_blocker_gaps = _select_plan_blocker_gap_refs(select_data_plan)
            if initial_blocker_gaps and not any(check.should_call_provider for check in select_data_plan.warehouse_checks):
                raise SelectionDataJobStepError(
                    "selection_warehouse_unavailable",
                    "selection warehouse 缺失且没有批准的 provider 补取计划",
                    data_gaps=initial_blocker_gaps,
                )
            self._save_status(plan, status=SelectionDataRunStatus.FETCHING_DATA, lease_id=lease_id)
            provider_result = self._provider_fetch_batch(plan)
            self._validate_provider_result(plan, provider_result)
            provider_attempt_refs = provider_result.attempt_refs
            normalized_refs = provider_result.normalized_refs
            select_data_plan = build_select_data_plan(plan=plan, provider_result=provider_result)
            select_data_plan_ref = select_data_plan.plan_id
            warehouse_check_ref = provider_result.warehouse_check_ref
            all_data_gaps.extend(provider_result.data_gaps)

            self._save_status(plan, status=SelectionDataRunStatus.NORMALIZING_INPUTS, lease_id=lease_id)
            normalized_inputs = normalize_selection_inputs(
                plan=plan,
                raw_rows=provider_result.rows,
                normalized_refs=provider_result.normalized_refs,
                attempt_refs=provider_result.attempt_refs,
                upstream_gaps=provider_result.data_gaps,
            )

            self._save_status(plan, status=SelectionDataRunStatus.BUILDING_FEATURES, lease_id=lease_id)
            feature_snapshot = build_feature_snapshot(plan=plan, inputs=normalized_inputs)

            self._save_status(plan, status=SelectionDataRunStatus.FILTERING_AND_SCORING, lease_id=lease_id)
            strategy = self._load_approved_strategy(plan.approved_strategy_config_ref)
            _validate_strategy_field_coverage(plan=plan, snapshot=feature_snapshot, strategy=strategy)
            filtered = run_hard_filters(plan=plan, snapshot=feature_snapshot, strategy=strategy)
            scoring = score_candidates(plan=plan, filtered=filtered, strategy=strategy)

            self._save_status(plan, status=SelectionDataRunStatus.BUILDING_CANDIDATE_PACK, lease_id=lease_id)
            draft = build_candidate_pack(
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

            self._save_status(plan, status=SelectionDataRunStatus.APPROVING_CANDIDATE_PACK, lease_id=lease_id)
            approved_pack = approve_candidate_pack(
                plan=plan,
                draft=draft,
                artifact_backend=self._candidate_pack_backend,
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
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref,
                candidate_pack_ref=approved_pack.candidate_pack_ref,
                data_gaps=tuple(all_data_gaps),
                started_at=completed_at,
                completed_at=completed_at,
            )
            record = SelectionDataRunRecord(
                run_plan=plan,
                data_run=completed,
                manifest=approved_pack.manifest,
            )
            self._store.save_data_run_record(record)
            evidence_path = self._write_evidence(
                plan=plan,
                data_run=completed,
                manifest=approved_pack.manifest,
                provider_attempt_refs=provider_attempt_refs,
                normalized_refs=normalized_refs,
                select_data_plan=select_data_plan,
                feature_snapshot=feature_snapshot,
                scoring=scoring,
                strategy=strategy,
                data_gaps=tuple(all_data_gaps),
                verification_log_refs=approved_pack.verification_log_refs,
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
        except (SelectionFeatureError, SelectionEngineError, SelectionDataJobStepError, CandidatePackError) as raw_exc:
            exc = raw_exc
            if isinstance(raw_exc, CandidatePackError):
                exc = SelectionDataJobStepError(raw_exc.code, raw_exc.reason)
            all_data_gaps.extend(exc.data_gaps)
            provider_result_or_none = provider_result if "provider_result" in locals() else None
            if _is_no_candidate_outcome(raw_exc):
                completed_at = _isoformat(self._now_fn())
                no_candidate_run = SelectionDataRun(
                    selection_run_id=plan.selection_run_id,
                    status=SelectionDataRunStatus.NO_CANDIDATE,
                    lease_id=lease_id,
                    normalized_refs=normalized_refs,
                    provider_attempt_refs=provider_attempt_refs,
                    select_data_plan_ref=select_data_plan_ref,
                    warehouse_check_ref=provider_result_or_none.warehouse_check_ref
                    if provider_result_or_none is not None
                    else None,
                    feature_snapshot_ref=feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
                    data_gaps=tuple(all_data_gaps),
                    started_at=completed_at,
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
                    select_data_plan=build_select_data_plan(plan=plan, provider_result=provider_result_or_none)
                    if provider_result_or_none is not None
                    else None,
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
                warehouse_check_ref=provider_result_or_none.warehouse_check_ref
                if provider_result_or_none is not None
                else None,
                feature_snapshot_ref=feature_snapshot.feature_snapshot_ref if feature_snapshot else None,
                data_gaps=tuple(all_data_gaps),
                started_at=failed_at,
                failed_at=failed_at,
                failure_code=exc.code,
                failure_reason=exc.reason,
            )
            record = SelectionDataRunRecord(
                run_plan=plan,
                data_run=failed_run,
                manifest=approved_pack.manifest if approved_pack is not None else None,
            )
            self._store.save_data_run_record(record)
            evidence_path = self._write_evidence(
                plan=plan,
                data_run=failed_run,
                manifest=approved_pack.manifest if approved_pack is not None else None,
                provider_attempt_refs=provider_attempt_refs,
                normalized_refs=normalized_refs,
                select_data_plan=build_select_data_plan(plan=plan, provider_result=provider_result_or_none)
                if provider_result_or_none is not None
                else select_data_plan,
                feature_snapshot=feature_snapshot,
                scoring=scoring,
                strategy=strategy,
                data_gaps=tuple(all_data_gaps),
                verification_log_refs=approved_pack.verification_log_refs if approved_pack is not None else (),
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
    ) -> None:
        data_run = SelectionDataRun(
            selection_run_id=plan.selection_run_id,
            status=status,
            lease_id=lease_id,
            started_at=_isoformat(self._now_fn()) if status != SelectionDataRunStatus.LEASE_PENDING else None,
        )
        record = SelectionDataRunRecord(
            run_plan=plan,
            data_run=data_run,
            manifest=None,
        )
        self._store.save_data_run_record(record)

    def _validate_provider_result(self, plan: SelectionRunPlan, provider_result: SelectionProviderBatchResult) -> None:
        batch_plan = provider_result.provider_batch_plan
        if batch_plan.plan_id != plan.provider_batch_plan_ref:
            raise SelectionDataJobStepError(
                "provider_batch_plan_missing",
                "provider batch plan ref 与 run plan 不一致",
            )
        if batch_plan.scope != SelectionBatchScope.SELECTION_BATCH:
            raise SelectionDataJobStepError(
                "provider_batch_plan_missing",
                "provider batch plan scope 非 selection_batch",
            )
        if batch_plan.market != plan.market or batch_plan.profile != plan.profile:
            raise SelectionDataJobStepError(
                "provider_batch_plan_missing",
                "provider batch plan 市场或 profile 不匹配",
            )
        if batch_plan.trade_date != plan.trade_date:
            raise SelectionDataJobStepError(
                "provider_batch_plan_missing",
                "provider batch plan trade_date 不匹配",
            )
        if not provider_result.attempt_refs:
            raise SelectionDataJobStepError(
                "provider_evidence_failed",
                "provider attempts 缺失",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-provider-attempts-missing",
                        domain="selection",
                        gap_code="provider_attempts_missing",
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
                        reader_message="选股仓库检查缺少 openbb_normalized 引用，不能生成可用 /select run。",
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
        invalid_normalized_refs = tuple(
            ref for ref in provider_result.normalized_refs if not _is_openbb_normalized_ref(ref)
        )
        if invalid_normalized_refs:
            raise SelectionDataJobStepError(
                "selection_warehouse_check_missing",
                "selection warehouse normalized refs 未指向 openbb_normalized",
                data_gaps=(
                    DataGapRef(
                        gap_id=f"{plan.selection_run_id}-warehouse-normalized-refs-invalid",
                        domain="selection",
                        gap_code="selection_warehouse_normalized_refs_invalid",
                        severity=DataGapSeverity.BLOCKER,
                        attempt_refs=provider_result.attempt_refs,
                        reader_message="选股仓库检查发现 normalized refs 未指向 Mongo openbb_normalized，不能用本地/旧快路径结果满足 /select。",
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
        manifest: CandidatePackManifest | None,
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
            "provider_batch_plan_ref": plan.provider_batch_plan_ref,
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
            "trigger_source": plan.trigger_source.value,
            "supersedes_run_id": plan.supersedes_run_id,
            "status": data_run.status.value,
            "failure_code": failure_code,
            "started_at": data_run.started_at,
            "completed_at": data_run.completed_at,
            "failed_at": data_run.failed_at,
            "candidate_pack_stage": _candidate_pack_stage(data_run.status, manifest),
            "candidate_pack_ref": (
                {
                    "material_id": data_run.candidate_pack_ref.material_id,
                    "l1_uri": data_run.candidate_pack_ref.l1_uri,
                    "content_sha256": data_run.candidate_pack_ref.content_sha256,
                    "manifest_ref": data_run.candidate_pack_ref.manifest_ref,
                    "approved_at": data_run.candidate_pack_ref.approved_at,
                    "expires_at": data_run.candidate_pack_ref.expires_at,
                    "pack_summary_ref": data_run.candidate_pack_ref.pack_summary_ref,
                }
                if data_run.candidate_pack_ref is not None
                else None
            ),
            "candidate_pack_manifest": (
                {
                    "schema_version": manifest.schema_version,
                    "selection_run_id": manifest.selection_run_id,
                    "market": manifest.market.value,
                    "profile": manifest.profile.value,
                    "trade_date": manifest.trade_date,
                    "stage": manifest.stage,
                    "target": manifest.target,
                    "candidate_count": manifest.candidate_count,
                    "pack_body_sha256": manifest.pack_body_sha256,
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
            "candidate_pack_verification_log_refs": list(verification_log_refs),
            "provider_attempt_refs": list(provider_attempt_refs),
            "normalized_refs": list(normalized_refs),
            "select_data_plan_ref": data_run.select_data_plan_ref,
            "select_data_plan": select_data_plan_snapshot(select_data_plan)
            if select_data_plan is not None
            else None,
            "warehouse_check_ref": data_run.warehouse_check_ref,
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
    if isinstance(exc, CandidatePackError):
        return exc.code == "candidate_pack_top20_count_invalid"
    if not isinstance(exc, SelectionEngineError):
        return False
    return any(gap.gap_code in {"filtered_universe_empty", "scoring_rows_empty"} for gap in exc.data_gaps)


def _select_plan_blocker_gap_refs(select_data_plan: Any) -> tuple[DataGapRef, ...]:
    refs: list[DataGapRef] = []
    for check in select_data_plan.warehouse_checks:
        for gap in check.data_gaps:
            if gap.severity.value not in {"blocker", "fail"}:
                continue
            refs.append(
                DataGapRef(
                    gap_id=gap.gap_id,
                    domain=gap.domain.value,
                    gap_code=gap.reason.value,
                    severity=DataGapSeverity.BLOCKER,
                    attempt_refs=(select_data_plan.plan_id,),
                    reader_message=gap.worker_visible_text,
                    source_metadata={
                        "requirement_id": gap.requirement_id,
                        "market": gap.market.value if gap.market is not None else None,
                        "data_type": gap.data_type,
                        "field_path": gap.field_path,
                        "next_action": gap.next_action,
                    },
                )
            )
    return tuple(refs)


def _validate_strategy_field_coverage(
    *,
    plan: SelectionRunPlan,
    snapshot: FeatureSnapshot,
    strategy: ApprovedSelectionStrategy,
) -> None:
    missing = _missing_strategy_fields(snapshot=snapshot, strategy=strategy)
    if not missing:
        return
    raise SelectionDataJobStepError(
        "selection_strategy_fields_missing",
        "approved strategy 必需字段未闭合",
        data_gaps=tuple(
            DataGapRef(
                gap_id=f"{plan.selection_run_id}-strategy-field-missing-{field}",
                domain="selection",
                gap_code="selection_strategy_field_missing",
                severity=DataGapSeverity.BLOCKER,
                attempt_refs=(snapshot.feature_snapshot_ref,),
                reader_message=f"策略字段缺失：{field}。字段不足时不能生成 approved candidate pack。",
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
        ),
    )


def _missing_strategy_fields(
    *,
    snapshot: FeatureSnapshot,
    strategy: ApprovedSelectionStrategy,
) -> dict[str, tuple[str, ...]]:
    required: set[str] = set()
    for rule in strategy.strategy_set:
        required.update(field for field in rule.required_fields if field.strip())
    if not required:
        return {}
    missing: dict[str, tuple[str, ...]] = {}
    for row in snapshot.rows:
        for field in required:
            if row.feature_values.get(field) is None:
                missing.setdefault(field, ())
                missing[field] = (*missing[field], row.ticker)
    return {field: missing[field] for field in sorted(missing)}


def _candidate_pack_stage(status: SelectionDataRunStatus, manifest: CandidatePackManifest | None) -> str:
    if manifest is not None:
        return "approved"
    if status == SelectionDataRunStatus.NO_CANDIDATE:
        return "no_candidate"
    return "draft_only"


def _is_openbb_normalized_ref(ref: str) -> bool:
    return ref.startswith("normalized://mongo/openbb_normalized/") or ref.startswith("mongo://openbb_normalized/")


def _feature_snapshot_payload(feature_snapshot: FeatureSnapshot | None) -> list[dict[str, object]]:
    if feature_snapshot is None:
        return []
    return [
        {
            "ticker": row.ticker,
            "company_name": row.company_name,
            "industry": row.industry,
            "source_ref": row.source_ref,
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
    rows: list[dict[str, object]] = []
    for rule in strategy.strategy_set:
        hit_name = f"{rule.source}:{rule.name}" if rule.source else rule.name
        hit_rows = [row for row in scoring.all_scores if hit_name in row.strategy_hits]
        rows.append(
            {
                "source": rule.source,
                "variant_id": rule.name,
                "required_fields": list(rule.required_fields),
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


def _isoformat(value: datetime) -> str:
    utc = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc.isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
