from __future__ import annotations

from .generated_loader import load_generated_endpoints
from .models import endpoint, no_batch, output_contract

AUTH = "query_api_key"
BUCKET = "ratelimit:fred"
PROVIDER = "official_api_fred"

_CRYPTO_MACRO_SERIES = (
    ("fred.series_fedfunds", "FEDFUNDS", "Effective Federal Funds Rate"),
    ("fred.series_dgs10", "DGS10", "10-Year Treasury Constant Maturity Rate"),
    ("fred.series_walcl", "WALCL", "Federal Reserve total assets"),
    ("fred.series_m2sl", "M2SL", "M2 money stock"),
    ("fred.series_cpiaucsl", "CPIAUCSL", "Consumer Price Index"),
)


def _series_endpoint(endpoint_id: str, series_id: str, summary: str):
    return endpoint(
        provider_id=PROVIDER,
        source_type="fred",
        endpoint_id=endpoint_id,
        official_path_or_api_name="/fred/series/observations",
        method="GET",
        required_params=("series_id",),
        optional_params=("observation_start", "observation_end", "file_type"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        official_doc_ref="https://fred.stlouisfed.org/docs/api/fred/series_observations.html",
        request_template={"path": "/fred/series/observations", "query": {"series_id": series_id, "file_type": "json"}},
        response_shape={
            "observations": f"{summary} rows",
            "outputs": (
                output_contract(
                    market="CRYPTO",
                    data_type="macro_series",
                    public_api_ids=("macro_series", "macro_data"),
                    granularity=("daily", "monthly"),
                    fields=("series_id", "date", "value", "unit", "region"),
                    priority_rank=5,
                ),
                output_contract(
                    market="US",
                    data_type="macro_series",
                    public_api_ids=("macro_series", "macro_data"),
                    granularity=("daily", "monthly"),
                    fields=("series_id", "date", "value", "unit", "region"),
                    priority_rank=5,
                ),
            ),
        },
    )


_CURATED_ENDPOINTS = (
    *(_series_endpoint(endpoint_id, series_id, summary) for endpoint_id, series_id, summary in _CRYPTO_MACRO_SERIES),
    endpoint(
        provider_id=PROVIDER,
        source_type="fred",
        endpoint_id="fred.series_observations",
        official_path_or_api_name="/fred/series/observations",
        method="GET",
        required_params=("series_id",),
        optional_params=(
            "realtime_start",
            "realtime_end",
            "limit",
            "offset",
            "sort_order",
            "observation_start",
            "observation_end",
            "units",
            "frequency",
            "aggregation_method",
            "output_type",
            "vintage_dates",
            "file_type",
        ),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        official_doc_ref="https://fred.stlouisfed.org/docs/api/fred/series_observations.html",
        request_template={"path": "/fred/series/observations", "query": {"series_id": "<series_id>", "file_type": "json"}},
        response_shape={
            "observations": "date/value economic series rows",
            "outputs": (
                output_contract(
                    market="US",
                    data_type="macro_series",
                    public_api_ids=('macro_series', 'macro_data'),
                    granularity=("daily", "monthly", "quarterly"),
                    fields=("series_id", "date", "value", "unit", "region"),
                    priority_rank=5,
                ),
            ),
        },
    ),
)

ENDPOINTS = (
    *_CURATED_ENDPOINTS,
    *load_generated_endpoints(
        "fred_api_docs.json",
        provider_id=PROVIDER,
        source_type="fred",
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        markets=("US",),
        skip_paths=(endpoint.official_path_or_api_name for endpoint in _CURATED_ENDPOINTS),
    ),
)
