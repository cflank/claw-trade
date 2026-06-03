from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claw_trade.selection.data_job import SelectionDataJob, SelectionProviderBatchResult, build_selection_data_plan
from claw_trade.selection.engine import ApprovedSelectionStrategy, StableTop20Rule
from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import SelectionRunStore
from claw_trade.selection.strategy_config import load_cn_a_selection_v1_strategy


def _plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel-run-03-success",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://cn-a-2026-05-26",
        approved_strategy_config_ref="config://cn-a-approved-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _provider_batch_plan(plan_id: str = "plan://cn-a-2026-05-26") -> SelectionProviderBatchPlan:
    return SelectionProviderBatchPlan(
        plan_id=plan_id,
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        coverage_groups=("universe", "daily", "fundamental"),
        provider_candidates=("akshare", "eastmoney"),
        ttl_policy_ref="ttl://daily",
        lineage_root_ref="lineage://selection/2026-05-26",
    )


def _approved_strategy() -> ApprovedSelectionStrategy:
    strategy = load_cn_a_selection_v1_strategy("config://cn-a-approved-v1")
    assert strategy is not None
    return strategy


def test_select_data_plan_crypto_history_missing_has_blocker_gap() -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-run-crypto-missing",
        market=SelectionMarket.CRYPTO,
        profile=SelectionProfile.CRYPTO,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="approved_crypto_universe",
        provider_batch_plan_ref="plan://crypto-2026-05-26",
        approved_strategy_config_ref="config://crypto-target-design",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )

    select_data_plan = build_selection_data_plan(plan=plan)

    assert select_data_plan.support_status.value == "target_design"
    assert select_data_plan.provider_call_specs == ()
    assert select_data_plan.data_gap_ids == ("sel-run-crypto-missing:select:mongo_missing",)
    [warehouse_check] = select_data_plan.warehouse_checks
    assert warehouse_check.status.value == "missing"
    assert warehouse_check.should_call_provider is False
    [gap] = warehouse_check.data_gaps
    assert gap.reason == "mongo_missing"
    assert "历史包尚未批准下载并入 Mongo" in gap.root_cause


