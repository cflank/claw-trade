from __future__ import annotations

import importlib
import importlib.util
import json
from importlib import resources
from collections.abc import Iterable
from typing import Any

import pytest
from claw_trade.data_gateway.models import EndpointBatchPolicy
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint
from claw_trade.data_gateway.providers import build_minimal_provider_registry

FORBIDDEN_CATALOG_KEYS = {
    "allowed_worker",
    "allowed_domain",
    "allowed_report_section",
    "domain",
    "report_section",
    "provider_scope",
    "only_for_market",
    "only_for_fundamental",
    "markets",
    "semantic_outputs",
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
PUBLIC_SOURCE_TYPES = {
    "akshare",
    "eastmoney",
    "mootdx",
    "baostock",
    "cninfo",
    "yahoo_finance",
    "binance",
    "sec",
    "stocktwits",
    "sina_hk",
    "hkexnews",
    "defillama",
    "alternative_me",
    "github",
    "google_news",
    "ths",
    "baidu",
}
REQUIRED_ENDPOINT_IDS = {
    "tushare.daily",
    "tushare.raw_rt_k",
    "tushare.raw_stk_auction",
    "tushare.raw_research_report",
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
        forbidden_raw_keys = {key for key in raw_keys if _is_forbidden_catalog_key(key)}
        assert not forbidden_raw_keys, (endpoint_id, sorted(forbidden_raw_keys))
        for key in REQUIRED_NON_EMPTY_KEYS:
            assert _read_attr(endpoint, key), (endpoint_id, key)
        assert _value(_read_attr(endpoint, "method")) in VALID_METHODS, endpoint_id
        assert _value(_read_attr(endpoint, "parser_status")) in VALID_PARSER_STATUSES, endpoint_id
        assert _value(_read_attr(endpoint, "http_visibility")) in VALID_HTTP_VISIBILITY, endpoint_id
        assert _read_attr(endpoint, "batch_policy") is not None, endpoint_id


def test_official_catalog_rejects_nested_business_scope_metadata() -> None:
    for payload in (
        {"request_template": {"params": {"allowed_worker": "market_analyst"}}},
        {"request_template": {"params": {"allowed_news": "news_analyst"}}},
        {"request_template": {"params": {"only_for_news": "news_analyst"}}},
        {"response_shape": {"outputs": [{"only_for_social": "social_analyst"}]}},
    ):
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
                **payload,
            )


def test_official_provider_capabilities_are_catalog_endpoint_granular() -> None:
    catalog_endpoints = tuple(_iter_official_catalog_endpoints())
    catalog_endpoint_ids = {str(_read_attr(endpoint, "endpoint_id")) for endpoint in catalog_endpoints}
    official_provider_ids = {
        str(_read_attr(endpoint, "provider_id"))
        for endpoint in catalog_endpoints
        if str(_read_attr(endpoint, "provider_id")).startswith("official_api_")
    }
    registry = build_minimal_provider_registry()

    official_capabilities = [cap for cap in registry.list_all_capabilities() if cap.provider_id in official_provider_ids]

    assert official_capabilities
    assert all(cap.endpoint_id != "official_api_call" for cap in official_capabilities)
    assert {cap.endpoint_id for cap in official_capabilities} <= catalog_endpoint_ids
    assert {"tushare.daily", "finnhub.quote", "coinglass.futures_open_interest"} <= {cap.endpoint_id for cap in official_capabilities}
    assert not any(cap.endpoint_id == "finnhub.quote" and cap.market != "US" for cap in official_capabilities)
    assert not any(cap.endpoint_id == "coinglass.futures_open_interest" and cap.market != "CRYPTO" for cap in official_capabilities)


def test_public_source_catalog_endpoints_declare_data_outputs() -> None:
    public_endpoints = [
        endpoint
        for endpoint in _iter_official_catalog_endpoints()
        if str(_read_attr(endpoint, "source_type")) in PUBLIC_SOURCE_TYPES
    ]

    assert public_endpoints
    assert all((_read_attr(endpoint, "response_shape") or {}).get("outputs") for endpoint in public_endpoints)


def test_catalog_outputs_declare_public_api_ids() -> None:
    missing: list[str] = []
    for endpoint in _iter_official_catalog_endpoints():
        response_shape = _read_attr(endpoint, "response_shape") or {}
        outputs = response_shape.get("outputs") or ()
        for index, output in enumerate(outputs):
            if not isinstance(output, dict):
                missing.append(f"{_read_attr(endpoint, 'endpoint_id')}[{index}]:not_dict")
                continue
            public_api_ids = tuple(item for item in output.get("public_api_ids") or () if str(item).strip())
            if not public_api_ids:
                missing.append(f"{_read_attr(endpoint, 'endpoint_id')}[{index}]")

    assert not missing


def test_key_event_endpoints_have_structured_outputs() -> None:
    endpoints = {str(_read_attr(endpoint, "endpoint_id")): endpoint for endpoint in _iter_official_catalog_endpoints()}
    required = {
        "coinglass.raw_article_list": "company_news",
        "coinglass.raw_calendar_economic_data": "event_calendar",
        "finnhub.raw_news": "macro_news",
        "finnhub.raw_calendar_earnings": "event_calendar",
        "finnhub.raw_press_releases": "company_news",
        "finnhub.raw_global_filings_filter": "official_filing",
        "fred.raw_fred_releases": "event_calendar",
        "coingecko_pro.raw_news": "company_news",
    }

    for endpoint_id, data_type in required.items():
        endpoint = endpoints[endpoint_id]
        assert _value(_read_attr(endpoint, "parser_status")) == "normalized"
        outputs = (_read_attr(endpoint, "response_shape") or {}).get("outputs") or ()
        assert any(output.get("data_type") == data_type for output in outputs), endpoint_id
        event_output = next(output for output in outputs if output.get("data_type") == data_type)
        time_field = "event_date" if data_type == "event_calendar" else "published_at"
        required_fields = ("event_id", "event_type", "title", time_field, "source")
        if endpoint_id != "coinglass.raw_article_list":
            required_fields = (*required_fields, "url")
        if endpoint_id == "coinglass.raw_calendar_economic_data":
            required_fields = tuple(field for field in required_fields if field != "url")
        for field in required_fields:
            assert field in event_output["fields"], (endpoint_id, field)

    article_outputs = (_read_attr(endpoints["coinglass.raw_article_list"], "response_shape") or {}).get("outputs") or ()
    calendar_outputs = (_read_attr(endpoints["coinglass.raw_calendar_economic_data"], "response_shape") or {}).get("outputs") or ()
    assert any(output.get("data_type") == "macro_news" for output in article_outputs)
    assert any(output.get("data_type") == "macro_news" for output in calendar_outputs)


def test_coinglass_catalog_uses_official_llms_rest_snapshot_not_small_hand_list() -> None:
    endpoints = [endpoint for endpoint in _iter_official_catalog_endpoints() if _read_attr(endpoint, "source_type") == "coinglass"]
    endpoint_ids = {str(_read_attr(endpoint, "endpoint_id")) for endpoint in endpoints}
    paths = [str(_read_attr(endpoint, "official_path_or_api_name")) for endpoint in endpoints]
    generated = [endpoint for endpoint in endpoints if (_read_attr(endpoint, "response_shape") or {}).get("source") == "coinglass_llms_openapi"]

    assert len(endpoints) >= 150
    assert len(paths) == len(set(paths))
    assert len(generated) >= 100
    assert {
        "coinglass.raw_article_list",
        "coinglass.raw_futures_orderbook_history",
        "coinglass.raw_spot_orderbook_history",
        "coinglass.raw_futures_netflow_list",
        "coinglass.raw_spot_netflow_list",
        "coinglass.raw_user_account_subscription",
    } <= endpoint_ids

    account = next(endpoint for endpoint in endpoints if _read_attr(endpoint, "endpoint_id") == "coinglass.raw_user_account_subscription")
    assert not (_read_attr(account, "response_shape") or {}).get("outputs")
    funding = next(endpoint for endpoint in endpoints if _read_attr(endpoint, "endpoint_id") == "coinglass.raw_futures_funding_rate_history")
    outputs = (_read_attr(funding, "response_shape") or {}).get("outputs")
    assert outputs
    assert outputs[0]["public_api_ids"] == ("crypto.funding_rate",)
    assert _value(_read_attr(funding, "parser_status")) == "normalized"


def test_paid_catalogs_use_generated_official_snapshots_not_small_hand_lists() -> None:
    expected = {
        "coingecko_pro": ("coingecko_pro_oas", 80, "coingecko_pro.raw_derivatives"),
        "finnhub": ("finnhub_doc_schema", 100, "finnhub.raw_stock_upgrade_downgrade"),
        "fred": ("fred_api_docs", 30, "fred.raw_fred_v2_release_observations"),
        "glassnode": ("glassnode_llms_openapi", 800, "glassnode.raw_v1_metrics_derivatives_futures_funding_rate_perpetual"),
    }
    endpoints = tuple(_iter_official_catalog_endpoints())

    for source_type, (source_marker, minimum_count, required_endpoint_id) in expected.items():
        generated = [
            endpoint
            for endpoint in endpoints
            if _read_attr(endpoint, "source_type") == source_type
            and (_read_attr(endpoint, "response_shape") or {}).get("source") == source_marker
        ]
        endpoint_ids = {str(_read_attr(endpoint, "endpoint_id")) for endpoint in generated}

        assert len(generated) >= minimum_count, source_type
        assert required_endpoint_id in endpoint_ids
        for endpoint in generated:
            outputs = (_read_attr(endpoint, "response_shape") or {}).get("outputs") or ()
            assert all(output.get("public_api_ids") for output in outputs)


def test_tushare_catalog_combines_normalized_manual_endpoints_and_official_weborder_raw_endpoints() -> None:
    tushare = [
        endpoint
        for endpoint in _iter_official_catalog_endpoints()
        if _read_attr(endpoint, "provider_id") == "official_api_tushare"
    ]
    sources = {(_read_attr(endpoint, "response_shape") or {}).get("source") for endpoint in tushare}
    endpoint_ids = {str(_read_attr(endpoint, "endpoint_id")) for endpoint in tushare}

    assert tushare
    assert {"tushare_manual_official_doc", "tushare_weborder_permission_api"} <= sources
    assert {"tushare.raw_rt_k", "tushare.raw_stk_auction", "tushare.raw_research_report"} <= endpoint_ids


def test_tushare_generated_snapshot_uses_official_weborder_api_facts() -> None:
    specs = json.loads(
        resources.files("claw_trade.data_gateway.official_catalog.generated")
        .joinpath("tushare_weborder_permission_api.json")
        .read_text(encoding="utf-8")
    )
    endpoint_ids = {item["endpoint_id"] for item in specs}
    sources = {item["source"] for item in specs}

    assert len(specs) >= 70
    assert sources == {"tushare_weborder_permission_api"}
    assert {"tushare.raw_rt_k", "tushare.raw_stk_auction", "tushare.raw_research_report"} <= endpoint_ids
    assert all(item["parser_status"] == "parser_missing" for item in specs)


def test_official_catalog_does_not_mark_generated_raw_snapshots_as_normalized_without_explicit_outputs() -> None:
    normalized_endpoint_ids = {
        endpoint.endpoint_id
        for endpoint in _iter_official_catalog_endpoints()
        if str(_read_attr(endpoint, "provider_id")).startswith("official_api_")
        and _value(_read_attr(endpoint, "parser_status")) == "normalized"
    }

    assert {
        "tushare.daily",
        "coinglass.futures_funding_rate",
    } <= normalized_endpoint_ids
    assert "coinglass.raw_futures_funding_rate_history" in normalized_endpoint_ids
    assert "coinglass.raw_futures_open_interest_history" in normalized_endpoint_ids
    assert "glassnode.raw_v1_metrics_derivatives_futures_funding_rate_perpetual" in normalized_endpoint_ids


def test_coinglass_generated_long_short_catalog_outputs_are_inferred_from_official_response_fields() -> None:
    endpoint = next(
        endpoint
        for endpoint in _iter_official_catalog_endpoints()
        if _read_attr(endpoint, "endpoint_id") == "coinglass.raw_futures_top_long_short_account_ratio_history"
    )

    assert _value(_read_attr(endpoint, "parser_status")) == "normalized"
    outputs = (_read_attr(endpoint, "response_shape") or {}).get("outputs")
    assert outputs
    assert outputs[0]["data_type"] == "crypto_derivative_metric"
    assert outputs[0]["public_api_ids"] == ("crypto.long_short_ratio",)
    assert "long_short_ratio" in outputs[0]["fields"]


def test_coinglass_spot_markets_catalog_does_not_claim_supply_fields_absent_from_official_docs() -> None:
    endpoint = next(
        endpoint
        for endpoint in _iter_official_catalog_endpoints()
        if _read_attr(endpoint, "endpoint_id") == "coinglass.spot_coins_markets"
    )

    outputs = (_read_attr(endpoint, "response_shape") or {}).get("outputs")
    fields = {field for output in outputs for field in output["fields"]}
    assert "fdv" not in fields
    assert "circulating_supply" not in fields
    assert "total_supply" not in fields
    assert "volume_usd" in fields


def test_catalog_endpoints_have_source_evidence() -> None:
    missing: list[str] = []
    for endpoint in _iter_official_catalog_endpoints():
        response_shape = _read_attr(endpoint, "response_shape") or {}
        source = str(response_shape.get("source") or "").strip()
        doc_ref = str(_read_attr(endpoint, "official_doc_ref") or "").strip()
        observed_from_url = str(response_shape.get("observed_from_url") or "").strip()
        sdk_module = str(response_shape.get("sdk_module") or "").strip()
        if not source or not doc_ref or not (observed_from_url or sdk_module):
            missing.append(str(_read_attr(endpoint, "endpoint_id")))

    assert not missing


def test_sdk_internal_unknown_catalog_entries_identify_sdk_module() -> None:
    missing: list[str] = []
    for endpoint in _iter_official_catalog_endpoints():
        if _value(_read_attr(endpoint, "http_visibility")) != "sdk_internal_unknown":
            continue
        response_shape = _read_attr(endpoint, "response_shape") or {}
        sdk_module = str(response_shape.get("sdk_module") or "").strip()
        if not sdk_module:
            missing.append(str(_read_attr(endpoint, "endpoint_id")))

    assert not missing


def test_catalog_output_contracts_are_marked_normalized() -> None:
    mismatches: list[str] = []
    for endpoint in _iter_official_catalog_endpoints():
        response_shape = _read_attr(endpoint, "response_shape") or {}
        outputs = response_shape.get("outputs")
        parser_status = _value(_read_attr(endpoint, "parser_status"))
        if outputs and parser_status != "raw_only" and parser_status != "normalized":
            mismatches.append(str(_read_attr(endpoint, "endpoint_id")))

    assert not mismatches


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


def _is_forbidden_catalog_key(key: str) -> bool:
    return key in FORBIDDEN_CATALOG_KEYS or key.startswith(("allowed_", "only_for_"))
