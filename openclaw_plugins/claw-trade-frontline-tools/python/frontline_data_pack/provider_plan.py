from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Mapping

from .config import FrontlineProviderConfig
from .models import (
    Domain,
    ProviderAttempt,
    ProviderParamSource,
    ProviderQuery,
    ProviderQueryParameter,
    ProviderSpec,
    stable_json_dumps,
)
from .provider_specs import (
    assert_provider_approved,
    load_fundamental_provider_specs,
    load_market_provider_specs,
    load_news_provider_specs,
    load_social_provider_specs,
)
from .security import build_cn_a_provider_query


_DEFAULT_MARKET_ADJUST = "qfq"
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_LOCALE = "zh-CN"
_EASTMONEY_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_EASTMONEY_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def build_provider_query_from_tool_params(
    params: object,
    *,
    adjust: str | None = _DEFAULT_MARKET_ADJUST,
) -> ProviderQuery:
    """Build a normalized ProviderQuery from tool params.

    Extra fields (including `provider`) are ignored by design.
    """

    normalized = build_cn_a_provider_query(params)
    return build_provider_query(
        market="CN_A",
        ticker=normalized.ticker,
        company_name=normalized.company_name,
        industry=normalized.industry,
        start_date=normalized.start_date,
        end_date=normalized.end_date,
        adjust=adjust,
    )


def build_provider_query(
    *,
    market: str,
    ticker: str,
    company_name: str | None,
    industry: str | None,
    start_date: str,
    end_date: str,
    adjust: str | None,
) -> ProviderQuery:
    fingerprint = build_query_fingerprint(
        {
            "market": market,
            "ticker": ticker,
            "company_name": company_name,
            "industry": industry,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
        }
    )
    return ProviderQuery(
        market="CN_A",
        ticker=ticker,
        company_name=company_name,
        industry=industry,
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
        query_fingerprint=fingerprint,
    )


