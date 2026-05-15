from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

import pandas as pd

from claw_trade.instruments import InstrumentResolveError, resolve_hk_identity
from claw_trade.providers.tushare_client import TushareClientConfigError, create_tushare_pro


_HK_FUNDAMENTAL_TOOL = "fundamental_fundamentals_data_pack"
_HK_FUNDAMENTAL_WORKER = "fundamental_analyst"
_DEFAULT_WINDOW_DAYS = 365
_TUSHARE_PROVIDER = "tushare_pro_hk"
_AKSHARE_PROVIDER = "akshare_hk"
_YAHOO_PROVIDER = "yahoo_finance_hk"
_PERMISSION_HINTS = ("permission", "access denied", "forbidden", "denied", "unauthorized", "权限")
_RATE_HINTS = ("rate limit", "too many request", "frequency", "quota", "积分", "频次", "额度", "超限")
_SECRET_PATTERNS = (
    re.compile(r"token=([^&\s]+)", re.IGNORECASE),
    re.compile(r"mongodb://[^@\s]+@", re.IGNORECASE),
    re.compile(r"https?://([^:/\s]+):([^@\s]+)@", re.IGNORECASE),
)


def run_hk_fundamentals_data_pack(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context)
    request = _normalize_hk_input(tool_input, context=context)
    attempts: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}
    fields: dict[str, dict[str, Any]] = {}

    basic_result = _execute_dataframe_provider(
        provider=_TUSHARE_PROVIDER,
        endpoint="hk_basic",
        role="primary_company_profile",
        provider_symbol=request["provider_symbols"]["tushare_ts_code"],
        call=lambda: _call_tushare_hk_basic(request["provider_symbols"]["tushare_ts_code"]),
        mapper=lambda df: _map_tushare_hk_basic(df, request=request),
    )
    attempts.append(basic_result["attempt"])
    raw_payloads["tushare_hk_basic"] = basic_result["raw_payload"]
    _merge_fields(fields, basic_result["fields"])

    fina_result = _execute_dataframe_provider(
        provider=_TUSHARE_PROVIDER,
        endpoint="hk_fina_indicator",
        role="primary_financial_indicators",
        provider_symbol=request["provider_symbols"]["tushare_ts_code"],
        call=lambda: _call_tushare_hk_fina_indicator(
            request["provider_symbols"]["tushare_ts_code"],
            start_date=request["start_date"],
            end_date=request["end_date"],
        ),
        mapper=lambda df: _map_tushare_hk_fina_indicator(df),
    )
    attempts.append(fina_result["attempt"])
    raw_payloads["tushare_hk_fina_indicator"] = fina_result["raw_payload"]
    _merge_fields(fields, fina_result["fields"])

    if _needs_financial_supplement(fields, fina_result["attempt"]):
        ak_indicator_result = _execute_dataframe_provider(
            provider=_AKSHARE_PROVIDER,
            endpoint="stock_financial_hk_analysis_indicator_em",
            role="supplement_financial_indicators",
            provider_symbol=request["provider_symbols"]["akshare_code"],
            call=lambda: _call_akshare_hk_indicator(request["provider_symbols"]["akshare_code"]),
            mapper=lambda df: _map_akshare_hk_indicator(df),
        )
        attempts.append(ak_indicator_result["attempt"])
        raw_payloads["akshare_hk_indicator"] = ak_indicator_result["raw_payload"]
        _merge_fields(fields, ak_indicator_result["fields"])

    if _needs_statement_supplement(fields, attempts):
        ak_income_result = _execute_dataframe_provider(
            provider=_AKSHARE_PROVIDER,
            endpoint="stock_financial_hk_report_em:income",
            role="supplement_income_statement",
            provider_symbol=request["provider_symbols"]["akshare_code"],
            call=lambda: _call_akshare_hk_report_income(request["provider_symbols"]["akshare_code"]),
            mapper=lambda df: _map_akshare_hk_income_report(df),
        )
        attempts.append(ak_income_result["attempt"])
        raw_payloads["akshare_hk_income_report"] = ak_income_result["raw_payload"]
        _merge_fields(fields, ak_income_result["fields"])

    if _needs_yahoo_supplement(fields, attempts):
        yahoo_result = _execute_mapping_provider(
            provider=_YAHOO_PROVIDER,
            endpoint="Ticker.info+financials",
            role="supplement_valuation_and_statements",
            provider_symbol=request["provider_symbols"]["yahoo_symbol"],
            call=lambda: _call_yahoo_hk_fundamentals(request["provider_symbols"]["yahoo_symbol"]),
            mapper=lambda payload: _map_yahoo_hk_fundamentals(payload),
        )
        attempts.append(yahoo_result["attempt"])
        raw_payloads["yahoo_finance_hk"] = yahoo_result["raw_payload"]
        _merge_fields(fields, yahoo_result["fields"])

    missing_items = _missing_hk_fundamental_items(fields)
    status = "complete" if not missing_items else ("partial" if fields else "failed")
    pack = {
        "ok": status != "failed",
        "schema_version": "hk_fundamental_pack.v1",
        "tool_name": _HK_FUNDAMENTAL_TOOL,
        "domain": "fundamental",
        "input": request,
        "quality": {
            "status": status,
            "warnings": [f"missing: {', '.join(missing_items)}"] if missing_items else [],
        },
        "provider_attempts": attempts,
        "domain_data": {
            "fields": fields,
            "missing_items": missing_items,
            "source_priority": "tushare_first_then_free_supplement",
        },
    }
    pack["reader_brief"] = _hk_fundamental_brief(request, fields, attempts, missing_items)
    _write_pack_files(context, _HK_FUNDAMENTAL_TOOL, pack, raw_payloads)
    return pack


