from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from claw_trade.data_gateway.execution.fetch_engine import FetchTask
from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec, ManagedHttp
from claw_trade.data_gateway.needs import ProviderCallSpec
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.official_catalog.models import endpoint as catalog_endpoint
from claw_trade.data_gateway.provider_official_catalog import iter_official_provider_catalog
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.providers.plugins.official_api import (
    OFFICIAL_API_DATA_TYPE,
    OFFICIAL_API_SOURCE_SPECS,
    OfficialApiProviderPlugin,
    _official_endpoint_capabilities,
    _official_request_timeout_seconds,
    _path_from_official_path,
)


@dataclass
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str
    content: bytes | None = None


class _RecordingHttpClient:
    def __init__(self, response_text: str = '{"ok":true}', *, status_code: int = 200) -> None:
        self.requests: list[HttpRequestSpec] = []
        self.response_text = response_text
        self.status_code = status_code

    def send(self, request: HttpRequestSpec) -> _HttpResponse:
        self.requests.append(request)
        return _HttpResponse(status_code=self.status_code, headers={}, text=self.response_text)


class _Resolver:
    def __init__(self, source_type: str, *, endpoint_url: str | None = None, header_name: str | None = None) -> None:
        self.source_type = source_type
        self.endpoint_url = endpoint_url
        self.header_name = header_name

    def get_credential(self, name: str) -> str | None:
        return f"{self.source_type}-token" if name == f"data_source:{self.source_type}" else None

    def get_endpoint_url(self, name: str) -> str | None:
        if self.endpoint_url and name == f"data_source:{self.source_type}":
            return self.endpoint_url
        return None

    def get_header_name(self, name: str) -> str | None:
        if self.header_name and name == f"data_source:{self.source_type}":
            return self.header_name
        return None


def _task(
    provider_id: str,
    source_type: str,
    params: dict[str, object],
    *,
    market: str = "CRYPTO",
    symbol: str | None = None,
    data_type: str = OFFICIAL_API_DATA_TYPE,
) -> FetchTask:
    symbol = symbol or ("600519.SH" if source_type == "tushare" else "BTCUSDT")
    endpoint_id = _endpoint_id(params)
    return FetchTask(
        batch_id=f"batch:{source_type}",
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        market=market,
        data_type=data_type,
        granularity="event",
        symbol_ids=(symbol,),
        date_range_start=None,
        date_range_end=None,
        fields=("raw_payload",),
        provider_config_version="test",
        params=params,
        deadline_at=datetime(2026, 6, 12, 12, 1, tzinfo=UTC),
    )


def _endpoint_id(params: dict[str, object]) -> str:
    candidate = params.get("provider_call_spec") or params.get("call_spec")
    if isinstance(candidate, ProviderCallSpec):
        return candidate.catalog_endpoint_id
    if isinstance(candidate, dict):
        value = candidate.get("catalog_endpoint_id")
        return str(value) if value is not None else "unknown_endpoint"
    value = params.get("catalog_endpoint_id")
    return str(value) if value is not None else "unknown_endpoint"


def _call_spec(source_type: str, endpoint_id: str, params: dict[str, object]) -> dict[str, object]:
    return ProviderCallSpec(**_call_spec_payload(source_type, endpoint_id, params)).model_dump()


def _call_spec_for_public_api(source_type: str, endpoint_id: str, params: dict[str, object], public_api_id: str) -> dict[str, object]:
    payload = _call_spec_payload(source_type, endpoint_id, params)
    payload["public_api_id"] = public_api_id
    payload["business_api_id"] = public_api_id
    return ProviderCallSpec(**payload).model_dump()


def _call_spec_payload(source_type: str, endpoint_id: str, params: dict[str, object]) -> dict[str, object]:
    endpoint = next(item for item in iter_official_catalog_endpoints() if item.source_type == source_type and item.endpoint_id == endpoint_id)
    return {
        "call_id": f"call:{endpoint_id}",
        "method": endpoint.method,
        "provider_id": endpoint.provider_id,
        "catalog_endpoint_id": endpoint.endpoint_id,
        "official_path_or_api_name": endpoint.official_path_or_api_name,
        "params": params,
        "auth_scope": endpoint.auth,
        "rate_limit_bucket": endpoint.rate_limit_bucket,
        "http_visibility": endpoint.http_visibility.value,
        "parser_status": endpoint.parser_status,
        "batch_key": f"batch:{endpoint_id}",
        "official_doc_ref": endpoint.official_doc_ref,
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "need_ids": ("need-1",),
    }


def _plugin(source_type: str) -> OfficialApiProviderPlugin:
    spec = next(item for item in OFFICIAL_API_SOURCE_SPECS if item.source_type == source_type)
    return OfficialApiProviderPlugin(spec)


def test_coinglass_slow_official_endpoints_have_matching_http_timeout() -> None:
    assert _official_request_timeout_seconds(
        ProviderCallSpec(
            **_call_spec_payload(
                "coinglass",
                "coinglass.futures_liquidation_heatmap",
                {"symbol": "BTC", "range": "1y"},
            )
        )
    ) >= 55.0
    assert _official_request_timeout_seconds(
        ProviderCallSpec(
            **_call_spec_payload(
                "coinglass",
                "coinglass.bitcoin_ahr999",
                {"symbol": "BTC"},
            )
        )
    ) >= 55.0


