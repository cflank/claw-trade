from __future__ import annotations

import importlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from claw_trade.ui_contracts.enums import MarketProfile

_HTTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class _HttpResponse:
    payload: object
    status_code: int

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self) -> object:
        return self.payload


class _Requests:
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout: float = _HTTP_TIMEOUT_SECONDS,
    ) -> _HttpResponse:
        target = url
        if params:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(target, headers=dict(headers or {"accept": "application/json"}))
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body.strip() else {}
            return _HttpResponse(payload=payload, status_code=int(response.status))


requests = _Requests()


def build_price_alert_quote_provider(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], object] | None = None,
    **evidence_chain: object,
) -> Callable[[str, MarketProfile], dict[str, float]]:
    del env, now_provider
    required = ("evidence_helper", "cache_store", "rate_limit_store", "single_flight", "attempt_store")

    def _provider(instrument_code: str, market_profile: MarketProfile) -> dict[str, float]:
        del instrument_code, market_profile
        if any(evidence_chain.get(key) is None for key in required):
            raise RuntimeError("datasource_test_failed: evidence_chain_unavailable")
        raise RuntimeError("datasource_test_failed: price_alert_quote_provider_unimplemented")

    return _provider


def build_data_source_health_tester(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], object] | None = None,
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    env_values = dict(env or {})
    del now_provider

    def _tester(instance: Mapping[str, Any]) -> Mapping[str, Any]:
        supported_type = str(instance.get("supportedType", "")).strip()
        if supported_type == "tushare":
            _probe_tushare(instance=instance, env=env_values)
        elif supported_type == "akshare":
            _probe_akshare(instance=instance)
        elif supported_type == "finnhub":
            _probe_finnhub(instance=instance, env=env_values)
        elif supported_type == "fred":
            _probe_fred(instance=instance, env=env_values)
        elif supported_type == "coingecko":
            _probe_coingecko(instance=instance, env=env_values)
        elif supported_type == "coingecko_pro":
            _probe_coingecko_pro(instance=instance, env=env_values)
        elif supported_type == "coinglass":
            _probe_coinglass(instance=instance, env=env_values)
        elif supported_type == "glassnode":
            _probe_glassnode(instance=instance, env=env_values)
        elif supported_type == "binance":
            _probe_binance(instance=instance, env=env_values)
        elif supported_type == "okx":
            _probe_okx(instance=instance, env=env_values)
        else:
            raise RuntimeError("datasource_test_failed: probe_not_supported")
        return _probe_only_success(supported_type)

    return _tester


def _probe_only_success(supported_type: str) -> Mapping[str, Any]:
    return {
        "status": "validated",
        "message": "连接测试通过。",
        "impact": "low",
        "requestKind": "ui_probe",
        "consumerType": "ui_probe",
        "dataRequirement": {
            "requirementId": f"ui_probe:{supported_type}:connection",
            "dataType": "provider_connection_probe",
            "requiredLevel": "optional",
            "consumerType": "ui_probe",
            "consumerId": f"settings:{supported_type}",
            "domain": "market",
        },
        "evidence": {
            "kind": "probe_only",
            "probeOnly": True,
            "mainChainEvidence": False,
            "remoteSuccess": False,
        },
    }


def create_tushare_pro(*, token: str, endpoint_url: str | None = None) -> object:
    module = importlib.import_module("tushare")
    factory = getattr(module, "pro_api", None)
    if not callable(factory):
        raise RuntimeError("datasource_test_failed: probe_missing_capability")
    api = factory(token)
    endpoint = _optional_text(endpoint_url)
    if endpoint:
        setattr(api, "_DataApi__http_url", endpoint)
    return api


def call_tushare_pro_bar(
    *,
    api: object,
    ts_code: str,
    start_date: str,
    end_date: str,
    adj: str,
    limit: int,
) -> object:
    del adj
    daily = getattr(api, "daily", None)
    if not callable(daily):
        raise RuntimeError("datasource_test_failed: probe_missing_capability")
    return daily(ts_code=ts_code, start_date=start_date, end_date=end_date, limit=limit)


