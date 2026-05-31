from __future__ import annotations

import json
from dataclasses import replace

import pytest
from claw_trade.selection.candidate_pack import (
    CandidatePackError,
    build_candidate_pack,
    validate_candidate_pack_contract,
)
from claw_trade.selection.engine import (
    CandidateScoreRow,
    FilterDecision,
    FilteredUniverse,
    ScoringResult,
)
from claw_trade.selection.features import (
    FeatureRow,
    SelectionNormalizedInputs,
    SelectionNormalizedRow,
)
from claw_trade.selection.models import (
    DataGapRef,
    DataGapSeverity,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)


def _plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel04-contract-run",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=120,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://cn-a-2026-05-26",
        approved_strategy_config_ref="config://cn-a-approved-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _normalized_inputs(count: int = 20) -> SelectionNormalizedInputs:
    rows = []
    refs = []
    for idx in range(count):
        ticker = f"{600000 + idx:06d}.SH"
        ref = f"normalized://row-{idx + 1}"
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


def _scoring_result(count: int = 20) -> ScoringResult:
    all_rows = []
    for idx in range(count):
        ticker = f"{600000 + idx:06d}.SH"
        all_rows.append(
            CandidateScoreRow(
                ticker=ticker,
                company_name=f"样本股票{idx + 1}",
                industry="样本行业",
                score=float(100 - idx),
                strategy_hits=("myhhub/stock:myhhub_volume_rise",),
                feature_values={
                    "score": float(100 - idx),
                    "strategy_hit_coverage_score": 30.0,
                    "strategy_strength_score": 20.0,
                    "rps_trend_score": 18.0,
                    "liquidity_score": 12.0,
                    "industry_theme_strength_score": 3.0,
                    "evidence_completeness_score": 5.0,
                    "risk_penalty": 1.0,
                    "data_gap_penalty": 2.0,
                    "strategy_missing_field_count": 0.0,
                    "strategy_required_field_count": 64.0,
                    "hit_volume_ratio": 2.3,
                    "amount": float(100000000 + idx),
                },
            )
        )
    rows = tuple(all_rows)
    return ScoringResult(
        score_ref="score://sel04-contract-run",
        all_scores=rows,
        top20=rows,
    )


def test_build_candidate_pack_contract_produces_fact_only_model_visible_pack() -> None:
    plan = _plan()
    inputs = _normalized_inputs()
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result()

    draft = build_candidate_pack(
        plan=plan,
        inputs=inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://akshare-1",),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-contract-run",
        score_ref="score://sel04-contract-run",
    )

    assert len(draft.summary.candidates) == 20
    assert "标准化股票 20 行" in draft.summary.source_summary
    assert "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 | 数据质量 | 读者化来源摘要 |" in draft.summary.summary_md
    assert "策略配置版本：cn_a.selection_strategy.v1" in draft.summary.summary_md
    assert "权重版本：cn_a.selection_weights.v1" in draft.summary.summary_md
    assert "myhhub/stock" in draft.summary.summary_md
    assert "myhhub_volume_rise" in draft.summary.summary_md
    assert "成交额" in draft.summary.summary_md
    assert "命中量比" in draft.summary.summary_md
    assert "hit_volume_ratio" not in draft.summary.summary_md
    assert "amount" not in draft.summary.summary_md
    assert "strategy_hit_coverage_score" not in draft.summary.summary_md
    assert "risk_penalty_score" not in draft.summary.summary_md
    assert "data_gap_penalty_score" not in draft.summary.summary_md
    assert "risk_penalty" in draft.body_json
    assert "data_gap_penalty" in draft.body_json
    assert "strategy_hit_coverage_score" in draft.body_json
    assert "amount" in draft.body_json
    payload = json.loads(draft.body_json)
    first = payload["candidates"][0]
    assert first["total_score"] == 100.0
    assert first["component_scores"]["strategy_hit_coverage_score"] == 30.0
    assert first["strategy_sources"] == ["myhhub/stock"]
    assert first["strategy_variants"] == ["myhhub_volume_rise"]
    assert first["hit_fields"]["hit_volume_ratio"] == 2.3
    assert first["actual_metric_values"]["amount"] == 100000000.0
    assert first["risk_penalty"] == 1.0
    assert first["data_gap_penalty"] == 2.0
    assert first["strategy_config_version"] == "cn_a.selection_strategy.v1"
    assert first["weight_version"] == "cn_a.selection_weights.v1"
    assert "买入" not in draft.summary.summary_md
    assert "建议" not in draft.summary.summary_md
    assert "manifest" not in draft.summary.summary_md.lower()
    assert "sha256" not in draft.summary.summary_md.lower()
    assert "lineage" not in draft.summary.summary_md.lower()
    assert any(ref.startswith("attempt://") for ref in draft.source_lineage_refs)
    assert any(ref.startswith("normalized://") for ref in draft.source_lineage_refs)
    assert any(ref.startswith("feature://") for ref in draft.source_lineage_refs)
    assert any(ref.startswith("score://") for ref in draft.source_lineage_refs)


