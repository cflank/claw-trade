from __future__ import annotations

from dataclasses import dataclass

import pytest

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
from claw_trade.data_gateway.providers.execution_wrapper import ProviderExecutionWrapper
from claw_trade.data_gateway.providers.fetcher import ProviderCallSpecBindingError


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-wrapper-contract",
        call_id="call-wrapper-contract",
        worker_id="market_analyst",
        market=Market.US,
        domain=PackDomain.MARKET,
        ticker="AAPL",
        company_name="Apple",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="US",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _spec(*, adapter_id: str, provider: str, endpoint: str = "equity_price_historical") -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"{adapter_id}:{endpoint}",
        provider=provider,
        adapter_id=adapter_id,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-t6",
        endpoint=endpoint,
        source_role=SourceRole.MARKET_DATA,
        market=Market.US,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="us_market",
        coverage_quorum=1,
        params={"ticker": "AAPL"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="us.market.ohlcv.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


@dataclass
class _Adapter:
    adapter_id: str
    provider_id: str
    fail: bool = False
    fetch_count: int = 0

    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability(
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                provider_kind=self.provider_kind,
                market=Market.US,
                domain=PackDomain.MARKET,
                endpoint="equity_price_historical",
                source_role=SourceRole.MARKET_DATA,
                expected_schema_id="us.market.ohlcv.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="default",
                cache_ttl_seconds=300,
                required=True,
                attempt_required=True,
                coverage_group="us_market",
                coverage_quorum=1,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )

    def validate_credentials(self) -> CredentialStatus:
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest):  # pragma: no cover - not used in this contract
        del request
        return ()

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        self.fetch_count += 1
        if self.fail:
            raise RuntimeError(f"{self.adapter_id} failed")
        return ProviderFetch(
            payload={"rows": [{"date": "2026-05-17", "close": 100.0}]},
            content_type="application/json",
            source_url="https://api.example.com/market",
            is_empty=False,
            row_count=1,
            provider_request_id=f"req-{self.adapter_id}",
            response_status_code=200,
            response_headers_summary={"content-type": "application/json"},
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        del spec, fetch
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id="us.market.ohlcv.v1",
            rows=({"date": "2026-05-17", "close": 100.0},),
            compact_facts={"row_count": 1},
            row_count=1,
            field_units={"close": "USD"},
            currency="USD",
            timezone="America/New_York",
            source_raw_ref="raw://wrapper-contract",
        )


def test_wrapper_rejects_spec_provider_endpoint_mismatch_before_fetch() -> None:
    adapter = _Adapter(adapter_id="adapter.a", provider_id="provider_a")
    wrapper = ProviderExecutionWrapper(adapter=adapter)
    request = _request()
    mismatch = _spec(adapter_id="adapter.a", provider="provider_b")

    with pytest.raises(ProviderCallSpecBindingError):
        wrapper.fetch(spec=mismatch, request=request)

    assert adapter.fetch_count == 0


def test_failure_on_adapter_a_does_not_invoke_adapter_b_until_coordinator_calls_it() -> None:
    request = _request()
    adapter_a = _Adapter(adapter_id="adapter.a", provider_id="provider_a", fail=True)
    adapter_b = _Adapter(adapter_id="adapter.b", provider_id="provider_b", fail=False)
    wrapper_a = ProviderExecutionWrapper(adapter=adapter_a)
    wrapper_b = ProviderExecutionWrapper(adapter=adapter_b)

    with pytest.raises(RuntimeError, match="adapter.a failed"):
        wrapper_a.fetch(spec=_spec(adapter_id="adapter.a", provider="provider_a"), request=request)

    assert adapter_a.fetch_count == 1
    assert adapter_b.fetch_count == 0

    fetch_b = wrapper_b.fetch(spec=_spec(adapter_id="adapter.b", provider="provider_b"), request=request)
    assert fetch_b.row_count == 1
    assert adapter_b.fetch_count == 1
