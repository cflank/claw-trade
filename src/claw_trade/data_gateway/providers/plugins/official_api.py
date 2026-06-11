from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.models import FetchResult

from .common import CredentialPolicy, ProviderCapabilities
from .crypto import _coinglass_header_name, _is_keystore_coinglass_proxy
from .market_http import METADATA_ONLY_LICENSE, credential_value, endpoint, endpoint_capability, send_json_request

SUPPORTED_PROVIDER_MARKETS = ("CN_A", "US", "HK", "CRYPTO")
OFFICIAL_API_DATA_TYPE = "official_api_response"
OFFICIAL_API_ENDPOINT_ID = "official_api_call"
OFFICIAL_API_FIELDS = ("provider_endpoint", "raw_payload", "source_type")


@dataclass(frozen=True)
class OfficialApiSourceSpec:
    source_type: str
    provider_id: str
    credential_name: str
    default_endpoint: str
    auth_mode: str
    default_method: str = "GET"


OFFICIAL_API_SOURCE_SPECS: tuple[OfficialApiSourceSpec, ...] = (
    OfficialApiSourceSpec(
        source_type="tushare",
        provider_id="official_api_tushare",
        credential_name="data_source:tushare",
        default_endpoint="https://api.tushare.pro",
        auth_mode="tushare_body_token",
        default_method="POST",
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
        self._capabilities = ProviderCapabilities(
            provider_id=spec.provider_id,
            plugin_version=self.version,
            endpoints=tuple(
                endpoint_capability(
                    endpoint_id=OFFICIAL_API_ENDPOINT_ID,
                    market=market,
                    data_type=OFFICIAL_API_DATA_TYPE,
                    source_role="paid_data",
                    granularity=("event", "realtime", "daily"),
                    fields=OFFICIAL_API_FIELDS,
                    priority_rank=90,
                    supports_batch=False,
                    batch_by="none",
                    can_be_formal_fact_source=False,
                )
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
        token = credential_value(ctx, self._spec.credential_name)
        if token is None:
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{self._spec.credential_name}"))

        method = _request_method(task, default=self._spec.default_method)
        if method not in {"GET", "POST"}:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_official_api_method:{method}"))

        host, prefix = self._endpoint(ctx)
        request = self._request(task, ctx=ctx, token=token, host=host, prefix=prefix, method=method)
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

    def _endpoint(self, ctx: Any) -> tuple[str, str]:
        host, prefix = endpoint(ctx, self._spec.credential_name, self._spec.default_endpoint)
        if self._spec.source_type == "coinglass" and _is_keystore_coinglass_proxy(host, prefix) and not prefix.endswith("/v4") and "/v4/" not in prefix:
            prefix = f"{prefix}/v4"
        return host, prefix

    def _request(self, task: Any, *, ctx: Any, token: str, host: str, prefix: str, method: str) -> HttpRequestSpec | FetchResult:
        params = _params(task)
        if self._spec.auth_mode == "tushare_body_token":
            return self._tushare_request(task, token=token, host=host, prefix=prefix, params=params)

        path_result = _path_from_params(params)
        if isinstance(path_result, Exception):
            return FetchResult.from_error(task, status="not_applicable", error=path_result)
        path, path_query = path_result

        query = dict(path_query)
        query.update(_mapping_param(params, "query"))
        headers = {"accept": "application/json"}
        body, body_headers = _body_and_headers(params)
        headers.update(body_headers)

        if self._spec.auth_mode == "query_token":
            query.setdefault("token", token)
        elif self._spec.auth_mode == "query_api_key":
            query.setdefault("api_key", token)
            if self._spec.source_type == "fred":
                query.setdefault("file_type", "json")
        elif self._spec.auth_mode == "header_x_cg_pro_api_key":
            headers["x-cg-pro-api-key"] = token
        elif self._spec.auth_mode == "coinglass_header":
            headers[_coinglass_header_name_from_params(ctx, params, self._spec.credential_name)] = token
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

    def _tushare_request(self, task: Any, *, token: str, host: str, prefix: str, params: Mapping[str, Any]) -> HttpRequestSpec | FetchResult:
        api_name = _non_empty(params.get("api_name"))
        if api_name is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("tushare_api_name_required"))

        path_result = _path_from_params(params, default_path="/")
        if isinstance(path_result, Exception):
            return FetchResult.from_error(task, status="not_applicable", error=path_result)
        path, path_query = path_result

        fields = params.get("fields", "")
        if isinstance(fields, (list, tuple)):
            fields = ",".join(str(item) for item in fields)
        body = {
            "api_name": api_name,
            "token": token,
            "params": _mapping_param(params, "api_params") or _mapping_param(params, "params"),
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


def _request_method(task: Any, *, default: str) -> str:
    method = _non_empty(_params(task).get("method")) or default
    return method.upper()


def _path_from_params(params: Mapping[str, Any], *, default_path: str | None = None) -> tuple[str, Mapping[str, Any]] | Exception:
    raw_path = _non_empty(params.get("path")) or default_path
    if raw_path is None:
        return RuntimeError("official_api_path_required")
    split = urlsplit(raw_path)
    if split.scheme or split.netloc or raw_path.startswith("//"):
        return RuntimeError("official_api_path_must_be_relative")
    path = split.path or "/"
    if not path.startswith("/"):
        return RuntimeError("official_api_path_must_start_with_slash")
    if any(part == ".." for part in path.split("/")):
        return RuntimeError("official_api_path_must_not_escape_base")
    return path, dict(parse_qsl(split.query, keep_blank_values=True))


def _join_prefix_path(prefix: str, path: str) -> str:
    clean_prefix = prefix.rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    if not clean_prefix:
        return clean_path
    return f"{clean_prefix}{clean_path}"


def _mapping_param(params: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = params.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


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


def _coinglass_header_name_from_params(ctx: Any, params: Mapping[str, Any], credential_name: str) -> str:
    configured = _non_empty(params.get("header_name"))
    if configured:
        return configured
    return _coinglass_header_name(ctx, credential_name)


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
