from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

_DATA_LAYER_ATTEMPT_SUMMARY_KEY = "provider_" + "attempts_summary"


def summarize_report_prefetch_manifest(
    manifest_path: Path,
    *,
    ticker: str = "",
    company_name: str = "",
) -> str:
    if not manifest_path.exists():
        return "本次运行没有可读的数据预取摘要；下游报告只能依据已批准上游报告，不得补写缺失的数据事实。"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"本次运行的数据预取摘要读取失败：{exc}。不得把缺失数据补写成事实。"
    if not isinstance(payload, Mapping):
        return "本次运行的数据预取摘要格式无效；不得把缺失数据补写成事实。"
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
        return "本次运行没有可读的数据需求结果摘要；下游报告只能依据已批准上游报告，不得补写缺失的数据事实。"
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
        if not payload.get("data_results"):
            missing = _normalize_data_need_payload_gap_for_summary(payload)
            if missing is not None:
                data_results.append(missing)
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


def _normalize_data_need_payload_gap_for_summary(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    request = payload.get("request")
    if not isinstance(request, Mapping):
        return None
    request_item = str(request.get("api_id") or request.get("item") or "").strip()
    if not request_item:
        return None
    return {
        "request_id": f"data_need:missing:summary:0:{_payload_request_key(payload) or request_item}",
        "request_key": _payload_request_key(payload),
        "request_item": _payload_request_item(payload),
        "status": payload.get("status") or "missing",
        "rows": (),
        "gaps": payload.get("gaps") or (),
    }


def _summarize_report_data_payload(
    payload: Mapping[str, Any],
    *,
    ticker: str,
    company_name: str,
) -> str:

    results = tuple(item for item in payload.get("data_results") or () if isinstance(item, Mapping))
    lines = [
        f"本次数据层整体状态：{_status_zh(payload.get('status'))}。",
        "以下内容是数据证据边界，不是投资结论；同一数据项只要这里已有可用读数，最终报告不得沿用上游早期“缺失/不可用/凭证缺失/来源调用失败”说法，只能披露样本、口径和连续性限制。",
    ]
    domain_lines = _domain_status_lines(payload.get("domain_statuses"))
    if domain_lines:
        lines.append("数据域状态：" + "；".join(domain_lines) + "。")

    financial = _latest_result_row(results, "financial_statement")
    if financial:
        lines.append(
            "财务报表最新记录："
            f"{_row_time(financial)}，"
            f"收入 {_value(financial.get('revenue'))}，"
            f"净利润 {_value(financial.get('net_income'))}，"
            f"资产 {_value(financial.get('assets'))}，"
            f"负债 {_value(financial.get('liabilities'))}，"
            f"经营现金流 {_value(financial.get('cash_flow'))}。"
        )
        lines.append("财报口径：收入、净利润、现金流通常是报告期累计流量；资产、负债是期末时点值。未给出单季度推导值时，不得写成单季度。")

    financial_metric = _latest_result_row(
        results,
        "financial_metric",
        require_any=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
    )
    if financial_metric:
        lines.append(
            "财务指标最新记录："
            f"{_row_time(financial_metric)}，"
            f"ROE {_value(financial_metric.get('roe'))}，"
            f"ROA {_value(financial_metric.get('roa'))}，"
            f"毛利率 {_value(financial_metric.get('gross_margin'))}，"
            f"资产负债率 {_value(financial_metric.get('debt_ratio'))}，"
            f"EPS {_value(financial_metric.get('eps'))}。"
        )
        lines.append("EPS/PE口径：上述EPS若来自季度或中报记录，只能作为该报告期每股收益；不得直接用季度EPS计算全年、TTM或静态PE。计算PE必须使用明确的全年EPS、TTM EPS或数据源直接返回的PE，并写明口径。")

    valuation = _latest_result_row(results, "valuation_metric", require_any=("pe", "pb", "ps"))
    if valuation:
        lines.append(
            "估值指标最新记录："
            f"{_row_time(valuation)}，"
            f"PE {_value(valuation.get('pe'))}，"
            f"PB {_value(valuation.get('pb'))}，"
            f"PS {_value(valuation.get('ps'))}，"
            f"市值 {_value_with_unit(valuation.get('market_cap'), valuation.get('market_cap_unit'))}。"
        )

    realtime_valuation = _latest_result_row(results, "valuation_metric", require_any=("price",))
    if realtime_valuation:
        lines.append(
            "实时估值/行情快照："
            f"{_row_time(realtime_valuation)}，"
            f"价格 {_value(realtime_valuation.get('price'))}，"
            f"市值 {_value_with_unit(realtime_valuation.get('market_cap'), realtime_valuation.get('market_cap_unit'))}。"
        )

    crypto_lines = _crypto_metric_lines(results)
    if crypto_lines:
        lines.extend(crypto_lines)

    missing_trade_data = _missing_trade_data_lines(results)
    if missing_trade_data:
        lines.append("交易执行数据缺口：" + "；".join(missing_trade_data) + "。")

    capital_flow = _latest_result_row(results, "capital_flow")
    if capital_flow:
        lines.append(
            "资金流数据可用："
            f"{_row_time(capital_flow)}，"
            f"主力净流入 {_value_with_unit(capital_flow.get('main_net'), capital_flow.get('amount_unit'))}。"
        )
    northbound_flow = _latest_result_row(results, "northbound_flow")
    if northbound_flow:
        lines.append(
            "北向资金数据可用："
            f"{_row_time(northbound_flow)}，"
            f"沪股通 {_value_with_unit(northbound_flow.get('hgt_net'), northbound_flow.get('amount_unit'))}，"
            f"深股通 {_value_with_unit(northbound_flow.get('sgt_net'), northbound_flow.get('amount_unit'))}，"
            f"合计 {_value_with_unit(northbound_flow.get('northbound_net'), northbound_flow.get('amount_unit'))}。"
        )
    margin_trading = _latest_result_row(results, "margin_trading")
    if margin_trading:
        lines.append(
            "融资融券数据可用："
            f"{_row_time(margin_trading)}，"
            f"融资余额 {_value(margin_trading.get('financing_balance'))}，"
            f"融资融券余额 {_value(margin_trading.get('margin_balance'))}。"
        )
    sector = _latest_result_row(results, "sector_snapshot")
    if sector:
        lines.append(
            "行业/板块资金快照有数据，但需核对是否匹配标的所属行业；"
            f"最新样例为 {_value(sector.get('sector_name'))}，主力净流入 {_value_with_unit(sector.get('main_net'), sector.get('amount_unit'))}。"
        )
    if _has_capital_direction_gap(results):
        lines.append("资金流口径：未提供主力净流入、买卖方向、席位身份或北向净流入时，大宗交易、逐笔成交和成交明细只能说明成交异动或资金分歧待确认，不能判断主力接筹、机构增减持或北向方向。")

    if _has_macro_gap(results):
        lines.append("宏观口径：未取得官方宏观、监管或政策数据时，社零、CPI、消费信心、政策落地进展等只能写为待验证，不能写成已发生事实或确定驱动力。")

    news_line = _news_boundary_line(results, ticker=ticker, company_name=company_name)
    if news_line:
        lines.append(news_line)

    social_line = _social_boundary_line(results)
    if social_line:
        lines.append(social_line)

    gap_lines = _gap_summary_lines(results)
    if gap_lines:
        lines.append("仍需披露的数据缺口：" + "；".join(gap_lines[:8]) + "。")
    return "\n".join(lines)


def _domain_status_lines(raw_statuses: object) -> list[str]:
    if not isinstance(raw_statuses, Sequence) or isinstance(raw_statuses, (str, bytes, bytearray)):
        return []
    lines: list[str] = []
    for item in raw_statuses:
        if not isinstance(item, Mapping):
            continue
        domain = {
            "market": "行情",
            "fundamental": "基本面",
            "news": "新闻",
            "social": "舆情",
        }.get(str(item.get("domain") or ""), str(item.get("domain") or "未知"))
        lines.append(f"{domain}{_status_zh(item.get('status'))}")
    return lines


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


def _crypto_metric_lines(results: Sequence[Mapping[str, Any]]) -> list[str]:
    project_parts: list[str] = []
    profile = _latest_result_row(
        results,
        "company_profile",
        require_any=("circulating_supply", "total_supply", "max_supply"),
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


def _missing_trade_data_lines(results: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    labels = {
        "intraday_bar": "日内分时数据缺失，不能做分时级别交易信号判断",
        "order_book_snapshot": "盘口快照缺失，不能判断实时委托深度和盘中流动性",
    }
    for result in results:
        dataset = _result_dataset(str(result.get("request_id") or ""))
        if dataset in labels and str(result.get("status") or "") in {"missing", "error"}:
            lines.append(labels[dataset])
    return list(dict.fromkeys(lines))


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
    if not scored_count:
        parts.append("未形成可直接判定乐观/悲观的情绪方向")
    return "，".join(parts) + "。"


def _has_capital_direction_gap(results: Sequence[Mapping[str, Any]]) -> bool:
    for result in results:
        dataset = _result_dataset(str(result.get("request_id") or ""))
        if dataset not in {"capital_flow", "hot_money_event", "sector_snapshot"}:
            continue
        if str(result.get("status") or "") in {"partial", "missing", "error"}:
            return True
        for row in _result_rows(result):
            if not isinstance(row, Mapping):
                continue
            if (
                row.get("main_net") is None
                and row.get("northbound_net") is None
                and row.get("buy_amount") is None
                and row.get("sell_amount") is None
                and row.get("side") is None
            ):
                return True
    return False


def _has_macro_gap(results: Sequence[Mapping[str, Any]]) -> bool:
    if _has_gap_free_result_for_dataset(results, "macro_series"):
        return False
    if _has_gap_free_result_for_dataset(results, "macro_news"):
        return False
    for result in results:
        if _result_dataset(str(result.get("request_id") or "")) != "macro_news":
            continue
        if str(result.get("status") or "") in {"partial", "missing", "error"}:
            return True
    return False


def _gap_summary_lines(results: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    gap_free_datasets = _gap_free_datasets(results)
    gap_free_request_keys = _gap_free_request_keys(results)
    for result in results:
        dataset_id = _result_dataset(str(result.get("request_id") or ""))
        request_key = str(result.get("request_key") or "").strip()
        if dataset_id in gap_free_datasets:
            continue
        if request_key and request_key in gap_free_request_keys:
            continue
        dataset = _result_label(result)
        status = str(result.get("status") or "")
        if status not in {"partial", "missing", "error"}:
            continue
        if not result.get("gaps"):
            lines.append(f"{dataset}{_status_zh(status)}")
            continue
        reasons = []
        for gap in result.get("gaps") or ():
            if isinstance(gap, Mapping):
                reasons.append(_gap_reason_label(gap.get("reason")))
        reason_text = "、".join(item for item in dict.fromkeys(reasons) if item)
        lines.append(f"{dataset}{_status_zh(status)}" + (f"（{reason_text}）" if reason_text else ""))
    return list(dict.fromkeys(lines))


def _gap_free_datasets(results: Sequence[Mapping[str, Any]]) -> set[str]:
    datasets: set[str] = set()
    for result in results:
        dataset = _result_dataset(str(result.get("request_id") or ""))
        if dataset and _result_has_gap_free_rows(result):
            datasets.add(dataset)
    return datasets


def _gap_free_request_keys(results: Sequence[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for result in results:
        key = str(result.get("request_key") or "").strip()
        if key and _result_has_gap_free_rows(result):
            keys.add(key)
    return keys


def _has_gap_free_result_for_dataset(results: Sequence[Mapping[str, Any]], dataset: str) -> bool:
    return any(
        _result_dataset(str(result.get("request_id") or "")) == dataset and _result_has_gap_free_rows(result)
        for result in results
    )


def _result_has_gap_free_rows(result: Mapping[str, Any]) -> bool:
    return bool(_result_rows(result)) and not bool(result.get("gaps"))


def _result_label(result: Mapping[str, Any]) -> str:
    item = str(result.get("request_item") or "").strip()
    if item:
        return item
    return _dataset_label(_result_dataset(str(result.get("request_id") or "")))


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
    return str(value or "时间未提供")[:19]


def _row_sort_text(row: Mapping[str, Any]) -> str:
    return _row_time(row)


def _value(value: Any) -> str:
    if value is None or value == "":
        return "未提供"
    return str(value)


def _value_with_unit(value: Any, unit: Any) -> str:
    value_text = _value(value)
    unit_text = str(unit or "").strip()
    return f"{value_text} {unit_text}" if unit_text and value_text != "未提供" else value_text


def _dataset_label(value: str) -> str:
    return {
        "daily_bar": "日线行情",
        "intraday_bar": "日内分时",
        "quote_snapshot": "实时行情",
        "order_book_snapshot": "盘口快照",
        "capital_flow": "资金流",
        "northbound_flow": "北向资金",
        "margin_trading": "融资融券",
        "sector_snapshot": "行业/板块快照",
        "financial_statement": "财务报表",
        "financial_metric": "财务指标",
        "valuation_metric": "估值指标",
        "crypto_derivative_metric": "加密衍生品",
        "crypto_onchain_metric": "加密链上/估值",
        "company_news": "公司新闻",
        "macro_series": "宏观序列",
        "macro_news": "宏观新闻",
        "official_filing": "公告",
        "social_signal": "舆情/互动",
    }.get(value, value or "未知数据")


def _gap_reason_label(value: Any) -> str:
    return {
        "credential_missing": "接口凭证缺失",
        "rate_limited": "来源限流",
        "provider_error": "来源调用失败",
        "provider_empty": "来源返回为空",
        "permission_denied": "接口权限不足",
        "cached_empty": "缓存记录为空",
        "empty_result": "来源返回为空",
        "warehouse_missing": "仓库没有可用记录",
        "field_missing": "必需字段缺失",
        "date_range_missing": "未覆盖完整分析区间",
        "data_integrity_failed": "本地数据校验失败",
    }.get(str(value or ""), str(value or ""))


def _status_zh(value: Any) -> str:
    return {
        "ready": "可用",
        "partial": "部分可用",
        "missing": "缺失",
        "error": "错误",
    }.get(str(value or ""), str(value or "未知"))
