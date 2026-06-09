from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


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

    results = tuple(item for item in payload.get("data_results") or () if isinstance(item, Mapping))
    lines = [
        f"本次数据层整体状态：{_status_zh(payload.get('status'))}。",
        "以下内容是数据证据边界，不是投资结论；上游报告若与这里的数字或缺口冲突，应明确说明冲突并回到数据证据核对。",
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
    sector = _latest_result_row(results, "sector_snapshot")
    if sector:
        lines.append(
            "行业/板块资金快照有数据，但需核对是否匹配标的所属行业；"
            f"最新样例为 {_value(sector.get('sector_name'))}，主力净流入 {_value_with_unit(sector.get('main_net'), sector.get('amount_unit'))}。"
        )

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
    rows: list[Mapping[str, Any]] = []
    for result in results:
        if _result_dataset(str(result.get("request_id") or "")) != dataset:
            continue
        for row in result.get("rows") or ():
            if not isinstance(row, Mapping):
                continue
            if require_any and not any(row.get(field) is not None for field in require_any):
                continue
            rows.append(row)
    if not rows:
        return None
    return sorted(rows, key=_row_sort_text, reverse=True)[0]


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
        for row in result.get("rows") or ()
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
        for row in result.get("rows") or ()
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


def _gap_summary_lines(results: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for result in results:
        dataset = _dataset_label(_result_dataset(str(result.get("request_id") or "")))
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
        "sector_snapshot": "行业/板块快照",
        "financial_statement": "财务报表",
        "financial_metric": "财务指标",
        "valuation_metric": "估值指标",
        "company_news": "公司新闻",
        "macro_news": "宏观新闻",
        "official_filing": "公告",
        "social_signal": "舆情/互动",
    }.get(value, value or "未知数据")


def _gap_reason_label(value: Any) -> str:
    return {
        "credential_missing": "接口凭证缺失",
        "rate_limited": "来源限流",
        "provider_error": "来源调用失败",
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
