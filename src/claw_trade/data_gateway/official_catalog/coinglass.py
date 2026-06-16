from __future__ import annotations

from .generated_loader import load_generated_endpoints
from .models import ParserStatus, endpoint, no_batch, output_contract

DOC = "https://docs.coinglass.com/reference"
AUTH = "coinglass_header"
BUCKET = "ratelimit:coinglass"
PROVIDER = "official_api_coinglass"


def _cg(
    endpoint_id: str,
    path: str,
    *,
    required_params: tuple[str, ...] = (),
    optional_params: tuple[str, ...] = (),
    outputs: tuple[dict[str, object], ...] = (),
    parser_status: ParserStatus | None = None,
    doc: str = DOC,
):
    response_shape: dict[str, object] = {"data": "CoinGlass v4 response payload"}
    if outputs:
        response_shape["outputs"] = outputs
    return endpoint(
        provider_id=PROVIDER,
        source_type="coinglass",
        endpoint_id=f"coinglass.{endpoint_id}",
        official_path_or_api_name=path,
        method="GET",
        required_params=required_params,
        optional_params=optional_params,
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        parser_status=parser_status or ("normalized" if outputs else "parser_missing"),
        official_doc_ref=doc,
        request_template={"path": path},
        response_shape=response_shape,
    )


