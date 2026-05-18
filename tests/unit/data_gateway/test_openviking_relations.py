from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from claw_trade.artifacts.openviking_client import OpenVikingAccessError, OpenVikingClient, OpenVikingStat
from claw_trade.artifacts.refs import ApprovedMaterial, L2Entry, L2Index
from claw_trade.data_gateway.models import (
    CacheReceipt,
    ChartAsset,
    Conflict,
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessPolicy,
    GapSeverity,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderKind,
    ProviderStatus,
    Readiness,
    ReadinessStatus,
    SourceRole,
)
from claw_trade.data_gateway.openviking import FinalReportClaim, OpenVikingMaterialPlane
from claw_trade.workflow.models import Stage


@dataclass
class _Backend:
    linked_relations: list[dict[str, object]]

    def ensure_namespace(self, namespace: str) -> None:
        return None

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return b"content"

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        return L2Index(entries=(), empty_reason=None, index_uri=uri, index_sha256=None, index_size_bytes=None)

    def fetch_stat_by_uri(self, uri: str) -> OpenVikingStat:
        return OpenVikingStat(uri=uri, ok=True, sha256="0" * 64, size_bytes=7, exists=True, is_dir=False)

    def fetch_receipt_by_path(self, receipt_path: Path):  # pragma: no cover - not used in this suite
        raise FileNotFoundError(receipt_path)

    def link_relation(self, relation: dict[str, object]) -> dict[str, object]:
        self.linked_relations.append(relation)
        return {"status": "ok"}


@dataclass
class _BackendRelationBlocked(_Backend):
    def link_relation(self, relation: dict[str, object]) -> dict[str, object]:
        del relation
        raise OpenVikingAccessError("relations API unavailable", category="backend_unavailable")


def _material(*, worker_id: str, call_id: str, stage: Stage) -> ApprovedMaterial:
    return ApprovedMaterial(
        material_id=f"mat-{worker_id}",
        run_id="run-1",
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/report.md",
        l1_sha256="a" * 64,
        l1_size_bytes=123,
        l2_index_uri=f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="ev-1",
                    uri=f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/provider_attempts.json",
                    kind="provider_attempts",
                    source="openbb",
                    sha256="b" * 64,
                    size_bytes=32,
                ),
            ),
            empty_reason=None,
            index_uri=f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
            index_sha256="c" * 64,
            index_size_bytes=222,
        ),
        l1_claims=(),
        approved_at="2026-05-17T00:00:00Z",
        hard_gate_result_path=Path("runs/run-1/calls/call-1/hard-gate.json"),
    )


def _pack(worker_id: str) -> DomainPackResult:
    request = PackRequest(
        run_id="run-1",
        call_id=f"call-{worker_id}",
        worker_id=worker_id,
        market=Market.HK,
        domain=PackDomain.MARKET,
        ticker="00700.HK",
        company_name="腾讯控股",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="HKD",
        profile="HK",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    attempt = ProviderAttempt(
        attempt_id=f"attempt-{worker_id}",
        run_id="run-1",
        call_id=f"call-{worker_id}",
        worker_id=worker_id,
        pack="market",
        provider="openbb.hk",
        adapter_id="openbb.hk.market",
        adapter_kind="openbb_native",
        provider_kind=ProviderKind.OPENBB_NATIVE,
        provider_config_version="cfg-v1",
        endpoint="stock_hk_daily",
        source_role=SourceRole.MARKET_DATA,
        started_at="2026-05-17T00:00:00Z",
        finished_at="2026-05-17T00:00:01Z",
        status=ProviderStatus.REMOTE_SUCCESS,
        required=True,
        attempt_required=True,
        coverage_group="market",
        coverage_quorum=1,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=False,
        cache_status=None,
        single_flight_role="owner",
        shared_from_attempt_id=None,
        latency_ms=200,
        row_count=20,
        raw_ref="mongo://openbb_provider_raw/raw-1",
        normalized_ref="mongo://openbb_normalized/norm-1",
        error_code=None,
        error_message=None,
        schema_id="market.v1",
        license_note="ok",
    )
    cache_receipt = CacheReceipt(
        cache_key="cache-1",
        provider="openbb.hk",
        endpoint="stock_hk_daily",
        status=ProviderStatus.CACHE_MISS,
        hit=False,
        stale=False,
        cached_empty=False,
        created_at="2026-05-17T00:00:00Z",
        expires_at="2026-05-17T00:05:00Z",
        ttl_seconds=300,
        evidence_hash="sha256:" + "d" * 64,
        raw_ref=None,
        normalized_ref=None,
    )
    gap = DataGap(
        gap_id="gap-1",
        domain=PackDomain.MARKET,
        severity=GapSeverity.WARN,
        reason=DataGapReason.FIELD_MISSING,
        field_path="order_flow.cvd",
        provider_candidates=("openbb.hk",),
        attempt_ids=(attempt.attempt_id,),
        root_cause="provider field missing",
        next_action="补充数据源",
    )
    readiness = Readiness(
        status=ReadinessStatus.PARTIAL,
        coverage={"market": "partial"},
        required_domains=("market",),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=("gap-1",),
        root_cause=None,
    )
    chart = ChartAsset(
        chart_id="chart-1",
        title="K线图",
        kind="ohlcv",
        image_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-market/evidence/charts/ohlcv.png",
        data_ref="mongo://openbb_normalized/chart-data-1",
        status=ReadinessStatus.READY,
        root_cause=None,
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="claw.v1",
        run_provider_plan_id="run-plan-1",
        call_specs=(),
        attempts=(attempt,),
        cache_receipts=(cache_receipt,),
        data_gaps=(gap,),
        conflicts=(Conflict("cf-1", "price", ("1", "2"), ("a", "b"), "manual", "low"),),
        readiness=readiness,
        chart_assets=(chart,),
        raw_refs=("mongo://openbb_provider_raw/raw-1",),
        normalized_refs=("mongo://openbb_normalized/norm-1",),
        normalized_bundle_ref="mongo://openbb_normalized/bundle-1",
        payload_hash="sha256:test",
        generated_at="2026-05-17T00:00:00Z",
    )
    return DomainPackResult(
        request=request,
        reader_brief_md="市场资料已汇总。",
        compact_facts={},
        attempts=(attempt,),
        cache_receipts=(cache_receipt,),
        data_gaps=(gap,),
        conflicts=(),
        readiness=readiness,
        chart_assets=(chart,),
        raw_refs=("mongo://openbb_provider_raw/raw-1",),
        normalized_refs=("mongo://openbb_normalized/norm-1",),
        normalized_bundle_ref="mongo://openbb_normalized/bundle-1",
        audit_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-market/evidence/pack_audit.json",
        audit_payload_hash="sha256:audit",
        audit_payload=audit,
    )


def test_link_provider_evidence_covers_l1_l2_attempt_raw_normalized_cache_gap_and_chart() -> None:
    backend = _Backend(linked_relations=[])
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=backend))
    material = _material(worker_id="market_analyst", call_id="call-market", stage=Stage.FRONTLINE)
    pack = _pack(worker_id="market_analyst")

    relations = plane.link_provider_evidence(material=material, pack_result=pack)
    kinds = {relation.kind for relation in relations}
    assert "worker_l1_to_l2_evidence" in kinds
    assert "l2_evidence_to_pack_audit" in kinds
    assert "pack_audit_to_provider_attempt" in kinds
    assert "provider_attempt_to_raw_payload" in kinds
    assert "provider_attempt_to_normalized_result" in kinds
    assert "provider_attempt_to_cache_receipt" in kinds
    assert "worker_l1_to_chart_asset" in kinds
    assert "worker_l1_to_data_gap" in kinds
    assert backend.linked_relations
    assert {item["from_uri"] for item in backend.linked_relations} == {
        "viking://resources/workflow/run-1/frontline/market_analyst/call-market/"
    }
    relation_notes = [json.loads(str(item["note"])) for item in backend.linked_relations]
    assert any(note["kind"] == "provider_attempt_to_raw_payload" for note in relation_notes)
    assert any(note["semantic_from_uri"].startswith("mongo://openbb_provider_attempts/") for note in relation_notes)


