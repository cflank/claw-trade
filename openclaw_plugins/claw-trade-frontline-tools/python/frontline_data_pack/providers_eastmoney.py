from __future__ import annotations

from typing import Any, Callable, Mapping

from .errors import PROVIDER_HTTP_CLIENT_ERROR, PROVIDER_HTTP_SERVER_ERROR
from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallError, ProviderCallable
from .runtime_context import ToolRuntimeContext
from .security import summarize_provider_error


_EASTMONEY_PUSH2HIS_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_EASTMONEY_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"


def call_eastmoney_push2his_kline(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    timeout_seconds = _remaining_timeout_seconds(call_context)
    params = _build_params(query)
    response = _http_get(
        _EASTMONEY_PUSH2HIS_URL,
        params=params,
        timeout=timeout_seconds,
    )
    status_code = _status_code(response)
    if 400 <= status_code <= 499:
        raise ProviderCallError(PROVIDER_HTTP_CLIENT_ERROR, _status_error_message(status_code))
    if status_code >= 500:
        raise ProviderCallError(PROVIDER_HTTP_SERVER_ERROR, _status_error_message(status_code))
    payload = _json(response)
    rows, schema_invalid = _extract_rows(payload, query=query)
    if schema_invalid is not None:
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "schema_invalid": schema_invalid,
            "data": payload.get("data") if isinstance(payload, Mapping) else None,
        }
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "adjust": _resolve_adjust(query),
        "rows": rows,
        "klines": _extract_klines(payload),
        "data": payload.get("data") if isinstance(payload, Mapping) else None,
    }


EASTMONEY_MARKET_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("eastmoney_direct", "push2his_kline"): call_eastmoney_push2his_kline,
}


def _http_get(url: str, *, params: Mapping[str, str], timeout: float) -> Any:
    import requests

    return requests.get(url, params=params, timeout=timeout)


def _json(response: Any) -> Any:
    return response.json()


def _status_code(response: Any) -> int:
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    raise ValueError("EastMoney 响应缺少 status_code")


def _build_params(query: ProviderQuery) -> dict[str, str]:
    return {
        "secid": _to_secid(query.ticker),
        "fields1": _EASTMONEY_FIELDS1,
        "fields2": _EASTMONEY_FIELDS2,
        "klt": "101",
        "fqt": "1",
        "beg": _yyyymmdd(query.start_date),
        "end": _yyyymmdd(query.end_date),
    }


def _to_secid(ticker: str) -> str:
    code, exchange = _split_ticker(ticker)
    if exchange == "SH":
        return f"1.{code}"
    return f"0.{code}"


def _split_ticker(ticker: str) -> tuple[str, str]:
    code, _, exchange = ticker.partition(".")
    if not code or not exchange:
        raise ValueError("ticker 必须是 NNNNNN.SH/SZ")
    exchange_upper = exchange.upper()
    if exchange_upper not in {"SH", "SZ"}:
        raise ValueError("ticker 交易所必须为 SH/SZ")
    return code, exchange_upper


def _yyyymmdd(iso_date: str) -> str:
    return iso_date.replace("-", "")


def _extract_rows(payload: Any, *, query: ProviderQuery) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    klines = _extract_klines(payload)
    if klines is None:
        return [], {"reason": "missing_data_klines"}
    rows: list[dict[str, Any]] = []
    for entry in klines:
        if not isinstance(entry, str):
            continue
        row = _parse_kline(entry, adjust=_resolve_adjust(query))
        if row is None:
            continue
        rows.append(row)
    if klines and not rows:
        return [], {"reason": "unparseable_data_klines"}
    return rows, None


def _extract_klines(payload: Any) -> list[Any] | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    klines = data.get("klines")
    if isinstance(klines, list):
        return list(klines)
    return None


def _parse_kline(line: str, *, adjust: str) -> dict[str, Any] | None:
    parts = line.split(",")
    if len(parts) < 7:
        return None
    try:
        row = {
            "trade_date": parts[0],
            "open": _to_float(parts[1]),
            "close": _to_float(parts[2]),
            "high": _to_float(parts[3]),
            "low": _to_float(parts[4]),
            "volume": _to_float(parts[5]),
            "amount": _to_float(parts[6]),
            "adjust": adjust,
        }
        if len(parts) >= 10:
            row["pct_chg"] = _to_float(parts[8])
            row["change"] = _to_float(parts[9])
            if row["change"] is not None and row["close"] is not None:
                row["pre_close"] = row["close"] - row["change"]
        return row
    except ValueError:
        return None


def _to_float(value: str) -> float:
    return float(value)


def _resolve_adjust(query: ProviderQuery) -> str:
    if query.adjust:
        return query.adjust
    return "qfq"


def _remaining_timeout_seconds(call_context: ProviderCallContext) -> float:
    remaining_ms = call_context.remaining_timeout_ms()
    if remaining_ms <= 0:
        raise TimeoutError("provider call cancelled by budget deadline")
    return max(remaining_ms / 1000.0, 0.001)


def _status_error_message(status_code: int) -> str:
    return summarize_provider_error(f"eastmoney push2his http {status_code}")


__all__ = [
    "EASTMONEY_MARKET_CALL_REGISTRY",
    "call_eastmoney_push2his_kline",
]
