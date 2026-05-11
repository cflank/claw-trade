from __future__ import annotations

from datetime import date, datetime
import inspect
import math
import os
import re
import types
from typing import Any, Callable, Mapping

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallable, ProviderCallError
from .runtime_context import ToolRuntimeContext


_RANK_ALIASES: tuple[str, ...] = ("排名", "当前排名", "名次", "rank")
_HEAT_ALIASES: tuple[str, ...] = ("热度", "热度值", "heat", "heat_value")
_KEYWORD_ALIASES: tuple[str, ...] = ("关键词", "概念名称", "概念", "热词", "keyword", "concept", "concept_name")
_CODE_ALIASES: tuple[str, ...] = ("代码", "股票代码", "证券代码", "code", "symbol")
_NAME_ALIASES: tuple[str, ...] = ("股票名称", "名称", "股票简称", "name")
_RELATED_CODE_ALIASES: tuple[str, ...] = (
    "相关股票代码",
    "相关代码",
    "关联股票代码",
    "related_code",
    "related_symbol",
    "相关标的代码",
)
_RELATED_NAME_ALIASES: tuple[str, ...] = (
    "相关股票名称",
    "相关名称",
    "关联股票名称",
    "related_name",
    "related_stock_name",
    "相关标的名称",
)
_RANK_CHANGE_ALIASES: tuple[str, ...] = ("排名变化", "排名较昨日变动", "排名变动", "rank_change", "rankChange")
_OBSERVED_AT_ALIASES: tuple[str, ...] = ("更新时间", "时间", "日期", "update_time", "observed_at")
_ITEM_KEY_ALIASES: tuple[str, ...] = ("item", "项目", "字段", "key", "name")
_ITEM_VALUE_ALIASES: tuple[str, ...] = ("value", "值", "内容")
_CODE_RE = re.compile(r"(\d{6})")


def call_akshare_stock_hot_rank_latest_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_hot_rank_latest_em",
        func=akshare.stock_hot_rank_latest_em,
        kwargs={"symbol": _to_eastmoney_symbol(query.ticker)},
        call_context=call_context,
    )
    return _to_attention_payload(dataframe, spec=spec, query=query)


def call_akshare_stock_hot_keyword_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_hot_keyword_em",
        func=akshare.stock_hot_keyword_em,
        kwargs={"symbol": _to_eastmoney_symbol(query.ticker)},
        call_context=call_context,
    )
    return _to_keyword_payload(dataframe, spec=spec, query=query)


def call_akshare_stock_hot_rank_relate_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_hot_rank_relate_em",
        func=akshare.stock_hot_rank_relate_em,
        kwargs={"symbol": _to_eastmoney_symbol(query.ticker)},
        call_context=call_context,
    )
    return _to_related_symbol_payload(dataframe, spec=spec, query=query)


def call_akshare_stock_hot_rank_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_hot_rank_em",
        func=akshare.stock_hot_rank_em,
        kwargs={},
        call_context=call_context,
    )
    return _to_full_board_payload(dataframe, spec=spec, query=query, source_board="hot_rank")


