from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    FreshnessPolicy,
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
    ReadinessStatus,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.packs.news import NewsPackBuilder
from claw_trade.data_gateway.packs.service import _GateControlledProviderExecutor
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.providers.news import build_default_news_adapters, news_capabilities
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore
from claw_trade.data_gateway.store.single_flight import MongoSingleFlightCoordinator
from pymongo.errors import DuplicateKeyError


class _Collection:
    name = "test_collection"

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        key = query["_id"]
        current = self.docs.get(key)
        if current is None:
            if not upsert:
                return
            current = {}
        current.update(update.get("$setOnInsert", {}))
        if "$inc" in update:
            for field, delta in update["$inc"].items():
                current[field] = int(current.get(field, 0)) + int(delta)
        current.update(update.get("$set", {}))
        if "_id" not in current:
            current["_id"] = key
        self.docs[key] = current

    def insert_one(self, doc: dict[str, Any]) -> None:
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("duplicate")
        self.docs[doc["_id"]] = dict(doc)

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        key = query["_id"]
        if key not in self.docs and not upsert:
            return
        self.docs[key] = dict(doc)

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        key = query.get("_id")
        if not isinstance(key, str):
            return None
        doc = self.docs.get(key)
        if doc is None:
            return None
        for field, expected in query.items():
            if field == "_id":
                continue
            if isinstance(expected, dict):
                if "$lt" in expected and not (doc.get(field) is not None and doc[field] < expected["$lt"]):
                    return None
                if "$lte" in expected and not (doc.get(field) is not None and doc[field] <= expected["$lte"]):
                    return None
                if "$exists" in expected and ((field in doc) != bool(expected["$exists"])):
                    return None
                continue
            if doc.get(field) != expected:
                return None
        return dict(doc)

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: Any | None = None,
    ) -> dict[str, Any] | None:
        del return_document
        key = query["_id"]
        current = self.find_one(query)
        if current is None and not upsert:
            return None
        self.update_one(query if current is not None else {"_id": key}, update, upsert=upsert)
        return self.find_one({"_id": key})


def _helper() -> _GateControlledProviderExecutor:
    attempt_store = MongoAttemptStore(_Collection())
    return _GateControlledProviderExecutor(
        helper=ProviderExecutionEvidenceHelper(
            raw_store=MongoRawPayloadStore(_Collection()),
            normalized_store=MongoNormalizedStore(_Collection()),
            attempt_store=attempt_store,
            http_evidence_store=MongoProviderHttpEvidenceStore(_Collection()),
        ),
        cache_store=MongoCacheStore(_Collection()),
        rate_limit_store=MongoRateLimitStore(_Collection()),
        single_flight=MongoSingleFlightCoordinator(
            collection=_Collection(),
            attempt_store=attempt_store,
            wait_timeout_seconds=0.1,
            poll_interval_seconds=0.001,
        ),
        attempt_store=attempt_store,
    )


@dataclass
class _NewsContractAdapter:
    adapter_id: str
    provider_id: str
    source_role: SourceRole
    status: ProviderStatus
    rows: tuple[dict[str, str], ...]
    credential_missing: bool = False
    market: Market = Market.US
    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION
    endpoint: str | None = None

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        if self.endpoint is None:
            return ()
        return (
            ProviderCapability(
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                provider_kind=self.provider_kind,
                market=self.market,
                domain=PackDomain.NEWS,
                endpoint=self.endpoint,
                source_role=self.source_role,
                expected_schema_id=f"{self.provider_id}.news.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="test",
                cache_ttl_seconds=300,
                required=True,
                attempt_required=True,
                coverage_group=None,
                coverage_quorum=None,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )

    def validate_credentials(self) -> CredentialStatus:
        if self.credential_missing:
            return CredentialStatus(
                status=AdmissionCheckStatus.MISSING,
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                missing_keys=(f"{self.provider_id.upper()}_KEY",),
                root_cause=f"{self.provider_id} key missing",
            )
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest) -> tuple[ProviderCallSpec, ...]:
        del request
        return ()

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload=self.rows,
            content_type="application/json",
            source_url="https://example.com/source",
            is_empty=self.status == ProviderStatus.EMPTY,
            row_count=len(self.rows),
            provider_request_id=f"req-{self.provider_id}",
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        del spec, fetch
        return NormalizedResult(
            status=self.status,
            schema_id=f"{self.provider_id}.news.v1",
            rows=self.rows,
            compact_facts={},
            row_count=len(self.rows),
            field_units={},
            currency=None,
            timezone="UTC",
            source_raw_ref=f"raw://{self.provider_id}",
            error_message=None if self.status == ProviderStatus.REMOTE_SUCCESS else self.status.value,
        )


