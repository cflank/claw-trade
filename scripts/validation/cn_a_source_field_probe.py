from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ENV = ROOT / ".runtime" / "dev-services" / "runtime.env"
USER_AGENT = "claw-trade-data-source-field-probe/1.0"
TIMEOUT_SECONDS = 12
SYMBOL = "600519.SH"
CODE = "600519"
SH_CODE = "sh600519"
TS_CODE = "600519.SH"
COMPANY = "贵州茅台"


@dataclass(frozen=True)
class Probe:
    source_id: str
    data_type: str
    transport: str
    runner: Callable[[], tuple[int, Sequence[Mapping[str, Any]], str]]


class TimeoutGuard:
    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._previous: Any = None

    def __enter__(self) -> None:
        self._previous = signal.signal(signal.SIGALRM, self._raise)
        signal.alarm(self._seconds)

    def __exit__(self, *args: object) -> None:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self._previous)

    @staticmethod
    def _raise(signum: int, frame: object) -> None:
        del signum, frame
        raise TimeoutError("timeout")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    _load_runtime_env()
    started = datetime.now(tz=UTC)
    configured_sources = _configured_source_summary()
    probes = _build_probes()
    results = [_run_probe(probe) for probe in probes]
    payload = {
        "schema_version": "cn-a-source-field-probe-v1",
        "generated_at": started.isoformat(),
        "symbol": SYMBOL,
        "company": COMPANY,
        "date_window": _date_window(),
        "configured_sources": configured_sources,
        "results": results,
        "social_signal_required_fields": ("score", "mentions", "sentiment", "source", "timestamp"),
        "social_signal_summary": _summarize_social(results),
        "valuation_summary": _summarize_required(results, data_type="valuation_metric", required=("pe", "pb", "ps", "market_cap", "ev_ebitda")),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _print_summary(payload)
    return 0


def _build_probes() -> tuple[Probe, ...]:
    return (
        Probe("tushare.daily_basic", "valuation_metric", "managed_http_candidate", lambda: _tushare("daily_basic", {"ts_code": TS_CODE, "start_date": _compact_start(), "end_date": _compact_end()}, "ts_code,trade_date,pe,pb,ps,total_mv,circ_mv")),
        Probe("tushare.fina_indicator", "financial_metric", "managed_http_candidate", lambda: _tushare("fina_indicator", {"ts_code": TS_CODE, "start_date": _compact_start(), "end_date": _compact_end()}, "ts_code,end_date,roe,roa,grossprofit_margin,debt_to_assets,eps")),
        Probe("tushare.income", "financial_statement", "managed_http_candidate", lambda: _tushare("income", {"ts_code": TS_CODE, "start_date": _compact_start(), "end_date": _compact_end()}, "ts_code,end_date,revenue,n_income_attr_p,n_income,net_profit")),
        Probe("tushare.balancesheet", "financial_statement", "managed_http_candidate", lambda: _tushare("balancesheet", {"ts_code": TS_CODE, "start_date": _compact_start(), "end_date": _compact_end()}, "ts_code,end_date,total_assets,total_liab")),
        Probe("tushare.cashflow", "financial_statement", "managed_http_candidate", lambda: _tushare("cashflow", {"ts_code": TS_CODE, "start_date": _compact_start(), "end_date": _compact_end()}, "ts_code,end_date,n_cashflow_act,net_cash_flows_oper_act")),
        Probe("tushare.moneyflow", "hot_money_event", "managed_http_candidate", lambda: _tushare("moneyflow", {"ts_code": TS_CODE, "start_date": _compact_start(), "end_date": _compact_end()}, "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount")),
        Probe("tushare.moneyflow_hsgt", "hot_money_event", "managed_http_candidate", lambda: _tushare("moneyflow_hsgt", {"start_date": _compact_start(), "end_date": _compact_end()}, "trade_date,north_money,south_money")),
        Probe("cninfo.announcements", "official_filing", "managed_http_candidate", _cninfo_announcements),
        Probe("cninfo.unlock_search", "lockup_event", "managed_http_candidate", lambda: _cninfo_keyword("限售 解禁")),
        Probe("eastmoney.datacenter.dragon_tiger", "hot_money_event", "managed_http_candidate", lambda: _eastmoney_datacenter("RPT_DAILYBILLBOARD_DETAILSNEW", f'(SECURITY_CODE="{CODE}")')),
        Probe("eastmoney.datacenter.unlock", "lockup_event", "managed_http_candidate", lambda: _eastmoney_datacenter("RPT_LIFT_STAGE", f'(SECURITY_CODE="{CODE}")', sort_columns="FREE_DATE")),
        Probe("eastmoney.datacenter.shareholder", "lockup_event", "managed_http_candidate", lambda: _eastmoney_datacenter("RPT_HOLDERNUM_DET", f'(SECURITY_CODE="{CODE}")', sort_columns="END_DATE")),
        Probe("eastmoney.datacenter.block_trade", "lockup_event", "managed_http_candidate", lambda: _eastmoney_datacenter("RPT_DATA_BLOCKTRADE", f'(SECURITY_CODE="{CODE}")')),
        Probe("eastmoney.datacenter.margin", "lockup_event", "managed_http_candidate", lambda: _eastmoney_datacenter("RPTA_WEB_RZRQ_GGMX", f"(scode={CODE})", sort_columns="DATE", sort_types="-1")),
        Probe("eastmoney.datacenter.dividend", "lockup_event", "managed_http_candidate", lambda: _eastmoney_datacenter("RPT_SHAREBONUS_DET", f'(SECURITY_CODE="{CODE}")', sort_columns="EX_DIVIDEND_DATE")),
        Probe("eastmoney.push2his.fund_flow", "hot_money_event", "managed_http_candidate", _eastmoney_push2his_fund_flow),
        Probe("eastmoney.push2.sector_flow", "hot_money_event", "managed_http_candidate", _eastmoney_sector_flow),
        Probe("baidu.related_block", "social_signal", "managed_http_candidate", _baidu_related_block),
        Probe("google_news.rss.search", "company_news_or_social_discovery", "managed_http_candidate", _google_news),
        Probe("world_bank.cpi", "macro_news", "managed_http_candidate", _world_bank),
        Probe("sina.quote", "daily_bar_quote", "managed_http_candidate", _sina_quote),
        Probe("tencent.quote", "daily_bar_quote", "managed_http_candidate", _tencent_quote),
        Probe("akshare.stock_zh_a_hist", "daily_bar", "sdk_http_unknown", lambda: _akshare_df("stock_zh_a_hist", "stock_zh_a_hist", symbol=CODE, period="daily", start_date=_compact_start(), end_date=_compact_end(), adjust="qfq")),
        Probe("akshare.stock_zh_a_spot_em", "daily_bar_quote", "sdk_http_unknown", lambda: _akshare_df("stock_zh_a_spot_em", "stock_zh_a_spot_em")),
        Probe("akshare.stock_financial_analysis_indicator", "financial_metric", "sdk_http_unknown", lambda: _akshare_df("stock_financial_analysis_indicator", "stock_financial_analysis_indicator", symbol=CODE)),
        Probe("akshare.stock_a_lg_indicator", "valuation_metric", "sdk_http_unknown", lambda: _akshare_df("stock_a_lg_indicator", "stock_a_lg_indicator", symbol=CODE)),
        Probe("akshare.stock_news_em", "company_news", "sdk_http_unknown", lambda: _akshare_df("stock_news_em", "stock_news_em", symbol=CODE)),
        Probe("akshare.stock_zh_a_disclosure_report_cninfo", "official_filing", "sdk_http_unknown", lambda: _akshare_df("stock_zh_a_disclosure_report_cninfo", "stock_zh_a_disclosure_report_cninfo", symbol=CODE, market="沪深京", start_date=_compact_start(), end_date=_compact_end())),
        Probe("akshare.stock_board_concept_name_ths", "social_signal", "sdk_http_unknown", lambda: _akshare_df("stock_board_concept_name_ths", "stock_board_concept_name_ths")),
        Probe("akshare.stock_hot_rank_latest_em", "social_signal", "sdk_http_unknown", lambda: _akshare_df("stock_hot_rank_latest_em", "stock_hot_rank_latest_em", symbol=f"SH{CODE}")),
        Probe("akshare.stock_hot_keyword_em", "social_signal", "sdk_http_unknown", lambda: _akshare_df("stock_hot_keyword_em", "stock_hot_keyword_em", symbol=f"SH{CODE}")),
        Probe("akshare.stock_individual_fund_flow", "hot_money_event", "sdk_http_unknown", lambda: _akshare_df("stock_individual_fund_flow", "stock_individual_fund_flow", stock=CODE, market="sh")),
        Probe("akshare.stock_individual_fund_flow_rank", "hot_money_event", "sdk_http_unknown", lambda: _akshare_df("stock_individual_fund_flow_rank", "stock_individual_fund_flow_rank", indicator="今日")),
        Probe("akshare.stock_sector_fund_flow_rank.industry", "hot_money_event", "sdk_http_unknown", lambda: _akshare_df("stock_sector_fund_flow_rank", "stock_sector_fund_flow_rank", indicator="今日", sector_type="行业资金流")),
        Probe("akshare.stock_sector_fund_flow_rank.concept", "hot_money_event", "sdk_http_unknown", lambda: _akshare_df("stock_sector_fund_flow_rank", "stock_sector_fund_flow_rank", indicator="今日", sector_type="概念资金流")),
        Probe("baostock.query_history_k_data_plus", "daily_bar", "sdk_http_unknown", _baostock_daily),
        Probe("mootdx.quotes.stocks", "daily_bar_quote", "no_http", _mootdx_quote),
    )


def _run_probe(probe: Probe) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with TimeoutGuard(TIMEOUT_SECONDS):
            status_code, rows, source_url = probe.runner()
        row_count = len(rows)
        keys = _keys(rows)
        return {
            "source_id": probe.source_id,
            "data_type": probe.data_type,
            "transport": probe.transport,
            "status": "ok" if row_count else "empty",
            "status_code": status_code,
            "row_count": row_count,
            "fields": keys,
            "social_required_present": _present(keys, ("score", "mentions", "sentiment", "source", "timestamp")) if probe.data_type == "social_signal" else (),
            "valuation_required_present": _present(keys, ("pe", "pb", "ps", "market_cap", "total_mv", "ev_ebitda")) if probe.data_type == "valuation_metric" else (),
            "source_url": _redact_url(source_url),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "source_id": probe.source_id,
            "data_type": probe.data_type,
            "transport": probe.transport,
            "status": _error_status(exc),
            "error": str(exc)[:300],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


def _tushare(api_name: str, params: Mapping[str, Any], fields: str) -> tuple[int, Sequence[Mapping[str, Any]], str]:
    token, endpoint = _tushare_settings()
    if not token:
        raise RuntimeError("credential_missing:data_source:tushare")
    url = endpoint or "https://api.tushare.pro"
    payload = {
        "api_name": api_name,
        "token": token,
        "params": dict(params),
        "fields": fields,
    }
    status, body = _http_json(
        url,
        method="POST",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "application/json", "user-agent": USER_AGENT},
    )
    data = body if isinstance(body, Mapping) else {}
    if data.get("code") not in (0, "0", None):
        raise RuntimeError(f"provider_rejected:{data.get('code')}:{data.get('msg')}")
    fields_out = data.get("data", {}).get("fields", []) if isinstance(data.get("data"), Mapping) else []
    items = data.get("data", {}).get("items", []) if isinstance(data.get("data"), Mapping) else []
    rows = [dict(zip(fields_out, item, strict=False)) for item in items if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray))]
    return status, rows, f"{url}#{api_name}"


