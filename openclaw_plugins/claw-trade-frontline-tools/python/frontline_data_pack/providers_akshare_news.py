from __future__ import annotations

from datetime import date, datetime, time as datetime_time
import inspect
import math
import types
from typing import Any, Callable, Mapping

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallable
from .runtime_context import ToolRuntimeContext


_STOCK_NEWS_REQUIRED_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("新闻标题", "标题", "title"),
    "source": ("文章来源", "来源", "source"),
    "publish_time": ("发布时间", "publish_time", "time"),
    "url": ("新闻链接", "链接", "url"),
}


def call_akshare_stock_news_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_stock_news_em_with_budget_timeout(
        func=akshare.stock_news_em,
        symbol=_ticker_code_6(query.ticker),
        call_context=call_context,
    )
    return _to_stock_news_payload(dataframe, spec=spec)


def call_akshare_stock_info_global_cls(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_stock_info_global_cls_with_budget_timeout(
        func=akshare.stock_info_global_cls,
        symbol="全部",
        call_context=call_context,
    )
    return _to_stock_info_global_cls_payload(dataframe, spec=spec, query=query)


def call_akshare_stock_info_global_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_requests_backed_function_with_budget_timeout(
        func=akshare.stock_info_global_em,
        call_context=call_context,
        error_label="AkShare stock_info_global_em",
    )
    return _to_stock_info_global_em_payload(dataframe, spec=spec, query=query)


def call_akshare_news_cctv(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_requests_backed_function_with_budget_timeout(
        func=akshare.news_cctv,
        call_context=call_context,
        error_label="AkShare news_cctv",
        date=query.end_date.replace("-", ""),
    )
    return _to_news_cctv_payload(dataframe, spec=spec, query=query)


AKSHARE_NEWS_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("akshare", "stock_news_em"): call_akshare_stock_news_em,
    ("akshare", "stock_info_global_cls"): call_akshare_stock_info_global_cls,
    ("akshare", "stock_info_global_em"): call_akshare_stock_info_global_em,
    ("akshare", "news_cctv"): call_akshare_news_cctv,
}


def _load_akshare_module() -> Any:
    import akshare  # type: ignore

    return akshare


def _to_stock_news_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "rows": [],
            "items": [],
        }

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    columns = {str(column) for column in list(dataframe.columns)}
    missing_fields = [
        field_name
        for field_name, aliases in _STOCK_NEWS_REQUIRED_COLUMN_ALIASES.items()
        if not any(alias in columns for alias in aliases)
    ]
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
    for row in raw_rows:
        rows.append(
            {
                "title": _pick_first(row, _STOCK_NEWS_REQUIRED_COLUMN_ALIASES["title"]),
                "source": _pick_first(row, _STOCK_NEWS_REQUIRED_COLUMN_ALIASES["source"]),
                "publish_time": _normalize_datetime_like(
                    _pick_first(row, _STOCK_NEWS_REQUIRED_COLUMN_ALIASES["publish_time"])
                ),
                "url": _pick_first(row, _STOCK_NEWS_REQUIRED_COLUMN_ALIASES["url"]),
                "content": _pick_first(row, ("新闻内容", "内容", "content")),
            }
        )

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "items": rows,
        "data": raw_rows,
    }


def _to_stock_info_global_cls_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "rows": [],
            "items": [],
        }

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        title = _pick_first(row, ("标题", "新闻标题", "title"))
        if not isinstance(title, str) or not title.strip():
            continue
        publish_date = _normalize_date_value(_pick_first(row, ("发布日期", "date")))
        publish_clock = _normalize_time_value(_pick_first(row, ("发布时间", "time")))
        publish_time = _compose_publish_time(publish_date, publish_clock)
        if publish_date is None:
            publish_date = _extract_date_from_text(publish_time)
        if publish_date is None or publish_date < query.start_date or publish_date > query.end_date:
            continue
        rows.append(
            {
                "title": title.strip(),
                "source": _pick_first(row, ("来源", "文章来源", "source")) or "财联社",
                "publish_time": publish_time,
                "url": _pick_first(row, ("链接", "url", "新闻链接")),
                "content": _pick_first(row, ("内容", "新闻内容", "content")),
            }
        )

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "items": rows,
        "data": raw_rows,
    }


def _to_stock_info_global_em_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "rows": [],
            "items": [],
        }

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        title = _pick_first(row, ("标题", "新闻标题", "title"))
        if not isinstance(title, str) or not title.strip():
            continue
        publish_time = _normalize_datetime_like(_pick_first(row, ("发布时间", "publish_time", "time")))
        publish_date = _extract_date_from_text(publish_time if isinstance(publish_time, str) else None)
        if publish_date is not None and (publish_date < query.start_date or publish_date > query.end_date):
            continue
        rows.append(
            {
                "title": title.strip(),
                "source": _pick_first(row, ("来源", "文章来源", "source")) or "东方财富快讯",
                "publish_time": publish_time,
                "url": _pick_first(row, ("链接", "url", "新闻链接")),
                "content": _pick_first(row, ("摘要", "内容", "新闻内容", "content", "summary")),
            }
        )

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "items": rows,
        "data": raw_rows,
    }


