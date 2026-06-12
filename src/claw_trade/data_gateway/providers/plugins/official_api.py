from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote, urlsplit

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.needs import ProviderCallSpec
from claw_trade.data_gateway.models import FetchResult
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints

from .common import CredentialPolicy, ProviderCapabilities
from .crypto import _coinglass_header_name, _is_keystore_coinglass_proxy
from .market_http import METADATA_ONLY_LICENSE, credential_value, endpoint, endpoint_capability, send_json_request

SUPPORTED_PROVIDER_MARKETS = ("CN_A", "US", "HK", "CRYPTO")
OFFICIAL_API_DATA_TYPE = "official_api_response"
OFFICIAL_API_FIELDS = ("provider_endpoint", "raw_payload", "source_type")
CALL_SPEC_PARAM_KEYS = ("provider_call_spec", "call_spec")
FORBIDDEN_FREEFORM_PARAM_KEYS = frozenset(
    {
        "api_key",
        "api_name",
        "header",
        "header_name",
        "headers",
        "path",
        "token",
        "url",
    }
)
CALL_SPEC_REQUIRED_KEYS = frozenset(
    {
        "call_id",
        "method",
        "provider_id",
        "catalog_endpoint_id",
        "official_path_or_api_name",
        "params",
        "auth_scope",
        "rate_limit_bucket",
        "http_visibility",
        "parser_status",
        "batch_key",
        "official_doc_ref",
        "deadline_at",
        "need_ids",
    }
)


@dataclass(frozen=True)
class OfficialApiSourceSpec:
    source_type: str
    provider_id: str
    credential_name: str
    default_endpoint: str
    auth_mode: str


OFFICIAL_API_SOURCE_SPECS: tuple[OfficialApiSourceSpec, ...] = (
    OfficialApiSourceSpec(
        source_type="tushare",
        provider_id="official_api_tushare",
        credential_name="data_source:tushare",
        default_endpoint="https://api.tushare.pro",
        auth_mode="tushare_body_token",
    ),
    OfficialApiSourceSpec(
        source_type="finnhub",
        provider_id="official_api_finnhub",
        credential_name="data_source:finnhub",
        default_endpoint="https://finnhub.io/api/v1",
        auth_mode="query_token",
    ),
    OfficialApiSourceSpec(
        source_type="fred",
        provider_id="official_api_fred",
        credential_name="data_source:fred",
        default_endpoint="https://api.stlouisfed.org",
        auth_mode="query_api_key",
    ),
    OfficialApiSourceSpec(
        source_type="coingecko_pro",
        provider_id="official_api_coingecko_pro",
        credential_name="data_source:coingecko_pro",
        default_endpoint="https://pro-api.coingecko.com/api/v3",
        auth_mode="header_x_cg_pro_api_key",
    ),
    OfficialApiSourceSpec(
        source_type="coinglass",
        provider_id="official_api_coinglass",
        credential_name="data_source:coinglass",
        default_endpoint="https://open-api-v4.coinglass.com",
        auth_mode="coinglass_header",
    ),
    OfficialApiSourceSpec(
        source_type="glassnode",
        provider_id="official_api_glassnode",
        credential_name="data_source:glassnode",
        default_endpoint="https://api.glassnode.com",
        auth_mode="query_api_key",
    ),
)


