from __future__ import annotations

from claw_trade.data_gateway.planner.call_planner import build_batch_key


def test_different_tushare_api_names_do_not_share_official_raw_batch_key() -> None:
    daily = build_batch_key(
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        auth_scope="tushare_body_token",
        params={"ts_code": "600519.SH"},
    )
    moneyflow = build_batch_key(
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.moneyflow",
        official_path_or_api_name="moneyflow",
        auth_scope="tushare_body_token",
        params={"ts_code": "600519.SH"},
    )

    assert daily != moneyflow


def test_different_coinglass_paths_do_not_share_official_raw_batch_key() -> None:
    funding = build_batch_key(
        provider_id="official_api_coinglass",
        catalog_endpoint_id="coinglass.futures_funding_rate",
        official_path_or_api_name="/api/futures/funding-rate/oi-weight-history",
        auth_scope="coinglass_header",
        params={"symbol": "BTC", "interval": "1h"},
    )
    open_interest = build_batch_key(
        provider_id="official_api_coinglass",
        catalog_endpoint_id="coinglass.futures_open_interest_aggregated_history",
        official_path_or_api_name="/api/futures/open-interest/aggregated-history",
        auth_scope="coinglass_header",
        params={"symbol": "BTC", "interval": "1h"},
    )

    assert funding != open_interest


def test_different_rate_limit_buckets_do_not_share_batch_key() -> None:
    source_bucket = build_batch_key(
        provider_id="official_api_coinglass",
        catalog_endpoint_id="coinglass.raw_endpoint",
        official_path_or_api_name="/api/example",
        auth_scope="coinglass_header",
        rate_limit_bucket="ratelimit:coinglass",
        http_visibility="managed_http",
        params={"symbol": "BTC"},
    )
    proxy_bucket = build_batch_key(
        provider_id="official_api_coinglass",
        catalog_endpoint_id="coinglass.raw_endpoint",
        official_path_or_api_name="/api/example",
        auth_scope="coinglass_header",
        rate_limit_bucket="ratelimit:coinglass_proxy",
        http_visibility="managed_http",
        params={"symbol": "BTC"},
    )

    assert source_bucket != proxy_bucket


def test_different_http_visibility_does_not_share_batch_key() -> None:
    managed = build_batch_key(
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        auth_scope="tushare_body_token",
        rate_limit_bucket="ratelimit:tushare",
        http_visibility="managed_http",
        params={"ts_code": "600519.SH"},
    )
    sdk_internal = build_batch_key(
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        auth_scope="tushare_body_token",
        rate_limit_bucket="ratelimit:tushare",
        http_visibility="sdk_internal_unknown",
        params={"ts_code": "600519.SH"},
    )

    assert managed != sdk_internal