_CURATED_ENDPOINTS = (
    _cg(
        "spot_coins_markets",
        "/api/spot/coins-markets",
        optional_params=("symbol",),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="valuation_metric",
                public_api_ids=('valuation_metric',),
                granularity="realtime",
                fields=("price", "market_cap", "volume_usd", "timestamp", "symbol_id"),
                priority_rank=8,
            ),
            output_contract(
                market="CRYPTO",
                data_type="quote_snapshot",
                public_api_ids=("realtime_quote", "market_price"),
                granularity="realtime",
                fields=("price", "timestamp", "symbol_id", "volume_usd"),
                priority_rank=8,
            ),
        ),
        doc="https://docs.coinglass.com/reference/spot-coins-markets.md",
    ),
    _cg(
        "coin_market_data_history",
        "/api/coin/market-data-history",
        required_params=("symbol",),
        optional_params=("start_time", "end_time"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="valuation_metric",
                public_api_ids=('valuation_metric',),
                granularity="daily",
                fields=("price", "market_cap", "circulating_supply", "timestamp", "symbol_id"),
                priority_rank=8,
            ),
        ),
        doc="https://docs.coinglass.com/reference/coin-market-data-history.md",
    ),
    _cg(
        "futures_open_interest",
        "/api/futures/open-interest/exchange-list",
        required_params=("symbol",),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="realtime",
                fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
                public_api_ids=("open_interest",),
                priority_rank=10,
            ),
        ),
        doc="https://docs.coinglass.com/reference/oi-exchange-list.md",
    ),
    _cg(
        "futures_pairs_markets",
        "/api/futures/pairs-markets",
        required_params=("exchange", "symbol"),
        doc="https://docs.coinglass.com/reference/pairs-markets.md",
    ),
    _cg(
        "futures_open_interest_aggregated_history",
        "/api/futures/open-interest/aggregated-history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "unit", "start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
                public_api_ids=("open_interest",),
                priority_rank=9,
            ),
        ),
        doc="https://docs.coinglass.com/reference/oi-ohlc-aggregated-history.md",
    ),
    _cg(
        "futures_funding_rate",
        "/api/futures/funding-rate/oi-weight-history",
        required_params=("symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        parser_status="normalized",
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("funding_rate", "funding_rate_unit", "timestamp", "symbol_id"),
                public_api_ids=("funding_rate",),
                priority_rank=10,
            ),
        ),
        doc="https://docs.coinglass.com/reference/oi-weight-ohlc-history.md",
    ),
    _cg(
        "futures_long_short_ratio",
        "/api/futures/global-long-short-account-ratio/history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("long_short_ratio", "metric", "timestamp", "symbol_id"),
                public_api_ids=("long_short_ratio",),
                priority_rank=10,
            ),
        ),
        doc="https://docs.coinglass.com/reference/global-longshort-account-ratio.md",
    ),
    _cg(
        "futures_taker_buy_sell",
        "/api/futures/aggregated-taker-buy-sell-volume/history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
                public_api_ids=("active_buy_sell",),
                priority_rank=12,
            ),
        ),
        doc="https://docs.coinglass.com/reference/aggregated-taker-buysell-volume-history.md",
    ),
    _cg(
        "futures_liquidation",
        "/api/futures/liquidation/aggregated-history",
        required_params=("exchange_list", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("long_liquidation", "short_liquidation", "timestamp", "symbol_id"),
                public_api_ids=("liquidation",),
                priority_rank=12,
            ),
        ),
        doc="https://docs.coinglass.com/reference/aggregated-liquidation-history.md",
    ),
    _cg(
        "futures_liquidation_heatmap",
        "/api/futures/liquidation/aggregated-heatmap/model1",
        required_params=("symbol", "range"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("liquidation_price", "liquidation_size", "timestamp", "symbol_id"),
                public_api_ids=("liquidation_map", "liquidation_heatmap"),
                priority_rank=11,
            ),
        ),
        doc="https://docs.coinglass.com/reference/liquidation-aggregate-heatmap.md",
    ),
    _cg(
        "options_open_interest",
        "/api/option/exchange-oi-history",
        required_params=("symbol", "unit", "range"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("options_open_interest", "timestamp", "symbol_id"),
                public_api_ids=("options_open_interest",),
                priority_rank=11,
            ),
        ),
        doc="https://docs.coinglass.com/reference/exchange-open-interest-history.md",
    ),
    _cg(
        "options_volume",
        "/api/option/exchange-vol-history",
        required_params=("symbol", "unit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("options_volume", "timestamp", "symbol_id"),
                public_api_ids=("options_volume",),
                priority_rank=11,
            ),
        ),
        doc="https://docs.coinglass.com/reference/exchange-volume-history.md",
    ),
    _cg(
        "spot_cvd_history",
        "/api/spot/aggregated-cvd/history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "unit", "start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("cvd", "taker_buy_volume", "taker_sell_volume", "timestamp", "symbol_id"),
                public_api_ids=("cvd",),
                priority_rank=11,
            ),
        ),
        doc="https://docs.coinglass.com/reference/spot-aggregated-cvd-history.md",
    ),
    _cg(
        "futures_cvd_history",
        "/api/futures/aggregated-cvd/history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "unit", "start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="1h",
                fields=("cvd", "taker_buy_volume", "taker_sell_volume", "timestamp", "symbol_id"),
                public_api_ids=("cvd",),
                priority_rank=10,
            ),
        ),
        doc="https://docs.coinglass.com/reference/futures-aggregated-cvd-history.md",
    ),
    _cg(
        "etf_bitcoin_flow_history",
        "/api/etf/bitcoin/flow-history",
        optional_params=("start_time", "end_time"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="daily",
                fields=("etf_flow_usd", "price", "timestamp", "symbol_id"),
                public_api_ids=("etf_flow",),
                priority_rank=11,
            ),
        ),
        doc="https://docs.coinglass.com/reference/etf-flows-history.md",
    ),
    _cg(
        "etf_ethereum_flow_history",
        "/api/etf/ethereum/flow-history",
        optional_params=("start_time", "end_time"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="daily",
                fields=("etf_flow_usd", "price", "timestamp", "symbol_id"),
                public_api_ids=("etf_flow",),
                priority_rank=11,
            ),
        ),
        doc="https://docs.coinglass.com/reference/ethereum-etf-flows-history.md",
    ),
    _cg(
        "futures_price_history",
        "/api/futures/price/history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="daily_bar",
                public_api_ids=('daily_bar', 'market_price'),
                granularity="daily",
                fields=("open", "high", "low", "close", "volume", "volume_usd"),
                priority_rank=35,
            ),
            output_contract(
                market="CRYPTO",
                data_type="intraday_bar",
                public_api_ids=('intraday_bar', 'market_price'),
                granularity="1h",
                fields=("open", "high", "low", "close", "volume", "volume_usd"),
                priority_rank=35,
            ),
        ),
        doc="https://docs.coinglass.com/reference/price-ohlc-history.md",
    ),
    _cg(
        "spot_price_history",
        "/api/spot/price/history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="daily_bar",
                public_api_ids=('daily_bar', 'market_price'),
                granularity="daily",
                fields=("open", "high", "low", "close", "volume", "volume_usd"),
                priority_rank=35,
            ),
            output_contract(
                market="CRYPTO",
                data_type="intraday_bar",
                public_api_ids=('intraday_bar', 'market_price'),
                granularity="1h",
                fields=("open", "high", "low", "close", "volume", "volume_usd"),
                priority_rank=35,
            ),
        ),
        doc="https://docs.coinglass.com/reference/spot-price-ohlc-history.md",
    ),
    _cg(
        "futures_order_book",
        "/api/futures/orderbook/ask-bids-history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="order_book_snapshot",
                granularity=("hourly", "intraday"),
                fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
                public_api_ids=("order_book",),
                priority_rank=10,
            ),
        ),
        doc="https://docs.coinglass.com/reference/futures-aggregated-orderbook-history.md",
    ),
    _cg(
        "spot_order_book",
        "/api/spot/orderbook/ask-bids-history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="order_book_snapshot",
                granularity=("hourly", "intraday"),
                fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
                public_api_ids=("order_book",),
                priority_rank=20,
            ),
        ),
        doc="https://docs.coinglass.com/reference/spot-aggregated-history.md",
    ),
    _cg(
        "onchain_exchange_balance",
        "/api/exchange/balance/chart",
        required_params=("symbol",),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity="daily",
                fields=("metric", "exchange_balance", "balance", "value", "timestamp", "symbol_id"),
                public_api_ids=("exchange_balance", "onchain_metric"),
                priority_rank=12,
            ),
        ),
        doc="https://docs.coinglass.com/reference/exchange-balance-chart.md",
    ),
    _cg(
        "onchain_whale_transfer",
        "/api/chain/v2/whale-transfer",
        required_params=("symbol",),
        optional_params=("chain", "start_time", "end_time"),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity="event",
                fields=("whale_transfer", "value", "timestamp", "chain"),
                public_api_ids=("whale_transfer", "onchain_metric"),
                priority_rank=14,
            ),
        ),
        doc="https://docs.coinglass.com/reference/whale-transfer.md",
    ),
    _cg(
        "spot_coin_netflow",
        "/api/spot/coin/netflow",
        required_params=("symbol",),
        optional_params=("exchange_list",),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity="realtime",
                fields=("netflow", "netflow_unit", "net_inflow", "net_inflow_unit", "value", "value_unit", "timestamp", "symbol_id"),
                public_api_ids=("exchange_netflow",),
                priority_rank=16,
            ),
        ),
        doc="https://docs.coinglass.com/reference/spot-coin-netflow.md",
    ),
    _cg(
        "futures_coin_netflow",
        "/api/futures/coin/netflow",
        required_params=("symbol",),
        optional_params=("exchange_list",),
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="realtime",
                fields=("netflow", "net_inflow", "net_inflow_unit", "timestamp", "symbol_id"),
                public_api_ids=("exchange_netflow",),
                priority_rank=16,
            ),
        ),
        doc="https://docs.coinglass.com/reference/futures-coin-netflow.md",
    ),
    _cg(
        "bitcoin_ahr999",
        "/api/index/ahr999",
        outputs=(
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity="daily",
                fields=("ahr999", "value", "timestamp", "symbol_id"),
                public_api_ids=("ahr999",),
                priority_rank=18,
            ),
        ),
        doc="https://docs.coinglass.com/reference/ahr999.md",
    ),
)

ENDPOINTS = (
    *_CURATED_ENDPOINTS,
    *load_generated_endpoints(
        "coinglass_v4_rest.json",
        provider_id=PROVIDER,
        source_type="coinglass",
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        markets=("CRYPTO",),
        skip_paths=(endpoint.official_path_or_api_name for endpoint in _CURATED_ENDPOINTS),
    ),
)