def _normalize_context(runtime_context: Mapping[str, Any]) -> dict[str, str]:
    run_id = _required_text(runtime_context, "run_id", "runtime_context")
    worker_id = _required_text(runtime_context, "worker_id", "runtime_context")
    if worker_id != _HK_FUNDAMENTAL_WORKER:
        raise ValueError(f"runtime_context.worker_id must be {_HK_FUNDAMENTAL_WORKER}")
    stage = _optional_text(runtime_context, "stage") or "frontline"
    if stage != "frontline":
        raise ValueError("runtime_context.stage must be frontline")
    tool_name = _optional_text(runtime_context, "tool_name") or _HK_FUNDAMENTAL_TOOL
    if tool_name != _HK_FUNDAMENTAL_TOOL:
        raise ValueError(f"runtime_context.tool_name must be {_HK_FUNDAMENTAL_TOOL}")
    call_id = _optional_text(runtime_context, "call_id") or f"{run_id}-call"
    dispatch_id = _optional_text(runtime_context, "dispatch_id") or call_id
    current_time = _optional_text(runtime_context, "current_time") or datetime.now(UTC).isoformat()
    current_date = _optional_text(runtime_context, "current_date") or current_time[:10]
    evidence_root = _optional_text(runtime_context, "evidence_root") or "/tmp/claw-trade/frontline-evidence"
    return {
        "run_id": run_id,
        "worker_id": worker_id,
        "stage": stage,
        "tool_name": tool_name,
        "call_id": call_id,
        "dispatch_id": dispatch_id,
        "current_time": current_time,
        "current_date": current_date,
        "evidence_root": evidence_root,
    }