def call_akshare_stock_hot_up_em(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    call_context.raise_if_cancelled()
    akshare = _load_akshare_module()
    dataframe = _call_akshare_endpoint_with_budget_timeout(
        endpoint="stock_hot_up_em",
        func=akshare.stock_hot_up_em,
        kwargs={},
        call_context=call_context,
    )
    return _to_full_board_payload(dataframe, spec=spec, query=query, source_board="hot_up")


def call_eastmoney_direct_full_hot_rank_board(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = spec, query, runtime_context
    call_context.raise_if_cancelled()
    raise ProviderCallError(
        "PROVIDER_CONTRACT_MISSING",
        "eastmoney_direct/full_hot_rank_board 合同未闭合，缺少固定 endpoint/params/response/反爬边界",
    )


def call_bocha_public_page_search(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = spec, query, runtime_context
    call_context.raise_if_cancelled()
    api_key = os.getenv("CN_A_NEWS_BOCHA_API_KEY", "").strip()
    if not api_key:
        raise ProviderCallError(
            "PROVIDER_AUTH_MISSING",
            "bocha/public_page_search 缺少 CN_A_NEWS_BOCHA_API_KEY",
        )
    raise ProviderCallError(
        "PROVIDER_CONTRACT_MISSING",
        "bocha/public_page_search 合同未闭合，缺少 social 结构化响应映射",
    )


def call_tavily_public_page_search(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = spec, query, runtime_context
    call_context.raise_if_cancelled()
    api_key = os.getenv("CN_A_NEWS_TAVILY_API_KEY", "").strip()
    if not api_key:
        raise ProviderCallError(
            "PROVIDER_AUTH_MISSING",
            "tavily/public_page_search 缺少 CN_A_NEWS_TAVILY_API_KEY",
        )
    raise ProviderCallError(
        "PROVIDER_CONTRACT_MISSING",
        "tavily/public_page_search 合同未闭合，缺少 social 结构化响应映射",
    )


def call_jina_public_page_search(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = spec, query, runtime_context
    call_context.raise_if_cancelled()
    api_key = os.getenv("JINA_API_KEY", "").strip()
    if not api_key:
        raise ProviderCallError(
            "PROVIDER_AUTH_MISSING",
            "jina/public_page_search 缺少 JINA_API_KEY",
        )
    raise ProviderCallError(
        "PROVIDER_CONTRACT_MISSING",
        "jina/public_page_search 合同未闭合，缺少 social 结构化响应映射",
    )


def call_alphaear_market_background(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = spec, query, runtime_context
    call_context.raise_if_cancelled()
    raise ProviderCallError(
        "PROVIDER_CONTRACT_MISSING",
        "alphaear_news_source_list/market_background 合同未闭合，未定义 social 证据映射",
    )


def call_xueqiu_guba_post_level_sentiment(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = spec, query, runtime_context
    call_context.raise_if_cancelled()
    raise ProviderCallError(
        "PROVIDER_AUTH_MISSING",
        "xueqiu_guba/post_level_sentiment 缺少登录态或权限授权，且反爬合同未闭合",
    )


AKSHARE_SOCIAL_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("eastmoney_akshare", "hot_rank_latest"): call_akshare_stock_hot_rank_latest_em,
    ("eastmoney_akshare", "stock_hot_rank_latest_em"): call_akshare_stock_hot_rank_latest_em,
    ("eastmoney_akshare", "hot_keyword"): call_akshare_stock_hot_keyword_em,
    ("eastmoney_akshare", "stock_hot_keyword_em"): call_akshare_stock_hot_keyword_em,
    ("eastmoney_akshare", "related_hot_rank"): call_akshare_stock_hot_rank_relate_em,
    ("eastmoney_akshare", "stock_hot_rank_relate_em"): call_akshare_stock_hot_rank_relate_em,
    ("eastmoney_akshare", "stock_hot_rank_em"): call_akshare_stock_hot_rank_em,
    ("eastmoney_akshare", "stock_hot_up_em"): call_akshare_stock_hot_up_em,
    ("eastmoney_akshare", "full_hot_rank_board"): call_akshare_stock_hot_rank_em,
    ("eastmoney_direct", "full_hot_rank_board"): call_eastmoney_direct_full_hot_rank_board,
    ("bocha", "public_page_search"): call_bocha_public_page_search,
    ("tavily", "public_page_search"): call_tavily_public_page_search,
    ("jina", "public_page_search"): call_jina_public_page_search,
    ("alphaear_news_source_list", "market_background"): call_alphaear_market_background,
    ("xueqiu_guba", "post_level_sentiment"): call_xueqiu_guba_post_level_sentiment,
}


def _load_akshare_module() -> Any:
    import akshare  # type: ignore

    return akshare


def _to_attention_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": []}
    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    raw_rows = _normalize_item_value_rows(raw_rows)
    columns = {str(column) for column in list(dataframe.columns)}
    if (
        not _has_any_column(columns, _RANK_ALIASES)
        and not _has_any_column(columns, _HEAT_ALIASES)
        and not _rows_have_any_key(raw_rows, _RANK_ALIASES)
        and not _rows_have_any_key(raw_rows, _HEAT_ALIASES)
    ):
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["rank_or_heat_value"],
        )
    rows = [
        _build_signal_row(
            raw_row=row,
            query=query,
            signal_type="attention",
            provider=spec.provider,
            endpoint=spec.endpoint,
            matched_target_default=True,
        )
        for row in raw_rows
    ]
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "source_platform": "eastmoney",
        "rows": rows,
        "data": raw_rows,
    }


def _to_keyword_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": []}
    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    raw_rows = _normalize_item_value_rows(raw_rows)
    columns = {str(column) for column in list(dataframe.columns)}
    if not _has_any_column(columns, _KEYWORD_ALIASES) and not _rows_have_any_key(raw_rows, _KEYWORD_ALIASES):
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["keyword"],
        )
    rows = [
        _build_signal_row(
            raw_row=row,
            query=query,
            signal_type="topic_keyword",
            provider=spec.provider,
            endpoint=spec.endpoint,
            matched_target_default=True,
        )
        for row in raw_rows
    ]
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "source_platform": "eastmoney",
        "rows": rows,
        "data": raw_rows,
    }


def _to_related_symbol_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": []}
    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    raw_rows = _normalize_item_value_rows(raw_rows)
    columns = {str(column) for column in list(dataframe.columns)}
    has_related_code = _has_any_column(columns, _RELATED_CODE_ALIASES)
    has_related_name = _has_any_column(columns, _RELATED_NAME_ALIASES)
    if not has_related_code and not has_related_name:
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["related_ticker_or_related_name"],
        )
    rows = [
        _build_signal_row(
            raw_row=row,
            query=query,
            signal_type="related_symbol",
            provider=spec.provider,
            endpoint=spec.endpoint,
            matched_target_default=True,
        )
        for row in raw_rows
    ]
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "source_platform": "eastmoney",
        "rows": rows,
        "data": raw_rows,
    }


