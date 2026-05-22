from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote
from xml.etree import ElementTree

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
    "User-Agent": "claw-trade-openbb-policy-adapter/1.0 (research@localhost)",
}


@dataclass(frozen=True)
class DefaultPolicyAdapter:
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
        return PackDomain.POLICY

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
                params=_build_policy_params(request=request),
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
        if self.coverage_group == "cn_a_policy_official":
            rows, source_url = _fetch_cn_a_policy_official(params=params)
        elif self.coverage_group == "cn_a_policy_news":
            rows, source_url = _fetch_cn_a_policy_news(params=params)
        elif self.coverage_group == "cn_a_policy_macro":
            rows, source_url = _fetch_cn_a_policy_macro(params=params)
        elif self.coverage_group == "cn_a_policy_discovery":
            rows, source_url = _fetch_cn_a_policy_discovery(params=params)
        else:
            raise RuntimeError(f"unsupported policy coverage group: {self.coverage_group}")
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
                currency=None,
                timezone="Asia/Shanghai",
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                error_code="empty",
                error_message=f"{spec.provider}/{spec.endpoint} returned empty policy rows",
            )
        normalized_rows: list[dict[str, Any]] = []
        missing_fields: set[str] = set()
        for row in rows:
            normalized, missing = _normalize_policy_row(row=row, source_role=self.source_role)
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
                currency=None,
                timezone="Asia/Shanghai",
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                missing_fields=tuple(sorted(missing_fields or {"title", "url"})),
                error_code="field_missing",
                error_message=f"{spec.provider}/{spec.endpoint} missing required policy fields",
            )
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id=spec.expected_schema_id,
            rows=tuple(normalized_rows),
            compact_facts={"provider": spec.provider, "endpoint": spec.endpoint, "row_count": len(normalized_rows)},
            row_count=len(normalized_rows),
            field_units={},
            currency=None,
            timezone="Asia/Shanghai",
            source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
        )


def policy_capabilities() -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for adapter in build_default_policy_adapters(provider_config_version="default-catalog"):
        capabilities.extend(adapter.capabilities())
    return tuple(capabilities)


def build_default_policy_adapters(
    *,
    provider_config_version: str,
) -> tuple[ProviderAdapter, ...]:
    return (
        DefaultPolicyAdapter(
            adapter_id="policy.cninfo.cn_a",
            provider_id="cninfo",
            source_role=SourceRole.OFFICIAL_ORIGINAL,
            endpoint="announcements",
            expected_schema_id="cn_a.policy.official.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="cninfo.policy",
            cache_ttl_seconds=900,
            required=True,
            attempt_required=True,
            coverage_group="cn_a_policy_official",
            coverage_quorum=1,
            priority=0,
        ),
        DefaultPolicyAdapter(
            adapter_id="policy.eastmoney.news.cn_a",
            provider_id="eastmoney_news",
            source_role=SourceRole.MARKET_DATA,
            endpoint="policy_news",
            expected_schema_id="cn_a.policy.news.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.policy_news",
            cache_ttl_seconds=600,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_policy_news",
            coverage_quorum=1,
            priority=10,
        ),
        DefaultPolicyAdapter(
            adapter_id="policy.eastmoney.macro.cn_a",
            provider_id="eastmoney_macro",
            source_role=SourceRole.MACRO_DATA,
            endpoint="macro_news",
            expected_schema_id="cn_a.policy.macro.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="eastmoney.policy_macro",
            cache_ttl_seconds=900,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_policy_macro",
            coverage_quorum=1,
            priority=20,
        ),
        DefaultPolicyAdapter(
            adapter_id="policy.google.discovery.cn_a",
            provider_id="google_news",
            source_role=SourceRole.SEARCH_DISCOVERY,
            endpoint="search_discovery",
            expected_schema_id="cn_a.policy.discovery.v1",
            provider_config_version=provider_config_version,
            rate_limit_policy_id="google_news.discovery",
            cache_ttl_seconds=300,
            required=False,
            attempt_required=True,
            coverage_group="cn_a_policy_discovery",
            coverage_quorum=1,
            priority=50,
        ),
    )


