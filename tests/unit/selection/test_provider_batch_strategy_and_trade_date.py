from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from claw_trade.data_gateway.selection_batch import (
    _selection_missing_strategy_required_fields,
    _selection_strategy_required_source_fields,
)
from claw_trade.data_gateway.selection_batch import (
    load_cn_a_selection_v1_strategy as load_gateway_strategy,
)
from claw_trade.selection.data_job import SelectionProviderBatchResult
from claw_trade.selection.engine import FilteredUniverse, score_candidates
from claw_trade.selection.features import (
    FeatureRow,
    build_feature_snapshot,
    normalize_selection_inputs,
)
from claw_trade.selection.models import (
    DataGapRef,
    DataGapSeverity,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.provider_batch import (
    _smoke_cli_run,
    build_selection_provider_batch_plan,
    load_cn_a_selection_v1_strategy,
    load_cn_a_selection_v1_strategy_config_ref,
    resolve_cn_a_closed_trade_date,
)
from claw_trade.selection.strategy_config import (
    CN_A_SELECTION_V1_WEIGHTS,
    cn_a_selection_v1_variant_ids,
)


def test_resolve_cn_a_closed_trade_date_uses_same_day_after_1600_bjt() -> None:
    now = datetime.fromisoformat("2026-05-26T08:00:00+00:00")  # 16:00 BJT
    assert resolve_cn_a_closed_trade_date(now) == "2026-05-26"


def test_resolve_cn_a_closed_trade_date_uses_previous_natural_day_before_1600_bjt() -> None:
    now = datetime.fromisoformat("2026-05-26T07:59:59+00:00")  # 15:59:59 BJT
    assert resolve_cn_a_closed_trade_date(now) == "2026-05-25"


def test_load_cn_a_selection_v1_strategy_supports_approved_refs() -> None:
    strategy_v1 = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    strategy_legacy = load_cn_a_selection_v1_strategy("config://cn-a-approved-v1")
    assert strategy_v1 is not None
    assert strategy_legacy is not None
    assert dict(strategy_v1.weights) == CN_A_SELECTION_V1_WEIGHTS
    assert "intraday_return_pct" not in strategy_v1.weights
    assert "amount" not in strategy_v1.weights
    assert strategy_legacy.stable_top20_rule.missing_policy == "fail"


def test_load_cn_a_selection_v1_strategy_exposes_all_approved_variants() -> None:
    strategy = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    assert strategy is not None
    assert tuple(rule.name for rule in strategy.strategy_set) == cn_a_selection_v1_variant_ids()
    assert len(strategy.strategy_set) == 17


def test_build_feature_snapshot_derives_all_approved_strategy_signals() -> None:
    plan = _selection_run_plan("sel-unit-strategy-signals")
    inputs = normalize_selection_inputs(
        plan=plan,
        normalized_refs=("normalized://strategy-signals",),
        attempt_refs=("attempt://strategy-signals",),
        raw_rows=(
            _strategy_signal_row(
                ticker="600100.SH",
                close_open_ratio_case="up",
                extra={
                    "ma30": 30.0,
                    "ma30_slope_5d": 0.2,
                    "ma30_slope_10d": 0.1,
                    "ma30_growth_30d": 21.0,
                    "limit_up_recent": 1.0,
                    "post_limit_up_range_pct": 2.5,
                    "post_limit_up_return_abs_pct": 4.5,
                    "post_limit_up_window_days": 3.0,
                    "ma250": 90.0,
                    "ma250_backtrace_days": 20.0,
                    "ma250_back_ratio": 0.7,
                    "ma60": 101.0,
                    "platform_deviation_pct": 5.0,
                    "return_60d": 61.0,
                    "single_day_min_return_60d": -6.0,
                    "highest_close_60d": 103.0,
                    "limit_up_count_20d": 2.0,
                    "return_40d": 95.0,
                    "limit_up_streak_2d": 1.0,
                    "atr_14": 9.0,
                    "prev_ma5": 19.0,
                    "prev_ma20": 20.0,
                    "ma5": 22.0,
                    "ma20": 21.0,
                    "volume_ma20": 100.0,
                    "volume": 170.0,
                    "high_20d": 102.0,
                    "prev_close": 102.0,
                    "range_10d": 10.0,
                    "low_10d": 90.0,
                    "high_40d": 100.0,
                    "rps120": 91.0,
                    "roll_high_120d": 110.0,
                    "p_change_pct": 3.0,
                    "private_placement_event_date": "2026-05-20",
                    "private_placement_days_since": 6.0,
                },
            ),
            _strategy_signal_row(
                ticker="600101.SH",
                close_open_ratio_case="down",
                extra={
                    "limit_down_today": 1.0,
                    "limit_up_yesterday": 1.0,
                    "prev_volume": 100.0,
                    "prev_close": 100.0,
                    "volume": 250.0,
                    "volume_ma20": 100.0,
                    "prev_ma20": 30.0,
                    "prev_ma60": 20.0,
                },
            ),
            _strategy_signal_row(
                ticker="600102.SH",
                close_open_ratio_case="up",
                extra={
                    "return_40d": 95.0,
                    "range_10d": 10.0,
                    "low_10d": 90.0,
                    "high_40d": 100.0,
                    "volume": 50.0,
                    "volume_ma20": 100.0,
                },
            ),
        ),
    )

    snapshot = build_feature_snapshot(plan=plan, inputs=inputs)

    signal_fields = {
        key
        for row in snapshot.rows
        for key, value in row.feature_values.items()
        if key.startswith("strategy_signal_") and value == 1.0
    }
    assert signal_fields == {f"strategy_signal_{variant_id}" for variant_id in cn_a_selection_v1_variant_ids()}


def test_build_feature_snapshot_does_not_fabricate_signal_when_source_fields_are_missing() -> None:
    plan = _selection_run_plan("sel-unit-strategy-signal-missing")
    inputs = normalize_selection_inputs(
        plan=plan,
        normalized_refs=("normalized://strategy-signal-missing",),
        attempt_refs=("attempt://strategy-signal-missing",),
        raw_rows=(
            {
                "ticker": "600102.SH",
                "company_name": "信号缺字段样本",
                "industry": "样本行业",
                "source_ref": "normalized://strategy-signal-missing/1",
                "open": 100.0,
                "close": 103.0,
                "amount": 300000000.0,
            },
        ),
    )

    snapshot = build_feature_snapshot(plan=plan, inputs=inputs)

    assert snapshot.rows[0].feature_values["strategy_signal_myhhub_volume_rise"] == 0.0


def test_build_feature_snapshot_derives_strategy_fields_from_260_day_history() -> None:
    plan = _selection_run_plan("sel-unit-history-derived-fields")
    inputs = normalize_selection_inputs(
        plan=plan,
        normalized_refs=("normalized://history-a", "normalized://history-b"),
        attempt_refs=("attempt://history"),
        raw_rows=(
            {
                "ticker": "600200.SH",
                "company_name": "历史样本A",
                "industry": "样本行业",
                "source_ref": "normalized://history-a",
                "private_placement_event_date": "none",
                "private_placement_days_since": 9999.0,
                "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
            },
            {
                "ticker": "600201.SH",
                "company_name": "历史样本B",
                "industry": "样本行业",
                "source_ref": "normalized://history-b",
                "private_placement_event_date": "2026-05-20",
                "private_placement_days_since": 6.0,
                "history": _history_rows(base_close=8.0, daily_step=0.08, latest_volume=5000000.0),
            },
        ),
    )

    snapshot = build_feature_snapshot(plan=plan, inputs=inputs)

    by_ticker = {row.ticker: row.feature_values for row in snapshot.rows}
    first = by_ticker["600200.SH"]
    second = by_ticker["600201.SH"]
    assert first["ma5"] > first["ma20"]
    assert first["ma30_slope_5d"] > 0.0
    assert first["ma250"] > 0.0
    assert first["volume_ma20"] > 0.0
    assert first["vol_ratio"] > 1.0
    assert first["atr_14"] > 0.0
    assert first["highest_close_60d"] == first["close"]
    assert first["strategy_signal_myhhub_turtle_60_close"] == 1.0
    assert first["strategy_signal_sequoia_private_placement"] == 0.0
    assert second["strategy_signal_sequoia_private_placement"] == 1.0
    assert second["rps120"] > first["rps120"]
    for field in (f"strategy_signal_{variant_id}" for variant_id in cn_a_selection_v1_variant_ids()):
        assert field in first
        assert field in second


def test_normalize_selection_inputs_rejects_duplicate_ticker_with_same_company() -> None:
    plan = _selection_run_plan("sel-unit-duplicate-ticker")

    with pytest.raises(Exception) as exc_info:
        normalize_selection_inputs(
            plan=plan,
            normalized_refs=("normalized://dup-1", "normalized://dup-2"),
            attempt_refs=("attempt://dup"),
            raw_rows=(
                {
                    "ticker": "600202.SH",
                    "company_name": "重复样本",
                    "source_ref": "normalized://dup-1",
                    "open": 10.0,
                    "close": 10.2,
                    "high": 10.3,
                    "low": 9.9,
                    "amount": 300000000.0,
                },
                {
                    "ticker": "600202.SH",
                    "company_name": "重复样本",
                    "source_ref": "normalized://dup-2",
                    "open": 10.0,
                    "close": 10.2,
                    "high": 10.3,
                    "low": 9.9,
                    "amount": 300000000.0,
                },
            ),
        )

    assert getattr(exc_info.value, "data_gaps")[0].gap_code == "duplicate_ticker"


def test_selection_loaders_stay_aligned_for_strategy_config() -> None:
    selection_strategy = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    gateway_strategy = load_gateway_strategy("config://cn-a-selection-v1")
    assert selection_strategy is not None
    assert gateway_strategy is not None
    assert tuple(rule.name for rule in selection_strategy.strategy_set) == tuple(
        rule.name for rule in gateway_strategy.strategy_set
    )
    assert dict(selection_strategy.weights) == dict(gateway_strategy.weights)
    assert selection_strategy.stable_top20_rule == gateway_strategy.stable_top20_rule


def test_load_cn_a_selection_v1_strategy_returns_none_for_unapproved_ref() -> None:
    assert load_cn_a_selection_v1_strategy("config://unknown") is None


def test_strategy_config_ref_loader_is_cn_a_only() -> None:
    assert load_cn_a_selection_v1_strategy_config_ref(SelectionMarket.CN_A, SelectionProfile.CN_A) == "config://cn-a-selection-v1"


def test_selection_provider_batch_plan_includes_free_cn_a_market_candidates_beyond_tushare_and_eastmoney() -> None:
    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    assert "tushare_selection_batch" in plan.provider_candidates
    assert "eastmoney_selection_batch" in plan.provider_candidates
    assert "akshare_selection_batch" in plan.provider_candidates
    assert "baostock_selection_batch" in plan.provider_candidates
    assert "mootdx_selection_batch" in plan.provider_candidates
    assert "tencent_selection_batch" in plan.provider_candidates
    assert "sina_selection_batch" in plan.provider_candidates


def test_selection_strategy_amount_threshold_is_unchanged() -> None:
    strategy = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    assert strategy is not None
    amount_rule = next(rule for rule in strategy.hard_filters if rule.name == "amount_ge_2e8")
    assert amount_rule.value == 200000000


def test_selection_gateway_strategy_required_source_fields_include_variant_dependencies() -> None:
    strategy = load_gateway_strategy("config://cn-a-selection-v1")
    assert strategy is not None

    required_fields = set(_selection_strategy_required_source_fields(strategy=strategy))

    assert "history" in required_fields
    assert "private_placement_days_since" in required_fields
    assert "private_placement_event_date" in required_fields
    assert "open" in required_fields
    assert "close" in required_fields
    assert "strategy_signal_myhhub_volume_rise" not in required_fields


def test_selection_gateway_marks_current_only_rows_missing_strategy_history() -> None:
    plan = _selection_run_plan("sel-unit-current-only-source-fields")

    missing = _selection_missing_strategy_required_fields(
        plan=plan,
        rows=(
            {
                "ticker": "600203.SH",
                "company_name": "当前快照样本",
                "open": 10.0,
                "close": 10.2,
                "high": 10.3,
                "low": 9.9,
                "amount": 300000000.0,
            },
        ),
    )

    assert "history" in missing
    assert "private_placement_days_since" in missing
    assert "private_placement_event_date" in missing


def test_selection_gateway_accepts_rows_with_required_history_and_private_event_fields() -> None:
    plan = _selection_run_plan("sel-unit-complete-source-fields")

    missing = _selection_missing_strategy_required_fields(
        plan=plan,
        rows=(
            {
                "ticker": "600204.SH",
                "company_name": "完整历史样本",
                "open": 10.0,
                "close": 10.2,
                "high": 10.3,
                "low": 9.9,
                "amount": 300000000.0,
                "volume": 3000000.0,
                "private_placement_event_date": "none",
                "private_placement_days_since": 9999.0,
                "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
            },
        ),
    )

    assert missing == ()


def test_score_candidates_keeps_variants_and_penalizes_missing_strategy_fields() -> None:
    strategy = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    assert strategy is not None
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-missing-fields",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    rows = tuple(
        FeatureRow(
            ticker=f"{600000 + idx:06d}.SH",
            company_name=f"样本股票{idx + 1}",
            industry="样本行业",
            feature_values={"amount": 200000000.0 + idx * 10000000.0},
            source_ref=f"normalized://row-{idx + 1}",
        )
        for idx in range(20)
    )

    result = score_candidates(
        plan=plan,
        filtered=FilteredUniverse(rows=rows, decisions=()),
        strategy=strategy,
    )

    assert len(strategy.strategy_set) == 17
    assert len(result.top20) == 20
    assert result.top20[0].feature_values["data_gap_penalty_score"] > 0.0
    assert result.top20[0].feature_values["data_gap_penalty_score"] <= 15.0
    assert result.top20[0].feature_values["strategy_missing_field_count"] > 0.0
    assert result.top20[0].feature_values["strategy_hit_count"] == 0.0
    assert result.top20[0].strategy_hits == ()
    assert "strategy_hit_coverage_score" in result.top20[0].feature_values


