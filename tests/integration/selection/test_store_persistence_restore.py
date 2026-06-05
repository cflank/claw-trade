from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claw_trade.selection.columnar_warehouse import SelectionColumnarWarehouse
from claw_trade.selection.data_job import SelectionDataJob, SelectionProviderBatchResult
from claw_trade.selection.engine import ApprovedSelectionStrategy
from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import (
    SelectionDataRunRecord,
    SelectionRunStore,
    SelectUnavailableCode,
    resolve_latest_completed_selection_run,
    restore_selection_run_store,
)
from claw_trade.selection.strategy_config import load_cn_a_selection_v1_strategy


def _plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel-run-12-restore",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=120,
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
        lookback_trading_days=120,
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
                "vol_ratio": 1.5 + idx * 0.1,
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
    result = SelectionProviderBatchResult(
        provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
        attempt_refs=("attempt://akshare-1", "attempt://eastmoney-1"),
        normalized_refs=tuple(normalized_refs),
        rows=tuple(rows),
        warehouse_check_ref=f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/success",
    )
    root = Path(os.environ.get("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", "") or ".runtime/test-selection-columnar")
    writer = SelectionColumnarWarehouse(root=root).begin_write(plan=plan)
    writer.add_daily_rows(
        (
            {
                "ticker": str(row.get("ticker") or ""),
                "date": plan.trade_date,
                "close": row.get("close"),
                "amount": row.get("amount"),
                "source_ref": str(row.get("source_ref") or ""),
            }
            for row in rows
        )
    )
    writer.add_feature_rows(
        (
            {
                **dict(row),
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
            }
            for row in rows
        )
    )
    manifest = writer.commit(provider_attempt_refs=result.attempt_refs, normalized_refs=result.normalized_refs)
    return replace(
        result,
        warehouse_check_ref=manifest.warehouse_check_ref,
        columnar_manifest_ref=manifest.manifest_ref,
        columnar_manifest_sha256=SelectionColumnarWarehouse(root=root).manifest_sha256(manifest.manifest_ref),
    )


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


def _run_successful_data_job(*, tmp_path: Path, persisted: bool) -> str:
    plan = _plan()
    os.environ["CLAW_TRADE_SELECTION_COLUMNAR_ROOT"] = str(tmp_path / "columnar")
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs") if persisted else SelectionRunStore()
    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=_provider_result_success,
        strategy_config_loader=lambda _config_ref: _approved_strategy(),
        now_fn=lambda: datetime(2026, 5, 26, 9, 0, tzinfo=UTC),
        evidence_root=tmp_path,
    )
    result = job.run(plan)
    assert result.record.data_run.status.value == "completed"
    return plan.selection_run_id


@pytest.mark.integration
def test_restore_selection_store_from_data_job_evidence_roundtrip(tmp_path: Path) -> None:
    selection_run_id = _run_successful_data_job(tmp_path=tmp_path, persisted=False)

    restored = restore_selection_run_store(selection_runs_root=tmp_path)
    result = resolve_latest_completed_selection_run(
        store=restored,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )

    assert result.is_available is True
    assert result.run is not None
    assert result.run.run_plan.selection_run_id == selection_run_id


@pytest.mark.integration
def test_restore_selection_store_from_persisted_record_roundtrip(tmp_path: Path) -> None:
    selection_run_id = _run_successful_data_job(tmp_path=tmp_path, persisted=True)

    restored = restore_selection_run_store(selection_runs_root=tmp_path)
    result = resolve_latest_completed_selection_run(
        store=restored,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )

    assert result.is_available is True
    assert result.run is not None
    assert result.run.run_plan.selection_run_id == selection_run_id


@pytest.mark.integration
def test_restore_detects_missing_candidate_pack_body_file(tmp_path: Path) -> None:
    selection_run_id = _run_successful_data_job(tmp_path=tmp_path, persisted=True)
    persisted_path = tmp_path / "store" / "data-runs" / f"{selection_run_id}.json"
    payload = json.loads(persisted_path.read_text(encoding="utf-8"))
    body_path = _selection_artifact_path(
        tmp_path / "artifacts",
        payload["data_run"]["candidate_pack_ref"]["l1_uri"],
    )
    body_path.unlink()

    restored = restore_selection_run_store(selection_runs_root=tmp_path)
    result = resolve_latest_completed_selection_run(
        store=restored,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )

    assert result.is_available is False
    assert result.unavailable_code == SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH


