from __future__ import annotations

import json
from importlib import resources
from typing import Any, Iterable

from .models import OfficialEndpoint, endpoint, no_batch, output_contract


def load_generated_endpoints(
    resource_name: str,
    *,
    provider_id: str,
    source_type: str,
    auth: str,
    rate_limit_bucket: str,
    markets: Iterable[str] = (),
    skip_paths: Iterable[str] = (),
) -> tuple[OfficialEndpoint, ...]:
    skip = {path.strip() for path in skip_paths if path.strip()}
    payload = resources.files("claw_trade.data_gateway.official_catalog.generated").joinpath(resource_name).read_text(encoding="utf-8")
    specs = json.loads(payload)

    endpoints = []
    seen_paths = set(skip)
    for spec in specs:
        path = str(spec.get("path") or "").strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        response_shape: dict[str, Any] = {
            "summary": spec.get("summary") or "",
            "description": spec.get("description") or "",
            "source": spec.get("source") or "",
        }
        for evidence_key in ("snapshot_source", "observed_from_url", "sdk_module"):
            if spec.get(evidence_key):
                response_shape[evidence_key] = spec[evidence_key]
        doc_ref = str(spec.get("official_doc_ref") or spec.get("source") or "").strip()
        if doc_ref:
            response_shape.setdefault("observed_from_url", doc_ref)
        endpoint_markets = tuple(
            str(market).strip()
            for market in (
                *(markets or ()),
                *_as_tuple(spec.get("markets")),
                *_as_tuple(spec.get("market")),
            )
            if str(market).strip()
        )
        outputs = _explicit_outputs(spec) or _exact_catalog_outputs(spec, source_type=source_type)
        if endpoint_markets:
            response_shape["markets"] = tuple(dict.fromkeys(endpoint_markets))
        asset_classes = spec.get("asset_classes")
        if isinstance(asset_classes, list):
            response_shape["asset_classes"] = tuple(str(item) for item in asset_classes if str(item).strip())
        response_fields = spec.get("response_fields")
        if isinstance(response_fields, list):
            response_shape["response_fields"] = tuple(str(field) for field in response_fields if str(field).strip())
        if outputs:
            response_shape["outputs"] = outputs
        endpoints.append(
            endpoint(
                provider_id=provider_id,
                source_type=source_type,
                endpoint_id=str(spec["endpoint_id"]),
                official_path_or_api_name=path,
                method=str(spec.get("method") or "GET").upper(),  # type: ignore[arg-type]
                required_params=tuple(spec.get("required_params") or ()),
                optional_params=tuple(spec.get("optional_params") or ()),
                auth=auth,
                rate_limit_bucket=rate_limit_bucket,
                batch_policy=no_batch(),
                parser_status=_parser_status(spec=spec, outputs=outputs),  # type: ignore[arg-type]
                official_doc_ref=doc_ref,
                request_template={"path": path},
                response_shape=response_shape,
            )
        )
    return tuple(endpoints)


def _explicit_outputs(spec: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        output_contract(
            market=str(raw.get("market") or ""),
            data_type=str(raw.get("data_type") or ""),
            granularity=tuple(raw.get("granularity") or ()),
            fields=tuple(raw.get("fields") or ()),
            public_api_ids=tuple(raw.get("public_api_ids") or ()),
            priority_rank=int(raw.get("priority_rank") or 90),
        )
        for raw in spec.get("outputs", ())
        if raw.get("market") and raw.get("data_type") and raw.get("granularity")
    )


def _exact_catalog_outputs(spec: dict[str, Any], *, source_type: str) -> tuple[dict[str, Any], ...]:
    endpoint_id = str(spec.get("endpoint_id") or "")
    path = str(spec.get("path") or "")
    if source_type == "tushare":
        return _tushare_exact_outputs(endpoint_id)
    if source_type == "coinglass":
        return _coinglass_exact_outputs(endpoint_id, path=path)
    if source_type == "finnhub":
        return _finnhub_exact_outputs(endpoint_id, path=path)
    if source_type == "fred":
        return _fred_exact_outputs(endpoint_id, path=path)
    if source_type == "glassnode":
        return _glassnode_exact_outputs(endpoint_id)
    if source_type == "coingecko_pro":
        return _coingecko_exact_outputs(endpoint_id, path=path)
    return ()


