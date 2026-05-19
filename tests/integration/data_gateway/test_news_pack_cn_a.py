from __future__ import annotations

from dataclasses import dataclass

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
    ProviderFetch,
    ProviderKind,
    ProviderStatus,
    ReadinessStatus,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.packs.news import NewsPackBuilder
from claw_trade.data_gateway.providers.news import news_capabilities


@dataclass
class _NewsContractAdapter:
    adapter_id: str
    provider_id: str
    source_role: SourceRole
    status: ProviderStatus
    rows: tuple[dict[str, str], ...]
    credential_missing: bool = False
    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION

    def capabilities(self) -> tuple[object, ...]:
        return ()

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
        status=ProviderStatus.SCHEMA_INVALID,
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
    assert ProviderStatus.SCHEMA_INVALID in statuses

    gap_reasons = {gap.reason.value for gap in result.data_gaps}
    assert "credential_missing" in gap_reasons
    assert "rate_limited" in gap_reasons
    assert "empty" in gap_reasons
    assert "schema_invalid" in gap_reasons


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
