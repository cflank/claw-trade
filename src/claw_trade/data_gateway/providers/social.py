from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

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
from claw_trade.data_gateway.providers.polymarket import fetch_polymarket_events
from claw_trade.instruments.resolver import resolve_crypto_provider_symbols

_HTTP_TIMEOUT_SECONDS = 15
_DEFAULT_HEADERS = {
    "User-Agent": "claw-trade-openbb-social-adapter/1.0 (research@localhost)",
}


@dataclass(frozen=True)
class DefaultSocialAdapter:
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
                domain=PackDomain.SOCIAL,
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
                params=_build_social_params(request=request),
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
        if self.provider_id == "x":
            token = self._required_key("X_BEARER_TOKEN")
            rows, source_url = _fetch_x_posts(query=_social_query(params=params), bearer_token=token)
        elif self.provider_id == "lunarcrush":
            api_key = self._required_key("LUNARCRUSH_API_KEY")
            rows, source_url = _fetch_lunarcrush_metrics(symbol=_symbol_from_params(params), api_key=api_key)
        elif "alternative_me" in self.provider_id:
            rows, source_url = _fetch_alternative_me_sentiment()
        elif "polymarket" in self.provider_id:
            rows, source_url = fetch_polymarket_events(params=params)
        elif self.source_role == SourceRole.SEARCH_DISCOVERY:
            rows, source_url = _fetch_social_search_discovery(query=_social_query(params=params))
        elif self.provider_id == "reddit":
            rows, source_url = _fetch_reddit_posts(query=_social_query(params=params))
        elif self.provider_id == "stocktwits":
            rows, source_url = _fetch_stocktwits(symbol=_symbol_from_params(params))
        elif self.provider_id == "eastmoney_akshare":
            rows, source_url = _fetch_eastmoney_akshare_metrics(symbol=_eastmoney_symbol_from_params(params))
        elif self.provider_id == "ths_concept_hot":
            rows, source_url = _fetch_ths_concept_hot()
        elif self.provider_id == "baidu_concept":
            rows, source_url = _fetch_baidu_concept_blocks(symbol=_symbol_from_params(params))
        else:
            raise RuntimeError(f"social provider is not configured for live fetch: {self.provider_id}/{self.endpoint}")
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
                error_message=f"{spec.provider}/{spec.endpoint} returned empty social rows",
            )
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            normalized = _normalize_social_row(row)
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

    def _required_key(self, key: str) -> str:
        env = os.environ if self.env is None else self.env
        value = str(env.get(key, "")).strip()
        if not value:
            raise RuntimeError(f"credential missing: {key}")
        return value


def social_capabilities() -> tuple[ProviderCapability, ...]:
    capabilities: list[ProviderCapability] = []
    for adapter in build_default_social_adapters(provider_config_version="default-catalog"):
        capabilities.extend(adapter.capabilities())
    return tuple(capabilities)


def build_default_social_adapters(
    *,
    provider_config_version: str,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderAdapter, ...]:
    items: list[ProviderAdapter] = []
    items.extend(
        (
            _capability_adapter(
                market=Market.CN_A,
                provider="ths_concept_hot",
                endpoint="hot_topics",
                source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
                schema_suffix="concept",
                required=True,
                priority=0,
                coverage_group="cn_a_social_concept",
                coverage_quorum=1,
                provider_config_version=provider_config_version,
                env=env,
            ),
            _capability_adapter(
                market=Market.CN_A,
                provider="baidu_concept",
                endpoint="concept_board",
                source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
                schema_suffix="concept",
                required=False,
                priority=10,
                coverage_group="cn_a_social_concept",
                coverage_quorum=1,
                provider_config_version=provider_config_version,
                env=env,
            ),
            _capability_adapter(
                market=Market.CN_A,
                provider="google_news",
                endpoint="search",
                source_role=SourceRole.SEARCH_DISCOVERY,
                schema_suffix="discovery",
                required=False,
                priority=50,
                coverage_group="cn_a_social_search_discovery",
                coverage_quorum=1,
                provider_config_version=provider_config_version,
                env=env,
            ),
        )
    )
    items.extend(_equity_social_adapters(Market.HK, sample_provider="xueqiu_hk", aggregate_provider="eastmoney_hk_guba", provider_config_version=provider_config_version, env=env))
    items.extend(_equity_social_adapters(Market.US, sample_provider="stocktwits", aggregate_provider="reddit", provider_config_version=provider_config_version, env=env))
    items.extend(
        (
            _capability_adapter(
                market=Market.CRYPTO,
                provider="x",
                endpoint="posts",
                source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
                schema_suffix="sample",
                required=False,
                priority=0,
                credential_requirements=("X_BEARER_TOKEN",),
                provider_config_version=provider_config_version,
                env=env,
            ),
            _capability_adapter(
                market=Market.CRYPTO,
                provider="lunarcrush",
                endpoint="social_metrics",
                source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
                schema_suffix="aggregate",
                required=True,
                priority=10,
                credential_requirements=("LUNARCRUSH_API_KEY",),
                provider_config_version=provider_config_version,
                env=env,
            ),
            _capability_adapter(
                market=Market.CRYPTO,
                provider="alternative_me",
                endpoint="fear_greed",
                source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
                schema_suffix="market_sentiment",
                required=False,
                priority=20,
                provider_config_version=provider_config_version,
                env=env,
            ),
            _capability_adapter(
                market=Market.CRYPTO,
                provider="polymarket",
                endpoint="event_markets",
                source_role=SourceRole.EVENT_EXPECTATION,
                schema_suffix="event_expectation",
                required=False,
                priority=30,
                provider_config_version=provider_config_version,
                env=env,
            ),
            _capability_adapter(
                market=Market.CRYPTO,
                provider="google_news",
                endpoint="search_discovery",
                source_role=SourceRole.SEARCH_DISCOVERY,
                schema_suffix="discovery",
                required=False,
                priority=50,
                provider_config_version=provider_config_version,
                env=env,
            ),
        )
    )
    return tuple(items)


