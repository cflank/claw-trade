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
from claw_trade.data_gateway.packs.social import SocialPackBuilder
from claw_trade.data_gateway.providers.social import social_capabilities
from claw_trade.data_gateway.providers.social_source_roles import is_social_search_discovery_provider


@dataclass
class _SocialContractAdapter:
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
            source_url="https://example.com/social",
            is_empty=self.status == ProviderStatus.EMPTY,
            row_count=len(self.rows),
            provider_request_id=f"req-{self.provider_id}",
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        del spec, fetch
        return NormalizedResult(
            status=self.status,
            schema_id=f"{self.provider_id}.social.v1",
            rows=self.rows,
            compact_facts={},
            row_count=len(self.rows),
            field_units={},
            currency=None,
            timezone="UTC",
            source_raw_ref=f"raw://{self.provider_id}",
            error_message=None if self.status == ProviderStatus.REMOTE_SUCCESS else self.status.value,
        )


def _social_request(market: Market) -> PackRequest:
    return PackRequest(
        run_id=f"run-social-{market.value.lower()}",
        call_id="call-social-1",
        worker_id="social_analyst",
        market=market,
        domain=PackDomain.SOCIAL,
        ticker="TEST",
        company_name="测试公司",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _social_spec(market: Market, adapter: _SocialContractAdapter, endpoint: str) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"social:{adapter.adapter_id}:{endpoint}",
        provider=adapter.provider_id,
        adapter_id=adapter.adapter_id,
        provider_kind=adapter.provider_kind,
        provider_config_version="cfg-v1",
        endpoint=endpoint,
        source_role=adapter.source_role,
        market=market,
        domain=PackDomain.SOCIAL,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={"ticker": "TEST"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id=f"{adapter.provider_id}.social.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def _run_plan(market: Market, specs: tuple[ProviderCallSpec, ...]) -> RunProviderPlan:
    return RunProviderPlan(
        run_id=f"run-social-{market.value.lower()}",
        provider_config_version="cfg-v1",
        market=market,
        ticker="TEST",
        domains=(PackDomain.SOCIAL,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=tuple(f"cache:{idx}" for idx, _ in enumerate(specs, start=1)),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def assert_social_pack_contract_for_market(market: Market) -> None:
    caps = [cap for cap in social_capabilities() if cap.market == market]
    assert caps
    assert any(
        cap.source_role == SourceRole.SEARCH_DISCOVERY
        for cap in caps
        if is_social_search_discovery_provider(f"{cap.provider}:{cap.adapter_id}:{cap.endpoint}")
    )
    alternative_caps = [cap for cap in caps if "alternative_me" in cap.adapter_id or "alternative_me" in cap.provider]
    polymarket_caps = [cap for cap in caps if "polymarket" in cap.adapter_id or "polymarket" in cap.provider]
    assert all(cap.source_role == SourceRole.SOCIAL_AGGREGATE_METRIC for cap in alternative_caps)
    assert all(cap.source_role == SourceRole.EVENT_EXPECTATION for cap in polymarket_caps)

    social_sample = _SocialContractAdapter(
        adapter_id="project.reddit_posts",
        provider_id="reddit_posts",
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "用户讨论原帖", "url": "https://example.com/reddit"},),
    )
    aggregate = _SocialContractAdapter(
        adapter_id="project.lunarcrush_metrics",
        provider_id="lunarcrush_metrics",
        source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "社媒互动指数", "url": "https://example.com/lunar"},),
    )
    alternative = _SocialContractAdapter(
        adapter_id="project.alternative_me",
        provider_id="alternative_me",
        source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "Fear & Greed 71", "url": "https://example.com/alternative"},),
    )
    polymarket = _SocialContractAdapter(
        adapter_id="project.polymarket",
        provider_id="polymarket",
        source_role=SourceRole.EVENT_EXPECTATION,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "事件概率 62%", "url": "https://example.com/polymarket"},),
    )
    discovery = _SocialContractAdapter(
        adapter_id="project.social_search",
        provider_id="social_search",
        source_role=SourceRole.SEARCH_DISCOVERY,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "讨论线索", "url": "https://example.com/discovery"},),
    )
    key_missing = _SocialContractAdapter(
        adapter_id="project.x_posts",
        provider_id="x_posts",
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=(),
        credential_missing=True,
    )
    rate_limited = _SocialContractAdapter(
        adapter_id="project.telegram_posts",
        provider_id="telegram_posts",
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        status=ProviderStatus.RATE_LIMITED,
        rows=(),
    )
    empty = _SocialContractAdapter(
        adapter_id="project.discord_posts",
        provider_id="discord_posts",
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        status=ProviderStatus.EMPTY,
        rows=(),
    )
    schema_drift = _SocialContractAdapter(
        adapter_id="project.social_schema_drift",
        provider_id="social_schema_drift",
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        status=ProviderStatus.SCHEMA_INVALID,
        rows=({"unexpected": "field"},),
    )
    adapters = (
        social_sample,
        aggregate,
        alternative,
        polymarket,
        discovery,
        key_missing,
        rate_limited,
        empty,
        schema_drift,
    )
    specs = tuple(_social_spec(market, adapter, endpoint=f"endpoint_{idx}") for idx, adapter in enumerate(adapters, start=1))
    builder = SocialPackBuilder()
    request = _social_request(market)
    result = builder.build(
        request=request,
        run_plan=_run_plan(market, specs),
        adapters_by_id={adapter.adapter_id: adapter for adapter in adapters},
    )

    assert result.readiness.status == ReadinessStatus.PARTIAL
    assert "Alternative.me 市场级情绪" in result.reader_brief_md
    assert "Polymarket 事件预期" in result.reader_brief_md
    assert "不包含投资判断" in result.reader_brief_md
    assert "source_role=" not in result.reader_brief_md
    assert result.compact_facts["market_level_sentiment"]
    assert all("Fear & Greed" in item["title"] for item in result.compact_facts["market_level_sentiment"])
    assert result.compact_facts["event_expectation"]

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


