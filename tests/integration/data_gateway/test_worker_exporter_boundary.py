from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from claw_trade.artifacts.refs import ApprovedMaterial, L2Index
from claw_trade.config.stage_policy import StagePolicy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools
from claw_trade.data_gateway.mcp.runtime_wrapper import OpenBBRuntimeWrapper
from claw_trade.data_gateway.models import (
    DomainPackResult,
    FreshnessPolicy,
    GatewaySettings,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    Readiness,
    ReadinessStatus,
    RunProviderPlan,
)
from claw_trade.reports import exporter as exporter_module
from claw_trade.reports.exporter import ReportMaterial, render_final_report
from claw_trade.workflow.models import Stage


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


def _request(domain: PackDomain = PackDomain.MARKET) -> PackRequest:
    return PackRequest(
        run_id="run-boundary",
        call_id="call-market",
        worker_id="market_analyst",
        market=Market.HK,
        domain=domain,
        ticker="00700.HK",
        company_name="腾讯控股",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="HKD",
        profile="HK",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _pack_result() -> DomainPackResult:
    request = _request()
    readiness = Readiness(
        status=ReadinessStatus.PARTIAL,
        coverage={"market": "partial"},
        required_domains=("market",),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=(),
        root_cause=None,
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="claw-test",
        run_provider_plan_id=request.run_id,
        call_specs=(),
        attempts=(),
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=("mongo://openbb_provider_raw/raw-1",),
        normalized_refs=("mongo://openbb_normalized/norm-1",),
        normalized_bundle_ref="mongo://openbb_normalized/bundle-1",
        payload_hash="sha256:test",
        generated_at="2026-05-17T00:00:00+00:00",
    )
    return DomainPackResult(
        request=request,
        reader_brief_md="## 行情资料包\n自然语言行情摘要，不含内部证据协议。",
        compact_facts={"latest_close": 530.0},
        attempts=(),
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=audit.raw_refs,
        normalized_refs=audit.normalized_refs,
        normalized_bundle_ref=audit.normalized_bundle_ref,
        audit_ref="viking://resources/workflow/run-boundary/frontline/market_analyst/call-market/evidence/pack_audit.json",
        audit_payload_hash=audit.payload_hash,
        audit_payload=audit,
    )


@dataclass
class _PackService:
    def get_pack(self, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        del request, run_plan
        return _pack_result()


@dataclass
class _PlanStore:
    def load(self, run_id: str) -> RunProviderPlan:
        return RunProviderPlan(
            run_id=run_id,
            provider_config_version="cfg-v1",
            market=Market.HK,
            ticker="00700.HK",
            domains=(PackDomain.MARKET,),
            call_specs=(),
            shared_call_keys=(),
            cache_keys=(),
            rate_limit_plan=(),
            initial_gaps=(),
            generated_at="2026-05-17T00:00:00+00:00",
            remote_prefetch_allowed=False,
        )


def test_worker_primary_material_is_reader_brief_not_audit_protocol() -> None:
    pack = _pack_result()

    assert pack.worker_primary_material_md == pack.reader_brief_md
    assert pack.raw_refs
    assert pack.audit_ref.startswith("viking://")
    for forbidden in ("mongo://", "raw://", "viking://", "audit_payload", "cache_receipts", "provider_attempts"):
        assert forbidden not in pack.worker_primary_material_md


def test_openbb_pack_endpoint_response_hides_audit_and_raw_refs() -> None:
    wrapper = OpenBBRuntimeWrapper(
        settings=_settings(),
        adapters=(),
        pack_service=_PackService(),
        run_provider_plan_store=_PlanStore(),
    )
    client = TestClient(wrapper.create_pack_fastapi_app())

    response = client.post(
        "/api/v1/claw/get_market_pack",
        json={
            "ticker": "00700.HK",
            "market": "HK",
            "profile": "HK",
            "company_name": "腾讯控股",
            "start_date": "2026-05-01",
            "end_date": "2026-05-17",
            "current_date": "2026-05-17",
            "currency": "HKD",
            "run_id": "run-boundary",
            "call_id": "call-market",
            "worker_id": "market_analyst",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"reader_brief_md", "status"}
    rendered = str(body)
    for forbidden in ("mongo://", "raw_refs", "attempts", "cache_receipts", "audit_payload", "viking://"):
        assert forbidden not in rendered


def test_openbb_tool_schema_is_frontline_only_and_downstream_has_no_data_tools(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED", "true")
    registry_result = load_tool_registry()
    assert registry_result.registry is not None
    registry = registry_result.registry
    frontline = {
        "market_analyst": ("cn_a_market_data", "claw_get_market_pack"),
        "fundamental_analyst": ("cn_a_fundamentals_data", "claw_get_fundamental_pack"),
        "news_analyst": ("cn_a_news_data", "claw_get_news_pack"),
        "social_analyst": ("cn_a_social_sentiment", "claw_get_social_pack"),
    }
    for worker_id, (intent, expected_tool) in frontline.items():
        policy = StagePolicy(
            worker_id=worker_id,
            stage=Stage.FRONTLINE,
            profile="CN_A",
            tool_intents=(intent,),
            openviking_access="none",
            source_path=Path("test"),
        )
        assert resolve_tools(policy, registry) == (expected_tool,)

    downstream = (
        ("bull_researcher", Stage.INVESTMENT_DEBATE),
        ("bear_researcher", Stage.INVESTMENT_DEBATE),
        ("research_manager", Stage.INVESTMENT_DECISION),
        ("trader", Stage.TRADE_DECISION),
        ("risk_challenger", Stage.RISK_DEBATE),
        ("risk_guardian", Stage.RISK_DEBATE),
        ("risk_moderator", Stage.RISK_DEBATE),
        ("portfolio_manager", Stage.PORTFOLIO_DECISION),
    )
    for worker_id, stage in downstream:
        policy = StagePolicy(
            worker_id=worker_id,
            stage=stage,
            profile="CN_A",
            tool_intents=(),
            openviking_access="none",
            source_path=Path("test"),
        )
        assert resolve_tools(policy, registry) == ()


def test_exporter_source_reads_approved_material_not_data_gateway_raw_cache() -> None:
    source = inspect.getsource(exporter_module)

    assert "read_approved_l1" in source
    for forbidden in (
        "pymongo",
        "MongoClient",
        "data_gateway.store",
        "openbb_provider_raw",
        "openbb_cache",
        "raw_ref",
        "cache_receipt",
    ):
        assert forbidden not in source


def test_render_final_report_preserves_pm_text_without_rewriting_decision() -> None:
    pm_material = _material("portfolio_manager", Stage.PORTFOLIO_DECISION)
    market_material = _material("market_analyst", Stage.FRONTLINE)
    pm_text = "组合经理最终裁决：维持买入，分三批执行，跌破 500 港元撤退。"

    rendered = render_final_report(
        materials=(market_material, pm_material),
        report_materials=(
            ReportMaterial(material=market_material, l1_text="市场资料：趋势向上。"),
            ReportMaterial(material=pm_material, l1_text=pm_text),
        ),
    )

    assert pm_text in rendered.text
    assert "（PM 原文缺失）" not in rendered.text


def _material(worker_id: str, stage: Stage) -> ApprovedMaterial:
    return ApprovedMaterial(
        material_id=f"mat-{worker_id}",
        run_id="run-boundary",
        call_id=f"call-{worker_id}",
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=f"viking://resources/workflow/run-boundary/{stage.value}/{worker_id}/call/report.md",
        l1_sha256=f"sha-{worker_id}",
        l1_size_bytes=128,
        l2_index_uri=None,
        l2_index=L2Index(entries=(), empty_reason=None, index_uri=None, index_sha256=None, index_size_bytes=None),
        l1_claims=(),
        approved_at="2026-05-17T00:00:00+00:00",
        hard_gate_result_path=Path("runs/run-boundary/calls/call/hard-gate.json"),
    )