def _cninfo_announcements() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    return _cninfo_keyword("")


def _cninfo_keyword(keyword: str) -> tuple[int, Sequence[Mapping[str, Any]], str]:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    body = {
        "pageNum": 1,
        "pageSize": 20,
        "column": "sse",
        "tabName": "fulltext",
        "plate": "sh",
        "stock": f"{CODE},{COMPANY}",
        "searchkey": keyword,
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{_start_date().isoformat()}~{_end_date().isoformat()}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    status, payload = _http_json(
        url,
        method="POST",
        body=urllib.parse.urlencode(body).encode("utf-8"),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
            "user-agent": USER_AGENT,
            "x-requested-with": "XMLHttpRequest",
        },
    )
    rows = payload.get("announcements", []) if isinstance(payload, Mapping) else []
    return status, _row_seq(rows), url


def _eastmoney_datacenter(report_name: str, filter_expr: str, *, sort_columns: str = "TRADE_DATE", sort_types: str = "-1") -> tuple[int, Sequence[Mapping[str, Any]], str]:
    query = {
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "pageSize": 20,
        "pageNumber": 1,
        "reportName": report_name,
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": filter_expr,
    }
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    status, payload = _http_json(f"{url}?{urllib.parse.urlencode(query)}", headers={"accept": "application/json", "user-agent": USER_AGENT})
    if isinstance(payload, Mapping) and payload.get("success") is False:
        code = str(payload.get("code") or "").strip()
        message = str(payload.get("message") or "").strip()
        if code != "9201" and message != "返回数据为空":
            raise RuntimeError(f"provider_rejected:{code}:{message}")
    result = payload.get("result") if isinstance(payload, Mapping) else None
    data = result.get("data", []) if isinstance(result, Mapping) else []
    return status, _row_seq(data), url


