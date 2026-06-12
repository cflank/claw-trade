from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable
from typing import Any

import pytest
from claw_trade.data_gateway.models import EndpointBatchPolicy
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint

FORBIDDEN_CATALOG_KEYS = {
    "allowed_worker",
    "allowed_domain",
    "allowed_report_section",
    "domain",
    "report_section",
    "provider_scope",
    "only_for_market",
    "only_for_fundamental",
}
REQUIRED_CATALOG_KEYS = {
    "provider_id",
    "endpoint_id",
    "official_path_or_api_name",
    "method",
    "required_params",
    "optional_params",
    "auth",
    "rate_limit_bucket",
    "batch_policy",
    "parser_status",
    "official_doc_ref",
    "http_visibility",
}
ALLOWED_CATALOG_KEYS = REQUIRED_CATALOG_KEYS | {
    "provider",
    "source_type",
    "request_template",
    "response_shape",
}
REQUIRED_NON_EMPTY_KEYS = {
    "provider_id",
    "endpoint_id",
    "official_path_or_api_name",
    "method",
    "auth",
    "rate_limit_bucket",
    "parser_status",
    "official_doc_ref",
    "http_visibility",
}
VALID_METHODS = {"GET", "POST"}
VALID_PARSER_STATUSES = {"normalized", "raw_only", "parser_missing"}
VALID_HTTP_VISIBILITY = {"managed_http", "sdk_internal_unknown", "no_http"}
REQUIRED_SOURCE_TYPES = {
    "tushare",
    "coinglass",
    "finnhub",
    "fred",
    "glassnode",
    "coingecko_pro",
    "akshare",
    "eastmoney",
    "mootdx",
    "baostock",
    "cninfo",
    "yahoo_finance",
    "binance",
    "sec",
}
REQUIRED_ENDPOINT_IDS = {
    "tushare.daily",
    "tushare.daily_basic",
    "tushare.moneyflow",
    "tushare.fina_indicator",
    "tushare.income",
    "tushare.balancesheet",
    "tushare.cashflow",
    "tushare.anns_d",
    "coinglass.futures_funding_rate",
    "coinglass.futures_open_interest",
    "coinglass.futures_long_short_ratio",
    "coinglass.futures_liquidation",
    "coinglass.options_open_interest",
    "coinglass.futures_coin_netflow",
    "coinglass.spot_coin_netflow",
    "finnhub.quote",
    "finnhub.stock_candle",
    "finnhub.company_news",
    "finnhub.stock_metric",
    "fred.series_observations",
    "glassnode.addresses_active_count",
    "coingecko_pro.coins_markets",
    "akshare.stock_zh_a_hist",
    "eastmoney.push2his.daily_bar",
    "mootdx.daily_bar",
    "yahoo_finance.chart_daily",
    "binance.spot_intraday_bar",
}


def test_official_catalog_has_endpoint_granularity_and_no_business_scope() -> None:
    spec = importlib.util.find_spec("claw_trade.data_gateway.official_catalog")
    assert spec is not None, "official catalog package is missing: claw_trade.data_gateway.official_catalog"

    endpoints = tuple(_iter_official_catalog_endpoints())
    assert endpoints, "official catalog must expose at least one endpoint"
    endpoint_ids = [str(_read_attr(endpoint, "endpoint_id")) for endpoint in endpoints]
    assert set(endpoint_ids) != {"official_api_call"}
    assert len(endpoint_ids) == len(set(endpoint_ids))
    assert REQUIRED_ENDPOINT_IDS <= set(endpoint_ids)
    assert REQUIRED_SOURCE_TYPES <= {str(_read_attr(endpoint, "source_type")) for endpoint in endpoints}

    for endpoint in endpoints:
        endpoint_id = str(_read_attr(endpoint, "endpoint_id"))
        raw_keys = _raw_keys(endpoint)
        assert endpoint_id and endpoint_id != "official_api_call"
        assert raw_keys <= ALLOWED_CATALOG_KEYS, (endpoint_id, sorted(raw_keys - ALLOWED_CATALOG_KEYS))
        assert REQUIRED_CATALOG_KEYS <= raw_keys, (endpoint_id, sorted(REQUIRED_CATALOG_KEYS - raw_keys))
        assert FORBIDDEN_CATALOG_KEYS.isdisjoint(raw_keys), (endpoint_id, sorted(FORBIDDEN_CATALOG_KEYS & raw_keys))
        for key in REQUIRED_NON_EMPTY_KEYS:
            assert _read_attr(endpoint, key), (endpoint_id, key)
        assert _value(_read_attr(endpoint, "method")) in VALID_METHODS, endpoint_id
        assert _value(_read_attr(endpoint, "parser_status")) in VALID_PARSER_STATUSES, endpoint_id
        assert _value(_read_attr(endpoint, "http_visibility")) in VALID_HTTP_VISIBILITY, endpoint_id
        assert _read_attr(endpoint, "batch_policy") is not None, endpoint_id


def test_official_catalog_rejects_nested_business_scope_metadata() -> None:
    with pytest.raises(ValueError, match="业务范围字段"):
        OfficialEndpoint(
            provider_id="official_api_tushare",
            source_type="tushare",
            endpoint_id="tushare.test_nested_scope",
            official_path_or_api_name="daily",
            method="POST",
            required_params=("ts_code",),
            auth="tushare_body_token",
            rate_limit_bucket="ratelimit:tushare",
            batch_policy=EndpointBatchPolicy(supports_batch=False, batch_by="none"),
            parser_status="normalized",
            official_doc_ref="https://tushare.pro/document/2",
            http_visibility="managed_http",
            request_template={"params": {"allowed_worker": "market_analyst"}},
        )


def _iter_official_catalog_endpoints() -> Iterable[Any]:
    catalog = importlib.import_module("claw_trade.data_gateway.official_catalog")
    for name in ("iter_official_catalog_endpoints", "iter_endpoints", "all_endpoints"):
        candidate = getattr(catalog, name, None)
        if callable(candidate):
            return candidate()
    endpoints = getattr(catalog, "OFFICIAL_ENDPOINTS", None)
    assert endpoints is not None, "official catalog must expose iter_endpoints/all_endpoints/OFFICIAL_ENDPOINTS"
    return endpoints


def _read_attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _raw_keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj)
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return set(model_dump())
    dataclass_fields = getattr(obj, "__dataclass_fields__", None)
    if isinstance(dataclass_fields, dict):
        return set(dataclass_fields)
    return set(getattr(obj, "__dict__", {}))
