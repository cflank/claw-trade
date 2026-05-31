from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

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
from claw_trade.data_gateway.providers import managed_requests
from claw_trade.data_gateway.providers import managed_requests as requests
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.tushare_client import create_tushare_pro

_HTTP_TIMEOUT_SECONDS = 15
_DEFAULT_HEADERS = {
    "User-Agent": "claw-trade-openbb-hot-money-adapter/1.0 (research@localhost)",
}
_THS_HSGT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}
_EASTMONEY_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EASTMONEY_PUSH2_UT = "b2884a393a59ad64002292a3e90d46a5"


@dataclass(frozen=True)
class DefaultHotMoneyAdapter:
    adapter_id: str
    provider_id: str
    source_role: SourceRole
    endpoint: str
    expected_schema_id: str
    provider_config_version: str
    rate_limit_policy_id: str
    cache_ttl_seconds: int
    required: bool
    attempt_required: bool
    coverage_group: str
    coverage_quorum: int
    priority: int
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION
    adapter_kind: str = "project_extension"
    credential_requirements: tuple[str, ...] = ()
    data_type: str | None = None
    env: Mapping[str, str] | None = None

    @property
    def market(self) -> Market:
        return Market.CN_A

    @property
    def domain(self) -> PackDomain:
        return PackDomain.HOT_MONEY

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability(
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                provider_kind=self.provider_kind,
                market=self.market,
                domain=self.domain,
                endpoint=self.endpoint,
                source_role=self.source_role,
                expected_schema_id=self.expected_schema_id,
                license_policy_id="personal_research",
                credential_requirements=self.credential_requirements,
                rate_limit_policy_id=self.rate_limit_policy_id,
                cache_ttl_seconds=self.cache_ttl_seconds,
                required=self.required,
                attempt_required=self.attempt_required,
                coverage_group=self.coverage_group,
                coverage_quorum=self.coverage_quorum,
                priority=self.priority,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                data_type=self.data_type,
            ),
        )

    def validate_credentials(self) -> CredentialStatus:
        env = os.environ if self.env is None else self.env
        missing = tuple(key for key in self.credential_requirements if not str(env.get(key, "")).strip())
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
        capability = self.capabilities()[0]
        return (
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
                params=_build_hot_money_params(request=request),
                cache_ttl_seconds=capability.cache_ttl_seconds,
                license_policy_id=capability.license_policy_id,
                expected_schema_id=capability.expected_schema_id,
                priority=capability.priority,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                user_preferred=False,
                data_type=capability.data_type,
            ),
        )

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        params = dict(spec.params)
        request_id = _stable_request_id(spec=spec, params=params)
        rows: tuple[Mapping[str, Any], ...]
        source_url: str
        if self.provider_id.startswith("tushare"):
            rows, source_url = _fetch_tushare_hot_money(endpoint=self.endpoint, params=params, env=self.env)
        elif self.provider_id.startswith("akshare"):
            rows, source_url = _fetch_akshare_hot_money(endpoint=self.endpoint, params=params)
        elif self.coverage_group == "cn_a_hot_money_dragon_tiger":
            rows, source_url = _fetch_cn_a_dragon_tiger(params=params)
        elif self.coverage_group == "cn_a_hot_money_fund_flow":
            rows, source_url = _fetch_cn_a_fund_flow(params=params)
        elif self.coverage_group == "cn_a_hot_money_northbound":
            rows, source_url = _fetch_cn_a_northbound(params=params)
        elif self.coverage_group == "cn_a_hot_money_sector_flow":
            rows, source_url = _fetch_cn_a_sector_flow(params=params)
        else:
            raise RuntimeError(f"unsupported hot_money coverage group: {self.coverage_group}")
        return ProviderFetch(
            payload={"rows": list(rows)},
            content_type="application/json",
            source_url=source_url,
            is_empty=len(rows) == 0,
            row_count=len(rows),
            provider_request_id=request_id,
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        rows = _extract_rows(fetch.payload)
        if not rows:
            return NormalizedResult(
                status=ProviderStatus.EMPTY,
                schema_id=spec.expected_schema_id,
                rows=(),
                compact_facts={"provider": spec.provider, "endpoint": spec.endpoint, "row_count": 0},
                row_count=0,
                field_units={},
                currency="CNY",
                timezone="Asia/Shanghai",
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                error_code="empty",
                error_message=f"{spec.provider}/{spec.endpoint} returned empty hot_money rows",
            )
        normalized_rows: list[dict[str, Any]] = []
        missing_fields: set[str] = set()
        for row in rows:
            normalized, missing = _normalize_hot_money_row(row=row, coverage_group=self.coverage_group)
            missing_fields.update(missing)
            if normalized is not None:
                normalized_rows.append(normalized)
        if not normalized_rows:
            return NormalizedResult(
                status=ProviderStatus.FIELD_MISSING,
                schema_id=spec.expected_schema_id,
                rows=(),
                compact_facts={"provider": spec.provider, "endpoint": spec.endpoint, "row_count": 0},
                row_count=0,
                field_units={},
                currency="CNY",
                timezone="Asia/Shanghai",
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                missing_fields=tuple(sorted(missing_fields or {"as_of"})),
                error_code="field_missing",
                error_message=f"{spec.provider}/{spec.endpoint} missing required hot_money fields",
            )
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id=spec.expected_schema_id,
            rows=tuple(normalized_rows),
            compact_facts={"provider": spec.provider, "endpoint": spec.endpoint, "row_count": len(normalized_rows)},
            row_count=len(normalized_rows),
            field_units={"amount": "CNY"},
            currency="CNY",
            timezone="Asia/Shanghai",
            source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
        )