def test_official_api_plugins_are_registered_for_only_kept_keyed_sources() -> None:
    registry = build_minimal_provider_registry()
    official_provider_ids = {entry.provider_id for entry in OFFICIAL_API_SOURCE_SPECS}
    official_caps = [cap for cap in registry.list_all_capabilities() if cap.provider_id in official_provider_ids]
    source_types = {
        entry.source_type
        for provider_id in {cap.provider_id for cap in official_caps}
        for entry in OFFICIAL_API_SOURCE_SPECS
        if entry.provider_id == provider_id
    }

    assert source_types == {"tushare", "finnhub", "fred", "coingecko_pro", "coinglass", "glassnode"}
    assert all(cap.endpoint_id != "official_api_call" for cap in official_caps)
    assert {"tushare.daily", "finnhub.quote", "coinglass.futures_open_interest_aggregated_history"} <= {
        cap.endpoint_id for cap in official_caps
    }
    assert not any(cap.endpoint_id == "finnhub.quote" and cap.market != "US" for cap in official_caps)
    assert not any(cap.endpoint_id == "coinglass.futures_open_interest" and cap.market != "CRYPTO" for cap in official_caps)
    formal_fact_ids = {cap.endpoint_id for cap in official_caps if cap.can_be_formal_fact_source}
    assert {"tushare.daily", "coinglass.futures_funding_rate"} <= formal_fact_ids
    assert "coinglass.raw_futures_funding_rate_history" in formal_fact_ids


def test_official_provider_catalog_keeps_docs_for_only_kept_keyed_sources() -> None:
    catalog = iter_official_provider_catalog()
    assert {entry.source_type for entry in catalog} == {"tushare", "finnhub", "fred", "coingecko_pro", "coinglass", "glassnode"}
    assert all(entry.docs_url.startswith("https://") for entry in catalog)
    tushare = next(entry for entry in catalog if entry.source_type == "tushare")
    assert tushare.interface_mode == "post_api_name_and_sdk"


def test_raw_official_endpoint_without_catalog_market_is_not_registered() -> None:
    raw_endpoint = catalog_endpoint(
        provider_id="official_api_tushare",
        source_type="tushare",
        endpoint_id="tushare.raw_future_catalog_entry",
        official_path_or_api_name="future_catalog_entry",
        method="POST",
        required_params=("ts_code",),
        auth="tushare_body_token",
        rate_limit_bucket="ratelimit:tushare",
        parser_status="parser_missing",
        official_doc_ref="https://tushare.pro/document/2",
    )

    assert _official_endpoint_capabilities((raw_endpoint,)) == ()


def test_raw_official_endpoint_without_output_contract_is_not_registered() -> None:
    raw_endpoint = catalog_endpoint(
        provider_id="official_api_tushare",
        source_type="tushare",
        endpoint_id="tushare.raw_future_catalog_entry",
        official_path_or_api_name="future_catalog_entry",
        method="POST",
        required_params=("ts_code",),
        auth="tushare_body_token",
        rate_limit_bucket="ratelimit:tushare",
        parser_status="parser_missing",
        official_doc_ref="https://tushare.pro/document/2",
        response_shape={"markets": ("CN_A",)},
    )

    assert _official_endpoint_capabilities((raw_endpoint,)) == ()


def test_tushare_official_api_uses_api_name_body_token() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "empty"
    request = client.requests[0]
    assert request.method == "POST"
    assert request.host == "https://api.tushare.pro"
    assert request.path == "/daily"
    assert request.timeout_seconds == 6.0
    body = json.loads(str(request.body))
    assert body == {
        "api_name": "daily",
        "token": "tushare-token",
        "params": {"ts_code": "600519.SH", "ts_type_name": "https://api.tushare.pro"},
        "fields": "",
    }


def test_tushare_official_daily_parses_rows_as_daily_bar() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","open","high","low","close","vol","amount"],'
        '"items":[["600519.SH","20260611",1500,1510,1490,1505,1000,1505000]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "daily_bar"
    assert row["symbol_id"] == "600519.SH"
    assert row["date"].isoformat() == "2026-06-11"
    assert row["close"] == 1505.0
    assert row["amount"] == 1505000000.0
    assert row["source_roles"] == ("paid_data",)


def test_tushare_official_daily_skips_rows_without_core_ohlcv_values() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","open","high","low","close","vol","amount"],'
        '"items":[["600519.SH","20260611","","bad",null,"",null,1505000]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "empty"
    assert result.error_code == "provider_empty"
    assert result.row_count == 0


def test_tushare_official_daily_basic_maps_total_mv_unit() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","pe","pb","ps","total_mv"],'
        '"items":[["600519.SH","20260611",20.1,8.2,12.3,2500000]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily_basic", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "valuation_metric"
    assert row["symbol_id"] == "600519.SH"
    assert row["date"].isoformat() == "2026-06-11"
    assert row["market_cap"] == 2500000.0
    assert row["market_cap_unit"] == "CNY_10K"


def test_tushare_official_moneyflow_maps_main_net_and_unit() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","trade_date","net_mf_amount","buy_lg_amount"],'
        '"items":[["600519.SH","20260612",1234.5,88.0]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.moneyflow", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "capital_flow"
    assert row["main_net"] == 1234.5
    assert row["large_net"] == 88.0
    assert row["amount_unit"] == "CNY_10K"


def test_tushare_official_sector_dc_maps_moneyflow_fields() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["trade_date","content_type","ts_code","name","net_amount",'
        '"net_amount_rate","buy_elg_amount","buy_lg_amount","buy_md_amount","buy_sm_amount"],'
        '"items":[["20260612","行业","BK0475","白酒",123456789,3.5,100,90,80,70]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {
            "provider_call_spec": _call_spec(
                "tushare",
                "tushare.moneyflow_ind_dc",
                {"trade_date": "20260612", "content_type": "行业"},
            )
        },
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "sector_snapshot"
    assert row["sector_code"] == "BK0475"
    assert row["sector_name"] == "白酒"
    assert row["main_net"] == 123456789.0
    assert row["super_net"] == 100.0
    assert row["large_net"] == 90.0
    assert row["timestamp"].isoformat() == "2026-06-12T00:00:00+00:00"
    assert row["amount_unit"] == "CNY"


def test_tushare_official_empty_tabular_payload_is_not_parsed_as_code_row() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["trade_date","ts_code","name","net_amount"],"items":[]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.moneyflow_ind_dc", {"trade_date": "20260612"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "empty"
    assert result.row_count == 0