def test_social_pack_cn_a_source_role_contract() -> None:
    assert_social_pack_contract_for_market(Market.CN_A)


def test_social_pack_crypto_search_discovery_does_not_prove_consensus_or_institutional_facts() -> None:
    aggregate = _SocialContractAdapter(
        adapter_id="project.lunarcrush_metrics",
        provider_id="lunarcrush_metrics",
        source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "社媒互动指数", "url": "https://example.com/lunar"},),
    )
    search = _SocialContractAdapter(
        adapter_id="project.social_search",
        provider_id="social_search",
        source_role=SourceRole.SEARCH_DISCOVERY,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "讨论线索：ETF institution accumulation", "url": "https://example.com/search"},),
    )
    specs = (
        _social_spec(Market.CRYPTO, aggregate, "metrics"),
        _social_spec(Market.CRYPTO, search, "search_discovery"),
    )

    result = SocialPackBuilder().build(
        request=_social_request(Market.CRYPTO),
        run_plan=_run_plan(Market.CRYPTO, specs),
        adapters_by_id={aggregate.adapter_id: aggregate, search.adapter_id: search},
    )

    assert result.readiness.status == ReadinessStatus.READY
    assert "搜索发现" in result.reader_brief_md
    assert "不能作为社交共识、ETF 资金流、机构持仓/买入、链上大户行为或已验证事实" in result.reader_brief_md
    assert result.compact_facts["search_discovery"]


def test_social_pack_rejects_alternative_and_polymarket_role_drift() -> None:
    market = Market.CN_A
    invalid_alternative = _SocialContractAdapter(
        adapter_id="project.alternative_me",
        provider_id="alternative_me",
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "invalid", "url": "https://example.com/invalid"},),
    )
    invalid_polymarket = _SocialContractAdapter(
        adapter_id="project.polymarket",
        provider_id="polymarket",
        source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "invalid", "url": "https://example.com/invalid"},),
    )
    specs = (
        _social_spec(market, invalid_alternative, "alt"),
        _social_spec(market, invalid_polymarket, "pm"),
    )
    result = SocialPackBuilder().build(
        request=_social_request(market),
        run_plan=_run_plan(market, specs),
        adapters_by_id={
            invalid_alternative.adapter_id: invalid_alternative,
            invalid_polymarket.adapter_id: invalid_polymarket,
        },
    )
    statuses = {attempt.status for attempt in result.attempts}
    assert statuses == {ProviderStatus.SCHEMA_INVALID}
    assert result.readiness.status == ReadinessStatus.INSUFFICIENT


def test_social_pack_rejects_search_provider_role_drift() -> None:
    market = Market.CN_A
    search_as_social = _SocialContractAdapter(
        adapter_id="project.social_search",
        provider_id="social_search",
        source_role=SourceRole.SOCIAL_AGGREGATE_METRIC,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=({"title": "搜索结果不能当聚合情绪", "url": "https://example.com/search"},),
    )
    spec = _social_spec(market, search_as_social, "search_discovery")

    result = SocialPackBuilder().build(
        request=_social_request(market),
        run_plan=_run_plan(market, (spec,)),
        adapters_by_id={search_as_social.adapter_id: search_as_social},
    )

    assert {attempt.status for attempt in result.attempts} == {ProviderStatus.SCHEMA_INVALID}
    assert result.readiness.status == ReadinessStatus.INSUFFICIENT
    assert "搜索类来源只能作为发现线索" in result.reader_brief_md
