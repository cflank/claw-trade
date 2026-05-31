from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Sequence

from claw_trade.selection.models import DataGapRef, DataGapSeverity, SelectionRunPlan


class SelectionFeatureError(ValueError):
    def __init__(self, code: str, reason: str, *, data_gaps: tuple[DataGapRef, ...] = ()) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.data_gaps = data_gaps


@dataclass(frozen=True)
class SelectionNormalizedRow:
    ticker: str
    company_name: str
    industry: str | None
    numeric_fields: Mapping[str, float]
    source_ref: str
    text_fields: Mapping[str, str] = field(default_factory=dict)
    history_rows: tuple[Mapping[str, float | str], ...] = ()


@dataclass(frozen=True)
class SelectionNormalizedInputs:
    normalized_refs: tuple[str, ...]
    rows: tuple[SelectionNormalizedRow, ...]
    data_gaps: tuple[DataGapRef, ...]


@dataclass(frozen=True)
class FeatureRow:
    ticker: str
    company_name: str
    industry: str | None
    feature_values: Mapping[str, float | str]
    source_ref: str


@dataclass(frozen=True)
class FeatureSnapshot:
    feature_snapshot_ref: str
    rows: tuple[FeatureRow, ...]


_STRATEGY_SIGNAL_FIELDS = (
    "strategy_signal_myhhub_volume_rise",
    "strategy_signal_myhhub_ma30_keep_increasing",
    "strategy_signal_myhhub_parking_apron",
    "strategy_signal_myhhub_backtrace_ma250",
    "strategy_signal_myhhub_breakthrough_platform",
    "strategy_signal_myhhub_low_backtrace_increase",
    "strategy_signal_myhhub_turtle_60_close",
    "strategy_signal_myhhub_high_tight_flag",
    "strategy_signal_myhhub_climax_limitdown",
    "strategy_signal_myhhub_low_atr",
    "strategy_signal_sequoia_ma_volume",
    "strategy_signal_sequoia_turtle_20_high",
    "strategy_signal_sequoia_high_tight_flag",
    "strategy_signal_sequoia_limit_up_shakeout",
    "strategy_signal_sequoia_uptrend_limit_down",
    "strategy_signal_sequoia_rps_breakout",
    "strategy_signal_sequoia_private_placement",
)


def normalize_selection_inputs(
    *,
    plan: SelectionRunPlan,
    raw_rows: tuple[Mapping[str, object], ...],
    normalized_refs: tuple[str, ...],
    attempt_refs: tuple[str, ...],
    upstream_gaps: tuple[DataGapRef, ...] = (),
) -> SelectionNormalizedInputs:
    if not normalized_refs:
        raise SelectionFeatureError(
            "selection_inputs_insufficient",
            "normalized refs 缺失",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-normalized-refs-missing",
                    gap_code="normalized_refs_missing",
                    attempt_refs=attempt_refs,
                    reader_message="标准化数据引用缺失，无法进入特征计算。",
                ),
            ),
        )
    rows: list[SelectionNormalizedRow] = []
    seen_tickers: dict[str, str] = {}
    for idx, row in enumerate(raw_rows):
        ticker = str(row.get("ticker", "")).strip()
        company_name = str(row.get("company_name", "")).strip()
        source_ref = str(row.get("source_ref", normalized_refs[min(idx, len(normalized_refs) - 1)])).strip()
        if not ticker or not company_name or not source_ref:
            raise SelectionFeatureError(
                "selection_inputs_insufficient",
                "核心字段缺失",
                data_gaps=(
                    _blocker_gap(
                        gap_id=f"{plan.selection_run_id}-core-field-missing-{idx}",
                        gap_code="core_field_missing",
                        attempt_refs=attempt_refs,
                        reader_message="标准化数据缺少 ticker/company/source 核心字段。",
                    ),
                ),
            )
        previous_company = seen_tickers.get(ticker)
        if previous_company is not None:
            gap_code = "ticker_company_mismatch" if previous_company != company_name else "duplicate_ticker"
            raise SelectionFeatureError(
                "selection_inputs_insufficient",
                "ticker 重复或 ticker/company 不匹配",
                data_gaps=(
                    _blocker_gap(
                        gap_id=f"{plan.selection_run_id}-{gap_code}-{ticker}",
                        gap_code=gap_code,
                        attempt_refs=attempt_refs,
                        reader_message=(
                            f"标准化数据中 {ticker} 出现重复或名称冲突："
                            f"first={previous_company}, current={company_name}。"
                        ),
                    ),
                ),
            )
        seen_tickers[ticker] = company_name
        numeric_fields: dict[str, float] = {}
        for key, value in row.items():
            if key in {"ticker", "company_name", "industry", "source_ref", "history"}:
                continue
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int | float):
                numeric_fields[key] = float(value)
                continue
            try:
                numeric_fields[key] = float(str(value))
            except ValueError:
                continue
        text_fields: dict[str, str] = {}
        for key in ("private_placement_event_date", "private_placement_source_ref"):
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                text_fields[key] = text
        rows.append(
            SelectionNormalizedRow(
                ticker=ticker,
                company_name=company_name,
                industry=str(row.get("industry")).strip() if row.get("industry") is not None else None,
                numeric_fields=numeric_fields,
                text_fields=text_fields,
                history_rows=_normalize_history_rows(row.get("history")),
                source_ref=source_ref,
            )
        )
    if not rows:
        raise SelectionFeatureError(
            "selection_inputs_insufficient",
            "无可用标准化行",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-rows-empty",
                    gap_code="normalized_rows_empty",
                    attempt_refs=attempt_refs,
                    reader_message="标准化结果为空，无法构建特征快照。",
                ),
            ),
        )
    return SelectionNormalizedInputs(
        normalized_refs=normalized_refs,
        rows=tuple(rows),
        data_gaps=upstream_gaps,
    )


