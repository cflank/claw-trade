from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, datetime
from importlib import import_module
import io
import math
from typing import Any, Callable, Mapping

from claw_trade.providers.tushare_client import TushareClientConfigError, create_tushare_pro

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallError, ProviderCallable
from .runtime_context import ToolRuntimeContext
from .security import summarize_provider_error


_REQUIRED_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": ("trade_date", "日期"),
    "open": ("open", "开盘"),
    "close": ("close", "收盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "volume": ("vol", "volume", "成交量"),
    "amount": ("amount", "成交额"),
}
_OPTIONAL_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "pre_close": ("pre_close", "昨收", "昨收价", "昨收盘"),
    "change": ("change", "chg", "涨跌额", "涨跌"),
    "pct_chg": ("pct_chg", "pct_change", "change_pct", "涨跌幅", "涨跌幅%"),
}


def call_tushare_pro_bar(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    try:
        pro = create_tushare_pro()
    except TushareClientConfigError as exc:
        raise ProviderCallError("PROVIDER_KEY_MISSING", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderCallError("PROVIDER_ERROR", str(exc)) from exc

    dataframe, stdout_pollution = _call_pro_bar_with_stdout_isolation(
        tushare_module=_load_tushare_module(),
        pro=pro,
        ts_code=query.ticker,
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
        adjust=_resolve_adjust(query),
    )
    payload = _to_market_payload(dataframe, spec=spec, query=query)
    stdout_diagnostic = _build_stdout_diagnostic(stdout_pollution)
    if stdout_diagnostic is not None:
        payload["diagnostics"] = {"stdout_pollution": stdout_diagnostic}
    return payload


def call_tushare_hk_daily_adj(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    try:
        pro = create_tushare_pro()
    except TushareClientConfigError as exc:
        raise ProviderCallError("PROVIDER_KEY_MISSING", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderCallError("PROVIDER_ERROR", str(exc)) from exc
    if not hasattr(pro, "hk_daily_adj"):
        raise ProviderCallError("PROVIDER_ERROR", "tushare pro 缺少 hk_daily_adj 接口")

    dataframe = pro.hk_daily_adj(
        ts_code=query.ticker,
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
    )
    return _to_market_payload(dataframe, spec=spec, query=query, adjusted_price_factor="adj_factor")


TUSHARE_MARKET_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("tushare", "pro_bar"): call_tushare_pro_bar,
    ("tushare", "hk_daily_adj"): call_tushare_hk_daily_adj,
}


def _call_pro_bar(
    *,
    tushare_module: Any,
    pro: Any,
    ts_code: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> Any:
    pro_bar = getattr(tushare_module, "pro_bar", None)
    if not callable(pro_bar):
        raise ProviderCallError("PROVIDER_ERROR", "tushare 模块缺少 pro_bar 接口")
    return pro_bar(
        api=pro,
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        asset="E",
        freq="D",
        adj=adjust,
    )


def _call_pro_bar_with_stdout_isolation(**kwargs: Any) -> tuple[Any, str]:
    stdout_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer):
        dataframe = _call_pro_bar(**kwargs)
    return dataframe, stdout_buffer.getvalue()


def _build_stdout_diagnostic(captured_stdout: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in captured_stdout.splitlines() if line.strip()]
    if not lines:
        return None
    summary = summarize_provider_error("\n".join(lines))
    return {
        "source": "tushare.pro_bar",
        "channel": "stdout",
        "line_count": len(lines),
        "summary": summary,
    }


def _load_tushare_module() -> Any:
    return import_module("tushare")


def _to_market_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    adjusted_price_factor: str | None = None,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": []}

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    columns = {str(column) for column in list(dataframe.columns)}
    missing_fields = [
        field_name
        for field_name, aliases in _REQUIRED_COLUMN_ALIASES.items()
        if not any(alias in columns for alias in aliases)
    ]
    if adjusted_price_factor is not None and adjusted_price_factor not in columns:
        missing_fields.append(adjusted_price_factor)
    if missing_fields:
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "schema_invalid": {
                "reason": "missing_required_columns",
                "missing_fields": missing_fields,
            },
            "data": raw_rows,
        }

    rows: list[dict[str, Any]] = []
    adjust = _resolve_adjust(query)
    for row in raw_rows:
        factor = _to_float_or_none(row.get(adjusted_price_factor)) if adjusted_price_factor is not None else None
        rows.append(
            {
                "trade_date": _normalize_date(_pick_first(row, _REQUIRED_COLUMN_ALIASES["trade_date"])),
                "open": _adjust_price(_pick_first(row, _REQUIRED_COLUMN_ALIASES["open"]), factor),
                "close": _adjust_price(_pick_first(row, _REQUIRED_COLUMN_ALIASES["close"]), factor),
                "high": _adjust_price(_pick_first(row, _REQUIRED_COLUMN_ALIASES["high"]), factor),
                "low": _adjust_price(_pick_first(row, _REQUIRED_COLUMN_ALIASES["low"]), factor),
                "volume": _pick_first(row, _REQUIRED_COLUMN_ALIASES["volume"]),
                "amount": _pick_first(row, _REQUIRED_COLUMN_ALIASES["amount"]),
                "adjust": adjust,
            }
        )
        for key, aliases in _OPTIONAL_COLUMN_ALIASES.items():
            value = _pick_first(row, aliases)
            if value is not None:
                if key in {"pre_close", "change"}:
                    value = _adjust_price(value, factor)
                rows[-1][key] = value

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "data": raw_rows,
    }


def _validate_dataframe(dataframe: Any) -> None:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise ProviderCallError("PROVIDER_SCHEMA_INVALID", "tushare pro_bar 返回类型不是 DataFrame")


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append({str(key): _json_scalar(value) for key, value in row.items()})
    return output


def _pick_first(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            return row[alias]
    return None


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


def _to_float_or_none(value: Any) -> float | None:
    scalar = _json_scalar(value)
    if scalar is None:
        return None
    try:
        return float(scalar)
    except (TypeError, ValueError):
        return None


def _adjust_price(value: Any, factor: float | None) -> Any:
    if factor is None:
        return value
    try:
        return float(value) * factor
    except (TypeError, ValueError):
        return value


def _normalize_date(value: Any) -> Any:
    normalized = _json_scalar(value)
    if isinstance(normalized, str):
        if "T" in normalized:
            return normalized.split("T", 1)[0]
        if " " in normalized:
            return normalized.split(" ", 1)[0]
    return normalized


def _resolve_adjust(query: ProviderQuery) -> str:
    return query.adjust or "qfq"


def _yyyymmdd(iso_date: str) -> str:
    return iso_date.replace("-", "")


__all__ = [
    "TUSHARE_MARKET_CALL_REGISTRY",
    "call_tushare_hk_daily_adj",
    "call_tushare_pro_bar",
]
