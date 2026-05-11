from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Mapping

from claw_trade.providers.tushare_client import TushareClientConfigError, create_tushare_pro

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallError, ProviderCallable
from .runtime_context import ToolRuntimeContext


def call_tushare_anns_d(
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

    if not hasattr(pro, "anns_d"):
        raise ProviderCallError("PROVIDER_ERROR", "tushare pro 缺少 anns_d 接口")

    dataframe = pro.anns_d(
        ts_code=query.ticker,
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
    )
    return _to_announcement_payload(dataframe, spec=spec)


TUSHARE_NEWS_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("tushare", "anns_d"): call_tushare_anns_d,
}


def _to_announcement_payload(dataframe: Any, *, spec: ProviderSpec) -> dict[str, Any]:
    _validate_dataframe(dataframe)
    if bool(dataframe.empty):
        return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": [], "items": []}

    raw_rows = _to_json_rows(dataframe.to_dict(orient="records"))
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        title = _first_non_empty_str(row, ("title", "ann_title", "公告标题"))
        ann_date = _first_non_empty_str(row, ("ann_date", "date", "公告日期"))
        if title is None:
            continue
        rows.append(
            {
                "title": title,
                "source": _first_non_empty_str(row, ("source", "公告来源")) or "tushare_anns_d",
                "publish_time": _to_publish_time(ann_date),
                "url": _first_non_empty_str(row, ("url", "公告链接")),
                "content": _first_non_empty_str(row, ("summary", "content", "公告内容")),
                "announcement_subject": _first_non_empty_str(row, ("name", "short_name", "简称")),
            }
        )

    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "rows": rows,
        "items": rows,
        "data": raw_rows,
    }


def _validate_dataframe(dataframe: Any) -> None:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise ProviderCallError("PROVIDER_SCHEMA_INVALID", "tushare anns_d 返回类型不是 DataFrame")


def _to_json_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append({str(key): _json_scalar(value) for key, value in row.items()})
    return output


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


def _first_non_empty_str(row: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _to_publish_time(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _yyyymmdd(iso_date: str) -> str:
    return iso_date.replace("-", "")


__all__ = [
    "TUSHARE_NEWS_CALL_REGISTRY",
    "call_tushare_anns_d",
]
