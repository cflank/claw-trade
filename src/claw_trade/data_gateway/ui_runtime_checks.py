from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from claw_trade.data_gateway.models import (
    ConsumerType,
    FreshnessPolicy,
    Market,
    PackDomain,
    PackRequest,
    ProviderStatus,
    RequestKind,
    RequiredLevel,
    utc_now_iso,
)
from claw_trade.data_gateway.providers import managed_requests
from claw_trade.data_gateway.providers import managed_requests as requests
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.providers.gate import run_provider_call_gate
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters
from claw_trade.data_gateway.providers.tushare_client import (
    call_tushare_pro_bar,
    create_tushare_pro,
)
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.single_flight import MongoSingleFlightCoordinator
from claw_trade.ui_contracts.enums import MarketProfile

_HTTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class _AdapterSpec:
    adapter: Any
    spec: Any


def build_price_alert_quote_provider(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], datetime] | None = None,
    evidence_helper: ProviderExecutionEvidenceHelper | None = None,
    cache_store: MongoCacheStore | None = None,
    rate_limit_store: MongoRateLimitStore | None = None,
    single_flight: MongoSingleFlightCoordinator | None = None,
    attempt_store: MongoAttemptStore | None = None,
) -> Callable[[str, MarketProfile], dict[str, float]]:
    env_values = dict(os.environ if env is None else env)
    clock = now_provider or (lambda: datetime.now(UTC))

    def _provider(instrument_code: str, market_profile: MarketProfile) -> dict[str, float]:
        if (
            evidence_helper is None
            or cache_store is None
            or rate_limit_store is None
            or single_flight is None
            or attempt_store is None
        ):
            raise RuntimeError("datasource_test_failed: evidence_chain_unavailable")
        market = _as_market(market_profile)
        request = _build_request(instrument_code=instrument_code, market=market, now=clock())
        adapter_spec = _select_adapter_spec(request=request, env=env_values)
        owner_rows: tuple[Mapping[str, Any], ...] = ()

        def _owner_call():
            nonlocal owner_rows
            result = evidence_helper.execute(
                request=request,
                spec=adapter_spec.spec,
                adapter=adapter_spec.adapter,
                started_at=utc_now_iso(),
            )
            owner_rows = tuple(result.rows)
            return result

        result = run_provider_call_gate(
            request=request,
            spec=adapter_spec.spec,
            cache_store=cache_store,
            rate_limit_store=rate_limit_store,
            single_flight=single_flight,
            attempt_store=attempt_store,
            owner_call=_owner_call,
        )
        if result.status != ProviderStatus.REMOTE_SUCCESS or not result.remote_success:
            raise RuntimeError(f"datasource_test_failed: {result.status.value}")
        if not result.raw_payload_ref or not result.normalized_ref:
            raise RuntimeError("datasource_test_failed: evidence_refs_missing")
        rows = tuple(result.rows) or owner_rows
        if not rows:
            raise RuntimeError("datasource_test_failed: empty_quote")
        current_price = _pick_float(
            rows[-1],
            "current_price",
            "currentPrice",
            "latest_price",
            "last_price",
            "lastPrice",
            "price",
            "最新价",
            "最新",
            "close",
            "Close",
            "收盘",
            "last",
            "Last",
        )
        if current_price is None:
            raise RuntimeError("datasource_test_failed: current_price_missing")
        percent_change = _extract_percent_change(rows=rows, current_price=current_price)
        if percent_change is None:
            raise RuntimeError("datasource_test_failed: percent_change_missing")
        quote = {
            "current_price": current_price,
            "percent_change": percent_change,
        }
        if market == Market.CRYPTO:
            quote["percent_change_24h"] = percent_change
        return quote

    return _provider


