from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

_DATA_LAYER_ATTEMPT_SUMMARY_KEY = "provider_" + "attempts_summary"
_PRICE_IDENTITY_CONFLICT_FACTOR = 5.0


def summarize_report_prefetch_manifest(
    manifest_path: Path,
    *,
    ticker: str = "",
    company_name: str = "",
) -> str:
    if not manifest_path.exists():
        return "只写已批准上游材料里的已有事实。"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"数据事实核对摘要读取失败：{exc}。只写已批准上游材料里的已有事实。"
    if not isinstance(payload, Mapping):
        return "数据事实核对摘要格式无效；只写已批准上游材料里的已有事实。"
    return _summarize_report_data_payload(payload, ticker=ticker, company_name=company_name)


def summarize_report_data_need_results(
    results_root: Path,
    *,
    ticker: str = "",
    company_name: str = "",
    fallback_manifest_path: Path | None = None,
) -> str:
    payloads = _load_data_need_result_payloads(results_root)
    if not payloads:
        if fallback_manifest_path is not None:
            return summarize_report_prefetch_manifest(
                fallback_manifest_path,
                ticker=ticker,
                company_name=company_name,
            )
        return "只写已批准上游材料里的已有事实。"
    return _summarize_report_data_payload(
        _aggregate_data_need_payloads(payloads),
        ticker=ticker,
        company_name=company_name,
    )


def _load_data_need_result_payloads(results_root: Path) -> tuple[Mapping[str, Any], ...]:
    if not results_root.exists():
        return ()
    payloads: list[Mapping[str, Any]] = []
    paths = {
        *results_root.glob("*/result.json"),
        *results_root.glob("**/data-need-results/*/*/result.json"),
    }
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, Mapping):
            payloads.append(payload)
    return tuple(payloads)


