from __future__ import annotations

from .models import endpoint, no_batch, symbol_batch

AUTH = "header_x_cg_pro_api_key"
BUCKET = "ratelimit:coingecko_pro"
PROVIDER = "official_api_coingecko_pro"


ENDPOINTS = (
    endpoint(
        provider_id=PROVIDER,
        source_type="coingecko_pro",
        endpoint_id="coingecko_pro.coins_markets",
        official_path_or_api_name="/coins/markets",
        method="GET",
        required_params=("vs_currency",),
        optional_params=("ids", "names", "symbols", "include_tokens", "category", "order", "per_page", "page", "sparkline"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=symbol_batch(max_symbols_per_call=250),
        official_doc_ref="https://docs.coingecko.com/reference/coins-markets",
        request_template={"path": "/coins/markets", "query": {"vs_currency": "usd", "ids": "<coin_ids>"}},
        response_shape={"items": "coin market data rows"},
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="coingecko_pro",
        endpoint_id="coingecko_pro.coins_id",
        official_path_or_api_name="/coins/{id}",
        method="GET",
        required_params=("id",),
        optional_params=("localization", "tickers", "market_data", "community_data", "developer_data", "sparkline"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        parser_status="raw_only",
        official_doc_ref="https://docs.coingecko.com/reference/coins-id",
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="coingecko_pro",
        endpoint_id="coingecko_pro.coins_id_market_chart",
        official_path_or_api_name="/coins/{id}/market_chart",
        method="GET",
        required_params=("id", "vs_currency", "days"),
        optional_params=("interval", "precision"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        parser_status="raw_only",
        official_doc_ref="https://docs.coingecko.com/reference/coins-id-market-chart",
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="coingecko_pro",
        endpoint_id="coingecko_pro.simple_price",
        official_path_or_api_name="/simple/price",
        method="GET",
        required_params=("ids", "vs_currencies"),
        optional_params=("include_market_cap", "include_24hr_vol", "include_24hr_change", "include_last_updated_at"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=symbol_batch(max_symbols_per_call=250),
        parser_status="raw_only",
        official_doc_ref="https://docs.coingecko.com/reference/simple-price",
    ),
)