class OfficialApiProviderPlugin:
    version = "1.0.0"

    def __init__(self, spec: OfficialApiSourceSpec) -> None:
        self._spec = spec
        self.plugin_id = spec.provider_id
        self._catalog_endpoints = tuple(
            endpoint
            for endpoint in iter_official_catalog_endpoints()
            if endpoint.provider_id == spec.provider_id and endpoint.source_type == spec.source_type
        )
        self._catalog_by_endpoint_id = {endpoint.endpoint_id: endpoint for endpoint in self._catalog_endpoints}
        self._capabilities = ProviderCapabilities(
            provider_id=spec.provider_id,
            plugin_version=self.version,
            endpoints=tuple(
                endpoint_capability(
                    endpoint_id=official_endpoint.endpoint_id,
                    market=market,
                    data_type=OFFICIAL_API_DATA_TYPE,
                    source_role="paid_data",
                    granularity=("event", "realtime", "daily"),
                    fields=OFFICIAL_API_FIELDS,
                    priority_rank=90,
                    supports_batch=official_endpoint.batch_policy.supports_batch,
                    batch_by=official_endpoint.batch_policy.batch_by,
                    http_visibility=str(official_endpoint.http_visibility.value),
                    can_be_formal_fact_source=False,
                )
                for official_endpoint in self._catalog_endpoints
                for market in SUPPORTED_PROVIDER_MARKETS
            ),
            credential_policy=CredentialPolicy(
                credential_required=True,
                credential_names=(spec.credential_name,),
                credential_scope="provider_token",
                missing_behavior="credential_missing",
            ),
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=90,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        call_spec_result = self._call_spec(task)
        if isinstance(call_spec_result, FetchResult):
            return call_spec_result
        call_spec = call_spec_result

        token = credential_value(ctx, self._spec.credential_name)
        if token is None:
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{self._spec.credential_name}"))

        method = call_spec.method.upper()
        if method not in {"GET", "POST"}:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_official_api_method:{method}"))

        host, prefix = self._endpoint(ctx)
        request = self._request(task, ctx=ctx, token=token, host=host, prefix=prefix, call_spec=call_spec, method=method)
        if isinstance(request, FetchResult):
            return request

        payload, observations, error = send_json_request(task, ctx, request)
        if error is not None:
            return error
        return FetchResult.from_success(
            task,
            payload={
                "rows": [
                    {
                        "provider_id": self.plugin_id,
                        "source_type": self._spec.source_type,
                        "provider_endpoint": request.path,
                        "raw_payload": payload,
                    }
                ]
            },
            row_count=1,
            http_observations=observations,
        )

    def _call_spec(self, task: Any) -> ProviderCallSpec | FetchResult:
        params = _params(task)
        forbidden = FORBIDDEN_FREEFORM_PARAM_KEYS & set(params)
        if forbidden:
            joined = ",".join(sorted(forbidden))
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"official_api_freeform_params_rejected:{joined}"))

        candidate: Any | None = None
        for key in CALL_SPEC_PARAM_KEYS:
            if key in params:
                candidate = params[key]
                break
        if candidate is None and CALL_SPEC_REQUIRED_KEYS <= set(params):
            candidate = params
        if candidate is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("official_api_call_spec_required"))

        try:
            call_spec = ProviderCallSpec.model_validate(candidate)
        except Exception as exc:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"official_api_call_spec_invalid:{exc}"))

        catalog_endpoint = self._catalog_by_endpoint_id.get(call_spec.catalog_endpoint_id)
        if catalog_endpoint is None:
            return FetchResult.from_error(
                task,
                status="not_applicable",
                error=RuntimeError(f"official_api_catalog_endpoint_unknown:{call_spec.catalog_endpoint_id}"),
            )
        if call_spec.provider_id != self.plugin_id:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"official_api_provider_mismatch:{call_spec.provider_id}"))
        if getattr(task, "endpoint_id", call_spec.catalog_endpoint_id) != call_spec.catalog_endpoint_id:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("official_api_task_endpoint_mismatch"))
        if call_spec.official_path_or_api_name != catalog_endpoint.official_path_or_api_name:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("official_api_official_endpoint_mismatch"))
        if call_spec.method.upper() != catalog_endpoint.method:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("official_api_method_mismatch"))
        if call_spec.auth_scope != catalog_endpoint.auth:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("official_api_auth_scope_mismatch"))
        forbidden_call_params = _forbidden_param_keys(call_spec.params)
        if forbidden_call_params:
            joined = ",".join(sorted(forbidden_call_params))
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"official_api_call_params_forbidden:{joined}"))
        return call_spec

    def _endpoint(self, ctx: Any) -> tuple[str, str]:
        host, prefix = endpoint(ctx, self._spec.credential_name, self._spec.default_endpoint)
        if self._spec.source_type == "coinglass" and _is_keystore_coinglass_proxy(host, prefix) and not prefix.endswith("/v4") and "/v4/" not in prefix:
            prefix = f"{prefix}/v4"
        return host, prefix

    def _request(
        self,
        task: Any,
        *,
        ctx: Any,
        token: str,
        host: str,
        prefix: str,
        call_spec: ProviderCallSpec,
        method: str,
    ) -> HttpRequestSpec | FetchResult:
        params = call_spec.params
        if self._spec.auth_mode == "tushare_body_token":
            return self._tushare_request(task, token=token, host=host, prefix=prefix, call_spec=call_spec)

        path_param_names = _path_template_names(call_spec.official_path_or_api_name)
        path_result = _path_from_official_path(call_spec.official_path_or_api_name, params=params)
        if isinstance(path_result, Exception):
            return FetchResult.from_error(task, status="not_applicable", error=path_result)
        path, path_query = path_result

        query = dict(path_query)
        query.update(_query_params(params, exclude=path_param_names))
        headers = {"accept": "application/json"}
        body, body_headers = _body_and_headers(params)
        headers.update(body_headers)

        if self._spec.auth_mode == "query_token":
            query["token"] = token
        elif self._spec.auth_mode == "query_api_key":
            query["api_key"] = token
            if self._spec.source_type == "fred":
                query.setdefault("file_type", "json")
        elif self._spec.auth_mode == "header_x_cg_pro_api_key":
            headers["x-cg-pro-api-key"] = token
        elif self._spec.auth_mode == "coinglass_header":
            headers[_coinglass_header_name(ctx, self._spec.credential_name)] = token
        else:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_auth_mode:{self._spec.auth_mode}"))

        return HttpRequestSpec(
            method=method,
            host=host,
            path=_join_prefix_path(prefix, path),
            query=query,
            body=body,
            headers=headers,
            provider_config_version=getattr(task, "provider_config_version", None),
        )

    def _tushare_request(
        self,
        task: Any,
        *,
        token: str,
        host: str,
        prefix: str,
        call_spec: ProviderCallSpec,
    ) -> HttpRequestSpec | FetchResult:
        api_name = _non_empty(call_spec.official_path_or_api_name)
        if api_name is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("tushare_api_name_required"))

        path_result = _path_from_official_path("/")
        if isinstance(path_result, Exception):
            return FetchResult.from_error(task, status="not_applicable", error=path_result)
        path, path_query = path_result

        params = call_spec.params
        fields = params.get("fields", "")
        if isinstance(fields, (list, tuple)):
            fields = ",".join(str(item) for item in fields)
        api_params = {key: value for key, value in params.items() if key != "fields"}
        body = {
            "api_name": api_name,
            "token": token,
            "params": api_params,
            "fields": fields,
        }
        return HttpRequestSpec(
            method="POST",
            host=host,
            path=_join_prefix_path(prefix, path),
            query=dict(path_query),
            body=json.dumps(body, ensure_ascii=True, separators=(",", ":")),
            headers={"accept": "application/json", "content-type": "application/json"},
            provider_config_version=getattr(task, "provider_config_version", None),
        )