def hot_money_capabilities() -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for adapter in build_default_hot_money_adapters(provider_config_version="default-catalog"):
        capabilities.extend(adapter.capabilities())
    return tuple(capabilities)


def build_default_hot_money_adapters(
    *,
    provider_config_version: str,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderAdapter, ...]:
    return (
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.tushare.moneyflow.cn_a",
            provider_id="tushare_moneyflow",
            source_role=SourceRole.MARKET_DATA,
            endpoint="moneyflow",
            expected_schema_id="cn_a.hot_money.tushare.moneyflow.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_fund_flow",
            coverage_quorum=1,
            priority=5,
            credential_requirements=("TUSHARE_TOKEN",),
            data_type="cn_a_hot_money_fund_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.tushare.moneyflow_ths.cn_a",
            provider_id="tushare_moneyflow_ths",
            source_role=SourceRole.MARKET_DATA,
            endpoint="moneyflow_ths",
            expected_schema_id="cn_a.hot_money.tushare.moneyflow_ths.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_fund_flow",
            coverage_quorum=1,
            priority=6,
            credential_requirements=("TUSHARE_TOKEN",),
            data_type="cn_a_hot_money_fund_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.tushare.moneyflow_hsgt.cn_a",
            provider_id="tushare_moneyflow_hsgt",
            source_role=SourceRole.MARKET_DATA,
            endpoint="moneyflow_hsgt",
            expected_schema_id="cn_a.hot_money.tushare.moneyflow_hsgt.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_northbound",
            coverage_quorum=1,
            priority=7,
            credential_requirements=("TUSHARE_TOKEN",),
            data_type="cn_a_hot_money_northbound",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.tushare.moneyflow_ind_dc.cn_a",
            provider_id="tushare_moneyflow_ind_dc",
            source_role=SourceRole.MARKET_DATA,
            endpoint="moneyflow_ind_dc",
            expected_schema_id="cn_a.hot_money.tushare.moneyflow_ind_dc.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_sector_flow",
            coverage_quorum=1,
            priority=8,
            credential_requirements=("TUSHARE_TOKEN",),
            data_type="cn_a_hot_money_sector_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.tushare.moneyflow_ind_ths.cn_a",
            provider_id="tushare_moneyflow_ind_ths",
            source_role=SourceRole.MARKET_DATA,
            endpoint="moneyflow_ind_ths",
            expected_schema_id="cn_a.hot_money.tushare.moneyflow_ind_ths.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="tushare.default",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_sector_flow",
            coverage_quorum=1,
            priority=9,
            credential_requirements=("TUSHARE_TOKEN",),
            data_type="cn_a_hot_money_sector_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.akshare.individual_fund_flow.cn_a",
            provider_id="akshare_individual_fund_flow",
            source_role=SourceRole.MARKET_DATA,
            endpoint="stock_individual_fund_flow",
            expected_schema_id="cn_a.hot_money.akshare.individual_fund_flow.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="akshare.stock_individual_fund_flow",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_fund_flow",
            coverage_quorum=1,
            priority=15,
            data_type="cn_a_hot_money_fund_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.akshare.individual_fund_flow_rank.cn_a",
            provider_id="akshare_individual_fund_flow_rank",
            source_role=SourceRole.MARKET_DATA,
            endpoint="stock_individual_fund_flow_rank",
            expected_schema_id="cn_a.hot_money.akshare.individual_fund_flow_rank.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="akshare.stock_individual_fund_flow_rank",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_fund_flow_rank",
            coverage_quorum=1,
            priority=16,
            data_type="cn_a_hot_money_fund_flow_rank",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.akshare.sector_fund_flow_industry.cn_a",
            provider_id="akshare_sector_fund_flow_industry",
            source_role=SourceRole.MARKET_DATA,
            endpoint="stock_sector_fund_flow_rank_industry",
            expected_schema_id="cn_a.hot_money.akshare.sector_fund_flow_rank.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="akshare.stock_sector_fund_flow_rank.industry",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_sector_flow",
            coverage_quorum=1,
            priority=17,
            data_type="cn_a_hot_money_sector_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.akshare.sector_fund_flow_concept.cn_a",
            provider_id="akshare_sector_fund_flow_concept",
            source_role=SourceRole.MARKET_DATA,
            endpoint="stock_sector_fund_flow_rank_concept",
            expected_schema_id="cn_a.hot_money.akshare.sector_fund_flow_rank.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="akshare.stock_sector_fund_flow_rank.concept",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_sector_flow",
            coverage_quorum=1,
            priority=18,
            data_type="cn_a_hot_money_sector_flow",
            env=env,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.eastmoney.dragon_tiger.cn_a",
            provider_id="eastmoney_datacenter",
            source_role=SourceRole.MARKET_DATA,
            endpoint="dragon_tiger",
            expected_schema_id="cn_a.hot_money.dragon_tiger.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.dragon_tiger",
            cache_ttl_seconds=600,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_hot_money_dragon_tiger",
            coverage_quorum=1,
            priority=10,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.eastmoney.fund_flow.cn_a",
            provider_id="eastmoney_push2his",
            source_role=SourceRole.MARKET_DATA,
            endpoint="fund_flow",
            expected_schema_id="cn_a.hot_money.fund_flow.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.fund_flow",
            cache_ttl_seconds=600,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_hot_money_fund_flow",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.ths.northbound.cn_a",
            provider_id="ths_hsgt",
            source_role=SourceRole.MARKET_DATA,
            endpoint="northbound",
            expected_schema_id="cn_a.hot_money.northbound.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="ths.northbound",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_northbound",
            coverage_quorum=1,
            priority=30,
        ),
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.eastmoney.sector_flow.cn_a",
            provider_id="eastmoney_push2",
            source_role=SourceRole.MARKET_DATA,
            endpoint="sector_flow",
            expected_schema_id="cn_a.hot_money.sector_flow.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.sector_flow",
            cache_ttl_seconds=600,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_sector_flow",
            coverage_quorum=1,
            priority=40,
        ),
    )


