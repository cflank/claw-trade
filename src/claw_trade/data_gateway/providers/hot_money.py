from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

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
)
from claw_trade.data_gateway.providers.base import ProviderAdapter

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
                credential_requirements=(),
                rate_limit_policy_id=self.rate_limit_policy_id,
                cache_ttl_seconds=self.cache_ttl_seconds,
                required=self.required,
                attempt_required=self.attempt_required,
                coverage_group=self.coverage_group,
                coverage_quorum=self.coverage_quorum,
                priority=self.priority,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )

    def validate_credentials(self) -> CredentialStatus:
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
            ),
        )

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        params = dict(spec.params)
        request_id = _stable_request_id(spec=spec, params=params)
        rows: tuple[Mapping[str, Any], ...]
        source_url: str
        if self.coverage_group == "cn_a_hot_money_dragon_tiger":
            rows, source_url = _fetch_cn_a_dragon_tiger(params=params)
        elif self.coverage_group == "cn_a_hot_money_fund_flow":
            rows, source_url = _fetch_cn_a_fund_flow(params=params)
        elif self.coverage_group == "cn_a_hot_money_northbound":
            rows, source_url = _fetch_cn_a_northbound(params=params)
        elif self.coverage_group == "cn_a_hot_money_sector_flow":
            rows, source_url = _fetch_cn_a_sector_flow(params=params)
        elif self.coverage_group == "cn_a_hot_money_theme_heat":
            rows, source_url = _fetch_cn_a_theme_heat(params=params)
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
) -> tuple[ProviderAdapter, ...]:
    return (
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
        DefaultHotMoneyAdapter(
            adapter_id="hot_money.ths.theme_heat.cn_a",
            provider_id="ths_hot",
            source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
            endpoint="theme_heat",
            expected_schema_id="cn_a.hot_money.theme_heat.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="ths.theme_heat",
            cache_ttl_seconds=300,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_hot_money_theme_heat",
            coverage_quorum=1,
            priority=50,
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
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&pageNumber=1&pageSize=20"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = ((body.get("result") or {}).get("data")) or ()
    rows = tuple(
        {
            "as_of": item.get("TRADE_DATE"),
            "name": item.get("SECURITY_NAME_ABBR"),
            "amount": item.get("NET_BUY_AMT"),
            "seat_name": item.get("OPERATEDEPT_NAME"),
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
        f"?secid={secid}&klt=101&lmt=60"
    )
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
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
    response = requests.get(url, headers=_THS_HSGT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
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
    url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&fid=f62&fs=m:90+t:2"
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
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


def _fetch_cn_a_theme_heat(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    del params
    url = "https://q.10jqka.com.cn/gn/index/field/addtime/order/desc/page/1/ajax/1/"
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.text
    rows = []
    for line in body.splitlines():
        if "data-code" not in line:
            continue
        rows.append({"as_of": None, "name": "theme_heat", "amount": 0, "unit": "index"})
    return tuple(rows), url


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
    as_of = _pick_first(row, "as_of", "date", "trade_date")
    amount = _pick_first(row, "amount", "net_amount", "net_buy", "net_flow")
    name = _pick_first(row, "name", "theme", "sector", "seat_name") or coverage_group
    unit = _pick_first(row, "unit", "currency") or "CNY"

    missing: set[str] = set()
    if coverage_group != "cn_a_hot_money_sector_flow" and not as_of:
        missing.add("as_of")
    if coverage_group != "cn_a_hot_money_theme_heat" and not amount:
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


def _eastmoney_secid(ticker: str) -> str:
    code = ticker.strip().upper()
    if "." in code:
        symbol, market = code.split(".", 1)
        exchange = "1" if market in {"SH", "SS"} else "0"
        return f"{exchange}.{symbol}"
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


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
