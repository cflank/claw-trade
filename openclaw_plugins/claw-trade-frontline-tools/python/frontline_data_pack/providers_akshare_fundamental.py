from __future__ import annotations

from datetime import date, datetime
import inspect
import math
import types
from typing import Any, Callable, Mapping

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallable
from .runtime_context import ToolRuntimeContext


_COMPANY_KEY_ALIASES: tuple[str, ...] = ("item", "项目", "指标", "字段", "key")
_COMPANY_VALUE_ALIASES: tuple[str, ...] = ("value", "值", "内容", "数值")

_COMPANY_NAME_ALIASES: tuple[str, ...] = ("股票简称", "公司名称", "名称", "证券简称")
_INDUSTRY_ALIASES: tuple[str, ...] = ("所属行业", "行业", "行业分类")
_MAIN_BUSINESS_ALIASES: tuple[str, ...] = ("主营业务", "主营业务及产品", "主营范围", "经营范围")
_LISTING_DATE_ALIASES: tuple[str, ...] = ("上市时间", "上市日期")
_LISTING_MARKET_ALIASES: tuple[str, ...] = ("上市地点", "上市市场", "上市交易所")

_REPORT_PERIOD_ALIASES: tuple[str, ...] = ("报告期", "报表期", "报告日期", "report_period")
_NET_PROFIT_ALIASES: tuple[str, ...] = ("净利润", "归母净利润", "net_profit")
_REVENUE_ALIASES: tuple[str, ...] = ("营业总收入", "营业收入", "revenue")
_EPS_ALIASES: tuple[str, ...] = ("每股收益", "基本每股收益", "eps")
_ROE_ALIASES: tuple[str, ...] = ("净资产收益率", "roe", "ROE")

_CODE_ALIASES: tuple[str, ...] = ("代码", "股票代码", "证券代码", "symbol", "code")
_LAST_PRICE_ALIASES: tuple[str, ...] = ("最新价", "现价", "close", "last_price")
_TOTAL_MV_ALIASES: tuple[str, ...] = ("总市值", "总市值(元)", "total_mv")
_PE_TTM_ALIASES: tuple[str, ...] = ("市盈率-动态", "市盈率", "pe_ttm", "pe")
_PB_ALIASES: tuple[str, ...] = ("市净率", "pb")
_TRADE_DATE_ALIASES: tuple[str, ...] = ("更新时间", "日期", "trade_date", "time")


def call_akshare_stock_individual_info_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_individual_info_em",
        func=akshare.stock_individual_info_em,
        kwargs={"symbol": _ticker_code_6(query.ticker)},
        call_context=call_context,
    )
    return _to_company_info_payload(dataframe, spec=spec)


def call_akshare_stock_financial_abstract_ths(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_financial_abstract_ths",
        func=akshare.stock_financial_abstract_ths,
        kwargs={
            "symbol": _ticker_code_6(query.ticker),
            "indicator": _yyyymmdd(query.end_date),
        },
        call_context=call_context,
    )
    return _to_financial_abstract_payload(dataframe, spec=spec)


def call_akshare_stock_zh_a_spot_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_zh_a_spot_em",
        func=akshare.stock_zh_a_spot_em,
        kwargs={},
        call_context=call_context,
    )
    return _to_valuation_snapshot_payload(dataframe, spec=spec, query=query)


AKSHARE_FUNDAMENTAL_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("akshare", "stock_individual_info_em"): call_akshare_stock_individual_info_em,
    ("akshare", "company_info"): call_akshare_stock_individual_info_em,
    ("akshare", "stock_financial_abstract_ths"): call_akshare_stock_financial_abstract_ths,
    ("akshare", "financial_abstract"): call_akshare_stock_financial_abstract_ths,
    ("akshare", "stock_zh_a_spot_em"): call_akshare_stock_zh_a_spot_em,
}


def _load_akshare_module() -> Any:
    import akshare  # type: ignore

    return akshare


