from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
    ProviderKind,
    ProviderStatus,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService


@dataclass
class _Adapter:
    adapter_id: str
    provider_id: str
    source_role: SourceRole
    status: ProviderStatus
    row: Mapping[str, Any]
    credential_missing: bool = False

    adapter_kind: str = "test"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION

    def capabilities(self):  # pragma: no cover - not used by this test
        return ()

    def validate_credentials(self) -> CredentialStatus:
        if self.credential_missing:
            return CredentialStatus(
                status=AdmissionCheckStatus.MISSING,
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                missing_keys=("TEST_KEY",),
                root_cause="missing TEST_KEY",
            )
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest):
        return (_spec(request.market, self.adapter_id, self.provider_id, self.source_role),)

    def fetch(self, spec: ProviderCallSpec, request: PackRequest):
        _ = spec, request
        return object()

    def normalize(self, spec: ProviderCallSpec, fetch):
        _ = spec, fetch
        rows = (dict(self.row),) if self.status == ProviderStatus.REMOTE_SUCCESS else ()
        return NormalizedResult(
            status=self.status,
            schema_id="test.fundamental.v1",
            rows=rows,
            compact_facts=dict(self.row),
            row_count=len(rows),
            field_units={},
            currency="USD",
            timezone="UTC",
            source_raw_ref=f"raw://{self.adapter_id}",
            missing_fields=() if rows else tuple(self.row.keys()),
            error_code=None if rows else "field_missing",
            error_message=None if rows else "missing row",
        )


def _spec(market: Market, adapter_id: str, provider: str, source_role: SourceRole) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"{market.value}:fundamental:{adapter_id}",
        provider=provider,
        adapter_id=adapter_id,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="fundamental",
        source_role=source_role,
        market=market,
        domain=PackDomain.FUNDAMENTAL,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={},
        cache_ttl_seconds=600,
        license_policy_id="personal_research",
        expected_schema_id="test.fundamental.v1",
        priority=1,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def _request(market: Market, ticker: str) -> PackRequest:
    return PackRequest(
        run_id=f"run-{market.value.lower()}",
        call_id="call-1",
        worker_id="fundamental_analyst",
        market=market,
        domain=PackDomain.FUNDAMENTAL,
        ticker=ticker,
        company_name=ticker,
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _run_plan(request: PackRequest, specs: tuple[ProviderCallSpec, ...]) -> RunProviderPlan:
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-v1",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.FUNDAMENTAL,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=tuple(f"cache:{spec.adapter_id}" for spec in specs),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def test_fundamental_readiness_ready_with_complete_equity_fields() -> None:
    request = _request(Market.US, "AAPL")
    adapter = _Adapter(
        adapter_id="fundamental.yfinance.us",
        provider_id="openbb_yfinance",
        source_role=SourceRole.FUNDAMENTAL_DATA,
        status=ProviderStatus.REMOTE_SUCCESS,
        row={"valuation.pe": 28.3, "valuation.pb": 39.5, "financial_indicators.roe": 0.44},
    )
    plan = _run_plan(request, (adapter.build_call_specs(request)[0],))
    result = FundamentalPackService(settings=object(), adapters=(adapter,)).get_pack(request, plan)

    assert result.readiness.status.value == "ready"
    assert result.data_gaps == ()


def test_fundamental_readiness_blocked_when_required_provider_credentials_missing() -> None:
    request = _request(Market.HK, "00700.HK")
    adapter = _Adapter(
        adapter_id="fundamental.tushare.hk",
        provider_id="tushare_hk",
        source_role=SourceRole.FUNDAMENTAL_DATA,
        status=ProviderStatus.REMOTE_SUCCESS,
        row={"valuation.pe": 30.0, "valuation.pb": 8.1, "financial_indicators.roe": 0.35},
        credential_missing=True,
    )
    plan = _run_plan(request, (adapter.build_call_specs(request)[0],))
    result = FundamentalPackService(settings=object(), adapters=(adapter,)).get_pack(request, plan)

    assert result.readiness.status.value == "blocked"
    assert any(gap.reason.value == "credential_missing" for gap in result.data_gaps)