@pytest.mark.parametrize(
    ("endpoint_id", "name_field", "name_value"),
    [
        ("tushare.moneyflow_ind_ths", "industry", "白酒"),
        ("tushare.moneyflow_cnt_ths", "name", "白酒概念"),
    ],
)
def test_tushare_official_ths_sector_interfaces_map_common_moneyflow_fields(
    endpoint_id: str,
    name_field: str,
    name_value: str,
) -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["trade_date","ts_code","'
        + name_field
        + '","net_amount"],"items":[["20260612","881001.TI","'
        + name_value
        + '",12.3]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", endpoint_id, {"trade_date": "20260612"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "sector_snapshot"
    assert row["sector_code"] == "881001.TI"
    assert row["sector_name"] == name_value
    assert row["main_net"] == 12.3
    assert row["amount_unit"] == "CNY_100M"


def test_tushare_official_financial_rows_map_common_field_aliases_and_unit() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","end_date","revenue","n_income_attr_p"],'
        '"items":[["600519.SH","20260331",9000000000,4500000000]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.income", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "financial_statement"
    assert row["symbol_id"] == "600519.SH"
    assert row["period"].isoformat() == "2026-03-31"
    assert row["revenue"] == 9000000000.0
    assert row["net_income"] == 4500000000.0
    assert row["amount_unit"] == "CNY"


def test_tushare_official_hk_mins_maps_intraday_rows() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","trade_time","open","high","low","close","vol","amount"],'
        '"items":[["00700.HK","2026-06-11 10:30:00",388.0,392.0,386.0,390.5,1000000,390500000]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.hk_mins", {"ts_code": "00700.HK", "freq": "60MIN"})},
        market="HK",
        symbol="00700.HK",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    request = client.requests[0]
    body = json.loads(str(request.body))
    assert body["api_name"] == "hk_mins"
    assert body["params"] == {
        "ts_code": "00700.HK",
        "freq": "60MIN",
        "ts_type_name": "https://api.tushare.pro",
    }
    row = result.payload["rows"][0]
    assert row["dataset"] == "intraday_bar"
    assert row["market"] == "HK"
    assert row["symbol_id"] == "00700.HK"
    assert row["timestamp"].isoformat() == "2026-06-11T10:30:00+00:00"
    assert row["open"] == 388.0
    assert row["close"] == 390.5
    assert row["volume"] == 1000000.0


def test_tushare_official_rt_hk_k_maps_realtime_quote_rows() -> None:
    client = _RecordingHttpClient(
        '{"code":0,"msg":"","data":{"fields":["ts_code","trade_time","open","high","low","close","vol"],'
        '"items":[["00700.HK","2026-06-11 15:59:00",388.0,392.0,386.0,390.5,1000000]]}}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.rt_hk_k", {"ts_code": "00700.HK"})},
        market="HK",
        symbol="00700.HK",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "quote_snapshot"
    assert row["price"] == 390.5
    assert row["timestamp"].isoformat() == "2026-06-11T15:59:00+00:00"
    assert row["volume"] == 1000000.0


def test_tushare_official_hk_statement_interfaces_map_distinct_statement_fields() -> None:
    cases = (
        (
            "tushare.hk_income",
            '{"code":0,"msg":"","data":{"fields":["ts_code","end_date","revenue","profit_to_parent"],'
            '"items":[["00700.HK","20260331",180000000000,42000000000]]}}',
            {"revenue": 180000000000.0, "net_income": 42000000000.0},
        ),
        (
            "tushare.hk_balancesheet",
            '{"code":0,"msg":"","data":{"fields":["ts_code","end_date","total_assets","total_liab"],'
            '"items":[["00700.HK","20260331",1700000000000,760000000000]]}}',
            {"assets": 1700000000000.0, "liabilities": 760000000000.0},
        ),
        (
            "tushare.hk_cashflow",
            '{"code":0,"msg":"","data":{"fields":["ts_code","end_date","net_operate_cash_flow"],'
            '"items":[["00700.HK","20260331",88000000000]]}}',
            {"cash_flow": 88000000000.0},
        ),
    )

    for endpoint_id, payload, expected in cases:
        client = _RecordingHttpClient(payload)
        plugin = _plugin("tushare")
        task = _task(
            plugin.plugin_id,
            "tushare",
            {"provider_call_spec": _call_spec("tushare", endpoint_id, {"ts_code": "00700.HK"})},
            market="HK",
            symbol="00700.HK",
        )

        result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

        assert result.status.value == "success"
        row = result.payload["rows"][0]
        assert row["dataset"] == "financial_statement"
        assert row["market"] == "HK"
        assert row["symbol_id"] == "00700.HK"
        assert row["period"].isoformat() == "2026-03-31"
        for key, value in expected.items():
            assert row[key] == value


def test_tushare_api_business_error_is_not_reported_as_success() -> None:
    client = _RecordingHttpClient(
        '{"code":40101,"msg":"请指定正确的接口名","data":null}'
    )
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "error"
    assert "tushare_api_error:40101:请指定正确的接口名" in str(result.error_message)


def test_tushare_permission_business_error_is_permission_denied() -> None:
    client = _RecordingHttpClient('{"code":2002,"msg":"该接口无权限，请升级套餐","data":null}')
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "permission_denied"
    assert "该接口无权限" in str(result.error_message)


def test_tushare_frequency_business_error_is_rate_limited() -> None:
    client = _RecordingHttpClient('{"code":2001,"msg":"每分钟最多访问 10 次","data":null}')
    plugin = _plugin("tushare")
    task = _task(
        plugin.plugin_id,
        "tushare",
        {"provider_call_spec": _call_spec("tushare", "tushare.daily", {"ts_code": "600519.SH"})},
        market="CN_A",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("tushare")))

    assert result.status.value == "rate_limited"


def test_rest_official_http_403_is_permission_denied() -> None:
    client = _RecordingHttpClient('{"error":"forbidden"}', status_code=403)
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_open_interest", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "permission_denied"
    assert "http_403" in str(result.error_message)


def test_coinglass_business_error_code_is_not_normalized_as_success() -> None:
    client = _RecordingHttpClient('{"code":400,"msg":"bad request","data":null}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_open_interest", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "error"
    assert "coinglass_api_error:400:bad request" in str(result.error_message)
    assert result.payload is None


def test_official_api_generic_parser_empty_when_required_business_field_missing() -> None:
    client = _RecordingHttpClient('{"code":"0","data":[{"time":1781269200000}]}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.futures_open_interest_aggregated_history",
                {"symbol": "BTC", "interval": "1h", "unit": "USD"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "empty"
    assert result.error_code == "provider_empty"
    assert result.row_count == 0


def test_official_api_generic_parser_structures_event_rows() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"title":"ETF flow update","published_at":"2026-06-12T12:00:00Z",'
        '"link":"https://example.test/news","source":"Coinglass","description":"net inflow"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_article_list", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "company_news"
    assert row["event_id"].startswith("event:coinglass:coinglass.raw_article_list:")
    assert row["event_type"] == "company_news"
    assert row["title"] == "ETF flow update"
    assert row["published_at"].isoformat() == "2026-06-12T12:00:00+00:00"
    assert "url" not in row
    assert row["source"] == "Coinglass"


def test_coinglass_article_list_can_structure_macro_news_rows() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"title":"Fed liquidity update","published_at":"2026-06-12T12:00:00Z",'
        '"link":"https://example.test/macro","source":"Coinglass","description":"macro driver"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_article_list", {"symbol": "BTC"})},
        data_type="macro_news",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "macro_news"
    assert row["event_type"] == "macro_news"
    assert row["title"] == "Fed liquidity update"
    assert row["published_at"].isoformat() == "2026-06-12T12:00:00+00:00"


def test_coinglass_article_list_maps_live_article_fields_without_fake_url() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "code": "0",
                "data": [
                    {
                        "article_title": "Bitcoin ETF flow update",
                        "article_release_time": "2026-06-12T12:00:00Z",
                        "article_description": "Net inflow expanded",
                        "source_name": "CoinGlass",
                    }
                ],
            }
        )
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_article_list", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["title"] == "Bitcoin ETF flow update"
    assert row["published_at"].isoformat() == "2026-06-12T12:00:00+00:00"
    assert row["summary"] == "Net inflow expanded"
    assert row["source"] == "CoinGlass"
    assert "url" not in row


def test_coinglass_economic_calendar_structures_event_calendar_rows() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"event":"Fed decision","date":"2026-06-12",'
        '"category":"macro","source":"Coinglass","description":"rate decision"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_calendar_economic_data", {})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "event_calendar"
    assert row["event_id"].startswith("event:coinglass:coinglass.raw_calendar_economic_data:")
    assert row["event_type"] == "macro"
    assert row["title"] == "Fed decision"
    assert row["event_date"].isoformat() == "2026-06-12"
    assert row["source"] == "Coinglass"


def test_coinglass_economic_calendar_structures_live_calendar_fields() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "code": "0",
                "data": [
                    {
                        "calendar_name": "Initial Jobless Claims",
                        "country_code": "US",
                        "country_name": "United States",
                        "data_effect": "positive",
                        "forecast_value": "240K",
                        "previous_value": "245K",
                        "publish_timestamp": "2026-06-12T12:30:00Z",
                        "importance_level": 3,
                    }
                ],
            }
        )
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_calendar_economic_data", {})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "event_calendar"
    assert row["title"] == "Initial Jobless Claims"
    assert row["event_date"].isoformat() == "2026-06-12"
    assert row["source"] == "United States"


