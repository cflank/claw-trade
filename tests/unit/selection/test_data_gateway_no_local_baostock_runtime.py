from __future__ import annotations

import pytest
from claw_trade.data_gateway import _selection_batch as selection_batch_module
from claw_trade.selection.models import (
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)


def test_data_gateway_selection_batch_ignores_local_baostock_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CLAW_TRADE_SELECTION_LOCAL_BAOSTOCK_ROOT", str(tmp_path / "baostock"))
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(tmp_path / "columnar"))
    plan = SelectionRunPlan(
        selection_run_id="sel-no-local-baostock-runtime",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        data_need_audit_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )

    def _gateway_path_reached():
        raise RuntimeError("gateway path reached")

    monkeypatch.setattr(selection_batch_module, "_build_selection_gateway_context", _gateway_path_reached)

    result = selection_batch_module.fetch_selection_batch_from_data_gateway(plan)

    assert result.rows == ()
    assert result.attempt_refs == ()
    assert result.normalized_refs == ()
    assert result.warehouse_check_ref is None
    assert result.data_gaps[0].gap_code == "selection_data_api_unavailable"
    assert "gateway path reached" in result.data_gaps[0].reader_message