def build_feature_snapshot(*, plan: SelectionRunPlan, inputs: SelectionNormalizedInputs) -> FeatureSnapshot:
    feature_rows: list[FeatureRow] = []
    for row in inputs.rows:
        feature_values: dict[str, float | str] = dict(row.numeric_fields)
        feature_values.update(row.text_fields)
        close_value = row.numeric_fields.get("close")
        open_value = row.numeric_fields.get("open")
        high_value = row.numeric_fields.get("high")
        low_value = row.numeric_fields.get("low")
        if open_value is not None and open_value != 0 and close_value is not None:
            feature_values["close_open_ratio"] = close_value / open_value
            feature_values["intraday_return_pct"] = (close_value - open_value) / open_value * 100.0
        if high_value is not None and low_value is not None and low_value > 0:
            feature_values["intraday_range_pct"] = (high_value - low_value) / low_value * 100.0
            feature_values.setdefault("range_pct", feature_values["intraday_range_pct"])
        feature_values.update(_derive_history_features(row.history_rows))
        feature_rows.append(
            FeatureRow(
                ticker=row.ticker,
                company_name=row.company_name,
                industry=row.industry,
                feature_values=feature_values,
                source_ref=row.source_ref,
            )
        )
    if not feature_rows:
        raise SelectionFeatureError(
            "selection_inputs_insufficient",
            "特征快照为空",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-feature-empty",
                    gap_code="feature_snapshot_empty",
                    attempt_refs=("attempt://feature",),
                    reader_message="标准化数据无法生成任何特征。",
                ),
            ),
        )
    feature_rows = _attach_rps_fields(tuple(feature_rows))
    feature_rows = tuple(
        FeatureRow(
            ticker=row.ticker,
            company_name=row.company_name,
            industry=row.industry,
            feature_values={**row.feature_values, **_derive_strategy_signals(row.feature_values)},
            source_ref=row.source_ref,
        )
        for row in feature_rows
    )
    return FeatureSnapshot(
        feature_snapshot_ref=f"feature://{plan.selection_run_id}",
        rows=feature_rows,
    )