def _tushare_exact_outputs(endpoint_id: str) -> tuple[dict[str, Any], ...]:
    if endpoint_id in {
        "tushare.raw_rt_k",
        "tushare.raw_tdx_daily",
        "tushare.raw_ths_daily",
    }:
        return (
            output_contract(
                market="CN_A",
                data_type="daily_bar",
                granularity=("daily", "realtime"),
                fields=("open", "high", "low", "close", "volume", "price", "timestamp", "symbol_id"),
                public_api_ids=("market_price", "quote_snapshot"),
                priority_rank=8,
            ),
        )
    if endpoint_id in {
        "tushare.raw_rt_min",
        "tushare.raw_rt_min_daily",
    }:
        return (
            output_contract(
                market="CN_A",
                data_type="intraday_bar",
                granularity=("intraday", "1m", "realtime"),
                fields=("open", "high", "low", "close", "volume", "price", "timestamp", "symbol_id"),
                public_api_ids=("market_price", "quote_snapshot"),
                priority_rank=8,
            ),
        )
    if endpoint_id in {
        "tushare.raw_stk_auction",
        "tushare.raw_stk_auction_c",
        "tushare.raw_stk_auction_o",
    }:
        return (
            output_contract(
                market="CN_A",
                data_type="order_book_snapshot",
                granularity="realtime",
                fields=("bid_price", "bid_size", "ask_price", "ask_size", "price", "volume", "timestamp", "symbol_id"),
                public_api_ids=("auction_order_book_snapshot", "order_book_snapshot"),
                priority_rank=8,
            ),
        )
    if endpoint_id in {"tushare.raw_news", "tushare.raw_major_news", "tushare.raw_cctv_news", "tushare.raw_npr"}:
        return (
            output_contract(
                market="CN_A",
                data_type="company_news",
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
                public_api_ids=("company_news",),
                priority_rank=12,
            ),
            output_contract(
                market="CN_A",
                data_type="macro_news",
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url"),
                public_api_ids=("macro_news",),
                priority_rank=12,
            ),
        )
    if endpoint_id == "tushare.raw_research_report":
        return (
            output_contract(
                market="CN_A",
                data_type="company_news",
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
                public_api_ids=("research_report", "company_news"),
                priority_rank=12,
            ),
        )
    return ()