def test_coinglass_economic_calendar_can_structure_macro_news_rows() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"event":"Fed decision","date":"2026-06-12",'
        '"category":"macro","source":"Coinglass","description":"rate decision"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec_for_public_api("coinglass", "coinglass.raw_calendar_economic_data", {}, "crypto.macro_news")},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "macro_news"
    assert row["event_type"] == "macro"
    assert row["title"] == "Fed decision"
    assert row["published_at"].isoformat() == "2026-06-12T00:00:00+00:00"


def test_coinglass_calendar_catalog_does_not_require_news_published_at() -> None:
    endpoint = next(
        item
        for item in iter_official_catalog_endpoints()
        if item.endpoint_id == "coinglass.raw_calendar_economic_data"
    )
    output = endpoint.response_shape["outputs"][0]

    assert output["data_type"] == "event_calendar"
    assert "event_date" in output["fields"]
    assert "published_at" not in output["fields"]
    assert "url" not in output["fields"]
    assert "summary" not in output["fields"]


def test_fred_observations_structure_crypto_macro_series_rows() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "observations": [
                    {"date": "2026-05-01", "value": "4.08"},
                    {"date": "2026-06-01", "value": "4.05"},
                ]
            }
        )
    )
    plugin = _plugin("fred")
    task = _task(
        plugin.plugin_id,
        "fred",
        {"provider_call_spec": _call_spec("fred", "fred.series_fedfunds", {"series_id": "FEDFUNDS"})},
        market="CRYPTO",
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("fred")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "macro_series"
    assert row["series_id"] == "FEDFUNDS"
    assert row["date"].isoformat() == "2026-05-01"
    assert row["value"] == 4.08
    assert row["region"] == "US"


def test_coinglass_funding_rate_parses_ohlc_close_as_crypto_derivative_metric() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":['
        '{"time":1781269200000,"open":"-0.00021","high":"0.000952","low":"-0.00021","close":"0.000952"},'
        '{"time":1781272800000,"open":"0","high":"0","low":"0","close":"0"}'
        "]}"
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_funding_rate", {"symbol": "BTC", "interval": "1h"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["symbol_id"] == "BTC/USDT"
    assert row["granularity"] == "hourly"
    assert row["funding_rate"] == 0.000952
    assert row["funding_rate_unit"] == "percent"
    assert row["exchange"] == "COINGLASS_AGGREGATED"
    assert row["timestamp"].tzinfo == UTC
    assert row["source_roles"] == ("paid_data",)
    assert result.payload["rows"][1]["funding_rate"] == 0.0


def test_coinglass_generated_exact_output_contract_normalizes_funding_history() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","msg":"success","data":[{"time":1658880000000,"open":"0.004603","high":"0.009388","low":"-0.005063","close":"0.009229"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.raw_futures_funding_rate_history",
                {"exchange": "Binance", "symbol": "BTC", "interval": "1h"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["funding_rate"] == 0.009229
    assert row["funding_rate_unit"] == "percent"


def test_coinglass_generated_long_short_contract_normalizes_tabular_array_payload() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":{"columns":["time","top_account_long_short_ratio"],"rows":[[1781269200000,"1.23"]]}}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.raw_futures_top_long_short_account_ratio_history",
                {"exchange": "Binance", "symbol": "BTC", "interval": "1h"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["symbol_id"] == "BTC/USDT"
    assert row["granularity"] == "hourly"
    assert row["long_short_ratio"] == 1.23
    assert row["metric"] == "top_account_long_short_ratio"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_borrow_interest_rate_contract_normalizes_history() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1781269200000,"borrowInterestRate":"0.0125"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.raw_borrow_interest_rate_history",
                {"exchange": "Binance", "symbol": "BTC", "interval": "1h"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["symbol_id"] == "BTC/USDT"
    assert row["granularity"] == "hourly"
    assert row["borrow_interest_rate"] == 0.0125
    assert row["borrow_interest_rate_unit"] == "percent"
    assert row["timestamp"].tzinfo == UTC


def test_official_api_generic_parser_uses_catalog_output_contract() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1781269200000,"openInterest":"123.4"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.futures_open_interest_aggregated_history",
                {"symbol": "BTC", "interval": "1h", "unit": "USD"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["symbol_id"] == "BTC/USDT"
    assert row["granularity"] == "hourly"
    assert row["open_interest"] == 123.4
    assert row["open_interest_unit"] == "USD"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_funding_rate_granularity_follows_interval_param() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1781269200000,"close":"0.009229"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.futures_funding_rate",
                {"symbol": "BTC", "interval": "1d"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["granularity"] == "daily"
    assert row["funding_rate"] == 0.009229


def test_official_api_realtime_output_adds_timestamp_when_provider_omits_time() -> None:
    client = _RecordingHttpClient('{"code":"0","data":[{"symbol":"BTC","current_price":"123.4"}]}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.spot_coins_markets", {"symbol": "BTC"})},
        data_type="quote_snapshot",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["price"] == 123.4
    assert row["timestamp"].tzinfo == UTC
    assert row["period_start"] == row["timestamp"].date()


def test_coinglass_global_long_short_ratio_uses_official_response_field() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1741604400000,"global_account_long_percent":73.88,'
        '"global_account_short_percent":26.12,"global_account_long_short_ratio":2.83}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.futures_long_short_ratio",
                {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1d"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["long_short_ratio"] == 2.83
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_liquidation_aggregated_history_maps_official_fields() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1658966400000,"aggregated_long_liquidation_usd":5916885.14234,'
        '"aggregated_short_liquidation_usd":12969583.87632}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.futures_liquidation",
                {"symbol": "BTC", "exchange_list": "Binance", "interval": "1d"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["long_liquidation"] == 5916885.14234
    assert row["short_liquidation"] == 12969583.87632
    assert "liquidation_value" not in row


def test_coinglass_liquidation_heatmap_maps_official_matrix_payload() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":{"y_axis":[47968.54,48000.0],"liquidation_leverage_data":[[0,1,2288867.26]],'
        '"price_candlesticks":[[1722676500,"61486","61596.4","61434.4","61539.9","63753192.1129"]]}}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_liquidation_heatmap", {"symbol": "BTC", "range": "3d"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["liquidation_price"] == 48000.0
    assert row["liquidation_size"] == 2288867.26
    assert row["timestamp"].tzinfo == UTC
    assert client.requests[0].timeout_seconds == 55.0


def test_coinglass_official_cvd_maps_official_fields() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1772236800000,"cum_vol_delta":"-2500",'
        '"aggregated_buy_volume_usd":"100","aggregated_sell_volume_usd":"2600"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.spot_cvd_history",
                {"symbol": "BTC", "exchange_list": "Binance,OKX,Bybit", "interval": "1h", "unit": "usd"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["symbol_id"] == "BTC/USDT"
    assert row["granularity"] == "hourly"
    assert row["cvd"] == -2500.0
    assert row["taker_buy_volume"] == 100.0
    assert row["taker_sell_volume"] == 2600.0
    assert row["taker_volume_unit"] == "USD"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_generated_raw_cvd_maps_live_cum_delta_field() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1772236800000,"cum_vol_delta":"-2500",'
        '"taker_buy_vol":"100","taker_sell_vol":"2600"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.raw_futures_cvd_history",
                {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1h"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["cvd"] == -2500.0
    assert row["taker_buy_volume"] == 100.0
    assert row["taker_sell_volume"] == 2600.0


def test_coinglass_official_cvd_without_cvd_field_is_provider_empty() -> None:
    client = _RecordingHttpClient('{"code":"0","data":[{"time":1772236800000,"close":"-2500","value":"-2500"}]}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {
            "provider_call_spec": _call_spec(
                "coinglass",
                "coinglass.spot_cvd_history",
                {"symbol": "BTC", "exchange_list": "Binance,OKX,Bybit", "interval": "1h", "unit": "usd"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "empty"
    assert result.error_code == "provider_empty"
    assert result.row_count == 0


def test_coinglass_exchange_balance_maps_official_time_list_data_map_payload() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time_list":[1691460000000],"price_list":[29140.9],'
        '"data_map":{"huobi":[15167.03527],"gate":[23412.723]}}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.onchain_exchange_balance", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_onchain_metric"
    assert row["metric"] == "exchange_balance"
    assert row["exchange_balance"] == pytest.approx(38579.75827)
    assert row["balance"] == pytest.approx(38579.75827)


def test_coinglass_official_ahr999_maps_dimensionless_value() -> None:
    client = _RecordingHttpClient('{"code":"0","data":{"ahr999":"0.3187","time":1772236800000}}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.bitcoin_ahr999", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_onchain_metric"
    assert row["symbol_id"] == "BTC/USDT"
    assert row["metric"] == "ahr999"
    assert row["ahr999"] == 0.3187
    assert row["value"] == 0.3187
    assert row["value_unit"] == "dimensionless"
    assert row["chain"] == "BTC"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_official_ahr999_maps_date_string_value() -> None:
    client = _RecordingHttpClient('{"code":"0","data":{"ahr999_value":"0.7","date_string":"2026/06/13"}}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.bitcoin_ahr999", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["metric"] == "ahr999"
    assert row["ahr999"] == 0.7
    assert row["period_start"].isoformat() == "2026-06-13"
    assert row["timestamp"].isoformat() == "2026-06-13T00:00:00+00:00"


def test_coinglass_spot_coin_netflow_maps_24h_value_from_official_payload() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":{"symbol":"BTC","net_flow_usd_24h":"286072825",'
        '"taker_buy_volume_usd_24h":"7566899147","taker_sell_volume_usd_24h":"7280826322"}}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.spot_coin_netflow", {"symbol": "BTC", "exchange_list": "Binance"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_onchain_metric"
    assert row["netflow"] == 286072825.0
    assert row["netflow_unit"] == "USD"
    assert row["net_inflow"] == 286072825.0
    assert row["net_inflow_unit"] == "USD"
    assert row["value"] == 286072825.0
    assert row["value_unit"] == "USD"


def test_coinglass_whale_transfer_maps_official_payload_fields() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"transaction_hash":"abc","amount_usd":"18403606.873493258",'
        '"asset_quantity":"10081.791421235566","asset_symbol":"WETH","blockchain_name":"ethereum",'
        '"block_timestamp":1746265043}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.onchain_whale_transfer", {"symbol": "BTC"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_onchain_metric"
    assert row["whale_transfer"] == 18403606.873493258
    assert row["value"] == 18403606.873493258
    assert row["chain"] == "ethereum"
    assert row["timestamp"].year == 2025


def test_glassnode_generated_exact_output_contract_normalizes_funding_metric() -> None:
    client = _RecordingHttpClient('[{"t":1781269200,"v":"0.0123"}]')
    plugin = _plugin("glassnode")
    task = _task(
        plugin.plugin_id,
        "glassnode",
        {
            "provider_call_spec": _call_spec(
                "glassnode",
                "glassnode.raw_v1_metrics_derivatives_futures_funding_rate_perpetual",
                {"a": "BTC", "i": "1h"},
            )
        },
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("glassnode")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["funding_rate"] == 0.0123
    assert row["funding_rate_unit"] == "percent"


@pytest.mark.parametrize(
    ("source_type", "params", "expected_path", "expected_query", "expected_header"),
    [
        (
            "finnhub",
            {"provider_call_spec": _call_spec("finnhub", "finnhub.quote", {"symbol": "AAPL"})},
            "/api/v1/quote",
            {"symbol": "AAPL", "token": "finnhub-token"},
            None,
        ),
        (
            "fred",
            {"provider_call_spec": _call_spec("fred", "fred.series_observations", {"series_id": "GDP"})},
            "/fred/series/observations",
            {"series_id": "GDP", "api_key": "fred-token", "file_type": "json"},
            None,
        ),
        (
            "coingecko_pro",
            {"provider_call_spec": _call_spec("coingecko_pro", "coingecko_pro.coins_markets", {"vs_currency": "usd"})},
            "/api/v3/coins/markets",
            {"vs_currency": "usd"},
            "x-cg-pro-api-key",
        ),
        (
            "glassnode",
            {"provider_call_spec": _call_spec("glassnode", "glassnode.addresses_active_count", {"a": "BTC"})},
            "/v1/metrics/addresses/active_count",
            {"a": "BTC", "api_key": "glassnode-token"},
            None,
        ),
    ],
)
def test_rest_official_api_plugins_inject_auth_without_endpoint_whitelist(
    source_type: str,
    params: dict[str, object],
    expected_path: str,
    expected_query: dict[str, str],
    expected_header: str | None,
) -> None:
    client = _RecordingHttpClient()
    plugin = _plugin(source_type)
    task = _task(plugin.plugin_id, source_type, params)

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver(source_type)))

    assert result.status.value == "empty"
    request = client.requests[0]
    assert request.path == expected_path
    assert request.query == expected_query
    if expected_header is not None:
        assert request.headers is not None
        assert request.headers[expected_header] == f"{source_type}-token"


def test_coinglass_official_api_reuses_configured_proxy_and_header_name() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_funding_rate", {"symbol": "BTC", "interval": "1h"})},
    )

    result = plugin.fetch(
        task,
        SimpleNamespace(
            managed_http=ManagedHttp(client),
            credential_resolver=_Resolver(
                "coinglass",
                endpoint_url="https://proxy.keystore.com.cn/api/v1/proxy/coinglass",
                header_name="X-Api-Key",
            ),
        ),
    )

    assert result.status.value == "empty"
    request = client.requests[0]
    assert request.host == "https://proxy.keystore.com.cn"
    assert request.path == "/api/v1/proxy/coinglass/v4/api/futures/funding-rate/oi-weight-history"
    assert request.headers is not None
    assert request.headers["X-Api-Key"] == "coinglass-token"


def test_coinglass_official_api_defaults_keystore_proxy_header_name() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_funding_rate", {"symbol": "BTC", "interval": "1h"})},
    )

    result = plugin.fetch(
        task,
        SimpleNamespace(
            managed_http=ManagedHttp(client),
            credential_resolver=_Resolver(
                "coinglass",
                endpoint_url="https://proxy.keystore.com.cn/api/v1/proxy/coinglass",
            ),
        ),
    )

    assert result.status.value == "empty"
    request = client.requests[0]
    assert request.headers is not None
    assert request.headers["X-Api-Key"] == "coinglass-token"
    assert "CG-API-KEY" not in request.headers


def test_coinglass_official_spot_markets_filters_list_to_requested_asset() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "code": "0",
                "data": [
                    {"symbol": "ETH", "price": "3500", "marketCap": "420000000000"},
                    {"symbol": "BTC", "price": "65000", "marketCap": "1280000000000"},
                ],
            }
        )
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.spot_coins_markets", {"symbol": "BTC"})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    rows = result.payload["rows"]
    assert len(rows) == 1
    assert rows[0]["base_asset"] == "BTC"
    assert rows[0]["price"] == 65000.0
    assert rows[0]["market_cap"] == 1280000000000.0


def test_coinglass_official_spot_markets_can_feed_realtime_quote_without_fake_supply_fields() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "code": "0",
                "data": [
                    {
                        "symbol": "ETH",
                        "current_price": "3500",
                        "market_cap": "420000000000",
                        "volume_usd_24h": "8000000000",
                    },
                    {
                        "symbol": "BTC",
                        "current_price": "65000",
                        "market_cap": "1280000000000",
                        "volume_usd_24h": "38000000000",
                    }
                ],
            }
        )
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.spot_coins_markets", {"symbol": "BTC"})},
        symbol="BTC/USDT",
        data_type="quote_snapshot",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    rows = result.payload["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["dataset"] == "quote_snapshot"
    assert row["price"] == 65000.0
    assert row["volume_usd"] == 38000000000.0
    assert "fdv" not in row
    assert "circulating_supply" not in row
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_coin_market_data_history_maps_supply_from_official_fields() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"timestamp":1781269200000,"price":"65000",'
        '"market_cap":"1280000000000","circulating_supply":"19700000"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.coin_market_data_history", {"symbol": "BTC"})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "valuation_metric"
    assert row["price"] == 65000.0
    assert row["market_cap"] == 1280000000000.0
    assert row["circulating_supply"] == 19700000.0
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_generated_btc_onchain_index_maps_project_metric() -> None:
    client = _RecordingHttpClient('{"code":"0","data":[{"timestamp":1781269200000,"active_address_count":"912345"}]}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_index_bitcoin_active_addresses", {})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_onchain_metric"
    assert row["metric"] == "active_addresses"
    assert row["active_addresses"] == 912345.0
    assert row["value"] == 912345.0
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_stablecoin_marketcap_columnar_payload_maps_project_metric() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":{"time_list":[1781269200],"data_list":[{"USDT":"165000000000","USDC":"25000000000"}],"price_list":[65000]}}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_index_stablecoin_marketcap_history", {})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_onchain_metric"
    assert row["metric"] == "stablecoin_market_cap"
    assert row["stablecoin_market_cap"] == 190000000000.0
    assert row["value"] == 190000000000.0
    assert row["value_unit"] == "USD"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_options_open_interest_maps_data_map_payload() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time_list":[1781269200000],"price_list":[65000],'
        '"data_map":{"Deribit":["123.5"],"OKX":["76.5"]}}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.options_open_interest", {"symbol": "BTC", "unit": "USD", "range": "all"})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["options_open_interest"] == 200.0
    assert row["options_open_interest_unit"] == "USD"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_options_volume_maps_data_map_payload() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time_list":[1781269200000],"price_list":[65000],'
        '"data_map":{"Deribit":["12.5"],"OKX":["7.5"]}}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.options_volume", {"symbol": "BTC", "unit": "USD"})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "crypto_derivative_metric"
    assert row["options_volume"] == 20.0
    assert row["options_volume_unit"] == "USD"
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_fear_greed_history_maps_social_signal() -> None:
    client = _RecordingHttpClient('{"code":"0","data":{"time_list":[1781269200000],"data_list":["72"],"price_list":["65000"]}}')
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.raw_index_fear_greed_history", {})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "social_signal"
    assert row["source"] == "coinglass"
    assert row["sentiment"] == 72.0
    assert row["value"] == 72.0
    assert row["timestamp"].tzinfo == UTC


def test_coinglass_taker_buy_sell_does_not_derive_ratio_from_live_volumes() -> None:
    client = _RecordingHttpClient(
        '{"code":"0","data":[{"time":1772236800000,"aggregated_buy_volume_usd":"150",'
        '"aggregated_sell_volume_usd":"100"}]}'
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.futures_taker_buy_sell", {"symbol": "BTCUSDT", "interval": "1h"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["taker_buy_volume"] == 150.0
    assert row["taker_sell_volume"] == 100.0
    assert "taker_buy_sell_ratio" not in row


def test_coinglass_official_etf_flow_expands_nested_flow_rows() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "code": "0",
                "data": [
                    {
                        "timestamp": 1781269200,
                        "price_usd": "65000",
                        "etf_flows": [
                            {"etf_ticker": "IBIT", "flow_usd": "1234567"},
                            {"etf_ticker": "FBTC", "flow_usd": "-234567"},
                        ],
                    }
                ],
            }
        )
    )
    plugin = _plugin("coinglass")
    task = _task(
        plugin.plugin_id,
        "coinglass",
        {"provider_call_spec": _call_spec("coinglass", "coinglass.etf_bitcoin_flow_history", {})},
        symbol="BTC/USDT",
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coinglass")))

    assert result.status.value == "success"
    rows = result.payload["rows"]
    assert len(rows) == 2
    assert rows[0]["etf_ticker"] == "IBIT"
    assert rows[0]["etf_flow_usd"] == 1234567.0
    assert rows[0]["etf_flow_usd_unit"] == "USD"
    assert rows[0]["price"] == 65000.0
    assert rows[1]["etf_ticker"] == "FBTC"
    assert rows[1]["etf_flow_usd"] == -234567.0


def test_official_api_rejects_nested_query_auth_injection_without_http() -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))
    call_spec = _call_spec_payload("finnhub", "finnhub.quote", {"query": {"symbol": "AAPL", "token": "evil-token"}})

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {"provider_call_spec": call_spec}), ctx)

    assert result.status.value == "not_applicable"
    assert "token" in str(result.error_message)
    assert client.requests == []


def test_official_api_rejects_nested_business_scope_params_without_http() -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))
    call_spec = _call_spec_payload("finnhub", "finnhub.quote", {"query": {"symbol": "AAPL", "only_for_social": "social"}})

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {"provider_call_spec": call_spec}), ctx)

    assert result.status.value == "not_applicable"
    assert "only_for_social" in str(result.error_message)
    assert client.requests == []


def test_official_api_replaces_catalog_path_template_params() -> None:
    client = _RecordingHttpClient()
    plugin = _plugin("coingecko_pro")
    task = _task(
        plugin.plugin_id,
        "coingecko_pro",
        {"provider_call_spec": _call_spec("coingecko_pro", "coingecko_pro.coins_id", {"id": "bitcoin"})},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coingecko_pro")))

    assert result.status.value == "empty"
    request = client.requests[0]
    assert request.path == "/api/v3/coins/bitcoin"
    assert request.query == {}
    assert request.headers is not None
    assert request.headers["x-cg-pro-api-key"] == "coingecko_pro-token"


def test_coingecko_pro_coin_profile_maps_supply_metadata() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "name": "Bitcoin",
                "symbol": "btc",
                "description": {"en": "Bitcoin has a fixed supply cap."},
                "links": {"homepage": ["https://bitcoin.org"]},
                "market_cap_rank": 1,
                "market_data": {
                    "circulating_supply": 20043234.849,
                    "total_supply": 20043234.849,
                    "max_supply": 21000000,
                },
            }
        )
    )
    plugin = _plugin("coingecko_pro")
    task = _task(
        plugin.plugin_id,
        "coingecko_pro",
        {"provider_call_spec": _call_spec_for_public_api("coingecko_pro", "coingecko_pro.coins_id", {"id": "bitcoin"}, "company_profile")},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coingecko_pro")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "company_profile"
    assert row["name"] == "Bitcoin"
    assert row["homepage"] == "https://bitcoin.org"
    assert row["market_cap_rank"] == 1
    assert row["circulating_supply"] == 20043234.849
    assert row["total_supply"] == 20043234.849
    assert row["max_supply"] == 21000000.0
    assert row["supply_unit"] == "BTC"


def test_coingecko_pro_coin_id_maps_valuation_supply_metadata() -> None:
    client = _RecordingHttpClient(
        json.dumps(
            {
                "market_data": {
                    "current_price": {"usd": 65862.54},
                    "market_cap": {"usd": 1319238923690.2993},
                    "fully_diluted_valuation": {"usd": 1383113340000},
                    "total_volume": {"usd": 33000000000},
                    "circulating_supply": 20043234.849,
                    "total_supply": 20043234.849,
                    "max_supply": 21000000,
                }
            }
        )
    )
    plugin = _plugin("coingecko_pro")
    task = _task(
        plugin.plugin_id,
        "coingecko_pro",
        {"provider_call_spec": _call_spec_for_public_api("coingecko_pro", "coingecko_pro.coins_id", {"id": "bitcoin"}, "valuation_metric")},
    )

    result = plugin.fetch(task, SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("coingecko_pro")))

    assert result.status.value == "success"
    row = result.payload["rows"][0]
    assert row["dataset"] == "valuation_metric"
    assert row["price"] == 65862.54
    assert row["market_cap"] == 1319238923690.2993
    assert row["fdv"] == 1383113340000.0
    assert row["circulating_supply"] == 20043234.849
    assert row["total_supply"] == 20043234.849
    assert row["max_supply"] == 21000000.0
    assert row["supply_unit"] == "BTC"


