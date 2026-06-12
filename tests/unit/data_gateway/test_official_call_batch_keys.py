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
