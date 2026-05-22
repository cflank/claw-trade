from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import os
from typing import Any, Callable, Mapping, Sequence

import requests

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters
from claw_trade.data_gateway.providers.tushare_client import call_tushare_pro_bar, create_tushare_pro
from claw_trade.ui_contracts.enums import MarketProfile

_HTTP_TIMEOUT_SECONDS = 10
_COINGLASS_DEFAULT_BASE = "https://open-api-v4.coinglass.com"


@dataclass(frozen=True)
class _AdapterSpec:
    adapter: Any
    spec: Any


def build_price_alert_quote_provider(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> Callable[[str, MarketProfile], dict[str, float]]:
    env_values = dict(os.environ if env is None else env)
    clock = now_provider or (lambda: datetime.now(UTC))

    def _provider(instrument_code: str, market_profile: MarketProfile) -> dict[str, float]:
        market = _as_market(market_profile)
        request = _build_request(instrument_code=instrument_code, market=market, now=clock())
        adapter_spec = _select_adapter_spec(request=request, env=env_values)
        fetch = adapter_spec.adapter.fetch(adapter_spec.spec, request)
        rows = _extract_rows(fetch.payload)
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
    quote_provider = build_price_alert_quote_provider(env=env_values, now_provider=now_provider)

    def _tester(instance: Mapping[str, Any]) -> Mapping[str, Any]:
        supported_type = str(instance.get("supportedType", "")).strip()
        if supported_type == "tushare":
            _probe_tushare(instance=instance, env=env_values)
        elif supported_type == "coingecko":
            _probe_coingecko(instance=instance, env=env_values)
        elif supported_type == "coinglass":
            _probe_coinglass(instance=instance, env=env_values)
        else:
            raise RuntimeError("datasource_test_failed: probe_not_supported")
        return {
            "status": "validated",
            "message": "连接测试通过。",
            "impact": "low",
        }

    return _tester


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
    )
    if result is None:
        raise RuntimeError("datasource_test_failed: empty_probe")


def _probe_coingecko(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = str(
        instance.get("apiKeyReplacement")
        or env.get("COINGECKO_PRO_API_KEY", "")
        or env.get("COINGECKO_DEMO_API_KEY", "")
    ).strip()
    endpoint = str(instance.get("endpointUrl") or "https://api.coingecko.com/api/v3").strip().rstrip("/")
    proxy = str(instance.get("proxyUrl") or "").strip() or None
    headers: dict[str, str] = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key"] = api_key
    proxies = {"http": proxy, "https": proxy} if proxy else None
    response = requests.get(
        f"{endpoint}/ping",
        headers=headers,
        timeout=_HTTP_TIMEOUT_SECONDS,
        proxies=proxies,
    )
    response.raise_for_status()


def _probe_coinglass(*, instance: Mapping[str, Any], env: Mapping[str, str]) -> None:
    api_key = str(instance.get("apiKeyReplacement") or env.get("COINGLASS_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("datasource_test_failed: credential_missing")
    base = str(instance.get("endpointUrl") or env.get("COINGLASS_API_BASE", _COINGLASS_DEFAULT_BASE)).strip().rstrip("/")
    header_name = str(instance.get("headerName") or env.get("COINGLASS_API_HEADER_NAME", "")).strip() or "CG-API-KEY"
    proxy = str(instance.get("proxyUrl") or "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    response = requests.get(
        f"{base}/api/futures/open-interest/exchange-list",
        params={"symbol": "BTC"},
        headers={header_name: api_key, "accept": "application/json"},
        timeout=_HTTP_TIMEOUT_SECONDS,
        proxies=proxies,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("datasource_test_failed: invalid_payload")
    code = str(payload.get("code", "")).strip()
    if code and code not in {"0", "200"}:
        raise RuntimeError("datasource_test_failed: provider_rejected")


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
