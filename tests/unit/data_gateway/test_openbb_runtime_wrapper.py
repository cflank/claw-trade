from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from claw_trade.data_gateway.mcp import runtime_wrapper as wrapper_module
from claw_trade.data_gateway.mcp.runtime_wrapper import (
    OpenBBRuntimeWrapper,
    PackToolInput,
)
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DomainPackResult,
    GatewaySettings,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    ProviderKind,
    Readiness,
    ReadinessStatus,
    RunProviderPlan,
)


def _settings() -> GatewaySettings:
    return GatewaySettings(
        openbb_runtime_url="http://127.0.0.1:8001",
        openbb_home="/tmp/openbb-home",
        mongo_uri="mongodb://127.0.0.1:27017",
        provider_config_version="cfg-v1",
        provider_catalog_path="/tmp/provider-catalog.json",
        provider_catalog={},
        provider_settings={},
        secret_store_uri="env://",
        object_store_uri="file:///tmp/openbb-evidence",
        single_flight_lease_seconds=60,
        raw_payload_inline_max_bytes=4096,
        allowed_declarative_provider_domains=("example.com",),
    )


def _payload() -> dict[str, Any]:
    return {
        "ticker": "00700.HK",
        "market": "HK",
        "profile": "HK",
        "company_name": "腾讯控股",
        "start_date": "2026-05-01",
        "end_date": "2026-05-17",
        "current_date": "2026-05-17",
        "currency": "HKD",
        "run_id": "run-1",
        "call_id": "call-1",
        "worker_id": "market_analyst",
    }


def _pack_result(request: PackRequest) -> DomainPackResult:
    readiness = Readiness(
        status=ReadinessStatus.PARTIAL,
        coverage={"market": "partial"},
        required_domains=("market",),
        missing_domains=("fundamental",),
        blocking_gap_ids=(),
        non_blocking_gap_ids=(),
        root_cause=None,
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="claw-pack-wrapper.v0",
        run_provider_plan_id="run-plan-1",
        call_specs=(),
        attempts=(),
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=(),
        normalized_refs=(),
        normalized_bundle_ref=None,
        payload_hash="sha256:test",
        generated_at="2026-05-17T00:00:00+00:00",
    )
    return DomainPackResult(
        request=request,
        reader_brief_md="## 资料包\n行情资料已汇总。",
        compact_facts={},
        attempts=(),
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=(),
        normalized_refs=(),
        normalized_bundle_ref=None,
        audit_ref="ov://audit/run-1/call-1",
        audit_payload_hash="sha256:test",
        audit_payload=audit,
    )