def build_data_source_health_tester(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    env_values = dict(os.environ if env is None else env)
    _ = now_provider

    def _tester(instance: Mapping[str, Any]) -> Mapping[str, Any]:
        supported_type = str(instance.get("supportedType", "")).strip()
        if supported_type == "tushare":
            _probe_tushare(instance=instance, env=env_values)
        elif supported_type == "akshare":
            _probe_akshare(instance=instance)
        elif supported_type == "alpha_vantage":
            _probe_alpha_vantage(instance=instance, env=env_values)
        elif supported_type == "fmp":
            _probe_fmp(instance=instance, env=env_values)
        elif supported_type == "polygon":
            _probe_polygon(instance=instance, env=env_values)
        elif supported_type == "finnhub":
            _probe_finnhub(instance=instance, env=env_values)
        elif supported_type == "tiingo":
            _probe_tiingo(instance=instance, env=env_values)
        elif supported_type == "nasdaq_data_link":
            _probe_nasdaq_data_link(instance=instance, env=env_values)
        elif supported_type == "coingecko":
            _probe_coingecko(instance=instance, env=env_values)
        elif supported_type == "coingecko_pro":
            _probe_coingecko_pro(instance=instance, env=env_values)
        elif supported_type == "coinmarketcap":
            _probe_coinmarketcap(instance=instance, env=env_values)
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
        "requestKind": RequestKind.UI_PROBE.value,
        "consumerType": ConsumerType.UI_PROBE.value,
        "dataRequirement": {
            "requirementId": f"ui_probe:{supported_type}:connection",
            "dataType": "provider_connection_probe",
            "requiredLevel": RequiredLevel.OPTIONAL.value,
            "consumerType": ConsumerType.UI_PROBE.value,
            "consumerId": f"settings:{supported_type}",
            "domain": PackDomain.MARKET.value,
        },
        "evidence": {
            "kind": "probe_only",
            "probeOnly": True,
            "mainChainEvidence": False,
            "remoteSuccess": False,
        },
    }


