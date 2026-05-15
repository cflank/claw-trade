from __future__ import annotations

from datetime import date, datetime
import inspect
import math
import types
from typing import Any, Callable, Mapping

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallable
from .runtime_context import ToolRuntimeContext


_REQUIRED_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": ("日期", "date", "day"),
    "open": ("开盘", "open"),
    "close": ("收盘", "close"),
    "high": ("最高", "high"),
    "low": ("最低", "low"),
    "volume": ("成交量", "volume"),
    "amount": ("成交额", "amount"),
}
_OPTIONAL_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "pre_close": ("昨收", "昨收价", "昨收盘", "pre_close", "preclose", "previous_close"),
    "change": ("涨跌额", "涨跌", "change", "chg"),
    "pct_chg": ("涨跌幅", "涨跌幅%", "pct_chg", "pct_change", "change_pct"),
}


def call_akshare_stock_zh_a_hist(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    timeout_seconds = _remaining_timeout_seconds(call_context)
    akshare = _load_akshare_module()
    dataframe = akshare.stock_zh_a_hist(
        symbol=_ticker_code_6(query.ticker),
        period="daily",
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
        adjust=_resolve_adjust(query),
        timeout=timeout_seconds,
    )
    return _to_market_payload(dataframe, spec=spec, query=query)


def call_akshare_sina_daily(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_stock_zh_a_daily_with_budget_timeout(
        func=akshare.stock_zh_a_daily,
        symbol=_ticker_exchange_prefix(query.ticker),
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
        adjust=_resolve_adjust(query),
        call_context=call_context,
    )
    return _to_market_payload(dataframe, spec=spec, query=query)


def call_akshare_tencent_hist_tx(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    timeout_seconds = _remaining_timeout_seconds(call_context)
    akshare = _load_akshare_module()
    dataframe = akshare.stock_zh_a_hist_tx(
        symbol=_ticker_exchange_prefix(query.ticker),
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
        adjust=_resolve_adjust(query),
        timeout=timeout_seconds,
    )
    return _to_market_payload(dataframe, spec=spec, query=query)


def call_akshare_stock_hk_hist(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_stock_hk_hist_with_budget_timeout(
        func=akshare.stock_hk_hist,
        symbol=_ticker_hk_code(query.ticker),
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
        adjust=_resolve_adjust(query),
        call_context=call_context,
    )
    return _to_market_payload(dataframe, spec=spec, query=query)


AKSHARE_MARKET_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("akshare", "stock_zh_a_hist"): call_akshare_stock_zh_a_hist,
    ("akshare", "stock_hk_hist"): call_akshare_stock_hk_hist,
    ("sina", "stock_zh_a_daily"): call_akshare_sina_daily,
    ("tencent", "stock_zh_a_hist_tx"): call_akshare_tencent_hist_tx,
}


def _load_akshare_module() -> Any:
    import akshare  # type: ignore

    return akshare


def _to_market_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise TypeError("AkShare 返回类型不是 DataFrame")

    if bool(dataframe.empty):
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "adjust": _resolve_adjust(query),
            "rows": [],
        }

    rows = _to_json_rows(dataframe.to_dict(orient="records"))
    columns = {str(column) for column in list(dataframe.columns)}
    missing_fields = [
        field_name
        for field_name, aliases in _REQUIRED_COLUMN_ALIASES.items()
        if not any(alias in columns for alias in aliases)
    ]
    if missing_fields:
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "adjust": _resolve_adjust(query),
            "schema_invalid": {
                "reason": "missing_required_columns",
                "missing_fields": missing_fields,
            },
            "data": rows,
        }

    mapped_rows: list[dict[str, Any]] = []
    adjust = _resolve_adjust(query)
    for row in rows:
        mapped = {
            "trade_date": _pick_first(row, _REQUIRED_COLUMN_ALIASES["trade_date"]),
            "open": _pick_first(row, _REQUIRED_COLUMN_ALIASES["open"]),
            "close": _pick_first(row, _REQUIRED_COLUMN_ALIASES["close"]),
            "high": _pick_first(row, _REQUIRED_COLUMN_ALIASES["high"]),
            "low": _pick_first(row, _REQUIRED_COLUMN_ALIASES["low"]),
            "volume": _pick_first(row, _REQUIRED_COLUMN_ALIASES["volume"]),
            "amount": _pick_first(row, _REQUIRED_COLUMN_ALIASES["amount"]),
            "adjust": adjust,
        }
        for key, aliases in _OPTIONAL_COLUMN_ALIASES.items():
            value = _pick_first(row, aliases)
            if value is not None:
                mapped[key] = value
        mapped_rows.append(mapped)

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "adjust": adjust,
        "rows": mapped_rows,
        "data": rows,
    }


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    json_rows: list[dict[str, Any]] = []
    for row in rows:
        json_row: dict[str, Any] = {}
        for key, value in row.items():
            json_row[str(key)] = _json_scalar(value)
        json_rows.append(json_row)
    return json_rows


