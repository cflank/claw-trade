from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from collections.abc import Iterable as IterableABC
from typing import Any, Mapping, Sequence
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
from claw_trade.data_gateway.providers.polymarket import fetch_polymarket_events

_HTTP_TIMEOUT_SECONDS = 15
_DEFAULT_HEADERS = {
    "User-Agent": "claw-trade-openbb-news-adapter/1.0 (research@localhost)",
}


@dataclass(frozen=True)
class DefaultNewsAdapter:
    adapter_id: str
    provider_id: str
    market: Market
    source_role: SourceRole
    endpoint: str
    expected_schema_id: str
    provider_kind: ProviderKind
    provider_config_version: str
    rate_limit_policy_id: str
    cache_ttl_seconds: int
    required: bool
    attempt_required: bool
    coverage_group: str | None
    coverage_quorum: int | None
    priority: int
    credential_requirements: tuple[str, ...] = ()
    adapter_kind: str = "project_extension"
    env: Mapping[str, str] | None = None

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability(
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                provider_kind=self.provider_kind,
                market=self.market,
                domain=PackDomain.NEWS,
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
                params=_build_news_params(request=request),
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
        if self.source_role == SourceRole.OFFICIAL_ORIGINAL:
            rows, source_url = _fetch_news_official(market=request.market, ticker=request.ticker, params=params)
        elif self.source_role == SourceRole.MACRO_DATA:
            rows, source_url = _fetch_news_macro(market=request.market, params=params)
        elif self.source_role == SourceRole.SEARCH_DISCOVERY:
            rows, source_url = _fetch_news_search_discovery(params=params)
        elif self.source_role == SourceRole.EVENT_EXPECTATION and self.provider_id == "polymarket":
            rows, source_url = fetch_polymarket_events(params=params)
        else:
            raise RuntimeError(f"unsupported news source role: {self.source_role.value}")
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
                timezone="UTC",
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                error_code="empty",
                error_message=f"{spec.provider}/{spec.endpoint} returned empty news rows",
            )

        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            normalized = _normalize_news_row(row)
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
                timezone="UTC",
                source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
                missing_fields=("title", "url"),
                error_code="field_missing",
                error_message=f"{spec.provider}/{spec.endpoint} missing title/url fields",
            )
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id=spec.expected_schema_id,
            rows=tuple(normalized_rows),
            compact_facts={"provider": spec.provider, "endpoint": spec.endpoint, "row_count": len(normalized_rows)},
            row_count=len(normalized_rows),
            field_units={},
            currency=None,
            timezone="UTC",
            source_raw_ref=_raw_ref(spec=spec, fetch=fetch),
        )


def news_capabilities() -> tuple[ProviderCapability, ...]:
    items: list[ProviderCapability] = []
    for adapter in build_default_news_adapters(provider_config_version="default-catalog"):
        items.extend(adapter.capabilities())
    return tuple(items)


def build_default_news_adapters(
    *,
    provider_config_version: str,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderAdapter, ...]:
    adapters: list[ProviderAdapter] = []
    for market, official_provider, official_endpoint in (
        (Market.CN_A, "cninfo", "announcements"),
        (Market.HK, "hkexnews", "announcements"),
        (Market.US, "sec", "filings"),
        (Market.CRYPTO, "project_official", "announcements"),
    ):
        market_key = market.value.lower()
        adapters.append(
            DefaultNewsAdapter(
                adapter_id=f"news.{official_provider}.{market_key}",
                provider_id=official_provider,
                market=market,
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                endpoint=official_endpoint,
                expected_schema_id=f"{market_key}.news.official.v1",
                provider_kind=ProviderKind.PROJECT_EXTENSION if market in {Market.CN_A, Market.HK, Market.CRYPTO} else ProviderKind.OPENBB_NATIVE,
                provider_config_version=provider_config_version,
                rate_limit_policy_id=f"{official_provider}.news",
                cache_ttl_seconds=900,
                required=True,
                attempt_required=True,
                coverage_group=f"{market_key}_news_fact",
                coverage_quorum=1,
                priority=0,
                env=env,
            )
        )
        adapters.append(
            DefaultNewsAdapter(
                adapter_id=f"news.macro.{market_key}",
                provider_id="fred" if market == Market.US else "global_macro",
                market=market,
                source_role=SourceRole.MACRO_DATA,
                endpoint="macro_news",
                expected_schema_id=f"{market_key}.news.macro.v1",
                provider_kind=ProviderKind.OPENBB_NATIVE if market == Market.US else ProviderKind.PROJECT_EXTENSION,
                provider_config_version=provider_config_version,
                rate_limit_policy_id=f"{market_key}.macro_news",
                cache_ttl_seconds=900,
                required=False,
                attempt_required=True,
                coverage_group=f"{market_key}_news_fact",
                coverage_quorum=1,
                priority=10,
                env=env,
            )
        )
        adapters.append(
            DefaultNewsAdapter(
                adapter_id=f"news.google_news.{market_key}",
                provider_id="google_news",
                market=market,
                source_role=SourceRole.SEARCH_DISCOVERY,
                endpoint="search_discovery",
                expected_schema_id=f"{market_key}.news.discovery.v1",
                provider_kind=ProviderKind.PROJECT_EXTENSION,
                provider_config_version=provider_config_version,
                rate_limit_policy_id="google_news.discovery",
                cache_ttl_seconds=300,
                required=False,
                attempt_required=True,
                coverage_group=None,
                coverage_quorum=None,
                priority=50,
                env=env,
            )
        )
        if market == Market.CRYPTO:
            adapters.append(
                DefaultNewsAdapter(
                    adapter_id="news.polymarket.crypto",
                    provider_id="polymarket",
                    market=market,
                    source_role=SourceRole.EVENT_EXPECTATION,
                    endpoint="event_markets",
                    expected_schema_id="crypto.news.event_expectation.v1",
                    provider_kind=ProviderKind.PROJECT_EXTENSION,
                    provider_config_version=provider_config_version,
                    rate_limit_policy_id="polymarket.event_markets",
                    cache_ttl_seconds=300,
                    required=False,
                    attempt_required=True,
                    coverage_group=None,
                    coverage_quorum=None,
                    priority=30,
                    env=env,
                )
            )
    return tuple(adapters)