def test_link_provider_evidence_blocks_when_relation_write_fails() -> None:
    backend = _BackendRelationBlocked(linked_relations=[])
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=backend))
    material = _material(worker_id="market_analyst", call_id="call-market", stage=Stage.FRONTLINE)
    pack = _pack(worker_id="market_analyst")

    with pytest.raises(RuntimeError, match="openviking relation write failed"):
        plane.link_provider_evidence(material=material, pack_result=pack)


def test_link_final_report_chain_covers_final_report_pm_worker_and_evidence_chain() -> None:
    backend = _Backend(linked_relations=[])
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=backend))
    pm = _material(worker_id="portfolio_manager", call_id="call-pm", stage=Stage.PORTFOLIO_DECISION)
    market = _material(worker_id="market_analyst", call_id="call-market", stage=Stage.FRONTLINE)
    fundamental = _material(worker_id="fundamental_analyst", call_id="call-fund", stage=Stage.FRONTLINE)
    claim = FinalReportClaim(
        claim_id="claim-1",
        text="趋势偏强",
        section="结论",
        pm_material_id=pm.material_id,
        worker_material_ids=(market.material_id, fundamental.material_id),
    )
    relations = plane.link_final_report_chain(
        run_id="run-1",
        final_report_uri="viking://resources/workflow/run-1/final_report/report_polisher/call-final/report.md",
        final_claims=(claim,),
        pm_material=pm,
        upstream_materials=(market, fundamental),
        pack_results=(_pack("market_analyst"),),
    )

    assert any(item.kind == "final_report_claim_to_pm_l1" for item in relations)
    assert any(item.kind == "pm_l1_to_worker_l1" for item in relations)
    assert any(item.kind == "pack_audit_to_provider_attempt" for item in relations)
    assert any(item.from_uri.endswith("/report.md") and item.to_uri.endswith("/relations/manifest.json") for item in relations)
    assert len(backend.linked_relations) == len(relations)
    linked_sources = {item["from_uri"] for item in backend.linked_relations}
    assert "viking://resources/workflow/run-1/final_report/report_polisher/call-final/" in linked_sources
    assert "viking://resources/workflow/run-1/portfolio_decision/portfolio_manager/call-pm/" in linked_sources
    assert "viking://resources/workflow/run-1/frontline/market_analyst/call-market/" in linked_sources


def test_link_final_report_chain_keeps_final_report_to_pm_anchor_without_claims() -> None:
    backend = _Backend(linked_relations=[])
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=backend))
    pm = _material(worker_id="portfolio_manager", call_id="call-pm", stage=Stage.PORTFOLIO_DECISION)
    market = _material(worker_id="market_analyst", call_id="call-market", stage=Stage.FRONTLINE)

    relations = plane.link_final_report_chain(
        run_id="run-1",
        final_report_uri="viking://resources/workflow/run-1/final_report/report_polisher/call-final/report.md",
        final_claims=(),
        pm_material=pm,
        upstream_materials=(market,),
        pack_results=(_pack("market_analyst"),),
    )

    assert any(
        item.from_uri.endswith("/call-final/report.md")
        and item.to_uri == pm.l1_uri
        and item.kind == "final_report_claim_to_pm_l1"
        for item in relations
    )
    assert any(item.kind == "pack_audit_to_provider_attempt" for item in relations)