def _to_full_board_payload(
    dataframe: Any,
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    source_board: str,
) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": []}
    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    raw_rows = _normalize_item_value_rows(raw_rows)
    columns = {str(column) for column in list(dataframe.columns)}
    if not _has_any_column(columns, _RANK_ALIASES):
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["rank"],
        )
    if not _has_any_column(columns, _CODE_ALIASES) and not _has_any_column(columns, _NAME_ALIASES):
        return _schema_invalid_payload(
            spec=spec,
            raw_rows=raw_rows,
            missing_fields=["code_or_name"],
        )

    rows = [
        _build_signal_row(
            raw_row=row,
            query=query,
            signal_type="attention",
            provider=spec.provider,
            endpoint=spec.endpoint,
            matched_target_default=False,
        )
        for row in raw_rows
    ]
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "source_platform": "eastmoney",
        "source_board": source_board,
        "rows": rows,
        "data": raw_rows,
    }


def _build_signal_row(
    *,
    raw_row: Mapping[str, Any],
    query: ProviderQuery,
    signal_type: str,
    provider: str,
    endpoint: str,
    matched_target_default: bool,
) -> dict[str, Any]:
    matched_target = _match_target(raw_row, query, default_when_unknown=matched_target_default)
    row: dict[str, Any] = {
        "signal_type": signal_type,
        "target_ticker": query.ticker,
        "matched_target": matched_target,
        "accepted_target": matched_target,
        "rank": _to_int_or_none(_pick_first(raw_row, _RANK_ALIASES)),
        "heat_value": _to_float_or_none(_pick_first(raw_row, _HEAT_ALIASES)),
        "keyword": _to_optional_str(_pick_first(raw_row, _KEYWORD_ALIASES)),
        "related_ticker": _to_related_ticker(_pick_first(raw_row, _RELATED_CODE_ALIASES)),
        "related_name": _to_optional_str(_pick_first(raw_row, _RELATED_NAME_ALIASES)),
        "observed_at": _normalize_datetime_like(_pick_first(raw_row, _OBSERVED_AT_ALIASES)),
        "provider": provider,
        "endpoint": endpoint,
        "source_platform": "eastmoney",
        "rank_change": _to_int_or_none(_pick_first(raw_row, _RANK_CHANGE_ALIASES)),
    }
    if row["related_ticker"] is None:
        row["related_ticker"] = _to_related_ticker(_pick_first(raw_row, _CODE_ALIASES))
    if row["related_name"] is None:
        row["related_name"] = _to_optional_str(_pick_first(raw_row, _NAME_ALIASES))
    return row


def _schema_invalid_payload(
    *,
    spec: ProviderSpec,
    raw_rows: list[dict[str, Any]],
    missing_fields: list[str],
) -> dict[str, Any]:
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "schema_invalid": {
            "reason": "missing_required_columns",
            "missing_fields": missing_fields,
        },
        "data": raw_rows,
    }


def _validate_dataframe(dataframe: Any) -> None:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise TypeError("AkShare 返回类型不是 DataFrame")


def _has_any_column(columns: set[str], aliases: tuple[str, ...]) -> bool:
    lowered_columns = {column.lower() for column in columns}
    for alias in aliases:
        if alias in columns:
            return True
        if alias.lower() in lowered_columns:
            return True
    return False


def _rows_have_any_key(rows: list[dict[str, Any]], aliases: tuple[str, ...]) -> bool:
    lowered_aliases = {alias.lower() for alias in aliases}
    for row in rows:
        keys = set(row.keys())
        lowered_keys = {key.lower() for key in keys}
        if any(alias in keys for alias in aliases):
            return True
        if lowered_aliases & lowered_keys:
            return True
    return False