def _eastmoney_push2his_fund_flow() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    query = {
        "lmt": 0,
        "klt": 101,
        "secid": f"1.{CODE}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    status, payload = _http_json(f"{url}?{urllib.parse.urlencode(query)}", headers={"accept": "application/json", "user-agent": USER_AGENT})
    klines = payload.get("data", {}).get("klines", []) if isinstance(payload, Mapping) else []
    rows = []
    for line in klines:
        if isinstance(line, str):
            parts = line.split(",")
            rows.append({f"f{i}": value for i, value in enumerate(parts, start=51)})
    return status, rows, url


def _eastmoney_sector_flow() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    query = {
        "pn": 1,
        "pz": 20,
        "po": 1,
        "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": 2,
        "invt": 2,
        "fid0": "f62",
        "fs": "m:90 t:3",
        "stat": 1,
        "fields": "f12,f14,f62,f66,f69,f72,f75,f78,f81,f84,f87",
        "rt": "52975239",
        "_": int(time.time() * 1000),
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    status, payload = _http_json(f"{url}?{urllib.parse.urlencode(query)}", headers={"accept": "application/json", "user-agent": USER_AGENT})
    diff = payload.get("data", {}).get("diff", []) if isinstance(payload, Mapping) else []
    return status, _row_seq(diff), url


def _baidu_related_block() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    query = {"code": CODE, "market": "ab", "typeCode": "all", "finClientType": "pc"}
    url = "https://finance.pae.baidu.com/api/getrelatedblock"
    status, payload = _http_json(
        f"{url}?{urllib.parse.urlencode(query)}",
        headers={
            "accept": "application/vnd.finance-web.v1+json",
            "origin": "https://gushitong.baidu.com",
            "referer": "https://gushitong.baidu.com/",
            "user-agent": USER_AGENT,
        },
    )
    rows: list[Mapping[str, Any]] = []
    result = payload.get("Result", []) if isinstance(payload, Mapping) else []
    for block in result if isinstance(result, Sequence) else ():
        if isinstance(block, Mapping):
            for item in block.get("list", []) or []:
                if isinstance(item, Mapping):
                    merged = dict(item)
                    merged["block_type"] = block.get("type")
                    rows.append(merged)
    return status, rows, url


def _google_news() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    query = {"q": f"{SYMBOL} {COMPANY} discussion", "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
    url = "https://news.google.com/rss/search"
    status, text = _http_text(f"{url}?{urllib.parse.urlencode(query)}", headers={"user-agent": USER_AGENT})
    root = ElementTree.fromstring(text)
    rows = []
    for item in root.findall(".//item"):
        rows.append({child.tag: child.text for child in item if child.text})
    return status, rows, url


def _world_bank() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    url = "https://api.worldbank.org/v2/country/CHN/indicator/FP.CPI.TOTL.ZG"
    status, payload = _http_json(f"{url}?format=json&per_page=5", headers={"accept": "application/json", "user-agent": USER_AGENT})
    rows = payload[1] if isinstance(payload, Sequence) and len(payload) > 1 else []
    return status, _row_seq(rows), url


def _sina_quote() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    url = f"https://hq.sinajs.cn/list={SH_CODE}"
    status, text = _http_text(url, headers={"referer": "https://finance.sina.com.cn", "user-agent": USER_AGENT}, encoding="gbk")
    prefix, _, payload = text.partition("=")
    values = payload.strip().strip('";').split(",")
    row = {"script_var": prefix, **{f"field_{index}": value for index, value in enumerate(values)}}
    return status, [row], url


def _tencent_quote() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    url = f"https://qt.gtimg.cn/q={SH_CODE}"
    status, text = _http_text(url, headers={"referer": "https://gu.qq.com", "user-agent": USER_AGENT}, encoding="gbk")
    prefix, _, payload = text.partition("=")
    values = payload.strip().strip('";').split("~")
    row = {"script_var": prefix, **{f"field_{index}": value for index, value in enumerate(values)}}
    return status, [row], url


def _akshare_df(module_name: str, function_name: str, **kwargs: Any) -> tuple[int, Sequence[Mapping[str, Any]], str]:
    import importlib

    module = importlib.import_module("akshare")
    func = getattr(module, function_name)
    frame = func(**kwargs)
    return 200, _frame_rows(frame), f"akshare://{module_name}"


def _baostock_daily() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    import baostock as bs

    login = bs.login()
    if getattr(login, "error_code", "") not in ("0", 0, ""):
        raise RuntimeError(f"baostock_login_failed:{getattr(login, 'error_msg', '')}")
    rows: list[Mapping[str, Any]] = []
    try:
        rs = bs.query_history_k_data_plus(
            "sh.600519",
            "date,code,open,high,low,close,volume,amount",
            start_date=_start_date().isoformat(),
            end_date=_end_date().isoformat(),
            frequency="d",
            adjustflag="2",
        )
        fields = list(getattr(rs, "fields", []) or [])
        while getattr(rs, "error_code", "0") == "0" and rs.next() and len(rows) < 20:
            rows.append(dict(zip(fields, rs.get_row_data(), strict=False)))
    finally:
        bs.logout()
    return 200, rows, "baostock://query_history_k_data_plus"


def _mootdx_quote() -> tuple[int, Sequence[Mapping[str, Any]], str]:
    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")
    frame = client.quotes(symbol=[CODE])
    return 200, _frame_rows(frame), "mootdx://quotes.stocks"


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, Any]:
    status, text = _http_text(url, method=method, body=body, headers=headers)
    return status, json.loads(text) if text.strip() else {}


def _http_text(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    encoding: str = "utf-8",
) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=dict(headers or {"user-agent": USER_AGENT}), method=method)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        data = response.read()
        return int(response.status), data.decode(encoding, errors="replace")


def _frame_rows(frame: Any) -> Sequence[Mapping[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "reset_index") and getattr(getattr(frame, "index", None), "name", None):
        frame = frame.reset_index()
    if hasattr(frame, "head"):
        frame = frame.head(20)
    if hasattr(frame, "to_dict"):
        rows = frame.to_dict(orient="records")
        return _row_seq(rows)
    if isinstance(frame, Mapping):
        return [frame]
    return []


def _row_seq(value: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _keys(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    keys: list[str] = []
    for row in rows[:3]:
        for key in row:
            key_text = str(key)
            if key_text not in keys:
                keys.append(key_text)
    return tuple(keys)


def _present(keys: Sequence[str], required: Sequence[str]) -> tuple[str, ...]:
    normalized = {_canonical_field(key) for key in keys}
    present = []
    for field in required:
        if field in normalized:
            present.append(field)
    if "market_cap" in required and "total_mv" in normalized:
        present.append("market_cap")
    if "timestamp" in required and normalized.intersection({"published_at", "date", "time", "trade_date", "update_time"}):
        present.append("timestamp")
    if "source" in required and normalized.intersection({"source", "media", "publisher", "文章来源"}):
        present.append("source")
    return tuple(dict.fromkeys(present))


def _canonical_field(key: str) -> str:
    text = str(key).strip().lower()
    mapping = {
        "热度": "score",
        "热度值": "score",
        "关注度": "score",
        "人气": "score",
        "排名": "score",
        "当前排名": "score",
        "情绪": "sentiment",
        "情感": "sentiment",
        "舆情": "sentiment",
        "发布时间": "published_at",
        "日期": "date",
        "时间": "time",
        "来源": "source",
        "文章来源": "source",
        "pe_ttm": "pe",
        "pb_mrq": "pb",
        "总市值": "market_cap",
        "total_mv": "market_cap",
    }
    return mapping.get(text, text)


def _summarize_social(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    social = [item for item in results if item.get("data_type") == "social_signal"]
    required = ("score", "mentions", "sentiment", "source", "timestamp")
    covered = sorted({field for item in social for field in item.get("social_required_present", ())})
    complete_sources = [item["source_id"] for item in social if set(item.get("social_required_present", ())) >= set(required)]
    return {
        "tested_sources": tuple(item["source_id"] for item in social),
        "covered_fields": tuple(covered),
        "missing_fields": tuple(field for field in required if field not in covered),
        "complete_sources": tuple(complete_sources),
    }


def _summarize_required(results: Sequence[Mapping[str, Any]], *, data_type: str, required: Sequence[str]) -> dict[str, Any]:
    items = [item for item in results if item.get("data_type") == data_type]
    covered = sorted({field for item in items for field in item.get("valuation_required_present", ())})
    return {
        "tested_sources": tuple(item["source_id"] for item in items),
        "covered_fields": tuple(covered),
        "missing_fields": tuple(field for field in required if field not in covered),
    }


def _configured_source_summary() -> tuple[Mapping[str, Any], ...]:
    try:
        db = _mongo_db()
        docs = db["ui_data_source_settings"].find({})
        rows = []
        for doc in docs:
            record = doc.get("record", doc) if isinstance(doc, Mapping) else {}
            rows.append(
                {
                    "supported_type": record.get("supported_type"),
                    "enabled": bool(record.get("enabled")),
                    "has_credential_ref": bool(record.get("credential_ref")),
                    "has_endpoint_url": bool(record.get("endpoint_url") or record.get("endpointUrl")),
                }
            )
        return tuple(rows)
    except Exception as exc:
        return ({"error": str(exc)[:200]},)


def _tushare_settings() -> tuple[str | None, str | None]:
    db = _mongo_db()
    record: Mapping[str, Any] | None = None
    for doc in db["ui_data_source_settings"].find({}):
        candidate = doc.get("record", doc) if isinstance(doc, Mapping) else {}
        if str(candidate.get("supported_type", "")).strip() == "tushare" and bool(candidate.get("enabled", False)):
            record = candidate
            break
    if record is None:
        return None, None
    ref = str(record.get("credential_ref") or "").strip()
    secret_doc = db["ui_secret_settings"].find_one({"_id": ref}) if ref else None
    token = str(secret_doc.get("value")) if isinstance(secret_doc, Mapping) and isinstance(secret_doc.get("value"), str) else None
    endpoint = str(record.get("endpoint_url") or record.get("endpointUrl") or "").strip() or None
    return token, endpoint


def _mongo_db() -> Any:
    from pymongo import MongoClient

    uri = os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("DATA_GATEWAY_MONGODB_URI missing")
    db_name = os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip() or os.environ.get("CN_A_MONGODB_DATABASE", "").strip() or "claw_trade"
    return MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]


def _load_runtime_env() -> None:
    if not RUNTIME_ENV.exists():
        return
    for line in RUNTIME_ENV.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _date_window() -> Mapping[str, str]:
    return {"start": _start_date().isoformat(), "end": _end_date().isoformat()}


def _start_date() -> date:
    return _end_date() - timedelta(days=730)


def _end_date() -> date:
    return date(2026, 6, 1)


def _compact_start() -> str:
    return _start_date().strftime("%Y%m%d")


def _compact_end() -> str:
    return _end_date().strftime("%Y%m%d")


def _error_status(exc: Exception) -> str:
    text = str(exc).lower()
    if "credential_missing" in text:
        return "credential_missing"
    if "timeout" in text or isinstance(exc, TimeoutError):
        return "timeout"
    if "http error 4" in text or "provider_rejected" in text:
        return "provider_rejected"
    return "error"


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = []
    for key, value in query:
        if key.lower() in {"token", "api_key", "apikey", "secret", "key"}:
            redacted.append((key, "<redacted>"))
        else:
            redacted.append((key, value))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment))


def _print_summary(payload: Mapping[str, Any]) -> None:
    results = payload["results"]
    print(f"field_probe_output={payload.get('generated_at')}")
    print(f"tested_sources={len(results)}")
    counts: dict[str, int] = {}
    for item in results:
        counts[str(item.get("status"))] = counts.get(str(item.get("status")), 0) + 1
    print("status_counts=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    print("social_signal_summary=" + json.dumps(payload["social_signal_summary"], ensure_ascii=False, sort_keys=True))
    print("valuation_summary=" + json.dumps(payload["valuation_summary"], ensure_ascii=False, sort_keys=True))
    print(f"evidence_path={Path(sys.argv[sys.argv.index('--output') + 1]).resolve() if '--output' in sys.argv else ''}")


if __name__ == "__main__":
    raise SystemExit(main())
