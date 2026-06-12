from __future__ import annotations

from .models import ParserStatus, endpoint, no_batch

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
    parser_status: ParserStatus = "normalized",
    doc: str = DOC,
):
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
        parser_status=parser_status,
        official_doc_ref=doc,
        request_template={"path": path},
        response_shape={"data": "CoinGlass v4 response payload"},
    )


ENDPOINTS = (
    _cg(
        "spot_coins_markets",
        "/api/spot/coins-markets",
        optional_params=("symbol",),
        doc="https://docs.coinglass.com/reference/spot-coins-markets.md",
    ),
    _cg(
        "coin_market_data_history",
        "/api/coin/market-data-history",
        required_params=("symbol",),
        optional_params=("start_time", "end_time"),
        doc="https://docs.coinglass.com/reference/coin-market-data-history.md",
    ),
    _cg(
        "futures_open_interest",
        "/api/futures/open-interest/exchange-list",
        required_params=("symbol",),
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
        doc="https://docs.coinglass.com/reference/oi-ohlc-aggregated-history.md",
    ),
    _cg(
        "futures_funding_rate",
        "/api/futures/funding-rate/oi-weight-history",
        required_params=("symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/oi-weight-ohlc-history.md",
    ),
    _cg(
        "futures_long_short_ratio",
        "/api/futures/global-long-short-account-ratio/history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/global-longshort-account-ratio.md",
    ),
    _cg(
        "futures_taker_buy_sell",
        "/api/futures/aggregated-taker-buy-sell-volume/history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/aggregated-taker-buysell-volume-history.md",
    ),
    _cg(
        "futures_liquidation",
        "/api/futures/liquidation/aggregated-history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/aggregated-liquidation-history.md",
    ),
    _cg(
        "futures_liquidation_heatmap",
        "/api/futures/liquidation/aggregated-heatmap/model1",
        required_params=("symbol", "range"),
        doc="https://docs.coinglass.com/reference/liquidation-aggregate-heatmap.md",
    ),
    _cg(
        "options_open_interest",
        "/api/option/exchange-oi-history",
        required_params=("symbol", "range"),
        doc="https://docs.coinglass.com/reference/exchange-open-interest-history.md",
    ),
    _cg(
        "options_volume",
        "/api/option/exchange-vol-history",
        required_params=("symbol", "range"),
        doc="https://docs.coinglass.com/reference/exchange-volume-history.md",
    ),
    _cg(
        "spot_cvd_history",
        "/api/spot/aggregated-cvd/history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "unit", "start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/spot-aggregated-cvd-history.md",
    ),
    _cg(
        "futures_cvd_history",
        "/api/futures/aggregated-cvd/history",
        required_params=("symbol", "interval"),
        optional_params=("exchange_list", "unit", "start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/futures-aggregated-cvd-history.md",
    ),
    _cg(
        "etf_bitcoin_flow_history",
        "/api/etf/bitcoin/flow-history",
        optional_params=("start_time", "end_time"),
        doc="https://docs.coinglass.com/reference/etf-flows-history.md",
    ),
    _cg(
        "etf_ethereum_flow_history",
        "/api/etf/ethereum/flow-history",
        optional_params=("start_time", "end_time"),
        doc="https://docs.coinglass.com/reference/ethereum-etf-flows-history.md",
    ),
    _cg(
        "futures_price_history",
        "/api/futures/price/history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/price-ohlc-history.md",
    ),
    _cg(
        "spot_price_history",
        "/api/spot/price/history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/spot-price-ohlc-history.md",
    ),
    _cg(
        "futures_order_book",
        "/api/futures/orderbook/ask-bids-history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/futures-aggregated-orderbook-history.md",
    ),
    _cg(
        "spot_order_book",
        "/api/spot/orderbook/ask-bids-history",
        required_params=("exchange", "symbol", "interval"),
        optional_params=("start_time", "end_time", "limit"),
        doc="https://docs.coinglass.com/reference/spot-aggregated-history.md",
    ),
    _cg(
        "onchain_exchange_balance",
        "/api/exchange/balance/chart",
        required_params=("symbol",),
        doc="https://docs.coinglass.com/reference/exchange-balance-chart.md",
    ),
    _cg(
        "onchain_whale_transfer",
        "/api/chain/v2/whale-transfer",
        required_params=("symbol",),
        optional_params=("chain", "start_time", "end_time"),
        doc="https://docs.coinglass.com/reference/whale-transfer.md",
    ),
    _cg(
        "spot_coin_netflow",
        "/api/spot/coin/netflow",
        required_params=("symbol",),
        optional_params=("exchange_list",),
        doc="https://docs.coinglass.com/reference/spot-coin-netflow.md",
    ),
    _cg(
        "futures_coin_netflow",
        "/api/futures/coin/netflow",
        required_params=("symbol",),
        optional_params=("exchange_list",),
        doc="https://docs.coinglass.com/reference/futures-coin-netflow.md",
    ),
    _cg(
        "bitcoin_ahr999",
        "/api/index/ahr999",
        doc="https://docs.coinglass.com/reference/ahr999.md",
    ),
)
