from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from ..input_contract import CryptoLensInput
from ..output_contract import AnalysisSection, AnalysisState
from .readiness import state_from_domain_status


_VEGAS_BLUE_BAND_MIN_CANDLES = 169
_VEGAS_PURPLE_BAND_MIN_CANDLES = 676


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _extract_rows(ohlcv: Mapping[str, Any]) -> int | None:
    rows = ohlcv.get("rows")
    if isinstance(rows, int):
        return rows
    candles = ohlcv.get("candles")
    if isinstance(candles, (list, tuple)):
        return len(candles)
    return None


def _extract_closes(ohlcv: Mapping[str, Any]) -> list[float]:
    return [candle["close"] for candle in _extract_candles(ohlcv)]


def _extract_candles(ohlcv: Mapping[str, Any]) -> list[dict[str, Any]]:
    candles = ohlcv.get("candles")
    parsed: list[dict[str, Any]] = []
    if not isinstance(candles, (list, tuple)):
        return parsed
    for index, row in enumerate(candles):
        if not isinstance(row, Mapping):
            continue
        close = _to_float(row.get("close"))
        if close is None:
            continue
        parsed.append(
            {
                "index": index,
                "time": row.get("date") or row.get("time") or row.get("timestamp") or index,
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": close,
                "volume": _to_float(row.get("volume")),
            }
        )
    return parsed


