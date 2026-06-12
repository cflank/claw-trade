from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from claw_trade.data_gateway.models import EndpointBatchPolicy, HttpVisibility

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
    parser_status: ParserStatus = "normalized",
    official_doc_ref: str,
    http_visibility: HttpVisibility | str = HttpVisibility.MANAGED_HTTP,
    request_template: dict[str, Any] | None = None,
    response_shape: dict[str, Any] | None = None,
) -> OfficialEndpoint:
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
        parser_status=parser_status,
        official_doc_ref=official_doc_ref,
        http_visibility=HttpVisibility(http_visibility),
        request_template=request_template or {},
        response_shape=response_shape or {},
    )


def _nested_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_BUSINESS_SCOPE_KEYS:
                found.add(key)
            found.update(_nested_forbidden_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_nested_forbidden_keys(child))
    return found


__all__ = [
    "FORBIDDEN_BUSINESS_SCOPE_KEYS",
    "OfficialEndpoint",
    "ParserStatus",
    "date_batch",
    "endpoint",
    "no_batch",
    "symbol_batch",
]
