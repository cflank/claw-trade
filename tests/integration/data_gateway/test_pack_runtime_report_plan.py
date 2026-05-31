from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from claw_trade.data_gateway.mcp.runtime_wrapper import OpenBBRuntimeWrapper
from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessPolicy,
    GapSeverity,
    GatewaySettings,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    Readiness,
    ReadinessStatus,
    RunProviderPlan,
)
from claw_trade.data_gateway.packs.materializer import materialize_domain_pack_result


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
        "ticker": "600519.SH",
        "market": "CN_A",
        "profile": "CN_A",
        "company_name": "贵州茅台",
        "start_date": "2025-05-29",
        "end_date": "2026-05-29",
        "current_date": "2026-05-29",
        "currency": "CNY",
        "run_id": "run-report-plan",
        "call_id": "call-market",
        "worker_id": "market_analyst",
    }


def _plan() -> RunProviderPlan:
    return RunProviderPlan(
        run_id="run-report-plan",
        provider_config_version="cfg-v1",
        market=Market.CN_A,
        ticker="600519.SH",
        domains=(
            PackDomain.MARKET,
            PackDomain.FUNDAMENTAL,
            PackDomain.NEWS,
            PackDomain.SOCIAL,
            PackDomain.POLICY,
            PackDomain.HOT_MONEY,
            PackDomain.LOCKUP,
        ),
        call_specs=(),
        shared_call_keys=(),
        cache_keys=(),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-29T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def _pack_result(request: PackRequest) -> DomainPackResult:
    readiness = Readiness(
        status=ReadinessStatus.PARTIAL,
        coverage={"market": "partial"},
        required_domains=("market",),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=("gap-1",),
        root_cause=None,
    )
    gap = DataGap(
        gap_id="gap-1",
        domain=PackDomain.MARKET,
        severity=GapSeverity.WARN,
        reason=DataGapReason.CACHED_EMPTY,
        field_path="market.close",
        provider_candidates=("tushare",),
        attempt_ids=(),
        root_cause="mongo://openbb_normalized/rows-1",
        next_action="retry",
        human_readable="gap ref mongo://openbb_normalized/rows-1",
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="claw-pack-runtime.v1",
        run_provider_plan_id=request.run_id,
        call_specs=(),
        attempts=(),
        cache_receipts=(),
        data_gaps=(gap,),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=(),
        normalized_refs=("mongo://openbb_normalized/rows-1",),
        normalized_bundle_ref=None,
        payload_hash="sha256:test",
        generated_at="2026-05-29T00:00:00+00:00",
    )
    return DomainPackResult(
        request=request,
        reader_brief_md="brief mongo://openbb_normalized/rows-1",
        compact_facts={},
        attempts=(),
        cache_receipts=(),
        data_gaps=(gap,),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=(),
        normalized_refs=("mongo://openbb_normalized/rows-1",),
        normalized_bundle_ref=None,
        audit_ref="ov://audit/report-plan",
        audit_payload_hash="sha256:test",
        audit_payload=audit,
    )


@dataclass
class _PlanStore:
    plan: RunProviderPlan
    load_calls: int = 0

    def write(self, plan: RunProviderPlan) -> str:
        self.plan = plan
        return plan.run_id

    def load(self, run_id: str) -> RunProviderPlan:
        self.load_calls += 1
        assert run_id == self.plan.run_id
        return self.plan


@dataclass
class _PackService:
    settings: GatewaySettings
    adapters: tuple[Any, ...] = ()
    last_plan: RunProviderPlan | None = None
    last_request: PackRequest | None = None

    def get_pack(self, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        self.last_plan = run_plan
        self.last_request = request
        return _pack_result(request)


def test_pack_runtime_uses_report_run_plan_from_store() -> None:
    service = _PackService(settings=_settings())
    plan_store = _PlanStore(plan=_plan())
    wrapper = OpenBBRuntimeWrapper(
        settings=_settings(),
        adapters=(),
        pack_service=service,
        run_provider_plan_store=plan_store,
    )
    client = TestClient(wrapper.create_pack_fastapi_app())

    response = client.post("/api/v1/claw/get_market_pack", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"reader_brief_md", "status"}
    assert body["status"] == "partial"
    assert service.last_plan is not None
    assert service.last_plan.remote_prefetch_allowed is False
    assert plan_store.load_calls == 1
    assert service.last_request is not None
    assert service.last_request.domain == PackDomain.MARKET


def test_materialized_pack_hides_protocol_refs_from_worker_visible_text() -> None:
    request = PackRequest(
        run_id="run-report-plan",
        call_id="call-market",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2025-05-29",
        end_date="2026-05-29",
        current_date="2026-05-29",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    materialized = materialize_domain_pack_result(_pack_result(request))
    joined = "\n".join(materialized.worker_visible_material())
    assert "mongo://" not in joined
    assert "[evidence_ref]" in joined
