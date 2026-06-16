from __future__ import annotations

from .generated_loader import load_generated_endpoints
from .models import endpoint, no_batch, output_contract, symbol_batch

AUTH = "header_x_cg_pro_api_key"
BUCKET = "ratelimit:coingecko_pro"
PROVIDER = "official_api_coingecko_pro"


_CURATED_ENDPOINTS = (
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
        response_shape={
            "items": "coin market data rows",
            "outputs": (
                output_contract(
                    market="CRYPTO",
                    data_type="valuation_metric",
                    public_api_ids=('valuation_metric',),
                    granularity="realtime",
                    fields=("price", "market_cap", "fdv", "circulating_supply", "total_supply", "max_supply", "volume"),
                    priority_rank=20,
                ),
            ),
        },
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
        response_shape={
            "outputs": (
                output_contract(
                    market="CRYPTO",
                    data_type="company_profile",
                    public_api_ids=('company_profile',),
                    granularity="event",
                    fields=("name", "symbol", "asset_platform", "description", "homepage", "market_cap_rank", "circulating_supply", "total_supply", "max_supply", "supply_unit"),
                    priority_rank=20,
                ),
                output_contract(
                    market="CRYPTO",
                    data_type="valuation_metric",
                    public_api_ids=('valuation_metric',),
                    granularity="realtime",
                    fields=("price", "market_cap", "fdv", "circulating_supply", "total_supply", "max_supply", "volume"),
                    priority_rank=20,
                ),
            )
        },
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
        response_shape={
            "outputs": (
                output_contract(
                    market="CRYPTO",
                    data_type="valuation_metric",
                    public_api_ids=('valuation_metric',),
                    granularity="daily",
                    fields=("price", "market_cap", "volume", "timestamp", "symbol_id"),
                    priority_rank=20,
                ),
            )
        },
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
        response_shape={
            "outputs": (
                output_contract(
                    market="CRYPTO",
                    data_type="valuation_metric",
                    public_api_ids=('valuation_metric',),
                    granularity="realtime",
                    fields=("price", "market_cap", "volume", "timestamp", "symbol_id"),
                    priority_rank=20,
                ),
            )
        },
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="coingecko_pro",
        endpoint_id="coingecko_pro.derivatives",
        official_path_or_api_name="/derivatives",
        method="GET",
        required_params=(),
        optional_params=(),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        parser_status="parser_missing",
        official_doc_ref="https://docs.coingecko.com/reference#derivatives",
        response_shape={
            "markets": ("CRYPTO",),
            "granularity": ("realtime",),
            "asset_classes": ("crypto",),
            "summary": "Derivatives tickers list with funding rate, open interest, basis, spread, volume and price facts.",
            "description": "Official CoinGecko Pro derivatives endpoint for crypto derivatives market metrics.",
        },
    ),
)

ENDPOINTS = (
    *_CURATED_ENDPOINTS,
    *load_generated_endpoints(
        "coingecko_pro_oas.json",
        provider_id=PROVIDER,
        source_type="coingecko_pro",
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        markets=("CRYPTO",),
        skip_paths=(
            endpoint.official_path_or_api_name
            for endpoint in _CURATED_ENDPOINTS
            if endpoint.endpoint_id != "coingecko_pro.derivatives"
        ),
    ),
)
