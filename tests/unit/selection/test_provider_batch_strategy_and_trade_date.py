from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from claw_trade.data_gateway._selection_batch import (
    _daily_dates_between,
    _history_row,
    _LocalFeatureRowsResult,
    _refresh_request_chunks,
    _selection_feature_rows_from_repository,
    _selection_missing_strategy_required_fields,
    _selection_strategy_required_source_fields,
)
from claw_trade.data_gateway._selection_batch import (
    fetch_selection_batch_from_data_gateway as fetch_gateway_selection_batch,
)
from claw_trade.data_gateway._selection_batch import (
    load_cn_a_selection_v1_strategy as load_gateway_strategy,
)
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResult,
    DataResultStatus,
    Market,
)
from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse
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
    resolve_cn_a_closed_trade_date_for_scheduler,
)
from claw_trade.selection.provider_batch import (
    fetch_selection_batch_from_data_gateway as fetch_selection_batch_facade,
)
from claw_trade.selection.strategy_config import (
    CN_A_SELECTION_V1_WEIGHTS,
    cn_a_selection_v1_variant_ids,
)


@pytest.fixture(autouse=True)
def _isolated_selection_columnar_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(tmp_path / "columnar"))


def test_resolve_cn_a_closed_trade_date_uses_same_day_after_1600_bjt() -> None:
    now = datetime.fromisoformat("2026-05-26T08:00:00+00:00")  # 16:00 BJT
    assert resolve_cn_a_closed_trade_date(now) == "2026-05-26"


def test_resolve_cn_a_closed_trade_date_uses_previous_natural_day_before_1600_bjt() -> None:
    now = datetime.fromisoformat("2026-05-26T07:59:59+00:00")  # 15:59:59 BJT
    assert resolve_cn_a_closed_trade_date(now) == "2026-05-25"


def test_resolve_cn_a_closed_trade_date_skips_weekend_after_cutoff() -> None:
    now = datetime.fromisoformat("2026-06-06T08:00:00+00:00")  # Saturday 16:00 BJT
    assert resolve_cn_a_closed_trade_date(now) == "2026-06-05"


def test_resolve_cn_a_closed_trade_date_for_scheduler_normalizes_explicit_weekend() -> None:
    assert resolve_cn_a_closed_trade_date_for_scheduler("2026-06-06") == "2026-06-05"


def test_load_cn_a_selection_v1_strategy_supports_approved_refs() -> None:
    strategy_v1 = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    strategy_legacy = load_cn_a_selection_v1_strategy("config://cn-a-approved-v1")
    assert strategy_v1 is not None
    assert strategy_legacy is not None
    assert dict(strategy_v1.weights) == CN_A_SELECTION_V1_WEIGHTS
    assert "intraday_return_pct" not in strategy_v1.weights
    assert "amount" not in strategy_v1.weights
    assert strategy_legacy.stable_top20_rule.missing_policy == "fail"


def test_selection_batch_facade_forwards_progress_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-progress-facade",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-04",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-06-04/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
    )
    provider_plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-04",
        plan_id=plan.provider_batch_plan_ref,
    )
    progress_events: list[tuple[str, int, int]] = []

    def fake_fetch(run_plan, *, evidence_root=None, progress_callback=None):  # noqa: ANN001
        del evidence_root
        assert run_plan is plan
        assert progress_callback is not None
        progress_callback(type("Progress", (), {"label": "补齐全市场日线数据", "completed": 1, "total": 2})())
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=("attempt://progress-facade",),
            normalized_refs=("dataset://normalized/CN_A/daily/progress-facade",),
            rows=({"ticker": "600519.SH"},),
            warehouse_check_ref="warehouse-check://progress-facade",
        )

    monkeypatch.setattr("claw_trade.selection.provider_batch._fetch_selection_batch_from_data_gateway", fake_fetch)

    result = fetch_selection_batch_facade(
        plan,
        progress_callback=lambda progress: progress_events.append(
            (progress.label, progress.completed, progress.total)
        ),
    )

    assert result.attempt_refs == ("attempt://progress-facade",)
    assert progress_events == [("补齐全市场日线数据", 1, 2)]


def test_selection_universe_refresh_dates_skip_cn_a_exchange_holidays() -> None:
    assert _daily_dates_between(date(2025, 1, 1), date(2025, 1, 1)) == ()
    assert _daily_dates_between(date(2025, 1, 28), date(2025, 2, 4)) == ()
    assert _daily_dates_between(date(2025, 1, 27), date(2025, 2, 5)) == (
        date(2025, 1, 27),
        date(2025, 2, 5),
    )


def test_selection_refresh_chunks_split_across_weekends() -> None:
    requests = tuple(
        DataRequest(
            request_id=f"req-{day.isoformat()}",
            market=Market.CN_A,
            universe_ref="all_a_shares",
            timezone="Asia/Shanghai",
            calendar="CN_A_SSE_SZSE",
            data_type="daily_bar",
            granularity="daily",
            fields=("date", "open", "high", "low", "close", "volume", "amount"),
            date_range_start=day,
            date_range_end=day,
            freshness_policy="trading_day",
            consumer="select",
            consumer_id="sel-unit",
            as_of=datetime(2026, 6, 4, tzinfo=UTC),
        )
        for day in (date(2025, 4, 18), date(2025, 4, 21), date(2025, 4, 22))
    )

    chunks = _refresh_request_chunks(requests, 5)

    assert tuple(tuple(request.date_range_start for request in chunk) for chunk in chunks) == (
        (date(2025, 4, 18),),
        (date(2025, 4, 21), date(2025, 4, 22)),
    )


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


