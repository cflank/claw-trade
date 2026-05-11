from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Mapping

from claw_trade.providers.tushare_client import TushareClientConfigError, create_tushare_pro

from .models import ProviderQuery, ProviderSpec
from .provider_executor import ProviderCallContext, ProviderCallError, ProviderCallable
from .runtime_context import ToolRuntimeContext


def call_tushare_stock_basic(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    dataframe = _call_pro_api(
        api_name="stock_basic",
        call_context=call_context,
        ts_code=query.ticker,
        list_status="L",
        fields="ts_code,name,industry,market,list_date",
    )
    return _to_stock_basic_payload(dataframe, spec=spec, query=query)


def call_tushare_daily_basic(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    dataframe = _call_pro_api(
        api_name="daily_basic",
        call_context=call_context,
        ts_code=query.ticker,
        start_date=_yyyymmdd(query.start_date),
        end_date=_yyyymmdd(query.end_date),
        fields="ts_code,trade_date,close,pe_ttm,pb,total_mv,float_mv",
    )
    return _to_daily_basic_payload(dataframe, spec=spec)


def call_tushare_fina_indicator(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    dataframe = _call_pro_api(
        api_name="fina_indicator",
        call_context=call_context,
        ts_code=query.ticker,
        period=_latest_report_period(query.end_date),
        fields="ts_code,end_date,ann_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets",
    )
    return _to_fina_indicator_payload(dataframe, spec=spec)


def call_tushare_income(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    dataframe = _call_pro_api(
        api_name="income",
        call_context=call_context,
        ts_code=query.ticker,
        period=_latest_report_period(query.end_date),
        fields="ts_code,end_date,ann_date,revenue,n_income,basic_eps",
    )
    return _to_income_payload(dataframe, spec=spec)


def call_tushare_cashflow(
    spec: ProviderSpec,
    query: ProviderQuery,
    runtime_context: ToolRuntimeContext,
    call_context: ProviderCallContext,
) -> dict[str, Any]:
    _ = runtime_context
    dataframe = _call_pro_api(
        api_name="cashflow",
        call_context=call_context,
        ts_code=query.ticker,
        period=_latest_report_period(query.end_date),
        fields="ts_code,end_date,ann_date,n_cashflow_act",
    )
    return _to_cashflow_payload(dataframe, spec=spec)


TUSHARE_FUNDAMENTAL_CALL_REGISTRY: dict[tuple[str, str], ProviderCallable] = {
    ("tushare", "stock_basic"): call_tushare_stock_basic,
    ("tushare", "daily_basic"): call_tushare_daily_basic,
    ("tushare", "fina_indicator"): call_tushare_fina_indicator,
    ("tushare", "income"): call_tushare_income,
    ("tushare", "cashflow"): call_tushare_cashflow,
}


def _call_pro_api(*, api_name: str, call_context: ProviderCallContext, **kwargs: Any) -> Any:
    call_context.raise_if_cancelled()
    try:
        pro = create_tushare_pro()
    except TushareClientConfigError as exc:
        raise ProviderCallError("PROVIDER_KEY_MISSING", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderCallError("PROVIDER_ERROR", str(exc)) from exc

    call = getattr(pro, api_name, None)
    if not callable(call):
        raise ProviderCallError("PROVIDER_ERROR", f"tushare pro 缺少 {api_name} 接口")
    call_context.raise_if_cancelled()
    return call(**kwargs)


def _to_stock_basic_payload(dataframe: Any, *, spec: ProviderSpec, query: ProviderQuery) -> dict[str, Any]:
    raw_rows = _dataframe_rows(dataframe, endpoint=spec.endpoint)
    target_rows = [row for row in raw_rows if _normalize_ts_code(row.get("ts_code")) == query.ticker]
    rows: list[dict[str, Any]] = []
    for row in target_rows:
        _append_field(rows, "company_profile.company_name", row.get("name"), "text")
        _append_field(rows, "company_profile.industry", row.get("industry"), "text")
        _append_field(rows, "company_profile.listing_market", row.get("market"), "text")
        _append_field(rows, "company_profile.listing_date", _to_date(row.get("list_date")), "date")
    return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": rows, "data": raw_rows}


def _to_daily_basic_payload(dataframe: Any, *, spec: ProviderSpec) -> dict[str, Any]:
    raw_rows = _dataframe_rows(dataframe, endpoint=spec.endpoint)
    latest = _latest_row(raw_rows, "trade_date")
    rows: list[dict[str, Any]] = []
    if latest is not None:
        rows.append(
            {
                "price_context.close": latest.get("close"),
                "valuation.total_mv": _ten_thousand_cny_to_cny(latest.get("total_mv")),
                "valuation.pe_ttm": latest.get("pe_ttm"),
                "valuation.pb": latest.get("pb"),
                "price_context.trade_date": _to_date(latest.get("trade_date")),
                "source_time": _to_date(latest.get("trade_date")),
            }
        )
    return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": rows, "data": raw_rows}


def _to_fina_indicator_payload(dataframe: Any, *, spec: ProviderSpec) -> dict[str, Any]:
    raw_rows = _dataframe_rows(dataframe, endpoint=spec.endpoint)
    latest = _latest_row(raw_rows, "end_date")
    rows: list[dict[str, Any]] = []
    if latest is not None:
        rows.append(
            {
                "report_period": _to_date(latest.get("end_date")),
                "source_time": _to_date(latest.get("ann_date")) or _to_date(latest.get("end_date")),
                "financial_indicators.roe": latest.get("roe"),
                "financial_indicators.roa": latest.get("roa"),
                "financial_indicators.gross_margin": latest.get("grossprofit_margin"),
                "financial_indicators.netprofit_margin": latest.get("netprofit_margin"),
                "financial_indicators.debt_to_assets": latest.get("debt_to_assets"),
            }
        )
    return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": rows, "data": raw_rows}


def _to_income_payload(dataframe: Any, *, spec: ProviderSpec) -> dict[str, Any]:
    raw_rows = _dataframe_rows(dataframe, endpoint=spec.endpoint)
    latest = _latest_row(raw_rows, "end_date")
    rows: list[dict[str, Any]] = []
    if latest is not None:
        rows.append(
            {
                "report_period": _to_date(latest.get("end_date")),
                "source_time": _to_date(latest.get("ann_date")) or _to_date(latest.get("end_date")),
                "income_statement.revenue": latest.get("revenue"),
                "income_statement.net_profit": latest.get("n_income"),
                "income_statement.eps": latest.get("basic_eps"),
            }
        )
    return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": rows, "data": raw_rows}


def _to_cashflow_payload(dataframe: Any, *, spec: ProviderSpec) -> dict[str, Any]:
    raw_rows = _dataframe_rows(dataframe, endpoint=spec.endpoint)
    latest = _latest_row(raw_rows, "end_date")
    rows: list[dict[str, Any]] = []
    if latest is not None:
        rows.append(
            {
                "report_period": _to_date(latest.get("end_date")),
                "source_time": _to_date(latest.get("ann_date")) or _to_date(latest.get("end_date")),
                "cash_flow.operating_cash_flow": latest.get("n_cashflow_act"),
            }
        )
    return {"provider": spec.provider, "endpoint": spec.endpoint, "rows": rows, "data": raw_rows}


def _dataframe_rows(dataframe: Any, *, endpoint: str) -> list[dict[str, Any]]:
    if not hasattr(dataframe, "empty") or not hasattr(dataframe, "to_dict") or not hasattr(dataframe, "columns"):
        raise ProviderCallError("PROVIDER_SCHEMA_INVALID", f"tushare {endpoint} 返回类型不是 DataFrame")
    if bool(dataframe.empty):
        return []
    return _to_json_rows(dataframe.to_dict(orient="records"))


def _append_field(rows: list[dict[str, Any]], field_name: str, value: Any, unit: str) -> None:
    if value in (None, ""):
        return
    rows.append({"field_name": field_name, "value": value, "unit": unit})


def _latest_row(rows: list[Mapping[str, Any]], date_key: str) -> Mapping[str, Any] | None:
    candidates = [row for row in rows if row.get(date_key) not in (None, "")]
    if not candidates:
        return rows[0] if rows else None
    return sorted(candidates, key=lambda row: str(row.get(date_key)), reverse=True)[0]


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


def _normalize_ts_code(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().upper()


def _to_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if "T" in text:
        return text.split("T", 1)[0]
    return text


def _ten_thousand_cny_to_cny(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return float(value) * 10000.0
    except (TypeError, ValueError):
        return value


def _latest_report_period(end_date: str) -> str:
    year = int(end_date[0:4])
    month = int(end_date[5:7])
    day = int(end_date[8:10])
    if month > 10 or (month == 10 and day >= 31):
        return f"{year}0930"
    if month > 8 or (month == 8 and day >= 31):
        return f"{year}0630"
    if month > 4 or (month == 4 and day >= 30):
        return f"{year}0331"
    return f"{year - 1}1231"


def _yyyymmdd(iso_date: str) -> str:
    return iso_date.replace("-", "")


__all__ = [
    "TUSHARE_FUNDAMENTAL_CALL_REGISTRY",
    "call_tushare_cashflow",
    "call_tushare_daily_basic",
    "call_tushare_fina_indicator",
    "call_tushare_income",
    "call_tushare_stock_basic",
]