def _to_company_info_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": [], "data": []}

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    mapped = _build_company_field_map(raw_rows)
    if mapped is None:
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["company_info_key_value_columns"],
        )

    rows: list[dict[str, Any]] = []
    _append_field_row(rows, "company_profile.company_name", _first_mapped(mapped, _COMPANY_NAME_ALIASES), "text")
    _append_field_row(rows, "company_profile.industry", _first_mapped(mapped, _INDUSTRY_ALIASES), "text")
    _append_field_row(rows, "company_profile.main_business", _first_mapped(mapped, _MAIN_BUSINESS_ALIASES), "text")
    _append_field_row(
        rows,
        "company_profile.listing_date",
        _normalize_date_like(_first_mapped(mapped, _LISTING_DATE_ALIASES)),
        "date",
    )
    _append_field_row(rows, "company_profile.listing_market", _first_mapped(mapped, _LISTING_MARKET_ALIASES), "text")

    if not rows:
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["company_profile_subject"],
        )
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "data": raw_rows,
    }


def _to_financial_abstract_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": [], "data": []}

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    columns = _collect_columns(raw_rows)
    if not columns:
        return _schema_invalid_payload(spec=spec, raw_rows=raw_rows, missing_fields=["financial_summary_columns"])

    required_alias_groups = (
        _REPORT_PERIOD_ALIASES,
        _NET_PROFIT_ALIASES,
        _REVENUE_ALIASES,
        _EPS_ALIASES,
        _ROE_ALIASES,
    )
    if not any(_has_any_column(columns, aliases) for aliases in required_alias_groups):
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["report_period/net_profit/revenue/eps/roe"],
        )

    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        row = {
            "report_period": _normalize_date_like(_pick_first(raw_row, _REPORT_PERIOD_ALIASES)),
            "income_statement.net_profit": _pick_first(raw_row, _NET_PROFIT_ALIASES),
            "income_statement.revenue": _pick_first(raw_row, _REVENUE_ALIASES),
            "income_statement.eps": _pick_first(raw_row, _EPS_ALIASES),
            "financial_indicators.roe": _pick_first(raw_row, _ROE_ALIASES),
        }
        mapped_count = sum(1 for value in row.values() if value not in (None, ""))
        if mapped_count >= 2:
            rows.append(row)

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "data": raw_rows,
    }


def _to_valuation_snapshot_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": [], "data": []}

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    columns = _collect_columns(raw_rows)
    if not _has_any_column(columns, _CODE_ALIASES):
        return _schema_invalid_payload(spec=spec, raw_rows=raw_rows, missing_fields=["code"])

    target_code = _ticker_code_6(query.ticker)
    target_rows = [row for row in raw_rows if _extract_code_6(_pick_first(row, _CODE_ALIASES)) == target_code]
    if not target_rows:
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": [], "data": raw_rows}

    rows: list[dict[str, Any]] = []
    for target_row in target_rows:
        mapped = {
            "ticker_code": target_code,
            "price_context.close": _pick_first(target_row, _LAST_PRICE_ALIASES),
            "valuation.total_mv": _pick_first(target_row, _TOTAL_MV_ALIASES),
            "valuation.pe_ttm": _pick_first(target_row, _PE_TTM_ALIASES),
            "valuation.pb": _pick_first(target_row, _PB_ALIASES),
            "price_context.trade_date": _normalize_date_like(_pick_first(target_row, _TRADE_DATE_ALIASES)),
        }
        mapped_metric_count = sum(
            1
            for key in ("price_context.close", "valuation.total_mv", "valuation.pe_ttm", "valuation.pb")
            if mapped[key] not in (None, "")
        )
        if mapped_metric_count >= 2:
            rows.append(mapped)

    if not rows:
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=target_rows,
            missing_fields=["latest_price/total_mv/pe_ttm/pb"],
        )
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "data": raw_rows,
    }


