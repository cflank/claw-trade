from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import os
from typing import Any, Mapping, Sequence

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
)
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.tushare_client import call_tushare_pro_bar, create_tushare_pro


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
                    license_policy_id="personal_research",
                    credential_requirements=self.required_env_keys,
                    rate_limit_policy_id=f"{seed.provider}.{seed.endpoint}",
                    cache_ttl_seconds=seed.cache_ttl_seconds,
                    required=seed.required,
                    attempt_required=seed.attempt_required,
                    coverage_group=seed.coverage_group,
                    coverage_quorum=seed.coverage_quorum,
                    priority=seed.priority,
                    priority_source=PrioritySource.SYSTEM_DEFAULT,
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
            return _fetch_crypto(spec=spec, request=request, params=params, request_id=request_id)
        raise RuntimeError(f"unsupported market: {request.market}")

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        rows = _extract_rows(fetch.payload)
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
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                error_code="empty",
                error_message=f"{spec.provider}/{spec.endpoint} returned empty rows",
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
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
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
            source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
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
                    provider="tushare",
                    adapter_id="project.cn_a.market",
                    endpoint="daily",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="cn_a_ohlcv",
                    coverage_quorum=1,
                    priority=0,
                ),
                _CapabilitySeed(
                    provider="akshare",
                    adapter_id="project.cn_a.market",
                    endpoint="stock_zh_a_hist",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=False,
                    attempt_required=True,
                    coverage_group="cn_a_ohlcv",
                    coverage_quorum=1,
                    priority=1,
                ),
                _CapabilitySeed(
                    provider="eastmoney",
                    adapter_id="project.cn_a.market",
                    endpoint="quote",
                    source_role=SourceRole.MARKET_DATA,
                    expected_schema_id="cn_a.market.ohlcv.v1",
                    required=False,
                    attempt_required=False,
                    priority=2,
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
                    priority=0,
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
            raise RuntimeError("tushare daily token missing; set TUSHARE_TOKEN or rely on the configured akshare parallel path")
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
    if spec.endpoint == "stock_zh_a_hist":
        rows = _call_akshare_stock_zh_a_hist(
            symbol=_normalize_cn_symbol_for_akshare(request.ticker),
            start_date=request.start_date,
            end_date=request.end_date,
            adjust="qfq",
        )
        return _build_fetch(
            provider="akshare",
            endpoint=spec.endpoint,
            source_url="https://akshare.akfamily.xyz/data/stock/stock.html",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.endpoint == "quote":
        rows = _call_akshare_stock_zh_a_spot(symbol=_normalize_cn_symbol_for_akshare(request.ticker))
        return _build_fetch(
            provider="eastmoney",
            endpoint=spec.endpoint,
            source_url="https://quote.eastmoney.com",
            request_id=request_id,
            params=params,
            rows=rows,
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
) -> ProviderFetch:
    if spec.endpoint != "crypto_price_historical":
        raise RuntimeError(f"unsupported crypto endpoint: {spec.endpoint}")
    symbol = str(params.get("symbol") or _normalize_crypto_symbol_for_openbb(request.ticker))
    rows = _call_openbb_crypto_price_historical(
        symbol=symbol,
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
    open_value = _to_float(_pick(row, "open", "开盘"))
    high_value = _to_float(_pick(row, "high", "最高"))
    low_value = _to_float(_pick(row, "low", "最低"))
    close_value = _to_float(_pick(row, "close", "收盘"))
    volume_value = _to_float(_pick(row, "volume", "vol", "成交量"))
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
    token = ticker.strip().upper()
    if token.startswith("SH") or token.startswith("SZ"):
        token = token[2:]
    if token.endswith(".SH") or token.endswith(".SZ"):
        token = token[: -3]
    if token.isdigit() and len(token) < 6:
        token = token.zfill(6)
    return token


def _normalize_cn_symbol_for_tushare(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.startswith("SH") and token[2:].isdigit():
        return f"{token[2:].zfill(6)}.SH"
    if token.startswith("SZ") and token[2:].isdigit():
        return f"{token[2:].zfill(6)}.SZ"
    if token.endswith(".SH") or token.endswith(".SZ"):
        code, market = token.split(".", maxsplit=1)
        return f"{code.zfill(6)}.{market}"
    if token.isdigit():
        suffix = "SH" if token.startswith(("5", "6", "9")) else "SZ"
        return f"{token.zfill(6)}.{suffix}"
    return token


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


def _pick(row: Mapping[str, Any], *candidates: str) -> Any:
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