def _pick_first(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            value = row[alias]
            if alias in {"日期", "date", "day"}:
                return _normalize_date(value)
            return value
    return None


def _normalize_date(value: Any) -> Any:
    normalized = _json_scalar(value)
    if isinstance(normalized, str):
        if "T" in normalized:
            return normalized.split("T", 1)[0]
        if " " in normalized:
            return normalized.split(" ", 1)[0]
    return normalized


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


def _call_stock_zh_a_daily_with_budget_timeout(
    *,
    func: Callable[..., Any],
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
    call_context: ProviderCallContext,
) -> Any:
    if not inspect.isfunction(func):
        call_context.raise_if_cancelled()
        return func(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )

    original_requests = func.__globals__.get("requests")
    if original_requests is None or not hasattr(original_requests, "get"):
        raise RuntimeError("AkShare stock_zh_a_daily 无法注入 requests timeout")

    proxied_globals = dict(func.__globals__)
    proxied_globals["requests"] = _RequestsBudgetProxy(original_requests, call_context)
    proxied = types.FunctionType(
        func.__code__,
        proxied_globals,
        name=func.__name__,
        argdefs=func.__defaults__,
        closure=func.__closure__,
    )
    proxied.__kwdefaults__ = getattr(func, "__kwdefaults__", None)
    call_context.raise_if_cancelled()
    return proxied(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )


def _call_stock_hk_hist_with_budget_timeout(
    *,
    func: Callable[..., Any],
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
    call_context: ProviderCallContext,
) -> Any:
    call_context.raise_if_cancelled()
    timeout_seconds = _remaining_timeout_seconds(call_context)
    try:
        return func(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            timeout=timeout_seconds,
        )
    except TypeError:
        call_context.raise_if_cancelled()
        return func(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )


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


def _remaining_timeout_seconds(call_context: ProviderCallContext) -> float:
    remaining_ms = call_context.remaining_timeout_ms()
    if remaining_ms <= 0:
        raise TimeoutError("provider call cancelled by budget deadline")
    return max(remaining_ms / 1000.0, 0.001)


def _resolve_adjust(query: ProviderQuery) -> str:
    if query.adjust:
        return query.adjust
    return "qfq"


def _ticker_code_6(ticker: str) -> str:
    code, _ = _split_ticker(ticker)
    return code


def _ticker_exchange_prefix(ticker: str) -> str:
    code, exchange = _split_ticker(ticker)
    if exchange == "SH":
        return f"sh{code}"
    if exchange == "HK":
        return f"hk{code}"
    return f"sz{code}"


def _ticker_hk_code(ticker: str) -> str:
    code, exchange = _split_ticker(ticker)
    if exchange != "HK":
        raise ValueError("ticker 必须是 NNNNN.HK")
    return code


def _split_ticker(ticker: str) -> tuple[str, str]:
    code, _, exchange = ticker.partition(".")
    if not code or not exchange:
        raise ValueError("ticker 必须是 NNNNNN.SH/SZ")
    return code, exchange.upper()


def _yyyymmdd(iso_date: str) -> str:
    return iso_date.replace("-", "")


__all__ = [
    "AKSHARE_MARKET_CALL_REGISTRY",
    "call_akshare_sina_daily",
    "call_akshare_stock_hk_hist",
    "call_akshare_stock_zh_a_hist",
    "call_akshare_tencent_hist_tx",
]
