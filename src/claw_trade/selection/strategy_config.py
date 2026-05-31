from __future__ import annotations

from claw_trade.selection.engine import (
    ApprovedSelectionStrategy,
    HardFilterRule,
    StableSortField,
    StableTop20Rule,
    StrategyCondition,
    StrategyRule,
)

CN_A_SELECTION_STRATEGY_CONFIG_REF = "config://cn-a-selection-v1"
CN_A_SELECTION_STRATEGY_COMPAT_REFS = frozenset(
    {
        CN_A_SELECTION_STRATEGY_CONFIG_REF,
        "config://cn-a-approved-v1",
    }
)

CN_A_SELECTION_V1_WEIGHT_VERSION = "cn_a.selection_weights.v1"
CN_A_SELECTION_V1_STRATEGY_CONFIG_VERSION = "cn_a.selection_strategy.v1"

CN_A_SELECTION_V1_WEIGHTS: dict[str, float] = {
    "strategy_hit_coverage_score": 30.0,
    "strategy_inner_strength_score": 25.0,
    "rps_trend_score": 20.0,
    "liquidity_tradability_score": 15.0,
    "industry_theme_score": 5.0,
    "evidence_completeness_score": 5.0,
    "risk_penalty_score": 20.0,
    "data_gap_penalty_score": 15.0,
}

_STRATEGY_VARIANTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "myhhub/stock",
        "myhhub_volume_rise",
        ("amount", "vol_ratio", "p_change_pct", "close_open_ratio"),
    ),
    (
        "myhhub/stock",
        "myhhub_ma30_keep_increasing",
        ("ma30", "ma30_slope_5d", "ma30_slope_10d", "ma30_growth_30d"),
    ),
    (
        "myhhub/stock",
        "myhhub_parking_apron",
        (
            "limit_up_recent",
            "post_limit_up_range_pct",
            "post_limit_up_return_abs_pct",
            "post_limit_up_window_days",
        ),
    ),
    (
        "myhhub/stock",
        "myhhub_backtrace_ma250",
        ("ma250", "ma250_backtrace_days", "ma250_back_ratio", "vol_ratio"),
    ),
    (
        "myhhub/stock",
        "myhhub_breakthrough_platform",
        ("open", "close", "ma60", "platform_deviation_pct"),
    ),
    ("myhhub/stock", "myhhub_low_backtrace_increase", ("return_60d", "single_day_min_return_60d")),
    ("myhhub/stock", "myhhub_turtle_60_close", ("close", "highest_close_60d")),
    (
        "myhhub/stock",
        "myhhub_high_tight_flag",
        ("limit_up_count_20d", "return_40d", "limit_up_streak_2d"),
    ),
    ("myhhub/stock", "myhhub_climax_limitdown", ("limit_down_today", "amount", "vol_ratio")),
    ("myhhub/stock", "myhhub_low_atr", ("atr_14", "ma250", "range_pct")),
    ("Sequoia-X", "sequoia_ma_volume", ("prev_ma5", "prev_ma20", "ma5", "ma20", "volume_ma20", "volume")),
    ("Sequoia-X", "sequoia_turtle_20_high", ("high_20d", "close", "open", "prev_close", "amount")),
    (
        "Sequoia-X",
        "sequoia_high_tight_flag",
        ("return_40d", "range_10d", "low_10d", "high_40d", "volume", "volume_ma20"),
    ),
    (
        "Sequoia-X",
        "sequoia_limit_up_shakeout",
        ("limit_up_yesterday", "close_open_ratio", "volume", "prev_volume", "low", "prev_close"),
    ),
    (
        "Sequoia-X",
        "sequoia_uptrend_limit_down",
        ("prev_ma20", "prev_ma60", "close", "prev_close", "volume", "volume_ma20"),
    ),
    ("Sequoia-X", "sequoia_rps_breakout", ("rps120", "close", "roll_high_120d")),
    ("Sequoia-X", "sequoia_private_placement", ("private_placement_event_date", "private_placement_days_since")),
)


def load_cn_a_selection_v1_strategy(config_ref: str) -> ApprovedSelectionStrategy | None:
    normalized_ref = config_ref.strip()
    if normalized_ref not in CN_A_SELECTION_STRATEGY_COMPAT_REFS:
        return None
    return ApprovedSelectionStrategy(
        config_ref=normalized_ref,
        hard_filters=(
            HardFilterRule(name="amount_ge_2e8", field="amount", operator=">=", value=200000000),
        ),
        strategy_set=tuple(_strategy_rule(source=source, variant_id=variant_id, fields=fields) for source, variant_id, fields in _STRATEGY_VARIANTS),
        weights=CN_A_SELECTION_V1_WEIGHTS,
        stable_top20_rule=StableTop20Rule(
            score_field="score",
            tie_break_fields=(
                StableSortField(field="strategy_hit_count", descending=True),
                StableSortField(field="liquidity_tradability_score", descending=True),
                StableSortField(field="rps_trend_score", descending=True),
                StableSortField(field="amount", descending=True),
                StableSortField(field="data_gap_penalty_score", descending=False),
                StableSortField(field="risk_penalty_score", descending=False),
            ),
            missing_policy="fail",
        ),
    )


def load_cn_a_selection_v1_strategy_config_ref(market: object, profile: object) -> str | None:
    if getattr(market, "value", market) == "CN_A" and getattr(profile, "value", profile) == "CN_A":
        return CN_A_SELECTION_STRATEGY_CONFIG_REF
    return None


def cn_a_selection_v1_variant_ids() -> tuple[str, ...]:
    return tuple(variant_id for _source, variant_id, _fields in _STRATEGY_VARIANTS)


def _strategy_rule(*, source: str, variant_id: str, fields: tuple[str, ...]) -> StrategyRule:
    signal_field = f"strategy_signal_{variant_id}"
    return StrategyRule(
        name=variant_id,
        all_of=(
            StrategyCondition(field=signal_field, operator=">=", value=1.0),
        ),
        source=source,
        required_fields=(signal_field, *fields),
    )