def _call_akshare_endpoint_with_budget_timeout(
    *,
    endpoint: str,
    func: Callable[..., Any],
    kwargs: dict[str, Any],
    call_context: ProviderCallContext,
) -> Any:
    timeout_seconds = _remaining_timeout_seconds(call_context)
    call_context.raise_if_cancelled()

    signature = _safe_signature(func)
    if signature is not None and "timeout" in signature.parameters:
        bound_kwargs = dict(kwargs)
        existing_timeout = bound_kwargs.get("timeout")
        if isinstance(existing_timeout, (int, float)) and existing_timeout > 0:
            bound_kwargs["timeout"] = min(float(existing_timeout), timeout_seconds)
        else:
            bound_kwargs["timeout"] = timeout_seconds
        return func(**bound_kwargs)

    if not inspect.isfunction(func):
        raise RuntimeError(f"AkShare {endpoint} 无法注入 requests timeout")

    original_requests = func.__globals__.get("requests")
    if original_requests is not None and hasattr(original_requests, "get"):
        proxied = _clone_function(
            func,
            {"requests": _RequestsBudgetProxy(original_requests, call_context)},
        )
        return proxied(**kwargs)

    request_helper = func.__globals__.get("make_request_with_retry_json")
    if inspect.isfunction(request_helper):
        proxied_helper = _clone_request_helper_with_budget_timeout(
            endpoint=endpoint,
            func=request_helper,
            call_context=call_context,
        )
        proxied = _clone_function(
            func,
            {"make_request_with_retry_json": proxied_helper},
        )
        return proxied(**kwargs)

    raise RuntimeError(f"AkShare {endpoint} 无法注入 requests timeout")


def _safe_signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def _clone_request_helper_with_budget_timeout(
    *,
    endpoint: str,
    func: Callable[..., Any],
    call_context: ProviderCallContext,
) -> Callable[..., Any]:
    original_requests = func.__globals__.get("requests")
    if original_requests is None or not hasattr(original_requests, "get"):
        raise RuntimeError(f"AkShare {endpoint} 无法注入 helper requests timeout")

    original_time = func.__globals__.get("time")
    if original_time is None or not hasattr(original_time, "sleep"):
        raise RuntimeError(f"AkShare {endpoint} 无法注入 helper sleep timeout")

    return _clone_function(
        func,
        {
            "requests": _RequestsBudgetProxy(original_requests, call_context),
            "time": _TimeBudgetProxy(original_time, call_context),
        },
    )


def _clone_function(func: Callable[..., Any], replacements: Mapping[str, Any]) -> Callable[..., Any]:
    proxied_globals = dict(func.__globals__)
    proxied_globals.update(replacements)
    proxied = types.FunctionType(
        func.__code__,
        proxied_globals,
        name=func.__name__,
        argdefs=func.__defaults__,
        closure=func.__closure__,
    )
    proxied.__kwdefaults__ = getattr(func, "__kwdefaults__", None)
    return proxied


class _RequestsBudgetProxy:
    def __init__(self, requests_module: Any, call_context: ProviderCallContext) -> None:
        self._requests = requests_module
        self._call_context = call_context

    def get(self, *args: Any, **kwargs: Any) -> Any:
        self._call_context.raise_if_cancelled()
        timeout_seconds = _remaining_timeout_seconds(self._call_context)
        existing_timeout = kwargs.get("timeout")
        if isinstance(existing_timeout, (int, float)) and existing_timeout > 0:
            kwargs["timeout"] = min(float(existing_timeout), timeout_seconds)
        else:
            kwargs["timeout"] = timeout_seconds
        return self._requests.get(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._requests, name)


class _TimeBudgetProxy:
    def __init__(self, time_module: Any, call_context: ProviderCallContext) -> None:
        self._time = time_module
        self._call_context = call_context

    def sleep(self, seconds: float) -> None:
        self._call_context.raise_if_cancelled()
        requested = float(seconds) if isinstance(seconds, (int, float)) else 0.0
        if requested <= 0:
            self._time.sleep(0)
            return
        remaining = _remaining_timeout_seconds(self._call_context)
        self._time.sleep(min(requested, remaining))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._time, name)