def _derive_history_features(history_rows: tuple[Mapping[str, float | str], ...]) -> dict[str, float]:
    bars = [row for row in history_rows if _has_ohlc(row)]
    if not bars:
        return {}
    bars = sorted(bars, key=lambda item: str(item.get("date") or ""))
    closes = [_float_value(row.get("close")) for row in bars]
    highs = [_float_value(row.get("high")) for row in bars]
    lows = [_float_value(row.get("low")) for row in bars]
    opens = [_float_value(row.get("open")) for row in bars]
    volumes = [_float_value(row.get("volume")) for row in bars]
    amounts = [_float_value(row.get("amount")) for row in bars]
    if not closes or closes[-1] is None:
        return {}
    result: dict[str, float] = {}
    latest_close = closes[-1]
    latest_open = opens[-1]
    latest_high = highs[-1]
    latest_low = lows[-1]
    latest_volume = volumes[-1]
    latest_amount = amounts[-1]
    if latest_open is not None:
        result["open"] = latest_open
    if latest_high is not None:
        result["high"] = latest_high
    if latest_low is not None:
        result["low"] = latest_low
    result["close"] = latest_close
    if latest_volume is not None:
        result["volume"] = latest_volume
    if latest_amount is not None:
        result["amount"] = latest_amount
    if latest_open is not None and latest_open != 0:
        result["close_open_ratio"] = latest_close / latest_open
        result["intraday_return_pct"] = (latest_close - latest_open) / latest_open * 100.0
    if latest_high is not None and latest_low is not None and latest_low > 0:
        result["intraday_range_pct"] = (latest_high - latest_low) / latest_low * 100.0

    p_changes = _pct_change_series(bars=bars, closes=closes)
    if p_changes and p_changes[-1] is not None:
        result["p_change_pct"] = p_changes[-1]
    if len(closes) >= 2 and closes[-2] is not None:
        result["prev_close"] = closes[-2]
    if len(volumes) >= 2 and volumes[-2] is not None:
        result["prev_volume"] = volumes[-2]
    if latest_volume is not None:
        volume_base = _mean([value for value in volumes[-6:-1] if value is not None])
        if volume_base is not None and volume_base > 0:
            result["vol_ratio"] = latest_volume / volume_base
    for window in (5, 10, 20, 30, 60, 120, 250):
        value = _window_mean(closes, window)
        if value is not None:
            result[f"ma{window}"] = value
    for window in (5, 20, 60):
        value = _window_mean(closes[:-1], window)
        if value is not None:
            result[f"prev_ma{window}"] = value
    volume_ma20 = _window_mean(volumes[:-1], 20)
    if volume_ma20 is not None:
        result["volume_ma20"] = volume_ma20
    ma30_current = result.get("ma30")
    if isinstance(ma30_current, float):
        ma30_5 = _window_mean(closes[:-5], 30) if len(closes) > 35 else None
        ma30_10 = _window_mean(closes[:-10], 30) if len(closes) > 40 else None
        ma30_30 = _window_mean(closes[:-30], 30) if len(closes) > 60 else None
        if ma30_5 is not None:
            result["ma30_slope_5d"] = ma30_current - ma30_5
        if ma30_10 is not None:
            result["ma30_slope_10d"] = ma30_current - ma30_10
        if ma30_30 is not None and ma30_30 > 0:
            result["ma30_growth_30d"] = (ma30_current - ma30_30) / ma30_30 * 100.0

    for window in (40, 60, 120):
        return_value = _window_return(closes, window)
        if return_value is not None:
            result[f"return_{window}d"] = return_value
    min_return_60 = _min_recent_change(p_changes, 60)
    if min_return_60 is not None:
        result["single_day_min_return_60d"] = min_return_60
    highest_close_60 = _window_max(closes, 60)
    if highest_close_60 is not None:
        result["highest_close_60d"] = highest_close_60
    high_20 = _window_max(highs[:-1], 20)
    if high_20 is not None:
        result["high_20d"] = high_20
    roll_high_120 = _window_max(closes, 120)
    if roll_high_120 is not None:
        result["roll_high_120d"] = roll_high_120
    high_40 = _window_max(highs, 40)
    low_40 = _window_min(lows, 40)
    if high_40 is not None:
        result["high_40d"] = high_40
    if low_40 is not None:
        result["low_40d"] = low_40
    high_10 = _window_max(highs, 10)
    low_10 = _window_min(lows, 10)
    if high_10 is not None:
        result["high_10d"] = high_10
    if low_10 is not None:
        result["low_10d"] = low_10
    if high_10 is not None and low_10 is not None and low_10 > 0:
        result["range_10d"] = (high_10 - low_10) / low_10 * 100.0
    history_high = _window_max(closes, len(closes))
    history_low = _window_min(closes, len(closes))
    if history_high is not None and history_low is not None and history_low > 0:
        result["range_pct"] = (history_high - history_low) / history_low * 100.0

    result.update(_limit_features(bars=bars, p_changes=p_changes))
    result.update(_parking_apron_features(bars=bars, p_changes=p_changes))
    result.update(_backtrace_ma250_features(bars=bars, closes=closes, volumes=volumes))
    result.update(_platform_features(result))
    result.update(_atr_features(bars=bars, closes=closes, p_changes=p_changes))
    return result