def _probe_tushare(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    token = str(instance.get("apiKeyReplacement") or "").strip()
    if not token:
        raise RuntimeError("datasource_test_failed: credential_missing")
    endpoint_url = str(instance.get("endpointUrl") or "").strip()
    today = date.today()
    start = (today - timedelta(days=14)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    api = create_tushare_pro(token=token, endpoint_url=endpoint_url or None)
    result = call_tushare_pro_bar(
        api=api,
        ts_code="000001.SZ",
        start_date=start,
        end_date=end,
        adj="qfq",
        limit=1,
    )
    if _is_empty_object(result):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_akshare(*, instance: Mapping[str, Any]) -> None:
    del instance
    module = importlib.import_module("akshare")
    fetch = getattr(module, "tool_trade_date_hist_sina", None)
    if not callable(fetch):
        raise RuntimeError("datasource_test_failed: probe_missing_capability")
    if _is_empty_object(fetch()):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_finnhub(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    token = _required_credential(instance=instance)
    base = _resolve_base_url(instance=instance, default="https://finnhub.io/api/v1")
    payload = _request_json(f"{base}/quote", params={"symbol": "AAPL", "token": token})
    if not isinstance(payload, Mapping) or _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    try:
        current = float(payload.get("c"))
    except (TypeError, ValueError):
        raise RuntimeError("datasource_test_failed: invalid_payload") from None
    if current <= 0:
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_fred(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    api_key = _required_credential(instance=instance)
    base = _resolve_base_url(instance=instance, default="https://api.stlouisfed.org")
    payload = _request_json(
        f"{base}/fred/series/observations",
        params={"series_id": "DGS10", "api_key": api_key, "file_type": "json", "limit": 1},
    )
    observations = payload.get("observations") if isinstance(payload, Mapping) else None
    if not isinstance(payload, Mapping) or _mapping_contains_error(payload) or _is_empty_object(observations):
        raise RuntimeError("datasource_test_failed: provider_rejected")


def _probe_coingecko(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    api_key = str(instance.get("apiKeyReplacement") or "").strip()
    endpoint = str(instance.get("endpointUrl") or "https://api.coingecko.com/api/v3").strip().rstrip("/")
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    payload = _request_json(f"{endpoint}/ping", headers=headers)
    if not isinstance(payload, Mapping) or _mapping_contains_error(payload) or _is_empty_object(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")


def _probe_coingecko_pro(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    api_key = _required_credential(instance=instance)
    base = _resolve_base_url(instance=instance, default="https://pro-api.coingecko.com/api/v3")
    payload = _request_json(f"{base}/ping", headers={"accept": "application/json", "x-cg-pro-api-key": api_key})
    if not isinstance(payload, Mapping) or _mapping_contains_error(payload) or _is_empty_object(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")


def _probe_coinglass(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    api_key = _required_credential(instance=instance)
    base = _resolve_base_url(instance=instance, default="https://open-api-v4.coinglass.com")
    if "proxy/coinglass" in base and not base.rstrip("/").endswith("/v4") and "/v4/" not in base:
        base = f"{base.rstrip('/')}/v4"
    header_name = str(instance.get("headerName") or "CG-API-KEY").strip() or "CG-API-KEY"
    payload = _request_json(
        f"{base}/api/futures/open-interest/exchange-list",
        headers={"accept": "application/json", header_name: api_key},
        params={"symbol": "BTC"},
    )
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(payload, Mapping) or _mapping_contains_error(payload) or _is_empty_object(data):
        raise RuntimeError("datasource_test_failed: provider_rejected")


def _probe_glassnode(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    api_key = _required_credential(instance=instance)
    base = _resolve_base_url(instance=instance, default="https://api.glassnode.com")
    payload = _request_json(
        f"{base}/v1/metrics/addresses/active_count",
        params={
            "a": "BTC",
            "i": "24h",
            "f": "json",
            "api_key": api_key,
        },
    )
    if isinstance(payload, Mapping) and _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_binance(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    base = str(instance.get("endpointUrl") or "https://api.binance.com").strip().rstrip("/")
    if not base:
        raise RuntimeError("datasource_test_failed: invalid_endpoint")
    response = requests.get(f"{base}/api/v3/ping", headers={"accept": "application/json"}, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()


def _probe_okx(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    del env
    base = str(instance.get("endpointUrl") or "https://www.okx.com").strip().rstrip("/")
    if not base:
        raise RuntimeError("datasource_test_failed: invalid_endpoint")
    payload = _request_json(f"{base}/api/v5/public/time", headers={"accept": "application/json"})
    if not isinstance(payload, Mapping) or str(payload.get("code", "")).strip() not in {"", "0"}:
        raise RuntimeError("datasource_test_failed: provider_rejected")


def _request_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
) -> Any:
    response = requests.get(url, headers=dict(headers or {"accept": "application/json"}), params=dict(params or {}), timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _required_credential(*, instance: Mapping[str, Any]) -> str:
    provided = _optional_text(instance.get("apiKeyReplacement"))
    if provided:
        return provided
    raise RuntimeError("datasource_test_failed: credential_missing")


def _resolve_base_url(*, instance: Mapping[str, Any], default: str) -> str:
    endpoint = _optional_text(instance.get("endpointUrl"))
    if endpoint:
        return endpoint.rstrip("/")
    return default.rstrip("/")


def _mapping_contains_error(payload: Mapping[str, Any]) -> bool:
    for key in ("error", "errors", "error_message", "errorMessage", "Error Message", "Note", "Information", "message", "detail", "quandl_error"):
        value = payload.get(key)
        if isinstance(value, Mapping) and value:
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) > 0:
            return True
        if _optional_text(value):
            return True
    status = payload.get("status")
    if isinstance(status, Mapping):
        if _optional_text(status.get("error_message")):
            return True
        code = status.get("error_code")
        try:
            if code is not None and int(code) != 0:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_empty_object(value: Any) -> bool:
    if value is None:
        return True
    empty = getattr(value, "empty", None)
    if isinstance(empty, bool):
        return empty
    if isinstance(value, Mapping):
        return len(value) == 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value) == 0
    try:
        return len(value) == 0  # type: ignore[arg-type]
    except Exception:
        return False