def _equity_social_adapters(
    market: Market,
    *,
    sample_provider: str,
    aggregate_provider: str,
    provider_config_version: str,
    env: Mapping[str, str] | None,
) -> tuple[ProviderAdapter, ...]:
    return (
        _capability_adapter(
            market=market,
            provider=sample_provider,
            endpoint="posts",
            source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
            schema_suffix="sample",
            required=False,
            priority=0,
            provider_config_version=provider_config_version,
            env=env,
        ),
        _capability_adapter(
            market=market,
            provider=aggregate_provider,
            endpoint="social_metrics",
            source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
            schema_suffix="aggregate",
            required=True,
            priority=10,
            provider_config_version=provider_config_version,
            env=env,
        ),
        _capability_adapter(
            market=market,
            provider="google_news",
            endpoint="search_discovery",
            source_role=SourceRole.SEARCH_DISCOVERY,
            schema_suffix="discovery",
            required=False,
            priority=50,
            provider_config_version=provider_config_version,
            env=env,
        ),
    )


def _capability_adapter(
    *,
    market: Market,
    provider: str,
    endpoint: str,
    source_role: SourceRole,
    schema_suffix: str,
    required: bool,
    priority: int,
    provider_config_version: str,
    env: Mapping[str, str] | None,
    credential_requirements: tuple[str, ...] = (),
    coverage_group: str | None = None,
    coverage_quorum: int | None = None,
) -> ProviderAdapter:
    market_key = market.value.lower()
    return DefaultSocialAdapter(
        adapter_id=f"social.{provider}.{market_key}",
        provider_id=provider,
        market=market,
        source_role=source_role,
        endpoint=endpoint,
        expected_schema_id=f"{market_key}.social.{schema_suffix}.v1",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version=provider_config_version,
        rate_limit_policy_id=f"{provider}.{endpoint}",
        cache_ttl_seconds=300,
        required=required,
        attempt_required=True,
        coverage_group=coverage_group
        if coverage_group is not None
        else (f"{market_key}_social_core" if source_role in {SourceRole.SOCIAL_ORIGINAL_SAMPLE, SourceRole.SOCIAL_AGGREGATE_METRIC} else None),
        coverage_quorum=coverage_quorum
        if coverage_quorum is not None
        else (1 if source_role in {SourceRole.SOCIAL_ORIGINAL_SAMPLE, SourceRole.SOCIAL_AGGREGATE_METRIC} else None),
        priority=priority,
        credential_requirements=credential_requirements,
        env=env,
    )


def _build_social_params(*, request: PackRequest) -> Mapping[str, Any]:
    return {
        "ticker": request.ticker,
        "company_name": request.company_name,
        "market": request.market.value,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "current_date": request.current_date,
    }


def _social_query(*, params: Mapping[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            str(params.get("ticker", "")).strip(),
            str(params.get("company_name", "")).strip(),
            "discussion",
        )
        if part
    )


def _symbol_from_params(params: Mapping[str, Any]) -> str:
    ticker = str(params.get("ticker", "")).strip().upper()
    if ticker and str(params.get("market", "")).strip().upper() == "CRYPTO":
        return resolve_crypto_provider_symbols(ticker).crypto_base_symbol or ticker
    if "." in ticker:
        ticker = ticker.split(".", 1)[0]
    ticker = ticker.replace("-USD", "").replace("USDT", "")
    return ticker or "BTC"