@pytest.mark.integration
def test_restore_detects_candidate_pack_body_hash_mismatch(tmp_path: Path) -> None:
    selection_run_id = _run_successful_data_job(tmp_path=tmp_path, persisted=True)
    persisted_path = tmp_path / "store" / "data-runs" / f"{selection_run_id}.json"
    payload = json.loads(persisted_path.read_text(encoding="utf-8"))
    body_path = _selection_artifact_path(
        tmp_path / "artifacts",
        payload["data_run"]["candidate_pack_ref"]["l1_uri"],
    )
    body_path.write_text(f"{body_path.read_text(encoding='utf-8')}\n# corrupt\n", encoding="utf-8")

    restored = restore_selection_run_store(selection_runs_root=tmp_path)
    result = resolve_latest_completed_selection_run(
        store=restored,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )

    assert result.is_available is False
    assert result.unavailable_code == SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH


@pytest.mark.integration
def test_restore_keeps_persisted_active_data_run_read_only_by_default(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=plan,
            data_run=SelectionDataRun(
                selection_run_id=plan.selection_run_id,
                status=SelectionDataRunStatus.FETCHING_DATA,
                lease_id="lease://sel-run-12-restore",
                started_at="2026-05-26T09:00:00Z",
                progress_label="补齐全市场日线数据",
                progress_completed=64,
                progress_total=256,
            ),
            manifest=None,
        )
    )

    restored = restore_selection_run_store(selection_runs_root=tmp_path)

    active = restored.load_active_data_run_record(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    assert active is not None
    assert active.data_run.status == SelectionDataRunStatus.FETCHING_DATA
    assert active.data_run.progress_completed == 64
    persisted_path = tmp_path / "store" / "data-runs" / f"{plan.selection_run_id}.json"
    payload = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert payload["data_run"]["status"] == "fetching_data"
    assert payload["data_run"]["progress_completed"] == 64


@pytest.mark.integration
def test_restore_marks_persisted_active_data_run_failed_when_owner_restarts(tmp_path: Path) -> None:
    plan = _plan()
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=plan,
            data_run=SelectionDataRun(
                selection_run_id=plan.selection_run_id,
                status=SelectionDataRunStatus.FETCHING_DATA,
                lease_id="lease://sel-run-12-restore",
                started_at="2026-05-26T09:00:00Z",
                progress_label="补齐全市场日线数据",
                progress_completed=64,
                progress_total=256,
            ),
            manifest=None,
        )
    )

    restored = restore_selection_run_store(selection_runs_root=tmp_path, fail_interrupted_active=True)

    assert (
        restored.load_active_data_run_record(
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
        )
        is None
    )
    latest = restored.load_latest_data_run_record(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    assert latest is not None
    assert latest.data_run.status == SelectionDataRunStatus.FAILED
    assert latest.data_run.failure_code == "selection_data_run_interrupted"
    assert "上一次进程已中断" in (latest.data_run.failure_reason or "")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    [
        (
            lambda payload: payload["data_run"]["candidate_pack_ref"].__setitem__("expires_at", "2026-05-26T08:59:59Z"),
            SelectUnavailableCode.STALE_SELECTION_RUN,
        ),
        (
            lambda payload: payload["manifest"].__setitem__("pack_body_sha256", "f" * 64),
            SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH,
        ),
        (
            lambda payload: payload["integrity"].__setitem__("readback_verified", False),
            SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED,
        ),
        (
            lambda payload: payload["integrity"].__setitem__("lineage_complete", False),
            SelectUnavailableCode.CANDIDATE_PACK_LINEAGE_INCOMPLETE,
        ),
        (
            lambda payload: payload.__setitem__("manifest", None),
            SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED,
        ),
    ],
)
def test_restore_selection_store_fail_closed_for_integrity_violations(
    tmp_path: Path,
    mutator,
    expected_code: SelectUnavailableCode,
) -> None:
    selection_run_id = _run_successful_data_job(tmp_path=tmp_path, persisted=True)
    persisted_path = tmp_path / "store" / "data-runs" / f"{selection_run_id}.json"
    payload = json.loads(persisted_path.read_text(encoding="utf-8"))
    mutator(payload)
    persisted_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    restored = restore_selection_run_store(selection_runs_root=tmp_path)
    result = resolve_latest_completed_selection_run(
        store=restored,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )

    assert result.is_available is False
    assert result.unavailable_code == expected_code


def _selection_artifact_path(artifact_root: Path, uri: str) -> Path:
    prefix = "local://selection/"
    assert uri.startswith(prefix)
    return artifact_root / uri[len(prefix) :].strip("/")
