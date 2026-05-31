from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claw_trade.selection.data_job import SelectionDataJob, SelectionProviderBatchResult
from claw_trade.selection.engine import ApprovedSelectionStrategy
from claw_trade.selection.models import (
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import (
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
        ref = f"normalized://row-{idx + 1}"
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
                "source_ref": ref,
            }
        )
    return SelectionProviderBatchResult(
        provider_batch_plan=_provider_batch_plan(plan.provider_batch_plan_ref),
        attempt_refs=("attempt://akshare-1", "attempt://eastmoney-1"),
        normalized_refs=tuple(normalized_refs),
        rows=tuple(rows),
    )


def _run_successful_data_job(*, tmp_path: Path, persisted: bool) -> str:
    plan = _plan()
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
