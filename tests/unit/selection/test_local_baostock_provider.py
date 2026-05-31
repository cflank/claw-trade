from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from claw_trade.data_gateway import selection_batch as selection_batch_module
from claw_trade.data_gateway.selection_local_baostock import fetch_selection_batch_from_local_baostock
from claw_trade.selection.data_job import SelectionDataJob
from claw_trade.selection.models import (
    DataGapSeverity,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import SelectionRunStore
from claw_trade.selection.strategy_config import load_cn_a_selection_v1_strategy


def test_local_baostock_provider_returns_history_and_private_event_gap(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    plan = _selection_run_plan("sel-local-baostock-history")

    result = fetch_selection_batch_from_local_baostock(plan, root=root)

    assert result is not None
    assert result.provider_batch_plan.plan_id == plan.provider_batch_plan_ref
    assert result.attempt_refs
    assert any(ref.startswith("attempt://local-baostock/") for ref in result.attempt_refs)
    assert result.normalized_refs
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["ticker"] == "600001.SH"
    assert row["company_name"] == "样本银行"
    assert len(row["history"]) == 260
    assert row["history"][-1]["date"] == "2026-05-26"
    assert row["source_ref"] == result.normalized_refs[0]
    assert "private_placement_event_date" not in row
    gap_codes = {gap.gap_code for gap in result.data_gaps}
    assert "selection_private_placement_source_missing" in gap_codes
    private_gap = next(gap for gap in result.data_gaps if gap.gap_code == "selection_private_placement_source_missing")
    assert private_gap.severity == DataGapSeverity.BLOCKER
    assert private_gap.source_metadata is not None
    assert private_gap.source_metadata["selection_candidate_type"] == "local_history_seed_partial_candidate"


def test_local_baostock_provider_keeps_attempt_ref_when_sha_manifest_exists(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    manifest_dir = root / "manifest"
    manifest_dir.mkdir()
    (manifest_dir / "baostock_files.sha256").write_text("abc  daily/qfq/sh.600001.csv\n", encoding="utf-8")
    plan = _selection_run_plan("sel-local-baostock-attempt-manifest")

    result = fetch_selection_batch_from_local_baostock(plan, root=root)

    assert any(ref.startswith("attempt://local-baostock/") for ref in result.attempt_refs)
    assert any(ref.endswith("/baostock_files.sha256") for ref in result.attempt_refs)


def test_local_baostock_provider_joins_private_placement_event_cache(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    private_root = tmp_path / "akshare_stock_add_stock"
    private_root.mkdir()
    (private_root / "600001.csv").write_text(
        "公告日期,发行方式,发行价格,实际公司募集资金总额,发行费用总额,实际发行数量\n"
        "2026-05-25,定向配售,10.00元,100.00万元,1.00万元,10.00万股\n"
        "2026-04-01,网下定价发行,9.00元,90.00万元,1.00万元,9.00万股\n",
        encoding="utf-8",
    )
    plan = _selection_run_plan("sel-local-baostock-private-cache")

    result = fetch_selection_batch_from_local_baostock(plan, root=root, private_placement_root=private_root)

    assert result is not None
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["private_placement_event_date"] == "2026-05-25"
    assert row["private_placement_days_since"] == 1.0
    assert str(row["private_placement_source_ref"]).endswith("/600001.csv")
    assert "selection_private_placement_source_missing" not in {gap.gap_code for gap in result.data_gaps}


def test_local_baostock_provider_marks_empty_private_event_cache_as_covered(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    private_root = tmp_path / "akshare_stock_add_stock"
    private_root.mkdir()
    (private_root / "600001.csv").write_text(
        "公告日期,发行方式,发行价格,实际公司募集资金总额,发行费用总额,实际发行数量\n",
        encoding="utf-8",
    )
    plan = _selection_run_plan("sel-local-baostock-private-empty-cache")

    result = fetch_selection_batch_from_local_baostock(plan, root=root, private_placement_root=private_root)

    assert result is not None
    assert result.rows[0]["private_placement_event_date"] == "none"
    assert result.rows[0]["private_placement_days_since"] == 9999.0
    assert "selection_private_placement_source_missing" not in {gap.gap_code for gap in result.data_gaps}


def test_local_baostock_provider_does_not_mix_index_rows_into_stock_universe(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    plan = _selection_run_plan("sel-local-baostock-no-index")

    result = fetch_selection_batch_from_local_baostock(plan, root=root)

    assert result is not None
    assert [row["ticker"] for row in result.rows] == ["600001.SH"]


def test_local_baostock_provider_fails_closed_when_history_is_insufficient(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=3)
    plan = _selection_run_plan("sel-local-baostock-short-history")

    result = fetch_selection_batch_from_local_baostock(plan, root=root)

    assert result is not None
    assert result.rows == ()
    assert any(gap.gap_code == "selection_local_baostock_rows_empty" for gap in result.data_gaps)
    assert any(
        gap.gap_code == "selection_local_baostock_history_insufficient"
        and gap.severity == DataGapSeverity.BLOCKER
        for gap in result.data_gaps
    )


def test_local_baostock_provider_drops_stale_history(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    stock_file = root / "daily" / "qfq" / "sh.600001.csv"
    stale_rows = stock_file.read_text(encoding="utf-8").splitlines()
    stock_file.write_text("\n".join(stale_rows[:-1]) + "\n", encoding="utf-8")
    plan = _selection_run_plan("sel-local-baostock-stale-history")

    result = fetch_selection_batch_from_local_baostock(plan, root=root)

    assert result.rows == ()
    assert any(
        gap.gap_code == "selection_local_baostock_history_stale"
        and gap.severity == DataGapSeverity.BLOCKER
        for gap in result.data_gaps
    )
    assert any(gap.gap_code == "selection_local_baostock_rows_empty" for gap in result.data_gaps)


def test_data_gateway_does_not_use_explicit_local_baostock_root(monkeypatch, tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    monkeypatch.setenv("CLAW_TRADE_SELECTION_LOCAL_BAOSTOCK_ROOT", str(root))
    plan = _selection_run_plan("sel-local-baostock-env")

    def _gateway_path_reached():
        raise RuntimeError("gateway path reached")

    monkeypatch.setattr(selection_batch_module, "_build_selection_gateway_context", _gateway_path_reached)

    with pytest.raises(RuntimeError, match="gateway path reached"):
        selection_batch_module.fetch_selection_batch_from_data_gateway(plan)


def test_local_baostock_history_without_private_event_cannot_complete_data_job(tmp_path: Path) -> None:
    root = _write_local_baostock_seed(tmp_path, history_days=261)
    plan = _selection_run_plan("sel-local-baostock-data-job")
    job = SelectionDataJob(
        store=SelectionRunStore(),
        provider_fetch_batch=lambda run_plan: fetch_selection_batch_from_local_baostock(run_plan, root=root),
        strategy_config_loader=load_cn_a_selection_v1_strategy,
        evidence_root=tmp_path / "runs",
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.FAILED
    assert result.record.data_run.failure_code == "selection_warehouse_check_missing"
    assert result.record.manifest is None
    assert result.top20_tickers == ()
    gap_codes = {gap.gap_code for gap in result.record.data_run.data_gaps}
    assert "selection_warehouse_check_ref_missing" in gap_codes


def _selection_run_plan(selection_run_id: str) -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id=selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _write_local_baostock_seed(tmp_path: Path, *, history_days: int) -> Path:
    root = tmp_path / "baostock"
    universe_dir = root / "universe"
    stock_dir = root / "daily" / "qfq"
    index_dir = root / "index_daily"
    universe_dir.mkdir(parents=True)
    stock_dir.mkdir(parents=True)
    index_dir.mkdir(parents=True)
    (universe_dir / "query_all_stock_2026-05-26.csv").write_text(
        "code,tradeStatus,code_name\n"
        "sh.600001,1,样本银行\n"
        "sh.000001,1,上证综合指数\n",
        encoding="utf-8",
    )
    _write_history_csv(stock_dir / "sh.600001.csv", code="sh.600001", history_days=history_days)
    _write_history_csv(index_dir / "sh.000001.csv", code="sh.000001", history_days=history_days)
    return root


def _write_history_csv(path: Path, *, code: str, history_days: int) -> None:
    start = date(2026, 5, 26) - timedelta(days=history_days - 1)
    lines = [
        "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,pctChg,tradestatus,isST\n"
    ]
    previous_close = 10.0
    for idx in range(history_days):
        trade_date = start + timedelta(days=idx)
        close = 10.0 + idx * 0.01
        open_price = close * 0.99
        high = close * 1.01
        low = open_price * 0.99
        volume = 1_000_000 + idx
        amount = close * volume
        lines.append(
            f"{trade_date.isoformat()},{code},{open_price:.4f},{high:.4f},{low:.4f},{close:.4f},"
            f"{previous_close:.4f},{volume},{amount:.4f},2,1.0,0.1,1,0\n"
        )
        previous_close = close
    path.write_text("".join(lines), encoding="utf-8")