def _build_news_params(*, request: PackRequest) -> Mapping[str, Any]:
    return {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "market": request.market.value,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
    }


def _fetch_news_official(*, market: Market, ticker: str, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    if market == Market.CN_A:
        return _fetch_cninfo_announcements(ticker=ticker, start_date=str(params.get("start_date", "")), end_date=str(params.get("end_date", "")))
    if market == Market.HK:
        return _fetch_hkex_regulatory_announcements(ticker=ticker)
    if market == Market.US:
        return _fetch_sec_filings(ticker=ticker)
    if market == Market.CRYPTO:
        return _fetch_crypto_project_official(ticker=ticker)
    raise RuntimeError(f"unsupported market for official news: {market.value}")


def _fetch_news_macro(*, market: Market, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    if market == Market.CRYPTO:
        return _fetch_coindesk_rss()
    country = {
        Market.CN_A: "CHN",
        Market.HK: "HKG",
        Market.US: "USA",
    }.get(market, "WLD")
    return _fetch_world_bank_macro(country=country)


def _fetch_news_search_discovery(*, params: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], str]:
    query = " ".join(
        part
        for part in (
            str(params.get("ticker", "")).strip(),
            str(params.get("company_name", "")).strip(),
            "news",
        )
        if part
    )
    return _fetch_google_news_search(query=query)


def _fetch_cninfo_announcements(*, ticker: str, start_date: str, end_date: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    stock_code = ticker.strip().upper().split(".", 1)[0]
    payload = {
        "pageNum": 1,
        "pageSize": 20,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": stock_code,
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(url, data=payload, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(data.get("announcements"))[:20]:
        adjunct = str(item.get("adjunctUrl") or "").strip()
        rows.append(
            {
                "title": item.get("announcementTitle"),
                "url": f"https://static.cninfo.com.cn/{adjunct.lstrip('/')}" if adjunct else "",
                "published_at": item.get("announcementTime"),
                "summary": item.get("announcementTypeName"),
            }
        )
    return tuple(rows), url


def _fetch_hkex_regulatory_announcements(*, ticker: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://www.hkex.com.hk/Services/RSS-Feeds/regulatory-announcements?sc_lang=en"
    text = _http_get_text(url)
    rows = _parse_rss_items(text)
    if not rows:
        return (), url
    ticker_token = ticker.strip().upper().replace(".HK", "")
    filtered = tuple(
        row for row in rows if ticker_token and (ticker_token in str(row.get("title", "")).upper() or ticker_token in str(row.get("summary", "")).upper())
    )
    return filtered if filtered else rows[:20], url


def _fetch_sec_filings(*, ticker: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    lookup_url = "https://www.sec.gov/files/company_tickers.json"
    company_map = _http_get_json(lookup_url)
    normalized = ticker.strip().upper()
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    cik = ""
    for value in _as_sequence(company_map.values()) if isinstance(company_map, Mapping) else ():
        if str(value.get("ticker", "")).upper() == normalized:
            cik = str(value.get("cik_str", "")).strip()
            break
    if not cik:
        raise RuntimeError(f"sec ticker not found in company_tickers.json: {normalized}")
    submissions_url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    submissions = _http_get_json(submissions_url)
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = _as_sequence(recent.get("accessionNumber"))
    forms = _as_sequence(recent.get("form"))
    dates = _as_sequence(recent.get("filingDate"))
    docs = _as_sequence(recent.get("primaryDocument"))
    rows: list[Mapping[str, Any]] = []
    for idx, accession in enumerate(accessions[:20]):
        accession_token = str(accession).replace("-", "")
        document = str(docs[idx]) if idx < len(docs) else ""
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_token}/{document}" if document else ""
        rows.append(
            {
                "title": f"{forms[idx] if idx < len(forms) else 'FILING'} {accession}",
                "url": filing_url,
                "published_at": dates[idx] if idx < len(dates) else "",
                "summary": "SEC filing",
            }
        )
    return tuple(rows), submissions_url


def _fetch_crypto_project_official(*, ticker: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    repo = {
        "BTC": "bitcoin/bitcoin",
        "ETH": "ethereum/go-ethereum",
        "SOL": "solana-labs/solana",
        "DOGE": "dogecoin/dogecoin",
    }.get(ticker.strip().upper().replace("-USD", "").replace("USDT", ""))
    if not repo:
        raise RuntimeError(f"crypto official source not configured for ticker: {ticker}")
    url = f"https://api.github.com/repos/{repo}/releases?per_page=10"
    releases = _http_get_json(url)
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(releases):
        rows.append(
            {
                "title": item.get("name") or item.get("tag_name"),
                "url": item.get("html_url"),
                "published_at": item.get("published_at") or item.get("created_at"),
                "summary": item.get("body"),
            }
        )
    return tuple(rows), url


def _fetch_world_bank_macro(*, country: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = f"https://api.worldbank.org/v2/country/{country}/indicator/FP.CPI.TOTL.ZG"
    payload = _http_get_json(url, params={"format": "json", "per_page": 5})
    if not isinstance(payload, Sequence) or len(payload) < 2:
        raise RuntimeError("world_bank macro payload malformed")
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(payload[1]):
        value = item.get("value")
        if value is None:
            continue
        rows.append(
            {
                "title": f"CPI YoY {item.get('date')}: {value}",
                "url": url,
                "published_at": item.get("date"),
                "summary": f"world_bank_cpi={value}",
            }
        )
    return tuple(rows), url


def _fetch_coindesk_rss() -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    rows = _parse_rss_items(_http_get_text(url))
    return rows[:20], url


def _fetch_google_news_search(*, query: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://news.google.com/rss/search"
    text = _http_get_text(
        url,
        params={
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        },
    )
    return _parse_rss_items(text)[:20], url


def _parse_rss_items(xml_text: str) -> tuple[Mapping[str, Any], ...]:
    root = ElementTree.fromstring(xml_text)
    items = root.findall(".//item")
    rows: list[Mapping[str, Any]] = []
    for item in items:
        rows.append(
            {
                "title": _xml_text(item, "title"),
                "url": _xml_text(item, "link"),
                "published_at": _xml_text(item, "pubDate"),
                "summary": _xml_text(item, "description"),
            }
        )
    return tuple(rows)


def _xml_text(parent: ElementTree.Element, tag: str) -> str:
    node = parent.find(tag)
    return node.text.strip() if node is not None and node.text else ""


def _extract_rows(payload: bytes | str | Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        for key in ("rows", "items", "announcements", "results", "data", "filings"):
            rows = payload.get(key)
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
                return tuple(item for item in rows if isinstance(item, Mapping))
        return ()
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="ignore")
    elif isinstance(payload, str):
        text = payload
    else:
        return ()
    if not text.strip():
        return ()
    try:
        parsed = json.loads(text)
    except Exception:
        return ()
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
        return tuple(item for item in parsed if isinstance(item, Mapping))
    if isinstance(parsed, Mapping):
        return _extract_rows(parsed)
    return ()


def _normalize_news_row(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    title = str(row.get("title") or row.get("headline") or row.get("announcementTitle") or "").strip()
    url = str(row.get("url") or row.get("link") or "").strip()
    if not title and not url:
        return None
    return {
        "title": title or "未命名新闻条目",
        "url": url,
        "published_at": str(row.get("published_at") or row.get("date") or row.get("filingDate") or "").strip(),
        "summary": str(row.get("summary") or row.get("description") or "").strip(),
    }


def _stable_request_id(*, spec: ProviderCallSpec, params: Mapping[str, Any]) -> str:
    payload = f"{spec.adapter_id}|{spec.provider}|{spec.endpoint}|{sorted(params.items())}"
    return "req_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _raw_ref(*, spec: ProviderCallSpec, fetch: ProviderFetch) -> str:
    request_id = fetch.provider_request_id or "no_request_id"
    return f"raw://{spec.provider}/{spec.endpoint}/{request_id}"


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    if isinstance(value, IterableABC) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        return tuple(value)
    return ()


def _http_get_json(url: str, *, params: Mapping[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _http_get_text(url: str, *, params: Mapping[str, Any] | None = None) -> str:
    response = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text
