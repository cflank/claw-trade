from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, quote, urlsplit

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.needs import ProviderCallSpec
from claw_trade.data_gateway.models import FetchResult
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint

from .common import CredentialPolicy, ProviderCapabilities
from .crypto import _coinglass_header_name, _is_keystore_coinglass_proxy
from .market_http import METADATA_ONLY_LICENSE, credential_value, endpoint, endpoint_capability, send_json_request

OFFICIAL_API_DATA_TYPE = "official_api_response"
OFFICIAL_API_FIELDS = ("provider_endpoint", "raw_payload", "source_type")
TUSHARE_OFFICIAL_REQUEST_TIMEOUT_SECONDS = 6.0
OFFICIAL_REQUEST_TIMEOUT_SECONDS_BY_ENDPOINT = {
    "coinglass.bitcoin_ahr999": 55.0,
    "coinglass.futures_liquidation_heatmap": 55.0,
}
CALL_SPEC_PARAM_KEYS = ("provider_call_spec", "call_spec")
FORBIDDEN_FREEFORM_PARAM_KEYS = frozenset(
    {
        "api_key",
        "api_name",
        "allowed_domain",
        "allowed_report_section",
        "allowed_worker",
        "consumer",
        "domain",
        "header",
        "header_name",
        "headers",
        "only_for_fundamental",
        "only_for_market",
        "path",
        "provider",
        "provider_scope",
        "report_section",
        "requested_by_worker",
        "token",
        "url",
        "worker",
        "worker_id",
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
            endpoints=tuple(_official_endpoint_capabilities(self._catalog_endpoints)),
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
        catalog_endpoint = self._catalog_by_endpoint_id.get(call_spec.catalog_endpoint_id)

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
        payload_error = _official_payload_error(payload=payload, source_type=self._spec.source_type)
        if payload_error is not None:
            return FetchResult.from_error(
                task,
                status=_official_payload_error_status(payload_error),
                error=payload_error,
                http_observations=observations,
            )
        rows = _normalized_rows_from_payload(
            call_spec=call_spec,
            payload=payload,
            task=task,
            source_type=self._spec.source_type,
            catalog_endpoint=catalog_endpoint,
        )
        if rows is not None:
            if not rows:
                return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
            return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)
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
        wrapped_call_spec = any(key in params for key in CALL_SPEC_PARAM_KEYS)
        flat_call_spec = CALL_SPEC_REQUIRED_KEYS <= set(params)
        if wrapped_call_spec:
            freeform_params = {key: value for key, value in params.items() if key not in CALL_SPEC_PARAM_KEYS}
            forbidden = _forbidden_param_keys(freeform_params)
        elif not flat_call_spec:
            forbidden = _forbidden_param_keys(params)
        else:
            forbidden = set()
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
            timeout_seconds=_official_request_timeout_seconds(call_spec),
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

        path_result = _path_from_official_path(f"/{api_name}")
        if isinstance(path_result, Exception):
            return FetchResult.from_error(task, status="not_applicable", error=path_result)
        path, path_query = path_result

        params = call_spec.params
        fields = params.get("fields", "")
        if isinstance(fields, (list, tuple)):
            fields = ",".join(str(item) for item in fields)
        api_params = {key: value for key, value in params.items() if key != "fields"}
        api_params.setdefault("ts_type_name", f"{host}{prefix}".rstrip("/"))
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
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "user-agent": "python-requests/2.x",
            },
            provider_config_version=getattr(task, "provider_config_version", None),
            timeout_seconds=TUSHARE_OFFICIAL_REQUEST_TIMEOUT_SECONDS,
        )


def build_official_api_provider_plugins() -> tuple[OfficialApiProviderPlugin, ...]:
    return tuple(OfficialApiProviderPlugin(spec) for spec in OFFICIAL_API_SOURCE_SPECS)


def _official_request_timeout_seconds(call_spec: ProviderCallSpec) -> float:
    return OFFICIAL_REQUEST_TIMEOUT_SECONDS_BY_ENDPOINT.get(str(call_spec.catalog_endpoint_id or ""), 30.0)


def _official_endpoint_capabilities(catalog_endpoints: tuple[OfficialEndpoint, ...]) -> tuple[Any, ...]:
    capabilities: list[Any] = []
    for official_endpoint in catalog_endpoints:
        outputs = official_endpoint.response_shape.get("outputs")
        if not isinstance(outputs, (list, tuple)):
            continue
        for output in outputs:
            if not isinstance(output, Mapping):
                continue
            market = _non_empty(output.get("market"))
            data_type = _non_empty(output.get("data_type"))
            granularities = _tuple_text(output.get("granularity") or output.get("granularities"))
            if not market or not data_type or not granularities:
                continue
            fields = _tuple_text(output.get("fields")) or OFFICIAL_API_FIELDS
            capabilities.append(
                endpoint_capability(
                    endpoint_id=official_endpoint.endpoint_id,
                    market=market,
                    data_type=data_type,
                    source_role="paid_data",
                    granularity=granularities,
                    fields=fields,
                    priority_rank=_int_or(output.get("priority_rank"), 90),
                    supports_batch=official_endpoint.batch_policy.supports_batch,
                    batch_by=official_endpoint.batch_policy.batch_by,
                    http_visibility=str(official_endpoint.http_visibility.value),
                    can_be_formal_fact_source=_bool_or(
                        output.get("can_be_formal_fact_source"),
                        official_endpoint.parser_status == "normalized",
                    ),
                )
            )
    return tuple(capabilities)


def _params(task: Any) -> Mapping[str, Any]:
    value = getattr(task, "params", {}) or {}
    return value if isinstance(value, Mapping) else {}


