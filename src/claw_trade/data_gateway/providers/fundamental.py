from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree
import hashlib
import json
import os
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

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
from claw_trade.data_gateway.providers.tushare_client import create_tushare_pro


def fundamental_capabilities() -> tuple[ProviderCapability, ...]:
    return (
        # CN_A
        ProviderCapability(
            provider="tushare",
            adapter_id="fundamental.tushare.cn_a",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CN_A,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="fina_indicator+income+balancesheet+cashflow+fina_mainbz",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="cn_a.fundamental.core.v1",
            license_policy_id="personal_research",
            credential_requirements=("TUSHARE_TOKEN",),
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_fundamental_core",
            coverage_quorum=1,
            priority=20,
        ),
        ProviderCapability(
            provider="akshare",
            adapter_id="fundamental.akshare.cn_a",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CN_A,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="stock_financial_analysis_indicator",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="cn_a.fundamental.supplement.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="akshare.default",
            cache_ttl_seconds=900,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_fundamental_core",
            coverage_quorum=1,
            priority=10,
        ),
        # HK
        ProviderCapability(
            provider="tushare_hk",
            adapter_id="fundamental.tushare.hk",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.HK,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="hk_fina_indicator",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="hk.fundamental.tushare.v1",
            license_policy_id="personal_research",
            credential_requirements=("TUSHARE_TOKEN",),
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="hk_fundamental_core",
            coverage_quorum=1,
            priority=10,
        ),
        ProviderCapability(
            provider="akshare_hk",
            adapter_id="fundamental.akshare.hk",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.HK,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="stock_financial_hk_analysis_indicator_em",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="hk.fundamental.akshare.indicator.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="akshare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="hk_fundamental_core",
            coverage_quorum=1,
            priority=20,
        ),
        ProviderCapability(
            provider="akshare_hk",
            adapter_id="fundamental.akshare.hk.income",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.HK,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="stock_financial_hk_report_em:income",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="hk.fundamental.akshare.income.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="akshare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="hk_fundamental_statement",
            coverage_quorum=1,
            priority=30,
        ),
        ProviderCapability(
            provider="openbb_yfinance_hk",
            adapter_id="fundamental.yfinance.hk",
            provider_kind=ProviderKind.OPENBB_NATIVE,
            market=Market.HK,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="equity_fundamentals_yfinance",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="hk.fundamental.yfinance.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="openbb_yfinance.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="hk_fundamental_core",
            coverage_quorum=1,
            priority=40,
        ),
        ProviderCapability(
            provider="hk_official_filing",
            adapter_id="fundamental.hk.official",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.HK,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="hkexnews_filings",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            expected_schema_id="hk.fundamental.official.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="hk_official.default",
            cache_ttl_seconds=1800,
            required=False,
            attempt_required=True,
            coverage_group="hk_official_refs",
            coverage_quorum=1,
            priority=0,
        ),
        # US
        ProviderCapability(
            provider="openbb_yfinance",
            adapter_id="fundamental.yfinance.us",
            provider_kind=ProviderKind.OPENBB_NATIVE,
            market=Market.US,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="equity_fundamentals_yfinance",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="us.fundamental.core.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="openbb_yfinance.default",
            cache_ttl_seconds=900,
            required=True,
            attempt_required=True,
            coverage_group="us_fundamental_core",
            coverage_quorum=1,
            priority=10,
        ),
        ProviderCapability(
            provider="openbb_sec",
            adapter_id="fundamental.sec.us",
            provider_kind=ProviderKind.OPENBB_NATIVE,
            market=Market.US,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="company_facts+filings",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            expected_schema_id="us.fundamental.official.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="sec.default",
            cache_ttl_seconds=1800,
            required=False,
            attempt_required=True,
            coverage_group="us_official_refs",
            coverage_quorum=1,
            priority=0,
        ),
        # CRYPTO
        ProviderCapability(
            provider="coingecko",
            adapter_id="fundamental.coingecko.crypto",
            provider_kind=ProviderKind.OPENBB_NATIVE,
            market=Market.CRYPTO,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="coin_profile+market_cap+supply",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="crypto.fundamental.coingecko.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="coingecko.default",
            cache_ttl_seconds=900,
            required=True,
            attempt_required=True,
            coverage_group="crypto_fundamental_core",
            coverage_quorum=1,
            priority=10,
        ),
        ProviderCapability(
            provider="defillama",
            adapter_id="fundamental.defillama.crypto",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            market=Market.CRYPTO,
            domain=PackDomain.FUNDAMENTAL,
            endpoint="tvl+fees+revenue+security+funding",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            expected_schema_id="crypto.fundamental.defillama.v1",
            license_policy_id="personal_research",
            credential_requirements=(),
            rate_limit_policy_id="defillama.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="crypto_fundamental_supplement",
            coverage_quorum=1,
            priority=20,
        ),
    )


