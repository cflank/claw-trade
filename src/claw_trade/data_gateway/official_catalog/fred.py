from __future__ import annotations

from .models import endpoint, no_batch


ENDPOINTS = (
    endpoint(
        provider_id="official_api_fred",
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
        auth="query_api_key",
        rate_limit_bucket="ratelimit:fred",
        batch_policy=no_batch(),
        official_doc_ref="https://fred.stlouisfed.org/docs/api/fred/series_observations.html",
        request_template={"path": "/fred/series/observations", "query": {"series_id": "<series_id>", "file_type": "json"}},
        response_shape={"observations": "date/value economic series rows"},
    ),
)