def _news_request(market: Market) -> PackRequest:
    return PackRequest(
        run_id=f"run-news-{market.value.lower()}",
        call_id="call-news-1",
        worker_id="news_analyst",
        market=market,
        domain=PackDomain.NEWS,
        ticker="TEST",
        company_name="测试公司",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _news_spec(market: Market, adapter: _NewsContractAdapter, endpoint: str) -> ProviderCallSpec:
    adapter.market = market
    adapter.endpoint = endpoint
    return ProviderCallSpec(
        call_key=f"news:{adapter.adapter_id}:{endpoint}",
        provider=adapter.provider_id,
        adapter_id=adapter.adapter_id,
        provider_kind=adapter.provider_kind,
        provider_config_version="cfg-v1",
        endpoint=endpoint,
        source_role=adapter.source_role,
        market=market,
        domain=PackDomain.NEWS,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={"ticker": "TEST"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id=f"{adapter.provider_id}.news.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def _run_plan(market: Market, specs: tuple[ProviderCallSpec, ...]) -> RunProviderPlan:
    return RunProviderPlan(
        run_id=f"run-news-{market.value.lower()}",
        provider_config_version="cfg-v1",
        market=market,
        ticker="TEST",
        domains=(PackDomain.NEWS,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=tuple(f"cache:{idx}" for idx, _ in enumerate(specs, start=1)),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def assert_news_pack_contract_for_market(market: Market) -> None:
    caps = [cap for cap in news_capabilities() if cap.market == market]
    assert caps
    assert any(cap.source_role == SourceRole.OFFICIAL_ORIGINAL for cap in caps)
    assert any(cap.source_role == SourceRole.MACRO_DATA for cap in caps)
    assert any(cap.source_role == SourceRole.SEARCH_DISCOVERY for cap in caps)
    assert all(cap.source_role == SourceRole.SEARCH_DISCOVERY for cap in caps if "search" in cap.adapter_id or "search" in cap.provider)

    official = _NewsContractAdapter(
        adapter_id="project.official_news",
        provider_id="official_news",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "监管公告", "url": "https://example.com/official"},),
    )
    macro = _NewsContractAdapter(
        adapter_id="project.macro_news",
        provider_id="macro_news",
        source_role=SourceRole.MACRO_DATA,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "全球利率动态", "url": "https://example.com/macro"},),
    )
    search = _NewsContractAdapter(
        adapter_id="project.search_api",
        provider_id="search_api",
        source_role=SourceRole.SEARCH_DISCOVERY,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "搜索线索：可追溯网页", "url": "https://example.com/discovery"},),
    )
    key_missing = _NewsContractAdapter(
        adapter_id="project.news_paid",
        provider_id="news_paid",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=(),
        credential_missing=True,
    )
    rate_limited = _NewsContractAdapter(
        adapter_id="project.news_rate_limited",
        provider_id="news_rate_limited",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.RATE_LIMITED,
        rows=(),
    )
    empty = _NewsContractAdapter(
        adapter_id="project.news_empty",
        provider_id="news_empty",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.EMPTY,
        rows=(),
    )
    schema_drift = _NewsContractAdapter(
        adapter_id="project.news_schema_drift",
        provider_id="news_schema_drift",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.FIELD_MISSING,
        rows=({"unexpected": "field"},),
    )
    adapters = (official, macro, search, key_missing, rate_limited, empty, schema_drift)
    specs = tuple(_news_spec(market, adapter, endpoint=f"endpoint_{idx}") for idx, adapter in enumerate(adapters, start=1))
    builder = NewsPackBuilder()
    request = _news_request(market)
    result = builder.build(
        request=request,
        run_plan=_run_plan(market, specs),
        adapters_by_id={adapter.adapter_id: adapter for adapter in adapters},
        provider_execution_helper=_helper(),
    )

    assert result.readiness.status == ReadinessStatus.PARTIAL
    assert "搜索发现" in result.reader_brief_md
    assert "仅作线索" in result.reader_brief_md
    assert "source_role=" not in result.reader_brief_md
    assert "不包含投资判断" in result.reader_brief_md
    assert result.compact_facts["search_discovery"]
    assert all(item["source_role"] != SourceRole.SEARCH_DISCOVERY.value for item in result.compact_facts["news_facts"])

    statuses = {attempt.status for attempt in result.attempts}
    assert ProviderStatus.CREDENTIAL_MISSING in statuses
    assert ProviderStatus.RATE_LIMITED in statuses
    assert ProviderStatus.EMPTY in statuses
    assert ProviderStatus.FIELD_MISSING in statuses

    gap_reasons = {gap.reason.value for gap in result.data_gaps}
    assert "credential_missing" in gap_reasons
    assert "rate_limited" in gap_reasons
    assert "empty" in gap_reasons
    assert "field_missing" in gap_reasons


def test_news_pack_cn_a_source_role_contract() -> None:
    assert_news_pack_contract_for_market(Market.CN_A)