def _coinglass_exact_outputs(endpoint_id: str, *, path: str) -> tuple[dict[str, Any], ...]:
    if endpoint_id == "coinglass.raw_article_list":
        return (
            _event_output(
                market="CRYPTO",
                data_type="company_news",
                public_api_ids=("company_news",),
                priority_rank=12,
                fields=("event_id", "event_type", "title", "published_at", "source", "summary", "symbol_id"),
            ),
            _event_output(
                market="CRYPTO",
                data_type="macro_news",
                public_api_ids=("macro_news",),
                priority_rank=12,
                fields=("event_id", "event_type", "title", "published_at", "source", "summary", "symbol_id"),
            ),
        )
    if endpoint_id == "coinglass.raw_calendar_economic_data":
        return (
            _event_output(
                market="CRYPTO",
                data_type="event_calendar",
                public_api_ids=("event_calendar",),
                priority_rank=12,
                fields=("event_id", "event_type", "title", "event_date", "source", "symbol_id"),
            ),
            _event_output(
                market="CRYPTO",
                data_type="macro_news",
                public_api_ids=("macro_news",),
                priority_rank=12,
                fields=("event_id", "event_type", "title", "published_at", "source", "symbol_id"),
            ),
        )
    if endpoint_id in _COINGLASS_LONG_SHORT_ENDPOINTS:
        priority_rank = 10 if endpoint_id in _COINGLASS_PRIMARY_LONG_SHORT_ENDPOINTS else 14
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("long_short_ratio", "metric", "timestamp", "symbol_id"),
                public_api_ids=("long_short_ratio",),
                priority_rank=priority_rank,
            ),
        )
    if endpoint_id in _COINGLASS_FUNDING_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("funding_rate", "funding_rate_unit", "timestamp", "symbol_id"),
                public_api_ids=("funding_rate",),
                priority_rank=10,
            ),
        )
    if endpoint_id in _COINGLASS_OPEN_INTEREST_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily", "realtime"),
                fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
                public_api_ids=("open_interest",),
                priority_rank=10,
            ),
        )
    if endpoint_id in _COINGLASS_LIQUIDATION_MAP_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("liquidation_price", "liquidation_size", "timestamp", "symbol_id"),
                public_api_ids=("liquidation_map", "liquidation_heatmap"),
                priority_rank=11,
            ),
        )
    if endpoint_id in _COINGLASS_LIQUIDATION_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id"),
                public_api_ids=("liquidation",),
                priority_rank=12,
            ),
        )
    if endpoint_id in _COINGLASS_CVD_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("cvd", "taker_buy_volume", "taker_sell_volume", "timestamp", "symbol_id"),
                public_api_ids=("cvd",),
                priority_rank=11,
            ),
        )
    if endpoint_id in _COINGLASS_NETFLOW_ENDPOINTS:
        data_type = "crypto_derivative_metric" if "/futures/" in path or ".raw_futures_" in endpoint_id else "crypto_onchain_metric"
        return (
            output_contract(
                market="CRYPTO",
                data_type=data_type,
                granularity="realtime",
                fields=("netflow", "net_inflow", "net_inflow_unit", "timestamp", "symbol_id"),
                public_api_ids=("exchange_netflow",),
                priority_rank=16,
            ),
        )
    if endpoint_id in _COINGLASS_PRICE_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="daily_bar",
                granularity="daily",
                fields=("open", "high", "low", "close", "volume", "timestamp", "symbol_id"),
                public_api_ids=("market_price",),
                priority_rank=35,
            ),
            output_contract(
                market="CRYPTO",
                data_type="intraday_bar",
                granularity=("1h", "intraday"),
                fields=("open", "high", "low", "close", "volume", "timestamp", "symbol_id"),
                public_api_ids=("market_price",),
                priority_rank=35,
            ),
        )
    if endpoint_id in _COINGLASS_ORDER_BOOK_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="order_book_snapshot",
                granularity=("1h", "realtime"),
                fields=("bids_usd", "bids_quantity", "asks_usd", "asks_quantity", "timestamp", "symbol_id"),
                public_api_ids=("order_book_snapshot",),
                priority_rank=20,
            ),
        )
    if endpoint_id in _COINGLASS_OPTIONS_OPEN_INTEREST_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("options_open_interest", "timestamp", "symbol_id"),
                public_api_ids=("options_open_interest",),
                priority_rank=11,
            ),
        )
    if endpoint_id in _COINGLASS_OPTIONS_VOLUME_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("options_volume", "timestamp", "symbol_id"),
                public_api_ids=("options_volume",),
                priority_rank=11,
            ),
        )
    if endpoint_id in _COINGLASS_BORROW_RATE_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("borrow_interest_rate", "borrow_interest_rate_unit", "timestamp", "symbol_id"),
                public_api_ids=("borrow_interest_rate",),
                priority_rank=18,
            ),
        )
    if endpoint_id in _COINGLASS_AHR999_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity="daily",
                fields=("ahr999", "value", "timestamp", "symbol_id"),
                public_api_ids=("ahr999",),
                priority_rank=18,
            ),
        )
    metric_field = _COINGLASS_ONCHAIN_INDEX_METRIC_FIELDS.get(endpoint_id)
    if metric_field:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity="daily",
                fields=("metric", metric_field, "value", "timestamp", "symbol_id"),
                public_api_ids=("onchain_metric",),
                priority_rank=18,
            ),
        )
    if endpoint_id in _COINGLASS_ETF_FLOW_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity="daily",
                fields=("etf_flow_usd", "price", "timestamp", "symbol_id"),
                public_api_ids=("etf_flow",),
                priority_rank=11,
            ),
        )
    if endpoint_id == "coinglass.raw_index_fear_greed_history":
        return (
            output_contract(
                market="CRYPTO",
                data_type="social_signal",
                granularity="event",
                fields=("source", "timestamp", "sentiment", "value", "event_type", "symbol_id"),
                public_api_ids=("social_signal",),
                priority_rank=16,
            ),
        )
    return ()