def _build_hot_money_params(*, request: PackRequest) -> Mapping[str, Any]:
    return {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
    }


def _fetch_cn_a_dragon_tiger(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    url = _eastmoney_datacenter_url(
        report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_expr=f'(SECURITY_CODE="{code}")',
    )
    response = managed_requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = ((body.get("result") or {}).get("data")) or ()
    rows = tuple(
        {
            "as_of": item.get("TRADE_DATE"),
            "name": item.get("SECURITY_NAME_ABBR"),
            "amount": item.get("BILLBOARD_NET_AMT") or item.get("NET_BS_AMT") or item.get("NET_BUY_AMT"),
            "seat_name": item.get("OPERATEDEPT_NAME") or item.get("EXPLAIN"),
            "unit": "CNY",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_fund_flow(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    secid = _eastmoney_secid(str(params.get("ticker", "")))
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?{urlencode(_eastmoney_fflow_params(secid=secid, limit=60))}"
    )
    response = managed_requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    klines = ((body.get("data") or {}).get("klines")) or ()
    rows: list[Mapping[str, Any]] = []
    for line in klines:
        if not isinstance(line, str):
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        rows.append({"as_of": parts[0], "amount": parts[1], "name": "main_net_inflow", "unit": "CNY"})
    return tuple(rows), url


def _fetch_cn_a_northbound(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    del params
    # Approved source evidence: TradingAgents-astock get_northbound_flow uses
    # 同花顺 hsgtApi dayChart endpoint for northbound flow.
    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    response = managed_requests.get(url, headers=_THS_HSGT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    times = body.get("time") or ()
    hgt = body.get("hgt") or ()
    sgt = body.get("sgt") or ()
    rows: list[Mapping[str, Any]] = []
    for idx, time_point in enumerate(times):
        if not isinstance(time_point, str):
            continue
        if idx >= len(hgt) or idx >= len(sgt):
            continue
        hgt_value = _to_float(hgt[idx])
        sgt_value = _to_float(sgt[idx])
        if hgt_value is None or sgt_value is None:
            continue
        rows.append(
            {
                "as_of": time_point,
                "amount": str(hgt_value + sgt_value),
                "name": "northbound_net_flow",
                "unit": "CNY",
            }
        )
    return tuple(rows), url


def _fetch_cn_a_sector_flow(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    del params
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urlencode(
        {
            "pn": 1,
            "pz": 20,
            "po": 1,
            "np": 1,
            "ut": _EASTMONEY_PUSH2_UT,
            "fltt": 2,
            "invt": 2,
            "fid": "f62",
            "fs": "m:90+t:3",
            "fields": "f12,f14,f62,f66,f69,f72,f75,f78,f81,f84,f87",
        }
    )
    response = managed_requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    diff = ((body.get("data") or {}).get("diff")) or ()
    rows = tuple(
        {
            "as_of": None,
            "name": item.get("f14"),
            "amount": item.get("f62"),
            "unit": "CNY",
        }
        for item in diff
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_tushare_hot_money(
    *,
    endpoint: str,
    params: Mapping[str, Any],
    env: Mapping[str, str] | None,
) -> tuple[tuple[Mapping[str, Any], ...], str]:
    env_values = os.environ if env is None else env
    token = str(env_values.get("TUSHARE_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("tushare token missing for hot_money adapter (TUSHARE_TOKEN)")
    pro = create_tushare_pro(token=token, env=env_values)
    ts_code = _normalize_cn_symbol_for_tushare(str(params.get("ticker", "")))
    start = _compact_date(str(params.get("start_date", "")))
    end = _compact_date(str(params.get("end_date", "")))
    current = _compact_date(str(params.get("current_date", "")))
    if endpoint == "moneyflow":
        rows = _df_to_rows(pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end))
        return tuple(_tushare_stock_moneyflow_row(row=row, source="tushare_moneyflow") for row in rows), "https://api.tushare.pro#moneyflow"
    if endpoint == "moneyflow_ths":
        rows = _df_to_rows(pro.moneyflow_ths(ts_code=ts_code, start_date=start, end_date=end))
        return tuple(_tushare_stock_moneyflow_row(row=row, source="tushare_moneyflow_ths") for row in rows), "https://api.tushare.pro#moneyflow_ths"
    if endpoint == "moneyflow_hsgt":
        rows = _df_to_rows(pro.moneyflow_hsgt(start_date=start, end_date=end))
        return tuple(_tushare_hsgt_moneyflow_row(row=row) for row in rows), "https://api.tushare.pro#moneyflow_hsgt"
    if endpoint == "moneyflow_ind_dc":
        rows = _df_to_rows(pro.moneyflow_ind_dc(trade_date=current))
        return tuple(_tushare_sector_moneyflow_row(row=row, source="tushare_moneyflow_ind_dc", scale=1.0) for row in rows), "https://api.tushare.pro#moneyflow_ind_dc"
    if endpoint == "moneyflow_ind_ths":
        rows = _df_to_rows(pro.moneyflow_ind_ths(trade_date=current))
        return tuple(_tushare_sector_moneyflow_row(row=row, source="tushare_moneyflow_ind_ths", scale=100_000_000.0) for row in rows), "https://api.tushare.pro#moneyflow_ind_ths"
    raise RuntimeError(f"unsupported tushare hot_money endpoint: {endpoint}")


def _fetch_akshare_hot_money(
    *,
    endpoint: str,
    params: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], str]:
    import akshare as ak

    ticker = str(params.get("ticker", ""))
    current_date = str(params.get("current_date", ""))
    if endpoint == "stock_individual_fund_flow":
        rows = _df_to_rows(
            ak.stock_individual_fund_flow(
                stock=_normalize_cn_stock_code(ticker),
                market=_akshare_cn_market(ticker),
            )
        )
        return tuple(rows), "https://akshare.akfamily.xyz/data/stock/stock.html"
    if endpoint == "stock_individual_fund_flow_rank":
        rows = _df_to_rows(ak.stock_individual_fund_flow_rank(indicator="今日"))
        return _with_default_as_of(rows, as_of=current_date), "https://akshare.akfamily.xyz/data/stock/stock.html"
    if endpoint == "stock_sector_fund_flow_rank_industry":
        rows = _df_to_rows(ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
        return _with_default_as_of(rows, as_of=current_date), "https://akshare.akfamily.xyz/data/stock/stock.html"
    if endpoint == "stock_sector_fund_flow_rank_concept":
        rows = _df_to_rows(ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流"))
        return _with_default_as_of(rows, as_of=current_date), "https://akshare.akfamily.xyz/data/stock/stock.html"
    raise RuntimeError(f"unsupported akshare hot_money endpoint: {endpoint}")


def _extract_rows(payload: bytes | str | Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return tuple(item for item in rows if isinstance(item, Mapping))
        if isinstance(rows, tuple):
            return tuple(item for item in rows if isinstance(item, Mapping))
        return ()
    if isinstance(payload, bytes):
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return ()
        return _extract_rows(parsed)
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:  # noqa: BLE001
            return ()
        return _extract_rows(parsed)
    return ()


def _normalize_hot_money_row(*, row: Mapping[str, Any], coverage_group: str) -> tuple[dict[str, Any] | None, set[str]]:
    as_of = _pick_first(row, "as_of", "date", "trade_date", "日期")
    amount = _pick_first(
        row,
        "amount",
        "net_amount",
        "net_buy",
        "net_flow",
        "主力净流入-净额",
        "今日主力净流入-净额",
        "3日主力净流入-净额",
        "5日主力净流入-净额",
        "10日主力净流入-净额",
    )
    name = _pick_first(row, "name", "theme", "sector", "seat_name", "名称", "板块名称", "股票名称") or coverage_group
    unit = _pick_first(row, "unit", "currency") or "CNY"

    missing: set[str] = set()
    if coverage_group != "cn_a_hot_money_sector_flow" and not as_of:
        missing.add("as_of")
    if not amount:
        missing.add("amount")
    if missing:
        return (None, missing)

    normalized = {
        "as_of": as_of,
        "name": name,
        "amount": amount,
        "unit": unit,
        "coverage_group": coverage_group,
    }
    if "seat_name" in row:
        normalized["seat_name"] = _pick_first(row, "seat_name")
    return (normalized, set())


def _pick_first(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _df_to_rows(frame: Any) -> list[Mapping[str, Any]]:
    if frame is None:
        return []
    if not hasattr(frame, "to_dict"):
        raise RuntimeError("tushare response is not tabular")
    return [row for row in frame.to_dict(orient="records") if isinstance(row, Mapping)]


def _tushare_stock_moneyflow_row(*, row: Mapping[str, Any], source: str) -> Mapping[str, Any]:
    amount = _pick_first(row, "net_mf_amount", "net_amount")
    if amount is None:
        amount = _computed_stock_net_amount(row)
    return {
        "as_of": row.get("trade_date"),
        "name": source,
        "amount": _scaled_text(amount, 10_000.0),
        "unit": "CNY",
    }


def _tushare_sector_moneyflow_row(*, row: Mapping[str, Any], source: str, scale: float) -> Mapping[str, Any]:
    return {
        "as_of": row.get("trade_date"),
        "name": row.get("name") or row.get("industry") or source,
        "amount": _scaled_text(row.get("net_amount"), scale),
        "unit": "CNY",
    }


def _tushare_hsgt_moneyflow_row(*, row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "as_of": row.get("trade_date"),
        "name": "northbound_net_flow",
        "amount": _scaled_text(row.get("north_money"), 1_000_000.0),
        "unit": "CNY",
    }


def _computed_stock_net_amount(row: Mapping[str, Any]) -> str | None:
    buy = sum(_to_float(row.get(key)) or 0.0 for key in ("buy_elg_amount", "buy_lg_amount", "buy_md_amount", "buy_sm_amount"))
    sell = sum(_to_float(row.get(key)) or 0.0 for key in ("sell_elg_amount", "sell_lg_amount", "sell_md_amount", "sell_sm_amount"))
    if buy == 0.0 and sell == 0.0:
        return None
    return str(buy - sell)


def _scaled_text(value: Any, scale: float) -> str | None:
    number = _to_float(value)
    if number is None:
        return None
    return str(number * scale)


def _compact_date(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    return text.replace("-", "")[:8]


def _with_default_as_of(rows: list[Mapping[str, Any]], *, as_of: str) -> tuple[Mapping[str, Any], ...]:
    out: list[Mapping[str, Any]] = []
    for row in rows:
        mapped = dict(row)
        mapped.setdefault("as_of", as_of)
        out.append(mapped)
    return tuple(out)


def _normalize_cn_stock_code(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.startswith(("SH", "SZ", "BJ")):
        token = token[2:]
    if "." in token:
        token = token.split(".", 1)[0]
    if token.isdigit() and len(token) < 6:
        return token.zfill(6)
    return token


def _akshare_cn_market(ticker: str) -> str:
    token = ticker.strip().upper()
    if token.endswith(".SH") or token.startswith(("6", "9")):
        return "sh"
    if token.endswith(".BJ") or token.startswith(("4", "8")):
        return "bj"
    return "sz"


def _normalize_cn_symbol_for_tushare(ticker: str) -> str:
    token = ticker.strip().upper()
    if "." in token:
        symbol, market = token.split(".", 1)
        return f"{symbol}.{market}"
    if token.startswith(("6", "9")):
        return f"{token}.SH"
    if token.startswith(("4", "8")):
        return f"{token}.BJ"
    return f"{token}.SZ"


def _eastmoney_secid(ticker: str) -> str:
    code = ticker.strip().upper()
    if "." in code:
        symbol, market = code.split(".", 1)
        exchange = "1" if market in {"SH", "SS"} else "0"
        return f"{exchange}.{symbol}"
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _eastmoney_datacenter_url(*, report_name: str, filter_expr: str) -> str:
    return _EASTMONEY_DATACENTER_URL + "?" + urlencode(
        {
            "reportName": report_name,
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "pageNumber": 1,
            "pageSize": 20,
            "filter": filter_expr,
        }
    )


def _eastmoney_fflow_params(*, secid: str, limit: int) -> Mapping[str, Any]:
    return {
        "secid": secid,
        "klt": 101,
        "lmt": limit,
        "ut": _EASTMONEY_PUSH2_UT,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }


def _stable_request_id(*, spec: ProviderCallSpec, params: Mapping[str, Any]) -> str:
    payload = {
        "adapter_id": spec.adapter_id,
        "endpoint": spec.endpoint,
        "provider": spec.provider,
        "params": dict(sorted((key, str(value)) for key, value in params.items())),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"req:{digest[:16]}"


def _raw_ref(*, spec: ProviderCallSpec, fetch: ProviderFetch) -> str:
    payload = {
        "provider": spec.provider,
        "adapter_id": spec.adapter_id,
        "endpoint": spec.endpoint,
        "request_id": fetch.provider_request_id,
        "source_url": fetch.source_url,
        "row_count": fetch.row_count,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"raw://{spec.provider}/{spec.endpoint}/{digest[:16]}"