@pytest.mark.parametrize(
    "freeform_params",
    [
        {"path": "/quote"},
        {"api_name": "quote"},
        {"url": "https://evil.test"},
        {"header": "X-Key"},
        {"token": "secret"},
        {"consumer": "report"},
        {"allowed_worker": "market_analyst"},
        {"allowed_news": "news_analyst"},
        {"only_for_news": "news_analyst"},
    ],
)
def test_official_api_rejects_freeform_call_spec_params_without_http(freeform_params: dict[str, object]) -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", freeform_params), ctx)

    assert result.status.value == "not_applicable"
    assert client.requests == []


def test_official_api_rejects_missing_call_spec_without_http() -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {}), ctx)

    assert result.status.value == "not_applicable"
    assert client.requests == []


@pytest.mark.parametrize("official_path", ["https://evil.test/quote", "//evil.test/quote", "quote", "/../quote"])
def test_official_api_rejects_absolute_or_escaping_catalog_paths(official_path: str) -> None:
    plugin = _plugin("finnhub")
    client = _RecordingHttpClient()
    ctx = SimpleNamespace(managed_http=ManagedHttp(client), credential_resolver=_Resolver("finnhub"))
    call_spec = _call_spec("finnhub", "finnhub.quote", {"symbol": "AAPL"})
    call_spec["official_path_or_api_name"] = official_path
    plugin._catalog_by_endpoint_id["finnhub.quote"] = plugin._catalog_by_endpoint_id["finnhub.quote"].model_copy(
        update={"official_path_or_api_name": official_path}
    )

    result = plugin.fetch(_task(plugin.plugin_id, "finnhub", {"provider_call_spec": call_spec}), ctx)

    assert result.status.value == "not_applicable"
    assert "official_api_path" in str(result.error_message)
    assert client.requests == []


@pytest.mark.parametrize("official_path", ["https://evil.test/quote", "//evil.test/quote", "quote", "/../quote"])
def test_official_path_validator_rejects_absolute_or_escaping_paths(official_path: str) -> None:
    result = _path_from_official_path(official_path)

    assert isinstance(result, RuntimeError)
