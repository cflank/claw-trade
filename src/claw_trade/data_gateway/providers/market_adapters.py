from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import os
import re
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    Market,
    NormalizedResult,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderCallSpec,
    ProviderCapability,
    ProviderFetch,
    ProviderKind,
    ProviderStatus,
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.tushare_client import call_tushare_pro_bar, create_tushare_pro

_HTTP_TIMEOUT_SECONDS = 15
_COINGLASS_DEFAULT_BASE = "https://open-api-v4.coinglass.com"
_COINGLASS_METRIC_FIELDS = (
    "value",
    "v",
    "close",
    "ahr999",
    "ahr999_value",
    "ahr999_index",
    "index",
    "funding",
    "funding_rate",
    "fundingRate",
    "open_interest",
    "openInterest",
    "open_interest_usd",
    "open_interest_quantity",
    "open_interest_by_stable_coin_margin",
    "long_short_ratio",
    "longShortRatio",
    "global_account_long_short_ratio",
    "sopr",
    "sth_sopr",
    "lth_sopr",
    "nupl",
    "net_unpnl",
    "count",
    "addresses",
    "active_address_count",
)
_COINGLASS_BALANCE_FIELDS = ("balance", "value", "amount", "total_balance", "total", "reserve")
_COINGLASS_NETFLOW_FIELDS = ("netflow_usd", "net_flow_usd", "netflow", "net_flow", "flow", "value", "amount", "volume")
_COINGLASS_STABLECOINS = ("USDT", "USDC")
_COINGLASS_DEFAULT_EXCHANGES = "Binance,OKX,Bybit,Bitget,Gate"
_FRED_BASE = "https://api.stlouisfed.org/fred"
_TAVILY_BASE = "https://api.tavily.com"
_CRYPTO_VEGAS_PURPLE_MIN_DAILY_CANDLES = 676
_CRYPTO_TECHNICAL_LOOKBACK_DAYS = _CRYPTO_VEGAS_PURPLE_MIN_DAILY_CANDLES + 30
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "xapikey",
        "apikey",
        "key",
        "token",
        "accesstoken",
        "secret",
        "authorization",
        "auth",
    }
)
_URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")


