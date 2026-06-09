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
    kd = _kd_9_3_3(candles)
    td = _td_sequential(candles)
    limitations = _technical_limitations(candles, vegas, double_line, kd)

    if rows is not None and rows < 30:
        state = AnalysisState.GAP
        summary = "OHLCV 样本不足（<30），仅保留保守技术描述。"
    else:
        state = _technical_state(base_state, limitations)
        summary = "技术形态以统一数据层 OHLCV 结构化输入生成，覆盖维加斯通道、双线反转、FVG、KD、TD。"
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
            "kd_9_3_3": kd,
            "td_sequential": td,
            "rule_versions": {
                "vegas": "bb-vegas-v1",
                "fvg": "bb-fvg-v1",
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


def _technical_limitations(
    candles: list[dict[str, Any]],
    vegas: Mapping[str, Any],
    double_line: Mapping[str, Any],
    kd: Mapping[str, Any],
) -> list[str]:
    limitations: list[str] = []
    if candles and len(_high_low_candles(candles)) != len(candles):
        limitations.append("K线缺少 high/low，KD、FVG、TD countdown 只能部分覆盖")
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
        f"位置={vegas.get('band_position')}，"
        f"主趋势={vegas.get('major_trend')}，"
        f"距蓝带中线={_format_optional_pct(vegas.get('distance_to_blue_band_pct'))}。"
    )


def _double_line_summary(double_line: Mapping[str, Any]) -> str:
    if double_line.get("state") == "insufficient":
        return "双线反转：样本不足。"
    return (
        "双线反转："
        f"EMA20/EMA50 状态={double_line.get('state')}，"
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


def _kd_summary(kd: Mapping[str, Any]) -> str:
    if kd.get("k") is None or kd.get("d") is None:
        return "KD(9,3,3)：样本不足。"
    return (
        "KD(9,3,3)："
        f"K={_format_optional_number(kd.get('k'))}，"
        f"D={_format_optional_number(kd.get('d'))}，"
        f"J={_format_optional_number(kd.get('j'))}。"
        f"状态：K={kd.get('k_state')}，D={kd.get('d_state')}。"
    )


def _td_summary(td: Mapping[str, Any]) -> str:
    return (
        "TD Sequential："
        f"方向={td.get('setup_direction')}，"
        f"setup={td.get('setup_count')}，"
        f"countdown={td.get('countdown_count')}，"
        f"信号={td.get('signal')}，"
        f"解释={td.get('interpretation')}。"
    )


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
