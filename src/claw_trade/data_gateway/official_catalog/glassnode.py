from __future__ import annotations

from .models import endpoint, no_batch

AUTH = "query_api_key"
BUCKET = "ratelimit:glassnode"
DOC = "https://docs.glassnode.com/basic-api/endpoints"
PROVIDER = "official_api_glassnode"


def _gn(endpoint_id: str, path: str, metric: str):
    return endpoint(
        provider_id=PROVIDER,
        source_type="glassnode",
        endpoint_id=f"glassnode.{endpoint_id}",
        official_path_or_api_name=path,
        method="GET",
        required_params=("a",),
        optional_params=("s", "u", "i", "f", "timestamp_format", "api_key"),
        auth=AUTH,
        rate_limit_bucket=BUCKET,
        batch_policy=no_batch(),
        official_doc_ref=DOC,
        request_template={"path": path, "query": {"a": "<asset>"}},
        response_shape={"metric": metric, "rows": "time/value points"},
    )


ENDPOINTS = (
    _gn("addresses_active_count", "/v1/metrics/addresses/active_count", "active_addresses"),
    _gn("indicators_mvrv_account_based", "/v1/metrics/indicators/mvrv_account_based", "mvrv"),
    _gn("indicators_sopr_less_155", "/v1/metrics/indicators/sopr_less_155", "sth_sopr"),
    _gn("indicators_sopr_more_155", "/v1/metrics/indicators/sopr_more_155", "lth_sopr"),
    _gn("indicators_nupl", "/v1/metrics/indicators/net_unrealized_profit_loss", "nupl"),
)