def _tuple_text(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _int_or(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _bool_or(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return fallback


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
            if _is_forbidden_freeform_param_key(key_text):
                found.add(key_text)
            found.update(_forbidden_param_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_forbidden_param_keys(child))
    return found


def _is_forbidden_freeform_param_key(key: str) -> bool:
    return key in FORBIDDEN_FREEFORM_PARAM_KEYS or key.startswith(("allowed_", "only_for_"))


def _official_payload_error(*, payload: Any, source_type: str) -> RuntimeError | None:
    if not isinstance(payload, Mapping):
        return None
    code = payload.get("code")
    if source_type == "coinglass" and code not in (None, 0, "0"):
        msg = _non_empty(payload.get("msg")) or _non_empty(payload.get("message")) or _non_empty(payload.get("error")) or "unknown"
        return RuntimeError(f"coinglass_api_error:{code}:{msg}")
    if source_type != "tushare":
        return None
    if code in (None, 0, "0"):
        return None
    msg = _non_empty(payload.get("msg")) or _non_empty(payload.get("message")) or "unknown"
    return RuntimeError(f"tushare_api_error:{code}:{msg}")


def _official_payload_error_status(error: RuntimeError) -> str:
    text = str(error).strip().lower()
    if any(token in text for token in ("频次", "频率", "too many", "rate limit", "ratelimit", "每分钟")):
        return "rate_limited"
    if any(
        token in text
        for token in (
            "token",
            "auth",
            "认证",
            "权限",
            "无权",
            "积分",
            "套餐",
            "permission",
            "forbidden",
            "unauthorized",
            "denied",
        )
    ):
        return "permission_denied"
    return "error"


def _normalized_rows_from_payload(
    *,
    call_spec: ProviderCallSpec,
    payload: Any,
    task: Any,
    source_type: str,
    catalog_endpoint: OfficialEndpoint | None,
) -> list[dict[str, Any]] | None:
    if source_type == "tushare" and call_spec.catalog_endpoint_id == "tushare.daily":
        return _tushare_daily_rows(payload=payload, task=task)
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.futures_funding_rate":
        return _coinglass_funding_rate_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.futures_liquidation_heatmap":
        return _coinglass_liquidation_heatmap_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coinglass" and call_spec.catalog_endpoint_id in {"coinglass.spot_cvd_history", "coinglass.futures_cvd_history"}:
        return _coinglass_cvd_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.onchain_exchange_balance":
        return _coinglass_exchange_balance_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.bitcoin_ahr999":
        return _coinglass_ahr999_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.options_open_interest":
        return _coinglass_options_data_map_rows(payload=payload, task=task, params=call_spec.params, field="options_open_interest")
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.options_volume":
        return _coinglass_options_data_map_rows(payload=payload, task=task, params=call_spec.params, field="options_volume")
    if source_type == "coinglass" and call_spec.catalog_endpoint_id == "coinglass.raw_index_stablecoin_marketcap_history":
        return _coinglass_stablecoin_marketcap_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coinglass" and _catalog_has_public_api(catalog_endpoint, "etf_flow"):
        return _coinglass_etf_flow_rows(payload=payload, task=task, params=call_spec.params)
    if source_type == "coingecko_pro" and call_spec.catalog_endpoint_id == "coingecko_pro.coins_id":
        return _coingecko_coin_id_rows(
            payload=payload,
            task=task,
            params=call_spec.params,
            catalog_endpoint=catalog_endpoint,
            public_api_id=call_spec.public_api_id or call_spec.business_api_id,
        )
    return _generic_catalog_rows(
        payload=payload,
        task=task,
        params=call_spec.params,
        catalog_endpoint=catalog_endpoint,
        public_api_id=call_spec.public_api_id or call_spec.business_api_id,
    )


def _generic_catalog_rows(
    *,
    payload: Any,
    task: Any,
    params: Mapping[str, Any],
    catalog_endpoint: OfficialEndpoint | None,
    public_api_id: str | None = None,
) -> list[dict[str, Any]] | None:
    output = _select_catalog_output(catalog_endpoint=catalog_endpoint, task=task, params=params, public_api_id=public_api_id)
    if output is None:
        return None
    records = _payload_records(payload)
    if not records:
        return []

    data_type = str(output.get("data_type") or "").strip()
    market = str(output.get("market") or getattr(task, "market", "") or "").strip().upper()
    if not data_type or not market:
        return None
    source_type = str(getattr(catalog_endpoint, "source_type", "") or "").strip().lower() if catalog_endpoint is not None else ""
    fields = _tuple_text(output.get("fields"))
    granularity = _selected_output_granularity(output=output, task=task, params=params)
    symbol, base_asset, quote_asset = _catalog_symbol_parts(task=task, params=params, market=market)
    rows: list[dict[str, Any]] = []
    for record in records:
        if not _record_matches_requested_asset(
            record,
            data_type=data_type,
            market=market,
            base_asset=base_asset,
            quote_asset=quote_asset,
        ):
            continue
        populated_fields: set[str] = set()
        row: dict[str, Any] = {
            "dataset": data_type,
            "market": market,
            "symbol_id": symbol,
            "granularity": granularity,
            "source_roles": ("paid_data",),
            "schema_id": f"{data_type}.v1",
            "quality_flags": (),
        }
        if market == "CRYPTO":
            row["base_asset"] = base_asset
            row["quote_asset"] = quote_asset

        timestamp = _record_timestamp(record)
        row_date = _record_date(record)
        if timestamp is not None:
            row["timestamp"] = timestamp
            row["period_start"] = timestamp.date()
            row["period_end"] = timestamp.date()
        elif row_date is not None:
            row["date"] = row_date
            row["period_start"] = row_date
            row["period_end"] = row_date
        elif _needs_realtime_timestamp(fields=fields, granularity=granularity):
            timestamp = datetime.now(tz=UTC)
            row["timestamp"] = timestamp
            row["period_start"] = timestamp.date()
            row["period_end"] = timestamp.date()

        for field in fields:
            if field in {"symbol_id", "timestamp", "date"}:
                continue
            value = _record_field_value(record, field)
            if value is None and source_type == "glassnode" and _is_metric_value_field(field):
                value = _record_field_value(record, "value")
            if value is None:
                continue
            if field == "period":
                row[field] = _parse_any_date(value) or _coerce_scalar(value)
            else:
                row[field] = _coerce_scalar(value)
            if _is_measure_output_field(field):
                populated_fields.add(field)

        if _is_event_output(data_type=data_type, granularity=granularity, fields=fields):
            _fill_event_fields(row=row, record=record, fields=fields, source_type=source_type, endpoint_id=getattr(catalog_endpoint, "endpoint_id", ""))
        _fill_derived_metric_fields(row=row, fields=fields)
        _fill_catalog_metric_identity(row=row, fields=fields, catalog_endpoint=catalog_endpoint)
        populated_fields.update(field for field in fields if _is_measure_output_field(field) and row.get(field) is not None)
        if not populated_fields:
            continue
        _copy_unit_defaults(row=row, params=params, fields=fields, quote_asset=quote_asset)
        rows.append(row)
    return rows


def _select_catalog_output(
    *,
    catalog_endpoint: OfficialEndpoint | None,
    task: Any,
    params: Mapping[str, Any],
    public_api_id: str | None = None,
) -> Mapping[str, Any] | None:
    outputs = _catalog_outputs(catalog_endpoint)
    if not outputs:
        return None
    market = str(getattr(task, "market", "") or "").strip().upper()
    market_matches = tuple(output for output in outputs if str(output.get("market") or "").strip().upper() == market)
    candidates = market_matches or outputs
    public_api = str(public_api_id or "").strip().lower()
    if public_api:
        public_api_matches = tuple(
            output
            for output in candidates
            if _output_has_public_api_id(output=output, public_api_id=public_api)
        )
        if public_api_matches:
            candidates = public_api_matches
    task_data_type = _non_empty(getattr(task, "data_type", None))
    if task_data_type:
        data_type_matches = tuple(output for output in candidates if _non_empty(output.get("data_type")) == task_data_type)
        if data_type_matches:
            candidates = data_type_matches
    requested = _canonical_output_granularity(
        _non_empty(params.get("interval")) or _non_empty(params.get("i")) or _non_empty(getattr(task, "granularity", None))
    )
    if requested is not None:
        for output in candidates:
            granularities = tuple(_canonical_output_granularity(item) for item in _tuple_text(output.get("granularity") or output.get("granularities")))
            if requested in granularities:
                return output
    return candidates[0]


def _output_has_public_api_id(*, output: Mapping[str, Any], public_api_id: str) -> bool:
    suffix = public_api_id.rsplit(".", 1)[-1]
    for item in tuple(output.get("public_api_ids") or ()):
        candidate = str(item or "").strip().lower()
        if candidate == public_api_id or candidate == suffix or candidate.endswith(f".{suffix}"):
            return True
    return False


def _catalog_outputs(catalog_endpoint: OfficialEndpoint | None) -> tuple[Mapping[str, Any], ...]:
    if catalog_endpoint is None:
        return ()
    outputs = catalog_endpoint.response_shape.get("outputs")
    if not isinstance(outputs, (list, tuple)):
        return ()
    return tuple(output for output in outputs if isinstance(output, Mapping))


def _catalog_has_public_api(catalog_endpoint: OfficialEndpoint | None, api_id: str) -> bool:
    wanted = str(api_id).strip()
    if not wanted:
        return False
    for output in _catalog_outputs(catalog_endpoint):
        public_ids = {str(item).strip() for item in tuple(output.get("public_api_ids") or ())}
        if wanted in public_ids or any(item.endswith(f".{wanted}") for item in public_ids):
            return True
    return False


def _selected_output_granularity(*, output: Mapping[str, Any], task: Any, params: Mapping[str, Any]) -> str:
    requested = _canonical_output_granularity(
        _non_empty(params.get("interval")) or _non_empty(params.get("i")) or _non_empty(getattr(task, "granularity", None))
    )
    granularities = tuple(_canonical_output_granularity(item) for item in _tuple_text(output.get("granularity") or output.get("granularities")))
    granularities = tuple(item for item in granularities if item)
    if requested in granularities:
        return str(requested)
    return str(granularities[0] if granularities else requested or "event")


def _canonical_output_granularity(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return {
        "1h": "hourly",
        "60m": "hourly",
        "10m": "intraday",
        "1d": "daily",
        "24h": "daily",
        "d": "daily",
        "1w": "weekly",
    }.get(text, text)


def _needs_realtime_timestamp(*, fields: tuple[str, ...], granularity: str) -> bool:
    return "timestamp" in fields and granularity == "realtime"


def _is_measure_output_field(field: str) -> bool:
    if field in {"symbol_id", "timestamp", "date", "period", "period_start", "period_end", "metric"}:
        return False
    return not field.endswith("_unit")


def _is_event_output(*, data_type: str, granularity: str, fields: tuple[str, ...]) -> bool:
    return granularity == "event" or data_type in {"company_news", "macro_news", "official_filing", "event_calendar", "social_signal", "corporate_action"} or "event_id" in fields


def _catalog_symbol_parts(*, task: Any, params: Mapping[str, Any], market: str) -> tuple[str, str | None, str | None]:
    if market == "CRYPTO":
        symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
        return symbol, base_asset, quote_asset
    symbol = (
        _first_symbol(task)
        or _non_empty(params.get("ts_code"))
        or _non_empty(params.get("symbol"))
        or _non_empty(params.get("code"))
        or _non_empty(params.get("secid"))
        or ""
    )
    return str(symbol).strip().upper(), None, None


def _payload_records(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        return tuple(item for item in payload if isinstance(item, Mapping))
    if not isinstance(payload, Mapping):
        return ()
    tushare_items = _tushare_payload_items(payload)
    if tushare_items:
        return tushare_items
    if _is_tushare_tabular_payload(payload):
        return ()
    columnar = _columnar_records(payload)
    if columnar:
        return columnar
    for key in ("data", "items", "result", "results", "rows", "observations"):
        value = payload.get(key)
        records = _records_from_value(value)
        if records:
            return records
    data = payload.get("data")
    if isinstance(data, Mapping):
        tabular = _tabular_records(data)
        if tabular:
            return tabular
        columnar = _columnar_records(data)
        if columnar:
            return columnar
        for key in ("items", "result", "results", "rows", "observations", "list"):
            records = _records_from_value(data.get(key))
            if records:
                return records
    scalar_record = {str(key): value for key, value in payload.items() if _is_scalar(value)}
    return (scalar_record,) if scalar_record else ()


def _record_matches_requested_asset(
    record: Mapping[str, Any],
    *,
    data_type: str,
    market: str,
    base_asset: str | None,
    quote_asset: str | None,
) -> bool:
    if market != "CRYPTO" or data_type not in {"quote_snapshot", "valuation_metric"} or not base_asset:
        return True
    asset = _crypto_record_asset(record)
    if asset is None:
        return True
    normalized_asset = _normalize_crypto_symbol_text(asset)
    base = _normalize_crypto_symbol_text(base_asset)
    quote = _normalize_crypto_symbol_text(quote_asset)
    if normalized_asset == base:
        return True
    if quote and normalized_asset == f"{base}{quote}":
        return True
    return False


def _crypto_record_asset(record: Mapping[str, Any]) -> str | None:
    for key in (
        "base_asset",
        "baseAsset",
        "asset",
        "coin",
        "currency",
        "symbol",
        "base_currency",
        "baseCurrency",
    ):
        value = _non_empty(record.get(key))
        if value:
            return value
    return None


def _normalize_crypto_symbol_text(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _is_tushare_tabular_payload(payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    return isinstance(data, Mapping) and ("fields" in data or "items" in data)


def _records_from_value(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, list):
        rows: list[Mapping[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            columnar = _columnar_records(item)
            if columnar:
                rows.extend(columnar)
            else:
                rows.append(item)
        return tuple(rows)
    if isinstance(value, Mapping):
        tabular = _tabular_records(value)
        if tabular:
            return tabular
        columnar = _columnar_records(value)
        if columnar:
            return columnar
        scalar_record = {str(key): item for key, item in value.items() if _is_scalar(item)}
        return (scalar_record,) if scalar_record else ()
    return ()


def _columnar_records(value: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    list_items = {str(key): item for key, item in value.items() if isinstance(item, list)}
    if not list_items:
        return ()
    if any(not all(_is_scalar(child) for child in item) for item in list_items.values()):
        return ()
    lengths = {len(item) for item in list_items.values()}
    if len(lengths) != 1:
        return ()
    count = next(iter(lengths))
    if count == 0:
        return ()
    rows: list[dict[str, Any]] = []
    scalar_items = {str(key): item for key, item in value.items() if key not in list_items and _is_scalar(item)}
    for index in range(count):
        row = dict(scalar_items)
        row.update({key: items[index] for key, items in list_items.items()})
        rows.append(row)
    return tuple(rows)


def _tabular_records(value: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    columns = value.get("columns") or value.get("fields")
    if not isinstance(columns, list):
        return ()
    names = tuple(str(column).strip() for column in columns if str(column).strip())
    if not names:
        return ()
    rows_value = None
    for key in ("rows", "items", "data", "values"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            rows_value = candidate
            break
    if rows_value is None:
        return ()
    rows: list[dict[str, Any]] = []
    for item in rows_value:
        if isinstance(item, Mapping):
            rows.append(dict(item))
        elif isinstance(item, list):
            rows.append(dict(zip(names, item, strict=False)))
    return tuple(rows)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _record_timestamp(record: Mapping[str, Any]) -> datetime | None:
    return _parse_any_timestamp(
        _first_present(
            record,
            (
                "timestamp",
                "time",
                "t",
                "trade_time",
                "trade_datetime",
                "datetime",
                "block_timestamp",
                "last_updated",
                "time_list",
                "published_at",
                "publishedAt",
                "publish_timestamp",
                "publishTimestamp",
                "article_release_time",
                "articleReleaseTime",
            ),
        )
    )


def _record_date(record: Mapping[str, Any]) -> date | None:
    return _parse_any_date(_first_present(record, ("date", "trade_date", "day", "period", "end_date")))


def _parse_any_timestamp(value: Any) -> datetime | None:
    parsed_epoch = _parse_epoch_timestamp(value)
    if parsed_epoch is not None:
        return parsed_epoch
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed_date = _parse_yyyymmdd(text)
        if parsed_date is None:
            try:
                parsed_date = date.fromisoformat(text)
            except ValueError:
                return None
        if parsed_date is None:
            return None
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_any_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed_yyyymmdd = _parse_yyyymmdd(value)
    if parsed_yyyymmdd is not None:
        return parsed_yyyymmdd
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        parsed_dt = _parse_any_timestamp(text) if "T" in text or ":" in text else None
        return None if parsed_dt is None else parsed_dt.date()


def _record_field_value(record: Mapping[str, Any], field: str) -> Any:
    aliases = _field_aliases(field)
    for key in aliases:
        if key in record and record[key] not in (None, ""):
            return record[key]
    lowered = {str(key).lower(): value for key, value in record.items()}
    for key in aliases:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _field_aliases(field: str) -> tuple[str, ...]:
    camel = _snake_to_camel(field)
    aliases = {
        "period": ("period", "end_date", "ann_date", "trade_date"),
        "value": ("value", "v", "c", "close", "amount_usd", "usd_value"),
        "price": ("price", "current_price", "c", "close", "value"),
        "change": ("change", "d"),
        "change_pct": ("change_pct", "changePercent", "dp", "percent_change_24h"),
        "open": ("open", "o"),
        "high": ("high", "h"),
        "low": ("low", "l"),
        "close": ("close", "c"),
        "volume": ("volume", "vol", "v", "total_volume", "volume_usd", "volumeUsd", "volume_usd_24h"),
        "volume_usd": ("volume_usd", "volumeUsd", "total_volume", "volume", "volume_usd_24h"),
        "etf_flow_usd": ("etf_flow_usd", "flow_usd", "flowUsd", "net_flow", "netFlow", "net_flow_usd", "netFlowUsd", "value"),
        "amount": ("amount", "amt", "turnover"),
        "market_cap": ("market_cap", "marketCap", "market_capitalization", "total_mv", "circ_mv"),
        "sector_code": ("sector_code", "ts_code", "code", "symbol"),
        "sector_name": ("sector_name", "name", "industry", "concept_name"),
        "main_net": ("main_net", "net_amount", "net_buy_amount", "net_mf_amount"),
        "main_pct": ("main_pct", "net_amount_rate"),
        "super_net": ("super_net", "buy_elg_amount"),
        "large_net": ("large_net", "buy_lg_amount"),
        "mid_net": ("mid_net", "buy_md_amount"),
        "small_net": ("small_net", "buy_sm_amount"),
        "fdv": ("fdv", "fully_diluted_valuation"),
        "circulating_supply": ("circulating_supply", "circulatingSupply"),
        "total_supply": ("total_supply", "totalSupply"),
        "revenue": ("revenue", "total_revenue", "oper_revenue", "operating_revenue"),
        "net_income": ("net_income", "n_income_attr_p", "n_income", "net_profit", "profit_to_parent", "parent_net_profit"),
        "assets": ("assets", "total_assets"),
        "liabilities": ("liabilities", "total_liab"),
        "cash_flow": ("cash_flow", "n_cashflow_act", "net_cash_flows_oper_act", "net_operate_cash_flow"),
        "roe": ("roe", "roe_avg", "roe_yearly", "roe_dt", "roe_weighted"),
        "eps": ("eps", "basic_eps", "diluted_eps"),
        "gross_profit": ("gross_profit", "grossprofit", "gross_profit_margin"),
        "open_interest": ("open_interest", "openInterest", "oi", "c", "close", "value"),
        "long_short_ratio": (
            "long_short_ratio",
            "longShortRatio",
            "global_account_long_short_ratio",
            "top_account_long_short_ratio",
            "top_position_long_short_ratio",
            "account_long_short_ratio",
            "long_short_account_ratio",
            "ratio",
            "c",
            "close",
            "value",
        ),
        "taker_buy_volume": (
            "taker_buy_volume",
            "takerBuyVolume",
            "taker_buy_vol",
            "agg_taker_buy_vol",
            "aggregated_buy_volume_usd",
            "taker_buy_volume_usd",
            "buy_volume",
            "buyVolume",
            "buy_vol_usd",
        ),
        "taker_sell_volume": (
            "taker_sell_volume",
            "takerSellVolume",
            "taker_sell_vol",
            "agg_taker_sell_vol",
            "aggregated_sell_volume_usd",
            "taker_sell_volume_usd",
            "sell_volume",
            "sellVolume",
            "sell_vol_usd",
        ),
        "taker_buy_sell_ratio": ("taker_buy_sell_ratio", "takerBuySellRatio", "ratio"),
        "long_liquidation": ("long_liquidation", "longLiquidation", "long_liquidation_usd", "aggregated_long_liquidation_usd"),
        "short_liquidation": ("short_liquidation", "shortLiquidation", "short_liquidation_usd", "aggregated_short_liquidation_usd"),
        "liquidation_value": ("liquidation_value", "liquidationValue", "value"),
        "liquidation_price": ("liquidation_price", "liquidationPrice", "price"),
        "liquidation_size": ("liquidation_size", "liquidationSize", "size", "value"),
        "netflow": ("netflow", "netFlow", "net_flow", "net_flow_usd_24h", "net_flow_usd_12h", "net_flow_usd_1h", "net_flow_usd_1d", "value"),
        "net_inflow": ("net_inflow", "netInflow", "net_flow", "netFlow", "net_flow_usd_24h", "net_flow_usd_12h", "net_flow_usd_1h", "net_flow_usd_1d", "value"),
        "exchange_balance": ("exchange_balance", "exchangeBalance", "balance", "total_balance", "value", "c", "close"),
        "balance": ("balance", "exchange_balance", "exchangeBalance", "total_balance", "value", "c", "close"),
        "whale_transfer": ("whale_transfer", "whaleTransfer", "amount_usd", "amount", "volume", "value", "usd_value"),
        "chain": ("chain", "network", "blockchain", "blockchain_name"),
        "funding_rate": ("funding_rate", "fundingRate", "rate", "c", "close", "value"),
        "borrow_interest_rate": ("borrow_interest_rate", "borrowInterestRate", "interest_rate", "interestRate", "rate", "value"),
        "cvd": ("cvd", "cum_vol_delta", "cumVolDelta", "cumulative_volume_delta", "cumulativeVolumeDelta", "delta"),
        "event_id": ("event_id", "id", "uuid", "guid"),
        "event_type": ("event_type", "type", "category", "event", "eventType"),
        "title": ("title", "headline", "headlineText", "name", "event", "eventName", "calendar_name", "calendarName", "article_title", "articleTitle"),
        "published_at": (
            "published_at",
            "publishedAt",
            "datetime",
            "time",
            "timestamp",
            "date",
            "releaseTime",
            "publish_timestamp",
            "publishTimestamp",
            "article_release_time",
            "articleReleaseTime",
        ),
        "event_date": ("event_date", "eventDate", "date", "release_date", "releaseDate", "period", "publish_timestamp", "publishTimestamp"),
        "source": ("source", "site", "publisher", "provider", "category", "country_name", "countryName", "source_name", "sourceName"),
        "summary": ("summary", "description", "desc", "body", "content", "snippet", "text", "article_description", "articleDescription", "article_content", "articleContent"),
        "url": ("url", "link", "source_url", "sourceUrl", "reportUrl", "articleUrl"),
        "sentiment": ("sentiment", "sentiment_score", "sentimentScore", "data_list", "dataList", "value"),
        "active_addresses": ("active_addresses", "activeAddresses", "active_address_count", "activeAddressCount"),
        "sth_sopr": ("sth_sopr", "sthSopr"),
        "lth_sopr": ("lth_sopr", "lthSopr"),
        "nupl": ("nupl", "net_unpnl", "netUnpnl"),
        "stablecoin_market_cap": ("stablecoin_market_cap", "stablecoinMarketCap", "data_list", "dataList", "value"),
    }.get(field, ())
    return tuple(dict.fromkeys((field, camel, field.replace("_", ""), *aliases)))


def _is_metric_value_field(field: str) -> bool:
    return field not in {
        "date",
        "timestamp",
        "symbol_id",
        "period_start",
        "period_end",
        "market",
        "dataset",
        "granularity",
        "source_roles",
        "schema_id",
        "quality_flags",
        "value",
    } and not field.endswith("_unit")


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    if not parts:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _coerce_scalar(value: Any) -> Any:
    parsed = _decimal_float(value)
    return parsed if parsed is not None else value


def _fill_derived_metric_fields(*, row: dict[str, Any], fields: tuple[str, ...]) -> None:
    if "liquidation_value" in fields and "liquidation_value" not in row:
        long_value = _decimal_float(row.get("long_liquidation"))
        short_value = _decimal_float(row.get("short_liquidation"))
        if long_value is not None and short_value is not None:
            row["liquidation_value"] = long_value + short_value
    if "netflow" in fields and "netflow" not in row and row.get("net_inflow") is not None:
        row["netflow"] = row["net_inflow"]
    if "net_inflow" in fields and "net_inflow" not in row and row.get("netflow") is not None:
        row["net_inflow"] = row["netflow"]
    if "taker_buy_sell_ratio" in fields and "taker_buy_sell_ratio" not in row:
        taker_buy = _decimal_float(row.get("taker_buy_volume"))
        taker_sell = _decimal_float(row.get("taker_sell_volume"))
        if taker_buy is not None and taker_sell not in (None, 0):
            row["taker_buy_sell_ratio"] = taker_buy / taker_sell
    if "value" in fields and "value" not in row:
        for candidate in (
            "net_inflow",
            "netflow",
            "whale_transfer",
            "liquidation_value",
            "sentiment",
            "active_addresses",
            "sth_sopr",
            "lth_sopr",
            "nupl",
            "stablecoin_market_cap",
        ):
            if row.get(candidate) is not None:
                row["value"] = row[candidate]
                break


def _fill_catalog_metric_identity(*, row: dict[str, Any], fields: tuple[str, ...], catalog_endpoint: OfficialEndpoint | None) -> None:
    if "metric" not in fields or row.get("metric") or catalog_endpoint is None:
        return
    metric = _long_short_metric_for_endpoint(str(catalog_endpoint.endpoint_id or ""))
    if metric is None and catalog_endpoint.source_type == "coinglass":
        metric = _coinglass_onchain_metric_for_endpoint(str(catalog_endpoint.endpoint_id or ""))
    if metric:
        row["metric"] = metric


def _long_short_metric_for_endpoint(endpoint_id: str) -> str | None:
    lowered = endpoint_id.lower()
    if "top_long_short_account" in lowered or "top_long_short_account_ratio" in lowered:
        return "top_account_long_short_ratio"
    if "top_long_short_position" in lowered or "top_long_short_position_ratio" in lowered:
        return "top_position_long_short_ratio"
    if "global_long_short_account" in lowered or lowered.endswith("futures_long_short_ratio"):
        return "global_account_long_short_ratio"
    if "bitfinex_margin_long_short" in lowered:
        return "bitfinex_margin_long_short"
    if "hyperliquid" in lowered and "long_short" in lowered:
        return "hyperliquid_global_long_short_account_ratio"
    return None


def _coinglass_onchain_metric_for_endpoint(endpoint_id: str) -> str | None:
    lowered = endpoint_id.lower()
    if "bitcoin_active_addresses" in lowered:
        return "active_addresses"
    if "bitcoin_sth_sopr" in lowered:
        return "sth_sopr"
    if "bitcoin_lth_sopr" in lowered:
        return "lth_sopr"
    if "bitcoin_net_unrealized_profit_loss" in lowered:
        return "nupl"
    if "stablecoin_marketcap_history" in lowered:
        return "stablecoin_market_cap"
    return None


def _fill_event_fields(
    *,
    row: dict[str, Any],
    record: Mapping[str, Any],
    fields: tuple[str, ...],
    source_type: str,
    endpoint_id: str,
) -> None:
    if "source_type" in fields or not row.get("source_type"):
        row["source_type"] = source_type
    if "source" in fields and not row.get("source"):
        row["source"] = _coerce_scalar(_first_present(record, _field_aliases("source"))) or source_type
    if "title" in fields and not row.get("title"):
        title = _first_present(record, _field_aliases("title")) or _first_present(record, ("headline", "name", "event", "summary", "description"))
        if title is not None:
            row["title"] = _coerce_scalar(title)
    if "summary" in fields and not row.get("summary"):
        summary = _first_present(record, _field_aliases("summary"))
        if summary is not None:
            row["summary"] = _coerce_scalar(summary)
    if "url" in fields and not row.get("url"):
        url = _first_present(record, _field_aliases("url"))
        if url is not None:
            row["url"] = _coerce_scalar(url)
    if "event_type" in fields and not row.get("event_type"):
        event_type = _first_present(record, _field_aliases("event_type")) or row.get("dataset") or endpoint_id
        row["event_type"] = str(event_type)

    timestamp = _record_timestamp(record) or row.get("timestamp")
    event_date = _record_date(record)
    if isinstance(timestamp, datetime):
        row.setdefault("timestamp", timestamp)
        row.setdefault("period_start", timestamp.date())
        row.setdefault("period_end", timestamp.date())
        if "published_at" in fields:
            row["published_at"] = timestamp
        if "event_date" in fields:
            row["event_date"] = timestamp.date()
    elif event_date is not None:
        row.setdefault("date", event_date)
        row.setdefault("period_start", event_date)
        row.setdefault("period_end", event_date)
        if "timestamp" in fields and not row.get("timestamp"):
            row["timestamp"] = datetime(event_date.year, event_date.month, event_date.day, tzinfo=UTC)
        if "event_date" in fields:
            row["event_date"] = event_date
        if "published_at" in fields:
            row["published_at"] = datetime(event_date.year, event_date.month, event_date.day, tzinfo=UTC)

    if "event_id" in fields and not row.get("event_id"):
        row["event_id"] = _event_id(row=row, record=record, source_type=source_type, endpoint_id=endpoint_id)

    if source_type == "tushare" and row.get("dataset") == "sector_snapshot" and "amount_unit" in fields and not row.get("amount_unit"):
        row["amount_unit"] = "CNY" if endpoint_id == "tushare.moneyflow_ind_dc" else "CNY_100M"
    if source_type == "tushare" and row.get("dataset") == "capital_flow" and not row.get("amount_unit"):
        row["amount_unit"] = "CNY_10K"


def _event_id(*, row: Mapping[str, Any], record: Mapping[str, Any], source_type: str, endpoint_id: str) -> str:
    explicit = _first_present(record, ("event_id", "id", "uuid", "guid"))
    if explicit not in (None, ""):
        return str(explicit)
    basis = {
        "source_type": source_type,
        "endpoint_id": endpoint_id,
        "symbol_id": row.get("symbol_id"),
        "title": row.get("title"),
        "published_at": str(row.get("published_at") or row.get("timestamp") or row.get("event_date") or row.get("date") or ""),
        "url": row.get("url"),
    }
    digest = sha256(json.dumps(basis, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]
    return f"event:{source_type}:{endpoint_id}:{digest}"


def _copy_unit_defaults(*, row: dict[str, Any], params: Mapping[str, Any], fields: tuple[str, ...], quote_asset: str | None) -> None:
    if row.get("dataset") == "macro_series":
        if "series_id" in fields and not row.get("series_id"):
            series_id = _non_empty(params.get("series_id"))
            if series_id:
                row["series_id"] = series_id
        if "region" in fields and not row.get("region"):
            row["region"] = "US"
        if "unit" in fields and not row.get("unit"):
            row["unit"] = "FRED"
    if str(row.get("market")) == "CN_A" and row.get("dataset") == "valuation_metric" and "market_cap_unit" in fields and "market_cap_unit" not in row:
        row["market_cap_unit"] = "CNY_10K"
    if str(row.get("market")) == "CN_A" and row.get("dataset") == "capital_flow" and "amount_unit" not in row:
        row["amount_unit"] = "CNY_10K"
    if str(row.get("market")) == "CN_A" and row.get("dataset") == "financial_statement" and "amount_unit" in fields and "amount_unit" not in row:
        row["amount_unit"] = "CNY"
    if "open_interest_unit" in fields and "open_interest_unit" not in row:
        unit = _non_empty(params.get("unit")) or quote_asset
        if unit:
            row["open_interest_unit"] = unit
    if "funding_rate_unit" in fields and "funding_rate_unit" not in row:
        row["funding_rate_unit"] = "percent"
    if "borrow_interest_rate_unit" in fields and "borrow_interest_rate_unit" not in row:
        row["borrow_interest_rate_unit"] = "percent"
    if row.get("netflow") is not None or row.get("net_inflow") is not None:
        if "netflow_unit" in fields and "netflow_unit" not in row:
            row["netflow_unit"] = "USD"
        if "net_inflow_unit" in fields and "net_inflow_unit" not in row:
            row["net_inflow_unit"] = "USD"
        if "value_unit" in fields and "value_unit" not in row:
            row["value_unit"] = "USD"
    if row.get("etf_flow_usd") is not None and "etf_flow_usd_unit" not in row:
        row["etf_flow_usd_unit"] = "USD"


def _tushare_payload_items(payload: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        return ()
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return ()
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not isinstance(items, list):
        return ()
    rows: list[dict[str, Any]] = []
    field_names = tuple(str(field) for field in fields)
    for item in items:
        if isinstance(item, list):
            rows.append(dict(zip(field_names, item, strict=False)))
    return tuple(rows)


def _tushare_daily_rows(*, payload: Any, task: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mapped in _tushare_payload_items(payload):
        symbol = str(mapped.get("ts_code") or _first_symbol(task) or "").strip().upper()
        row_date = _parse_yyyymmdd(mapped.get("trade_date"))
        if not symbol or row_date is None:
            continue
        row = {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": symbol,
            "granularity": "daily",
            "period_start": row_date,
            "period_end": row_date,
            "date": row_date,
            "open": _decimal_float(mapped.get("open")),
            "high": _decimal_float(mapped.get("high")),
            "low": _decimal_float(mapped.get("low")),
            "close": _decimal_float(mapped.get("close")),
            "volume": _decimal_float(mapped.get("vol")),
            "amount": _amount_thousand_cny_to_cny(mapped.get("amount")),
            "amount_unit": "CNY",
            "exchange": _cn_a_exchange(symbol),
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "source_roles": ("paid_data",),
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
        }
        if any(row[field] is None for field in ("open", "high", "low", "close", "volume")):
            continue
        rows.append(row)
    return rows


def _coinglass_funding_rate_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return rows
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    granularity = _canonical_output_granularity(
        _non_empty(params.get("interval")) or _non_empty(getattr(task, "granularity", None)) or "hourly"
    ) or "hourly"
    for item in data:
        if not isinstance(item, Mapping):
            continue
        timestamp = _parse_epoch_timestamp(_first_present(item, ("time", "timestamp", "t")))
        funding_rate = _decimal_float(_first_present(item, ("funding_rate", "fundingRate", "rate", "close", "value")))
        if timestamp is None or funding_rate is None:
            continue
        period = timestamp.date()
        row = {
            "dataset": "crypto_derivative_metric",
            "market": "CRYPTO",
            "symbol_id": symbol,
            "granularity": granularity,
            "period_start": period,
            "period_end": period,
            "timestamp": timestamp,
            "funding_rate": funding_rate,
            "funding_rate_unit": "percent",
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "exchange": _non_empty(params.get("exchange")) or "COINGLASS_AGGREGATED",
            "currency": quote_asset,
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "source_roles": ("paid_data",),
            "schema_id": "crypto_derivative_metric.v1",
            "quality_flags": (),
        }
        open_value = _decimal_float(item.get("open"))
        high_value = _decimal_float(item.get("high"))
        low_value = _decimal_float(item.get("low"))
        if open_value is not None:
            row["funding_rate_open"] = open_value
        if high_value is not None:
            row["funding_rate_high"] = high_value
        if low_value is not None:
            row["funding_rate_low"] = low_value
        rows.append(row)
    return rows


def _coinglass_liquidation_heatmap_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return []
    y_axis = data.get("y_axis")
    leverage_data = data.get("liquidation_leverage_data")
    candlesticks = data.get("price_candlesticks")
    if not isinstance(y_axis, list) or not isinstance(leverage_data, list) or not isinstance(candlesticks, list):
        return []
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    rows: list[dict[str, Any]] = []
    for item in leverage_data:
        if not isinstance(item, list) or len(item) < 3:
            continue
        x_index = _int_value(item[0])
        y_index = _int_value(item[1])
        liquidation_size = _decimal_float(item[2])
        if x_index is None or y_index is None or liquidation_size is None:
            continue
        if x_index < 0 or x_index >= len(candlesticks) or y_index < 0 or y_index >= len(y_axis):
            continue
        candle = candlesticks[x_index]
        if not isinstance(candle, list) or not candle:
            continue
        timestamp = _parse_epoch_timestamp(candle[0])
        liquidation_price = _decimal_float(y_axis[y_index])
        if timestamp is None or liquidation_price is None:
            continue
        rows.append(
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": symbol,
                "granularity": "hourly",
                "period_start": timestamp.date(),
                "period_end": timestamp.date(),
                "timestamp": timestamp,
                "liquidation_price": liquidation_price,
                "liquidation_size": liquidation_size,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "exchange": _non_empty(params.get("exchange")) or "COINGLASS_AGGREGATED",
                "currency": quote_asset,
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "source_roles": ("paid_data",),
                "schema_id": "crypto_derivative_metric.v1",
                "quality_flags": (),
            }
        )
    return rows


def _coinglass_cvd_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return []
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    granularity = _canonical_output_granularity(
        _non_empty(params.get("interval")) or _non_empty(getattr(task, "granularity", None)) or "1h"
    ) or "hourly"
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        timestamp = _parse_epoch_timestamp(_first_present(item, ("time", "timestamp", "t")))
        cvd = _decimal_float(_first_present(item, ("cvd", "cum_vol_delta", "cumulativeVolumeDelta", "cumulative_volume_delta", "delta")))
        taker_buy_volume = _decimal_float(
            _first_present(
                item,
                (
                    "taker_buy_volume",
                    "agg_taker_buy_vol",
                    "aggregated_buy_volume_usd",
                    "taker_buy_volume_usd",
                    "takerBuyVolume",
                    "buy_volume",
                    "buyVolume",
                    "buy",
                ),
            )
        )
        taker_sell_volume = _decimal_float(
            _first_present(
                item,
                (
                    "taker_sell_volume",
                    "agg_taker_sell_vol",
                    "aggregated_sell_volume_usd",
                    "taker_sell_volume_usd",
                    "takerSellVolume",
                    "sell_volume",
                    "sellVolume",
                    "sell",
                ),
            )
        )
        if timestamp is None or all(value is None for value in (cvd, taker_buy_volume, taker_sell_volume)):
            continue
        period = timestamp.date()
        row: dict[str, Any] = {
            "dataset": "crypto_derivative_metric",
            "market": "CRYPTO",
            "symbol_id": symbol,
            "granularity": granularity,
            "period_start": period,
            "period_end": period,
            "timestamp": timestamp,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "exchange": _non_empty(params.get("exchange")) or "COINGLASS_AGGREGATED",
            "currency": quote_asset,
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "source_roles": ("paid_data",),
            "schema_id": "crypto_derivative_metric.v1",
            "quality_flags": (),
        }
        if cvd is not None:
            row["cvd"] = cvd
        if taker_buy_volume is not None:
            row["taker_buy_volume"] = taker_buy_volume
        if taker_sell_volume is not None:
            row["taker_sell_volume"] = taker_sell_volume
        if taker_buy_volume is not None or taker_sell_volume is not None:
            row["taker_volume_unit"] = "USD"
        rows.append(row)
    return rows


def _coinglass_etf_flow_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    rows: list[dict[str, Any]] = []
    for record in _coinglass_etf_flow_records(payload):
        timestamp = _record_timestamp(record)
        row_date = _record_date(record)
        flow = _decimal_float(
            _first_present(
                record,
                ("etf_flow_usd", "flow_usd", "flowUsd", "net_flow_usd", "netFlowUsd", "net_flow", "netFlow", "flow", "value"),
            )
        )
        price = _decimal_float(_first_present(record, ("price", "price_usd", "priceUsd", "close")))
        if flow is None and price is None:
            continue
        row: dict[str, Any] = {
            "dataset": "crypto_derivative_metric",
            "market": "CRYPTO",
            "symbol_id": symbol,
            "granularity": "daily",
            "etf_flow_usd": flow,
            "etf_flow_usd_unit": "USD" if flow is not None else None,
            "price": price,
            "price_unit": "USD" if price is not None else None,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "exchange": "COINGLASS_AGGREGATED",
            "currency": quote_asset,
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "source_roles": ("paid_data",),
            "schema_id": "crypto_derivative_metric.v1",
            "quality_flags": (),
        }
        etf_ticker = _non_empty(_first_present(record, ("etf_ticker", "etfTicker", "ticker", "symbol")))
        if etf_ticker:
            row["etf_ticker"] = etf_ticker
        if timestamp is not None:
            row["timestamp"] = timestamp
            row["period_start"] = timestamp.date()
            row["period_end"] = timestamp.date()
        elif row_date is not None:
            row["date"] = row_date
            row["period_start"] = row_date
            row["period_end"] = row_date
        else:
            continue
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _coinglass_etf_flow_records(payload: Any) -> tuple[Mapping[str, Any], ...]:
    data = payload.get("data") if isinstance(payload, Mapping) else payload
    parents = data if isinstance(data, list) else (data,)
    records: list[Mapping[str, Any]] = []
    for parent in parents:
        if not isinstance(parent, Mapping):
            continue
        parent_scalars = {str(key): value for key, value in parent.items() if _is_scalar(value)}
        flows = parent.get("etf_flows") or parent.get("etfFlows") or parent.get("flows")
        if isinstance(flows, list):
            for flow in flows:
                if isinstance(flow, Mapping):
                    records.append({**parent_scalars, **dict(flow)})
            continue
        records.append(parent_scalars or dict(parent))
    if records:
        return tuple(records)
    return _payload_records(payload)


def _coinglass_exchange_balance_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, Mapping):
        return []
    time_list = data.get("time_list")
    data_map = data.get("data_map")
    if not isinstance(time_list, list) or not isinstance(data_map, Mapping):
        return []
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(time_list):
        timestamp = _parse_epoch_timestamp(raw_time)
        if timestamp is None:
            continue
        exchange_values: dict[str, float] = {}
        for exchange_name, values in data_map.items():
            if not isinstance(values, list) or index >= len(values):
                continue
            parsed = _decimal_float(values[index])
            if parsed is not None:
                exchange_values[str(exchange_name)] = parsed
        if not exchange_values:
            continue
        balance = sum(exchange_values.values())
        rows.append(
            {
                "dataset": "crypto_onchain_metric",
                "market": "CRYPTO",
                "symbol_id": symbol,
                "granularity": "daily",
                "period_start": timestamp.date(),
                "period_end": timestamp.date(),
                "timestamp": timestamp,
                "exchange_balance": balance,
                "balance": balance,
                "value": balance,
                "metric": "exchange_balance",
                "exchange_balances": exchange_values,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "exchange": "COINGLASS_AGGREGATED",
                "currency": base_asset,
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "source_roles": ("paid_data",),
                "schema_id": "crypto_onchain_metric.v1",
                "quality_flags": (),
            }
        )
    return rows


def _coinglass_options_data_map_rows(*, payload: Any, task: Any, params: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
    records = _coinglass_data_map_records(payload)
    if not records:
        return []
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    rows: list[dict[str, Any]] = []
    for record in records:
        timestamp = _parse_any_timestamp(record.get("time"))
        value = _decimal_float(record.get("value"))
        if timestamp is None or value is None:
            continue
        row = {
            "dataset": "crypto_derivative_metric",
            "market": "CRYPTO",
            "symbol_id": symbol,
            "granularity": "hourly",
            "period_start": timestamp.date(),
            "period_end": timestamp.date(),
            "timestamp": timestamp,
            field: value,
            f"{field}_unit": _non_empty(params.get("unit")) or "USD",
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "exchange": "COINGLASS_AGGREGATED",
            "currency": quote_asset,
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "source_roles": ("paid_data",),
            "schema_id": "crypto_derivative_metric.v1",
            "quality_flags": (),
        }
        rows.append(row)
    return rows


def _coinglass_data_map_records(payload: Any) -> tuple[Mapping[str, Any], ...]:
    data = payload.get("data") if isinstance(payload, Mapping) else payload
    parents = data if isinstance(data, list) else (data,)
    records: list[dict[str, Any]] = []
    for parent in parents:
        if not isinstance(parent, Mapping):
            continue
        time_list = parent.get("time_list")
        data_map = parent.get("data_map")
        if not isinstance(time_list, list) or not isinstance(data_map, Mapping):
            continue
        for index, raw_time in enumerate(time_list):
            total = 0.0
            saw_value = False
            for values in data_map.values():
                if not isinstance(values, list) or index >= len(values):
                    continue
                value = _decimal_float(values[index])
                if value is None:
                    continue
                total += value
                saw_value = True
            if saw_value:
                records.append({"time": raw_time, "value": total})
    return tuple(records)


def _coinglass_stablecoin_marketcap_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else payload
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, Mapping):
        return []
    time_list = data.get("time_list")
    data_list = data.get("data_list")
    if not isinstance(time_list, list) or not isinstance(data_list, list):
        return []
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(time_list):
        if index >= len(data_list):
            continue
        timestamp = _parse_any_timestamp(raw_time)
        value = _stablecoin_marketcap_value(data_list[index])
        if timestamp is None or value is None:
            continue
        rows.append(
            {
                "dataset": "crypto_onchain_metric",
                "market": "CRYPTO",
                "symbol_id": symbol,
                "granularity": "daily",
                "period_start": timestamp.date(),
                "period_end": timestamp.date(),
                "timestamp": timestamp,
                "metric": "stablecoin_market_cap",
                "stablecoin_market_cap": value,
                "value": value,
                "value_unit": "USD",
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "exchange": "COINGLASS_AGGREGATED",
                "currency": quote_asset,
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "source_roles": ("paid_data",),
                "schema_id": "crypto_onchain_metric.v1",
                "quality_flags": (),
            }
        )
    return rows


def _stablecoin_marketcap_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        numbers = [parsed for parsed in (_decimal_float(item) for item in value.values()) if parsed is not None]
        return sum(numbers) if numbers else None
    return _decimal_float(value)


def _coinglass_ahr999_rows(*, payload: Any, task: Any, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[Mapping[str, Any]]
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if isinstance(data, list):
        records = [item for item in data if isinstance(item, Mapping)]
    elif isinstance(data, Mapping):
        records = [data]
    elif isinstance(payload, Mapping):
        records = [payload]
    else:
        records = []
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    rows: list[dict[str, Any]] = []
    for item in records:
        timestamp = _parse_any_timestamp(_first_present(item, ("time", "timestamp", "t", "date_string", "date", "created_at")))
        value = _decimal_float(_first_present(item, ("ahr999", "ahr999Index", "ahr999_value", "index", "current_value", "value")))
        if timestamp is None or value is None:
            continue
        rows.append(
            {
                "dataset": "crypto_onchain_metric",
                "market": "CRYPTO",
                "symbol_id": symbol,
                "granularity": "daily",
                "period_start": timestamp.date(),
                "period_end": timestamp.date(),
                "timestamp": timestamp,
                "metric": "ahr999",
                "ahr999": value,
                "value": value,
                "value_unit": "dimensionless",
                "chain": base_asset,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "exchange": "COINGLASS_AGGREGATED",
                "currency": quote_asset,
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "source_roles": ("paid_data",),
                "schema_id": "crypto_onchain_metric.v1",
                "quality_flags": (),
            }
        )
    return rows


def _coingecko_coin_id_rows(
    *,
    payload: Any,
    task: Any,
    params: Mapping[str, Any],
    catalog_endpoint: OfficialEndpoint | None,
    public_api_id: str | None,
) -> list[dict[str, Any]] | None:
    if not isinstance(payload, Mapping):
        return []
    output = _select_catalog_output(catalog_endpoint=catalog_endpoint, task=task, params=params, public_api_id=public_api_id)
    if output is None:
        return None
    data_type = str(output.get("data_type") or "").strip()
    symbol, base_asset, quote_asset = _crypto_symbol_parts(task, params)
    now = datetime.now(tz=UTC)
    row: dict[str, Any] = {
        "dataset": data_type,
        "market": "CRYPTO",
        "symbol_id": symbol,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "granularity": _selected_output_granularity(output=output, task=task, params=params),
        "timestamp": now,
        "period_start": now.date(),
        "period_end": now.date(),
        "source_roles": ("paid_data",),
        "schema_id": f"{data_type}.v1",
        "quality_flags": (),
    }
    market_data = payload.get("market_data") if isinstance(payload.get("market_data"), Mapping) else {}
    if data_type == "company_profile":
        links = payload.get("links") if isinstance(payload.get("links"), Mapping) else {}
        homepage = links.get("homepage") if isinstance(links, Mapping) else ()
        homepage_url = (
            next((_non_empty(item) for item in homepage if _non_empty(item)), None)
            if isinstance(homepage, Sequence) and not isinstance(homepage, (str, bytes, bytearray))
            else None
        )
        description = payload.get("description") if isinstance(payload.get("description"), Mapping) else {}
        row.update(
            {
                "name": _non_empty(payload.get("name")),
                "symbol": _non_empty(payload.get("symbol")),
                "description": _non_empty(description.get("en")) if isinstance(description, Mapping) else None,
                "homepage": homepage_url,
                "market_cap_rank": _int_value(payload.get("market_cap_rank")),
                "circulating_supply": _decimal_float(market_data.get("circulating_supply")),
                "total_supply": _decimal_float(market_data.get("total_supply")),
                "max_supply": _decimal_float(market_data.get("max_supply")),
                "supply_unit": base_asset,
            }
        )
        return [row] if any(row.get(key) is not None for key in ("name", "symbol", "description", "homepage", "market_cap_rank", "circulating_supply", "total_supply", "max_supply")) else []
    if data_type == "valuation_metric":
        row.update(
            {
                "price": _nested_market_data_number(market_data, "current_price", "usd"),
                "price_unit": "USD",
                "market_cap": _nested_market_data_number(market_data, "market_cap", "usd"),
                "market_cap_unit": "USD",
                "fdv": _nested_market_data_number(market_data, "fully_diluted_valuation", "usd"),
                "fdv_unit": "USD",
                "circulating_supply": _decimal_float(market_data.get("circulating_supply")),
                "total_supply": _decimal_float(market_data.get("total_supply")),
                "max_supply": _decimal_float(market_data.get("max_supply")),
                "supply_unit": base_asset,
                "volume": _nested_market_data_number(market_data, "total_volume", "usd"),
                "volume_unit": "USD",
            }
        )
        return [row] if any(row.get(key) is not None for key in ("price", "market_cap", "fdv", "circulating_supply", "total_supply", "max_supply", "volume")) else []
    return None


def _nested_market_data_number(market_data: Mapping[str, Any], key: str, currency: str) -> float | None:
    value = market_data.get(key)
    if isinstance(value, Mapping):
        return _decimal_float(value.get(currency) or value.get(currency.upper()))
    return _decimal_float(value)


def _first_present(item: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _int_value(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_epoch_timestamp(value: Any) -> datetime | None:
    parsed = _decimal_float(value)
    if parsed is None:
        return None
    if parsed > 10_000_000_000:
        parsed = parsed / 1000.0
    try:
        return datetime.fromtimestamp(parsed, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _crypto_symbol_parts(task: Any, params: Mapping[str, Any]) -> tuple[str, str, str]:
    raw_symbol = str(_first_symbol(task) or params.get("symbol") or "").strip().upper()
    if "/" in raw_symbol:
        base, quote = raw_symbol.split("/", 1)
    elif raw_symbol.endswith("USDT") and len(raw_symbol) > 4:
        base, quote = raw_symbol[:-4], "USDT"
    elif raw_symbol.endswith("USD") and len(raw_symbol) > 3:
        base, quote = raw_symbol[:-3], "USD"
    else:
        base, quote = raw_symbol or "BTC", "USDT"
    base = base.strip().upper() or "BTC"
    quote = quote.strip().upper() or "USDT"
    return f"{base}/{quote}", base, quote


def _first_symbol(task: Any) -> str | None:
    symbols = tuple(str(symbol).strip() for symbol in tuple(getattr(task, "symbol_ids", ()) or ()) if str(symbol).strip())
    return symbols[0] if symbols else None


def _parse_yyyymmdd(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _decimal_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _amount_thousand_cny_to_cny(value: Any) -> float | None:
    parsed = _decimal_float(value)
    return None if parsed is None else parsed * 1000.0


def _cn_a_exchange(symbol: str) -> str | None:
    upper = symbol.upper()
    if upper.endswith(".SH"):
        return "XSHG"
    if upper.endswith(".SZ"):
        return "XSHE"
    if upper.endswith(".BJ"):
        return "XBJE"
    return None


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