def _aggregate_data_need_payloads(payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [str(payload.get("status") or "") for payload in payloads]
    data_results: list[dict[str, Any]] = []
    data_attempts: list[Mapping[str, Any]] = []
    for payload in payloads:
        data_attempts.extend(
            item
            for item in (payload.get("data_attempts_summary") or payload.get(_DATA_LAYER_ATTEMPT_SUMMARY_KEY) or ())
            if isinstance(item, Mapping)
        )
        for item in payload.get("data_results") or ():
            if not isinstance(item, Mapping):
                continue
            data_results.append(_normalize_data_need_result_for_summary(item, payload))
    return {
        "status": _aggregate_status(statuses),
        "data_results": data_results,
        "data_attempts_summary": data_attempts,
    }


def _aggregate_status(statuses: Sequence[str]) -> str:
    if not statuses:
        return "missing"
    normalized = {str(status or "").strip().lower() for status in statuses}
    if normalized <= {"ready"}:
        return "ready"
    if normalized & {"partial", "missing", "error"}:
        return "partial"
    return "partial"


def _normalize_data_need_result_for_summary(item: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = item.get("rows")
    if not rows:
        rows = item.get("sample_rows") or ()
    request_id = item.get("request_id")
    return {
        "request_id": request_id,
        "request_key": _payload_request_key(payload),
        "request_item": _payload_request_item(payload),
        "status": item.get("status"),
        "rows": tuple(row for row in rows if isinstance(row, Mapping)),
        "gaps": item.get("gaps") or (),
        "row_count": item.get("row_count"),
        "field_set": tuple(str(field) for field in item.get("field_set") or ()),
        "latest_value_summary": tuple(
            summary
            for summary in payload.get("latest_value_summary") or ()
            if isinstance(summary, Mapping) and summary.get("request_id") == request_id
        ),
    }


def _summarize_report_data_payload(
    payload: Mapping[str, Any],
    *,
    ticker: str,
    company_name: str,
) -> str:

    results = tuple(item for item in payload.get("data_results") or () if isinstance(item, Mapping))
    lines = [
        "以下是已返回数据读数，仅用于核对数字口径。",
    ]

    financial = _latest_result_row(results, "financial_statement")
    if financial:
        financial_text = _available_field_text(
            financial,
            (
                ("收入", "revenue", None),
                ("净利润", "net_income", None),
                ("资产", "assets", None),
                ("负债", "liabilities", None),
                ("经营现金流", "cash_flow", None),
            ),
        )
        if financial_text:
            lines.append("财务报表最新记录：" + _dated_text(financial, financial_text) + "。")
        lines.append("财报口径：收入、净利润、现金流通常是报告期累计流量；资产、负债是期末时点值。不得把报告期累计值写成单季度。")

    financial_metric = _latest_result_row(
        results,
        "financial_metric",
        require_any=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
    )
    if financial_metric:
        financial_metric_text = _available_field_text(
            financial_metric,
            (
                ("ROE", "roe", None),
                ("ROA", "roa", None),
                ("毛利率", "gross_margin", None),
                ("资产负债率", "debt_ratio", None),
                ("EPS", "eps", None),
            ),
        )
        if financial_metric_text:
            lines.append("财务指标最新记录：" + _dated_text(financial_metric, financial_metric_text) + "。")
        lines.append("EPS/PE口径：上述EPS若来自季度或中报记录，只能作为该报告期每股收益；可以把季度EPS年化作为前瞻情景推演，但必须写明这是情景假设，不能冒充静态PE、TTM PE或已确认全年EPS。")

    valuation = _latest_result_row(results, "valuation_metric", require_any=("pe", "pb", "ps"))
    if valuation:
        valuation_text = _available_field_text(
            valuation,
            (
                ("PE", "pe", None),
                ("PB", "pb", None),
                ("PS", "ps", None),
                ("市值", "market_cap", "market_cap_unit"),
            ),
        )
        if valuation_text:
            lines.append("估值指标最新记录：" + _dated_text(valuation, valuation_text) + "。")

    market_price = _trusted_market_price(results)
    realtime_valuation = _latest_result_row(results, "valuation_metric", require_any=("price",))
    valuation_identity_conflict = _price_conflicts_with_market_reference(realtime_valuation, market_price)
    if realtime_valuation and not valuation_identity_conflict:
        realtime_text = _available_field_text(
            realtime_valuation,
            (
                ("价格", "price", None),
                ("市值", "market_cap", "market_cap_unit"),
            ),
        )
        if realtime_text:
            lines.append("实时估值/行情快照：" + _dated_text(realtime_valuation, realtime_text) + "。")

    crypto_lines = _crypto_metric_lines(
        results,
        suppress_project_profile=valuation_identity_conflict,
    )
    if crypto_lines:
        lines.extend(crypto_lines)

    capital_flow = _latest_result_row(results, "capital_flow")
    if capital_flow:
        capital_flow_text = _available_field_text(
            capital_flow,
            (("主力净流入", "main_net", "amount_unit"),),
        )
        if capital_flow_text:
            lines.append("资金流数据可用：" + _dated_text(capital_flow, capital_flow_text) + "。")
    northbound_flow = _latest_result_row(results, "northbound_flow")
    if northbound_flow:
        northbound_text = _available_field_text(
            northbound_flow,
            (
                ("沪股通", "hgt_net", "amount_unit"),
                ("深股通", "sgt_net", "amount_unit"),
                ("合计", "northbound_net", "amount_unit"),
            ),
        )
        if northbound_text:
            lines.append("北向资金数据可用：" + _dated_text(northbound_flow, northbound_text) + "。")
            lines.append(
                "北向资金口径：若只返回沪股通、深股通或北向合计，这是市场/通道级背景，"
                f"不等同于 {company_name or ticker} 个股北向净买入或净卖出；不得写成该股被北向资金、外资或聪明钱单独增减持。"
            )
    margin_trading = _latest_result_row(results, "margin_trading")
    if margin_trading:
        margin_text = _available_field_text(
            margin_trading,
            (
                ("融资余额", "financing_balance", "amount_unit"),
                ("融资融券余额", "margin_balance", "amount_unit"),
            ),
            default_unit="CNY",
        )
        if margin_text:
            lines.append("融资融券数据可用：" + _dated_text(margin_trading, margin_text) + "。")
        leverage_line = _margin_to_market_cap_line(
            margin_trading,
            valuation or (None if valuation_identity_conflict else realtime_valuation),
        )
        if leverage_line:
            lines.append(leverage_line)
    sector = _latest_result_row(results, "sector_snapshot")
    if sector:
        sector_text = _available_field_text(
            sector,
            (
                ("最新样例", "sector_name", None),
                ("主力净流入", "main_net", "amount_unit"),
            ),
        )
        if sector_text:
            lines.append("行业/板块资金快照有数据，但需核对是否匹配标的所属行业；" + sector_text + "。")
    news_line = _news_boundary_line(results, ticker=ticker, company_name=company_name)
    if news_line:
        lines.append(news_line)

    social_line = _social_boundary_line(results)
    if social_line:
        lines.append(social_line)

    return "\n".join(lines)


def _latest_result_row(
    results: Sequence[Mapping[str, Any]],
    dataset: str,
    *,
    require_any: tuple[str, ...] = (),
) -> Mapping[str, Any] | None:
    result_and_row = _latest_result_and_row(results, dataset, require_any=require_any)
    if result_and_row is None:
        return None
    return result_and_row[1]


def _latest_result_and_row(
    results: Sequence[Mapping[str, Any]],
    dataset: str,
    *,
    require_any: tuple[str, ...] = (),
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    rows: list[Mapping[str, Any]] = []
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for result in results:
        if _result_dataset(str(result.get("request_id") or "")) != dataset:
            continue
        for row in _result_rows(result):
            if not isinstance(row, Mapping):
                continue
            if require_any and not any(row.get(field) is not None for field in require_any):
                continue
            rows.append(row)
            pairs.append((result, row))
    if not pairs:
        return None
    return sorted(pairs, key=lambda pair: _row_sort_text(pair[1]), reverse=True)[0]


def _trusted_market_price(results: Sequence[Mapping[str, Any]]) -> float | None:
    for dataset, fields in (
        ("quote_snapshot", ("price", "last_price", "close")),
        ("daily_bar", ("close", "price", "last_price")),
    ):
        row = _latest_result_row(results, dataset, require_any=fields)
        if row is None:
            continue
        price = _first_positive_float(row, fields)
        if price is not None:
            return price
    return None


def _price_conflicts_with_market_reference(row: Mapping[str, Any] | None, market_price: float | None) -> bool:
    if row is None or market_price is None:
        return False
    valuation_price = _first_positive_float(row, ("price", "last_price", "close"))
    if valuation_price is None:
        return False
    lower = min(market_price, valuation_price)
    if lower <= 0:
        return False
    return max(market_price, valuation_price) / lower >= _PRICE_IDENTITY_CONFLICT_FACTOR


def _first_positive_float(row: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    for field in fields:
        value = _float_value(row.get(field))
        if value is not None and value > 0:
            return value
    return None


def _float_value(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _available_field_text(
    row: Mapping[str, Any],
    fields: Sequence[tuple[str, str, str | None]],
    *,
    default_unit: str | None = None,
) -> str:
    parts: list[str] = []
    for label, field, unit_field in fields:
        value = row.get(field)
        if value is None or value == "":
            continue
        unit = row.get(unit_field) if unit_field else None
        if unit is None and unit_field:
            unit = default_unit
        value_text = _value_with_unit(value, unit)
        if value_text:
            parts.append(f"{label} {value_text}")
    return "，".join(parts)


def _dated_text(row: Mapping[str, Any], text: str) -> str:
    row_time = _row_time(row)
    return f"{row_time}，{text}" if row_time else text


def _crypto_metric_lines(
    results: Sequence[Mapping[str, Any]],
    *,
    suppress_project_profile: bool = False,
) -> list[str]:
    project_parts: list[str] = []
    profile = (
        None
        if suppress_project_profile
        else _latest_result_row(
            results,
            "company_profile",
            require_any=("circulating_supply", "total_supply", "max_supply"),
        )
    )
    if profile:
        supply_unit = profile.get("supply_unit")
        project_parts.append(
            "项目资料可用："
            f"流通供应量 {_value_with_unit(profile.get('circulating_supply'), supply_unit)}，"
            f"总供应量 {_value_with_unit(profile.get('total_supply'), supply_unit)}，"
            f"最大供应量 {_value_with_unit(profile.get('max_supply'), supply_unit)}。"
        )

    derivative_parts: list[str] = []
    _append_latest_metric(
        derivative_parts,
        results,
        dataset="crypto_derivative_metric",
        field="funding_rate",
        label="资金费率",
        unit_field="funding_rate_unit",
    )
    _append_latest_metric(
        derivative_parts,
        results,
        dataset="crypto_derivative_metric",
        field="open_interest",
        label="OI",
        unit_field="open_interest_unit",
    )
    _append_latest_metric(
        derivative_parts,
        results,
        dataset="crypto_derivative_metric",
        field="long_short_ratio",
        label="多空比",
    )
    _append_latest_metric(
        derivative_parts,
        results,
        dataset="crypto_derivative_metric",
        field="liquidation_value",
        label="清算额",
        unit_field="liquidation_value_unit",
    )
    heatmap = _latest_result_row(results, "crypto_derivative_metric", require_any=("liquidation_price",))
    if heatmap:
        derivative_parts.append(
            "清算热图价格点 "
            f"{_value(heatmap.get('liquidation_price'))}"
            f"，规模 {_value(heatmap.get('liquidation_size') or heatmap.get('liquidation_value'))}"
            f"，时间 {_row_time(heatmap)}"
        )
    _append_latest_metric(
        derivative_parts,
        results,
        dataset="crypto_derivative_metric",
        field="cvd",
        label="CVD",
        unit_field="taker_volume_unit",
    )
    _append_latest_metric(
        derivative_parts,
        results,
        dataset="crypto_derivative_metric",
        field="etf_flow_usd",
        label="ETF资金流",
        unit_field="etf_flow_usd_unit",
    )
    cvd = _latest_result_row(results, "crypto_derivative_metric", require_any=("taker_buy_volume", "taker_sell_volume"))
    if cvd:
        derivative_parts.append(
            "主动买卖量 "
            f"买 {_value_with_unit(cvd.get('taker_buy_volume'), cvd.get('taker_volume_unit'))}"
            f"，卖 {_value_with_unit(cvd.get('taker_sell_volume'), cvd.get('taker_volume_unit'))}"
            f"，时间 {_row_time(cvd)}"
        )

    onchain_parts: list[str] = []
    _append_latest_metric(
        onchain_parts,
        results,
        dataset="crypto_onchain_metric",
        field="net_inflow",
        label="交易所净流量",
        unit_field="value_unit",
    )
    _append_latest_metric(
        onchain_parts,
        results,
        dataset="crypto_onchain_metric",
        field="exchange_balance",
        label="交易所余额",
        unit_field="value_unit",
    )
    _append_latest_metric(
        onchain_parts,
        results,
        dataset="crypto_onchain_metric",
        field="ahr999",
        label="AHR999",
        unit_field="value_unit",
    )

    lines: list[str] = []
    if project_parts:
        lines.extend(project_parts)
    if derivative_parts:
        lines.append("加密衍生品可用读数：" + "；".join(derivative_parts) + "。")
    if onchain_parts:
        lines.append("加密链上/估值可用读数：" + "；".join(onchain_parts) + "。")
    return lines


def _append_latest_metric(
    parts: list[str],
    results: Sequence[Mapping[str, Any]],
    *,
    dataset: str,
    field: str,
    label: str,
    unit_field: str | None = None,
) -> None:
    result_and_row = _latest_result_and_row(results, dataset, require_any=(field,))
    if result_and_row is None:
        return
    result, row = result_and_row
    parts.append(
        f"{label} {_value_with_unit(row.get(field), row.get(unit_field) if unit_field else None)}，"
        f"时间 {_row_time(row)}{_result_coverage_context(result)}"
    )


def _result_coverage_context(result: Mapping[str, Any]) -> str:
    pieces: list[str] = []
    row_count = _result_row_count(result)
    if row_count is not None and row_count > 1:
        pieces.append(f"样本 {row_count} 行")
    latest = next((item for item in result.get("latest_value_summary") or () if isinstance(item, Mapping)), None)
    if latest:
        granularity = str(latest.get("granularity") or "").strip()
        if granularity:
            pieces.append(f"粒度 {granularity}")
        sample_range = latest.get("sample_range")
        if isinstance(sample_range, (list, tuple)) and len(sample_range) == 2:
            pieces.append(f"覆盖 {sample_range[0]} 至 {sample_range[1]}")
    if not pieces:
        return ""
    return "（" + "，".join(pieces) + "）"


def _result_row_count(result: Mapping[str, Any]) -> int | None:
    raw_count = result.get("row_count")
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        count = len(_result_rows(result))
    return count if count >= 0 else None


def _news_boundary_line(
    results: Sequence[Mapping[str, Any]],
    *,
    ticker: str,
    company_name: str,
) -> str:
    company_rows = [
        row
        for result in results
        if _result_dataset(str(result.get("request_id") or "")) == "company_news"
        for row in _result_rows(result)
        if isinstance(row, Mapping)
    ]
    if not company_rows:
        return ""
    discovery_count = sum("discovery" in tuple(str(item) for item in row.get("source_roles") or ()) for row in company_rows)
    polluted = [
        row
        for row in company_rows
        if _looks_like_non_target_news(row, ticker=ticker, company_name=company_name)
    ]
    text = f"公司新闻线索有 {len(company_rows)} 条"
    if discovery_count:
        text += f"，其中 {discovery_count} 条是公开搜索/媒体线索，不是正式事实源"
    if polluted:
        text += f"；发现 {len(polluted)} 条疑似非标的结果，不能据此判断公司舆情活跃"
    return text + "。"


def _social_boundary_line(results: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        row
        for result in results
        if _result_dataset(str(result.get("request_id") or "")) == "social_signal"
        for row in _result_rows(result)
        if isinstance(row, Mapping)
    ]
    if not rows:
        return ""
    qa_count = sum(bool(row.get("question") or row.get("answer")) for row in rows)
    scored_count = sum(row.get("score") is not None or row.get("sentiment") is not None for row in rows)
    parts = [f"舆情/互动数据有 {len(rows)} 行"]
    if qa_count:
        parts.append(f"其中互动问答原文 {qa_count} 行")
    return "，".join(parts) + "。"


def _looks_like_non_target_news(row: Mapping[str, Any], *, ticker: str, company_name: str) -> bool:
    text = f"{row.get('title') or ''} {row.get('summary') or ''}"
    if company_name and company_name in text:
        return False
    code = ticker.split(".", 1)[0] if ticker else ""
    if code == "000001" and "上证指数" in text and "平安银行" not in text:
        return True
    return False


def _result_dataset(request_id: str) -> str:
    parts = request_id.split(":")
    return parts[4] if len(parts) >= 5 else ""


def _payload_request_key(payload: Mapping[str, Any]) -> str:
    request = payload.get("request")
    if not isinstance(request, Mapping):
        return ""
    request_id = str(request.get("request_id") or "").strip()
    if ":data:" in request_id:
        return request_id.rsplit(":data:", 1)[-1].strip()
    api_id = str(request.get("api_id") or "").strip()
    if api_id:
        return api_id
    return str(request.get("item") or "").strip()


def _payload_request_item(payload: Mapping[str, Any]) -> str:
    request = payload.get("request")
    if not isinstance(request, Mapping):
        return ""
    return str(request.get("item") or request.get("api_id") or "").strip()


def _result_rows(result: Mapping[str, Any]) -> tuple[Any, ...]:
    rows = result.get("rows")
    if rows:
        return tuple(rows)
    return tuple(result.get("sample_rows") or ())


def _row_time(row: Mapping[str, Any]) -> str:
    value = row.get("period") or row.get("period_end") or row.get("date") or row.get("published_at") or row.get("timestamp")
    if isinstance(value, datetime):
        return value.isoformat()[:10]
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:19]


def _row_sort_text(row: Mapping[str, Any]) -> str:
    return _row_time(row)


def _value(value: Any) -> str:
    if value is None or value == "":
        return ""
    return str(value)


def _value_with_unit(value: Any, unit: Any) -> str:
    value_text = _value(value)
    unit_text = str(unit or "").strip()
    if not unit_text or not value_text:
        return value_text
    human_text = _human_cny_amount(value, unit_text)
    if human_text:
        return f"{value_text} {unit_text}（约{human_text}）"
    return f"{value_text} {unit_text}"


def _human_cny_amount(value: Any, unit: str) -> str:
    cny_value = _money_to_cny(value, unit)
    if cny_value is None:
        return ""
    if abs(cny_value) >= 100_000_000:
        return f"{cny_value / 100_000_000:.2f}亿元"
    if abs(cny_value) >= 10_000:
        return f"{cny_value / 10_000:.2f}万元"
    return f"{cny_value:.2f}元"


def _money_to_cny(value: Any, unit: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    unit_text = str(unit or "").strip().upper().replace(" ", "_")
    if unit_text in {"CNY", "RMB", "人民币", "元"}:
        return number
    if unit_text in {"CNY_10K", "RMB_10K", "CNY_WAN", "CNY_万元", "万元", "人民币_万元"}:
        return number * 10_000
    if unit_text in {"CNY_100M", "RMB_100M", "CNY_YI", "CNY_亿元", "亿元", "人民币_亿元"}:
        return number * 100_000_000
    return None


def _margin_to_market_cap_line(
    margin_trading: Mapping[str, Any],
    valuation: Mapping[str, Any] | None,
) -> str:
    if valuation is None:
        return ""
    financing_balance = _money_to_cny(
        margin_trading.get("financing_balance"),
        margin_trading.get("amount_unit") or "CNY",
    )
    market_cap = _money_to_cny(valuation.get("market_cap"), valuation.get("market_cap_unit"))
    if financing_balance is None or market_cap is None or market_cap <= 0:
        return ""
    ratio = financing_balance / market_cap * 100
    return f"杠杆比例口径：融资余额与市值同按人民币换算，融资余额/市值约 {ratio:.2f}%；不得把万元口径市值错读成亿元后计算比例。"