def _probe_tushare(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    token = str(instance.get("apiKeyReplacement") or env.get("TUSHARE_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("datasource_test_failed: credential_missing")
    endpoint_url = str(instance.get("endpointUrl") or env.get("TUSHARE_HTTP_URL", "")).strip()
    probe_env = dict(env)
    probe_env["TUSHARE_TOKEN"] = token
    if endpoint_url:
        probe_env["TUSHARE_HTTP_URL"] = endpoint_url
    today = date.today()
    start = (today - timedelta(days=14)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    pro = create_tushare_pro(token=token, env=probe_env)
    result = call_tushare_pro_bar(
        api=pro,
        ts_code="000001.SZ",
        start_date=start,
        end_date=end,
        adj="qfq",
        limit=1,
        env=probe_env,
    )
    if result is None:
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_akshare(*, instance: Mapping[str, Any]) -> None:
    _ = instance
    module = importlib.import_module("akshare")
    fetch = getattr(module, "tool_trade_date_hist_sina", None)
    if not callable(fetch):
        raise RuntimeError("datasource_test_failed: probe_missing_capability")
    result = fetch()
    if _is_empty_object(result):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_alpha_vantage(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = _required_credential(
        instance=instance,
        env=env,
        env_keys=("ALPHA_VANTAGE_API_KEY",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("ALPHA_VANTAGE_BASE_URL",),
        default="https://www.alphavantage.co",
    )
    payload = _request_json(
        f"{base}/query",
        params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": api_key},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    for key in ("Error Message", "Note", "Information"):
        if str(payload.get(key, "")).strip():
            raise RuntimeError("datasource_test_failed: provider_rejected")
    quote = payload.get("Global Quote")
    if not isinstance(quote, Mapping) or not quote:
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_fmp(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = _required_credential(
        instance=instance,
        env=env,
        env_keys=("FMP_API_KEY",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("FMP_BASE_URL",),
        default="https://financialmodelingprep.com/api/v3",
    )
    payload = _request_json(f"{base}/profile/AAPL", params={"apikey": api_key})
    if isinstance(payload, Mapping):
        if _mapping_contains_error(payload):
            raise RuntimeError("datasource_test_failed: provider_rejected")
        if not payload:
            raise RuntimeError("datasource_test_failed: empty_probe")
        return
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        if len(payload) == 0:
            raise RuntimeError("datasource_test_failed: empty_probe")
        return
    raise RuntimeError("datasource_test_failed: invalid_payload")


def _probe_polygon(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = _required_credential(
        instance=instance,
        env=env,
        env_keys=("POLYGON_API_KEY",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("POLYGON_BASE_URL",),
        default="https://api.polygon.io",
    )
    payload = _request_json(
        f"{base}/v3/reference/tickers/AAPL",
        params={"apiKey": api_key},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    status = str(payload.get("status", "")).strip().lower()
    if status and status not in {"ok", "success"}:
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload.get("results")):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_finnhub(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    token = _required_credential(
        instance=instance,
        env=env,
        env_keys=("FINNHUB_TOKEN",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("FINNHUB_BASE_URL",),
        default="https://finnhub.io/api/v1",
    )
    payload = _request_json(
        f"{base}/quote",
        params={"symbol": "AAPL", "token": token},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    try:
        current = float(payload.get("c"))
    except (TypeError, ValueError):
        raise RuntimeError("datasource_test_failed: invalid_payload") from None
    if current <= 0:
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_tiingo(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    token = _required_credential(
        instance=instance,
        env=env,
        env_keys=("TIINGO_TOKEN",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("TIINGO_BASE_URL",),
        default="https://api.tiingo.com",
    )
    payload = _request_json(
        f"{base}/tiingo/daily/AAPL",
        headers={"Authorization": f"Token {token}", "accept": "application/json"},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_nasdaq_data_link(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = _required_credential(
        instance=instance,
        env=env,
        env_keys=("NASDAQ_DATA_LINK_API_KEY",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("NASDAQ_DATA_LINK_BASE_URL",),
        default="https://data.nasdaq.com/api/v3",
    )
    payload = _request_json(
        f"{base}/datasets.json",
        params={"per_page": 1, "api_key": api_key},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload.get("datasets")):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_coingecko(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = str(
        instance.get("apiKeyReplacement")
        or env.get("COINGECKO_DEMO_API_KEY", "")
    ).strip()
    endpoint = str(instance.get("endpointUrl") or "https://api.coingecko.com/api/v3").strip().rstrip("/")
    headers: dict[str, str] = {"accept": "application/json"}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    response = managed_requests.get(
        f"{endpoint}/ping",
        headers=headers,
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_coingecko_pro(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = _required_credential(
        instance=instance,
        env=env,
        env_keys=("COINGECKO_PRO_API_KEY",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("COINGECKO_PRO_BASE_URL",),
        default="https://pro-api.coingecko.com/api/v3",
    )
    response = managed_requests.get(
        f"{base}/ping",
        headers={"accept": "application/json", "x-cg-pro-api-key": api_key},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if _mapping_contains_error(payload):
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_coinmarketcap(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = _required_credential(
        instance=instance,
        env=env,
        env_keys=("CMC_PRO_API_KEY",),
    )
    base = _resolve_base_url(
        instance=instance,
        env=env,
        env_keys=("COINMARKETCAP_BASE_URL",),
        default="https://pro-api.coinmarketcap.com",
    )
    payload = _request_json(
        f"{base}/v1/key/info",
        headers={"X-CMC_PRO_API_KEY": api_key, "accept": "application/json"},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    status = payload.get("status")
    if not isinstance(status, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    try:
        error_code = int(status.get("error_code"))
    except (TypeError, ValueError):
        raise RuntimeError("datasource_test_failed: invalid_payload") from None
    if error_code != 0:
        raise RuntimeError("datasource_test_failed: provider_rejected")
    if _is_empty_object(payload.get("data")):
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_binance(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    base = str(instance.get("endpointUrl") or env.get("BINANCE_SPOT_BASE_URL") or "https://api.binance.com").strip().rstrip("/")
    if not base:
        raise RuntimeError("datasource_test_failed: invalid_endpoint")
    response = managed_requests.get(
        f"{base}/api/v3/ping",
        headers={"accept": "application/json"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_okx(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    base = str(instance.get("endpointUrl") or env.get("OKX_API_BASE_URL") or "https://www.okx.com").strip().rstrip("/")
    if not base:
        raise RuntimeError("datasource_test_failed: invalid_endpoint")
    response = managed_requests.get(
        f"{base}/api/v5/public/time",
        headers={"accept": "application/json"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    if str(payload.get("code", "")).strip() not in {"", "0"}:
        raise RuntimeError("datasource_test_failed: provider_rejected")


def _required_credential(
    *,
    instance: Mapping[str, Any],
    env: Mapping[str, str],
    env_keys: Sequence[str],
) -> str:
    provided = _optional_text(instance.get("apiKeyReplacement"))
    if provided:
        return provided
    for key in env_keys:
        candidate = _optional_text(env.get(key))
        if candidate:
            return candidate
    raise RuntimeError("datasource_test_failed: credential_missing")


def _resolve_base_url(
    *,
    instance: Mapping[str, Any],
    env: Mapping[str, str],
    env_keys: Sequence[str],
    default: str,
) -> str:
    endpoint = _optional_text(instance.get("endpointUrl"))
    if endpoint:
        return endpoint.rstrip("/")
    for key in env_keys:
        candidate = _optional_text(env.get(key))
        if candidate:
            return candidate.rstrip("/")
    return default.rstrip("/")


def _request_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
) -> Any:
    response = managed_requests.get(
        url,
        headers=dict(headers or {"accept": "application/json"}),
        params=dict(params or {}),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _mapping_contains_error(payload: Mapping[str, Any]) -> bool:
    for key in (
        "error",
        "errors",
        "error_message",
        "errorMessage",
        "Error Message",
        "Note",
        "Information",
        "message",
        "detail",
        "quandl_error",
    ):
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


def _as_market(profile: MarketProfile) -> Market:
    mapping = {
        MarketProfile.CN_A: Market.CN_A,
        MarketProfile.HK: Market.HK,
        MarketProfile.US: Market.US,
        MarketProfile.CRYPTO: Market.CRYPTO,
    }
    if profile not in mapping:
        raise RuntimeError("datasource_test_failed: unsupported_market")
    return mapping[profile]


def _build_request(*, instrument_code: str, market: Market, now: datetime) -> PackRequest:
    end_date = now.astimezone(UTC).date()
    start_date = end_date - timedelta(days=7)
    return PackRequest(
        run_id=f"ui-alert-{int(now.timestamp())}",
        call_id=f"ui-alert-{instrument_code.strip().upper()}",
        worker_id="price_alert_service",
        market=market,
        domain=PackDomain.MARKET,
        ticker=instrument_code.strip().upper(),
        company_name=instrument_code.strip().upper(),
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        current_date=end_date.isoformat(),
        currency=_default_currency(market),
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _default_currency(market: Market) -> str:
    if market == Market.CN_A:
        return "CNY"
    if market == Market.HK:
        return "HKD"
    if market == Market.CRYPTO:
        return "USDT"
    return "USD"


def _select_adapter_spec(*, request: PackRequest, env: Mapping[str, str]) -> _AdapterSpec:
    adapters = tuple(item for item in build_default_market_adapters(provider_config_version="ui-runtime", env=env) if item.market == request.market)
    candidate_specs: list[_AdapterSpec] = []
    for adapter in adapters:
        for spec in adapter.build_call_specs(request):
            candidate_specs.append(_AdapterSpec(adapter=adapter, spec=spec))
    endpoint_priority = _endpoint_priority(request.market)
    for endpoint in endpoint_priority:
        for item in candidate_specs:
            if str(item.spec.endpoint) == endpoint:
                return item
    raise RuntimeError("datasource_test_failed: no_market_adapter_spec")


def _endpoint_priority(market: Market) -> tuple[str, ...]:
    if market == Market.CN_A:
        return ("quote", "stock_zh_a_hist", "daily")
    if market == Market.HK:
        return ("quote", "stock_hk_daily", "hk_daily")
    if market == Market.US:
        return ("equity_price_historical",)
    if market == Market.CRYPTO:
        return ("crypto_price_historical",)
    return ()


def _extract_rows(payload: bytes | str | Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        data = payload
    else:
        raise RuntimeError("datasource_test_failed: payload_not_mapping")
    rows = data.get("rows")
    if rows is None:
        rows = data.get("data")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in rows if isinstance(item, Mapping))


def _extract_percent_change(*, rows: Sequence[Mapping[str, Any]], current_price: float) -> float | None:
    latest = rows[-1]
    from_field = _pick_float(
        latest,
        "percent_change_24h",
        "percentChange24h",
        "percent_change",
        "percentChange",
        "change_percent",
        "changePercent",
        "pct_chg",
        "涨跌幅",
        "change",
    )
    if from_field is not None:
        return from_field
    if len(rows) >= 2:
        previous = _pick_float(
            rows[-2],
            "current_price",
            "currentPrice",
            "latest_price",
            "last_price",
            "lastPrice",
            "price",
            "close",
            "Close",
            "收盘",
        )
        if previous and previous != 0:
            return ((current_price - previous) / previous) * 100.0
    open_value = _pick_float(latest, "open", "Open", "开盘")
    if open_value and open_value != 0:
        return ((current_price - open_value) / open_value) * 100.0
    return None


def _pick_float(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        for actual, value in row.items():
            if str(actual).lower() != key.lower():
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                break
    return None