def _finnhub_exact_outputs(endpoint_id: str, *, path: str) -> tuple[dict[str, Any], ...]:
    if path in {"/news", "/stock/newsroom"} or endpoint_id in {"finnhub.raw_news", "finnhub.raw_stock_newsroom"}:
        return (_event_output(market="US", data_type="macro_news", public_api_ids=("macro_news", "company_news"), priority_rank=14),)
    if path == "/press-releases" or endpoint_id == "finnhub.raw_press_releases":
        return (_event_output(market="US", data_type="company_news", public_api_ids=("company_news",), priority_rank=14),)
    if path in {
        "/calendar/earnings",
        "/calendar/economic",
        "/calendar/ipo",
        "/fda-advisory-committee-calendar",
        "/stock/earnings-call-live",
        "/stock/market-holiday",
    } or "calendar" in endpoint_id:
        return (_event_output(market="US", data_type="event_calendar", public_api_ids=("event_calendar",), priority_rank=14),)
    if "filing" in path or "filings" in endpoint_id:
        return (_event_output(market="US", data_type="official_filing", public_api_ids=("official_filing",), priority_rank=14),)
    if path in {"/ca/isin-change", "/ca/symbol-change"}:
        return (_event_output(market="US", data_type="corporate_action", public_api_ids=("corporate_action", "event_calendar"), priority_rank=16),)
    if path in {"/stock/congressional-trading", "/stock/insider-transactions", "/stock/uspto-patent"}:
        return (_event_output(market="US", data_type="event_calendar", public_api_ids=("event_calendar",), priority_rank=16),)
    if path == "/news-sentiment":
        return (
            output_contract(
                market="US",
                data_type="social_signal",
                granularity="event",
                fields=("event_id", "event_type", "title", "published_at", "source", "summary", "url", "symbol_id", "sentiment"),
                public_api_ids=("social_signal", "company_news"),
                priority_rank=16,
            ),
        )
    return ()


def _fred_exact_outputs(endpoint_id: str, *, path: str) -> tuple[dict[str, Any], ...]:
    if "/release" in path or "/releases" in path:
        return (_event_output(market="US", data_type="event_calendar", public_api_ids=("event_calendar", "macro_news"), priority_rank=14),)
    return ()


def _coingecko_exact_outputs(endpoint_id: str, *, path: str) -> tuple[dict[str, Any], ...]:
    if path == "/news" or endpoint_id == "coingecko_pro.raw_news":
        return (_event_output(market="CRYPTO", data_type="company_news", public_api_ids=("company_news", "macro_news"), priority_rank=16),)
    return ()


def _event_output(
    *,
    market: str,
    data_type: str,
    public_api_ids: tuple[str, ...],
    priority_rank: int,
    fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    fields = fields or (
        ("event_id", "event_type", "title", "event_date", "source", "summary", "url", "symbol_id")
        if data_type == "event_calendar"
        else ("event_id", "event_type", "title", "published_at", "source", "summary", "url", "symbol_id")
    )
    return output_contract(
        market=market,
        data_type=data_type,
        granularity="event",
        fields=fields,
        public_api_ids=public_api_ids,
        priority_rank=priority_rank,
    )


def _glassnode_exact_outputs(endpoint_id: str) -> tuple[dict[str, Any], ...]:
    if endpoint_id in _GLASSNODE_FUNDING_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("funding_rate", "funding_rate_unit", "timestamp", "symbol_id"),
                public_api_ids=("funding_rate",),
                priority_rank=18,
            ),
        )
    if endpoint_id in _GLASSNODE_OPEN_INTEREST_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
                public_api_ids=("open_interest",),
                priority_rank=18,
            ),
        )
    if endpoint_id in _GLASSNODE_LIQUIDATION_MAP_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("liquidation_price", "liquidation_size", "timestamp", "symbol_id"),
                public_api_ids=("liquidation_map", "liquidation_heatmap"),
                priority_rank=18,
            ),
        )
    if endpoint_id in _GLASSNODE_LIQUIDATION_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id"),
                public_api_ids=("liquidation",),
                priority_rank=18,
            ),
        )
    if endpoint_id in _GLASSNODE_CVD_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_derivative_metric",
                granularity=("1h", "daily"),
                fields=("cvd", "value", "timestamp", "symbol_id"),
                public_api_ids=("cvd",),
                priority_rank=18,
            ),
        )
    if endpoint_id in _GLASSNODE_EXCHANGE_NETFLOW_ENDPOINTS:
        return (
            output_contract(
                market="CRYPTO",
                data_type="crypto_onchain_metric",
                granularity=("1h", "daily"),
                fields=("netflow", "net_inflow", "value", "timestamp", "symbol_id"),
                public_api_ids=("exchange_netflow",),
                priority_rank=18,
            ),
        )
    return ()