def _remaining_timeout_seconds(call_context: ProviderCallContext) -> float:
    remaining_ms = call_context.remaining_timeout_ms()
    if remaining_ms <= 0:
        raise TimeoutError("provider call cancelled by budget deadline")
    return max(remaining_ms / 1000.0, 0.001)


def _validate_dataframe(dataframe: Any) -> None:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise TypeError("AkShare 返回类型不是 DataFrame")


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    json_rows: list[dict[str, Any]] = []
    for row in rows:
        json_row: dict[str, Any] = {}
        for key, value in row.items():
            json_row[str(key)] = _json_scalar(value)
        json_rows.append(json_row)
    return json_rows


def _collect_columns(rows: list[Mapping[str, Any]]) -> set[str]:
    columns: set[str] = set()
    for row in rows:
        columns.update(str(key) for key in row.keys())
    return columns


def _build_company_field_map(rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return {}
    first_row = rows[0]
    key_column = _first_available_alias(first_row, _COMPANY_KEY_ALIASES)
    value_column = _first_available_alias(first_row, _COMPANY_VALUE_ALIASES)
    if key_column is None or value_column is None:
        return None

    mapped: dict[str, Any] = {}
    for row in rows:
        field_name = row.get(key_column)
        if not isinstance(field_name, str):
            continue
        normalized_name = field_name.strip()
        if not normalized_name:
            continue
        mapped[normalized_name] = row.get(value_column)
    return mapped


def _first_available_alias(row: Mapping[str, Any], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in row:
            return alias
    return None


def _first_mapped(mapped: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in mapped:
            return mapped[alias]
    return None


def _append_field_row(rows: list[dict[str, Any]], field_name: str, value: Any, unit: str) -> None:
    if value in (None, ""):
        return
    rows.append({"field_name": field_name, "value": value, "unit": unit})


def _pick_first(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            return row[alias]
    return None


def _extract_code_6(value: Any) -> str | None:
    normalized = _json_scalar(value)
    if isinstance(normalized, int):
        return f"{normalized:06d}"
    if not isinstance(normalized, str):
        return None
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return None


def _has_any_column(columns: set[str], aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        if alias in columns:
            return True
    return False


def _schema_invalid_payload(
    *,
    spec: ProviderSpec,
    raw_rows: list[Mapping[str, Any]],
    missing_fields: list[str],
) -> dict[str, Any]:
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "schema_invalid": {
            "reason": "missing_required_columns",
            "missing_fields": missing_fields,
        },
        "data": [dict(row) for row in raw_rows],
    }


def _ticker_code_6(ticker: str) -> str:
    code, _, exchange = ticker.partition(".")
    if not code or not exchange:
        raise ValueError("ticker 必须是 NNNNNN.SH/SZ")
    exchange_upper = exchange.upper()
    if exchange_upper not in {"SH", "SZ"}:
        raise ValueError("ticker 交易所必须为 SH/SZ")
    return code


def _yyyymmdd(iso_date: str) -> str:
    if len(iso_date) != 10 or iso_date[4] != "-" or iso_date[7] != "-":
        raise ValueError("date 必须是 YYYY-MM-DD")
    return f"{iso_date[0:4]}{iso_date[5:7]}{iso_date[8:10]}"


def _normalize_date_like(value: Any) -> Any:
    normalized = _json_scalar(value)
    if not isinstance(normalized, str):
        return normalized
    text = normalized.strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            value = value.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


__all__ = [
    "AKSHARE_FUNDAMENTAL_CALL_REGISTRY",
    "call_akshare_stock_financial_abstract_ths",
    "call_akshare_stock_individual_info_em",
    "call_akshare_stock_zh_a_spot_em",
]