def test_selection_provider_batch_plan_uses_current_unified_data_api_provider_ids() -> None:
    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    assert "cn_a_primary" in plan.provider_candidates
    assert "cn_a_tushare_fundamental" in plan.provider_candidates
    assert "cn_a_akshare_social_news" in plan.provider_candidates
    assert "cn_a_eastmoney_market_data" in plan.provider_candidates
    assert "cn_a_baostock_market" in plan.provider_candidates
    assert "cn_a_mootdx_market" in plan.provider_candidates


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
    assert "private_placement_days_since" not in missing
    assert "private_placement_event_date" not in missing


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


def test_selection_gateway_fetch_uses_data_api_select_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _selection_run_plan("sel-unit-data-api-fetch")
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.READY,
                rows=(
                    {
                        "ticker": "600204.SH",
                        "company_name": "完整历史样本",
                        "industry": "样本行业",
                        "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
                        "private_placement_event_date": "none",
                        "private_placement_days_since": 9999.0,
                    },
                ),
                dataset_refs=("dataset:daily_bar:CN_A:unit",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:unit",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "完整历史样本",
                    "industry": "样本行业",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 17.77,
                    "amount": 300000000.0,
                },
            ),
            normalized_refs=("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",),
            attempt_refs=("attempt:cn_a_primary:daily_bar:unit",),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert result.provider_batch_plan.plan_id == plan.provider_batch_plan_ref
    assert result.attempt_refs == ("attempt:cn_a_primary:daily_bar:unit",)
    assert result.normalized_refs == ("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",)
    assert result.warehouse_check_ref == "warehouse-check://selection-columnar/CN_A/CN_A/2026-05-26"
    assert result.columnar_manifest_ref is not None
    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "600204.SH"
    assert result.data_gaps == ()
    assert len(fake_api.requests) == 1
    assert fake_api.requests[0].consumer == "select"
    assert fake_api.requests[0].consumer_id == f"{plan.selection_run_id}:coverage_check"
    assert fake_api.requests[0].universe_ref == "all_a_shares"
    assert fake_api.requests[0].data_type == "daily_bar"
    assert len(fake_api.calls) == 1