def _attach_rps_fields(rows: tuple[FeatureRow, ...]) -> tuple[FeatureRow, ...]:
    if not rows:
        return rows
    by_window: dict[str, dict[str, float]] = {}
    for window in ("60", "120"):
        values: list[tuple[str, float]] = []
        field = f"return_{window}d"
        for row in rows:
            value = _float_value(row.feature_values.get(field))
            if value is not None:
                values.append((row.ticker, value))
        values.sort(key=lambda item: (item[1], item[0]))
        denominator = max(1, len(values))
        ranks = {ticker: (rank + 1) / denominator * 100.0 for rank, (ticker, _value) in enumerate(values)}
        by_window[window] = ranks
    updated: list[FeatureRow] = []
    for row in rows:
        feature_values = dict(row.feature_values)
        for window, ranks in by_window.items():
            value = ranks.get(row.ticker)
            if value is not None:
                feature_values[f"rps{window}"] = value
        updated.append(
            FeatureRow(
                ticker=row.ticker,
                company_name=row.company_name,
                industry=row.industry,
                feature_values=feature_values,
                source_ref=row.source_ref,
            )
        )
    return tuple(updated)


def _derive_strategy_signals(feature_values: Mapping[str, float | str]) -> dict[str, float]:
    signals: dict[str, float] = {field: 0.0 for field in _STRATEGY_SIGNAL_FIELDS}
    if _all_present(feature_values, ("amount", "vol_ratio", "p_change_pct", "close_open_ratio")) and (
        _num(feature_values["p_change_pct"]) >= 2.0
        and feature_values["close_open_ratio"] >= 1.0
        and _num(feature_values["amount"]) >= 200000000.0
        and _num(feature_values["vol_ratio"]) >= 2.0
    ):
        signals["strategy_signal_myhhub_volume_rise"] = 1.0
    if _all_present(feature_values, ("ma30", "ma30_slope_5d", "ma30_slope_10d", "ma30_growth_30d")) and (
        feature_values["ma30_slope_5d"] > 0.0
        and feature_values["ma30_slope_10d"] > 0.0
        and feature_values["ma30_growth_30d"] >= 20.0
    ):
        signals["strategy_signal_myhhub_ma30_keep_increasing"] = 1.0
    if _all_present(
        feature_values,
        (
            "limit_up_recent",
            "post_limit_up_range_pct",
            "post_limit_up_return_abs_pct",
            "post_limit_up_window_days",
        ),
    ) and (
        feature_values["limit_up_recent"] >= 1.0
        and feature_values["post_limit_up_window_days"] >= 3.0
        and feature_values["post_limit_up_range_pct"] <= 3.0
        and feature_values["post_limit_up_return_abs_pct"] <= 5.0
    ):
        signals["strategy_signal_myhhub_parking_apron"] = 1.0
    if _all_present(feature_values, ("ma250", "ma250_backtrace_days", "ma250_back_ratio", "vol_ratio")) and (
        10.0 <= feature_values["ma250_backtrace_days"] <= 50.0
        and feature_values["ma250_back_ratio"] < 0.8
        and feature_values["vol_ratio"] > 2.0
    ):
        signals["strategy_signal_myhhub_backtrace_ma250"] = 1.0
    if _all_present(feature_values, ("open", "close", "ma60", "platform_deviation_pct")) and (
        feature_values["open"] < feature_values["ma60"] <= feature_values["close"]
        and -5.0 <= feature_values["platform_deviation_pct"] <= 20.0
    ):
        signals["strategy_signal_myhhub_breakthrough_platform"] = 1.0
    if _all_present(feature_values, ("return_60d", "single_day_min_return_60d")) and (
        feature_values["return_60d"] >= 60.0 and feature_values["single_day_min_return_60d"] > -7.0
    ):
        signals["strategy_signal_myhhub_low_backtrace_increase"] = 1.0
    if _all_present(feature_values, ("close", "highest_close_60d")) and (
        feature_values["close"] >= feature_values["highest_close_60d"]
    ):
        signals["strategy_signal_myhhub_turtle_60_close"] = 1.0
    if _all_present(feature_values, ("limit_up_count_20d", "return_40d", "limit_up_streak_2d")) and (
        feature_values["limit_up_count_20d"] >= 2.0
        and feature_values["return_40d"] >= 90.0
        and feature_values["limit_up_streak_2d"] >= 1.0
    ):
        signals["strategy_signal_myhhub_high_tight_flag"] = 1.0
    if _all_present(feature_values, ("limit_down_today", "amount", "vol_ratio")) and (
        feature_values["limit_down_today"] >= 1.0
        and feature_values["amount"] >= 200000000.0
        and feature_values["vol_ratio"] >= 4.0
    ):
        signals["strategy_signal_myhhub_climax_limitdown"] = 1.0
    if _all_present(feature_values, ("atr_14", "ma250", "range_pct")) and (
        feature_values["ma250"] > 0.0
        and feature_values["atr_14"] <= 10.0
        and feature_values["range_pct"] > 110.0
    ):
        signals["strategy_signal_myhhub_low_atr"] = 1.0
    if _all_present(feature_values, ("prev_ma5", "prev_ma20", "ma5", "ma20", "volume_ma20", "volume")) and (
        feature_values["prev_ma5"] <= feature_values["prev_ma20"]
        and feature_values["ma5"] > feature_values["ma20"]
        and feature_values["volume"] > 1.5 * feature_values["volume_ma20"]
    ):
        signals["strategy_signal_sequoia_ma_volume"] = 1.0
    if _all_present(feature_values, ("high_20d", "close", "open", "prev_close", "amount")) and (
        feature_values["close"] > feature_values["high_20d"]
        and feature_values["amount"] > 100000000.0
        and feature_values["close"] > feature_values["open"]
        and feature_values["close"] > feature_values["prev_close"]
    ):
        signals["strategy_signal_sequoia_turtle_20_high"] = 1.0
    if _all_present(feature_values, ("return_40d", "range_10d", "low_10d", "high_40d", "volume", "volume_ma20")) and (
        feature_values["return_40d"] > 60.0
        and feature_values["range_10d"] < 15.0
        and feature_values["low_10d"] >= 0.8 * feature_values["high_40d"]
        and feature_values["volume"] < 0.6 * feature_values["volume_ma20"]
    ):
        signals["strategy_signal_sequoia_high_tight_flag"] = 1.0
    if _all_present(
        feature_values,
        ("limit_up_yesterday", "close_open_ratio", "volume", "prev_volume", "low", "prev_close"),
    ) and (
        feature_values["limit_up_yesterday"] >= 1.0
        and feature_values["close_open_ratio"] < 1.0
        and feature_values["volume"] > 2.0 * feature_values["prev_volume"]
        and feature_values["low"] >= feature_values["prev_close"]
    ):
        signals["strategy_signal_sequoia_limit_up_shakeout"] = 1.0
    if _all_present(feature_values, ("prev_ma20", "prev_ma60", "close", "prev_close", "volume", "volume_ma20")) and (
        feature_values["prev_ma20"] > feature_values["prev_ma60"]
        and feature_values["close"] <= 0.905 * feature_values["prev_close"]
        and feature_values["volume"] > 2.0 * feature_values["volume_ma20"]
    ):
        signals["strategy_signal_sequoia_uptrend_limit_down"] = 1.0
    if _all_present(feature_values, ("rps120", "close", "roll_high_120d")) and (
        feature_values["rps120"] >= 90.0 and feature_values["close"] >= 0.9 * feature_values["roll_high_120d"]
    ):
        signals["strategy_signal_sequoia_rps_breakout"] = 1.0
    if _all_present(feature_values, ("private_placement_days_since",)) and (
        0.0 <= feature_values["private_placement_days_since"] <= 7.0
    ):
        signals["strategy_signal_sequoia_private_placement"] = 1.0
    return signals


