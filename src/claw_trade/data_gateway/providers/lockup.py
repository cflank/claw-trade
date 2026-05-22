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
    "User-Agent": "claw-trade-openbb-lockup-adapter/1.0 (research@localhost)",
}


@dataclass(frozen=True)
class DefaultLockupAdapter:
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
        return PackDomain.LOCKUP

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
                params=_build_lockup_params(request=request),
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
        if self.coverage_group == "cn_a_lockup_unlock":
            rows, source_url = _fetch_cn_a_unlock(params=params, source_role=self.source_role)
        elif self.coverage_group == "cn_a_lockup_shareholder_count":
            rows, source_url = _fetch_cn_a_shareholder_count(params=params, source_role=self.source_role)
        elif self.coverage_group == "cn_a_lockup_block_trade":
            rows, source_url = _fetch_cn_a_block_trade(params=params)
        elif self.coverage_group == "cn_a_lockup_margin_financing":
            rows, source_url = _fetch_cn_a_margin_financing(params=params)
        elif self.coverage_group == "cn_a_lockup_dividend":
            rows, source_url = _fetch_cn_a_dividend(params=params, source_role=self.source_role)
        elif self.coverage_group == "cn_a_lockup_120d_flow":
            rows, source_url = _fetch_cn_a_120d_flow(params=params)
        else:
            raise RuntimeError(f"unsupported lockup coverage group: {self.coverage_group}")
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
                error_message=f"{spec.provider}/{spec.endpoint} returned empty lockup rows",
            )
        normalized_rows: list[dict[str, Any]] = []
        missing_fields: set[str] = set()
        for row in rows:
            normalized, missing = _normalize_lockup_row(row=row, coverage_group=self.coverage_group)
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
                error_message=f"{spec.provider}/{spec.endpoint} missing required lockup fields",
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


def lockup_capabilities() -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for adapter in build_default_lockup_adapters(provider_config_version="default-catalog"):
        capabilities.extend(adapter.capabilities())
    return tuple(capabilities)


def build_default_lockup_adapters(
    *,
    provider_config_version: str,
) -> tuple[ProviderAdapter, ...]:
    return (
        DefaultLockupAdapter(
            adapter_id="lockup.cninfo.unlock.official.cn_a",
            provider_id="cninfo",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            endpoint="unlock_official",
            expected_schema_id="cn_a.lockup.unlock.official.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="cninfo.unlock",
            cache_ttl_seconds=1800,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_unlock",
            coverage_quorum=1,
            priority=0,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.eastmoney.unlock.cn_a",
            provider_id="eastmoney_datacenter",
            source_role=SourceRole.MARKET_DATA,
            endpoint="unlock_market",
            expected_schema_id="cn_a.lockup.unlock.market.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.unlock",
            cache_ttl_seconds=900,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_lockup_unlock",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.cninfo.shareholder.official.cn_a",
            provider_id="cninfo",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            endpoint="shareholder_official",
            expected_schema_id="cn_a.lockup.shareholder.official.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="cninfo.shareholder",
            cache_ttl_seconds=1800,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_shareholder_count",
            coverage_quorum=1,
            priority=0,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.eastmoney.shareholder.cn_a",
            provider_id="eastmoney_datacenter",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            endpoint="shareholder_market",
            expected_schema_id="cn_a.lockup.shareholder.market.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.shareholder",
            cache_ttl_seconds=900,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_lockup_shareholder_count",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.eastmoney.block_trade.cn_a",
            provider_id="eastmoney_datacenter",
            source_role=SourceRole.MARKET_DATA,
            endpoint="block_trade",
            expected_schema_id="cn_a.lockup.block_trade.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.block_trade",
            cache_ttl_seconds=600,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_block_trade",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.eastmoney.margin.cn_a",
            provider_id="eastmoney_datacenter",
            source_role=SourceRole.MARKET_DATA,
            endpoint="margin_financing",
            expected_schema_id="cn_a.lockup.margin.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.margin",
            cache_ttl_seconds=600,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_margin_financing",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.cninfo.dividend.official.cn_a",
            provider_id="cninfo",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            endpoint="dividend_official",
            expected_schema_id="cn_a.lockup.dividend.official.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="cninfo.dividend",
            cache_ttl_seconds=1800,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_dividend",
            coverage_quorum=1,
            priority=0,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.eastmoney.dividend.cn_a",
            provider_id="eastmoney_datacenter",
            source_role=SourceRole.FUNDAMENTAL_DATA,
            endpoint="dividend_market",
            expected_schema_id="cn_a.lockup.dividend.market.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.dividend",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_dividend",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultLockupAdapter(
            adapter_id="lockup.eastmoney.flow120d.cn_a",
            provider_id="eastmoney_push2his",
            source_role=SourceRole.MARKET_DATA,
            endpoint="flow_120d",
            expected_schema_id="cn_a.lockup.flow120d.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.flow120d",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_lockup_120d_flow",
            coverage_quorum=1,
            priority=30,
        ),
    )


