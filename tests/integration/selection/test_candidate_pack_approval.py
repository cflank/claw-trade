from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from claw_trade.selection.columnar_warehouse import SelectionColumnarWarehouse
from claw_trade.selection.artifacts import SelectionFileArtifactBackend
from claw_trade.selection.candidate_pack import approve_candidate_pack, build_candidate_pack
from claw_trade.selection.engine import (
    CandidateScoreRow,
    FilterDecision,
    FilteredUniverse,
    ScoringResult,
    StableSortField,
    StableTop20Rule,
)
from claw_trade.selection.features import (
    FeatureRow,
    SelectionNormalizedInputs,
    SelectionNormalizedRow,
)
from claw_trade.selection.models import (
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import (
    SelectionDataRunRecord,
    SelectionRunStore,
    resolve_latest_completed_selection_run,
)


def _plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel04-approval-run",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=120,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://cn-a-2026-05-26",
        approved_strategy_config_ref="config://cn-a-approved-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _normalized_inputs() -> SelectionNormalizedInputs:
    rows = []
    refs = []
    for idx in range(20):
        ticker = f"{600000 + idx:06d}.SH"
        ref = f"normalized://mongo/normalized_datasets/row-{idx + 1}"
        refs.append(ref)
        rows.append(
            SelectionNormalizedRow(
                ticker=ticker,
                company_name=f"样本股票{idx + 1}",
                industry="样本行业",
                numeric_fields={"score": float(100 - idx), "amount": float(100000000 + idx)},
                source_ref=ref,
            )
        )
    return SelectionNormalizedInputs(
        normalized_refs=tuple(refs),
        rows=tuple(rows),
        data_gaps=(),
    )


def _filtered_universe(inputs: SelectionNormalizedInputs) -> FilteredUniverse:
    rows = tuple(
        FeatureRow(
            ticker=row.ticker,
            company_name=row.company_name,
            industry=row.industry,
            feature_values={"score": float(100 - idx), "amount": float(100000000 + idx)},
            source_ref=row.source_ref,
        )
        for idx, row in enumerate(inputs.rows)
    )
    decisions = tuple(
        FilterDecision(
            ticker=row.ticker,
            passed=True,
            failed_rules=(),
        )
        for row in inputs.rows
    )
    return FilteredUniverse(rows=rows, decisions=decisions)


def _scoring_result() -> ScoringResult:
    rows = tuple(
        CandidateScoreRow(
            ticker=f"{600000 + idx:06d}.SH",
            company_name=f"样本股票{idx + 1}",
            industry="样本行业",
            score=float(100 - idx),
            strategy_hits=("volume_breakout",),
            feature_values={
                "score": float(100 - idx),
                "amount": float(100000000 + idx),
                "strategy_missing_field_count": 0.0,
                "strategy_required_field_count": 64.0,
            },
        )
        for idx in range(20)
    )
    return ScoringResult(
        score_ref="score://sel04-approval-run",
        all_scores=rows,
        top20=rows,
    )


def _stable_top20_rule() -> StableTop20Rule:
    return StableTop20Rule(
        score_field="score",
        tie_break_fields=(StableSortField(field="amount", descending=True),),
        missing_policy="fail",
    )


@pytest.mark.integration
def test_candidate_pack_approval_uses_real_write_readback_hash_manifest_lineage(tmp_path: Path) -> None:
    plan = _plan()
    inputs = _normalized_inputs()
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result()
    draft = build_candidate_pack(
        plan=plan,
        inputs=inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://akshare-1", "attempt://eastmoney-1"),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-approval-run",
        score_ref="score://sel04-approval-run",
        stable_top20_rule=_stable_top20_rule(),
    )
    backend = SelectionFileArtifactBackend(root=tmp_path / "artifacts")

    approved = approve_candidate_pack(
        plan=plan,
        draft=draft,
        artifact_backend=backend,
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        candidate_scores_ref=scoring.score_ref,
        stable_top20_rule=_stable_top20_rule(),
    )

    ref = approved.candidate_pack_ref
    manifest = approved.manifest
    body_path = backend.uri_to_path(ref.l1_uri)
    manifest_path = backend.uri_to_path(ref.manifest_ref)
    summary_path = backend.uri_to_path(ref.pack_summary_ref)

    assert body_path.exists()
    assert summary_path.exists()
    assert manifest_path.exists()
    body_bytes = body_path.read_bytes()
    assert sha256(body_bytes).hexdigest() == ref.content_sha256
    assert manifest.pack_body_sha256 == ref.content_sha256
    assert manifest.candidate_count == 20
    assert manifest.readback_status.value == "verified"
    assert manifest.strategy_config_version == "cn_a.selection_strategy.v1"
    assert manifest.weight_version == "cn_a.selection_weights.v1"
    assert manifest.candidate_scores_ref == "score://sel04-approval-run"
    assert manifest.stable_top20_rule == {"primary": "score_desc", "tie_break": ["amount_desc"]}
    assert manifest.stage == "approving_candidate_pack"
    assert manifest.target == "candidate_pack"
    assert any(item.startswith("attempt://") for item in manifest.source_lineage_refs)
    assert any(item.startswith("normalized://") for item in manifest.source_lineage_refs)
    assert any(item.startswith("feature://") for item in manifest.source_lineage_refs)
    assert any(item.startswith("score://") for item in manifest.source_lineage_refs)

    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_payload["stage"] == "approving_candidate_pack"
    assert manifest_payload["target"] == "candidate_pack"
    assert manifest_payload["readback_status"] == "verified"
    assert manifest_payload["candidate_count"] == 20
    assert manifest_payload["pack_body_sha256"] == ref.content_sha256
    assert manifest_payload["strategy_config_version"] == "cn_a.selection_strategy.v1"
    assert manifest_payload["weight_version"] == "cn_a.selection_weights.v1"
    assert manifest_payload["candidate_scores_ref"] == "score://sel04-approval-run"
    assert manifest_payload["stable_top20_rule"] == {"primary": "score_desc", "tie_break": ["amount_desc"]}

    assert len(approved.verification_log_refs) == 4
    for log_ref in approved.verification_log_refs:
        log_path = backend.uri_to_path(log_ref)
        assert log_path.exists()
        payload = json.loads(log_path.read_text(encoding="utf-8"))
        assert payload["status"] == "verified"

    os.environ["CLAW_TRADE_SELECTION_COLUMNAR_ROOT"] = str(tmp_path / "columnar")
    columnar_writer = SelectionColumnarWarehouse(root=tmp_path / "columnar").begin_write(plan=plan)
    columnar_writer.add_daily_rows(
        (
                {
                    "ticker": inputs.rows[0].ticker,
                    "date": plan.trade_date,
                    "close": inputs.rows[0].numeric_fields.get("close", 10.0),
                    "source_ref": inputs.rows[0].source_ref,
                },
        )
    )
    columnar_writer.add_feature_rows(
        (
            {
                "ticker": inputs.rows[0].ticker,
                "company_name": inputs.rows[0].company_name,
                "source_ref": inputs.rows[0].source_ref,
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
                "amount": inputs.rows[0].numeric_fields["amount"],
            },
        )
    )
    columnar_manifest = columnar_writer.commit(
        provider_attempt_refs=("attempt://akshare-1", "attempt://eastmoney-1"),
        normalized_refs=inputs.normalized_refs,
    )
    store = SelectionRunStore(artifact_root=tmp_path / "artifacts")
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=plan,
            data_run=SelectionDataRun(
                selection_run_id=plan.selection_run_id,
                status=SelectionDataRunStatus.COMPLETED,
                normalized_refs=inputs.normalized_refs,
                provider_attempt_refs=("attempt://akshare-1", "attempt://eastmoney-1"),
                select_data_plan_ref=f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",
                warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/ok",
                columnar_manifest_ref=columnar_manifest.manifest_ref,
                columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(
                    columnar_manifest.manifest_ref
                ),
                candidate_pack_ref=ref,
                completed_at="2026-05-26T09:00:00+00:00",
            ),
            manifest=manifest,
        )
    )
    latest = resolve_latest_completed_selection_run(
        store=store,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=plan.trade_date,
        now_fn=lambda: datetime(2026, 5, 26, 9, 1, tzinfo=UTC),
    )
    assert latest.is_available is True
    assert latest.run is not None
    assert latest.run.manifest.readback_status.value == "verified"