def _normalize_history_rows(raw_history: object) -> tuple[Mapping[str, float | str], ...]:
    if not isinstance(raw_history, Sequence) or isinstance(raw_history, (str, bytes, bytearray)):
        return ()
    rows: list[Mapping[str, float | str]] = []
    for item in raw_history:
        if not isinstance(item, Mapping):
            continue
        mapped: dict[str, float | str] = {}
        date_text = _date_text(_pick(item, "date", "trade_date", "日期"))
        if date_text:
            mapped["date"] = date_text
        aliases = {
            "open": ("open", "开盘", "今开"),
            "high": ("high", "最高"),
            "low": ("low", "最低"),
            "close": ("close", "收盘", "最新价", "现价"),
            "volume": ("volume", "vol", "成交量", "总手"),
            "amount": ("amount", "turnover", "成交额", "成交额(元)"),
            "p_change_pct": ("p_change_pct", "pct_chg", "涨跌幅"),
        }
        for field, candidates in aliases.items():
            value = _float_value(_pick(item, *candidates))
            if value is not None:
                mapped[field] = value
        if mapped:
            rows.append(mapped)
    return tuple(rows)


def _pick(row: Mapping[str, object], *candidates: str) -> object | None:
    for key in candidates:
        if key in row:
            return row[key]
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in candidates:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _date_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _has_ohlc(row: Mapping[str, float | str]) -> bool:
    return all(_float_value(row.get(field)) is not None for field in ("open", "high", "low", "close"))