def _plan(
    *,
    run_id: str = "run-1",
    provider_config_version: str = "cfg-v1",
    market: Market = Market.HK,
    ticker: str = "00700.HK",
) -> RunProviderPlan:
    domains = (
        (
            PackDomain.MARKET,
            PackDomain.FUNDAMENTAL,
            PackDomain.NEWS,
            PackDomain.SOCIAL,
            PackDomain.POLICY,
            PackDomain.HOT_MONEY,
            PackDomain.LOCKUP,
        )
        if market == Market.CN_A
        else (
            PackDomain.MARKET,
            PackDomain.FUNDAMENTAL,
            PackDomain.NEWS,
            PackDomain.SOCIAL,
        )
    )
    return RunProviderPlan(
        run_id=run_id,
        provider_config_version=provider_config_version,
        market=market,
        ticker=ticker,
        domains=domains,
        call_specs=(),
        shared_call_keys=(),
        cache_keys=(),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


@dataclass
class _PlanStore:
    plans: dict[str, RunProviderPlan]

    def write(self, plan: RunProviderPlan) -> str:
        self.plans[plan.run_id] = plan
        return plan.run_id

    def load(self, run_id: str) -> RunProviderPlan:
        return self.plans[run_id]


@dataclass
class _Adapter:
    adapter_id: str = "project.openbb.contract"
    provider_id: str = "contract_provider"
    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION

    def capabilities(self) -> tuple[object, ...]:
        return ()

    def validate_credentials(self):  # pragma: no cover - contract placeholder
        return AdmissionCheckStatus.PASS

    def build_call_specs(self, request: PackRequest) -> tuple[object, ...]:
        return ()

    def fetch(self, spec, request):
        raise NotImplementedError

    def normalize(self, spec, fetch):
        raise NotImplementedError


@dataclass
class _PackService:
    settings: GatewaySettings
    adapters: tuple[_Adapter, ...]
    last_request: PackRequest | None = None
    last_plan: RunProviderPlan | None = None
    last_result: DomainPackResult | None = None

    def get_pack(self, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        self.last_request = request
        self.last_plan = run_plan
        self.last_result = _pack_result(request)
        return self.last_result


def test_rejects_non_provider_adapter() -> None:
    with pytest.raises(TypeError, match="ProviderAdapter"):
        OpenBBRuntimeWrapper(settings=_settings(), adapters=(object(),))


def test_unconfigured_service_returns_explicit_503() -> None:
    wrapper = OpenBBRuntimeWrapper(settings=_settings(), adapters=(_Adapter(),))
    client = TestClient(wrapper.create_pack_fastapi_app())

    response = client.post("/api/v1/claw/get_market_pack", json=_payload())
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "pack_service_unavailable"


def test_success_response_only_contains_brief_and_min_status() -> None:
    service = _PackService(settings=_settings(), adapters=(_Adapter(),))
    plan_store = _PlanStore(plans={"run-1": _plan()})
    wrapper = OpenBBRuntimeWrapper(
        settings=_settings(),
        adapters=(_Adapter(),),
        pack_service=service,
        run_provider_plan_store=plan_store,
    )
    client = TestClient(wrapper.create_pack_fastapi_app())

    response = client.post("/api/v1/claw/get_news_pack", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"reader_brief_md", "status"}
    assert body["reader_brief_md"].startswith("## 资料包")
    assert body["status"] == "partial"
    assert service.last_request is not None
    assert service.last_request.domain == PackDomain.NEWS
    assert service.last_plan is not None
    assert service.last_plan.run_id == "run-1"
    assert service.last_plan.remote_prefetch_allowed is False
    assert service.last_result is not None
    assert service.last_result.audit_payload.openbb_runtime_marker == "openbb-v4.7.0"
    assert service.last_result.audit_payload.openbb_extension_version == "claw-pack-wrapper.v0"


def test_cn_a_extension_routes_use_canonical_domains() -> None:
    service = _PackService(settings=_settings(), adapters=(_Adapter(),))
    plan_store = _PlanStore(plans={"run-cn-a": _plan(run_id="run-cn-a", market=Market.CN_A, ticker="600519")})
    wrapper = OpenBBRuntimeWrapper(
        settings=_settings(),
        adapters=(_Adapter(),),
        pack_service=service,
        run_provider_plan_store=plan_store,
    )
    client = TestClient(wrapper.create_pack_fastapi_app())
    payload = _payload()
    payload.update(
        {
            "market": "CN_A",
            "ticker": "600519",
            "profile": "CN_A",
            "company_name": "贵州茅台",
            "currency": "CNY",
            "run_id": "run-cn-a",
            "worker_id": "policy_analyst",
        }
    )
    expected = {
        "/api/v1/claw/get_policy_pack": PackDomain.POLICY,
        "/api/v1/claw/get_hot_money_pack": PackDomain.HOT_MONEY,
        "/api/v1/claw/get_lockup_pack": PackDomain.LOCKUP,
    }
    for endpoint, domain in expected.items():
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200
        assert response.json()["reader_brief_md"].startswith("## 资料包")
        assert service.last_request is not None
        assert service.last_request.domain == domain


def test_pack_endpoint_requires_run_plan_store() -> None:
    service = _PackService(settings=_settings(), adapters=(_Adapter(),))
    wrapper = OpenBBRuntimeWrapper(
        settings=_settings(),
        adapters=(_Adapter(),),
        pack_service=service,
    )
    client = TestClient(wrapper.create_pack_fastapi_app())

    response = client.post("/api/v1/claw/get_news_pack", json=_payload())

    assert response.status_code == 424
    assert response.json()["detail"]["error"] == "run_plan_missing"
    assert service.last_request is None


def test_pack_endpoint_rejects_provider_config_mismatch() -> None:
    service = _PackService(settings=_settings(), adapters=(_Adapter(),))
    plan_store = _PlanStore(plans={"run-1": _plan(provider_config_version="cfg-v2")})
    wrapper = OpenBBRuntimeWrapper(
        settings=_settings(),
        adapters=(_Adapter(),),
        pack_service=service,
        run_provider_plan_store=plan_store,
    )
    client = TestClient(wrapper.create_pack_fastapi_app())

    response = client.post("/api/v1/claw/get_news_pack", json=_payload())

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "config_version_mismatch"
    assert service.last_request is None


def test_invalid_required_input_returns_422() -> None:
    wrapper = OpenBBRuntimeWrapper(settings=_settings(), adapters=(_Adapter(),))
    client = TestClient(wrapper.create_pack_fastapi_app())
    payload = _payload()
    payload["ticker"] = ""

    response = client.post("/api/v1/claw/get_market_pack", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_input"
    assert "ticker" in response.json()["detail"]["message"]


def test_tool_input_maps_market_and_domain_contract() -> None:
    tool_input = PackToolInput.from_payload(_payload())
    request = tool_input.to_pack_request(PackDomain.SOCIAL)
    assert request.market == Market.HK
    assert request.domain == PackDomain.SOCIAL
    assert request.freshness_policy.max_age_seconds == 300


def test_wrapper_module_does_not_import_legacy_provider_executor() -> None:
    source = inspect.getsource(cast(ModuleType, wrapper_module))
    assert "provider_executor" not in source
    assert "frontline_data_pack.provider_executor" not in source