def analyze_market_structure(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    base_state = state_from_domain_status(data.domain_status["market"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="市场价格域缺失，无法生成结构判断。",
            evidence={},
            gap_ids=data.gap_ids_for("market"),
        )
    price = _to_float(payload.get("price"))
    ohlcv = data.domains.ohlcv or {}
    rows = _extract_rows(ohlcv)
    closes = _extract_closes(ohlcv)
    trend_pct: float | None = None
    if len(closes) >= 2 and closes[0] != 0:
        trend_pct = (closes[-1] - closes[0]) / closes[0] * 100.0
    if price is None:
        state = AnalysisState.GAP
        summary = "市场域已返回，但缺少可解析现价。"
    elif rows is None:
        state = AnalysisState.PARTIAL if base_state == AnalysisState.READY else base_state
        summary = f"现价 {price:.2f}，OHLCV 样本行数未给出，结构判断为部分可用。"
    else:
        state = base_state
        summary = f"现价 {price:.2f}，OHLCV 样本 {rows} 条。"
        if trend_pct is not None:
            summary += f" 观察期涨跌约 {trend_pct:.2f}%。"
    return AnalysisSection(
        status=state,
        summary=summary,
        evidence={"price": price, "ohlcv_rows": rows, "trend_pct": trend_pct},
        gap_ids=data.gap_ids_for("market"),
    )


def analyze_technical_patterns(payload: Mapping[str, Any] | None, data: CryptoLensInput) -> AnalysisSection:
    base_state = state_from_domain_status(data.domain_status["ohlcv"])
    if payload is None:
        return AnalysisSection(
            status=AnalysisState.GAP,
            summary="OHLCV 域缺失，技术形态不可用。",
            evidence={},
            gap_ids=data.gap_ids_for("ohlcv"),
        )

    rows = _extract_rows(payload)
    candles = _extract_candles(payload)
    closes = [candle["close"] for candle in candles]
    indicators = payload.get("indicators") if isinstance(payload.get("indicators"), Mapping) else {}
    rsi = _to_float(indicators.get("rsi")) if isinstance(indicators, Mapping) else None
    macd_hist = _to_float(indicators.get("macd_hist")) if isinstance(indicators, Mapping) else None
    atr14 = _atr14(candles)
    vegas = _analyze_vegas(candles)
    double_line = _analyze_double_line_reversal(candles)
    fvg = _analyze_fvg(candles, atr14=atr14)
    swings = _swing_points(candles)
    volume_profile = _volume_profile(candles)
    rule_123 = _rule_123_breakout(candles, swings)
    order_block = _order_block(candles, swings)
    amd_smc = _amd_smc(candles, swings, order_block=order_block)
    harmonic = _harmonic_pattern(swings)
    kd = _kd_9_3_3(candles)
    td = _td_sequential(candles)
    limitations = _technical_limitations(candles, vegas, double_line, kd)

    if rows is not None and rows < 30:
        state = AnalysisState.GAP
        summary = "OHLCV 样本不足（<30），仅保留保守技术描述。"
    else:
        state = _technical_state(base_state, limitations)
        summary = (
            "技术形态以统一数据层 OHLCV 结构化输入生成，覆盖维加斯通道、双线反转、FVG、KD、TD、"
            "OB、成交量分布、AMD/SMC、123 和谐波粗筛。"
        )
        if rsi is not None:
            if rsi >= 70:
                summary += " RSI 偏高。"
            elif rsi <= 30:
                summary += " RSI 偏低。"
            else:
                summary += " RSI 中性。"
        if macd_hist is not None:
            summary += f" MACD 柱值 {macd_hist:.4f}。"
        summary += " " + _vegas_summary(vegas)
        summary += " " + _double_line_summary(double_line)
        summary += " " + _fvg_summary(fvg)
        summary += " " + _volume_profile_summary(volume_profile)
        summary += " " + _rule_123_summary(rule_123)
        summary += " " + _order_block_summary(order_block)
        summary += " " + _amd_smc_summary(amd_smc)
        summary += " " + _harmonic_summary(harmonic)
        summary += " " + _kd_summary(kd)
        summary += " " + _td_summary(td)
        if len(closes) >= 2:
            direction = "上行" if closes[-1] > closes[0] else "下行" if closes[-1] < closes[0] else "横盘"
            summary += f" 观察期价格{direction}。"
        if limitations:
            summary += " 样本限制：" + "；".join(limitations) + "。"

    return AnalysisSection(
        status=state,
        summary=summary,
        evidence={
            "rows": rows,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "atr14": atr14,
            "vegas": vegas,
            "double_line_reversal": double_line,
            "fvg": fvg,
            "patterns": {
                "order_block": order_block,
                "volume_profile": volume_profile,
                "amd": amd_smc,
                "rule_123": rule_123,
                "harmonic": harmonic,
            },
            "kd_9_3_3": kd,
            "td_sequential": td,
            "rule_versions": {
                "vegas": "bb-vegas-v1",
                "fvg": "bb-fvg-v1",
                "order_block": "claw-ohlcv-order-block-v1",
                "volume_profile": "claw-ohlcv-volume-profile-v1",
                "amd_smc": "claw-ohlcv-amd-smc-v1",
                "rule_123": "claw-ohlcv-123-v1",
                "harmonic": "claw-ohlcv-harmonic-v1",
                "kd": "bb-indicators-v1",
                "td_sequential": "bb-indicators-v1",
                "double_line_reversal": "bb-double-line-reversal-v1",
            },
        },
        gap_ids=data.gap_ids_for("ohlcv"),
    )


def _technical_state(base_state: AnalysisState, limitations: list[str]) -> AnalysisState:
    if base_state != AnalysisState.READY:
        return base_state
    hard_limitations = (
        "K线缺少 high/low",
        "维加斯蓝带需要",
        "双线反转需要",
        "KD 需要",
    )
    if any(any(item.startswith(prefix) for prefix in hard_limitations) for item in limitations):
        return AnalysisState.PARTIAL
    return AnalysisState.READY


def _complete_ohlc(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        candle
        for candle in candles
        if candle.get("open") is not None and candle.get("high") is not None and candle.get("low") is not None
    ]


def _high_low_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candle for candle in candles if candle.get("high") is not None and candle.get("low") is not None]


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(_round(value * multiplier + output[-1] * (1 - multiplier)))
    return output


def _last_ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    series = _ema_series(values, period)
    return series[-1] if series else None


def _average_last(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    sample = values[-period:]
    return _round(sum(sample) / len(sample))


def _atr14(candles: list[dict[str, Any]]) -> float | None:
    sample = _high_low_candles(candles)
    if len(sample) < 15:
        return None
    true_ranges: list[float] = []
    for index in range(1, len(sample)):
        current = sample[index]
        previous = sample[index - 1]
        high = current["high"]
        low = current["low"]
        prior_close = previous["close"]
        true_ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    return _average_last(true_ranges, 14)


def _analyze_vegas(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [candle["close"] for candle in candles]
    latest_close = closes[-1] if closes else None
    ema144 = _last_ema(closes, 144)
    ema169 = _last_ema(closes, 169)
    ema576 = _last_ema(closes, 576)
    ema676 = _last_ema(closes, 676)
    blue_top = _max_nullable(ema144, ema169)
    blue_bottom = _min_nullable(ema144, ema169)
    purple_top = _max_nullable(ema576, ema676)
    purple_bottom = _min_nullable(ema576, ema676)

    band_position = "unknown"
    if latest_close is not None and blue_top is not None and blue_bottom is not None:
        if latest_close > blue_top:
            band_position = "above_blue_band"
        elif latest_close < blue_bottom:
            band_position = "below_blue_band"
        else:
            band_position = "inside_blue_band"

    major_trend = "unknown"
    if blue_bottom is not None and purple_top is not None and blue_top is not None and purple_bottom is not None:
        if blue_bottom > purple_top:
            major_trend = "major_trend_bullish"
        elif blue_top < purple_bottom:
            major_trend = "major_trend_bearish"
        else:
            major_trend = "neutral"

    return {
        "ema144": ema144,
        "ema169": ema169,
        "ema576": ema576,
        "ema676": ema676,
        "band_position": band_position,
        "major_trend": major_trend,
        "distance_to_blue_band_pct": _percent_distance(latest_close, _midpoint(ema144, ema169)),
        "distance_to_purple_band_pct": _percent_distance(latest_close, _midpoint(ema576, ema676)),
        "sample_status": "ok" if len(candles) >= _VEGAS_PURPLE_BAND_MIN_CANDLES else "insufficient_history",
        "blue_band_min_candles": _VEGAS_BLUE_BAND_MIN_CANDLES,
        "purple_band_min_candles": _VEGAS_PURPLE_BAND_MIN_CANDLES,
    }


def _analyze_double_line_reversal(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [candle["close"] for candle in candles]
    if len(closes) < 50:
        return {
            "fast_line": "ema20",
            "slow_line": "ema50",
            "state": "insufficient",
            "fast_value": None,
            "slow_value": None,
            "cross_time": None,
            "confidence": "low",
        }
    fast = _ema_series(closes, 20)
    slow = _ema_series(closes, 50)
    fast_value = fast[-1] if fast else None
    slow_value = slow[-1] if slow else None
    previous_fast = fast[-2] if len(fast) >= 2 else None
    previous_slow = slow[-2] if len(slow) >= 2 else None
    state = "neutral"
    cross_time = None
    if fast_value is not None and slow_value is not None and previous_fast is not None and previous_slow is not None:
        if previous_fast <= previous_slow and fast_value > slow_value:
            state = "bullish_cross"
            cross_time = _format_time(candles[-1]["time"])
        elif previous_fast >= previous_slow and fast_value < slow_value:
            state = "bearish_cross"
            cross_time = _format_time(candles[-1]["time"])
        elif fast_value > slow_value:
            state = "above_lines"
        elif fast_value < slow_value:
            state = "below_lines"
    return {
        "fast_line": "ema20",
        "slow_line": "ema50",
        "state": state,
        "fast_value": fast_value,
        "slow_value": slow_value,
        "cross_time": cross_time,
        "confidence": "medium" if state in {"bullish_cross", "bearish_cross"} else "low",
    }


def _analyze_fvg(candles: list[dict[str, Any]], *, atr14: float | None) -> dict[str, Any]:
    sample = _high_low_candles(candles)
    latest_close = sample[-1]["close"] if sample else None
    candidates: list[dict[str, Any]] = []
    lower_bound_index = max(2, len(sample) - 100)
    min_gap_pct = 0.15
    min_gap_atr_ratio = 0.05
    for index in range(2, len(sample)):
        if index < lower_bound_index:
            continue
        c1 = sample[index - 2]
        c3 = sample[index]
        if c1["high"] < c3["low"]:
            gap_width = c3["low"] - c1["high"]
            mid = c1["high"] + gap_width / 2
            gap_pct = (gap_width / mid) * 100 if mid else 0
            if not _fvg_passes_threshold(gap_pct, gap_width, min_gap_pct, min_gap_atr_ratio, atr14):
                continue
            status = _evaluate_bullish_fvg_fill(sample, index + 1, c1["high"], c3["low"])
            candidates.append(
                _fvg_candidate(
                    direction="bullish",
                    lower=c1["high"],
                    upper=c3["low"],
                    mid=mid,
                    gap_pct=gap_pct,
                    created_at=c3["time"],
                    status=status,
                    latest_close=latest_close,
                    indexes=(c1["index"], sample[index - 1]["index"], c3["index"]),
                    min_gap_pct=min_gap_pct,
                    min_gap_atr_ratio=min_gap_atr_ratio,
                )
            )
            continue
        if c1["low"] > c3["high"]:
            gap_width = c1["low"] - c3["high"]
            mid = c3["high"] + gap_width / 2
            gap_pct = (gap_width / mid) * 100 if mid else 0
            if not _fvg_passes_threshold(gap_pct, gap_width, min_gap_pct, min_gap_atr_ratio, atr14):
                continue
            status = _evaluate_bearish_fvg_fill(sample, index + 1, c3["high"], c1["low"])
            candidates.append(
                _fvg_candidate(
                    direction="bearish",
                    lower=c3["high"],
                    upper=c1["low"],
                    mid=mid,
                    gap_pct=gap_pct,
                    created_at=c3["time"],
                    status=status,
                    latest_close=latest_close,
                    indexes=(c1["index"], sample[index - 1]["index"], c3["index"]),
                    min_gap_pct=min_gap_pct,
                    min_gap_atr_ratio=min_gap_atr_ratio,
                )
            )
    return {
        "candidates": candidates,
        "nearest_above": _nearest_fvg(candidates, latest_close, "above"),
        "nearest_below": _nearest_fvg(candidates, latest_close, "below"),
        "open_count": sum(1 for candidate in candidates if candidate["status"] == "open"),
    }


def _kd_9_3_3(candles: list[dict[str, Any]]) -> dict[str, float | None]:
    sample = _high_low_candles(candles)
    period = 9
    if len(sample) < period:
        return {"k": None, "d": None, "j": None, "k_state": None, "d_state": None, "oversold_threshold": 20.0, "overbought_threshold": 80.0}
    k = 50.0
    d = 50.0
    for index in range(period - 1, len(sample)):
        window = sample[index - period + 1 : index + 1]
        high = max(candle["high"] for candle in window)
        low = min(candle["low"] for candle in window)
        close = sample[index]["close"]
        rsv = 50.0 if high == low else ((close - low) / (high - low)) * 100
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
    rounded_k = _round(k)
    rounded_d = _round(d)
    return {
        "k": rounded_k,
        "d": rounded_d,
        "j": _round(3 * k - 2 * d),
        "k_state": _oscillator_state(rounded_k),
        "d_state": _oscillator_state(rounded_d),
        "oversold_threshold": 20.0,
        "overbought_threshold": 80.0,
    }


def _oscillator_state(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 20.0:
        return "oversold"
    if value <= 25.0:
        return "near_oversold"
    if value > 80.0:
        return "overbought"
    if value >= 75.0:
        return "near_overbought"
    return "neutral"


def _td_sequential(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 5:
        return {"setup_direction": "none", "setup_count": 0, "countdown_count": 0, "signal": "none", "confidence": "low"}

    setup_direction = "none"
    setup_count = 0
    for index in range(len(candles) - 1, 3, -1):
        close = candles[index]["close"]
        prior = candles[index - 4]["close"]
        direction = "bullish_reversal" if close < prior else "bearish_reversal" if close > prior else "none"
        if direction == "none":
            break
        if setup_direction == "none":
            setup_direction = direction
        if direction != setup_direction:
            break
        setup_count += 1

    countdown_count = 0
    if setup_direction != "none":
        for index in range(len(candles) - 1, 1, -1):
            if countdown_count >= 13:
                break
            candle = candles[index]
            comparison = candles[index - 2]
            if comparison.get("high") is None or comparison.get("low") is None:
                continue
            if setup_direction == "bullish_reversal" and candle["close"] <= comparison["low"]:
                countdown_count += 1
            if setup_direction == "bearish_reversal" and candle["close"] >= comparison["high"]:
                countdown_count += 1

    if setup_direction == "bullish_reversal" and countdown_count >= 13:
        signal = "bullish_reversal_countdown_13"
    elif setup_direction == "bearish_reversal" and countdown_count >= 13:
        signal = "bearish_reversal_countdown_13"
    elif setup_direction == "bullish_reversal" and setup_count >= 9:
        signal = "bullish_reversal_setup_9"
    elif setup_direction == "bearish_reversal" and setup_count >= 9:
        signal = "bearish_reversal_setup_9"
    else:
        signal = "none"

    return {
        "setup_direction": setup_direction,
        "setup_count": min(setup_count, 13),
        "countdown_count": min(countdown_count, 13),
        "signal": signal,
        "confidence": "low" if signal == "none" else "medium",
        "price_sequence": _td_price_sequence(setup_direction),
        "setup_rule": _td_setup_rule(setup_direction),
        "interpretation": _td_interpretation(setup_direction, countdown_count=countdown_count, setup_count=setup_count),
    }


def _swing_points(candles: list[dict[str, Any]], *, window: int = 2) -> list[dict[str, Any]]:
    sample = _high_low_candles(candles)
    if len(sample) < window * 2 + 1:
        return []
    swings: list[dict[str, Any]] = []
    for index in range(window, len(sample) - window):
        candle = sample[index]
        left = sample[index - window : index]
        right = sample[index + 1 : index + window + 1]
        high = candle["high"]
        low = candle["low"]
        is_high = all(high > item["high"] for item in left + right)
        is_low = all(low < item["low"] for item in left + right)
        if is_high:
            swings.append(
                {
                    "type": "high",
                    "price": _round(high),
                    "time": _format_time(candle["time"]),
                    "index": candle["index"],
                }
            )
        if is_low:
            swings.append(
                {
                    "type": "low",
                    "price": _round(low),
                    "time": _format_time(candle["time"]),
                    "index": candle["index"],
                }
            )
    return sorted(swings, key=lambda item: int(item["index"]))


def _volume_profile(candles: list[dict[str, Any]]) -> dict[str, Any]:
    sample = [candle for candle in _high_low_candles(candles)[-160:] if candle.get("volume") is not None]
    if len(sample) < 20:
        return {
            "status": "insufficient_volume",
            "sample_count": len(sample),
            "min_required": 20,
            "poc": None,
            "value_area_low": None,
            "value_area_high": None,
        }
    low = min(candle["low"] for candle in sample)
    high = max(candle["high"] for candle in sample)
    if high <= low:
        return {
            "status": "flat_range",
            "sample_count": len(sample),
            "poc": _round(high),
            "value_area_low": _round(low),
            "value_area_high": _round(high),
        }
    bin_count = min(36, max(12, int(len(sample) ** 0.5) * 2))
    width = (high - low) / bin_count
    bins = [{"lower": low + width * index, "upper": low + width * (index + 1), "volume": 0.0} for index in range(bin_count)]
    for candle in sample:
        typical = (candle["high"] + candle["low"] + candle["close"]) / 3
        index = min(bin_count - 1, max(0, int((typical - low) / width)))
        bins[index]["volume"] += float(candle["volume"] or 0.0)
    total_volume = sum(item["volume"] for item in bins)
    sorted_bins = sorted(enumerate(bins), key=lambda item: item[1]["volume"], reverse=True)
    poc_index, poc_bin = sorted_bins[0]
    selected = {poc_index}
    running = poc_bin["volume"]
    target = total_volume * 0.7
    for index, item in sorted_bins[1:]:
        if running >= target:
            break
        selected.add(index)
        running += item["volume"]
    value_area_low = min(bins[index]["lower"] for index in selected)
    value_area_high = max(bins[index]["upper"] for index in selected)
    return {
        "status": "ready",
        "sample_count": len(sample),
        "bin_count": bin_count,
        "poc": _round((poc_bin["lower"] + poc_bin["upper"]) / 2),
        "poc_volume": _round(poc_bin["volume"]),
        "value_area_low": _round(value_area_low),
        "value_area_high": _round(value_area_high),
        "total_volume": _round(total_volume),
        "method": "close_typical_price_volume_buckets",
    }


def _rule_123_breakout(candles: list[dict[str, Any]], swings: list[dict[str, Any]]) -> dict[str, Any]:
    latest_close = candles[-1]["close"] if candles else None
    if latest_close is None or len(swings) < 3:
        return {"status": "insufficient_swings", "swing_count": len(swings), "min_required": 3}
    for a, b, c in _last_swing_windows(swings, size=3, limit=8):
        if a["type"] == "low" and b["type"] == "high" and c["type"] == "low" and c["price"] > a["price"]:
            status = "confirmed_breakout" if latest_close > b["price"] else "pending_breakout"
            return _rule_123_payload("bullish", a, b, c, latest_close, status)
        if a["type"] == "high" and b["type"] == "low" and c["type"] == "high" and c["price"] < a["price"]:
            status = "confirmed_breakdown" if latest_close < b["price"] else "pending_breakdown"
            return _rule_123_payload("bearish", a, b, c, latest_close, status)
    return {"status": "no_pattern", "swing_count": len(swings), "latest_close": _round(latest_close)}


def _rule_123_payload(
    direction: str,
    point1: Mapping[str, Any],
    point2: Mapping[str, Any],
    point3: Mapping[str, Any],
    latest_close: float,
    status: str,
) -> dict[str, Any]:
    breakout_level = float(point2["price"])
    return {
        "status": status,
        "direction": direction,
        "point1": _pivot_payload(point1),
        "point2": _pivot_payload(point2),
        "point3": _pivot_payload(point3),
        "breakout_level": _round(breakout_level),
        "latest_close": _round(latest_close),
        "distance_to_breakout_pct": _percent_distance(latest_close, breakout_level),
        "method": "last_alternating_three_swing_points",
    }


def _order_block(candles: list[dict[str, Any]], swings: list[dict[str, Any]]) -> dict[str, Any]:
    sample = _complete_ohlc(candles)
    if len(sample) < 20 or len(swings) < 2:
        return {"status": "insufficient_ohlc", "sample_count": len(sample), "swing_count": len(swings)}
    latest_close = sample[-1]["close"]
    last_high = _last_swing(swings, "high")
    last_low = _last_swing(swings, "low")
    direction = "none"
    bos_level: float | None = None
    if last_high and latest_close > float(last_high["price"]):
        direction = "bullish"
        bos_level = float(last_high["price"])
    elif last_low and latest_close < float(last_low["price"]):
        direction = "bearish"
        bos_level = float(last_low["price"])
    else:
        return {
            "status": "no_recent_break_of_structure",
            "latest_close": _round(latest_close),
            "last_swing_high": _pivot_payload(last_high) if last_high else None,
            "last_swing_low": _pivot_payload(last_low) if last_low else None,
        }
    lookback = sample[-40:]
    if direction == "bullish":
        source = _last_matching_candle(lookback, bearish=True)
        if source is None:
            return {"status": "no_source_candle", "direction": direction, "bos_level": _round(bos_level)}
        lower = source["low"]
        upper = source["open"] if source["open"] is not None else source["high"]
    else:
        source = _last_matching_candle(lookback, bearish=False)
        if source is None:
            return {"status": "no_source_candle", "direction": direction, "bos_level": _round(bos_level)}
        lower = source["open"] if source["open"] is not None else source["low"]
        upper = source["high"]
    if lower > upper:
        lower, upper = upper, lower
    midpoint = (lower + upper) / 2
    return {
        "status": "candidate",
        "direction": direction,
        "bos_level": _round(bos_level),
        "zone_low": _round(lower),
        "zone_high": _round(upper),
        "mid": _round(midpoint),
        "source_time": _format_time(source["time"]),
        "latest_close": _round(latest_close),
        "distance_from_price_pct": _percent_distance(latest_close, midpoint),
        "method": "last_opposite_candle_before_recent_structure_break",
    }


def _amd_smc(candles: list[dict[str, Any]], swings: list[dict[str, Any]], *, order_block: Mapping[str, Any]) -> dict[str, Any]:
    latest_close = candles[-1]["close"] if candles else None
    if latest_close is None or len(swings) < 4:
        return {"status": "insufficient_swings", "swing_count": len(swings), "min_required": 4}
    highs = [item for item in swings if item["type"] == "high"]
    lows = [item for item in swings if item["type"] == "low"]
    if not highs or not lows:
        return {"status": "insufficient_swing_types", "swing_count": len(swings)}
    recent_high = highs[-1]
    recent_low = lows[-1]
    prior_high = highs[-2] if len(highs) >= 2 else None
    prior_low = lows[-2] if len(lows) >= 2 else None
    if prior_high and prior_low and recent_high["price"] > prior_high["price"] and recent_low["price"] > prior_low["price"]:
        structure = "higher_high_higher_low"
        bias = "bullish_structure"
    elif prior_high and prior_low and recent_high["price"] < prior_high["price"] and recent_low["price"] < prior_low["price"]:
        structure = "lower_high_lower_low"
        bias = "bearish_structure"
    else:
        structure = "mixed_range"
        bias = "range_or_transition"
    phase = "markup" if bias == "bullish_structure" else "markdown" if bias == "bearish_structure" else "accumulation_or_distribution_unconfirmed"
    return {
        "status": "ready",
        "structure": structure,
        "phase_proxy": phase,
        "bias": bias,
        "buy_side_liquidity": _pivot_payload(recent_high),
        "sell_side_liquidity": _pivot_payload(recent_low),
        "latest_close": _round(latest_close),
        "order_block_status": order_block.get("status"),
        "method": "swing_structure_and_liquidity_pool_proxy",
        "limitation": "AMD/SMC 为OHLCV结构代理，不含逐笔订单流确认",
    }


def _harmonic_pattern(swings: list[dict[str, Any]]) -> dict[str, Any]:
    if len(swings) < 5:
        return {"status": "insufficient_swings", "swing_count": len(swings), "min_required": 5}
    pivots = swings[-5:]
    prices = [float(item["price"]) for item in pivots]
    xa = abs(prices[1] - prices[0])
    ab = abs(prices[2] - prices[1])
    bc = abs(prices[3] - prices[2])
    cd = abs(prices[4] - prices[3])
    if min(xa, ab, bc) == 0:
        return {"status": "invalid_pivot_geometry", "pivots": [_pivot_payload(item) for item in pivots]}
    ratios = {
        "ab_xa": _round(ab / xa),
        "bc_ab": _round(bc / ab),
        "cd_bc": _round(cd / bc),
    }
    pattern = _classify_harmonic(ratios)
    direction = "bullish" if pivots[-1]["type"] == "low" else "bearish" if pivots[-1]["type"] == "high" else "unknown"
    return {
        "status": "candidate" if pattern != "none" else "no_candidate",
        "pattern": pattern,
        "direction": direction,
        "pivots": [_pivot_payload(item) for item in pivots],
        "ratios": ratios,
        "method": "last_five_swing_ratio_screen",
    }


def _td_price_sequence(setup_direction: str) -> str:
    if setup_direction == "bullish_reversal":
        return "closes_below_four_bars_ago"
    if setup_direction == "bearish_reversal":
        return "closes_above_four_bars_ago"
    return "none"


def _td_setup_rule(setup_direction: str) -> str:
    if setup_direction == "bullish_reversal":
        return "close < close_4_bars_ago"
    if setup_direction == "bearish_reversal":
        return "close > close_4_bars_ago"
    return "none"


def _td_interpretation(setup_direction: str, *, countdown_count: int, setup_count: int) -> str:
    count = min(countdown_count or setup_count, 13)
    if setup_direction == "bullish_reversal":
        return f"下跌序列计数 {count}，表示当前 TD 下跌计数状态；不要编造卖压衰竭概率或交易动作"
    if setup_direction == "bearish_reversal":
        return f"上涨序列计数 {count}，表示当前 TD 上涨计数状态；不要编造买盘衰竭概率或交易动作"
    return "无有效 TD setup/countdown"


def _last_swing_windows(swings: list[dict[str, Any]], *, size: int, limit: int) -> list[tuple[dict[str, Any], ...]]:
    windows: list[tuple[dict[str, Any], ...]] = []
    start = max(0, len(swings) - size - limit + 1)
    for index in range(start, len(swings) - size + 1):
        windows.append(tuple(swings[index : index + size]))
    return list(reversed(windows))


def _pivot_payload(pivot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if pivot is None:
        return None
    return {
        "type": pivot.get("type"),
        "price": pivot.get("price"),
        "time": pivot.get("time"),
        "index": pivot.get("index"),
    }


def _last_swing(swings: list[dict[str, Any]], swing_type: str) -> dict[str, Any] | None:
    for item in reversed(swings):
        if item.get("type") == swing_type:
            return item
    return None


def _last_matching_candle(candles: list[dict[str, Any]], *, bearish: bool) -> dict[str, Any] | None:
    for candle in reversed(candles):
        open_value = candle.get("open")
        close_value = candle.get("close")
        if open_value is None or close_value is None:
            continue
        if bearish and close_value < open_value:
            return candle
        if not bearish and close_value > open_value:
            return candle
    return None


def _classify_harmonic(ratios: Mapping[str, float]) -> str:
    ab_xa = ratios["ab_xa"]
    bc_ab = ratios["bc_ab"]
    cd_bc = ratios["cd_bc"]
    if _near(ab_xa, 0.618, tolerance=0.12) and 0.382 <= bc_ab <= 0.886 and 1.13 <= cd_bc <= 1.75:
        return "gartley_like"
    if 0.382 <= ab_xa <= 0.55 and 0.382 <= bc_ab <= 0.886 and 1.50 <= cd_bc <= 2.80:
        return "bat_like"
    if 0.70 <= ab_xa <= 0.88 and 0.382 <= bc_ab <= 0.886 and 1.27 <= cd_bc <= 2.40:
        return "butterfly_like"
    if 0.382 <= ab_xa <= 0.70 and 0.382 <= bc_ab <= 0.886 and 2.20 <= cd_bc <= 3.80:
        return "crab_like"
    return "none"


def _near(value: float, target: float, *, tolerance: float) -> bool:
    return target - tolerance <= value <= target + tolerance


def _technical_limitations(
    candles: list[dict[str, Any]],
    vegas: Mapping[str, Any],
    double_line: Mapping[str, Any],
    kd: Mapping[str, Any],
) -> list[str]:
    limitations: list[str] = []
    if candles and len(_high_low_candles(candles)) != len(candles):
        limitations.append("K线缺少最高价/最低价，KD、FVG、TD倒数计数只能部分覆盖")
    if len(candles) < _VEGAS_BLUE_BAND_MIN_CANDLES:
        limitations.append(f"维加斯蓝带需要 {_VEGAS_BLUE_BAND_MIN_CANDLES} 根K线，当前 {len(candles)} 根")
    elif vegas.get("sample_status") != "ok":
        limitations.append(f"维加斯紫带需要 {_VEGAS_PURPLE_BAND_MIN_CANDLES} 根K线，当前 {len(candles)} 根；蓝带仍可用")
    if double_line.get("state") == "insufficient":
        limitations.append(f"双线反转需要 50 根K线，当前 {len(candles)} 根")
    if kd.get("k") is None:
        limitations.append(f"KD 需要 9 根带 high/low 的K线，当前 {len(_high_low_candles(candles))} 根")
    return limitations


def _vegas_summary(vegas: Mapping[str, Any]) -> str:
    if vegas.get("ema144") is None or vegas.get("ema169") is None:
        return "维加斯通道：蓝带样本不足。"
    return (
        "维加斯通道："
        f"位置={_reader_label(vegas.get('band_position'))}，"
        f"主趋势={_reader_label(vegas.get('major_trend'))}，"
        f"距蓝带中线={_format_optional_pct(vegas.get('distance_to_blue_band_pct'))}。"
    )


def _double_line_summary(double_line: Mapping[str, Any]) -> str:
    if double_line.get("state") == "insufficient":
        return "双线反转：样本不足。"
    return (
        "双线反转："
        f"EMA20/EMA50 状态={_reader_label(double_line.get('state'))}，"
        f"EMA20={_format_optional_number(double_line.get('fast_value'))}，"
        f"EMA50={_format_optional_number(double_line.get('slow_value'))}。"
    )


def _fvg_summary(fvg: Mapping[str, Any]) -> str:
    nearest_below = fvg.get("nearest_below")
    nearest_above = fvg.get("nearest_above")
    open_count = fvg.get("open_count")
    candidate_count = len(fvg.get("candidates") or ())
    if nearest_below:
        return f"FVG：候选 {candidate_count} 个，开放 {open_count} 个，最近下方缺口中线 {_format_optional_number(nearest_below.get('mid'))}。"
    if nearest_above:
        return f"FVG：候选 {candidate_count} 个，开放 {open_count} 个，最近上方缺口中线 {_format_optional_number(nearest_above.get('mid'))}。"
    return f"FVG：候选 {candidate_count} 个，开放 {open_count} 个，当前无近端未回补缺口。"


def _volume_profile_summary(volume_profile: Mapping[str, Any]) -> str:
    if volume_profile.get("status") != "ready":
        return f"成交量分布：样本不足，当前样本 {volume_profile.get('sample_count')}。"
    return (
        "成交量分布："
        f"POC={_format_optional_number(volume_profile.get('poc'))}，"
        f"价值区间={_format_optional_number(volume_profile.get('value_area_low'))}-"
        f"{_format_optional_number(volume_profile.get('value_area_high'))}。"
    )


def _rule_123_summary(rule_123: Mapping[str, Any]) -> str:
    status = str(rule_123.get("status") or "unknown")
    if status.startswith("insufficient"):
        return f"123：样本不足，摆动点数量={rule_123.get('swing_count')}。"
    if status == "no_pattern":
        return "123：当前未检测到有效三点突破结构。"
    return (
        "123："
        f"方向={_reader_label(rule_123.get('direction'))}，"
        f"状态={_reader_label(status)}，"
        f"突破位={_format_optional_number(rule_123.get('breakout_level'))}。"
    )


def _order_block_summary(order_block: Mapping[str, Any]) -> str:
    status = str(order_block.get("status") or "unknown")
    if status != "candidate":
        return f"OB：{_reader_label(status)}。"
    return (
        "OB："
        f"方向={_reader_label(order_block.get('direction'))}，"
        f"区间={_format_optional_number(order_block.get('zone_low'))}-"
        f"{_format_optional_number(order_block.get('zone_high'))}。"
    )


def _amd_smc_summary(amd_smc: Mapping[str, Any]) -> str:
    if amd_smc.get("status") != "ready":
        return f"AMD/SMC：样本不足，摆动点数量={amd_smc.get('swing_count')}。"
    return (
        "AMD/SMC："
        f"结构={_reader_label(amd_smc.get('structure'))}，"
        f"阶段={_reader_label(amd_smc.get('phase_proxy'))}。"
    )


def _harmonic_summary(harmonic: Mapping[str, Any]) -> str:
    status = str(harmonic.get("status") or "unknown")
    if status.startswith("insufficient"):
        return f"谐波：样本不足，摆动点数量={harmonic.get('swing_count')}。"
    if status == "no_candidate":
        return "谐波：最近五个摆动点未匹配常见比例。"
    return f"谐波：{_reader_label(harmonic.get('pattern'))} 候选，方向={_reader_label(harmonic.get('direction'))}。"


def _kd_summary(kd: Mapping[str, Any]) -> str:
    if kd.get("k") is None or kd.get("d") is None:
        return "KD(9,3,3)：样本不足。"
    return (
        "KD(9,3,3)："
        f"K={_format_optional_number(kd.get('k'))}，"
        f"D={_format_optional_number(kd.get('d'))}，"
        f"J={_format_optional_number(kd.get('j'))}。"
        f"状态：K={_reader_label(kd.get('k_state'))}，D={_reader_label(kd.get('d_state'))}。"
    )


def _td_summary(td: Mapping[str, Any]) -> str:
    return (
        "TD Sequential："
        f"方向={_reader_label(td.get('setup_direction'))}，"
        f"启动计数={td.get('setup_count')}，"
        f"倒数计数={td.get('countdown_count')}，"
        f"信号={_reader_label(td.get('signal'))}，"
        f"解释={td.get('interpretation')}。"
    )


def _reader_label(value: object) -> str:
    raw = str(value or "").strip()
    labels = {
        "unknown": "未确认",
        "none": "无信号",
        "open": "未回补",
        "filled": "已回补",
        "partially_filled": "部分回补",
        "bullish": "看涨",
        "bearish": "看跌",
        "bullish_reversal": "看涨反转",
        "bearish_reversal": "看跌反转",
        "bullish_reversal_countdown_13": "TD看涨反转13计数完成",
        "bearish_reversal_countdown_13": "TD看跌反转13计数完成",
        "bullish_reversal_setup_9": "TD看涨反转9计数完成",
        "bearish_reversal_setup_9": "TD看跌反转9计数完成",
        "below_blue_band": "价格在蓝带下方",
        "inside_blue_band": "价格位于蓝带内",
        "above_blue_band": "价格在蓝带上方",
        "major_trend_bullish": "长期趋势偏多",
        "major_trend_bearish": "长期趋势偏空",
        "bullish_cross": "向上交叉",
        "bearish_cross": "向下交叉",
        "above_lines": "价格在双线上方",
        "below_lines": "价格在双线下方",
        "between_lines": "价格位于双线之间",
        "pending_breakout": "上破待确认",
        "confirmed_breakout": "上破已确认",
        "pending_breakdown": "下破待确认",
        "confirmed_breakdown": "下破已确认",
        "no_recent_break_of_structure": "近期没有确认的结构突破",
        "no_source_candle": "未找到结构突破前的来源K线",
        "higher_high_higher_low": "高点抬高、低点抬高",
        "lower_high_lower_low": "高点下移、低点下移",
        "mixed_range": "区间震荡或结构转换",
        "markup": "上行推进阶段",
        "markdown": "下跌推进阶段",
        "accumulation_or_distribution_unconfirmed": "吸筹/派发阶段未确认",
        "near_oversold": "接近超卖",
        "oversold": "超卖",
        "near_overbought": "接近超买",
        "overbought": "超买",
        "neutral": "中性",
    }
    if raw in labels:
        return labels[raw]
    if "_" in raw:
        return "未识别状态"
    return raw or "未取得"


def _fvg_passes_threshold(
    gap_pct: float,
    gap_width: float,
    min_gap_pct: float,
    min_gap_atr_ratio: float,
    atr14: float | None,
) -> bool:
    if gap_pct < min_gap_pct:
        return False
    if atr14 is not None and atr14 > 0:
        return gap_width >= atr14 * min_gap_atr_ratio
    return True


def _evaluate_bullish_fvg_fill(candles: list[dict[str, Any]], start_index: int, lower: float, upper: float) -> str:
    for candle in candles[start_index:]:
        if candle["low"] <= lower:
            return "filled"
        if candle["low"] <= upper:
            return "partially_filled"
    return "open"


def _evaluate_bearish_fvg_fill(candles: list[dict[str, Any]], start_index: int, lower: float, upper: float) -> str:
    for candle in candles[start_index:]:
        if candle["high"] >= upper:
            return "filled"
        if candle["high"] >= lower:
            return "partially_filled"
    return "open"


def _fvg_candidate(
    *,
    direction: str,
    lower: float,
    upper: float,
    mid: float,
    gap_pct: float,
    created_at: object,
    status: str,
    latest_close: float | None,
    indexes: tuple[int, int, int],
    min_gap_pct: float,
    min_gap_atr_ratio: float,
) -> dict[str, Any]:
    if direction == "bullish":
        role = "support" if latest_close is not None and mid <= latest_close else "target"
    else:
        role = "resistance" if latest_close is not None and mid >= latest_close else "target"
    return {
        "direction": direction,
        "lower": _round(lower),
        "upper": _round(upper),
        "mid": _round(mid),
        "gap_pct": _round(gap_pct),
        "created_at": _format_time(created_at),
        "status": status,
        "distance_from_price_pct": _percent_distance(latest_close, mid),
        "role": role,
        "candle_indexes": indexes,
        "thresholds": {"min_gap_pct": min_gap_pct, "min_gap_atr_ratio": min_gap_atr_ratio},
    }


def _nearest_fvg(candidates: list[dict[str, Any]], close: float | None, side: str) -> dict[str, Any] | None:
    if close is None:
        return None
    filtered = [
        candidate
        for candidate in candidates
        if candidate["status"] in {"open", "partially_filled"}
        and ((side == "above" and candidate["mid"] > close) or (side == "below" and candidate["mid"] < close))
    ]
    if not filtered:
        return None
    return sorted(filtered, key=lambda item: abs((item["mid"] - close) / close))[0]


def _format_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    return str(value)


def _percent_distance(price: float | None, level: float | None) -> float | None:
    if price is None or level is None or level == 0:
        return None
    return _round(((price - level) / level) * 100)


def _midpoint(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return (a + b) / 2


def _max_nullable(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return max(a, b)


def _min_nullable(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return min(a, b)


def _format_optional_number(value: object) -> str:
    parsed = _to_float(value)
    if parsed is None:
        return "不可用"
    return f"{parsed:.2f}"


def _format_optional_pct(value: object) -> str:
    parsed = _to_float(value)
    if parsed is None:
        return "不可用"
    return f"{parsed:.2f}%"


def _round(value: float) -> float:
    return float(f"{value:.6f}")