def _normalize_item_value_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not raw_rows:
        return raw_rows
    if not all(_looks_like_item_value_row(row) for row in raw_rows):
        return raw_rows
    normalized: dict[str, Any] = {}
    for row in raw_rows:
        key = _to_optional_str(_pick_first(row, _ITEM_KEY_ALIASES))
        if key is None:
            continue
        normalized[key] = _pick_first(row, _ITEM_VALUE_ALIASES)
    return [normalized] if normalized else raw_rows


def _looks_like_item_value_row(row: Mapping[str, Any]) -> bool:
    return _pick_first(row, _ITEM_KEY_ALIASES) is not None and _pick_first(row, _ITEM_VALUE_ALIASES) is not None


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    json_rows: list[dict[str, Any]] = []
    for row in rows:
        json_row: dict[str, Any] = {}
        for key, value in row.items():
            json_row[str(key)] = _json_scalar(value)
        json_rows.append(json_row)
    return json_rows


def _pick_first(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered_row = {str(key).lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias in row:
            return row[alias]
        lowered_alias = alias.lower()
        if lowered_alias in lowered_row:
            return lowered_row[lowered_alias]
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


def _to_optional_str(value: Any) -> str | None:
    normalized = _json_scalar(value)
    if normalized is None:
        return None
    text = str(normalized).strip()
    if not text:
        return None
    return text


def _normalize_datetime_like(value: Any) -> Any:
    normalized = _json_scalar(value)
    if isinstance(normalized, str):
        text = normalized.strip()
        if " " in text:
            date_part, time_part = text.split(" ", 1)
            if date_part and time_part:
                return f"{date_part}T{time_part}"
        return text
    return normalized


def _to_int_or_none(value: Any) -> int | None:
    normalized = _json_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool):
        return int(normalized)
    if isinstance(normalized, int):
        return normalized
    if isinstance(normalized, float):
        if math.isnan(normalized):
            return None
        return int(normalized)
    if isinstance(normalized, str):
        text = normalized.strip().replace(",", "")
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _to_float_or_none(value: Any) -> float | None:
    normalized = _json_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool):
        return float(int(normalized))
    if isinstance(normalized, (int, float)):
        number = float(normalized)
        if math.isnan(number):
            return None
        return number
    if isinstance(normalized, str):
        text = normalized.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_related_ticker(value: Any) -> str | None:
    code = _extract_code6(value)
    if code is None:
        return None
    exchange = "SH" if code.startswith("6") else "SZ"
    return f"{code}.{exchange}"


def _match_target(raw_row: Mapping[str, Any], query: ProviderQuery, *, default_when_unknown: bool) -> bool:
    target_code = _ticker_code_6(query.ticker)
    code = _extract_code6(_pick_first(raw_row, _CODE_ALIASES))
    if code is not None:
        return code == target_code
    company_name = _to_optional_str(query.company_name)
    if company_name:
        row_name = _to_optional_str(_pick_first(raw_row, _NAME_ALIASES))
        if row_name:
            return row_name == company_name
    return default_when_unknown


def _extract_code6(value: Any) -> str | None:
    text = _to_optional_str(value)
    if text is None:
        return None
    match = _CODE_RE.search(text)
    if match is None:
        return None
    return match.group(1)


def _to_eastmoney_symbol(ticker: str) -> str:
    code, exchange = _split_ticker(ticker)
    if exchange == "SH":
        return f"SH{code}"
    return f"SZ{code}"


def _ticker_code_6(ticker: str) -> str:
    code, _ = _split_ticker(ticker)
    return code


def _split_ticker(ticker: str) -> tuple[str, str]:
    code, _, exchange = ticker.partition(".")
    if not code or not exchange:
        raise ValueError("ticker 必须是 NNNNNN.SH/SZ")
    exchange_upper = exchange.upper()
    if exchange_upper not in {"SH", "SZ"}:
        raise ValueError("ticker 交易所必须为 SH/SZ")
    return code, exchange_upper


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


__all__ = [
    "AKSHARE_SOCIAL_CALL_REGISTRY",
    "call_akshare_stock_hot_keyword_em",
    "call_akshare_stock_hot_rank_em",
    "call_akshare_stock_hot_rank_latest_em",
    "call_akshare_stock_hot_rank_relate_em",
    "call_akshare_stock_hot_up_em",
]