def test_data_job_crypto_history_missing_fails_before_provider_fetch(tmp_path: Path) -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-run-crypto-job-missing",
        market=SelectionMarket.CRYPTO,
        profile=SelectionProfile.CRYPTO,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="approved_crypto_universe",
        provider_batch_plan_ref="plan://crypto-2026-05-26",
        approved_strategy_config_ref="config://crypto-target-design",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    provider_calls = {"count": 0}

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        provider_calls["count"] += 1
        raise AssertionError("Crypto history missing must fail before provider fetch")

    job = SelectionDataJob(
        store=SelectionRunStore(),
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: None,
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert provider_calls["count"] == 0
    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "selection_warehouse_unavailable"
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "selection_warehouse_unavailable"
    assert payload["select_data_plan"]["warehouse_checks"][0]["status"] == "missing"
    assert payload["select_data_plan"]["provider_call_specs"] == []
    assert payload["data_gaps"][0]["gap_code"] == "mongo_missing"


def _provider_result_success(plan: SelectionRunPlan) -> SelectionProviderBatchResult:
    rows = []
    normalized_refs: list[str] = []
    for idx in range(20):
        ticker = f"{600000 + idx:06d}.SH"
        ref = f"normalized://mongo/normalized_datasets/row-{idx + 1}"
        normalized_refs.append(ref)
        open_price = 10.0 + idx * 0.1
        close_price = open_price + 0.2
        rows.append(
            {
                "ticker": ticker,
                "company_name": f"样本股票{idx + 1}",
                "industry": "样本行业",
                "open": open_price,
                "close": close_price,
                "high": close_price + 0.1,
                "low": open_price - 0.1,
                "amount": 200000000.0 + idx * 10000000.0,
                "vol_ratio": 2.5 + idx * 0.1,
                **_complete_strategy_fields(idx=idx, open_price=open_price, close_price=close_price),
                "strategy_hit_coverage_score": 30.0 - idx * 0.1,
                "strategy_inner_strength_score": 25.0 - idx * 0.1,
                "rps_trend_score": 20.0 - idx * 0.1,
                "liquidity_tradability_score": 15.0 - idx * 0.1,
                "industry_theme_score": 5.0,
                "evidence_completeness_score": 5.0,
                "risk_penalty_score": 0.0,
                "data_gap_penalty_score": 0.0,
                "source_ref": ref,
            }
        )
    return SelectionProviderBatchResult(
        provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
        attempt_refs=("attempt://akshare-1", "attempt://eastmoney-1"),
        normalized_refs=tuple(normalized_refs),
        rows=tuple(rows),
        warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/success",
    )


@pytest.mark.integration
def test_data_job_pipeline_success_builds_feature_score_and_top20(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()
    provider_calls = {"count": 0}

    def provider_fetch(run_plan: SelectionRunPlan) -> SelectionProviderBatchResult:
        provider_calls["count"] += 1
        assert run_plan.selection_run_id == plan.selection_run_id
        return _provider_result_success(run_plan)

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda config_ref: _approved_strategy() if config_ref == plan.approved_strategy_config_ref else None,
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert provider_calls["count"] == 1
    assert result.record.data_run.status == SelectionDataRunStatus.COMPLETED
    assert result.record.data_run.failure_code is None
    assert len(result.top20_tickers) == 20
    assert result.top20_tickers[0] == "600000.SH"
    assert "600019.SH" in result.top20_tickers
    assert result.feature_snapshot_ref == "feature://sel-run-03-success"
    assert result.score_ref == "score://sel-run-03-success"
    assert result.record.manifest is not None
    assert result.record.manifest.stage == "approving_candidate_pack"
    assert result.record.manifest.target == "candidate_pack"
    assert result.record.manifest.readback_status.value == "verified"
    assert result.record.manifest.strategy_config_version == "cn_a.selection_strategy.v1"
    assert result.record.manifest.weight_version == "cn_a.selection_weights.v1"
    assert result.record.manifest.candidate_scores_ref == "score://sel-run-03-success"
    assert result.record.manifest.stable_top20_rule == {
        "primary": "score_desc",
        "tie_break": [
            "strategy_hit_count_desc",
            "liquidity_tradability_score_desc",
            "rps_trend_score_desc",
            "amount_desc",
            "data_gap_penalty_score_asc",
            "risk_penalty_score_asc",
        ],
    }
    assert result.record.data_run.candidate_pack_ref is not None
    assert result.record.data_run.candidate_pack_ref.material_id.startswith("selection-candidate-pack-sel-run-03-success-")
    assert result.evidence_path.exists()
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["provider_attempt_refs"] == ["attempt://akshare-1", "attempt://eastmoney-1"]
    assert payload["candidate_pack_stage"] == "approved"
    assert payload["candidate_pack_ref"]["material_id"].startswith("selection-candidate-pack-sel-run-03-success-")
    assert payload["candidate_pack_manifest"]["stage"] == "approving_candidate_pack"
    assert payload["candidate_pack_manifest"]["target"] == "candidate_pack"
    assert payload["candidate_pack_manifest"]["selection_run_id"] == "sel-run-03-success"
    assert payload["candidate_pack_manifest"]["readback_status"] == "verified"
    assert payload["candidate_pack_manifest"]["strategy_config_version"] == "cn_a.selection_strategy.v1"
    assert payload["candidate_pack_manifest"]["weight_version"] == "cn_a.selection_weights.v1"
    assert payload["candidate_pack_manifest"]["candidate_scores_ref"] == "score://sel-run-03-success"
    assert payload["candidate_pack_manifest"]["stable_top20_rule"]["primary"] == "score_desc"
    assert payload["normalized_refs"] == [f"normalized://mongo/normalized_datasets/row-{idx}" for idx in range(1, 21)]
    assert payload["warehouse_check_ref"] == "warehouse-check://selection/sel-run-03-success/2026-05-26/success"
    assert payload["select_data_plan"]["schema_version"] == "selection_data_plan.v1"
    assert payload["select_data_plan"]["select_data_plan"]["support_status"] == "supported"
    assert payload["select_data_plan"]["requirement_batch"]["request_kind"] == "select"
    [select_requirement] = payload["select_data_plan"]["requirement_batch"]["merged_requirements"]
    assert select_requirement["granularity"] == "daily"
    assert select_requirement["lookback_trading_days"] == 260
    assert select_requirement["source_role_required"] == "market_data"
    assert "strategy_signal_myhhub_volume_rise" in select_requirement["field_set"]
    assert "private_placement_days_since" in select_requirement["field_set"]
    assert "amount" in select_requirement["field_set"]
    assert payload["select_data_plan"]["warehouse_checks"][0]["status"] == "fresh"
    assert payload["select_data_plan"]["provider_call_specs"] == []
    assert payload["select_data_plan"]["store_contract"]["no_select_data_plans_collection"] is True
    assert payload["feature_snapshot_ref"] == "feature://sel-run-03-success"
    assert payload["score_ref"] == "score://sel-run-03-success"
    assert payload["strategy_config_version"] == "cn_a.selection_strategy.v1"
    assert payload["weight_version"] == "cn_a.selection_weights.v1"
    assert len(payload["strategy_variants"]) == 17
    assert payload["strategy_variants"][0]["variant_id"] == "myhhub_volume_rise"
    assert payload["strategy_variants"][0]["source"] == "myhhub/stock"
    assert payload["per_strategy_raw_hits"][0]["variant_id"] == "myhhub_volume_rise"
    assert payload["per_strategy_raw_hits"][0]["hit_count"] == 20
    assert payload["per_strategy_raw_hits"][0]["hits"][0]["hit_fields"]["amount"] == 200000000.0
    assert len(payload["top20"]) == 20
    assert payload["top20"][0]["ticker"] == "600000.SH"
    assert payload["top20"][0]["strategy_hit_count"] == 1.0
    assert payload["top20"][0]["strategy_hit_coverage_score"] == 30.0
    assert payload["top20"][0]["strategy_inner_strength_score"] == 25.0
    assert payload["top20"][0]["rps_trend_score"] == 20.0
    assert payload["top20"][0]["liquidity_tradability_score"] == 15.0
    assert payload["top20"][0]["industry_theme_score"] == 5.0
    assert payload["top20"][0]["evidence_completeness_score"] == 5.0
    assert payload["top20"][0]["risk_penalty_score"] == 0.0
    assert payload["top20"][0]["data_gap_penalty_score"] == 0.0
    assert payload["top20"][0]["tie_break_fields"]["strategy_hit_count"] == 1.0
    assert payload["top20"][0]["tie_break_fields"]["amount"] == 200000000.0
    assert "600019.SH" in {item["ticker"] for item in payload["top20"]}
    assert payload["data_gaps"] == []

    body_uri = result.record.data_run.candidate_pack_ref.l1_uri
    body_path = tmp_path / "artifacts" / body_uri.removeprefix("local://selection/")
    body_text = body_path.read_text(encoding="utf-8")
    assert "策略配置版本：cn_a.selection_strategy.v1" in body_text
    assert "权重版本：cn_a.selection_weights.v1" in body_text
    assert "排序 tie-break 字段" in body_text


@pytest.mark.integration
def test_data_job_pipeline_disables_private_placement_strategy_when_event_fields_missing(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        rows = []
        normalized_refs: list[str] = []
        for idx in range(20):
            ticker = f"{600100 + idx:06d}.SH"
            ref = f"normalized://mongo/normalized_datasets/private-missing-{idx + 1}"
            normalized_refs.append(ref)
            open_price = 10.0 + idx * 0.1
            close_price = open_price + 0.2
            strategy_fields = _complete_strategy_fields(idx=idx, open_price=open_price, close_price=close_price)
            strategy_fields.pop("private_placement_event_date")
            strategy_fields.pop("private_placement_days_since")
            rows.append(
                {
                    "ticker": ticker,
                    "company_name": f"定增缺口样本{idx + 1}",
                    "industry": "样本行业",
                    "open": open_price,
                    "close": close_price,
                    "high": close_price + 0.1,
                    "low": open_price - 0.1,
                    "amount": 200000000.0 + idx * 10000000.0,
                    "vol_ratio": 2.5 + idx * 0.1,
                    **strategy_fields,
                    "source_ref": ref,
                }
            )
        return SelectionProviderBatchResult(
            provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
            attempt_refs=("attempt://akshare-1",),
            normalized_refs=tuple(normalized_refs),
            rows=tuple(rows),
            warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/private-missing",
        )

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: _approved_strategy(),
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.COMPLETED
    assert result.record.data_run.failure_code is None
    assert len(result.top20_tickers) == 20
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["failure_code"] is None
    assert payload["candidate_pack_stage"] == "approved"
    assert len(payload["strategy_variants"]) == 16
    assert "sequoia_private_placement" not in {item["variant_id"] for item in payload["strategy_variants"]}
    assert payload["disabled_strategy_variants"] == [
        {
            "source": "Sequoia-X",
            "variant_id": "sequoia_private_placement",
            "reason": "strategy_specific_data_missing",
            "decision": "disabled_for_current_run",
            "missing_fields": ["private_placement_days_since", "private_placement_event_date"],
        }
    ]
    [gap] = [item for item in payload["data_gaps"] if item["gap_code"] == "selection_strategy_variant_disabled"]
    assert gap["severity"] == "warn"
    assert gap["source_metadata"]["not_interpreted_as_no_event"] is True
    assert payload["top20"][0]["strategy_missing_field_count"] == 0.0
    assert payload["top20"][0]["strategy_required_field_count"] > 0.0


@pytest.mark.integration
def test_data_job_pipeline_provider_failure_fails_closed_without_fallback(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()
    strategy_loader_calls = {"count": 0}

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        return SelectionProviderBatchResult(
            provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
            attempt_refs=(),
            normalized_refs=(),
            rows=(),
        )

    def strategy_loader(_: str) -> ApprovedSelectionStrategy | None:
        strategy_loader_calls["count"] += 1
        return _approved_strategy()

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=strategy_loader,
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "provider_evidence_failed"
    assert strategy_loader_calls["count"] == 0
    assert result.top20_tickers == ()
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failure_code"] == "provider_evidence_failed"
    assert payload["select_data_plan_ref"] == "select-data-plan://selection/sel-run-03-success/2026-05-26"
    assert payload["select_data_plan"]["warehouse_checks"][0]["status"] == "missing"
    provider_specs = payload["select_data_plan"]["provider_call_specs"]
    assert provider_specs == [
        {
            "provider_batch_plan_ref": plan.provider_batch_plan_ref,
            "scope": "selection_batch",
            "market": "CN_A",
            "profile": "CN_A",
            "coverage_group": "cn_a_selection_batch",
            "data_type": "cn_a_select_features",
            "params": {
                "lookback_trading_days": plan.lookback_trading_days,
                "universe_scope": plan.universe_scope,
            },
        }
    ]
    assert provider_specs[0]["coverage_group"] == "cn_a_selection_batch"
    assert provider_specs[0]["data_type"] == "cn_a_select_features"
    assert provider_specs[0]["params"]["lookback_trading_days"] == 260
    assert payload["top20"] == []
    assert payload["normalized_refs"] == []
    assert payload["data_gaps"][0]["gap_code"] == "provider_attempts_missing"


@pytest.mark.integration
def test_data_job_pipeline_missing_approved_strategy_config_fails_closed(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()
    provider_calls = {"count": 0}

    def provider_fetch(run_plan: SelectionRunPlan) -> SelectionProviderBatchResult:
        provider_calls["count"] += 1
        return _provider_result_success(run_plan)

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: None,
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert provider_calls["count"] == 1
    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "strategy_config_unapproved"
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failure_code"] == "strategy_config_unapproved"
    assert payload["top20"] == []
    assert payload["data_gaps"][0]["gap_code"] == "strategy_config_unapproved"


@pytest.mark.integration
def test_data_job_pipeline_accepts_candidate_count_less_than_20(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        rows = []
        refs: list[str] = []
        for idx in range(19):
            ticker = f"{300000 + idx:06d}.SZ"
            ref = f"normalized://mongo/normalized_datasets/insufficient-{idx + 1}"
            refs.append(ref)
            rows.append(
                {
                    "ticker": ticker,
                    "company_name": f"不足样本{idx + 1}",
                    "industry": "样本行业",
                    "open": 10.0 + idx * 0.1,
                    "close": 10.2 + idx * 0.1,
                    "high": 10.3 + idx * 0.1,
                    "low": 9.9 + idx * 0.1,
                    "amount": 200000000.0 + idx * 10000000.0,
                    "vol_ratio": 2.5 + idx * 0.05,
                    **_complete_strategy_fields(idx=idx, open_price=10.0 + idx * 0.1, close_price=10.2 + idx * 0.1),
                    "source_ref": ref,
                }
            )
        return SelectionProviderBatchResult(
            provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
            attempt_refs=("attempt://akshare-1",),
            normalized_refs=tuple(refs),
            rows=tuple(rows),
            warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/less-than-20",
        )

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: _approved_strategy(),
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.COMPLETED
    assert result.record.data_run.failure_code is None
    assert len(result.top20_tickers) == 19
    assert result.record.manifest is not None
    assert result.record.manifest.candidate_count == 19
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["failure_code"] is None
    assert payload["candidate_pack_manifest"]["candidate_count"] == 19
    assert len(payload["top20"]) == 19
    assert payload["data_gaps"] == []


@pytest.mark.integration
def test_data_job_pipeline_marks_no_candidate_without_approved_pack(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        rows = []
        refs: list[str] = []
        for idx in range(3):
            ticker = f"{300000 + idx:06d}.SZ"
            ref = f"normalized://mongo/normalized_datasets/no-candidate-{idx + 1}"
            refs.append(ref)
            rows.append(
                {
                    "ticker": ticker,
                    "company_name": f"无候选样本{idx + 1}",
                    "industry": "样本行业",
                    "open": 10.0 + idx * 0.1,
                    "close": 10.2 + idx * 0.1,
                    "high": 10.3 + idx * 0.1,
                    "low": 9.9 + idx * 0.1,
                    "amount": 100000000.0,
                    "vol_ratio": 2.5,
                    **_complete_strategy_fields(idx=idx, open_price=10.0 + idx * 0.1, close_price=10.2 + idx * 0.1),
                    "source_ref": ref,
                }
            )
        return SelectionProviderBatchResult(
            provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
            attempt_refs=("attempt://akshare-1",),
            normalized_refs=tuple(refs),
            rows=tuple(rows),
            warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/no-candidate",
        )

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: _approved_strategy(),
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.NO_CANDIDATE
    assert result.record.data_run.failure_code is None
    assert result.record.manifest is None
    assert result.top20_tickers == ()
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["status"] == "no_candidate"
    assert payload["failure_code"] is None
    assert payload["candidate_pack_stage"] == "no_candidate"
    assert payload["candidate_pack_manifest"] is None
    assert payload["top20"] == []
    assert payload["data_gaps"][0]["gap_code"] == "filtered_universe_empty"


@pytest.mark.integration
def test_data_job_pipeline_fails_when_stable_top20_tie_break_missing(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()

    def invalid_strategy_loader(_: str) -> ApprovedSelectionStrategy:
        strategy = _approved_strategy()
        invalid_rule = StableTop20Rule(
            score_field=strategy.stable_top20_rule.score_field,
            tie_break_fields=strategy.stable_top20_rule.tie_break_fields,
            missing_policy=strategy.stable_top20_rule.missing_policy,
        )
        object.__setattr__(invalid_rule, "tie_break_fields", ())
        object.__setattr__(strategy, "stable_top20_rule", invalid_rule)
        return strategy

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=_provider_result_success,
        strategy_config_loader=invalid_strategy_loader,
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "strategy_config_unapproved"
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["failure_code"] == "strategy_config_unapproved"
    assert payload["data_gaps"][0]["gap_code"] == "stable_top20_rule_tie_break_missing"


@pytest.mark.integration
def test_data_job_pipeline_fails_when_strategy_fields_are_missing(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        return SelectionProviderBatchResult(
            provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
            attempt_refs=("attempt://current-only",),
            normalized_refs=("normalized://mongo/normalized_datasets/current-only-1",),
            rows=(
                {
                    "ticker": "600999.SH",
                    "company_name": "当前快照样本",
                    "industry": "样本行业",
                    "open": 10.0,
                    "close": 10.3,
                    "high": 10.5,
                    "low": 9.9,
                    "amount": 300000000.0,
                    "vol_ratio": 2.5,
                    "source_ref": "normalized://mongo/normalized_datasets/current-only-1",
                },
            ),
            warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/strategy-fields-missing",
        )

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: _approved_strategy(),
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "selection_strategy_fields_missing"
    assert result.record.manifest is None
    assert result.top20_tickers == ()
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["candidate_pack_stage"] == "draft_only"
    assert payload["top20"] == []
    missing_fields = {gap["source_metadata"]["field"] for gap in payload["data_gaps"]}
    assert "ma30" in missing_fields
    assert "rps120" in missing_fields
    assert "private_placement_event_date" not in missing_fields
    assert "private_placement_days_since" not in missing_fields
    assert all(gap["gap_code"] == "selection_strategy_field_missing" for gap in payload["data_gaps"])


@pytest.mark.integration
def test_data_job_pipeline_rejects_duplicate_ticker_before_candidate_pack(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore()

    def provider_fetch(_: SelectionRunPlan) -> SelectionProviderBatchResult:
        return SelectionProviderBatchResult(
            provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
            attempt_refs=("attempt://duplicate",),
            normalized_refs=(
                "normalized://mongo/normalized_datasets/duplicate-1",
                "normalized://mongo/normalized_datasets/duplicate-2",
            ),
            rows=(
                {
                    "ticker": "600998.SH",
                    "company_name": "重复样本A",
                    "industry": "样本行业",
                    "open": 10.0,
                    "close": 10.3,
                    "high": 10.5,
                    "low": 9.9,
                    "amount": 300000000.0,
                    "source_ref": "normalized://mongo/normalized_datasets/duplicate-1",
                },
                {
                    "ticker": "600998.SH",
                    "company_name": "重复样本B",
                    "industry": "样本行业",
                    "open": 10.0,
                    "close": 10.3,
                    "high": 10.5,
                    "low": 9.9,
                    "amount": 300000000.0,
                    "source_ref": "normalized://mongo/normalized_datasets/duplicate-2",
                },
            ),
            warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/duplicate",
        )

    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=provider_fetch,
        strategy_config_loader=lambda _config_ref: _approved_strategy(),
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "selection_inputs_insufficient"
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["data_gaps"][0]["gap_code"] == "ticker_company_mismatch"


def _complete_strategy_fields(*, idx: int, open_price: float, close_price: float) -> dict[str, object]:
    return {
        "p_change_pct": 3.0,
        "ma30": 30.0 + idx,
        "ma30_slope_5d": -0.2,
        "ma30_slope_10d": -0.1,
        "ma30_growth_30d": 0.0,
        "limit_up_recent": 0.0,
        "post_limit_up_range_pct": 999.0,
        "post_limit_up_return_abs_pct": 999.0,
        "post_limit_up_window_days": 0.0,
        "ma250": 90.0,
        "ma250_backtrace_days": -1.0,
        "ma250_back_ratio": 999.0,
        "ma60": close_price + 1.0,
        "platform_deviation_pct": 999.0,
        "return_60d": 0.0,
        "single_day_min_return_60d": -6.0,
        "highest_close_60d": close_price + 1.0,
        "limit_up_count_20d": 0.0,
        "return_40d": 0.0,
        "limit_up_streak_2d": 0.0,
        "limit_down_today": 0.0,
        "atr_14": 20.0,
        "range_pct": 0.0,
        "prev_ma5": 21.0,
        "prev_ma20": 20.0,
        "ma5": 22.0,
        "ma20": 21.0,
        "volume_ma20": 100.0,
        "volume": 170.0,
        "high_20d": close_price + 1.0,
        "prev_close": close_price - 0.2,
        "range_10d": 10.0,
        "low_10d": 90.0,
        "high_40d": 100.0,
        "limit_up_yesterday": 0.0,
        "prev_volume": 100.0,
        "prev_ma60": 20.0,
        "rps120": 50.0,
        "roll_high_120d": close_price,
        "private_placement_event_date": "none",
        "private_placement_days_since": 9999.0,
    }
