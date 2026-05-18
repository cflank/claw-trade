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
    ProviderCapability,
    ProviderFetch,
    ProviderKind,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.providers.base import ProviderAdapter


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="000001.SZ",
        company_name="平安银行",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _capability(kind: ProviderKind, adapter_id: str, provider: str) -> ProviderCapability:
    return ProviderCapability(
        provider=provider,
        adapter_id=adapter_id,
        provider_kind=kind,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        expected_schema_id="market.daily.v1",
        license_policy_id="personal_research",
        credential_requirements=("TUSHARE_TOKEN",),
        rate_limit_policy_id="default",
        cache_ttl_seconds=300,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
    )


def _spec(kind: ProviderKind, adapter_id: str, provider: str) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"{provider}:daily",
        provider=provider,
        adapter_id=adapter_id,
        provider_kind=kind,
        provider_config_version="cfg-v1",
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={"ticker": "000001.SZ"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="market.daily.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


@dataclass
class _AdapterImpl:
    adapter_id: str
    provider_id: str
    adapter_kind: str
    provider_kind: ProviderKind

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (_capability(self.provider_kind, self.adapter_id, self.provider_id),)

    def validate_credentials(self) -> CredentialStatus:
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest) -> tuple[ProviderCallSpec, ...]:
        return (_spec(self.provider_kind, self.adapter_id, self.provider_id),)

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        return ProviderFetch(
            payload={"ticker": request.ticker},
            content_type="application/json",
            source_url="https://example.com",
            is_empty=False,
            row_count=1,
            provider_request_id="req-1",
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id=spec.expected_schema_id,
            rows=({"ticker": "000001.SZ"},),
            compact_facts={"ticker": "000001.SZ"},
            row_count=1,
            field_units={},
            currency="CNY",
            timezone="Asia/Shanghai",
            source_raw_ref="raw://1",
        )


def test_all_provider_kinds_share_one_adapter_protocol() -> None:
    request = _request()
    adapters = (
        _AdapterImpl(
            adapter_id="openbb.fmp",
            provider_id="fmp",
            adapter_kind="openbb_native",
            provider_kind=ProviderKind.OPENBB_NATIVE,
        ),
        _AdapterImpl(
            adapter_id="project.tushare",
            provider_id="tushare",
            adapter_kind="project_extension",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
        ),
        _AdapterImpl(
            adapter_id="user.custom_rss",
            provider_id="custom_rss",
            adapter_kind="user_declarative",
            provider_kind=ProviderKind.USER_DECLARATIVE,
        ),
    )

    for adapter in adapters:
        assert isinstance(adapter, ProviderAdapter)
        specs = adapter.build_call_specs(request)
        assert specs
        fetch = adapter.fetch(specs[0], request)
        normalized = adapter.normalize(specs[0], fetch)
        assert normalized.schema_id == specs[0].expected_schema_id