def _eastmoney_symbol_from_params(params: Mapping[str, Any]) -> str:
    ticker = str(params.get("ticker", "")).strip().upper()
    code, _, exchange = ticker.partition(".")
    if exchange == "SH":
        return f"SH{code.zfill(6)}"
    if exchange == "SZ":
        return f"SZ{code.zfill(6)}"
    if exchange == "HK":
        return f"HK{code.zfill(5)}"
    if code.isdigit():
        return f"SH{code.zfill(6)}" if code.startswith(("5", "6", "9")) else f"SZ{code.zfill(6)}"
    return ticker


def _fetch_x_posts(*, query: str, bearer_token: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://api.x.com/2/tweets/search/recent"
    response = managed_requests.get(
        url,
        params={"query": query, "max_results": 20, "tweet.fields": "created_at"},
        headers={**_DEFAULT_HEADERS, "Authorization": f"Bearer {bearer_token}"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(payload.get("data")):
        tweet_id = str(item.get("id") or "").strip()
        rows.append(
            {
                "title": str(item.get("text") or "").strip(),
                "url": f"https://x.com/i/web/status/{tweet_id}" if tweet_id else "",
                "published_at": item.get("created_at"),
                "summary": "x_recent_post",
            }
        )
    return tuple(rows), url


def _fetch_lunarcrush_metrics(*, symbol: str, api_key: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://lunarcrush.com/api4/public/coins/list/v2"
    response = managed_requests.get(
        url,
        params={"symbol": symbol, "limit": 5},
        headers={**_DEFAULT_HEADERS, "Authorization": f"Bearer {api_key}"},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(payload.get("data")):
        alt_rank = item.get("alt_rank")
        galaxy_score = item.get("galaxy_score")
        rows.append(
            {
                "title": f"{item.get('symbol') or symbol} alt_rank={alt_rank} galaxy_score={galaxy_score}",
                "url": "https://lunarcrush.com",
                "published_at": item.get("time"),
                "summary": "lunarcrush_social_metrics",
            }
        )
    return tuple(rows), url


def _fetch_alternative_me_sentiment() -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://api.alternative.me/fng/"
    payload = _http_get_json(url, params={"limit": 3})
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(payload.get("data")) if isinstance(payload, Mapping) else ():
        rows.append(
            {
                "title": f"Fear & Greed {item.get('value')} ({item.get('value_classification')})",
                "url": "https://alternative.me/crypto/fear-and-greed-index/",
                "published_at": item.get("timestamp"),
                "summary": "alternative_me_market_sentiment",
            }
        )
    return tuple(rows), url


def _fetch_social_search_discovery(*, query: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
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


def _fetch_reddit_posts(*, query: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://www.reddit.com/search.json"
    payload = _http_get_json(url, params={"q": query, "limit": 20, "sort": "new"})
    children = payload.get("data", {}).get("children", []) if isinstance(payload, Mapping) else []
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(children):
        data = item.get("data", {}) if isinstance(item, Mapping) else {}
        permalink = str(data.get("permalink") or "").strip()
        rows.append(
            {
                "title": data.get("title"),
                "url": f"https://www.reddit.com{permalink}" if permalink else "",
                "published_at": data.get("created_utc"),
                "summary": data.get("selftext"),
            }
        )
    return tuple(rows), url


def _fetch_stocktwits(*, symbol: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    symbol_key = symbol.replace(".US", "").replace(".HK", "").replace(".SZ", "")
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol_key}.json"
    payload = _http_get_json(url)
    rows: list[Mapping[str, Any]] = []
    for item in _as_sequence(payload.get("messages")) if isinstance(payload, Mapping) else ():
        user = item.get("user", {}) if isinstance(item, Mapping) else {}
        user_name = str(user.get("username") or "").strip()
        rows.append(
            {
                "title": str(item.get("body") or "").strip(),
                "url": f"https://stocktwits.com/{user_name}" if user_name else "https://stocktwits.com",
                "published_at": item.get("created_at"),
                "summary": "stocktwits_social_post",
            }
        )
    return tuple(rows), url


def _fetch_eastmoney_akshare_metrics(*, symbol: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    import akshare as ak

    rows: list[Mapping[str, Any]] = []
    try:
        hot_rank = _df_to_rows(ak.stock_hot_rank_latest_em(symbol=symbol))
    except Exception:
        hot_rank = []
    for row in hot_rank[:10]:
        rows.append(_eastmoney_metric_row(row=row, summary="eastmoney_hot_rank"))

    try:
        keywords = _df_to_rows(ak.stock_hot_keyword_em(symbol=symbol))
    except Exception:
        keywords = []
    for row in keywords[:10]:
        rows.append(_eastmoney_metric_row(row=row, summary="eastmoney_hot_keyword"))

    return tuple(rows), "https://quote.eastmoney.com"


def _fetch_ths_concept_hot(*, limit: int = 20) -> tuple[tuple[Mapping[str, Any], ...], str]:
    import akshare as ak

    frame_rows = _df_to_rows(ak.stock_board_concept_name_ths())
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(frame_rows[: max(1, limit)], start=1):
        mapped = _concept_metric_row(row=row, summary="ths_concept_board", rank=index, url="https://q.10jqka.com.cn/gn/")
        if mapped is not None:
            rows.append(mapped)
    return tuple(rows), "https://q.10jqka.com.cn/gn/"


def _fetch_baidu_concept_blocks(*, symbol: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    url = "https://finance.pae.baidu.com/api/getrelatedblock"
    response = managed_requests.get(
        url,
        params={"code": symbol, "market": "ab", "typeCode": "all", "finClientType": "pc"},
        headers={
            **_DEFAULT_HEADERS,
            "Accept": "application/vnd.finance-web.v1+json",
            "Origin": "https://gushitong.baidu.com",
            "Referer": "https://gushitong.baidu.com/",
        },
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("baidu concept payload must be mapping")
    result_code = payload.get("ResultCode", 0)
    if str(result_code) not in {"0", "None"}:
        raise RuntimeError(f"baidu concept payload returned ResultCode={result_code}")
    rows: list[Mapping[str, Any]] = []
    for block in _as_sequence(payload.get("Result")):
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("type") or "").strip()
        for item in _as_sequence(block.get("list")):
            if not isinstance(item, Mapping):
                continue
            mapped = _concept_metric_row(
                row=item,
                summary=f"baidu_related_block:{block_type}" if block_type else "baidu_related_block",
                rank=len(rows) + 1,
                url="https://gushitong.baidu.com/",
            )
            if mapped is not None:
                rows.append(mapped)
    return tuple(rows), url


def _df_to_rows(frame: Any) -> list[Mapping[str, Any]]:
    if frame is None:
        return []
    if not hasattr(frame, "to_dict"):
        raise RuntimeError("provider response is not tabular")
    if hasattr(frame, "index") and getattr(frame.index, "name", None):
        frame = frame.reset_index()
    rows = frame.to_dict(orient="records")
    return [row for row in rows if isinstance(row, Mapping)]


def _concept_metric_row(*, row: Mapping[str, Any], summary: str, rank: int, url: str) -> Mapping[str, Any] | None:
    name = _first_text(row, "概念名称", "板块名称", "name", "blockName", "名称")
    if not name:
        return None
    title_parts = [name, f"rank={rank}"]
    change_pct = _first_text(row, "涨跌幅", "涨幅", "increase", "change_pct", "change")
    if change_pct:
        title_parts.append(f"change={change_pct}")
    heat = _first_text(row, "热度", "热度值", "heat", "hot", "关注度")
    if heat:
        title_parts.append(f"heat={heat}")
    return {
        "title": " / ".join(title_parts),
        "url": _first_text(row, "网址", "url", "link") or url,
        "published_at": _first_text(row, "更新时间", "日期", "date", "time"),
        "summary": summary,
    }


def _eastmoney_metric_row(*, row: Mapping[str, Any], summary: str) -> Mapping[str, Any]:
    title_parts = []
    for key in ("关键词", "概念名称", "股票名称", "名称", "keyword", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            title_parts.append(value)
            break
    for key in ("排名", "当前排名", "rank"):
        value = str(row.get(key) or "").strip()
        if value:
            title_parts.append(f"rank={value}")
            break
    for key in ("热度", "热度值", "heat", "heat_value"):
        value = str(row.get(key) or "").strip()
        if value:
            title_parts.append(f"heat={value}")
            break
    return {
        "title": " / ".join(title_parts) if title_parts else summary,
        "url": "https://quote.eastmoney.com",
        "published_at": str(row.get("更新时间") or row.get("日期") or row.get("update_time") or ""),
        "summary": summary,
    }


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


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
        for key in ("rows", "items", "results", "data", "messages"):
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
    if isinstance(parsed, Mapping):
        return _extract_rows(parsed)
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
        return tuple(item for item in parsed if isinstance(item, Mapping))
    return ()


def _normalize_social_row(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    title = str(row.get("title") or row.get("headline") or row.get("summary") or "").strip()
    url = str(row.get("url") or row.get("link") or "").strip()
    if not title and not url:
        return None
    return {
        "title": title or "未命名社交条目",
        "url": url,
        "published_at": str(row.get("published_at") or row.get("date") or row.get("timestamp") or "").strip(),
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
    return ()


def _http_get_json(url: str, *, params: Mapping[str, Any] | None = None) -> Any:
    response = managed_requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _http_get_text(url: str, *, params: Mapping[str, Any] | None = None) -> str:
    response = managed_requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text