def _normalize_hk_input(tool_input: Mapping[str, Any], *, context: Mapping[str, str]) -> dict[str, Any]:
    ticker = _optional_text(tool_input, "ticker")
    if ticker is None:
        raise ValueError("tool_input.ticker is required")
    market = (_optional_text(tool_input, "market") or "HK").upper()
    if market != "HK":
        raise ValueError("fundamental_fundamentals_data_pack requires market=HK for HK input")
    try:
        identity = resolve_hk_identity(ticker)
    except InstrumentResolveError as exc:
        raise ValueError(str(exc)) from exc
    end_date = _parse_date(_optional_text(tool_input, "end_date"), fallback=_date_from_context(context["current_date"]))
    start_date = _parse_date(
        _optional_text(tool_input, "start_date"),
        fallback=end_date - timedelta(days=_DEFAULT_WINDOW_DAYS - 1),
    )
    if end_date < start_date:
        raise ValueError("tool_input.end_date must not be earlier than start_date")
    return {
        "ticker": identity.canonical_ticker,
        "display_ticker": identity.display_ticker,
        "market": "HK",
        "company_name": _optional_text(tool_input, "company_name"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "currency": identity.currency,
        "currency_symbol": identity.currency_symbol,
        "provider_symbols": {
            "tushare_ts_code": identity.provider_symbols.tushare_ts_code,
            "akshare_code": identity.provider_symbols.akshare_code,
            "yahoo_symbol": identity.provider_symbols.yahoo_symbol,
        },
    }


def _call_tushare_hk_basic(ts_code: str) -> Any:
    pro = _create_tushare_pro()
    return pro.hk_basic(ts_code=ts_code)


def _call_tushare_hk_fina_indicator(ts_code: str, *, start_date: str, end_date: str) -> Any:
    pro = _create_tushare_pro()
    return pro.hk_fina_indicator(
        ts_code=ts_code,
        start_date=_yyyymmdd(start_date),
        end_date=_yyyymmdd(end_date),
    )


def _call_akshare_hk_indicator(symbol: str) -> Any:
    import akshare as ak  # type: ignore

    return ak.stock_financial_hk_analysis_indicator_em(symbol=symbol, indicator="年度")


def _call_akshare_hk_report_income(symbol: str) -> Any:
    import akshare as ak  # type: ignore

    return ak.stock_financial_hk_report_em(stock=symbol, symbol="利润表", indicator="年度")


def _call_yahoo_hk_fundamentals(symbol: str) -> dict[str, Any]:
    import yfinance as yf  # type: ignore

    ticker = yf.Ticker(symbol)
    info = dict(getattr(ticker, "info", {}) or {})
    return {
        "info": info,
        "income_stmt": _dataframe_records(getattr(ticker, "income_stmt", None)),
        "balance_sheet": _dataframe_records(getattr(ticker, "balance_sheet", None)),
        "cashflow": _dataframe_records(getattr(ticker, "cashflow", None)),
    }


def _create_tushare_pro() -> Any:
    try:
        return create_tushare_pro()
    except TushareClientConfigError as exc:
        raise RuntimeError(f"tushare config error: {exc}") from exc


def _execute_dataframe_provider(
    *,
    provider: str,
    endpoint: str,
    role: str,
    provider_symbol: str,
    call: Callable[[], Any],
    mapper: Callable[[Any], dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    return _execute_mapping_provider(
        provider=provider,
        endpoint=endpoint,
        role=role,
        provider_symbol=provider_symbol,
        call=lambda: _dataframe_payload(call()),
        mapper=lambda payload: mapper(payload["dataframe"]),
    )


def _execute_mapping_provider(
    *,
    provider: str,
    endpoint: str,
    role: str,
    provider_symbol: str,
    call: Callable[[], Any],
    mapper: Callable[[Any], dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    started = _now_iso()
    started_perf = datetime.now(UTC)
    raw_payload: Any | None = None
    fields: dict[str, dict[str, Any]] = {}
    status = "success"
    error_code: str | None = None
    error_message: str | None = None
    raw_count = 0
    try:
        raw_payload = call()
        if isinstance(raw_payload, Mapping) and "rows" in raw_payload and "dataframe" in raw_payload:
            raw_count = len(raw_payload["rows"])
            fields = mapper(raw_payload)
            raw_payload = raw_payload["rows"]
        else:
            raw_count = _raw_count(raw_payload)
            fields = mapper(raw_payload)
        if raw_count == 0 or not fields:
            status = "empty"
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error_code = _classify_provider_error(exc)
        error_message = _redact(str(exc))
        fields = {}
        raw_payload = None
        raw_count = 0

    finished = _now_iso()
    elapsed_ms = max(0, int((datetime.now(UTC) - started_perf).total_seconds() * 1000))
    attempt = {
        "provider": provider,
        "endpoint": endpoint,
        "role": role,
        "provider_symbol": provider_symbol,
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "elapsed_ms": elapsed_ms,
        "raw_count": raw_count,
        "accepted_count": len(fields),
        "error_code": error_code,
        "error_message_redacted": error_message,
    }
    return {"attempt": attempt, "fields": fields, "raw_payload": raw_payload}


def _dataframe_payload(dataframe: Any) -> dict[str, Any]:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict"):
        raise TypeError("provider did not return a DataFrame")
    rows = [] if bool(dataframe.empty) else _to_json_rows(dataframe.to_dict(orient="records"))
    return {"dataframe": dataframe, "rows": rows}


def _map_tushare_hk_basic(dataframe: Any, *, request: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _to_json_rows(dataframe.to_dict(orient="records")) if not bool(dataframe.empty) else []
    latest = _first_row(rows)
    fields: dict[str, dict[str, Any]] = {}
    if latest is None:
        return fields
    provider = _TUSHARE_PROVIDER
    endpoint = "hk_basic"
    _set_field(fields, "company_profile.company_name", latest.get("name"), "text", provider, endpoint)
    _set_field(fields, "company_profile.full_name", latest.get("fullname"), "text", provider, endpoint)
    _set_field(fields, "company_profile.listing_market", latest.get("market"), "text", provider, endpoint)
    _set_field(fields, "company_profile.listing_date", _date_like(latest.get("list_date")), "date", provider, endpoint)
    _set_field(fields, "company_profile.trade_unit", latest.get("trade_unit"), "shares", provider, endpoint)
    _set_field(fields, "company_profile.currency", latest.get("curr_type") or request.get("currency"), "currency", provider, endpoint)
    _set_field(fields, "company_profile.isin", latest.get("isin"), "text", provider, endpoint)
    return fields


def _map_tushare_hk_fina_indicator(dataframe: Any) -> dict[str, dict[str, Any]]:
    rows = _to_json_rows(dataframe.to_dict(orient="records")) if not bool(dataframe.empty) else []
    latest = _latest_row(rows, ("end_date", "ann_date"))
    if latest is None:
        return {}
    return _map_financial_indicator_row(latest, provider=_TUSHARE_PROVIDER, endpoint="hk_fina_indicator")


def _map_akshare_hk_indicator(dataframe: Any) -> dict[str, dict[str, Any]]:
    rows = _to_json_rows(dataframe.to_dict(orient="records")) if not bool(dataframe.empty) else []
    latest = _latest_row(rows, ("REPORT_DATE", "START_DATE"))
    if latest is None:
        return {}
    fields: dict[str, dict[str, Any]] = {}
    provider = _AKSHARE_PROVIDER
    endpoint = "stock_financial_hk_analysis_indicator_em"
    _set_field(fields, "company_profile.company_name", latest.get("SECURITY_NAME_ABBR"), "text", provider, endpoint)
    _set_field(fields, "company_profile.currency", latest.get("CURRENCY"), "currency", provider, endpoint)
    report_period = _date_like(latest.get("REPORT_DATE"))
    _set_field(fields, "report.period", report_period, "date", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.eps", latest.get("BASIC_EPS"), "per_share", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.eps_ttm", latest.get("EPS_TTM"), "per_share", provider, endpoint, report_period=report_period)
    _set_field(fields, "balance_sheet.book_value_per_share", latest.get("BPS"), "per_share", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.revenue", latest.get("OPERATE_INCOME"), "currency", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.net_profit", latest.get("HOLDER_PROFIT"), "currency", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.roe", latest.get("ROE_AVG") or latest.get("ROE_YEARLY"), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.roa", latest.get("ROA"), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.gross_margin", latest.get("GROSS_PROFIT_RATIO"), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.netprofit_margin", latest.get("NET_PROFIT_RATIO"), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.debt_to_assets", latest.get("DEBT_ASSET_RATIO"), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "cash_flow.operating_cash_flow_to_revenue", latest.get("PER_NETCASH_OPERATE"), "%", provider, endpoint, report_period=report_period)
    return fields


def _map_akshare_hk_income_report(dataframe: Any) -> dict[str, dict[str, Any]]:
    rows = _to_json_rows(dataframe.to_dict(orient="records")) if not bool(dataframe.empty) else []
    if not rows:
        return {}
    latest_period = _latest_row(rows, ("REPORT_DATE",))
    if latest_period is None:
        return {}
    report_date = latest_period.get("REPORT_DATE")
    latest_rows = [row for row in rows if row.get("REPORT_DATE") == report_date]
    provider = _AKSHARE_PROVIDER
    endpoint = "stock_financial_hk_report_em:income"
    fields: dict[str, dict[str, Any]] = {}
    values_by_name = {str(row.get("STD_ITEM_NAME") or "").strip(): row.get("AMOUNT") for row in latest_rows}
    report_period = _date_like(report_date)
    _set_field(fields, "income_statement.revenue", _first_value(values_by_name, ("营业额", "营业收入", "收入")), "currency", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.net_profit", _first_value(values_by_name, ("公司股东应占溢利", "股东应占溢利", "净利润")), "currency", provider, endpoint, report_period=report_period)
    return fields


def _map_yahoo_hk_fundamentals(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    provider = _YAHOO_PROVIDER
    endpoint = "Ticker.info+financials"
    fields: dict[str, dict[str, Any]] = {}
    info = payload.get("info")
    if isinstance(info, Mapping):
        _set_field(fields, "company_profile.company_name", info.get("longName") or info.get("shortName"), "text", provider, endpoint)
        _set_field(fields, "company_profile.currency", info.get("currency"), "currency", provider, endpoint)
        _set_field(fields, "valuation.pe_ttm", info.get("trailingPE"), "ratio", provider, endpoint)
        _set_field(fields, "valuation.pb", info.get("priceToBook"), "ratio", provider, endpoint)
        _set_field(fields, "valuation.market_cap", info.get("marketCap"), "currency", provider, endpoint)
        roe = info.get("returnOnEquity")
        if isinstance(roe, (int, float)):
            roe = float(roe) * 100.0
        _set_field(fields, "financial_indicators.roe", roe, "%", provider, endpoint)
    latest_income = _latest_statement_column(payload.get("income_stmt"))
    if latest_income:
        period = latest_income.get("period")
        _set_field(fields, "income_statement.revenue", _first_value(latest_income, ("Total Revenue", "Operating Revenue")), "currency", provider, endpoint, report_period=period)
        _set_field(fields, "income_statement.net_profit", _first_value(latest_income, ("Net Income", "Net Income Common Stockholders")), "currency", provider, endpoint, report_period=period)
        _set_field(fields, "income_statement.eps", _first_value(latest_income, ("Basic EPS", "Diluted EPS")), "per_share", provider, endpoint, report_period=period)
    latest_cashflow = _latest_statement_column(payload.get("cashflow"))
    if latest_cashflow:
        period = latest_cashflow.get("period")
        _set_field(fields, "cash_flow.operating_cash_flow", _first_value(latest_cashflow, ("Operating Cash Flow",)), "currency", provider, endpoint, report_period=period)
    return fields


def _map_financial_indicator_row(row: Mapping[str, Any], *, provider: str, endpoint: str) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    report_period = _date_like(row.get("end_date") or row.get("REPORT_DATE"))
    _set_field(fields, "company_profile.company_name", row.get("name"), "text", provider, endpoint)
    _set_field(fields, "company_profile.currency", row.get("currency") or row.get("CURRENCY"), "currency", provider, endpoint)
    _set_field(fields, "report.period", report_period, "date", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.eps", _first_value(row, ("basic_eps", "eps", "BASIC_EPS")), "per_share", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.eps_ttm", _first_value(row, ("eps_ttm", "EPS_TTM")), "per_share", provider, endpoint, report_period=report_period)
    _set_field(fields, "balance_sheet.book_value_per_share", _first_value(row, ("bps", "BPS")), "per_share", provider, endpoint, report_period=report_period)
    _set_field(fields, "valuation.pe_ttm", _first_value(row, ("pe_ttm", "PE_TTM", "pe", "PE")), "ratio", provider, endpoint, report_period=report_period)
    _set_field(fields, "valuation.pb", _first_value(row, ("pb", "PB")), "ratio", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.revenue", _first_value(row, ("total_revenue", "operate_income", "OPERATE_INCOME")), "currency", provider, endpoint, report_period=report_period)
    _set_field(fields, "income_statement.net_profit", _first_value(row, ("n_income_attr_p", "holder_profit", "net_profit", "HOLDER_PROFIT")), "currency", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.roe", _first_value(row, ("roe_avg", "roe_yearly", "roe_waa", "roe", "ROE_AVG", "ROE_YEARLY")), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.roa", _first_value(row, ("roa", "ROA")), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.gross_margin", _first_value(row, ("grossprofit_margin", "GROSS_PROFIT_RATIO")), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.netprofit_margin", _first_value(row, ("netprofit_margin", "NET_PROFIT_RATIO")), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "financial_indicators.debt_to_assets", _first_value(row, ("debt_to_assets", "DEBT_ASSET_RATIO")), "%", provider, endpoint, report_period=report_period)
    _set_field(fields, "cash_flow.operating_cash_flow_per_share", _first_value(row, ("ocfps", "PER_NETCASH_OPERATE")), "per_share", provider, endpoint, report_period=report_period)
    return fields


def _needs_financial_supplement(fields: Mapping[str, Any], attempt: Mapping[str, Any]) -> bool:
    if attempt.get("status") != "success":
        return True
    return any(name not in fields for name in ("financial_indicators.roe", "income_statement.revenue", "income_statement.net_profit"))


def _needs_statement_supplement(fields: Mapping[str, Any], attempts: list[Mapping[str, Any]]) -> bool:
    del attempts
    return any(name not in fields for name in ("income_statement.revenue", "income_statement.net_profit"))


def _needs_yahoo_supplement(fields: Mapping[str, Any], attempts: list[Mapping[str, Any]]) -> bool:
    if not any(attempt.get("provider") in {_AKSHARE_PROVIDER, _TUSHARE_PROVIDER} for attempt in attempts):
        return True
    return any(name not in fields for name in ("valuation.pe_ttm", "valuation.pb", "financial_indicators.roe"))


def _merge_fields(target: dict[str, dict[str, Any]], incoming: Mapping[str, dict[str, Any]]) -> None:
    for name, value in incoming.items():
        target.setdefault(name, dict(value))


def _missing_hk_fundamental_items(fields: Mapping[str, Any]) -> list[str]:
    required = (
        "company_profile.company_name",
        "company_profile.currency",
        "financial_indicators.roe",
        "income_statement.revenue",
        "income_statement.net_profit",
    )
    return [name for name in required if name not in fields]


def _set_field(
    fields: dict[str, dict[str, Any]],
    name: str,
    value: Any,
    unit: str,
    provider: str,
    endpoint: str,
    *,
    report_period: str | None = None,
) -> None:
    if value in (None, ""):
        return
    fields[name] = {
        "value": _json_scalar(value),
        "unit": unit,
        "provider": provider,
        "endpoint": endpoint,
        "report_period": report_period,
    }


def _hk_fundamental_brief(
    request: Mapping[str, Any],
    fields: Mapping[str, Mapping[str, Any]],
    attempts: list[Mapping[str, Any]],
    missing_items: list[str],
) -> str:
    successful = [f"{item.get('provider')}.{item.get('endpoint')}" for item in attempts if item.get("status") == "success"]
    failed = [
        f"{item.get('provider')}.{item.get('endpoint')}:{item.get('error_code') or item.get('status')}"
        for item in attempts
        if item.get("status") != "success"
    ]
    field_lines = []
    for name in sorted(fields):
        item = fields[name]
        field_lines.append(
            f"- {name}: {item.get('value')} ({item.get('unit')}, {item.get('provider')}.{item.get('endpoint')})"
        )
    missing_text = "无" if not missing_items else ", ".join(missing_items)
    return "\n".join(
        [
            f"港股基本面资料包：{request['display_ticker']}。",
            f"主源策略：优先使用 Tushare；Tushare 单接口失败、无权限、空结果或缺字段时，才使用免费来源补位。",
            f"成功来源：{', '.join(successful) if successful else '无'}。",
            f"失败或缺口来源：{', '.join(failed) if failed else '无'}。",
            f"缺失字段：{missing_text}。",
            "字段证据：",
            *field_lines,
        ]
    )


def _write_pack_files(context: Mapping[str, str], tool_dir_name: str, pack: Mapping[str, Any], raw_payloads: Mapping[str, Any]) -> None:
    root = Path(context["evidence_root"]) / tool_dir_name
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "normalized_pack.json", pack)
    raw_dir = root / "provider_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in raw_payloads.items():
        _write_json(raw_dir / f"{name}.json", payload)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _dataframe_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, pd.DataFrame) or value.empty:
        return []
    normalized = value.copy()
    normalized.columns = [str(item) for item in normalized.columns]
    records: list[dict[str, Any]] = []
    for index, row in normalized.iterrows():
        record = {"metric": str(index)}
        record.update({str(key): _json_scalar(item) for key, item in row.to_dict().items()})
        records.append(record)
    return records


def _latest_statement_column(rows: Any) -> dict[str, Any] | None:
    if not isinstance(rows, list) or not rows:
        return None
    period_keys = [key for key in rows[0].keys() if key != "metric"]
    if not period_keys:
        return None
    latest_period = sorted(period_keys, reverse=True)[0]
    output: dict[str, Any] = {"period": latest_period}
    for row in rows:
        metric = row.get("metric")
        if isinstance(metric, str):
            output[metric] = row.get(latest_period)
    return output


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{str(key): _json_scalar(value) for key, value in row.items()} for row in rows]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return _json_scalar(value)


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            value = value.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _latest_row(rows: list[Mapping[str, Any]], date_keys: tuple[str, ...]) -> Mapping[str, Any] | None:
    if not rows:
        return None
    candidates = []
    for row in rows:
        key = ""
        for date_key in date_keys:
            value = row.get(date_key)
            if value not in (None, ""):
                key = str(value)
                break
        candidates.append((key, row))
    return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]


def _first_row(rows: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return rows[0] if rows else None


def _first_value(mapping: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return None


def _raw_count(raw_payload: Any) -> int:
    if isinstance(raw_payload, Mapping):
        return len(raw_payload)
    if isinstance(raw_payload, list):
        return len(raw_payload)
    return 1 if raw_payload is not None else 0


def _date_like(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text


def _parse_date(value: str | None, *, fallback: date) -> date:
    if value is None:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc


def _date_from_context(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return datetime.now(UTC).date()


def _yyyymmdd(value: str) -> str:
    return value.replace("-", "")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _required_text(source: Mapping[str, Any], key: str, payload_name: str) -> str:
    value = _optional_text(source, key)
    if value is None:
        raise ValueError(f"{payload_name}.{key} is required")
    return value


def _optional_text(source: Mapping[str, Any], key: str) -> str | None:
    raw = source.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _classify_provider_error(error: Exception) -> str:
    text = str(error).lower()
    if any(item.lower() in text for item in _PERMISSION_HINTS):
        return "PROVIDER_PERMISSION_DENIED"
    if any(item.lower() in text for item in _RATE_HINTS):
        return "PROVIDER_RATE_LIMIT"
    if "token" in text or "config" in text:
        return "PROVIDER_KEY_MISSING"
    return "PROVIDER_ERROR"


def _redact(value: str) -> str:
    output = value
    for pattern in _SECRET_PATTERNS:
        output = pattern.sub(lambda match: match.group(0).split("=", 1)[0] + "=<redacted>" if "=" in match.group(0) else "<redacted>@", output)
    return output[:500]

__all__ = [
    "run_hk_fundamentals_data_pack",
]
