from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from claw_trade.data_gateway.models import EndpointBatchPolicy, HttpVisibility
from claw_trade.data_gateway.public_api import public_api_ids_for_output

FORBIDDEN_BUSINESS_SCOPE_KEYS = frozenset(
    {
        "allowed_worker",
        "allowed_domain",
        "allowed_report_section",
        "domain",
        "report_section",
        "provider_scope",
        "only_for_market",
        "only_for_fundamental",
    }
)
ParserStatus = Literal["normalized", "raw_only", "parser_missing"]


class OfficialEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    source_type: str
    endpoint_id: str
    official_path_or_api_name: str
    method: Literal["GET", "POST"]
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    auth: str
    rate_limit_bucket: str
    batch_policy: EndpointBatchPolicy
    parser_status: ParserStatus
    official_doc_ref: str
    http_visibility: HttpVisibility
    request_template: dict[str, Any] = Field(default_factory=dict)
    response_shape: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "OfficialEndpoint":
        for field_name in (
            "provider_id",
            "source_type",
            "endpoint_id",
            "official_path_or_api_name",
            "auth",
            "rate_limit_bucket",
            "parser_status",
            "official_doc_ref",
        ):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"{field_name} 不能为空")
        if self.endpoint_id == "official_api_call":
            raise ValueError("endpoint_id 必须是官方接口粒度，不能是 official_api_call")
        forbidden_nested = _nested_forbidden_keys(self.request_template) | _nested_forbidden_keys(self.response_shape)
        if forbidden_nested:
            raise ValueError(f"catalog metadata 含业务范围字段: {', '.join(sorted(forbidden_nested))}")
        return self


def no_batch() -> EndpointBatchPolicy:
    return EndpointBatchPolicy(supports_batch=False, batch_by="none")


def symbol_batch(max_symbols_per_call: int | None = None) -> EndpointBatchPolicy:
    return EndpointBatchPolicy(
        supports_batch=True,
        batch_by="symbol",
        max_symbols_per_call=max_symbols_per_call,
    )


def date_batch(max_days_per_call: int | None = None) -> EndpointBatchPolicy:
    return EndpointBatchPolicy(
        supports_batch=True,
        batch_by="date",
        max_days_per_call=max_days_per_call,
    )


def endpoint(
    *,
    provider_id: str,
    source_type: str,
    endpoint_id: str,
    official_path_or_api_name: str,
    method: Literal["GET", "POST"],
    required_params: tuple[str, ...],
    optional_params: tuple[str, ...] = (),
    auth: str,
    rate_limit_bucket: str,
    batch_policy: EndpointBatchPolicy | None = None,
    parser_status: ParserStatus | None = None,
    official_doc_ref: str,
    http_visibility: HttpVisibility | str = HttpVisibility.MANAGED_HTTP,
    request_template: dict[str, Any] | None = None,
    response_shape: dict[str, Any] | None = None,
) -> OfficialEndpoint:
    normalized_response_shape = _with_source_evidence(
        response_shape or {},
        source_type=source_type,
        official_doc_ref=official_doc_ref,
        http_visibility=HttpVisibility(http_visibility),
        official_path_or_api_name=official_path_or_api_name,
    )
    return OfficialEndpoint(
        provider_id=provider_id,
        source_type=source_type,
        endpoint_id=endpoint_id,
        official_path_or_api_name=official_path_or_api_name,
        method=method,
        required_params=required_params,
        optional_params=optional_params,
        auth=auth,
        rate_limit_bucket=rate_limit_bucket,
        batch_policy=batch_policy or no_batch(),
        parser_status=parser_status or _default_parser_status(normalized_response_shape),
        official_doc_ref=official_doc_ref,
        http_visibility=HttpVisibility(http_visibility),
        request_template=request_template or {},
        response_shape=normalized_response_shape,
    )


def output_contract(
    *,
    market: str,
    data_type: str,
    granularity: str | tuple[str, ...],
    fields: tuple[str, ...] = (),
    public_api_ids: tuple[str, ...] = (),
    priority_rank: int = 90,
    can_be_formal_fact_source: bool | None = None,
) -> dict[str, Any]:
    granularities = (granularity,) if isinstance(granularity, str) else granularity
    value: dict[str, Any] = {
        "market": market,
        "data_type": data_type,
        "granularity": granularities,
        "fields": fields,
        "public_api_ids": public_api_ids_for_output(
            market=market,
            data_type=data_type,
            public_api_ids=public_api_ids,
        ),
        "priority_rank": priority_rank,
    }
    if can_be_formal_fact_source is not None:
        value["can_be_formal_fact_source"] = can_be_formal_fact_source
    return value


def _nested_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _is_forbidden_business_scope_key(key_text):
                found.add(key_text)
            found.update(_nested_forbidden_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_nested_forbidden_keys(child))
    return found


def _is_forbidden_business_scope_key(key: str) -> bool:
    return key in FORBIDDEN_BUSINESS_SCOPE_KEYS or key.startswith(("allowed_", "only_for_"))


def _default_parser_status(response_shape: dict[str, Any]) -> ParserStatus:
    outputs = response_shape.get("outputs")
    if isinstance(outputs, (list, tuple)) and outputs:
        return "normalized"
    return "parser_missing"


def _with_source_evidence(
    response_shape: dict[str, Any],
    *,
    source_type: str,
    official_doc_ref: str,
    http_visibility: HttpVisibility,
    official_path_or_api_name: str,
) -> dict[str, Any]:
    value = dict(response_shape)
    value.setdefault("source", f"{source_type}_catalog_endpoint_metadata")
    if official_doc_ref:
        value.setdefault("observed_from_url", official_doc_ref)
    if http_visibility == HttpVisibility.SDK_INTERNAL_UNKNOWN:
        value.setdefault("sdk_module", official_path_or_api_name)
    return value


__all__ = [
    "FORBIDDEN_BUSINESS_SCOPE_KEYS",
    "OfficialEndpoint",
    "ParserStatus",
    "date_batch",
    "endpoint",
    "no_batch",
    "output_contract",
    "symbol_batch",
]