def build_official_api_provider_plugins() -> tuple[OfficialApiProviderPlugin, ...]:
    return tuple(OfficialApiProviderPlugin(spec) for spec in OFFICIAL_API_SOURCE_SPECS)


def _params(task: Any) -> Mapping[str, Any]:
    value = getattr(task, "params", {}) or {}
    return value if isinstance(value, Mapping) else {}


def _path_from_official_path(
    raw_path_value: Any,
    *,
    params: Mapping[str, Any] | None = None,
) -> tuple[str, Mapping[str, Any]] | Exception:
    raw_path = _non_empty(raw_path_value)
    if raw_path is None:
        return RuntimeError("official_api_path_required")
    for name in _path_template_names(raw_path):
        value = (params or {}).get(name)
        if value is None:
            return RuntimeError(f"official_api_path_param_required:{name}")
        raw_path = raw_path.replace("{" + name + "}", quote(str(value), safe=""))
    split = urlsplit(raw_path)
    if split.scheme or split.netloc or raw_path.startswith("//"):
        return RuntimeError("official_api_path_must_be_relative")
    path = split.path or "/"
    if not path.startswith("/"):
        return RuntimeError("official_api_path_must_start_with_slash")
    if any(part == ".." for part in path.split("/")):
        return RuntimeError("official_api_path_must_not_escape_base")
    return path, dict(parse_qsl(split.query, keep_blank_values=True))


def _path_template_names(raw_path_value: Any) -> tuple[str, ...]:
    raw_path = _non_empty(raw_path_value)
    if raw_path is None:
        return ()
    return tuple(dict.fromkeys(re.findall(r"{([A-Za-z_][A-Za-z0-9_]*)}", raw_path)))


def _join_prefix_path(prefix: str, path: str) -> str:
    clean_prefix = prefix.rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    if not clean_prefix:
        return clean_path
    return f"{clean_prefix}{clean_path}"


def _mapping_param(params: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = params.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _query_params(params: Mapping[str, Any], *, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    excluded = set(exclude)
    explicit = _mapping_param(params, "query")
    if explicit or "query" in params or "body" in params or "json" in params:
        return {str(key): value for key, value in explicit.items() if str(key) not in excluded}
    return {str(key): value for key, value in params.items() if str(key) not in excluded}


def _forbidden_param_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_FREEFORM_PARAM_KEYS:
                found.add(key_text)
            found.update(_forbidden_param_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_forbidden_param_keys(child))
    return found


def _body_and_headers(params: Mapping[str, Any]) -> tuple[str | bytes | None, dict[str, str]]:
    if "json" in params:
        return json.dumps(params["json"], ensure_ascii=True, separators=(",", ":")), {"content-type": "application/json"}
    body = params.get("body")
    if body is None:
        return None, {}
    if isinstance(body, bytes):
        return body, {}
    if isinstance(body, str):
        return body, {}
    if isinstance(body, Mapping):
        return json.dumps(body, ensure_ascii=True, separators=(",", ":")), {"content-type": "application/json"}
    return str(body), {}


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