def build_query_fingerprint(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_provider_plan(
    *,
    domain: Domain,
    query: ProviderQuery,
    config: FrontlineProviderConfig,
    cache_inspection: list[ProviderAttempt],
) -> list[ProviderSpec]:
    del cache_inspection

    specs = _load_specs_for_domain(domain=domain, config=config)
    plan: list[ProviderSpec] = []
    for spec in sorted(
        specs,
        key=lambda item: (_PRIORITY_ORDER[item.priority], item.role, item.provider, item.endpoint),
    ):
        assert_provider_approved(domain=domain, provider=spec.provider)
        plan.append(replace(spec, query_parameters=_build_query_parameters(spec)))
    return plan


def materialize_provider_query_parameters(
    *,
    spec: ProviderSpec,
    query: ProviderQuery,
    config: FrontlineProviderConfig,
) -> dict[str, str | int]:
    resolved: dict[str, str | int] = {}
    for parameter in spec.query_parameters:
        value = _resolve_parameter_value(parameter=parameter, query=query, config=config)
        if value is None:
            continue
        resolved[parameter.name] = value
    return resolved


def ticker_to_eastmoney_secid(ticker: str) -> str:
    code, exchange = _split_ticker(ticker)
    if exchange == "SH":
        return f"1.{code}"
    return f"0.{code}"


def ticker_to_eastmoney_symbol(ticker: str) -> str:
    code, exchange = _split_ticker(ticker)
    if exchange == "SH":
        return f"100.{code}"
    return f"0.{code}"


def _load_specs_for_domain(*, domain: Domain, config: FrontlineProviderConfig) -> list[ProviderSpec]:
    if domain == "market":
        return load_market_provider_specs(config)
    if domain == "news":
        return load_news_provider_specs(config)
    if domain == "social":
        return load_social_provider_specs(config)
    if domain == "fundamental":
        return load_fundamental_provider_specs(config)
    raise ValueError(f"unsupported domain: {domain}")


def _build_query_parameters(spec: ProviderSpec) -> list[ProviderQueryParameter]:
    if spec.mode == "cache":
        return [
            _parameter(name="market", source="market_cn_a"),
            _parameter(name="ticker", source="ticker_exchange_suffix"),
            _parameter(name="start_date", source="start_date_yyyymmdd", required=False),
            _parameter(name="end_date", source="end_date_yyyymmdd", required=False),
        ]

    if spec.domain == "market":
        return _build_market_query_parameters(spec)
    if spec.domain == "news":
        return _build_news_query_parameters(spec)
    if spec.domain == "social":
        return _build_social_query_parameters(spec)
    return _build_fundamental_query_parameters(spec)


def _build_market_query_parameters(spec: ProviderSpec) -> list[ProviderQueryParameter]:
    if spec.provider == "eastmoney_direct" and spec.endpoint == "push2his_kline":
        return [
            _parameter(name="secid", source="ticker_secid"),
            _parameter(name="fields1", source="market_cn_a", fixed_value=_EASTMONEY_FIELDS1),
            _parameter(name="fields2", source="market_cn_a", fixed_value=_EASTMONEY_FIELDS2),
            _parameter(name="klt", source="market_cn_a", fixed_value=101),
            _parameter(name="fqt", source="adjust_qfq", fixed_value=1),
            _parameter(name="beg", source="start_date_yyyymmdd"),
            _parameter(name="end", source="end_date_yyyymmdd"),
        ]

    symbol_source = "ticker_code_6"
    if spec.endpoint in {"stock_zh_a_daily", "stock_zh_a_hist_tx"}:
        symbol_source = "ticker_exchange_suffix"

    return [
        _parameter(name="symbol", source=symbol_source),
        _parameter(name="start_date", source="start_date_yyyymmdd"),
        _parameter(name="end_date", source="end_date_yyyymmdd"),
        _parameter(name="adjust", source="adjust_qfq"),
    ]


def _build_news_query_parameters(spec: ProviderSpec) -> list[ProviderQueryParameter]:
    if spec.provider == "akshare" and spec.endpoint == "stock_news_em":
        return [_parameter(name="symbol", source="ticker_code_6")]
    if spec.provider == "akshare" and spec.endpoint == "stock_info_global_cls":
        return [
            _parameter(name="start_date", source="start_date_yyyymmdd"),
            _parameter(name="end_date", source="end_date_yyyymmdd"),
        ]

    return [
        _parameter(name="ticker", source="ticker_exchange_suffix"),
        _parameter(name="company_name", source="company_name", required=False),
        _parameter(name="industry", source="industry", required=False),
        _parameter(name="market", source="market_cn_a"),
        _parameter(name="start_date", source="start_date_yyyymmdd"),
        _parameter(name="end_date", source="end_date_yyyymmdd"),
        _parameter(name="page_size", source="page_size"),
        _parameter(name="locale", source="locale_zh_cn"),
    ]


def _build_social_query_parameters(spec: ProviderSpec) -> list[ProviderQueryParameter]:
    if spec.provider == "eastmoney_akshare":
        return [_parameter(name="symbol", source="eastmoney_symbol")]
    if spec.provider == "eastmoney_direct":
        return [_parameter(name="secid", source="ticker_secid")]

    return [
        _parameter(name="ticker", source="ticker_exchange_suffix"),
        _parameter(name="company_name", source="company_name", required=False),
        _parameter(name="industry", source="industry", required=False),
        _parameter(name="market", source="market_cn_a"),
        _parameter(name="start_date", source="start_date_yyyymmdd"),
        _parameter(name="end_date", source="end_date_yyyymmdd"),
        _parameter(name="page_size", source="page_size"),
        _parameter(name="locale", source="locale_zh_cn"),
    ]


def _build_fundamental_query_parameters(spec: ProviderSpec) -> list[ProviderQueryParameter]:
    if spec.provider == "eastmoney_direct":
        return [_parameter(name="secid", source="ticker_secid")]

    return [
        _parameter(name="symbol", source="ticker_code_6"),
        _parameter(name="start_date", source="start_date_yyyymmdd", required=False),
        _parameter(name="end_date", source="end_date_yyyymmdd", required=False),
    ]


def _parameter(
    *,
    name: str,
    source: ProviderParamSource,
    required: bool = True,
    fixed_value: str | int | None = None,
) -> ProviderQueryParameter:
    return ProviderQueryParameter(
        name=name,
        source=source,
        required=required,
        fixed_value=fixed_value,
    )


def _resolve_parameter_value(
    *,
    parameter: ProviderQueryParameter,
    query: ProviderQuery,
    config: FrontlineProviderConfig,
) -> str | int | None:
    if parameter.fixed_value is not None:
        return parameter.fixed_value

    code, exchange = _split_ticker(query.ticker)
    if parameter.source == "ticker_code_6":
        return code
    if parameter.source == "ticker_exchange_suffix":
        return f"{code}.{exchange}"
    if parameter.source == "ticker_secid":
        return ticker_to_eastmoney_secid(query.ticker)
    if parameter.source == "eastmoney_symbol":
        return ticker_to_eastmoney_symbol(query.ticker)
    if parameter.source == "start_date_yyyymmdd":
        return query.start_date.replace("-", "")
    if parameter.source == "end_date_yyyymmdd":
        return query.end_date.replace("-", "")
    if parameter.source == "adjust_qfq":
        return query.adjust or _DEFAULT_MARKET_ADJUST
    if parameter.source == "market_cn_a":
        return "CN_A"
    if parameter.source == "company_name":
        return query.company_name
    if parameter.source == "industry":
        return query.industry
    if parameter.source == "page_size":
        return max(config.provider_runtime.max_concurrency, 1) * _DEFAULT_PAGE_SIZE
    if parameter.source == "locale_zh_cn":
        return _DEFAULT_LOCALE
    return None


def _split_ticker(ticker: str) -> tuple[str, str]:
    code, exchange = ticker.split(".", 1)
    return code, exchange.upper()


__all__ = [
    "build_provider_plan",
    "build_provider_query",
    "build_provider_query_from_tool_params",
    "build_query_fingerprint",
    "materialize_provider_query_parameters",
    "ticker_to_eastmoney_symbol",
    "ticker_to_eastmoney_secid",
]