def _smoke_result(*, rows: int, gaps: tuple[DataGapRef, ...]) -> SelectionProviderBatchResult:
    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    mapped_rows = tuple({"ticker": f"60051{idx}.SH"} for idx in range(rows))
    return SelectionProviderBatchResult(
        provider_batch_plan=plan,
        attempt_refs=("attempt://mongo/openbb_provider_attempts/test",),
        normalized_refs=("normalized://mongo/openbb_normalized/test",),
        rows=mapped_rows,
        data_gaps=gaps,
    )


def _gap(*, gap_code: str, severity: DataGapSeverity) -> DataGapRef:
    return DataGapRef(
        gap_id=f"sel13-{gap_code}",
        domain="selection",
        gap_code=gap_code,
        severity=severity,
        attempt_refs=("attempt://mongo/openbb_provider_attempts/test",),
        reader_message="test",
    )


def test_smoke_cli_returns_zero_for_rows_with_warn_gap_only(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _smoke_result(
        rows=1,
        gaps=(_gap(gap_code="selection_batch_rows_dropped", severity=DataGapSeverity.WARN),),
    )
    monkeypatch.setattr("claw_trade.selection.provider_batch.fetch_selection_batch_from_data_gateway", lambda _plan: result)
    code = _smoke_cli_run(["--selection-run-id", "sel13-smoke-warn", "--trade-date", "2026-05-26"])
    assert code == 0


def test_smoke_cli_returns_two_when_rows_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _smoke_result(rows=0, gaps=())
    monkeypatch.setattr("claw_trade.selection.provider_batch.fetch_selection_batch_from_data_gateway", lambda _plan: result)
    code = _smoke_cli_run(["--selection-run-id", "sel13-smoke-empty", "--trade-date", "2026-05-26"])
    assert code == 2


def test_smoke_cli_returns_two_for_blocker_gap_even_with_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _smoke_result(
        rows=1,
        gaps=(_gap(gap_code="selection_batch_remote_error", severity=DataGapSeverity.BLOCKER),),
    )
    monkeypatch.setattr("claw_trade.selection.provider_batch.fetch_selection_batch_from_data_gateway", lambda _plan: result)
    code = _smoke_cli_run(["--selection-run-id", "sel13-smoke-blocker", "--trade-date", "2026-05-26"])
    assert code == 2


def test_smoke_cli_output_includes_structured_gap_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    gap = DataGapRef(
        gap_id="sel13-gap-structured",
        domain="selection",
        gap_code="selection_batch_empty",
        severity=DataGapSeverity.WARN,
        attempt_refs=("attempt://mongo/openbb_provider_attempts/test",),
        reader_message="structured gap test",
        source_metadata={
            "selection_candidate_type": "partial_batch_candidate",
            "partial_chunk_error_count": 1,
            "partial_chunk_errors": ["sina quote parse failed for symbols=sz019525,sz019528"],
            "failed_chunk_symbols_sample": ["sz019525,sz019528"],
            "universe_source": "mootdx.stock_all",
            "quote_source": "sina.hq",
            "mixed_source_chain": ["mootdx.stock_all", "sina.hq"],
        },
    )
    result = _smoke_result(rows=0, gaps=(gap,))
    monkeypatch.setattr("claw_trade.selection.provider_batch.fetch_selection_batch_from_data_gateway", lambda _plan: result)
    output_json = tmp_path / "sel13-smoke-structured-gap.json"

    code = _smoke_cli_run(
        [
            "--selection-run-id",
            "sel13-smoke-structured-gap",
            "--trade-date",
            "2026-05-26",
            "--output-json",
            str(output_json),
        ]
    )

    assert code == 2
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["data_gap_codes"] == ["selection_batch_empty"]
    assert payload["data_gap_severities"] == ["warn"]
    assert payload["data_gaps"][0]["source_metadata"]["selection_candidate_type"] == "partial_batch_candidate"
    assert payload["data_gaps"][0]["source_metadata"]["partial_chunk_error_count"] == 1
    assert payload["data_gaps"][0]["source_metadata"]["partial_chunk_errors"]
    assert payload["data_gaps"][0]["source_metadata"]["failed_chunk_symbols_sample"]


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


def _strategy_signal_row(*, ticker: str, close_open_ratio_case: str, extra: dict[str, float]) -> dict[str, object]:
    if close_open_ratio_case == "up":
        open_price = 100.0
        close_price = 103.0
        high_price = 230.0
        low_price = 100.0
    elif close_open_ratio_case == "down":
        open_price = 110.0
        close_price = 90.0
        high_price = 112.0
        low_price = 100.0
    else:
        raise AssertionError(f"unexpected close_open_ratio_case={close_open_ratio_case}")
    base: dict[str, object] = {
        "ticker": ticker,
        "company_name": f"策略信号样本{ticker}",
        "industry": "样本行业",
        "source_ref": f"normalized://strategy-signals/{ticker}",
        "open": open_price,
        "close": close_price,
        "high": high_price,
        "low": low_price,
        "amount": 300000000.0,
        "vol_ratio": 4.5,
    }
    base.update(extra)
    return base


def _history_rows(*, base_close: float, daily_step: float, latest_volume: float) -> tuple[dict[str, object], ...]:
    start = datetime(2025, 5, 27)
    rows: list[dict[str, object]] = []
    for idx in range(260):
        close = base_close + daily_step * idx
        open_price = close * 0.99
        volume = 1000000.0 + idx * 1000.0
        if idx == 259:
            volume = latest_volume
        rows.append(
            {
                "date": (start + timedelta(days=idx)).date().isoformat(),
                "open": open_price,
                "high": close * 1.01,
                "low": open_price * 0.99,
                "close": close,
                "volume": volume,
                "amount": close * volume,
            }
        )
    return tuple(rows)