def test_news_pack_crypto_search_discovery_does_not_prove_etf_or_institutional_facts() -> None:
    official = _NewsContractAdapter(
        adapter_id="project.official_news",
        provider_id="official_news",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "项目公告", "url": "https://example.com/official"},),
    )
    search = _NewsContractAdapter(
        adapter_id="project.search_api",
        provider_id="search_api",
        source_role=SourceRole.SEARCH_DISCOVERY,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "搜索线索：BlackRock ETF buying", "url": "https://example.com/search"},),
    )
    event = _NewsContractAdapter(
        adapter_id="project.polymarket",
        provider_id="polymarket",
        source_role=SourceRole.EVENT_EXPECTATION,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "When will Bitcoin hit $150k?；Yes=42.0%", "url": "https://example.com/polymarket"},),
    )
    specs = (
        _news_spec(Market.CRYPTO, official, "official"),
        _news_spec(Market.CRYPTO, search, "search_discovery"),
        _news_spec(Market.CRYPTO, event, "event_markets"),
    )

    result = NewsPackBuilder().build(
        request=_news_request(Market.CRYPTO),
        run_plan=_run_plan(Market.CRYPTO, specs),
        adapters_by_id={official.adapter_id: official, search.adapter_id: search, event.adapter_id: event},
        provider_execution_helper=_helper(),
    )

    assert result.readiness.status == ReadinessStatus.READY
    assert "搜索发现" in result.reader_brief_md
    assert "不作为新闻事实、ETF 资金流、机构持仓/买入、监管事实或事件已发生的证明" in result.reader_brief_md
    assert "When will Bitcoin hit $150k?；Yes=42.0%" in result.reader_brief_md
    assert all(item["source_role"] != SourceRole.SEARCH_DISCOVERY.value for item in result.compact_facts["news_facts"])


def test_news_pack_rejects_search_provider_role_drift() -> None:
    market = Market.CN_A
    search_as_fact = _NewsContractAdapter(
        adapter_id="project.search_api",
        provider_id="search_api",
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "搜索结果不能直接当事实", "url": "https://example.com/search"},),
    )
    spec = _news_spec(market, search_as_fact, "search_discovery")

    result = NewsPackBuilder().build(
        request=_news_request(market),
        run_plan=_run_plan(market, (spec,)),
        adapters_by_id={search_as_fact.adapter_id: search_as_fact},
    )

    assert {attempt.status for attempt in result.attempts} == {ProviderStatus.SCHEMA_INVALID}
    assert result.readiness.status == ReadinessStatus.INSUFFICIENT
    assert "搜索类来源只能作为发现线索" in result.reader_brief_md


def test_cn_a_news_market_fact_providers_do_not_fallback_to_search_discovery(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("market_data provider must not call search_discovery fetch")

    monkeypatch.setattr("claw_trade.data_gateway.providers.news._fetch_google_news_search", _boom)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.news._fetch_eastmoney_company_news",
        lambda *, ticker, company_name: (
            ({"title": f"{ticker}-{company_name}-公司新闻", "url": "https://example.com/company", "published_at": "2026-05-17", "summary": "company"},),
            "https://finance.eastmoney.com/",
        ),
    )
    request = _news_request(Market.CN_A)
    adapters = tuple(adapter for adapter in build_default_news_adapters(provider_config_version="cfg-v1", env={}) if adapter.market == Market.CN_A)
    company = next(adapter for adapter in adapters if adapter.provider_id == "eastmoney_company_news")

    spec = company.build_call_specs(request)[0]
    fetch = company.fetch(spec, request)
    normalized = company.normalize(spec, fetch)
    assert normalized.status == ProviderStatus.REMOTE_SUCCESS


def test_cn_a_news_cls_flash_removed_from_default_catalog() -> None:
    adapters = tuple(adapter for adapter in build_default_news_adapters(provider_config_version="cfg-v1", env={}) if adapter.market == Market.CN_A)
    assert all(adapter.provider_id != "cls_flash" for adapter in adapters)
    assert all(adapter.endpoint != "telegraph" for adapter in adapters)


def test_cn_a_news_discovery_provider_id_and_source_url_are_consistent(monkeypatch) -> None:
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss><channel><item><title>x</title><link>https://example.com/x</link>"
        "<pubDate>Tue, 21 May 2026 00:00:00 GMT</pubDate><description>y</description></item></channel></rss>"
    )
    monkeypatch.setattr("claw_trade.data_gateway.providers.news._http_get_text", lambda url, params=None: rss)
    request = _news_request(Market.CN_A)
    adapters = tuple(adapter for adapter in build_default_news_adapters(provider_config_version="cfg-v1", env={}) if adapter.market == Market.CN_A)
    discovery = next(adapter for adapter in adapters if adapter.source_role == SourceRole.SEARCH_DISCOVERY)
    spec = discovery.build_call_specs(request)[0]
    fetch = discovery.fetch(spec, request)

    assert discovery.provider_id == "google_news"
    assert discovery.source_role == SourceRole.SEARCH_DISCOVERY
    assert fetch.source_url == "https://news.google.com/rss/search"
