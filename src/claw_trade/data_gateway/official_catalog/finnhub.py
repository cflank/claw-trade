from __future__ import annotations

from .models import endpoint, no_batch

DOC = "https://finnhub.io/docs/api"
AUTH = "query_token"
BUCKET = "ratelimit:finnhub"
PROVIDER = "official_api_finnhub"


ENDPOINTS = (
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
        response_shape={"fields": ("c", "d", "dp", "h", "l", "o", "pc", "t")},
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
    ),
)