def _build_policy_params(*, request: PackRequest) -> Mapping[str, Any]:
    return {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
    }


def _fetch_cn_a_policy_official(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    ticker = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    start_date = str(params.get("start_date", ""))
    end_date = str(params.get("end_date", ""))
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "pageNum": 1,
        "pageSize": 20,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": ticker,
        "searchkey": "政策 监管",
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
    announcements = body.get("announcements") or ()
    rows = tuple(
        {
            "title": item.get("announcementTitle"),
            "url": f"https://www.cninfo.com.cn/new/disclosure/detail?plate={item.get('plate','')}&orgId={item.get('orgId','')}&announcementId={item.get('announcementId','')}",
            "published_at": item.get("announcementTime"),
            "source": "cninfo",
            "policy_level": "company_announcement",
        }
        for item in announcements
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_policy_news(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    ticker = str(params.get("ticker", "")).strip().upper().split(".", 1)[0]
    query = quote(f"{ticker} 政策 监管")
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html?keyword={query}"
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = body.get("LivesList") or body.get("items") or ()
    rows = tuple(
        {
            "title": item.get("title") or item.get("Title"),
            "url": item.get("url") or item.get("Url"),
            "published_at": item.get("showtime") or item.get("ShowTime"),
            "source": "eastmoney_news",
            "policy_level": "industry",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_policy_macro(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    query = quote("中国 宏观 政策")
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html?keyword={query}"
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    items = body.get("LivesList") or body.get("items") or ()
    rows = tuple(
        {
            "title": item.get("title") or item.get("Title"),
            "url": item.get("url") or item.get("Url"),
            "published_at": item.get("showtime") or item.get("ShowTime"),
            "source": "eastmoney_macro",
            "policy_level": "macro",
        }
        for item in items
        if isinstance(item, Mapping)
    )
    return rows, url


def _fetch_cn_a_policy_discovery(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    ticker = str(params.get("ticker", "")).strip()
    company_name = str(params.get("company_name", "")).strip()
    query = quote(" ".join(part for part in (ticker, company_name, "政策", "监管") if part))
    url = f"https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    root = ElementTree.fromstring(response.text)
    rows: list[Mapping[str, Any]] = []
    for item in root.findall("./channel/item")[:20]:
        title = item.findtext("title")
        link = item.findtext("link")
        published_at = item.findtext("pubDate")
        if not title or not link:
            continue
        rows.append(
            {
                "title": title,
                "url": link,
                "published_at": published_at,
                "source": "google_news",
                "policy_level": "discovery",
            }
        )
    return tuple(rows), url


def _extract_rows(payload: bytes | str | Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return tuple(item for item in rows if isinstance(item, Mapping))
        if isinstance(rows, tuple):
            return tuple(item for item in rows if isinstance(item, Mapping))
        if isinstance(payload, list):
            return tuple(item for item in payload if isinstance(item, Mapping))
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


def _normalize_policy_row(*, row: Mapping[str, Any], source_role: SourceRole) -> tuple[dict[str, Any] | None, set[str]]:
    title = _pick_first(row, "title", "Title", "headline", "announcementTitle")
    url = _pick_first(row, "url", "Url", "link")
    published_at = _pick_first(row, "published_at", "pubDate", "showtime", "announcementTime")
    policy_level = _pick_first(row, "policy_level", "level", "category") or "industry"
    source = _pick_first(row, "source", "provider", "media_name") or "unknown"

    missing: set[str] = set()
    if not title:
        missing.add("title")
    if not url:
        missing.add("url")
    if source_role in {SourceRole.OFFICIAL_ORIGINAL, SourceRole.MACRO_DATA, SourceRole.MARKET_DATA} and not published_at:
        missing.add("published_at")
    if missing:
        return (None, missing)

    return (
        {
            "title": title,
            "url": url,
            "published_at": published_at,
            "source": source,
            "source_role": source_role.value,
            "policy_level": policy_level,
            "fact_tier": "fact" if source_role != SourceRole.SEARCH_DISCOVERY else "clue",
        },
        set(),
    )


def _pick_first(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


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