@dataclass(frozen=True)
class StaticFundamentalProviderAdapter:
    adapter_id: str
    provider_id: str
    adapter_kind: str
    provider_kind: ProviderKind
    market: Market
    provider_config_version: str
    capability: ProviderCapability
    env: Mapping[str, str] | None = None

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (self.capability,)

    def validate_credentials(self) -> CredentialStatus:
        env = os.environ if self.env is None else self.env
        missing = tuple(key for key in self.capability.credential_requirements if not str(env.get(key, "")).strip())
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
        capability = self.capability
        spec = ProviderCallSpec(
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
            params=_build_fundamental_params(request=request, capability=capability),
            cache_ttl_seconds=capability.cache_ttl_seconds,
            license_policy_id=capability.license_policy_id,
            expected_schema_id=capability.expected_schema_id,
            priority=capability.priority,
            priority_source=PrioritySource.SYSTEM_DEFAULT,
            user_preferred=False,
        )
        return (spec,)

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        params = dict(spec.params)
        request_id = _stable_request_id(spec=spec, params=params)
        if request.market == Market.CN_A:
            return _fetch_cn_a(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        if request.market == Market.HK:
            return _fetch_hk(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        if request.market == Market.US:
            return _fetch_us(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        if request.market == Market.CRYPTO:
            return _fetch_crypto(spec=spec, request=request, params=params, request_id=request_id, env=self._env)
        raise RuntimeError(f"unsupported market for fundamental adapter: {request.market}")

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        rows = _extract_rows(fetch.payload)
        raw_ref = _raw_ref(spec=spec, fetch=fetch)
        if not rows:
            return NormalizedResult(
                status=ProviderStatus.EMPTY,
                schema_id=spec.expected_schema_id,
                rows=(),
                compact_facts=_compact_facts(spec=spec, row_count=0),
                row_count=0,
                field_units={},
                currency=_default_currency(spec.market),
                timezone=_default_timezone(spec.market),
                source_raw_ref=raw_ref,
                error_code="empty",
                error_message=f"{spec.provider}/{spec.endpoint} returned empty rows",
            )

        normalized = _normalize_fields(spec=spec, rows=rows)
        if not normalized:
            return NormalizedResult(
                status=ProviderStatus.FIELD_MISSING,
                schema_id=spec.expected_schema_id,
                rows=(),
                compact_facts=_compact_facts(spec=spec, row_count=0),
                row_count=0,
                field_units={},
                currency=_default_currency(spec.market),
                timezone=_default_timezone(spec.market),
                source_raw_ref=raw_ref,
                missing_fields=_required_fields_for_adapter(spec.adapter_id),
                error_code="field_missing",
                error_message=f"{spec.provider}/{spec.endpoint} missing required fundamental fields",
            )

        missing_fields = tuple(
            field for field in _required_fields_for_adapter(spec.adapter_id) if field not in normalized or normalized[field] in (None, "")
        )
        normalized_row = dict(normalized)
        normalized_row.setdefault("currency", _default_currency(spec.market))
        normalized_row.setdefault("timezone", _default_timezone(spec.market))
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id=spec.expected_schema_id,
            rows=(normalized_row,),
            compact_facts=_compact_facts(spec=spec, row_count=1, row=normalized_row),
            row_count=1,
            field_units={},
            currency=str(normalized_row.get("currency")) if normalized_row.get("currency") is not None else None,
            timezone=str(normalized_row.get("timezone")) if normalized_row.get("timezone") is not None else None,
            source_raw_ref=raw_ref,
            missing_fields=missing_fields,
        )

    @property
    def _env(self) -> Mapping[str, str]:
        return os.environ if self.env is None else self.env


def build_default_fundamental_adapters(
    *,
    provider_config_version: str,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderAdapter, ...]:
    adapters: list[ProviderAdapter] = []
    for capability in fundamental_capabilities():
        adapters.append(
            StaticFundamentalProviderAdapter(
                adapter_id=capability.adapter_id,
                provider_id=capability.provider,
                adapter_kind="fundamental_adapter",
                provider_kind=capability.provider_kind,
                market=capability.market,
                provider_config_version=provider_config_version,
                capability=capability,
                env=env,
            )
        )
    return tuple(adapters)


def _build_fundamental_params(*, request: PackRequest, capability: ProviderCapability) -> Mapping[str, Any]:
    params: dict[str, Any] = {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
        "currency": request.currency,
        "market": request.market.value,
    }
    if capability.market == Market.CN_A:
        params["ts_code"] = _normalize_cn_symbol_for_tushare(request.ticker)
        params["symbol"] = _normalize_cn_symbol_for_akshare(request.ticker)
    if capability.market == Market.HK:
        params["ts_code"] = _normalize_hk_symbol_for_tushare(request.ticker)
        params["symbol"] = _normalize_hk_symbol_for_akshare(request.ticker)
        params["yfinance_symbol"] = _normalize_hk_symbol_for_yfinance(request.ticker)
    if capability.market == Market.US:
        params["symbol"] = request.ticker.strip().upper()
    if capability.market == Market.CRYPTO:
        params["coin_id"] = _normalize_crypto_coin_id(request.ticker)
        params["symbol"] = request.ticker.strip().upper()
    return params


def _fetch_cn_a(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    if spec.adapter_id == "fundamental.tushare.cn_a":
        token = str(env.get("TUSHARE_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("tushare token missing for cn_a fundamental adapter (TUSHARE_TOKEN)")
        rows = _call_tushare_cn_a_fundamental(token=token, ts_code=str(params.get("ts_code") or _normalize_cn_symbol_for_tushare(request.ticker)))
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://api.tushare.pro",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.akshare.cn_a":
        rows = _call_akshare_cn_a_fundamental(symbol=str(params.get("symbol") or _normalize_cn_symbol_for_akshare(request.ticker)))
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://akshare.akfamily.xyz/data/stock/stock.html",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    raise RuntimeError(f"unsupported cn_a fundamental adapter_id: {spec.adapter_id}")


def _fetch_hk(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    if spec.adapter_id == "fundamental.tushare.hk":
        token = str(env.get("TUSHARE_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("tushare token missing for hk fundamental adapter (TUSHARE_TOKEN)")
        rows = _call_tushare_hk_fundamental(
            token=token,
            ts_code=str(params.get("ts_code") or _normalize_hk_symbol_for_tushare(request.ticker)),
            start_date=str(params.get("start_date") or request.start_date),
            end_date=str(params.get("end_date") or request.end_date),
        )
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://api.tushare.pro",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.akshare.hk":
        rows = _call_akshare_hk_fundamental(symbol=str(params.get("symbol") or _normalize_hk_symbol_for_akshare(request.ticker)))
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://akshare.akfamily.xyz/data/stock/stock.html",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.akshare.hk.income":
        rows = _call_akshare_hk_income(symbol=str(params.get("symbol") or _normalize_hk_symbol_for_akshare(request.ticker)))
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://akshare.akfamily.xyz/data/stock/stock.html",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.yfinance.hk":
        symbol = str(params.get("yfinance_symbol") or _normalize_hk_symbol_for_yfinance(request.ticker))
        rows = _call_openbb_hk_fundamental_yfinance(symbol=symbol)
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://query1.finance.yahoo.com",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.hk.official":
        rows = _call_hk_official_filings(symbol=str(params.get("symbol") or _normalize_hk_symbol_for_akshare(request.ticker)))
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://www.hkexnews.hk",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    raise RuntimeError(f"unsupported hk fundamental adapter_id: {spec.adapter_id}")


def _fetch_us(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    del env
    symbol = str(params.get("symbol") or request.ticker.strip().upper())
    if spec.adapter_id == "fundamental.yfinance.us":
        rows = _call_openbb_us_fundamental_yfinance(symbol=symbol)
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://query1.finance.yahoo.com",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.sec.us":
        rows = _call_openbb_us_sec_filings(symbol=symbol)
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://www.sec.gov",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    raise RuntimeError(f"unsupported us fundamental adapter_id: {spec.adapter_id}")


def _fetch_crypto(
    *,
    spec: ProviderCallSpec,
    request: PackRequest,
    params: Mapping[str, Any],
    request_id: str,
    env: Mapping[str, str],
) -> ProviderFetch:
    if spec.adapter_id == "fundamental.coingecko.crypto":
        coin_id = str(params.get("coin_id") or _normalize_crypto_coin_id(request.ticker))
        demo_key = str(env.get("COINGECKO_DEMO_API_KEY", "")).strip() or None
        rows = (_call_coingecko_coin_fundamental(coin_id=coin_id, demo_api_key=demo_key),)
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://api.coingecko.com/api/v3",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    if spec.adapter_id == "fundamental.defillama.crypto":
        protocol = _normalize_defillama_protocol_slug(request.ticker)
        rows = (_call_defillama_fundamental(protocol_slug=protocol),)
        return _build_fetch(
            provider=spec.provider,
            endpoint=spec.endpoint,
            source_url="https://api.llama.fi",
            request_id=request_id,
            params=params,
            rows=rows,
        )
    raise RuntimeError(f"unsupported crypto fundamental adapter_id: {spec.adapter_id}")


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
    rows = parsed.get("rows")
    if rows is None:
        rows = parsed.get("data") or parsed.get("results") or ()
    if not isinstance(rows, Sequence):
        raise RuntimeError("provider payload rows must be sequence")
    out: list[Mapping[str, Any]] = []
    for item in rows:
        if isinstance(item, Mapping):
            out.append(item)
    return tuple(out)


def _normalize_fields(*, spec: ProviderCallSpec, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if spec.adapter_id in {
        "fundamental.tushare.cn_a",
        "fundamental.akshare.cn_a",
        "fundamental.tushare.hk",
        "fundamental.akshare.hk",
        "fundamental.yfinance.hk",
        "fundamental.yfinance.us",
    }:
        return _extract_equity_fields(rows)
    if spec.adapter_id == "fundamental.akshare.hk.income":
        return _extract_hk_income_fields(rows)
    if spec.adapter_id == "fundamental.hk.official":
        return _extract_hk_official_fields(rows)
    if spec.adapter_id == "fundamental.sec.us":
        return _extract_us_official_fields(rows)
    if spec.adapter_id == "fundamental.coingecko.crypto":
        return _extract_crypto_coingecko_fields(rows)
    if spec.adapter_id == "fundamental.defillama.crypto":
        return _extract_crypto_defillama_fields(rows)
    return {}


def _extract_equity_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pe = _pick_float(rows, "valuation.pe", "pe_ttm", "pe", "pe_ratio", "forward_pe", "市盈率(TTM)", "市盈率", "trailingPE", "peRatio")
    pb = _pick_float(rows, "valuation.pb", "pb", "price_to_book", "市净率", "priceToBook", "pbRatio")
    roe = _pick_float(
        rows,
        "financial_indicators.roe",
        "roe",
        "roe_ttm",
        "roe_avg",
        "roe_yearly",
        "净资产收益率",
        "returnOnEquity",
        "return_on_equity",
    )
    out: dict[str, Any] = {}
    if pe is not None:
        out["valuation.pe"] = pe
    if pb is not None:
        out["valuation.pb"] = pb
    if roe is not None:
        out["financial_indicators.roe"] = roe
    return out


def _extract_hk_income_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    revenue = _pick_float(rows, "income.revenue", "revenue", "营业额", "总营收", "营业收入")
    net_profit = _pick_float(rows, "income.net_profit", "net_profit", "净利润", "归母净利润")
    out: dict[str, Any] = {}
    if revenue is not None:
        out["income.revenue"] = revenue
    if net_profit is not None:
        out["income.net_profit"] = net_profit
    return out


def _extract_hk_official_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    report_date = _pick_text(rows, "report_date", "公告日期", "日期", "date")
    title = _pick_text(rows, "title", "公告标题", "标题", "name")
    out: dict[str, Any] = {}
    if report_date:
        out["official_filing.report_date"] = report_date
    if title:
        out["official_filing.title"] = title
    return out


def _extract_us_official_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    form_type = _pick_text(rows, "form", "form_type", "formType")
    filed_at = _pick_text(rows, "filed", "filing_date", "filedAt")
    out: dict[str, Any] = {}
    if form_type:
        out["official_filing.form_type"] = form_type
    if filed_at:
        out["official_filing.filed_at"] = filed_at
    return out


def _extract_crypto_coingecko_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    market_cap = _pick_float(rows, "valuation.market_cap_usd", "market_cap_usd", "market_cap")
    circulating = _pick_float(rows, "supply.circulating", "circulating_supply")
    total = _pick_float(rows, "supply.total", "total_supply", "max_supply")
    security = _pick_float(rows, "security.score", "security_score", "trust_score")
    funding = _pick_text(rows, "funding.last_round", "last_funding_round", "funding_round")
    out: dict[str, Any] = {}
    if market_cap is not None:
        out["valuation.market_cap_usd"] = market_cap
    if circulating is not None:
        out["supply.circulating"] = circulating
    if total is not None:
        out["supply.total"] = total
    if security is not None:
        out["security.score"] = security
    if funding:
        out["funding.last_round"] = funding
    return out


def _extract_crypto_defillama_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tvl = _pick_float(rows, "defi.tvl_usd", "tvl_usd", "tvl")
    fees = _pick_float(rows, "defi.fees_24h_usd", "fees_24h_usd", "dailyFees", "fees")
    revenue = _pick_float(rows, "defi.revenue_24h_usd", "revenue_24h_usd", "dailyRevenue", "revenue")
    security = _pick_float(rows, "security.score", "security_score")
    funding = _pick_text(rows, "funding.last_round", "funding_round")
    out: dict[str, Any] = {}
    if tvl is not None:
        out["defi.tvl_usd"] = tvl
    if fees is not None:
        out["defi.fees_24h_usd"] = fees
    if revenue is not None:
        out["defi.revenue_24h_usd"] = revenue
    if security is not None:
        out["security.score"] = security
    if funding:
        out["funding.last_round"] = funding
    return out


def _required_fields_for_adapter(adapter_id: str) -> tuple[str, ...]:
    if adapter_id in {
        "fundamental.tushare.cn_a",
        "fundamental.akshare.cn_a",
        "fundamental.tushare.hk",
        "fundamental.akshare.hk",
        "fundamental.yfinance.hk",
        "fundamental.yfinance.us",
    }:
        return ("valuation.pe", "valuation.pb", "financial_indicators.roe")
    if adapter_id == "fundamental.coingecko.crypto":
        return ("valuation.market_cap_usd", "supply.circulating", "supply.total")
    if adapter_id == "fundamental.defillama.crypto":
        return ("defi.tvl_usd", "defi.fees_24h_usd", "defi.revenue_24h_usd")
    return ()


def _compact_facts(*, spec: ProviderCallSpec, row_count: int, row: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    payload = {
        "provider": spec.provider,
        "endpoint": spec.endpoint,
        "schema_id": spec.expected_schema_id,
        "row_count": row_count,
    }
    if row:
        for field in (
            "valuation.pe",
            "valuation.pb",
            "financial_indicators.roe",
            "income.revenue",
            "income.net_profit",
            "valuation.market_cap_usd",
            "supply.circulating",
            "supply.total",
            "defi.tvl_usd",
            "defi.fees_24h_usd",
            "defi.revenue_24h_usd",
            "security.score",
            "funding.last_round",
        ):
            if field in row and row[field] not in (None, ""):
                payload[field] = row[field]
    return payload


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


def _compact_date(value: str) -> str:
    return value.strip().replace("-", "")


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


def _normalize_cn_symbol_for_akshare(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.startswith("SH") or token.startswith("SZ"):
        token = token[2:]
    if token.endswith(".SH") or token.endswith(".SZ"):
        token = token[: -3]
    if token.isdigit() and len(token) < 6:
        return token.zfill(6)
    return token


def _normalize_hk_symbol_for_tushare(ticker: str) -> str:
    return f"{_normalize_hk_symbol_for_akshare(ticker)}.HK"


def _normalize_hk_symbol_for_akshare(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.endswith(".HK"):
        token = token[: -len(".HK")]
    if token.isdigit() and len(token) < 5:
        return token.zfill(5)
    return token


def _normalize_hk_symbol_for_yfinance(ticker: str) -> str:
    code = _normalize_hk_symbol_for_akshare(ticker)
    yahoo_code = code.lstrip("0")
    if len(yahoo_code) < 4:
        yahoo_code = code[-4:]
    return f"{yahoo_code}.HK"


def _normalize_crypto_coin_id(ticker: str) -> str:
    token = ticker.strip().upper()
    mapping = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
    }
    return mapping.get(token, token.lower())


def _normalize_defillama_protocol_slug(ticker: str) -> str:
    token = ticker.strip().upper()
    mapping = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
    }
    return mapping.get(token, token.lower())


def _call_tushare_cn_a_fundamental(*, token: str, ts_code: str) -> tuple[Mapping[str, Any], ...]:
    pro = create_tushare_pro(token=token)
    primary = _df_to_rows(pro.fina_indicator(ts_code=ts_code, limit=1))
    extra = _df_to_rows(pro.daily_basic(ts_code=ts_code, limit=1))
    if not primary and not extra:
        return ()
    merged = dict(primary[0] if primary else {})
    if extra:
        merged.update(extra[0])
    return (merged,)


def _call_akshare_cn_a_fundamental(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    primary = _df_to_rows(ak.stock_financial_analysis_indicator(symbol=symbol))
    merged = dict(primary[0] if primary else {})
    try:
        lg = _df_to_rows(ak.stock_a_lg_indicator(symbol=symbol))
    except Exception:  # noqa: BLE001
        lg = []
    if lg:
        merged.update(lg[0])
    return (merged,) if merged else ()


def _call_tushare_hk_fundamental(
    *,
    token: str,
    ts_code: str,
    start_date: str,
    end_date: str,
) -> tuple[Mapping[str, Any], ...]:
    pro = create_tushare_pro(token=token)
    rows = _df_to_rows(
        pro.hk_fina_indicator(
            ts_code=ts_code,
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
        )
    )
    return tuple(rows[:1])


def _call_akshare_hk_fundamental(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    rows = _df_to_rows(ak.stock_financial_hk_analysis_indicator_em(symbol=symbol, indicator="年度"))
    if rows:
        return (rows[0],)
    return ()


def _call_akshare_hk_income(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    import akshare as ak

    rows = _df_to_rows(ak.stock_financial_hk_report_em(stock=symbol, symbol="利润表", indicator="年度"))
    if rows:
        return (rows[0],)
    return ()


def _call_hk_official_filings(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    url = "https://www.hkex.com.hk/Services/RSS-Feeds/regulatory-announcements?sc_lang=en"
    request = Request(url, headers={"User-Agent": "claw-trade-openbb-fundamental-adapter/1.0"}, method="GET")
    with urlopen(request, timeout=20.0) as response:
        xml_text = response.read().decode("utf-8", errors="replace")
    root = ElementTree.fromstring(xml_text)
    symbol_token = symbol.strip().upper().replace(".HK", "")
    rows: list[Mapping[str, Any]] = []
    for item in root.findall(".//item"):
        title = _xml_text(item, "title")
        summary = _xml_text(item, "description")
        if symbol_token and symbol_token not in title.upper() and symbol_token not in summary.upper():
            continue
        rows.append(
            {
                "title": title,
                "url": _xml_text(item, "link"),
                "report_date": _xml_text(item, "pubDate"),
                "summary": summary,
            }
        )
    if rows:
        return tuple(rows[:20])
    return ()


def _xml_text(parent: ElementTree.Element, tag: str) -> str:
    node = parent.find(tag)
    return node.text.strip() if node is not None and node.text else ""


def _call_openbb_us_fundamental_yfinance(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    return _call_openbb_fundamental_with_candidates(
        symbol=symbol,
        provider="yfinance",
        candidates=(
            "equity.fundamental.ratios",
            "equity.fundamental.metrics",
            "equity.fundamental.valuation",
        ),
    )


def _call_openbb_hk_fundamental_yfinance(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    return _call_openbb_fundamental_with_candidates(
        symbol=symbol,
        provider="yfinance",
        candidates=(
            "equity.fundamental.ratios",
            "equity.fundamental.metrics",
            "equity.fundamental.valuation",
        ),
    )


def _call_openbb_us_fundamental_fmp(*, symbol: str, api_key: str) -> tuple[Mapping[str, Any], ...]:
    _ = api_key  # FMP key is expected from runtime environment loaded by OpenBB.
    return _call_openbb_fundamental_with_candidates(
        symbol=symbol,
        provider="fmp",
        candidates=(
            "equity.fundamental.ratios",
            "equity.fundamental.metrics",
            "equity.fundamental.valuation",
        ),
    )


def _call_openbb_us_sec_filings(*, symbol: str) -> tuple[Mapping[str, Any], ...]:
    return _call_openbb_fundamental_with_candidates(
        symbol=symbol,
        provider="sec",
        candidates=(
            "equity.fundamental.filings",
            "equity.fundamental.company_facts",
        ),
    )


def _call_openbb_fundamental_with_candidates(
    *,
    symbol: str,
    provider: str,
    candidates: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    try:
        from openbb import obb  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"openbb import failed for fundamental adapter: {exc}") from exc

    errors: list[str] = []
    for path in candidates:
        try:
            fn = _resolve_attr(obb, path)
        except AttributeError:
            continue
        try:
            output = fn(symbol=symbol, provider=provider)
            rows = tuple(_openbb_output_rows(output))
            if rows:
                return rows
            errors.append(f"{path} returned empty")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: {exc}")
    joined = "; ".join(errors) if errors else "no callable endpoint found"
    raise RuntimeError(f"openbb fundamental call failed ({provider}/{symbol}): {joined}")


def _call_coingecko_coin_fundamental(*, coin_id: str, demo_api_key: str | None) -> Mapping[str, Any]:
    url = (
        "https://api.coingecko.com/api/v3/coins/"
        + quote(coin_id)
        + "?localization=false&tickers=false&market_data=true&community_data=true&developer_data=true&sparkline=false"
    )
    headers = {"accept": "application/json"}
    if demo_api_key:
        headers["x-cg-demo-api-key"] = demo_api_key
    payload = _http_get_json(url, headers=headers)
    market_data = payload.get("market_data")
    market_cap = None
    if isinstance(market_data, Mapping):
        cap_obj = market_data.get("market_cap")
        if isinstance(cap_obj, Mapping):
            market_cap = _to_float(cap_obj.get("usd"))
    return {
        "valuation.market_cap_usd": market_cap,
        "supply.circulating": _to_float(payload.get("circulating_supply")),
        "supply.total": _to_float(payload.get("total_supply") or payload.get("max_supply")),
        "security.score": _to_float(
            payload.get("sentiment_votes_up_percentage")
            or payload.get("community_score")
            or payload.get("developer_score")
        ),
        "funding.last_round": _to_text(payload.get("genesis_date")),
    }


def _call_defillama_fundamental(*, protocol_slug: str) -> Mapping[str, Any]:
    protocol_payload = _http_get_json(f"https://api.llama.fi/protocol/{quote(protocol_slug)}")
    tvl = _to_float(protocol_payload.get("tvl"))
    fees = _to_float(protocol_payload.get("dailyFees") or protocol_payload.get("fees24h"))
    revenue = _to_float(protocol_payload.get("dailyRevenue") or protocol_payload.get("revenue24h"))
    return {
        "defi.tvl_usd": tvl,
        "defi.fees_24h_usd": fees,
        "defi.revenue_24h_usd": revenue,
        "security.score": _to_float(protocol_payload.get("audit_links_count")),
        "funding.last_round": _to_text(protocol_payload.get("listedAt")),
    }


def _http_get_json(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 20.0) -> Mapping[str, Any]:
    request = Request(url, headers=dict(headers or {}), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
        raise RuntimeError(f"http {exc.code} from {url}: {body[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"url error from {url}: {exc}") from exc
    payload = json.loads(data)
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"json payload from {url} must be mapping")
    return payload


def _resolve_attr(root: Any, path: str) -> Any:
    current = root
    for token in path.split("."):
        current = getattr(current, token)
    return current


def _openbb_output_rows(output: Any) -> list[Mapping[str, Any]]:
    if hasattr(output, "to_df"):
        return _df_to_rows(output.to_df())
    if isinstance(output, Mapping):
        if isinstance(output.get("results"), Sequence):
            return [item for item in output["results"] if isinstance(item, Mapping)]
        return [output]
    if isinstance(output, Sequence):
        return [item for item in output if isinstance(item, Mapping)]
    if hasattr(output, "model_dump"):
        dumped = output.model_dump()
        if isinstance(dumped, Mapping):
            results = dumped.get("results")
            if isinstance(results, Sequence):
                return [item for item in results if isinstance(item, Mapping)]
            return [dumped]
    raise RuntimeError("openbb fundamental response shape unsupported")


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


def _pick_float(rows: Sequence[Mapping[str, Any]], *candidates: str) -> float | None:
    for row in rows:
        value = _pick_from_row(row, *candidates)
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _pick_text(rows: Sequence[Mapping[str, Any]], *candidates: str) -> str | None:
    for row in rows:
        value = _pick_from_row(row, *candidates)
        text = _to_text(value)
        if text:
            return text
    return None


def _pick_from_row(row: Mapping[str, Any], *candidates: str) -> Any:
    for key in candidates:
        if key in row:
            return row[key]
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in candidates:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized_date = _date_text(text)
    return normalized_date or text


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
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10:
        try:
            return datetime.fromisoformat(text[0:10]).date().isoformat()
        except ValueError:
            return text[0:10]
    return text


def _default_currency(market: Market) -> str:
    if market == Market.CN_A:
        return "CNY"
    if market == Market.HK:
        return "HKD"
    return "USD"


def _default_timezone(market: Market) -> str:
    if market == Market.CN_A:
        return "Asia/Shanghai"
    if market == Market.HK:
        return "Asia/Hong_Kong"
    if market == Market.US:
        return "America/New_York"
    return "UTC"