def normalize_hk_symbol_for_stock_hk_daily(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.endswith(".HK"):
        token = token[: -len(".HK")]
    token = token.strip()
    if token.isdigit() and len(token) < 5:
        return token.zfill(5)
    return token


@dataclass(frozen=True)
class _CapabilitySeed:
    provider: str
    adapter_id: str
    endpoint: str
    source_role: SourceRole
    expected_schema_id: str
    required: bool
    attempt_required: bool
    priority: int
    cache_ttl_seconds: int = 300
    coverage_group: str | None = None
    coverage_quorum: int | None = None
    license_policy_id: str = "personal_research"
    raw_export_policy: str = "metadata_only"


@dataclass(frozen=True)
class StaticMarketProviderAdapter:
    adapter_id: str
    provider_id: str
    adapter_kind: str
    provider_kind: ProviderKind
    market: Market
    provider_config_version: str
    capability_seeds: tuple[_CapabilitySeed, ...]
    required_env_keys: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        items: list[ProviderCapability] = []
        for seed in self.capability_seeds:
            items.append(
                ProviderCapability(
                    provider=seed.provider,
                    adapter_id=seed.adapter_id,
                    provider_kind=self.provider_kind,
                    market=self.market,
                    domain=PackDomain.MARKET,
                    endpoint=seed.endpoint,
                    source_role=seed.source_role,
                    expected_schema_id=seed.expected_schema_id,
                    license_policy_id=seed.license_policy_id,
                    credential_requirements=self.required_env_keys,
                    rate_limit_policy_id=f"{seed.provider}.{seed.endpoint}",
                    cache_ttl_seconds=seed.cache_ttl_seconds,
                    required=seed.required,
                    attempt_required=seed.attempt_required,
                    coverage_group=seed.coverage_group,
                    coverage_quorum=seed.coverage_quorum,
                    priority=seed.priority,
                    priority_source=PrioritySource.SYSTEM_DEFAULT,
                    raw_export_policy=seed.raw_export_policy,
                )
            )
        return tuple(items)

    def validate_credentials(self) -> CredentialStatus:
        env = os.environ if self.env is None else self.env
        missing = tuple(key for key in self.required_env_keys if not str(env.get(key, "")).strip())
        if missing:
            return CredentialStatus(
                status=AdmissionCheckStatus.MISSING,
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                missing_keys=missing,
                root_cause=f"missing credential keys: {', '.join(missing)}",
            )
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest) -> tuple[ProviderCallSpec, ...]:
        specs: list[ProviderCallSpec] = []
        for capability in sorted(self.capabilities(), key=lambda item: (item.priority, item.provider, item.endpoint)):
            if (
                request.market == Market.CRYPTO
                and capability.endpoint == "ahr999_index"
                and _asset_from_crypto_symbol(_normalize_crypto_symbol_for_openbb(request.ticker)) != "BTC"
            ):
                continue
            specs.append(
                ProviderCallSpec(
                    call_key=f"{capability.domain.value}:{capability.adapter_id}:{capability.endpoint}",
                    provider=capability.provider,
                    adapter_id=capability.adapter_id,
                    provider_kind=capability.provider_kind,
                    provider_config_version=self.provider_config_version,
                    endpoint=capability.endpoint,
                    source_role=capability.source_role,
                    market=request.market,
                    domain=request.domain,
                    required=capability.required,
                    attempt_required=capability.attempt_required,
                    coverage_group=capability.coverage_group,
                    coverage_quorum=capability.coverage_quorum,
                    params=_build_market_params(request=request, endpoint=capability.endpoint),
                    cache_ttl_seconds=capability.cache_ttl_seconds,
                    license_policy_id=capability.license_policy_id,
                    expected_schema_id=capability.expected_schema_id,
                    priority=capability.priority,
                    priority_source=PrioritySource.SYSTEM_DEFAULT,
                    user_preferred=False,
                    raw_export_policy=capability.raw_export_policy,
                )
            )
        return tuple(specs)

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        params = dict(spec.params)
        request_id = _stable_request_id(spec=spec, params=params)
        if request.market == Market.CN_A:
            return _fetch_cn_a(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        if request.market == Market.HK:
            return _fetch_hk(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        if request.market == Market.US:
            return _fetch_us(spec=spec, request=request, params=params, request_id=request_id)
        if request.market == Market.CRYPTO:
            return _fetch_crypto(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        raise RuntimeError(f"unsupported market: {request.market}")

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        rows = _extract_rows(fetch.payload)
        source_raw_ref = _raw_ref(spec=spec, fetch=fetch)
        if not rows:
            return NormalizedResult(
                status=ProviderStatus.EMPTY,
                schema_id=spec.expected_schema_id,
                rows=(),
                compact_facts=_compact_facts(spec=spec, row_count=0, currency=None, timezone=None),
                row_count=0,
                field_units={},
                currency=None,
                timezone=None,
                source_raw_ref=source_raw_ref,
                error_code="empty",
                error_message=f"{spec.provider}/{spec.endpoint} returned empty rows",
            )

        if spec.market == Market.CRYPTO and spec.endpoint in {
            "futures_oi_funding",
            "liquidation_heatmap",
            "onchain_signals",
            "macro_regime",
            "catalyst_events",
            "ahr999_index",
        }:
            return _normalize_crypto_domain_rows(
                spec=spec,
                rows=rows,
                source_raw_ref=source_raw_ref,
            )

        normalized_rows: list[dict[str, Any]] = []
        for item in rows:
            mapped = _normalize_ohlcv_row(item, market=spec.market)
            if mapped is not None:
                normalized_rows.append(mapped)

        if not normalized_rows:
            return NormalizedResult(
                status=ProviderStatus.FIELD_MISSING,
                schema_id=spec.expected_schema_id,
                rows=(),
                compact_facts=_compact_facts(spec=spec, row_count=0, currency=None, timezone=None),
                row_count=0,
                field_units={},
                currency=None,
                timezone=None,
                source_raw_ref=source_raw_ref,
                missing_fields=("date", "open", "high", "low", "close", "volume"),
                error_code="field_missing",
                error_message=f"{spec.provider}/{spec.endpoint} missing required OHLCV fields",
            )

        normalized_rows.sort(key=lambda row: row["date"])
        deduped: dict[str, dict[str, Any]] = {}
        for row in normalized_rows:
            deduped[row["date"]] = row
        out_rows = tuple(deduped[key] for key in sorted(deduped.keys()))
        currency = out_rows[-1].get("currency")
        timezone = out_rows[-1].get("timezone")
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id=spec.expected_schema_id,
            rows=out_rows,
            compact_facts=_compact_facts(spec=spec, row_count=len(out_rows), currency=currency, timezone=timezone),
            row_count=len(out_rows),
            field_units={"volume": "shares_or_contracts"},
            currency=str(currency) if currency is not None else None,
            timezone=str(timezone) if timezone is not None else None,
            source_raw_ref=source_raw_ref,
        )

    @property
    def _env(self) -> Mapping[str, str]:
        return os.environ if self.env is None else self.env


def build_default_market_adapters(
    *,
    provider_config_version: str,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderAdapter, ...]:
    return (
        StaticMarketProviderAdapter(
            adapter_id="project.cn_a.market",
            provider_id="cn_a_market",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CN_A,
            provider_config_version=provider_config_version,
            required_env_keys=(),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="mootdx_quote",
                    adapter_id="project.cn_a.market",
                    endpoint="stock_quote",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=True,
                    attempt_required=True,
                    coverage_group="cn_a_market_quote",
                    coverage_quorum=1,
                    priority=0,
                ),
                _CapabilitySeed(
                    provider="tencent_quote",
                    adapter_id="project.cn_a.market",
                    endpoint="quote_tencent",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="cn_a_market_quote",
                    coverage_quorum=1,
                    priority=1,
                ),
                _CapabilitySeed(
                    provider="baidu_kline",
                    adapter_id="project.cn_a.market",
                    endpoint="kline_baidu",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=True,
                    attempt_required=True,
                    coverage_group="cn_a_market_kline",
                    coverage_quorum=1,
                    priority=10,
                ),
                _CapabilitySeed(
                    provider="mootdx_orderbook",
                    adapter_id="project.cn_a.market",
                    endpoint="orderbook",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=True,
                    attempt_required=True,
                    coverage_group="cn_a_market_orderbook",
                    coverage_quorum=1,
                    priority=20,
                ),
                _CapabilitySeed(
                    provider="tencent_orderbook",
                    adapter_id="project.cn_a.market",
                    endpoint="orderbook_tencent",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="cn_a_market_orderbook",
                    coverage_quorum=1,
                    priority=21,
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.cn_a.market.tushare_fallback",
            provider_id="cn_a_market_tushare_fallback",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CN_A,
            provider_config_version=provider_config_version,
            required_env_keys=("TUSHARE_TOKEN",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="tushare_kline_fallback",
                    adapter_id="project.cn_a.market.tushare_fallback",
                    endpoint="daily",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="cn_a_market_kline",
                    coverage_quorum=1,
                    priority=12,
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.hk.market",
            provider_id="hk_market",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.HK,
            provider_config_version=provider_config_version,
            required_env_keys=(),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="tushare_hk",
                    adapter_id="project.hk.market",
                    endpoint="hk_daily",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="hk.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="hk_ohlcv",
                    coverage_quorum=1,
                    priority=0,
                ),
                _CapabilitySeed(
                    provider="akshare_hk",
                    adapter_id="project.hk.market",
                    endpoint="stock_hk_daily",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="hk.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="hk_ohlcv",
                    coverage_quorum=1,
                    priority=1,
                ),
                _CapabilitySeed(
                    provider="eastmoney_hk",
                    adapter_id="project.hk.market",
                    endpoint="quote",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="hk.market.ohlcv.v1",
                    required=False,
                    attempt_required=False,
                    priority=2,
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.us.market",
            provider_id="us_market",
            adapter_kind="openbb_native",
            provider_kind=ProviderKind.OPENBB_NATIVE,
            market=Market.US,
            provider_config_version=provider_config_version,
            required_env_keys=(),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="openbb_yfinance",
                    adapter_id="project.us.market",
                    endpoint="equity_price_historical",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="us.market.ohlcv.v1",
                    required=True,
                    attempt_required=True,
                    priority=0,
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.market",
            provider_id="crypto_market",
            adapter_kind="openbb_native",
            provider_kind=ProviderKind.OPENBB_NATIVE,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=(),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="openbb_yfinance",
                    adapter_id="project.crypto.market",
                    endpoint="crypto_price_historical",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="crypto.market.ohlcv.v1",
                    required=True,
                    attempt_required=True,
                    coverage_group="crypto_market_ohlcv",
                    coverage_quorum=1,
                    priority=0,
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.derivatives",
            provider_id="crypto_derivatives",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=("COINGLASS_API_KEY",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="coinglass",
                    adapter_id="project.crypto.derivatives",
                    endpoint="futures_oi_funding",
                    source_role=SourceRole.DERIVATIVE_MARKET_DATA,
                    expected_schema_id="crypto.market.derivatives.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="crypto_derivatives",
                    coverage_quorum=1,
                    priority=10,
                    license_policy_id="coinglass.pending_review",
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.liquidation_map",
            provider_id="crypto_liquidation_map",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=("COINGLASS_API_KEY",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="coinglass",
                    adapter_id="project.crypto.liquidation_map",
                    endpoint="liquidation_heatmap",
                    source_role=SourceRole.DERIVATIVE_MARKET_DATA,
                    expected_schema_id="crypto.market.liquidation_map.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="crypto_liquidation_map",
                    coverage_quorum=1,
                    priority=11,
                    license_policy_id="coinglass.pending_review",
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.onchain",
            provider_id="crypto_onchain",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=("COINGLASS_API_KEY",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="coinglass",
                    adapter_id="project.crypto.onchain",
                    endpoint="onchain_signals",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="crypto.market.onchain.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="crypto_onchain",
                    coverage_quorum=1,
                    priority=20,
                    license_policy_id="coinglass.pending_review",
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.macro",
            provider_id="crypto_macro",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=("FRED_API_KEY",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="fred",
                    adapter_id="project.crypto.macro",
                    endpoint="macro_regime",
                    source_role=SourceRole.MACRO_DATA,
                    expected_schema_id="crypto.market.macro.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="crypto_macro",
                    coverage_quorum=1,
                    priority=30,
                    license_policy_id="fred.pending_review",
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.events",
            provider_id="crypto_events",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=("TAVILY_API_KEY",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="tavily",
                    adapter_id="project.crypto.events",
                    endpoint="catalyst_events",
                    source_role=SourceRole.EVENT_EXPECTATION,
                    expected_schema_id="crypto.market.events.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="crypto_events",
                    coverage_quorum=1,
                    priority=40,
                    license_policy_id="tavily.pending_review",
                ),
            ),
        ),
        StaticMarketProviderAdapter(
            adapter_id="project.crypto.ahr999",
            provider_id="crypto_ahr999",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            provider_config_version=provider_config_version,
            required_env_keys=("COINGLASS_API_KEY",),
            env=env,
            capability_seeds=(
                _CapabilitySeed(
                    provider="coinglass",
                    adapter_id="project.crypto.ahr999",
                    endpoint="ahr999_index",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="crypto.market.ahr999.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="crypto_ahr999",
                    coverage_quorum=1,
                    priority=50,
                    license_policy_id="coinglass.pending_review",
                ),
            ),
        ),
    )


def _build_market_params(*, request: PackRequest, endpoint: str) -> Mapping[str, Any]:
    params: dict[str, Any] = {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
        "currency": request.currency,
        "market": request.market.value,
    }
    if request.market == Market.HK and endpoint == "stock_hk_daily":
        params["symbol"] = normalize_hk_symbol_for_stock_hk_daily(request.ticker)
        params["adjust"] = "qfq"
        params["interval"] = "daily"
        params["currency"] = "HKD"
        params["timezone"] = "Asia/Hong_Kong"
    if request.market == Market.CRYPTO and endpoint == "crypto_price_historical":
        params["symbol"] = _normalize_crypto_symbol_for_openbb(request.ticker)
        params["timezone"] = "UTC"
        technical_start_date = _crypto_technical_start_date(request.start_date, request.end_date)
        if technical_start_date != request.start_date:
            params["requested_start_date"] = request.start_date
            params["start_date"] = technical_start_date
            params["technical_lookback_reason"] = "vegas_purple_band_ema676_daily"
    if request.market == Market.CRYPTO and endpoint in {
        "futures_oi_funding",
        "liquidation_heatmap",
        "onchain_signals",
        "macro_regime",
        "catalyst_events",
        "ahr999_index",
    }:
        params["symbol"] = _normalize_crypto_symbol_for_openbb(request.ticker)
        params["timezone"] = "UTC"
    return params


def _fetch_cn_a(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    if spec.endpoint == "daily":
        token = str(env.get("TUSHARE_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("missing credential keys: TUSHARE_TOKEN")
        rows = _call_tushare_daily(
            token=token,
            ts_code=_normalize_cn_symbol_for_tushare(request.ticker),
            start_date=request.start_date,
            end_date=request.end_date,
        )
        return _build_fetch(
            provider="tushare",
            endpoint=spec.endpoint,
            source_url="https://api.tushare.pro",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.endpoint == "kline_baidu":
        rows = _call_baidu_kline_with_ma(
            symbol=_normalize_cn_symbol_for_akshare(request.ticker),
            start_date=request.start_date,
        )
        return _build_fetch(
            provider="baidu_kline",
            endpoint=spec.endpoint,
            source_url="https://finance.pae.baidu.com/selfselect/getstockquotation",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.endpoint in {"quote_tencent", "orderbook_tencent"}:
        row = _call_tencent_quote_row(
            symbol=_normalize_cn_symbol_for_tencent(request.ticker),
            fallback_date=request.current_date,
        )
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://qt.gtimg.cn",
            request_id=request_id,
            params=params,
            rows=(row,),
        )
    if spec.endpoint in {"stock_quote", "orderbook"}:
        row = _call_mootdx_quote_row(
            symbol=_normalize_cn_symbol_for_mootdx(request.ticker),
            fallback_date=request.current_date,
        )
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="tcp://mootdx:7709",
            request_id=request_id,
            params=params,
            rows=(row,),
        )
    raise RuntimeError(f"unsupported cn_a endpoint: {spec.endpoint}")


def _fetch_hk(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    if spec.endpoint == "hk_daily":
        token = str(env.get("TUSHARE_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("tushare hk_daily token missing; set TUSHARE_TOKEN or rely on the configured stock_hk_daily parallel path")
        rows = _call_tushare_hk_daily(
            token=token,
            ts_code=_normalize_hk_symbol_for_tushare(request.ticker),
            start_date=request.start_date,
            end_date=request.end_date,
        )
        return _build_fetch(
            provider="tushare_hk",
            endpoint=spec.endpoint,
            source_url="https://api.tushare.pro",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.endpoint == "stock_hk_daily":
        symbol = str(params.get("symbol") or normalize_hk_symbol_for_stock_hk_daily(request.ticker))
        rows = _call_akshare_stock_hk_daily(
            symbol=symbol,
            adjust=str(params.get("adjust") or "qfq"),
            start_date=request.start_date,
            end_date=request.end_date,
        )
        return _build_fetch(
            provider="akshare_hk",
            endpoint=spec.endpoint,
            source_url="https://akshare.akfamily.xyz/data/stock/stock.html",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.endpoint == "quote":
        rows = _call_akshare_stock_hk_spot(symbol=normalize_hk_symbol_for_stock_hk_daily(request.ticker))
        return _build_fetch(
            provider="eastmoney_hk",
            endpoint=spec.endpoint,
            source_url="https://quote.eastmoney.com/hk",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    raise RuntimeError(f"unsupported hk endpoint: {spec.endpoint}")


def _fetch_us(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
) -> ProviderFetch:
    if spec.endpoint != "equity_price_historical":
        raise RuntimeError(f"unsupported us endpoint: {spec.endpoint}")
    rows = _call_openbb_equity_price_historical(
        symbol=request.ticker.strip().upper(),
        start_date=request.start_date,
        end_date=request.end_date,
        provider="yfinance",
    )
    return _build_fetch(
        provider="openbb_yfinance",
        endpoint=spec.endpoint,
        source_url="https://query1.finance.yahoo.com",
        request_id=request_id,
        params=params,
        rows=rows,
    )


def _fetch_crypto(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    symbol = str(params.get("symbol") or _normalize_crypto_symbol_for_openbb(request.ticker))
    if spec.endpoint == "crypto_price_historical":
        rows = _call_openbb_crypto_price_historical(
            symbol=symbol,
            start_date=str(params.get("start_date") or request.start_date),
            end_date=request.end_date,
            provider="yfinance",
        )
        return _build_fetch(
            provider="openbb_yfinance",
            endpoint=spec.endpoint,
            source_url="https://query1.finance.yahoo.com",
            request_id=request_id,
            params=params,
            rows=rows,
        )

    if spec.endpoint == "futures_oi_funding":
        rows = _call_crypto_derivatives(symbol=symbol, env=env)
        source_url = _coinglass_base(env=env)
    elif spec.endpoint == "liquidation_heatmap":
        rows = _call_crypto_liquidation(symbol=symbol, env=env)
        source_url = _coinglass_base(env=env)
    elif spec.endpoint == "onchain_signals":
        rows = _call_crypto_onchain(symbol=symbol, env=env)
        source_url = _coinglass_base(env=env)
    elif spec.endpoint == "macro_regime":
        rows = _call_crypto_macro(symbol=symbol, env=env)
        source_url = _FRED_BASE
    elif spec.endpoint == "catalyst_events":
        rows = _call_crypto_events(symbol=symbol, env=env)
        source_url = _TAVILY_BASE
    elif spec.endpoint == "ahr999_index":
        rows = _call_crypto_ahr999(symbol=symbol, env=env)
        source_url = _coinglass_base(env=env)
    else:
        raise RuntimeError(f"unsupported crypto endpoint: {spec.endpoint}")

    return _build_fetch(
        provider=spec.provider,
        endpoint=spec.endpoint,
        source_url=source_url,
        request_id=request_id,
        params=params,
        rows=rows,
    )


def _crypto_technical_start_date(start_date: str, end_date: str) -> str:
    try:
        requested_start = date.fromisoformat(start_date)
        requested_end = date.fromisoformat(end_date)
    except ValueError:
        return start_date
    required_start = requested_end - timedelta(days=_CRYPTO_TECHNICAL_LOOKBACK_DAYS - 1)
    if requested_start <= required_start:
        return start_date
    return required_start.isoformat()


def _build_fetch(
    *,
    provider: str,
    endpoint: str,
    source_url: str,
    request_id: str,
    params: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> ProviderFetch:
    payload = {
        "provider": provider,
        "endpoint": endpoint,
        "request_id": request_id,
        "params": dict(params),
        "rows": list(rows),
        "row_count": len(rows),
    }
    return ProviderFetch(
        payload=payload,
        content_type="application/json",
        source_url=source_url,
        is_empty=len(rows) == 0,
        row_count=len(rows),
        provider_request_id=request_id,
    )


def _extract_rows(payload: bytes | str | Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, bytes):
        parsed = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        parsed = json.loads(payload)
    else:
        parsed = payload
    if not isinstance(parsed, Mapping):
        raise RuntimeError("provider payload must be mapping")
    raw_rows = parsed.get("rows")
    if raw_rows is None:
        data = parsed.get("data")
        if isinstance(data, Sequence):
            raw_rows = data
        else:
            raw_rows = ()
    if not isinstance(raw_rows, Sequence):
        raise RuntimeError("provider payload rows must be sequence")
    rows: list[Mapping[str, Any]] = []
    for item in raw_rows:
        if isinstance(item, Mapping):
            rows.append(item)
    return tuple(rows)


def _normalize_ohlcv_row(row: Mapping[str, Any], *, market: Market) -> dict[str, Any] | None:
    date_text = _date_text(_pick(row, "date", "trade_date", "日期"))
    open_value = _to_float(_pick(row, "open", "开盘", "今开"))
    high_value = _to_float(_pick(row, "high", "最高"))
    low_value = _to_float(_pick(row, "low", "最低"))
    close_value = _to_float(_pick(row, "close", "收盘", "最新价", "现价"))
    volume_value = _to_float(_pick(row, "volume", "vol", "成交量", "总手"))
    if not date_text or open_value is None or high_value is None or low_value is None or close_value is None or volume_value is None:
        return None
    mapped: dict[str, Any] = {
        "date": date_text,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
        "currency": str(_pick(row, "currency") or _default_currency_by_market(market)),
        "timezone": str(_pick(row, "timezone") or _default_timezone_by_market(market)),
    }
    amount_value = _to_float(_pick(row, "amount", "turnover", "成交额"))
    if amount_value is not None:
        mapped["amount"] = amount_value
    return mapped


def _compact_facts(
    *,
    spec: ProviderCallSpec,
    row_count: int,
    currency: Any,
    timezone: Any,
) -> Mapping[str, Any]:
    return {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "schema_id": spec.expected_schema_id,
        "row_count": row_count,
        "currency": currency,
        "timezone": timezone,
    }


def _normalize_crypto_domain_rows(
    *,
    spec: ProviderCallSpec,
    rows: Sequence[Mapping[str, Any]],
    source_raw_ref: str,
) -> NormalizedResult:
    normalized_rows: tuple[Mapping[str, Any], ...] = tuple(dict(row) for row in rows if isinstance(row, Mapping))
    if not normalized_rows:
        return NormalizedResult(
            status=ProviderStatus.EMPTY,
            schema_id=spec.expected_schema_id,
            rows=(),
            compact_facts=_compact_facts(spec=spec, row_count=0, currency="USD", timezone="UTC"),
            row_count=0,
            field_units={},
            currency="USD",
            timezone="UTC",
            source_raw_ref=source_raw_ref,
            error_code="empty",
            error_message=f"{spec.provider}/{spec.endpoint} returned empty rows",
        )
    return NormalizedResult(
        status=ProviderStatus.REMOTE_SUCCESS,
        schema_id=spec.expected_schema_id,
        rows=normalized_rows,
        compact_facts=_compact_facts(spec=spec, row_count=len(normalized_rows), currency="USD", timezone="UTC"),
        row_count=len(normalized_rows),
        field_units={},
        currency="USD",
        timezone="UTC",
        source_raw_ref=source_raw_ref,
    )


def _raw_ref(*, spec: ProviderCallSpec, fetch: ProviderFetch) -> str:
    request_id = fetch.provider_request_id or _stable_request_id(spec=spec, params=spec.params)
    return f"raw://{spec.provider}/{spec.endpoint}/{request_id}"


def _stable_request_id(*, spec: ProviderCallSpec, params: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        {
            "adapter_id": spec.adapter_id,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "params": dict(params),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "req_" + hashlib.sha256(rendered).hexdigest()[:24]


def _normalize_cn_symbol_for_akshare(ticker: str) -> str:
    code, _market = _normalize_cn_symbol_parts(ticker)
    return code


def _normalize_cn_symbol_for_mootdx(ticker: str) -> str:
    code, _market = _normalize_cn_symbol_parts(ticker)
    return code


def _normalize_cn_symbol_for_tushare(ticker: str) -> str:
    code, market = _normalize_cn_symbol_parts(ticker)
    if market in {"SH", "SZ", "BJ"} and code.isdigit():
        return f"{code}.{market}"
    if code.isdigit():
        if code.startswith("92") or code.startswith(("4", "8")):
            suffix = "BJ"
        elif code.startswith(("5", "6", "9")):
            suffix = "SH"
        else:
            suffix = "SZ"
        return f"{code}.{suffix}"
    if market in {"SH", "SZ", "BJ"}:
        return f"{code}.{market}"
    return code


def _normalize_cn_symbol_for_tencent(ticker: str) -> str:
    code, market = _normalize_cn_symbol_parts(ticker)
    if market == "SH":
        return f"sh{code}"
    if market == "SZ":
        return f"sz{code}"
    if market == "BJ":
        return f"bj{code}"
    if code.startswith("92") or code.startswith(("4", "8")):
        return f"bj{code}"
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _normalize_cn_symbol_parts(ticker: str) -> tuple[str, str | None]:
    token = ticker.strip().upper()
    if token.startswith(("SH", "SZ", "BJ")) and token[2:].isdigit():
        return token[2:].zfill(6), token[:2]
    if token.endswith((".SH", ".SZ", ".BJ")):
        code, market = token.rsplit(".", maxsplit=1)
        normalized_code = code.zfill(6) if code.isdigit() else code
        return normalized_code, market
    if token.isdigit() and len(token) < 6:
        token = token.zfill(6)
    return token, None


def _call_tencent_quote_row(*, symbol: str, fallback_date: str) -> Mapping[str, Any]:
    response = requests.get(
        "https://qt.gtimg.cn/q=" + symbol,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    text = response.content.decode("gbk", errors="ignore")
    matched = re.search(r'="([^"]+)"', text)
    if matched is None:
        raise RuntimeError(f"tencent quote parse failed for {symbol}")
    fields = matched.group(1).split("~")
    if len(fields) < 49:
        raise RuntimeError(f"tencent quote payload too short for {symbol}")

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": fields[1] if len(fields) > 1 else "",
        "code": fields[2] if len(fields) > 2 else symbol,
        "date": _tencent_quote_date(fields=fields, fallback_date=fallback_date),
        "close": _to_float(fields[3]),
        "last_close": _to_float(fields[4]),
        "open": _to_float(fields[5]),
        "high": _to_float(fields[33] if len(fields) > 33 else None),
        "low": _to_float(fields[34] if len(fields) > 34 else None),
        "volume": _to_float(fields[36] if len(fields) > 36 else None),
        "amount": _to_float(fields[37] if len(fields) > 37 else None),
        "turnover_pct": _to_float(fields[38] if len(fields) > 38 else None),
        "pe_ttm": _to_float(fields[39] if len(fields) > 39 else None),
        "amplitude_pct": _to_float(fields[43] if len(fields) > 43 else None),
        "mcap_yi": _to_float(fields[44] if len(fields) > 44 else None),
        "float_mcap_yi": _to_float(fields[45] if len(fields) > 45 else None),
        "pb": _to_float(fields[46] if len(fields) > 46 else None),
        "limit_up": _to_float(fields[47] if len(fields) > 47 else None),
        "limit_down": _to_float(fields[48] if len(fields) > 48 else None),
    }
    # Tencent returns bid/ask ladders in [9..28] with alternating price/volume pairs.
    for level in range(1, 6):
        bid_price_idx = 9 + (level - 1) * 2
        bid_volume_idx = bid_price_idx + 1
        ask_price_idx = 19 + (level - 1) * 2
        ask_volume_idx = ask_price_idx + 1
        row[f"bid{level}"] = _to_float(fields[bid_price_idx] if len(fields) > bid_price_idx else None)
        row[f"bid_vol{level}"] = _to_float(fields[bid_volume_idx] if len(fields) > bid_volume_idx else None)
        row[f"ask{level}"] = _to_float(fields[ask_price_idx] if len(fields) > ask_price_idx else None)
        row[f"ask_vol{level}"] = _to_float(fields[ask_volume_idx] if len(fields) > ask_volume_idx else None)

    # The market normalizer expects open/high/low/close/volume to be present.
    if row["open"] is None:
        row["open"] = row["close"]
    if row["high"] is None:
        row["high"] = row["close"]
    if row["low"] is None:
        row["low"] = row["close"]
    if row["volume"] is None:
        row["volume"] = 0.0
    return row


def _call_mootdx_quote_row(*, symbol: str, fallback_date: str) -> Mapping[str, Any]:
    rows = _call_mootdx_quotes(symbols=(symbol,))
    source_row = _select_mootdx_row(rows=rows, symbol=symbol)
    close_value = _to_float(_pick(source_row, "price", "close", "现价", "最新价", "last_price"))
    if close_value is None:
        raise RuntimeError(f"mootdx quotes row missing close/price for symbol={symbol}")
    open_value = _to_float(_pick(source_row, "open", "今开"))
    high_value = _to_float(_pick(source_row, "high", "最高"))
    low_value = _to_float(_pick(source_row, "low", "最低"))
    volume_value = _to_float(_pick(source_row, "vol", "volume", "成交量", "总手"))
    missing_fields: list[str] = []
    if open_value is None:
        missing_fields.append("open")
    if high_value is None:
        missing_fields.append("high")
    if low_value is None:
        missing_fields.append("low")
    if volume_value is None:
        missing_fields.append("volume")
    if missing_fields:
        missing_rendered = ",".join(missing_fields)
        raise RuntimeError(f"mootdx quotes row missing required fields for symbol={symbol}: {missing_rendered}")
    amount_value = _to_float(_pick(source_row, "amount", "turnover", "成交额"))
    date_text = _date_text(_pick(source_row, "date", "trade_date", "datetime", "servertime", "time")) or fallback_date
    row = dict(source_row)
    row.update(
        {
            "date": date_text,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume_value,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
        }
    )
    if amount_value is not None:
        row["amount"] = amount_value
    return row


def _call_mootdx_quotes(*, symbols: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
    try:
        from mootdx.quotes import Quotes  # type: ignore
    except Exception as exc:
        raise RuntimeError("mootdx dependency unavailable: install mootdx to enable cn_a market quote/orderbook") from exc

    try:
        client = Quotes.factory(market="std")
    except Exception as exc:
        joined = ",".join(symbols)
        raise RuntimeError(f"mootdx quotes client factory failed for symbols={joined}: {exc}") from exc
    try:
        response = client.quotes(symbol=list(symbols))
    except Exception as exc:
        joined = ",".join(symbols)
        raise RuntimeError(f"mootdx quotes request failed for symbols={joined}: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if hasattr(response, "to_dict"):
        rows = tuple(_df_to_rows(response))
    elif isinstance(response, Mapping):
        rows = (response,)
    elif isinstance(response, Sequence) and not isinstance(response, (str, bytes, bytearray)):
        rows = tuple(item for item in response if isinstance(item, Mapping))
    else:
        rows = ()
    if not rows:
        joined = ",".join(symbols)
        raise RuntimeError(f"mootdx quotes returned empty rows for symbols={joined}")
    return rows


def _select_mootdx_row(*, rows: Sequence[Mapping[str, Any]], symbol: str) -> Mapping[str, Any]:
    for row in rows:
        code = _normalize_mootdx_row_code(str(_pick(row, "code", "symbol", "证券代码", "股票代码") or ""))
        if code == symbol:
            return row
    if len(rows) == 1:
        return rows[0]
    available = [str(_pick(item, "code", "symbol", "证券代码", "股票代码") or "") for item in rows]
    raise RuntimeError(f"mootdx quotes row not found for symbol={symbol}; available={available}")


def _normalize_mootdx_row_code(value: str) -> str:
    token = value.strip().upper()
    if not token:
        return token
    code, _market = _normalize_cn_symbol_parts(token)
    return code


def _tencent_quote_date(*, fields: Sequence[str], fallback_date: str) -> str:
    # Tencent field[30] is usually a compact timestamp like YYYYMMDDHHMMSS.
    if len(fields) <= 30:
        return fallback_date
    compact = re.sub(r"\D", "", fields[30])
    if len(compact) >= 8:
        return f"{compact[0:4]}-{compact[4:6]}-{compact[6:8]}"
    return fallback_date


def _call_baidu_kline_with_ma(*, symbol: str, start_date: str) -> tuple[Mapping[str, Any], ...]:
    params = {
        "all": "1",
        "isIndex": "false",
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "true",
        "newFormat": "1",
        "group": "quotation_kline_ab",
        "finClientType": "pc",
        "code": symbol,
        "start_time": _compact_date(start_date),
        "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    payload = _http_get_json(
        "https://finance.pae.baidu.com/selfselect/getstockquotation",
        params=params,
        headers=headers,
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("baidu kline payload must be mapping")
    result = payload.get("Result")
    if not isinstance(result, Mapping):
        raise RuntimeError("baidu kline payload missing Result")
    market_data = result.get("newMarketData")
    if not isinstance(market_data, Mapping):
        raise RuntimeError("baidu kline payload missing Result.newMarketData")
    keys = market_data.get("keys")
    rows_text = market_data.get("marketData")
    if not isinstance(keys, Sequence) or isinstance(keys, (str, bytes, bytearray)):
        raise RuntimeError("baidu kline payload missing newMarketData.keys")
    if not isinstance(rows_text, str):
        raise RuntimeError("baidu kline payload missing newMarketData.marketData")

    rows: list[Mapping[str, Any]] = []
    normalized_keys = [str(item) for item in keys]
    for raw_row in rows_text.split(";"):
        line = raw_row.strip()
        if not line:
            continue
        values = line.split(",")
        if len(values) < len(normalized_keys):
            continue
        source = {normalized_keys[index]: values[index] for index in range(len(normalized_keys))}
        date_text = _date_text(source.get("time") or source.get("date")) or _date_text(source.get("trade_date"))
        if not date_text:
            continue
        row = {
            "date": date_text,
            "open": _to_float(source.get("open")),
            "high": _to_float(source.get("high")),
            "low": _to_float(source.get("low")),
            "close": _to_float(source.get("close")),
            "volume": _to_float(source.get("volume")) or 0.0,
            "amount": _to_float(source.get("amount")),
            "ma5avgprice": _to_float(source.get("ma5avgprice")),
            "ma10avgprice": _to_float(source.get("ma10avgprice")),
            "ma20avgprice": _to_float(source.get("ma20avgprice")),
        }
        rows.append(row)
    return tuple(rows)


def _normalize_hk_symbol_for_tushare(ticker: str) -> str:
    code = normalize_hk_symbol_for_stock_hk_daily(ticker)
    return f"{code}.HK"


def _normalize_crypto_symbol_for_openbb(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.endswith("USD"):
        return token
    return f"{token}USD"


def _call_tushare_daily(*, token: str, ts_code: str, start_date: str, end_date: str) -> tuple[Mapping[str, Any], ...]:
    pro = create_tushare_pro(token=token)
    frame = call_tushare_pro_bar(
        api=pro,
        ts_code=ts_code,
        start_date=_compact_date(start_date),
        end_date=_compact_date(end_date),
        adj="qfq",
    )
    return tuple(_df_to_rows(frame))


def _call_tushare_hk_daily(*, token: str, ts_code: str, start_date: str, end_date: str) -> tuple[Mapping[str, Any], ...]:
    pro = create_tushare_pro(token=token)
    frame = pro.hk_daily(ts_code=ts_code, start_date=_compact_date(start_date), end_date=_compact_date(end_date))
    return tuple(_df_to_rows(frame))


def _call_akshare_stock_zh_a_hist(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    frame = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=_compact_date(start_date),
        end_date=_compact_date(end_date),
        adjust=adjust,
    )
    return tuple(_df_to_rows(frame))


def _call_akshare_stock_hk_daily(
    *,
    symbol: str,
    adjust: str,
    start_date: str,
    end_date: str,
) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    frame = ak.stock_hk_daily(symbol=symbol, adjust=adjust)
    rows = tuple(_df_to_rows(frame))
    return tuple(
        row for row in rows if _in_date_range(_date_text(_pick(row, "date", "trade_date", "日期")), start_date, end_date)
    )


def _call_akshare_stock_zh_a_spot(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    frame = ak.stock_zh_a_spot_em()
    rows = list(_df_to_rows(frame))
    for row in rows:
        code = str(_pick(row, "代码", "symbol") or "").strip()
        if code == symbol:
            return (row,)
    return ()


def _call_akshare_stock_hk_spot(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    frame = ak.stock_hk_spot_em()
    rows = list(_df_to_rows(frame))
    for row in rows:
        code = str(_pick(row, "代码", "symbol") or "").strip()
        if normalize_hk_symbol_for_stock_hk_daily(code) == normalize_hk_symbol_for_stock_hk_daily(symbol):
            return (row,)
    return ()


def _call_openbb_equity_price_historical(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    provider: str,
) -> tuple[Mapping[str, Any], ...]:
    try:
        from openbb import obb  # type: ignore
    except Exception as exc:  # pragma: no cover - dependent on optional runtime
        raise RuntimeError(f"openbb import failed for us market adapter: {exc}") from exc
    output = obb.equity.price.historical(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )
    return tuple(_openbb_output_rows(output))


def _call_openbb_crypto_price_historical(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    provider: str,
) -> tuple[Mapping[str, Any], ...]:
    try:
        _ensure_openbb_provider_interface_obbjects("CryptoSearch", "CryptoHistorical")
        from openbb import obb  # type: ignore
    except Exception as exc:  # pragma: no cover - dependent on optional runtime
        raise RuntimeError(f"openbb import failed for crypto market adapter: {exc}") from exc
    output = obb.crypto.price.historical(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )
    return tuple(_openbb_output_rows(output))


def _ensure_openbb_provider_interface_obbjects(*model_names: str) -> None:
    import openbb_core.app.provider_interface as provider_interface  # type: ignore

    missing = [name for name in model_names if not hasattr(provider_interface, f"OBBject_{name}")]
    if not missing:
        return

    generated = provider_interface.ProviderInterface().return_annotations
    for name in missing:
        model = generated.get(name)
        if model is None:
            raise RuntimeError(f"openbb provider interface missing generated OBBject for {name}")
        setattr(provider_interface, f"OBBject_{name}", model)


def _call_crypto_derivatives(*, symbol: str, env: Mapping[str, str] | None = None) -> tuple[Mapping[str, Any], ...]:
    asset = _asset_from_crypto_symbol(symbol)
    contract_symbol = f"{asset}USDT"
    params_base = {"symbol": asset}
    oi_payload = _coinglass_get("/futures/open-interest/exchange-list", params=params_base, env=env)
    funding_payload = _coinglass_get("/futures/funding-rate/exchange-list", params=params_base, env=env)
    ratio_payload = _coinglass_get(
        "/futures/global-long-short-account-ratio/history",
        params={"exchange": "Binance", "symbol": contract_symbol, "interval": "h4", "limit": "6"},
        env=env,
    )
    taker_payload = _coinglass_get(
        "/futures/v2/taker-buy-sell-volume/history",
        params={"exchange": "Binance", "symbol": contract_symbol, "interval": "4h", "limit": "6"},
        env=env,
    )
    liquidations_payload = _coinglass_get(
        "/futures/liquidation/history",
        params={"exchange": "Binance", "symbol": contract_symbol, "interval": "h1", "limit": "24"},
        env=env,
    )
    taker_rows = _as_mapping_rows(_unwrap_provider_data(taker_payload))
    cvd = _build_cvd_proxy(taker_rows)
    row: dict[str, Any] = {
        "asset": asset,
        "symbol": contract_symbol,
        "source_type": "coinglass",
        "open_interest": _coinglass_latest_metric(_unwrap_provider_data(oi_payload)),
        "funding_rates": _coinglass_latest_metric(_unwrap_provider_data(funding_payload)),
        "long_short_ratio": _coinglass_latest_metric(_unwrap_provider_data(ratio_payload)),
        "taker_buy_sell": {
            "rows": taker_rows[-6:],
            "latest": taker_rows[-1] if taker_rows else None,
        },
        "liquidations": {
            "rows": _as_mapping_rows(_unwrap_provider_data(liquidations_payload))[-24:],
        },
        "cvd_proxy": cvd,
        "as_of": utc_now_iso(),
    }
    return (row,)


def _call_crypto_liquidation(*, symbol: str, env: Mapping[str, str] | None = None) -> tuple[Mapping[str, Any], ...]:
    asset = _asset_from_crypto_symbol(symbol)
    payload = _coinglass_get(
        "/futures/liquidation/aggregated-heatmap/model1",
        params={"symbol": asset, "range": "3d"},
        env=env,
    )
    normalized = _normalize_coinglass_liquidation_heatmap(_unwrap_provider_data(payload))
    return (
        {
            "asset": asset,
            "current_price": normalized["current_price"],
            "above_price_liquidity": normalized["above_price_liquidity"],
            "below_price_liquidity": normalized["below_price_liquidity"],
            "largest_clusters": normalized["largest_clusters"],
            "heatmap_model": "coinglass-v4-model1",
            "as_of": utc_now_iso(),
        },
    )


def _call_crypto_onchain(*, symbol: str, env: Mapping[str, str] | None = None) -> tuple[Mapping[str, Any], ...]:
    asset = _asset_from_crypto_symbol(symbol)
    now_ms = int(datetime.now().timestamp() * 1000)
    seven_days_ago_ms = now_ms - 7 * 24 * 60 * 60 * 1000
    warnings: list[str] = []

    def _try_get(path: str, *, params: Mapping[str, str] | None = None) -> Any:
        try:
            return _coinglass_get(path, params=params, env=env)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{path}: {exc}")
            return None

    balance = _try_get("/exchange/balance/list", params={"symbol": asset})
    balance_chart = _try_get("/exchange/balance/chart", params={"symbol": asset})
    whale_transfers = _try_get(
        "/chain/v2/whale-transfer",
        params={"symbol": asset, "start_time": str(seven_days_ago_ms), "end_time": str(now_ms)},
    )
    stablecoin_flows = _coinglass_stablecoin_netflow_proxy(env=env)
    btc_indicators = _coinglass_btc_onchain_indicators(asset=asset, env=env)
    warnings.extend(str(item) for item in stablecoin_flows.get("warnings", ()) if item)
    if isinstance(btc_indicators, Mapping):
        warnings.extend(str(item) for item in btc_indicators.get("warnings", ()) if item)
    balance_rows = _as_mapping_rows(_unwrap_provider_data(balance_chart)) if balance_chart is not None else []
    whale_rows = _as_mapping_rows(_unwrap_provider_data(whale_transfers)) if whale_transfers is not None else []
    active_addresses = _metric_value_from_mapping(btc_indicators.get("active_addresses")) if btc_indicators else None
    exchange_balance_trend = _numeric_trend(balance_rows, _COINGLASS_BALANCE_FIELDS)
    if not _coinglass_onchain_has_signal(
        exchange_balance_trend=exchange_balance_trend,
        whale_rows=whale_rows,
        stablecoin_flows=stablecoin_flows,
        btc_indicators=btc_indicators,
    ):
        detail = "; ".join(warnings) if warnings else "no usable Coinglass on-chain rows"
        raise RuntimeError(f"coinglass onchain unavailable: {detail}")
    return (
        {
            "asset": asset,
            "source_type": "coinglass-onchain",
            "exchange_balances": _unwrap_provider_data(balance) if balance is not None else None,
            "exchange_balance_trend": exchange_balance_trend,
            "whale_activity": {
                "large_tx_count": len(whale_rows),
                "large_tx_volume": _sum_numeric_fields(whale_rows, ("amount_usd", "value_usd", "usd", "value")),
                "transfers": tuple(whale_rows[:20]),
            },
            "stablecoin_flows": stablecoin_flows,
            "btc_indicators": btc_indicators,
            "active_addresses": active_addresses,
            "limitations": (
                "Coinglass on-chain coverage is normalized through OpenBB/data_gateway.",
                "Stablecoin exchange netflow is a CEX proxy, not wallet-labeled on-chain flow.",
                "BTC holder indicators are BTC-only.",
            ),
            "warnings": tuple(warnings),
            "as_of": utc_now_iso(),
        },
    )


def _call_crypto_macro(*, symbol: str, env: Mapping[str, str] | None = None) -> tuple[Mapping[str, Any], ...]:
    _ = symbol
    series_ids = ("CPIAUCSL", "CORESTICKM159SFRBATL", "FEDFUNDS", "DGS10", "UNRATE", "PAYEMS")
    latest_values = {series_id: _fred_latest(series_id, env=env) for series_id in series_ids}
    return (
        {
            "series": latest_values,
            "upcoming_events": (),
            "risk_window": "medium",
            "as_of": utc_now_iso(),
        },
    )


def _call_crypto_events(*, symbol: str, env: Mapping[str, str] | None = None) -> tuple[Mapping[str, Any], ...]:
    asset = _asset_from_crypto_symbol(symbol)
    payload = _tavily_search(
        query=f"{asset} official announcement governance security incident token unlock",
        max_results=10,
        env=env,
    )
    rows = _as_mapping_rows(payload.get("results"))
    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "title": str(_pick(row, "title") or "").strip(),
                "url": str(_pick(row, "url") or "").strip(),
                "published_date": str(_pick(row, "published_date") or "").strip(),
                "score": _to_float(_pick(row, "score")),
                "source": "tavily",
            }
        )
    return (
        {
            "asset": asset,
            "results": tuple(normalized_rows),
            "result_count": len(normalized_rows),
            "as_of": utc_now_iso(),
        },
    )


def _call_crypto_ahr999(*, symbol: str, env: Mapping[str, str] | None = None) -> tuple[Mapping[str, Any], ...]:
    asset = _asset_from_crypto_symbol(symbol)
    if asset != "BTC":
        raise RuntimeError(f"ahr999_index not applicable for {asset}; only BTC is supported")
    payload = _coinglass_get("/index/ahr999", env=env)
    summary = _coinglass_latest_metric(_unwrap_provider_data(payload))
    value = summary.get("value")
    return (
        {
            "asset": asset,
            "ahr999": value,
            "latest": summary.get("latest"),
            "dca_zone": bool(value is not None and value < 1.2),
            "deep_value_zone": bool(value is not None and value < 0.45),
            "formula_version": "coinglass-index",
            "as_of": utc_now_iso(),
        },
    )


def _asset_from_crypto_symbol(symbol: str) -> str:
    token = symbol.strip().upper()
    for suffix in ("USDT", "USD"):
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def _coinglass_base(*, env: Mapping[str, str] | None = None) -> str:
    env_map = os.environ if env is None else env
    return str(env_map.get("COINGLASS_API_BASE", _COINGLASS_DEFAULT_BASE)).rstrip("/")


def _coinglass_header_name(*, env: Mapping[str, str] | None = None) -> str:
    env_map = os.environ if env is None else env
    configured = str(env_map.get("COINGLASS_API_HEADER_NAME", "")).strip()
    if configured:
        return configured
    if "proxy.keystore.com.cn" in _coinglass_base(env=env_map):
        return "X-Api-Key"
    return "CG-API-KEY"


def _coinglass_url(
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    version: str = "v4",
    env: Mapping[str, str] | None = None,
) -> str:
    base = _coinglass_base(env=env)
    normalized_path = path if path.startswith("/") else f"/{path}"
    prefix = f"/{version}/api" if not base.endswith(("open-api-v4.coinglass.com", "open-api-v3.coinglass.com")) else "/api"
    if not params:
        return f"{base}{prefix}{normalized_path}"
    query_items = [(str(key), str(value)) for key, value in params.items() if value is not None and str(value).strip()]
    if not query_items:
        return f"{base}{prefix}{normalized_path}"
    from urllib.parse import urlencode

    return f"{base}{prefix}{normalized_path}?{urlencode(query_items)}"


def _coinglass_get(
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    version: str = "v4",
    env: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    env_map = os.environ if env is None else env
    api_key = str(env_map.get("COINGLASS_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("missing credential keys: COINGLASS_API_KEY")
    headers = {_coinglass_header_name(env=env_map): api_key, "accept": "application/json"}
    payload = _http_get_json(_coinglass_url(path, params=params, version=version, env=env_map), headers=headers)
    if not isinstance(payload, Mapping):
        raise RuntimeError("coinglass payload must be mapping")
    code = str(_pick(payload, "code") or "").strip()
    if code and code not in {"0", "200"}:
        message = str(_pick(payload, "msg", "message") or f"coinglass error code={code}")
        raise RuntimeError(message)
    return payload


def _unwrap_provider_data(payload: Mapping[str, Any]) -> Any:
    data = payload.get("data")
    if data is None:
        return payload
    return data


def _as_mapping_rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        for field in ("rows", "data", "list", "items", "results"):
            nested = value.get(field)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
                return [item for item in nested if isinstance(item, Mapping)]
        return [value]
    return []


def _coinglass_latest_metric(value: Any) -> Mapping[str, Any]:
    rows = _as_mapping_rows(value)
    if not rows:
        return {"latest": None, "value": None, "sample_size": 0}
    latest = (
        max(rows, key=lambda row: _parse_maybe_time(_pick(row, "time", "timestamp", "date")) or -1)
        if any(_parse_maybe_time(_pick(row, "time", "timestamp", "date")) is not None for row in rows)
        else rows[-1]
    )
    metric_value = _first_numeric_deep(latest)
    return {"latest": latest, "value": metric_value, "sample_size": len(rows)}


def _coinglass_btc_onchain_indicators(*, asset: str, env: Mapping[str, str] | None = None) -> Mapping[str, Any] | None:
    if asset != "BTC":
        return None
    indicators: dict[str, Any] = {}
    warnings: list[str] = []
    endpoints = {
        "sth_sopr": "/index/bitcoin-sth-sopr",
        "lth_sopr": "/index/bitcoin-lth-sopr",
        "nupl": "/index/bitcoin-net-unrealized-profit-loss",
        "active_addresses": "/index/bitcoin-active-addresses",
    }
    for key, path in endpoints.items():
        try:
            indicators[key] = _coinglass_latest_metric(_unwrap_provider_data(_coinglass_get(path, env=env)))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{path}: {exc}")
    if warnings:
        indicators["warnings"] = tuple(warnings)
    return indicators


def _coinglass_onchain_has_signal(
    *,
    exchange_balance_trend: Mapping[str, Any],
    whale_rows: Sequence[Mapping[str, Any]],
    stablecoin_flows: Mapping[str, Any],
    btc_indicators: Mapping[str, Any] | None,
) -> bool:
    signals = (
        exchange_balance_trend.get("latest"),
        len(whale_rows) if whale_rows else None,
        stablecoin_flows.get("aggregate_netflow"),
        _metric_value_from_mapping(btc_indicators.get("active_addresses")) if btc_indicators else None,
        _metric_value_from_mapping(btc_indicators.get("sth_sopr")) if btc_indicators else None,
        _metric_value_from_mapping(btc_indicators.get("lth_sopr")) if btc_indicators else None,
        _metric_value_from_mapping(btc_indicators.get("nupl")) if btc_indicators else None,
    )
    return any(value is not None for value in signals)


def _coinglass_stablecoin_netflow_proxy(*, env: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    assets: list[Mapping[str, Any]] = []
    warnings: list[str] = []
    for symbol in _COINGLASS_STABLECOINS:
        try:
            payload = _coinglass_get(
                "/spot/coin/netflow",
                params={"symbol": symbol, "exchange_list": _COINGLASS_DEFAULT_EXCHANGES},
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol} stablecoin netflow unavailable: {exc}")
            continue
        rows = _as_mapping_rows(_unwrap_provider_data(payload))
        assets.append(
            {
                "symbol": symbol,
                "netflow": _sum_numeric_fields(rows, _COINGLASS_NETFLOW_FIELDS),
                "rows": tuple(rows[:20]),
            }
        )
    aggregate = _sum_nullable(tuple(_to_float(item.get("netflow")) for item in assets))
    return {
        "source_type": "coinglass-cex-netflow-proxy",
        "assets": tuple(assets),
        "aggregate_netflow": aggregate,
        "exchange_netflow": aggregate,
        "confidence": "low" if aggregate is None else "medium",
        "limitations": ("stablecoin netflow is a Coinglass CEX proxy, not wallet-labeled on-chain flow",),
        "warnings": tuple(warnings),
    }


def _numeric_trend(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> Mapping[str, Any]:
    samples: list[tuple[int, float, Mapping[str, Any]]] = []
    for index, row in enumerate(rows):
        value = _first_numeric_field(row, fields)
        if value is None:
            continue
        timestamp = _parse_maybe_time(_pick(row, "time", "timestamp", "date")) or index
        samples.append((timestamp, value, row))
    if not samples:
        return {"latest": None, "previous": None, "change": None, "pct_change": None, "trend": "unknown", "sample_size": 0}
    samples.sort(key=lambda item: item[0])
    latest = samples[-1]
    previous = samples[-2] if len(samples) > 1 else None
    change = latest[1] - previous[1] if previous is not None else None
    pct_change = change / previous[1] if previous is not None and previous[1] else None
    trend = "up" if change is not None and change > 0 else "down" if change is not None and change < 0 else "flat" if change == 0 else "unknown"
    return {
        "latest": latest[1],
        "previous": previous[1] if previous is not None else None,
        "change": round(change, 4) if change is not None else None,
        "pct_change": round(pct_change, 6) if pct_change is not None else None,
        "trend": trend,
        "sample_size": len(samples),
        "latest_time": latest[0],
        "previous_time": previous[0] if previous is not None else None,
    }


def _metric_value_from_mapping(value: Any) -> float | None:
    return _first_numeric_deep(value)


def _sum_numeric_fields(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> float | None:
    values = [_first_numeric_field(row, fields) for row in rows]
    return _sum_nullable(values)


def _sum_nullable(values: Sequence[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean), 4)


def _build_cvd_proxy(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    deltas: list[float] = []
    latest_delta: float | None = None
    for row in rows:
        buy_volume = _to_float(_pick(row, "buy_volume", "buyVol", "buy", "buy_volume_usd", "buyUsd", "taker_buy_volume_usd"))
        sell_volume = _to_float(
            _pick(row, "sell_volume", "sellVol", "sell", "sell_volume_usd", "sellUsd", "taker_sell_volume_usd")
        )
        if buy_volume is None or sell_volume is None:
            continue
        delta = buy_volume - sell_volume
        deltas.append(delta)
        latest_delta = delta
    if not deltas:
        return {
            "source": "taker_buy_sell_proxy",
            "cumulative_delta": None,
            "latest_delta": None,
            "bias": "unknown",
            "sample_size": 0,
        }
    cumulative_delta = round(sum(deltas), 4)
    return {
        "source": "taker_buy_sell_proxy",
        "cumulative_delta": cumulative_delta,
        "latest_delta": round(latest_delta or 0.0, 4),
        "bias": "buy_pressure" if cumulative_delta > 0 else "sell_pressure" if cumulative_delta < 0 else "neutral",
        "sample_size": len(deltas),
    }


def _first_numeric_deep(value: Any) -> float | None:
    direct = _to_float(value)
    if direct is not None:
        return direct
    if isinstance(value, Mapping):
        direct = _first_numeric_field(value, _COINGLASS_METRIC_FIELDS)
        if direct is not None:
            return direct
        for key in (
            "latest",
            "data",
            "rows",
            "list",
            "items",
            "results",
            "stablecoin_margin_list",
            "token_margin_list",
        ):
            nested = value.get(key)
            direct = _first_numeric_deep(nested)
            if direct is not None:
                return direct
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            direct = _first_numeric_deep(item)
            if direct is not None:
                return direct
    return None


def _normalize_coinglass_liquidation_heatmap(value: Any) -> Mapping[str, Any]:
    record = value if isinstance(value, Mapping) else {}
    current_price = _coinglass_heatmap_current_price(record)
    y_axis = [_to_float(item) for item in _pick(record, "y_axis") or ()]
    clusters = sorted(
        (
            cluster
            for item in (_pick(record, "liquidation_leverage_data") or ())
            if (cluster := _coinglass_heatmap_cluster(item, y_axis=y_axis, current_price=current_price)) is not None
        ),
        key=lambda cluster: float(cluster["liquidation_value"]),
        reverse=True,
    )
    return {
        "current_price": current_price,
        "above_price_liquidity": tuple(cluster for cluster in clusters if cluster["side"] == "above")[:10],
        "below_price_liquidity": tuple(cluster for cluster in clusters if cluster["side"] == "below")[:10],
        "largest_clusters": tuple(clusters[:10]),
    }


def _coinglass_heatmap_current_price(record: Mapping[str, Any]) -> float | None:
    direct = _first_numeric_field(
        record,
        ("current_price", "currentPrice", "price", "last_price", "lastPrice", "mark_price", "markPrice", "index_price", "indexPrice"),
    )
    if direct is not None:
        return direct
    candles = _pick(record, "price_candlesticks")
    if not isinstance(candles, Sequence):
        return None
    parsed: list[tuple[int, float]] = []
    for index, candle in enumerate(candles):
        normalized = _coinglass_heatmap_candle(candle)
        if normalized is not None:
            parsed.append((normalized[0] if normalized[0] is not None else index, normalized[1]))
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[0])
    return parsed[-1][1]


def _coinglass_heatmap_candle(value: Any) -> tuple[int | None, float] | None:
    raw = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, Mapping)) else _pick(value, "value")
    if isinstance(raw, Sequence) and len(raw) >= 5:
        close_value = _to_float(raw[4])
        if close_value is not None:
            return (_parse_maybe_time(raw[0]), close_value)
    record = value if isinstance(value, Mapping) else {}
    close_value = _first_numeric_field(record, ("close", "c", "price"))
    if close_value is None:
        return None
    return (_parse_maybe_time(_pick(record, "time", "timestamp", "date")), close_value)


def _coinglass_heatmap_cluster(
    value: Any,
    *,
    y_axis: Sequence[float | None],
    current_price: float | None,
) -> Mapping[str, Any] | None:
    raw = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, Mapping)) else _pick(value, "value")
    time_index = _to_float(raw[0]) if isinstance(raw, Sequence) and len(raw) >= 3 else _to_float(_pick(value, "time_index", "x"))
    price_index = _to_float(raw[1]) if isinstance(raw, Sequence) and len(raw) >= 3 else _to_float(_pick(value, "price_index", "y"))
    liquidation_value = (
        _to_float(raw[2])
        if isinstance(raw, Sequence) and len(raw) >= 3
        else _first_numeric_field(value if isinstance(value, Mapping) else {}, ("liquidation_value", "value", "amount"))
    )
    if liquidation_value is None or liquidation_value <= 0:
        return None
    axis_index = int(price_index) if price_index is not None else None
    price = y_axis[axis_index] if axis_index is not None and 0 <= axis_index < len(y_axis) else _to_float(_pick(value, "price"))
    side = "unknown"
    if current_price is not None and price is not None:
        if price > current_price:
            side = "above"
        elif price < current_price:
            side = "below"
        else:
            side = "at_price"
    return {
        "time_index": time_index,
        "price_index": price_index,
        "price": price,
        "liquidation_value": round(liquidation_value, 4),
        "side": side,
    }


def _parse_maybe_time(value: Any) -> int | None:
    numeric = _to_float(value)
    if numeric is not None:
        return int(numeric)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _fred_latest(series_id: str, *, env: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    env_map = os.environ if env is None else env
    api_key = str(env_map.get("FRED_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("missing credential keys: FRED_API_KEY")
    payload = _http_get_json(
        f"{_FRED_BASE}/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": "5",
        },
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"fred payload must be mapping for series_id={series_id}")
    observations = _pick(payload, "observations")
    rows = [item for item in observations if isinstance(item, Mapping)] if isinstance(observations, Sequence) else []
    latest = next((item for item in rows if str(_pick(item, "value")) != "."), None)
    return {
        "date": str(_pick(latest, "date") or "") if latest else "",
        "value": _to_float(_pick(latest, "value")) if latest else None,
    }


def _tavily_search(*, query: str, max_results: int, env: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    env_map = os.environ if env is None else env
    api_key = str(env_map.get("TAVILY_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("missing credential keys: TAVILY_API_KEY")
    payload = _http_post_json(
        f"{_TAVILY_BASE}/search",
        headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        json_body={
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        },
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("tavily payload must be mapping")
    return payload


def _http_get_json(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(_safe_http_error_message(exc=exc, url=url)) from exc
    except ValueError as exc:
        raise RuntimeError(f"http response is not valid json for url={_sanitize_url(url)}") from exc
    return payload


def _http_post_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    try:
        response = requests.post(url, headers=headers, json=json_body, timeout=_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(_safe_http_error_message(exc=exc, url=url)) from exc
    except ValueError as exc:
        raise RuntimeError(f"http response is not valid json for url={_sanitize_url(url)}") from exc
    return payload


def _safe_http_error_message(*, exc: Exception, url: str) -> str:
    sanitized_text = _sanitize_error_text(str(exc))
    sanitized_url = _sanitize_url(url)
    if sanitized_url and sanitized_url not in sanitized_text:
        return f"http request failed: {sanitized_text} (url={sanitized_url})"
    return f"http request failed: {sanitized_text}"


def _sanitize_error_text(text: str) -> str:
    redacted = text
    for matched in _URL_PATTERN.findall(text):
        redacted = redacted.replace(matched, _sanitize_url(matched))
    key_pattern = re.compile(
        r"(?i)\b(api[_-]?key|x[_-]?api[_-]?key|key|token|access[_-]?token|secret|authorization|auth)\s*=\s*([^&\s,;]+)"
    )
    return key_pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)


def _sanitize_url(url: str) -> str:
    split = urlsplit(url)
    host_part = split.netloc.rsplit("@", maxsplit=1)[-1]
    if split.username is not None or split.password is not None or "@" in split.netloc:
        netloc = f"[REDACTED]@{host_part}"
    else:
        netloc = split.netloc
    if not split.query:
        return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))
    redacted_pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        if (
            normalized_key in _SENSITIVE_QUERY_KEYS
            or normalized_key.endswith("token")
            or normalized_key.endswith("apikey")
            or "secret" in normalized_key
        ):
            redacted_pairs.append((key, "[REDACTED]"))
        else:
            redacted_pairs.append((key, value))
    query = urlencode(redacted_pairs, doseq=True)
    return urlunsplit((split.scheme, netloc, split.path, query, split.fragment))


def _first_numeric_field(row: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    for field in fields:
        value = _to_float(_pick(row, field))
        if value is not None:
            return value
    return None


def _openbb_output_rows(output: Any) -> list[Mapping[str, Any]]:
    if hasattr(output, "to_df"):
        frame = output.to_df()
        return list(_df_to_rows(frame))
    if isinstance(output, Mapping):
        results = output.get("results")
        if isinstance(results, Sequence):
            return [item for item in results if isinstance(item, Mapping)]
    if isinstance(output, Sequence):
        return [item for item in output if isinstance(item, Mapping)]
    if hasattr(output, "model_dump"):
        dumped = output.model_dump()
        if isinstance(dumped, Mapping):
            results = dumped.get("results")
            if isinstance(results, Sequence):
                return [item for item in results if isinstance(item, Mapping)]
    raise RuntimeError("openbb historical response shape unsupported")


def _df_to_rows(frame: Any) -> list[Mapping[str, Any]]:
    if frame is None:
        return []
    if not hasattr(frame, "to_dict"):
        raise RuntimeError("provider response is not tabular")
    if hasattr(frame, "index") and getattr(frame.index, "name", None):
        frame = frame.reset_index()
    rows = frame.to_dict(orient="records")
    out: list[Mapping[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append(row)
    return out


def _pick(row: Any, *candidates: str) -> Any:
    if not isinstance(row, Mapping):
        return None
    for key in candidates:
        if key in row:
            return row[key]
    for key, value in row.items():
        key_text = str(key).lower()
        for candidate in candidates:
            if key_text == candidate.lower():
                return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _compact_date(value: str) -> str:
    return value.replace("-", "").strip()


def _default_currency_by_market(market: Market) -> str:
    if market == Market.CN_A:
        return "CNY"
    if market == Market.HK:
        return "HKD"
    return "USD"


def _default_timezone_by_market(market: Market) -> str:
    if market == Market.CN_A:
        return "Asia/Shanghai"
    if market == Market.HK:
        return "Asia/Hong_Kong"
    if market == Market.US:
        return "America/New_York"
    return "UTC"


def _in_date_range(date_text: str | None, start_date: str, end_date: str) -> bool:
    if date_text is None:
        return False
    start = _compact_date(start_date)
    end = _compact_date(end_date)
    candidate = date_text.replace("-", "")
    return start <= candidate <= end