_COINGLASS_LONG_SHORT_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_global_long_short_account_ratio_history",
        "coinglass.raw_futures_top_long_short_account_ratio_history",
        "coinglass.raw_futures_top_long_short_position_ratio_history",
        "coinglass.raw_hyperliquid_global_long_short_account_ratio_history",
    }
)
_COINGLASS_PRIMARY_LONG_SHORT_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_top_long_short_account_ratio_history",
        "coinglass.raw_futures_top_long_short_position_ratio_history",
    }
)
_COINGLASS_FUNDING_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_funding_rate_history",
        "coinglass.raw_futures_funding_rate_oi_weight_history",
        "coinglass.raw_futures_funding_rate_vol_weight_history",
    }
)
_COINGLASS_OPEN_INTEREST_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_open_interest_aggregated_coin_margin_history",
        "coinglass.raw_futures_open_interest_aggregated_history",
        "coinglass.raw_futures_open_interest_aggregated_stablecoin_history",
        "coinglass.raw_futures_open_interest_history",
    }
)
_COINGLASS_LIQUIDATION_MAP_ENDPOINTS = frozenset()
_COINGLASS_LIQUIDATION_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_liquidation_history",
    }
)
_COINGLASS_CVD_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_aggregated_cvd_history",
        "coinglass.raw_futures_cvd_history",
        "coinglass.raw_spot_aggregated_cvd_history",
        "coinglass.raw_spot_cvd_history",
    }
)
_COINGLASS_NETFLOW_ENDPOINTS = frozenset(
    {
        "coinglass.raw_futures_coin_netflow",
        "coinglass.raw_futures_netflow_list",
        "coinglass.raw_spot_coin_netflow",
        "coinglass.raw_spot_netflow_list",
    }
)
_COINGLASS_PRICE_ENDPOINTS = frozenset()
_COINGLASS_ORDER_BOOK_ENDPOINTS = frozenset()
_COINGLASS_OPTIONS_OPEN_INTEREST_ENDPOINTS = frozenset(
    {
        "coinglass.raw_option_exchange_oi_history",
    }
)
_COINGLASS_OPTIONS_VOLUME_ENDPOINTS = frozenset({"coinglass.raw_option_exchange_vol_history"})
_COINGLASS_BORROW_RATE_ENDPOINTS = frozenset({"coinglass.raw_borrow_interest_rate_history"})
_COINGLASS_AHR999_ENDPOINTS = frozenset({"coinglass.raw_index_ahr999"})
_COINGLASS_ONCHAIN_INDEX_METRIC_FIELDS = {
    "coinglass.raw_index_bitcoin_active_addresses": "active_addresses",
    "coinglass.raw_index_bitcoin_lth_sopr": "lth_sopr",
    "coinglass.raw_index_bitcoin_net_unrealized_profit_loss": "nupl",
    "coinglass.raw_index_bitcoin_sth_sopr": "sth_sopr",
    "coinglass.raw_index_stablecoin_marketcap_history": "stablecoin_market_cap",
}
_COINGLASS_ETF_FLOW_ENDPOINTS = frozenset(
    {
        "coinglass.raw_etf_bitcoin_flow_history",
        "coinglass.raw_etf_ethereum_flow_history",
        "coinglass.raw_etf_hype_flow_history",
        "coinglass.raw_etf_solana_flow_history",
        "coinglass.raw_etf_xrp_flow_history",
        "coinglass.raw_hk_etf_bitcoin_flow_history",
    }
)
_GLASSNODE_FUNDING_ENDPOINTS = frozenset(
    {
        "glassnode.raw_v1_metrics_derivatives_futures_funding_rate_perpetual",
        "glassnode.raw_v1_metrics_derivatives_futures_funding_rate_perpetual_all",
        "glassnode.raw_v1_metrics_derivatives_futures_funding_rate_perpetual_v2",
    }
)
_GLASSNODE_OPEN_INTEREST_ENDPOINTS = frozenset(
    {
        "glassnode.raw_v1_metrics_derivatives_futures_cme_open_interest_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_cash_margin_perpetual_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_cash_margin_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_crypto_margin_perpetual_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_crypto_margin_relative",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_crypto_margin_relative_perpetual",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_crypto_margin_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_latest",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_mcap_ratio",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_perpetual_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_perpetual_sum_all",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_sum",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_sum_all",
        "glassnode.raw_v1_metrics_derivatives_futures_open_interest_volume_ratio",
        "glassnode.raw_v1_metrics_derivatives_options_cme_open_interest_sum",
        "glassnode.raw_v1_metrics_derivatives_options_open_interest_distribution",
        "glassnode.raw_v1_metrics_derivatives_options_open_interest_put_call_ratio",
        "glassnode.raw_v1_metrics_derivatives_options_open_interest_sum",
        "glassnode.raw_v1_metrics_global_open_interest_sum_weekly_equal_index",
        "glassnode.raw_v1_metrics_global_open_interest_sum_weekly_equal_raw",
        "glassnode.raw_v1_metrics_options_ibit_options_open_interest",
        "glassnode.raw_v1_metrics_options_ibit_options_open_interest_by_maturity",
        "glassnode.raw_v1_metrics_options_options_open_interest_by_maturity",
    }
)
_GLASSNODE_LIQUIDATION_MAP_ENDPOINTS = frozenset(
    {
        "glassnode.raw_v1_metrics_derivatives_futures_liquidation_heatmap",
        "glassnode.raw_v1_metrics_derivatives_liquidation_entry_price_heatmap_long",
        "glassnode.raw_v1_metrics_derivatives_liquidation_entry_price_heatmap_net",
        "glassnode.raw_v1_metrics_derivatives_liquidation_entry_price_heatmap_short",
        "glassnode.raw_v1_metrics_derivatives_liquidation_heatmap",
    }
)
_GLASSNODE_LIQUIDATION_ENDPOINTS = frozenset(
    {
        "glassnode.raw_v1_metrics_derivatives_futures_liquidations_mcap_ratio",
        "glassnode.raw_v1_metrics_derivatives_futures_liquidations_open_interest_ratio",
    }
)
_GLASSNODE_CVD_ENDPOINTS = frozenset(
    {
        "glassnode.raw_v1_metrics_derivatives_futures_cvd",
        "glassnode.raw_v1_metrics_derivatives_futures_cvd_perpetual",
        "glassnode.raw_v1_metrics_market_spot_cvd_sum",
    }
)
_GLASSNODE_EXCHANGE_NETFLOW_ENDPOINTS = frozenset(
    {
        "glassnode.raw_v1_metrics_transactions_transfers_volume_exchanges_net",
        "glassnode.raw_v1_metrics_transactions_transfers_volume_exchanges_net_by_size",
    }
)


def _parser_status(*, spec: dict[str, Any], outputs: tuple[dict[str, Any], ...]) -> str:
    explicit = str(spec.get("parser_status") or "").strip()
    if explicit == "raw_only":
        return explicit
    if outputs:
        return "normalized"
    return explicit or "parser_missing"


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


__all__ = ["load_generated_endpoints"]