def test_selection_gateway_never_materializes_data_api_rows_for_select(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _selection_run_plan("sel-unit-no-direct-rows")
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.READY,
                rows=(
                    {
                        "ticker": "600204.SH",
                        "company_name": "DataAPI 行不能直接进入选股",
                        "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
                    },
                ),
                dataset_refs=("dataset:daily_bar:CN_A:unit",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:unit",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_rows_from_results",
        lambda **_kwargs: pytest.fail("select must not build rows from DataAPI result rows"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "列式仓库样本",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 17.77,
                    "amount": 300000000.0,
                },
            ),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert len(result.rows) == 1
    assert result.rows[0]["company_name"] == "列式仓库样本"
    assert result.columnar_manifest_ref is not None


def test_selection_gateway_does_not_fetch_supplemental_when_prepackaged_direct_rows_are_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _selection_run_plan("sel-unit-prepackaged-incomplete")
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.READY,
                rows=(
                    {
                        "ticker": "600205.SH",
                        "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
                    },
                ),
                dataset_refs=("dataset:daily_bar:CN_A:prepackaged-incomplete",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:prepackaged-incomplete",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(),
            normalized_refs=("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:prepackaged-incomplete",),
            attempt_refs=("attempt:cn_a_primary:daily_bar:prepackaged-incomplete",),
            data_gaps=(
                DataGapRef(
                    gap_id="rows-dropped",
                    domain="selection",
                    gap_code="selection_batch_rows_dropped",
                    severity=DataGapSeverity.WARN,
                    attempt_refs=("attempt:cn_a_primary:daily_bar:prepackaged-incomplete",),
                    reader_message="dropped",
                ),
                DataGapRef(
                    gap_id="rows-empty",
                    domain="selection",
                    gap_code="selection_batch_rows_empty",
                    severity=DataGapSeverity.BLOCKER,
                    attempt_refs=("attempt:cn_a_primary:daily_bar:prepackaged-incomplete",),
                    reader_message="empty",
                ),
            ),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert len(fake_api.calls) == 1
    assert [request.data_type for request in fake_api.calls[0]] == ["daily_bar"]
    assert result.rows == ()
    assert {gap.gap_code for gap in result.data_gaps} == {
        "selection_batch_rows_dropped",
        "selection_batch_rows_empty",
    }


def test_selection_gateway_backfills_old_direct_rows_with_short_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _selection_run_plan("sel-unit-history-backfill")
    old_short_history = _history_rows_from(start=date(2025, 12, 3), count=114, ticker="688981.SH")
    new_short_history = _history_rows_from(start=date(2026, 2, 3), count=72, ticker="001220.SZ")
    repaired_history = _history_rows_from(start=date(2025, 5, 27), count=260, ticker="688981.SH")
    old_short_direct_row = {
        "ticker": "688981.SH",
        "company_name": "中芯国际",
        "industry": "半导体",
        "history": old_short_history,
        "private_placement_event_date": "none",
        "private_placement_days_since": 9999.0,
    }
    fake_api = _SequencedFakeDataAPI(
        (
            (
                DataResult(
                    request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                    status=DataResultStatus.READY,
                    rows=(
                        {
                            "ticker": "600204.SH",
                            "company_name": "完整历史样本",
                            "industry": "样本行业",
                            "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
                            "private_placement_event_date": "none",
                            "private_placement_days_since": 9999.0,
                        },
                        old_short_direct_row,
                        {
                            "ticker": "001220.SZ",
                            "company_name": "世盟股份",
                            "industry": "样本行业",
                            "list_date": "2026-02-03",
                            "history": new_short_history,
                            "private_placement_event_date": "none",
                            "private_placement_days_since": 9999.0,
                        },
                    ),
                    dataset_refs=("dataset:daily_bar:CN_A:prepackaged",),
                    attempt_refs=("attempt:local_a_share_prepackaged:daily_bar:prepackaged",),
                    as_of=datetime(2026, 5, 26, tzinfo=UTC),
                ),
            ),
            (
                DataResult(
                    request_id=f"{plan.selection_run_id}:selection:history_backfill:1:688981.SH:daily_bar",
                    status=DataResultStatus.PARTIAL,
                    rows=(old_short_direct_row, *repaired_history),
                    dataset_refs=("dataset:daily_bar:CN_A:688981.SH:repair",),
                    attempt_refs=("attempt:cn_a_primary:daily_bar:688981-repair",),
                    gaps=(
                        DataGap.by_reason(
                            "empty_result",
                            request_id=f"{plan.selection_run_id}:selection:history_backfill:1:688981.SH:daily_bar",
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            evidence_refs=("attempt:cn_a_backup:daily_bar:688981-empty",),
                            message="backup provider returned no rows but primary repaired the history",
                            as_of=datetime(2026, 5, 26, tzinfo=UTC),
                        ),
                    ),
                    as_of=datetime(2026, 5, 26, tzinfo=UTC),
                ),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._listing_dates_for_tickers",
        lambda tickers: {"688981.SH": date(2020, 7, 16)},
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "完整历史样本",
                    "industry": "样本行业",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:600204",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 17.77,
                    "amount": 300000000.0,
                },
                {
                    "ticker": "688981.SH",
                    "company_name": "中芯国际",
                    "industry": "半导体",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:688981",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 20.0,
                    "amount": 300000000.0,
                },
            ),
            normalized_refs=("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:prepackaged",),
            attempt_refs=("attempt:local_a_share_prepackaged:daily_bar:prepackaged",),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert len(fake_api.calls) == 1
    assert [request.data_type for request in fake_api.calls[0]] == ["daily_bar"]
    tickers = {str(row["ticker"]) for row in result.rows}
    assert "600204.SH" in tickers
    assert "688981.SH" in tickers
    assert [str(row["ticker"]) for row in result.rows].count("688981.SH") == 1
    repaired = next(row for row in result.rows if row["ticker"] == "688981.SH")
    assert repaired["selection_features_materialized"] is True


def test_selection_gateway_expands_stale_all_share_batch_to_trade_date_universe_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-universe-refresh",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-04",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-06-04/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    stale_rows = (
        {
            "ticker": "600204.SH",
            "company_name": "上海电力",
            "industry": "电力",
            "list_date": "2003-10-29",
            "history": _history_rows_from(start=date(2025, 9, 17), count=260, ticker="600204.SH"),
            "private_placement_event_date": "none",
            "private_placement_days_since": 9999.0,
        },
        {
            "ticker": "688981.SH",
            "company_name": "中芯国际",
            "industry": "半导体",
            "list_date": "2020-07-16",
            "history": _history_rows_from(start=date(2025, 9, 17), count=260, ticker="688981.SH"),
            "private_placement_event_date": "none",
            "private_placement_days_since": 9999.0,
        },
    )
    fake_api = _SequencedFakeDataAPI(
        (
            (
                DataResult(
                    request_id=main_request_id,
                    status=DataResultStatus.PARTIAL,
                    rows=stale_rows,
                    dataset_refs=("dataset:daily_bar:CN_A:all-a-shares-stale",),
                    attempt_refs=("attempt:local_a_share_prepackaged:daily_bar:stale",),
                    gaps=(
                        DataGap.by_reason(
                            "date_range_missing",
                            request_id=main_request_id,
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            message="date_range_missing",
                            as_of=datetime(2026, 6, 4, tzinfo=UTC),
                        ),
                        DataGap.by_reason(
                            "warehouse_stale",
                            request_id=main_request_id,
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            message="warehouse_stale",
                            as_of=datetime(2026, 6, 4, tzinfo=UTC),
                        ),
                        DataGap.by_reason(
                            "provider_error",
                            request_id=main_request_id,
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            message="symbol_required",
                            as_of=datetime(2026, 6, 4, tzinfo=UTC),
                        ),
                    ),
                    as_of=datetime(2026, 6, 4, tzinfo=UTC),
                ),
            ),
            (
                DataResult(
                    request_id=f"{plan.selection_run_id}:selection:universe_refresh:1:all_a_shares:daily_bar",
                    status=DataResultStatus.READY,
                    rows=(
                        _daily_bar_row(ticker="600204.SH", trade_date="2026-06-04", close=21.4),
                        _daily_bar_row(ticker="688981.SH", trade_date="2026-06-04", close=56.7),
                    ),
                    dataset_refs=("dataset:daily_bar:CN_A:all-a-shares-refresh-20260604",),
                    attempt_refs=("attempt:cn_a_primary:daily_bar_by_trade_date:20260604",),
                    as_of=datetime(2026, 6, 4, tzinfo=UTC),
                ),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "上海电力",
                    "industry": "电力",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:600204:20260604",
                    "trade_date": "2026-06-04",
                    "selection_features_materialized": True,
                    "open": 20.0,
                    "high": 22.0,
                    "low": 19.5,
                    "close": 21.4,
                    "volume": 3000000.0,
                    "amount": 300000000.0,
                    "return_60d": 10.0,
                    "return_120d": 20.0,
                },
                {
                    "ticker": "688981.SH",
                    "company_name": "中芯国际",
                    "industry": "半导体",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:688981:20260604",
                    "trade_date": "2026-06-04",
                    "selection_features_materialized": True,
                    "open": 55.0,
                    "high": 57.0,
                    "low": 54.0,
                    "close": 56.7,
                    "volume": 3000000.0,
                    "amount": 300000000.0,
                    "return_60d": 12.0,
                    "return_120d": 22.0,
                },
            ),
            normalized_refs=(
                "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:600204:20260604",
                "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:688981:20260604",
            ),
            attempt_refs=("attempt:cn_a_primary:daily_bar_by_trade_date:20260604",),
            data_gaps=(),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert len(fake_api.calls) == 3
    assert [request.universe_ref for request in fake_api.calls[0]] == ["all_a_shares"]
    assert [request.freshness_policy for request in fake_api.calls[0]] == ["warehouse_only"]
    assert [request.symbol_id for request in fake_api.calls[1]] == [None]
    assert [request.universe_ref for request in fake_api.calls[1]] == ["all_a_shares"]
    assert all(request.freshness_policy == "trading_day" for request in fake_api.calls[1])
    assert fake_api.calls[1][0].date_range_start == date(2026, 6, 4)
    assert fake_api.calls[1][0].date_range_end == date(2026, 6, 4)
    assert [request.freshness_policy for request in fake_api.calls[2]] == ["warehouse_only"]
    assert fake_api.calls[2][0].consumer_id == f"{plan.selection_run_id}:coverage_check"
    assert {row["ticker"] for row in result.rows} == {"600204.SH", "688981.SH"}
    assert next(row for row in result.rows if row["ticker"] == "600204.SH")["trade_date"] == "2026-06-04"
    assert not any(gap.severity == DataGapSeverity.BLOCKER for gap in result.data_gaps)
    assert "selection_data_api_provider_error" not in {gap.gap_code for gap in result.data_gaps}


def test_selection_gateway_expands_metadata_only_coverage_gap_to_universe_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-metadata-universe-refresh",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-04",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-06-04/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    fake_api = _SequencedFakeDataAPI(
        (
            (
                DataResult(
                    request_id=main_request_id,
                    status=DataResultStatus.PARTIAL,
                    rows=(),
                    dataset_refs=("dataset:daily_bar:CN_A:metadata-only",),
                    attempt_refs=("attempt:metadata:daily_bar",),
                    gaps=(
                        DataGap.by_reason(
                            "date_range_missing",
                            request_id=main_request_id,
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            message="date_range_missing",
                            as_of=datetime(2026, 6, 4, tzinfo=UTC),
                        ),
                    ),
                    freshness={
                        "coverage_by_request": (
                            {
                                "request_id": main_request_id,
                                "actual_end": "2026-05-27",
                                "expected_end": "2026-06-04",
                                "missing_ranges": (),
                            },
                        )
                    },
                    as_of=datetime(2026, 6, 4, tzinfo=UTC),
                ),
            ),
            (),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )

    fetch_gateway_selection_batch(plan)

    assert len(fake_api.calls) == 4
    assert fake_api.calls[0][0].freshness_policy == "warehouse_only"
    refresh_requests = tuple(request for call in fake_api.calls[1:-1] for request in call)
    assert [request.date_range_start for request in refresh_requests] == [
        date(2026, 5, 28),
        date(2026, 5, 29),
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
    ]
    assert all(request.date_range_start == request.date_range_end for request in refresh_requests)
    assert all(request.universe_ref == "all_a_shares" for request in refresh_requests)
    assert all(request.symbol_id is None for request in refresh_requests)
    assert fake_api.calls[-1][0].freshness_policy == "warehouse_only"


def test_selection_gateway_refreshes_full_request_when_metadata_integrity_fails_without_usable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-integrity-refresh",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-04",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-06-04/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    fake_api = _SequencedFakeDataAPI(
        (
            (
                DataResult(
                    request_id=main_request_id,
                    status=DataResultStatus.MISSING,
                    rows=(),
                    dataset_refs=(),
                    attempt_refs=(),
                    gaps=(
                        DataGap.by_reason(
                            "data_integrity_failed",
                            request_id=main_request_id,
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            message="local dataset checksum metadata missing or row payload missing",
                            as_of=datetime(2026, 6, 4, tzinfo=UTC),
                        ),
                    ),
                    freshness={
                        "coverage_by_request": (
                            {
                                "request_id": main_request_id,
                                "actual_end": "2026-06-04",
                                "expected_end": "2026-06-04",
                                "missing_ranges": (),
                            },
                        )
                    },
                    as_of=datetime(2026, 6, 4, tzinfo=UTC),
                ),
            ),
            (),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )

    fetch_gateway_selection_batch(plan)

    refresh_requests = tuple(request for call in fake_api.calls[1:-1] for request in call)
    assert len(refresh_requests) == 1
    assert refresh_requests[0].date_range_start == date(2026, 6, 4)
    assert refresh_requests[0].date_range_end == date(2026, 6, 4)
    assert all(request.universe_ref == "all_a_shares" for request in refresh_requests)
    assert all(request.symbol_id is None for request in refresh_requests)
    assert all(request.freshness_policy == "trading_day" for request in refresh_requests)
    assert all(request.date_range_start == request.date_range_end for request in refresh_requests)
    assert fake_api.calls[-1][0].request_id == main_request_id
    assert fake_api.calls[-1][0].freshness_policy == "warehouse_only"


def test_selection_gateway_refreshes_only_metadata_integrity_mismatch_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-integrity-range-refresh",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-04",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-06-04/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    fake_api = _SequencedFakeDataAPI(
        (
            (
                DataResult(
                    request_id=main_request_id,
                    status=DataResultStatus.MISSING,
                    rows=(),
                    dataset_refs=("dataset:daily_bar:CN_A:metadata-only",),
                    attempt_refs=(),
                    gaps=(
                        DataGap.by_reason(
                            "data_integrity_failed",
                            request_id=main_request_id,
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            message="local dataset batch count mismatch",
                            as_of=datetime(2026, 6, 4, tzinfo=UTC),
                        ),
                    ),
                    freshness={
                        "coverage_by_request": (
                            {
                                "request_id": main_request_id,
                                "actual_start": "2025-01-02",
                                "actual_end": "2026-06-04",
                                "expected_start": "2025-01-01",
                                "expected_end": "2026-06-04",
                                "missing_ranges": (),
                                "integrity_mismatch_ranges": (
                                    {"start": "2025-04-17", "end": "2025-04-17"},
                                ),
                            },
                        )
                    },
                    as_of=datetime(2026, 6, 4, tzinfo=UTC),
                ),
            ),
            (),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )

    fetch_gateway_selection_batch(plan)

    refresh_requests = tuple(request for call in fake_api.calls[1:-1] for request in call)
    assert [request.date_range_start for request in refresh_requests] == [date(2025, 4, 17)]
    assert [request.date_range_end for request in refresh_requests] == [date(2025, 4, 17)]
    assert all(request.universe_ref == "all_a_shares" for request in refresh_requests)
    assert all(request.symbol_id is None for request in refresh_requests)
    assert all(request.freshness_policy == "trading_day" for request in refresh_requests)
    assert fake_api.calls[-1][0].request_id == main_request_id
    assert fake_api.calls[-1][0].freshness_policy == "warehouse_only"


def test_selection_local_feature_rows_use_company_name_identity_index() -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-local-feature-identity",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    collections = {name: {} for name in DatasetRepository.collection_names()}
    repository = DatasetRepository(collections=collections)
    ticker = "600204.SH"
    trade_day = date.fromisoformat(plan.trade_date)
    for row in _history_rows_from(start=trade_day - timedelta(days=259), count=260, ticker=ticker):
        repository.insert_normalized(_selection_daily_dataset_record(ticker=ticker, row=row))
    collections["normalized_datasets"]["identity:600204"] = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": ticker,
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2024-01-01",
        "period_end": "2024-01-01",
        "row": {"company_name": "上海电力"},
    }
    progress_events = []

    result = _selection_feature_rows_from_repository(
        plan=plan,
        repository=repository,
        progress_callback=progress_events.append,
    )

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == ticker
    assert result.rows[0]["company_name"] == "上海电力"
    assert result.normalized_refs
    assert not any(gap.severity == DataGapSeverity.BLOCKER for gap in result.data_gaps)
    assert progress_events[-1].completed == 1
    assert progress_events[-1].total == 1


def test_selection_local_feature_rows_normalize_legacy_mongo_source_refs() -> None:
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-local-feature-legacy-ref",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    collections = {name: {} for name in DatasetRepository.collection_names()}
    repository = DatasetRepository(collections=collections)
    ticker = "600204.SH"
    trade_day = date.fromisoformat(plan.trade_date)
    for idx, row in enumerate(_history_rows_from(start=trade_day - timedelta(days=259), count=260, ticker=ticker)):
        record = _selection_daily_dataset_record(ticker=ticker, row=row)
        record["dataset_ref"] = f"normalized://mongo/normalized_datasets/dataset:daily_bar:CN_A:legacy-{idx}"
        repository.insert_normalized(record)
    collections["normalized_datasets"]["identity:600204"] = {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": ticker,
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2024-01-01",
        "period_end": "2024-01-01",
        "row": {"company_name": "上海电力"},
    }

    result = _selection_feature_rows_from_repository(plan=plan, repository=repository)

    assert len(result.rows) == 1
    assert result.rows[0]["source_ref"].startswith("dataset://normalized/")
    assert "normalized://mongo" not in str(result.rows[0]["source_ref"])
    assert all(ref.startswith("dataset://normalized/") for ref in result.normalized_refs)
    manifest = SelectionColumnarWarehouse.default().load_valid_manifest(plan=plan)
    assert manifest is not None
    stored_feature_rows = SelectionColumnarWarehouse.default().read_feature_rows(
        manifest=manifest,
        columns=("ticker", "source_ref"),
        row_limit=10,
    )
    assert stored_feature_rows
    assert all(str(row["source_ref"]).startswith("dataset://normalized/") for row in stored_feature_rows)
    assert not any("normalized://mongo" in str(row["source_ref"]) for row in stored_feature_rows)


def test_selection_local_feature_rows_stop_at_explicit_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claw_trade.data_gateway._selection_batch._SELECTION_FEATURE_ROW_LIMIT", 1)
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-local-feature-row-limit",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    collections = {name: {} for name in DatasetRepository.collection_names()}
    repository = DatasetRepository(collections=collections)
    trade_day = date.fromisoformat(plan.trade_date)
    for ticker, company_name in (("600204.SH", "上海电力"), ("600205.SH", "山东铝业")):
        for row in _history_rows_from(start=trade_day - timedelta(days=259), count=260, ticker=ticker):
            repository.insert_normalized(_selection_daily_dataset_record(ticker=ticker, row=row))
        collections["normalized_datasets"][f"identity:{ticker}"] = {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": ticker,
            "universe_ref": "all_a_shares",
            "granularity": "daily",
            "period_start": "2024-01-01",
            "period_end": "2024-01-01",
            "row": {"company_name": company_name},
        }

    result = _selection_feature_rows_from_repository(plan=plan, repository=repository)

    assert len(result.rows) == 1
    assert any(gap.gap_code == "selection_batch_feature_row_limit_exceeded" for gap in result.data_gaps)
    limit_gap = next(gap for gap in result.data_gaps if gap.gap_code == "selection_batch_feature_row_limit_exceeded")
    assert limit_gap.severity == DataGapSeverity.BLOCKER
    assert limit_gap.source_metadata is not None
    assert limit_gap.source_metadata["row_limit"] == 1
    assert limit_gap.source_metadata["rows_returned"] == 1


def test_selection_history_row_scales_legacy_tushare_amount_to_cny() -> None:
    row = _history_row(
        {
            "date": "2026-06-05",
            "open": 10.0,
            "high": 11.0,
            "low": 9.9,
            "close": 10.8,
            "volume": 1000000.0,
            "amount": 1102446.143,
            "market": "CN_A",
            "provider_lineage": {"provider_id": "cn_a_primary", "endpoint_id": "daily_bar_by_trade_date"},
        }
    )

    assert row is not None
    assert row["amount"] == 1102446143.0


def test_selection_gateway_records_history_backfill_failure_without_fake_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _selection_run_plan("sel-unit-history-backfill-fail")
    fake_api = _SequencedFakeDataAPI(
        (
            (
                DataResult(
                    request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                    status=DataResultStatus.READY,
                    rows=(
                        {
                            "ticker": "600204.SH",
                            "company_name": "完整历史样本",
                            "industry": "样本行业",
                            "history": _history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0),
                            "private_placement_event_date": "none",
                            "private_placement_days_since": 9999.0,
                        },
                        {
                            "ticker": "688981.SH",
                            "company_name": "中芯国际",
                            "industry": "半导体",
                            "history": _history_rows_from(start=date(2025, 12, 3), count=114, ticker="688981.SH"),
                            "private_placement_event_date": "none",
                            "private_placement_days_since": 9999.0,
                        },
                    ),
                    dataset_refs=("dataset:daily_bar:CN_A:prepackaged",),
                    attempt_refs=("attempt:local_a_share_prepackaged:daily_bar:prepackaged",),
                    as_of=datetime(2026, 5, 26, tzinfo=UTC),
                ),
            ),
            (
                DataResult(
                    request_id=f"{plan.selection_run_id}:selection:history_backfill:1:688981.SH:daily_bar",
                    status=DataResultStatus.MISSING,
                    rows=(),
                    attempt_refs=("attempt:cn_a_primary:daily_bar:688981-repair",),
                    gaps=(
                        DataGap.by_reason(
                            "warehouse_missing",
                            request_id=f"{plan.selection_run_id}:selection:history_backfill:1:688981.SH:daily_bar",
                            market=Market.CN_A,
                            data_type="daily_bar",
                            granularity="daily",
                            evidence_refs=("attempt:cn_a_primary:daily_bar:688981-repair",),
                            message="provider returned no historical repair rows",
                            as_of=datetime(2026, 5, 26, tzinfo=UTC),
                        ),
                    ),
                    as_of=datetime(2026, 5, 26, tzinfo=UTC),
                ),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._listing_dates_for_tickers",
        lambda tickers: {"688981.SH": date(2020, 7, 16)},
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "完整历史样本",
                    "industry": "样本行业",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:600204",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 17.77,
                    "amount": 300000000.0,
                },
            ),
            normalized_refs=("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:prepackaged",),
            attempt_refs=("attempt:local_a_share_prepackaged:daily_bar:prepackaged",),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert {str(row["ticker"]) for row in result.rows} == {"600204.SH"}
    assert len(fake_api.calls) == 1
    assert not any(gap.severity == DataGapSeverity.BLOCKER for gap in result.data_gaps)


def test_selection_gateway_joins_valuation_metric_rows_into_selection_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _selection_run_plan("sel-unit-valuation-join")
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.READY,
                rows=tuple(
                    {
                        **row,
                        "ticker": "600204.SH",
                        "company_name": "估值样本",
                        "industry": "样本行业",
                        "dataset_ref": f"dataset:daily_bar:CN_A:unit:{idx}",
                    }
                    for idx, row in enumerate(_history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0))
                ),
                dataset_refs=("dataset:daily_bar:CN_A:unit",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:unit",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:2:corporate_action",
                status=DataResultStatus.READY,
                rows=(),
                dataset_refs=("dataset:corporate_action:CN_A:unit",),
                attempt_refs=("attempt:cn_a_market:corporate_action:unit",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:4:valuation_metric",
                status=DataResultStatus.READY,
                rows=(
                    {
                        "ticker": "600204.SH",
                        "date": "2026-05-26",
                        "pe": 12.3,
                        "pb": 1.4,
                        "ps": 2.5,
                        "market_cap": 123456.0,
                        "dataset_ref": "dataset:valuation_metric:CN_A:unit",
                    },
                ),
                dataset_refs=("dataset:valuation_metric:CN_A:unit",),
                attempt_refs=("attempt:cn_a_tushare:valuation_metric:unit",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "估值样本",
                    "industry": "样本行业",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 17.77,
                    "amount": 300000000.0,
                    "pe": 12.3,
                    "pb": 1.4,
                    "ps": 2.5,
                    "market_cap": 123456.0,
                    "valuation_source_ref": "dataset://normalized/CN_A/daily/dataset:valuation_metric:CN_A:unit",
                },
            ),
            normalized_refs=(
                "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",
                "dataset://normalized/CN_A/daily/dataset:valuation_metric:CN_A:unit",
            ),
            attempt_refs=(
                "attempt:cn_a_primary:daily_bar:unit",
                "attempt:cn_a_tushare:valuation_metric:unit",
            ),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert len(result.rows) == 1
    assert result.rows[0]["pe"] == 12.3
    assert result.rows[0]["pb"] == 1.4
    assert result.rows[0]["ps"] == 2.5
    assert result.rows[0]["market_cap"] == 123456.0
    assert result.rows[0]["valuation_source_ref"] == "dataset://normalized/CN_A/daily/dataset:valuation_metric:CN_A:unit"


def test_selection_gateway_fetch_returns_data_api_gap_without_fake_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _selection_run_plan("sel-unit-data-api-gap")
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.MISSING,
                attempt_refs=("attempt:cn_a_primary:daily_bar:missing",),
                gaps=(
                    DataGap.by_reason(
                        "warehouse_missing",
                        request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                        market=Market.CN_A,
                        data_type="daily_bar",
                        granularity="daily",
                        evidence_refs=("attempt:cn_a_primary:daily_bar:missing",),
                        message="warehouse_missing",
                        as_of=datetime(2026, 5, 26, tzinfo=UTC),
                    ),
                ),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )

    result = fetch_gateway_selection_batch(plan)

    assert result.rows == ()
    assert result.normalized_refs == ()
    assert result.attempt_refs == ("attempt:cn_a_primary:daily_bar:missing",)
    assert result.warehouse_check_ref is None
    assert result.data_gaps[0].gap_code == "selection_data_api_warehouse_missing"
    assert result.data_gaps[0].severity == DataGapSeverity.BLOCKER


def test_selection_gateway_private_placement_missing_is_warn_not_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _selection_run_plan("sel-unit-private-placement-warn")
    fake_api = _FakeDataAPI(
        (
            DataResult(
                request_id=f"{plan.selection_run_id}:selection:1:daily_bar",
                status=DataResultStatus.READY,
                rows=tuple(
                    {
                        **row,
                        "ticker": "600204.SH",
                        "company_name": "定增缺口样本",
                        "industry": "样本行业",
                        "dataset_ref": f"dataset:daily_bar:CN_A:private-missing:{idx}",
                    }
                    for idx, row in enumerate(_history_rows(base_close=10.0, daily_step=0.03, latest_volume=3000000.0))
                ),
                dataset_refs=("dataset:daily_bar:CN_A:private-missing",),
                attempt_refs=("attempt:cn_a_primary:daily_bar:private-missing",),
                as_of=datetime(2026, 5, 26, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._build_selection_gateway_context",
        lambda: _FakeGateway(fake_api),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway._selection_batch._selection_feature_rows_from_repository",
        lambda **_kwargs: _columnar_feature_result(
            plan,
            rows=(
                {
                    "ticker": "600204.SH",
                    "company_name": "定增缺口样本",
                    "industry": "样本行业",
                    "source_ref": "dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:private-missing",
                    "trade_date": "2026-05-26",
                    "selection_features_materialized": True,
                    "close": 17.77,
                    "amount": 300000000.0,
                },
            ),
            normalized_refs=("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:private-missing",),
            attempt_refs=("attempt:cn_a_primary:daily_bar:private-missing",),
            data_gaps=(
                DataGapRef(
                    gap_id=f"{plan.selection_run_id}-private-placement-source-missing",
                    domain="selection",
                    gap_code="selection_private_placement_source_missing",
                    severity=DataGapSeverity.WARN,
                    attempt_refs=("attempt:cn_a_primary:daily_bar:private-missing",),
                    reader_message="private placement source missing",
                ),
            ),
        ),
    )

    result = fetch_gateway_selection_batch(plan)

    assert len(fake_api.calls) == 1
    assert [request.data_type for request in fake_api.calls[0]] == ["daily_bar"]
    assert len(result.rows) == 1
    private_gap = next(gap for gap in result.data_gaps if gap.gap_code == "selection_private_placement_source_missing")
    assert private_gap.severity == DataGapSeverity.WARN
    assert not any(gap.severity == DataGapSeverity.BLOCKER for gap in result.data_gaps)


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


def test_score_candidates_keeps_only_top20_scores() -> None:
    strategy = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    assert strategy is not None
    plan = SelectionRunPlan(
        selection_run_id="sel-unit-top20-only",
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
        for idx in range(200)
    )

    result = score_candidates(
        plan=plan,
        filtered=FilteredUniverse(rows=rows, decisions=()),
        strategy=strategy,
    )

    assert len(result.top20) == 20
    assert result.all_scores == ()


def _smoke_result(*, rows: int, gaps: tuple[DataGapRef, ...]) -> SelectionProviderBatchResult:
    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    mapped_rows = tuple({"ticker": f"60051{idx}.SH"} for idx in range(rows))
    return SelectionProviderBatchResult(
        provider_batch_plan=plan,
        attempt_refs=("attempt://data-provider/test",),
        normalized_refs=("dataset://normalized/CN_A/daily/test",),
        rows=mapped_rows,
        data_gaps=gaps,
    )


def _gap(*, gap_code: str, severity: DataGapSeverity) -> DataGapRef:
    return DataGapRef(
        gap_id=f"sel13-{gap_code}",
        domain="selection",
        gap_code=gap_code,
        severity=severity,
        attempt_refs=("attempt://data-provider/test",),
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
        attempt_refs=("attempt://data-provider/test",),
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


class _FakeDataAPI:
    def __init__(self, results: tuple[DataResult, ...]) -> None:
        self._results = results
        self.requests = ()
        self.calls: list[tuple[object, ...]] = []

    def get_data_batch(self, requests):
        self.requests = tuple(requests)
        self.calls.append(self.requests)
        return list(self._results)


class _SequencedFakeDataAPI:
    def __init__(self, result_batches: tuple[tuple[DataResult, ...], ...]) -> None:
        self._result_batches = list(result_batches)
        self.requests = ()
        self.calls: list[tuple[object, ...]] = []

    def get_data_batch(self, requests):
        self.requests = tuple(requests)
        self.calls.append(self.requests)
        if not self._result_batches:
            return []
        return list(self._result_batches.pop(0))


class _FakeGateway:
    def __init__(self, data_api: _FakeDataAPI, repository: object | None = None) -> None:
        self.data_api = data_api
        self.repository = repository
        self.provider_candidates = ("cn_a_primary",)


def _columnar_feature_result(
    plan: SelectionRunPlan,
    *,
    rows: tuple[dict[str, object], ...],
    normalized_refs: tuple[str, ...] = ("dataset://normalized/CN_A/daily/dataset:daily_bar:CN_A:unit",),
    attempt_refs: tuple[str, ...] = ("attempt:cn_a_primary:daily_bar:unit",),
    data_gaps: tuple[DataGapRef, ...] = (),
) -> _LocalFeatureRowsResult:
    if not rows:
        return _LocalFeatureRowsResult(
            rows=(),
            normalized_refs=normalized_refs,
            attempt_refs=attempt_refs,
            data_gaps=data_gaps,
        )
    feature_rows = tuple(
        {
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000000.0,
            "amount": 1000000.0,
            **row,
        }
        for row in rows
    )
    writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    writer.add_daily_rows(
        {
            "market": "CN_A",
            "profile": "CN_A",
            "selection_trade_date": plan.trade_date,
            "ticker": str(row.get("ticker")),
            "date": plan.trade_date,
            "close": float(row.get("close") or 10.0),
            "amount": float(row.get("amount") or 1000000.0),
            "source_ref": str(row.get("source_ref") or normalized_refs[0]),
        }
        for row in feature_rows
    )
    writer.add_feature_rows(feature_rows)
    manifest = writer.commit(
        provider_attempt_refs=attempt_refs,
        normalized_refs=normalized_refs,
        coverage_status=(
            "incomplete"
            if any(gap.severity == DataGapSeverity.BLOCKER for gap in data_gaps)
            else "verified"
        ),
        coverage_gap_codes=tuple(gap.gap_code for gap in data_gaps),
    )
    return _LocalFeatureRowsResult(
        rows=feature_rows,
        normalized_refs=normalized_refs,
        attempt_refs=attempt_refs,
        data_gaps=data_gaps,
        columnar_manifest_ref=manifest.manifest_ref,
        columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(manifest.manifest_ref),
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


def _history_rows_from(*, start: date, count: int, ticker: str | None = None) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for idx in range(count):
        close = 10.0 + idx * 0.01
        row: dict[str, object] = {
            "date": (start + timedelta(days=idx)).isoformat(),
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": 1000000.0 + idx,
            "amount": close * (1000000.0 + idx),
        }
        if ticker is not None:
            row["ticker"] = ticker
            row["symbol_id"] = ticker
        rows.append(row)
    return tuple(rows)


def _daily_bar_row(*, ticker: str, trade_date: str, close: float) -> dict[str, object]:
    return {
        "symbol_id": ticker,
        "ticker": ticker,
        "date": trade_date,
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": 2_000_000.0,
        "amount": close * 2_000_000.0,
    }


def _selection_daily_dataset_record(*, ticker: str, row: dict[str, object]) -> dict[str, object]:
    payload = dict(row)
    payload["symbol_id"] = ticker
    payload.setdefault("ticker", ticker)
    trade_date = str(payload["date"])
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": ticker,
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": trade_date,
        "period_end": trade_date,
        "field_set": tuple(sorted(payload)),
        "as_of": datetime(2026, 5, 26, tzinfo=UTC),
        "fresh_until": datetime(2026, 5, 26, tzinfo=UTC),
        "source_roles": ("paid_data",),
        "exchange": "SSE" if ticker.endswith(".SH") else "SZSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {"provider_id": "cn_a_primary", "endpoint_id": "daily_bar_by_trade_date"},
        "schema_id": "daily_bar.v1",
        "quality_flags": (),
        "row": payload,
    }
