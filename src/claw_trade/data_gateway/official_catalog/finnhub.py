from __future__ import annotations

from .generated_loader import load_generated_endpoints
from .models import endpoint, no_batch, output_contract

DOC = "https://finnhub.io/docs/api"
AUTH = "query_token"
BUCKET = "ratelimit:finnhub"
PROVIDER = "official_api_finnhub"


def _shape(*outputs: dict[str, object], fields: tuple[str, ...] = ()) -> dict[str, object]:
    value: dict[str, object] = {}
    if fields:
        value["fields"] = fields
    if outputs:
        value["outputs"] = outputs
    return value


_CURATED_ENDPOINTS = (
    endpoint(
        provider_id=PROVIDER,
        source_type="finnhub",
        endpoint_id="finnhub.quote",
        official_path_or_api_name="/quote",
        method="GET",
        required_params=("symbol",),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        official_doc_ref="https://finnhub.io/docs/api/quote",
        request_template={"path": "/quote", "query": {"symbol": "<symbol>"}},
        response_shape=_shape(
            output_contract(
                market="US",
                data_type="quote_snapshot",
                public_api_ids=('quote_snapshot', 'market_price'),
                granularity="realtime",
                fields=("price", "change", "change_pct", "timestamp", "symbol_id"),
                priority_rank=14,
            ),
            fields=("c", "d", "dp", "h", "l", "o", "pc", "t"),
        ),
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="finnhub",
        endpoint_id="finnhub.stock_candle",
        official_path_or_api_name="/stock/candle",
        method="GET",
        required_params=("symbol", "resolution", "from", "to"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        official_doc_ref="https://finnhub.io/docs/api/stock-candles",
        response_shape=_shape(
            output_contract(
                market="US",
                data_type="daily_bar",
                public_api_ids=('market_price',),
                granularity="daily",
                fields=("date", "open", "high", "low", "close", "volume"),
                priority_rank=12,
            ),
            output_contract(
                market="US",
                data_type="intraday_bar",
                public_api_ids=('market_price',),
                granularity=("intraday", "hourly"),
                fields=("timestamp", "open", "high", "low", "close", "volume"),
                priority_rank=12,
            ),
        ),
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="finnhub",
        endpoint_id="finnhub.company_news",
        official_path_or_api_name="/company-news",
        method="GET",
        required_params=("symbol", "from", "to"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        official_doc_ref="https://finnhub.io/docs/api/company-news",
        response_shape=_shape(
            output_contract(
                market="US",
                data_type="company_news",
                public_api_ids=('company_news',),
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
                priority_rank=12,
            ),
        ),
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="finnhub",
        endpoint_id="finnhub.stock_metric",
        official_path_or_api_name="/stock/metric",
        method="GET",
        required_params=("symbol", "metric"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        official_doc_ref="https://finnhub.io/docs/api/company-basic-financials",
        response_shape=_shape(
            output_contract(
                market="US",
                data_type="financial_metric",
                public_api_ids=('financial_metric',),
                granularity=("quarterly", "realtime"),
                fields=("roe", "roa", "profit_margin", "eps", "revenue_growth"),
                priority_rank=16,
            ),
            output_contract(
                market="US",
                data_type="valuation_metric",
                public_api_ids=('valuation_metric',),
                granularity="realtime",
                fields=("pe", "pb", "ps", "market_cap"),
                priority_rank=16,
            ),
        ),
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="finnhub",
        endpoint_id="finnhub.stock_filings",
        official_path_or_api_name="/stock/filings",
        method="GET",
        required_params=("symbol",),
        optional_params=("from", "to"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        official_doc_ref="https://finnhub.io/docs/api/filings",
        response_shape=_shape(
            output_contract(
                market="US",
                data_type="official_filing",
                public_api_ids=('official_filing',),
                granularity="event",
                fields=("title", "published_at", "source", "url", "symbol_id"),
                priority_rank=12,
            ),
        ),
    ),
    endpoint(
        provider_id=PROVIDER,
        source_type="finnhub",
        endpoint_id="finnhub.company_profile2",
        official_path_or_api_name="/stock/profile2",
        method="GET",
        required_params=("symbol",),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        parser_status="raw_only",
        official_doc_ref=DOC,
        response_shape=_shape(
            output_contract(
                market="US",
                data_type="company_profile",
                public_api_ids=('company_profile',),
                granularity="event",
                fields=("name", "industry", "exchange", "currency", "symbol_id"),
                priority_rank=16,
            ),
        ),
    ),
)

ENDPOINTS = (
    *_CURATED_ENDPOINTS,
    *load_generated_endpoints(
        "finnhub_doc_schema.json",
        provider_id=PROVIDER,
        source_type="finnhub",
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        markets=("US",),
        skip_paths=(endpoint.official_path_or_api_name for endpoint in _CURATED_ENDPOINTS),
    ),
)