def test_build_candidate_pack_accepts_mongo_openbb_normalized_refs() -> None:
    plan = _plan()
    inputs = _normalized_inputs(count=1)
    mongo_ref = "mongo://openbb_normalized/sha256:abc123"
    mongo_inputs = replace(
        inputs,
        normalized_refs=(mongo_ref,),
        rows=tuple(replace(row, source_ref=mongo_ref) for row in inputs.rows),
    )
    filtered = _filtered_universe(mongo_inputs)
    scoring = _scoring_result(count=1)

    draft = build_candidate_pack(
        plan=plan,
        inputs=mongo_inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://mongo-warehouse-1",),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-contract-run",
        score_ref="score://sel04-contract-run",
    )

    assert mongo_ref in draft.source_lineage_refs


def test_build_candidate_pack_rejects_blocker_data_gap() -> None:
    plan = _plan()
    inputs = _normalized_inputs()
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result()

    with pytest.raises(CandidatePackError) as exc_info:
        build_candidate_pack(
            plan=plan,
            inputs=inputs,
            filtered=filtered,
            scoring=scoring,
            provider_attempt_refs=("attempt://local-baostock-1",),
            data_gaps=(
                DataGapRef(
                    gap_id="sel04-local-private-placement-missing",
                    domain="selection",
                    gap_code="selection_private_placement_source_missing",
                    severity=DataGapSeverity.BLOCKER,
                    attempt_refs=("attempt://local-baostock-1",),
                    reader_message="私募/定增事件源缺失。",
                ),
            ),
            feature_snapshot_ref="feature://sel04-contract-run",
            score_ref="score://sel04-contract-run",
        )

    assert exc_info.value.code == "candidate_pack_data_gap_blocker"


def test_build_candidate_pack_accepts_count_less_than_20() -> None:
    plan = _plan()
    inputs = _normalized_inputs(count=19)
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result(count=19)

    draft = build_candidate_pack(
        plan=plan,
        inputs=inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://akshare-1",),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-contract-run",
        score_ref="score://sel04-contract-run",
    )

    assert len(draft.summary.candidates) == 19


def test_build_candidate_pack_accepts_count_equal_to_one() -> None:
    plan = _plan()
    inputs = _normalized_inputs(count=1)
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result(count=1)

    draft = build_candidate_pack(
        plan=plan,
        inputs=inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://akshare-1",),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-contract-run",
        score_ref="score://sel04-contract-run",
    )

    assert len(draft.summary.candidates) == 1


def test_build_candidate_pack_rejects_when_candidate_count_exceeds_20() -> None:
    plan = _plan()
    inputs = _normalized_inputs(count=21)
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result(count=21)

    with pytest.raises(CandidatePackError, match="candidate_pack_top_limit_exceeded"):
        build_candidate_pack(
            plan=plan,
            inputs=inputs,
            filtered=filtered,
            scoring=scoring,
            provider_attempt_refs=("attempt://akshare-1",),
            data_gaps=(),
            feature_snapshot_ref="feature://sel04-contract-run",
            score_ref="score://sel04-contract-run",
        )


def test_build_candidate_pack_rejects_when_candidate_count_is_zero() -> None:
    plan = _plan()
    inputs = _normalized_inputs(count=0)
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result(count=0)

    with pytest.raises(CandidatePackError, match="candidate_pack_top20_count_invalid"):
        build_candidate_pack(
            plan=plan,
            inputs=inputs,
            filtered=filtered,
            scoring=scoring,
            provider_attempt_refs=("attempt://akshare-1",),
            data_gaps=(),
            feature_snapshot_ref="feature://sel04-contract-run",
            score_ref="score://sel04-contract-run",
        )


def test_validate_candidate_pack_contract_rejects_subjective_language() -> None:
    plan = _plan()
    inputs = _normalized_inputs()
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result()
    draft = build_candidate_pack(
        plan=plan,
        inputs=inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://akshare-1",),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-contract-run",
        score_ref="score://sel04-contract-run",
    )
    first = draft.summary.candidates[0]
    bad = replace(
        draft,
        summary=replace(
            draft.summary,
            candidates=(replace(first, source_summary="建议买入"), *draft.summary.candidates[1:]),
        ),
    )
    with pytest.raises(CandidatePackError, match="candidate_pack_subjective_language_forbidden"):
        validate_candidate_pack_contract(plan=plan, draft=bad)


def test_validate_candidate_pack_contract_rejects_protocol_terms_in_json_body() -> None:
    plan = _plan()
    inputs = _normalized_inputs()
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result()
    draft = build_candidate_pack(
        plan=plan,
        inputs=inputs,
        filtered=filtered,
        scoring=scoring,
        provider_attempt_refs=("attempt://akshare-1",),
        data_gaps=(),
        feature_snapshot_ref="feature://sel04-contract-run",
        score_ref="score://sel04-contract-run",
    )
    bad = replace(draft, body_json='{"debug":"manifest sha256"}')
    with pytest.raises(CandidatePackError, match="candidate_pack_model_boundary_forbidden"):
        validate_candidate_pack_contract(plan=plan, draft=bad)


def test_build_candidate_pack_rejects_lineage_missing_attempts() -> None:
    plan = _plan()
    inputs = _normalized_inputs()
    filtered = _filtered_universe(inputs)
    scoring = _scoring_result()

    with pytest.raises(CandidatePackError, match="candidate_pack_lineage_incomplete"):
        build_candidate_pack(
            plan=plan,
            inputs=inputs,
            filtered=filtered,
            scoring=scoring,
            provider_attempt_refs=(),
            data_gaps=(),
            feature_snapshot_ref="feature://sel04-contract-run",
            score_ref="score://sel04-contract-run",
        )