def _build_lockup_params(*, request: PackRequest) -> Mapping[str, Any]:
    return {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
    }


def _fetch_cn_a_unlock(
    *,
    params: Mapping[str, Any],
    source_role: SourceRole,
) -> tuple[tuple[Mapping[str, Any], ...], str]:
    if source_role == SourceRole.OFFICIAL_ORIGINAL:
        return _fetch_cninfo_unlock_official(params=params)
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPT_LIFT_STAGE&pageNumber=1&pageSize=20"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = ((body.get("result") or {}).get("data")) or ()
    rows = tuple(
        {
            "unlock_date": item.get("LIFT_DATE"),
            "shares": item.get("LIFT_NUM"),
            "as_of": item.get("TRADE_DATE"),
            "source": "eastmoney_unlock",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_shareholder_count(
    *,
    params: Mapping[str, Any],
    source_role: SourceRole,
) -> tuple[tuple[Mapping[str, Any], ...], str]:
    if source_role == SourceRole.OFFICIAL_ORIGINAL:
        return _fetch_cninfo_shareholder_official(params=params)
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPT_HOLDERNUM_DET&pageNumber=1&pageSize=20"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = ((body.get("result") or {}).get("data")) or ()
    rows = tuple(
        {
            "as_of": item.get("END_DATE"),
            "shareholder_count": item.get("HOLDER_NUM"),
            "source": "eastmoney_shareholder",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_block_trade(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPT_DATA_BLOCKTRADE&pageNumber=1&pageSize=20"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = ((body.get("result") or {}).get("data")) or ()
    rows = tuple(
        {
            "as_of": item.get("TRADE_DATE"),
            "amount": item.get("DEAL_AMT"),
            "source": "eastmoney_block_trade",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_margin_financing(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    secid = _eastmoney_secid(str(params.get("ticker", "")))
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/margin/get"
        f"?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55&klt=101&lmt=60"
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
        if len(parts) < 2:
            continue
        rows.append({"as_of": parts[0], "amount": parts[1], "source": "eastmoney_margin"})
    return tuple(rows), url


def _fetch_cn_a_dividend(
    *,
    params: Mapping[str, Any],
    source_role: SourceRole,
) -> tuple[tuple[Mapping[str, Any], ...], str]:
    if source_role == SourceRole.OFFICIAL_ORIGINAL:
        return _fetch_cninfo_dividend_official(params=params)
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPT_SHAREBONUS_DET&pageNumber=1&pageSize=20"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = ((body.get("result") or {}).get("data")) or ()
    rows = tuple(
        {
            "as_of": item.get("EX_DIVIDEND_DATE") or item.get("REPORT_DATE"),
            "dividend_plan": item.get("ASSIGN_DESC"),
            "source": "eastmoney_dividend",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_120d_flow(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    secid = _eastmoney_secid(str(params.get("ticker", "")))
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?secid={secid}&klt=101&lmt=120"
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
        rows.append({"as_of": parts[0], "amount": parts[1], "source": "eastmoney_flow120d"})
    return tuple(rows), url


def _fetch_cninfo_unlock_official(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    start_date = str(params.get("start_date", ""))
    end_date = str(params.get("end_date", ""))
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "pageNum": 1,
        "pageSize": 20,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": code,
        "searchkey": "限售 解禁",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(
        url,
        data=payload,
        headers={**_DEFAULT_HEADERS, "X-Requested-With": "XMLHttpRequest"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    rows = tuple(
        {
            "unlock_date": item.get("announcementTime"),
            "shares": None,
            "as_of": item.get("announcementTime"),
            "title": item.get("announcementTitle"),
            "source": "cninfo_unlock",
        }
        for item in (body.get("announcements") or ())
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cninfo_shareholder_official(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    start_date = str(params.get("start_date", ""))
    end_date = str(params.get("end_date", ""))
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "pageNum": 1,
        "pageSize": 20,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": code,
        "searchkey": "股东户数",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(
        url,
        data=payload,
        headers={**_DEFAULT_HEADERS, "X-Requested-With": "XMLHttpRequest"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    rows = tuple(
        {
            "as_of": item.get("announcementTime"),
            "shareholder_count": None,
            "title": item.get("announcementTitle"),
            "source": "cninfo_shareholder",
        }
        for item in (body.get("announcements") or ())
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cninfo_dividend_official(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    code = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    start_date = str(params.get("start_date", ""))
    end_date = str(params.get("end_date", ""))
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "pageNum": 1,
        "pageSize": 20,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": code,
        "searchkey": "分红 送转",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(
        url,
        data=payload,
        headers={**_DEFAULT_HEADERS, "X-Requested-With": "XMLHttpRequest"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    rows = tuple(
        {
            "as_of": item.get("announcementTime"),
            "dividend_plan": item.get("announcementTitle"),
            "source": "cninfo_dividend",
        }
        for item in (body.get("announcements") or ())
        if isinstance(item, Mapping)
    )
    return rows, url


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


def _normalize_lockup_row(*, row: Mapping[str, Any], coverage_group: str) -> tuple[dict[str, Any] | None, set[str]]:
    missing: set[str] = set()
    normalized: dict[str, Any] = {"coverage_group": coverage_group}
    if coverage_group == "cn_a_lockup_unlock":
        unlock_date = _pick_first(row, "unlock_date", "date")
        shares = _pick_first(row, "shares", "unlock_shares")
        if not unlock_date:
            missing.add("unlock_date")
        if not shares:
            missing.add("shares")
        normalized.update({"unlock_date": unlock_date, "shares": shares, "as_of": _pick_first(row, "as_of", "date")})
    elif coverage_group == "cn_a_lockup_shareholder_count":
        as_of = _pick_first(row, "as_of", "date", "report_date")
        holder_count = _pick_first(row, "shareholder_count", "holders")
        if not as_of:
            missing.add("as_of")
        if not holder_count:
            missing.add("shareholder_count")
        normalized.update({"as_of": as_of, "shareholder_count": holder_count})
    elif coverage_group == "cn_a_lockup_block_trade":
        as_of = _pick_first(row, "as_of", "date")
        amount = _pick_first(row, "amount", "trade_amount")
        if not as_of:
            missing.add("as_of")
        if not amount:
            missing.add("amount")
        normalized.update({"as_of": as_of, "amount": amount, "unit": "CNY"})
    elif coverage_group == "cn_a_lockup_margin_financing":
        as_of = _pick_first(row, "as_of", "date")
        amount = _pick_first(row, "amount", "financing_balance")
        if not as_of:
            missing.add("as_of")
        if not amount:
            missing.add("amount")
        normalized.update({"as_of": as_of, "amount": amount, "unit": "CNY"})
    elif coverage_group == "cn_a_lockup_dividend":
        as_of = _pick_first(row, "as_of", "date", "report_date")
        plan = _pick_first(row, "dividend_plan", "plan", "title")
        if not as_of:
            missing.add("as_of")
        if not plan:
            missing.add("dividend_plan")
        normalized.update({"as_of": as_of, "dividend_plan": plan})
    elif coverage_group == "cn_a_lockup_120d_flow":
        as_of = _pick_first(row, "as_of", "date")
        amount = _pick_first(row, "amount", "net_flow")
        if not as_of:
            missing.add("as_of")
        if not amount:
            missing.add("amount")
        normalized.update({"as_of": as_of, "amount": amount, "unit": "CNY"})
    else:
        missing.add("coverage_group")
    if missing:
        return (None, missing)
    source = _pick_first(row, "source", "provider")
    if source:
        normalized["source"] = source
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