def _to_news_cctv_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "rows": [],
            "items": [],
        }

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        title = _pick_first(row, ("title", "标题", "新闻标题"))
        if not isinstance(title, str) or not title.strip():
            continue
        publish_time = _normalize_datetime_like(_pick_first(row, ("date", "日期", "publish_time", "发布时间")))
        publish_date = _extract_date_from_text(publish_time if isinstance(publish_time, str) else None)
        if publish_date is not None and (publish_date < query.start_date or publish_date > query.end_date):
            continue
        rows.append(
            {
                "title": title.strip(),
                "source": "新闻联播",
                "publish_time": publish_time,
                "url": _pick_first(row, ("url", "链接")),
                "content": _pick_first(row, ("content", "内容", "summary", "摘要")),
            }
        )

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "items": rows,
        "data": raw_rows,
    }


def _call_stock_news_em_with_budget_timeout(
    *,
    func: Callable[..., Any],
    symbol: str,
    call_context: ProviderCallContext,
) -> Any:
    return _call_requests_backed_function_with_budget_timeout(
        func=func,
        call_context=call_context,
        error_label="AkShare stock_news_em",
        symbol=symbol,
    )


def _call_requests_backed_function_with_budget_timeout(
    *,
    func: Callable[..., Any],
    call_context: ProviderCallContext,
    error_label: str,
    **kwargs: Any,
) -> Any:
    if not inspect.isfunction(func):
        call_context.raise_if_cancelled()
        return func(**kwargs)
    if "requests" not in func.__code__.co_names:
        call_context.raise_if_cancelled()
        return func(**kwargs)

    original_requests = func.__globals__.get("requests")
    if original_requests is None or not hasattr(original_requests, "get"):
        raise RuntimeError(f"{error_label} 无法注入 requests timeout")

    proxied = _clone_function(
        func,
        {
            "requests": _RequestsBudgetProxy(original_requests, call_context),
        },
    )
    call_context.raise_if_cancelled()
    return proxied(**kwargs)


def _call_stock_info_global_cls_with_budget_timeout(
    *,
    func: Callable[..., Any],
    symbol: str,
    call_context: ProviderCallContext,
) -> Any:
    if not inspect.isfunction(func):
        call_context.raise_if_cancelled()
        return func(symbol=symbol)

    request_helper = func.__globals__.get("make_request_with_retry_json")
    if not inspect.isfunction(request_helper):
        raise RuntimeError("AkShare stock_info_global_cls 无法注入 request helper timeout")

    proxied_helper = _clone_request_helper_with_budget_timeout(
        request_helper,
        call_context=call_context,
    )
    proxied = _clone_function(
        func,
        {
            "make_request_with_retry_json": proxied_helper,
        },
    )
    call_context.raise_if_cancelled()
    return proxied(symbol=symbol)


def _clone_request_helper_with_budget_timeout(
    func: Callable[..., Any],
    *,
    call_context: ProviderCallContext,
) -> Callable[..., Any]:
    original_requests = func.__globals__.get("requests")
    if original_requests is None or not hasattr(original_requests, "get"):
        raise RuntimeError("AkShare request helper 无法注入 requests timeout")

    original_time = func.__globals__.get("time")
    if original_time is None or not hasattr(original_time, "sleep"):
        raise RuntimeError("AkShare request helper 无法注入 sleep timeout")

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


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    json_rows: list[dict[str, Any]] = []
    for row in rows:
        json_row: dict[str, Any] = {}
        for key, value in row.items():
            json_row[str(key)] = _json_scalar(value)
        json_rows.append(json_row)
    return json_rows


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
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime_time):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _pick_first(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            return row[alias]
    return None


def _normalize_datetime_like(value: Any) -> Any:
    normalized = _json_scalar(value)
    if not isinstance(normalized, str):
        return normalized
    return normalized.replace(" ", "T")


def _normalize_date_value(value: Any) -> str | None:
    normalized = _json_scalar(value)
    if isinstance(normalized, str):
        text = normalized.strip()
        if len(text) >= 10:
            candidate = text[:10]
            if candidate[4:5] == "-" and candidate[7:8] == "-":
                return candidate
    return None


def _normalize_time_value(value: Any) -> str | None:
    normalized = _json_scalar(value)
    if isinstance(normalized, str):
        text = normalized.strip()
        if text:
            return text
    return None


def _compose_publish_time(publish_date: str | None, publish_clock: str | None) -> str | None:
    if publish_date and publish_clock:
        return f"{publish_date}T{publish_clock}"
    if publish_date:
        return f"{publish_date}T00:00:00"
    if publish_clock:
        return publish_clock
    return None


def _extract_date_from_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < 10:
        return None
    candidate = text[:10]
    if candidate[4:5] == "-" and candidate[7:8] == "-":
        return candidate
    return None


def _ticker_code_6(ticker: str) -> str:
    code, _, exchange = ticker.partition(".")
    if not code or not exchange:
        raise ValueError("ticker 必须是 NNNNNN.SH/SZ")
    if len(code) != 6:
        raise ValueError("ticker 代码长度必须为 6")
    return code


def _validate_dataframe(dataframe: Any) -> None:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise TypeError("AkShare 返回类型不是 DataFrame")


__all__ = [
    "AKSHARE_NEWS_CALL_REGISTRY",
    "call_akshare_news_cctv",
    "call_akshare_stock_info_global_em",
    "call_akshare_stock_info_global_cls",
    "call_akshare_stock_news_em",
]