def _float_value(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _num(value: float | str | object) -> float:
    parsed = _float_value(value)
    return parsed if parsed is not None else 0.0


def _mean(values: Sequence[float]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _window_mean(values: Sequence[float | None], window: int) -> float | None:
    if len(values) < window:
        return None
    window_values = [value for value in values[-window:] if value is not None]
    if len(window_values) != window:
        return None
    return sum(window_values) / window


def _window_max(values: Sequence[float | None], window: int) -> float | None:
    if len(values) < window:
        return None
    window_values = [value for value in values[-window:] if value is not None]
    if not window_values:
        return None
    return max(window_values)


def _window_min(values: Sequence[float | None], window: int) -> float | None:
    if len(values) < window:
        return None
    window_values = [value for value in values[-window:] if value is not None]
    if not window_values:
        return None
    return min(window_values)


def _window_return(closes: Sequence[float | None], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    start = closes[-window - 1]
    end = closes[-1]
    if start is None or end is None or start <= 0:
        return None
    return (end - start) / start * 100.0


def _pct_change_series(
    *,
    bars: Sequence[Mapping[str, float | str]],
    closes: Sequence[float | None],
) -> list[float | None]:
    changes: list[float | None] = []
    for idx, row in enumerate(bars):
        explicit = _float_value(row.get("p_change_pct"))
        if explicit is not None:
            changes.append(explicit)
            continue
        if idx == 0:
            changes.append(None)
            continue
        previous = closes[idx - 1]
        current = closes[idx]
        if previous is None or current is None or previous <= 0:
            changes.append(None)
        else:
            changes.append((current - previous) / previous * 100.0)
    return changes


def _min_recent_change(changes: Sequence[float | None], window: int) -> float | None:
    if len(changes) < window:
        return None
    values = [value for value in changes[-window:] if value is not None]
    if not values:
        return None
    return min(values)


def _limit_features(
    *,
    bars: Sequence[Mapping[str, float | str]],
    p_changes: Sequence[float | None],
) -> dict[str, float]:
    _ = bars
    recent_20 = [value for value in p_changes[-20:] if value is not None]
    recent_15 = [value for value in p_changes[-15:] if value is not None]
    limit_up_flags = [value >= 9.5 for value in recent_20]
    streak_2d = any(left and right for left, right in zip(limit_up_flags, limit_up_flags[1:], strict=False))
    return {
        "limit_down_today": 1.0 if p_changes and p_changes[-1] is not None and p_changes[-1] <= -9.5 else 0.0,
        "limit_up_yesterday": 1.0 if len(p_changes) >= 2 and p_changes[-2] is not None and p_changes[-2] >= 9.5 else 0.0,
        "limit_up_recent": 1.0 if any(value >= 9.5 for value in recent_15) else 0.0,
        "limit_up_count_20d": float(sum(1 for value in recent_20 if value >= 9.5)),
        "limit_up_streak_2d": 1.0 if streak_2d else 0.0,
    }


def _parking_apron_features(
    *,
    bars: Sequence[Mapping[str, float | str]],
    p_changes: Sequence[float | None],
) -> dict[str, float]:
    result = {
        "post_limit_up_range_pct": 999.0,
        "post_limit_up_return_abs_pct": 999.0,
        "post_limit_up_window_days": 0.0,
    }
    start = max(0, len(bars) - 15)
    for idx in range(len(bars) - 4, start - 1, -1):
        if idx < 0 or idx >= len(p_changes) or p_changes[idx] is None or p_changes[idx] < 9.5:
            continue
        after = bars[idx + 1 : idx + 4]
        if len(after) < 3:
            continue
        range_values: list[float] = []
        return_values: list[float] = []
        for after_idx, after_row in enumerate(after, start=idx + 1):
            open_value = _float_value(after_row.get("open"))
            close_value = _float_value(after_row.get("close"))
            if open_value is not None and open_value > 0 and close_value is not None:
                range_values.append(abs(close_value - open_value) / open_value * 100.0)
            change = p_changes[after_idx] if after_idx < len(p_changes) else None
            if change is not None:
                return_values.append(abs(change))
        result["post_limit_up_range_pct"] = max(range_values) if range_values else 999.0
        result["post_limit_up_return_abs_pct"] = max(return_values) if return_values else 999.0
        result["post_limit_up_window_days"] = float(len(after))
        break
    return result


def _backtrace_ma250_features(
    *,
    bars: Sequence[Mapping[str, float | str]],
    closes: Sequence[float | None],
    volumes: Sequence[float | None],
) -> dict[str, float]:
    result = {
        "ma250_backtrace_days": -1.0,
        "ma250_back_ratio": 999.0,
    }
    if len(closes) < 250:
        return {}
    recent_start = max(0, len(closes) - 60)
    recent = [(idx, value) for idx, value in enumerate(closes[recent_start:], start=recent_start) if value is not None]
    if not recent:
        return result
    high_idx, high_close = max(recent, key=lambda item: item[1])
    after = [(idx, value) for idx, value in enumerate(closes[high_idx + 1 :], start=high_idx + 1) if value is not None]
    if not after or high_close <= 0:
        return result
    low_idx, low_close = min(after, key=lambda item: item[1])
    result["ma250_backtrace_days"] = float(low_idx - high_idx)
    result["ma250_back_ratio"] = low_close / high_close
    high_volume = volumes[high_idx] if high_idx < len(volumes) else None
    low_volume = volumes[low_idx] if low_idx < len(volumes) else None
    if high_volume is not None and low_volume is not None and low_volume > 0:
        result["ma250_high_low_volume_ratio"] = high_volume / low_volume
    _ = bars
    return result


def _platform_features(feature_values: Mapping[str, float | str]) -> dict[str, float]:
    prev_close = _float_value(feature_values.get("prev_close"))
    prev_ma60 = _float_value(feature_values.get("prev_ma60"))
    if prev_close is None or prev_ma60 is None or prev_ma60 <= 0:
        return {}
    return {"platform_deviation_pct": (prev_close - prev_ma60) / prev_ma60 * 100.0}


def _atr_features(
    *,
    bars: Sequence[Mapping[str, float | str]],
    closes: Sequence[float | None],
    p_changes: Sequence[float | None],
) -> dict[str, float]:
    result: dict[str, float] = {}
    abs_changes = [abs(value) for value in p_changes[-10:] if value is not None]
    if len(abs_changes) >= 10:
        result["avg_abs_return_10d"] = sum(abs_changes) / len(abs_changes)
    true_ranges: list[float] = []
    start = max(1, len(bars) - 14)
    for idx in range(start, len(bars)):
        high = _float_value(bars[idx].get("high"))
        low = _float_value(bars[idx].get("low"))
        prev_close = closes[idx - 1] if idx - 1 < len(closes) else None
        if high is None or low is None or prev_close is None or prev_close <= 0:
            continue
        true_range = max(high - low, abs(high - prev_close), abs(low - prev_close)) / prev_close * 100.0
        true_ranges.append(true_range)
    if true_ranges:
        result["atr_14"] = sum(true_ranges) / len(true_ranges)
    return result


def _all_present(feature_values: Mapping[str, float | str], fields: tuple[str, ...]) -> bool:
    return all(feature_values.get(field) is not None for field in fields)


def _blocker_gap(
    *,
    gap_id: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    reader_message: str,
) -> DataGapRef:
    return DataGapRef(
        gap_id=gap_id,
        domain="selection",
        gap_code=gap_code,
        severity=DataGapSeverity.BLOCKER,
        attempt_refs=attempt_refs if attempt_refs else ("attempt://unknown",),
        reader_message=reader_message,
    )
