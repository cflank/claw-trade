from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient, OpenVikingStat
from claw_trade.artifacts.refs import ApprovedMaterial, L2Entry, L2Index
from claw_trade.data_gateway.models import (
    CacheReceipt,
    ChartAsset,
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
    linked: list[dict[str, object]]

    def ensure_namespace(self, namespace: str) -> None:
        return None

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return b"ok"

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        return L2Index(entries=(), empty_reason=None, index_uri=uri, index_sha256=None, index_size_bytes=None)

    def fetch_stat_by_uri(self, uri: str) -> OpenVikingStat:
        return OpenVikingStat(uri=uri, ok=True, sha256="0" * 64, size_bytes=2, exists=True, is_dir=False)

    def fetch_receipt_by_path(self, receipt_path: Path):  # pragma: no cover - not used
        raise FileNotFoundError(receipt_path)

    def link_relation(self, relation: dict[str, object]) -> dict[str, object]:
        self.linked.append(relation)
        return {"status": "ok"}


def _material(worker_id: str, stage: Stage, call_id: str) -> ApprovedMaterial:
    return ApprovedMaterial(
        material_id=f"mat-{worker_id}",
        run_id="run-lineage",
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=f"viking://resources/workflow/run-lineage/{stage.value}/{worker_id}/{call_id}/report.md",
        l1_sha256="a" * 64,
        l1_size_bytes=100,
        l2_index_uri=f"viking://resources/workflow/run-lineage/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="e1",
                    uri=f"viking://resources/workflow/run-lineage/{stage.value}/{worker_id}/{call_id}/evidence/provider_attempts.json",
                    kind="provider_attempts",
                    source="openbb",
                    sha256="b" * 64,
                    size_bytes=10,
                ),
            ),
            empty_reason=None,
            index_uri=f"viking://resources/workflow/run-lineage/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
            index_sha256="c" * 64,
            index_size_bytes=120,
        ),
        l1_claims=(),
        approved_at="2026-05-17T00:00:00Z",
        hard_gate_result_path=Path("runs/run-lineage/calls/call/hard-gate.json"),
    )


def _pack(worker_id: str, domain: PackDomain) -> DomainPackResult:
    request = PackRequest(
        run_id="run-lineage",
        call_id=f"call-{worker_id}",
        worker_id=worker_id,
        market=Market.US,
        domain=domain,
        ticker="AAPL",
        company_name="Apple Inc.",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="US",
        freshness_policy=FreshnessPolicy(max_age_seconds=120),
    )
    attempt = ProviderAttempt(
        attempt_id=f"attempt-{worker_id}",
        run_id="run-lineage",
        call_id=request.call_id,
        worker_id=worker_id,
        pack=domain.value,
        provider="openbb.us",
        adapter_id=f"openbb.us.{domain.value}",
        adapter_kind="openbb_native",
        provider_kind=ProviderKind.OPENBB_NATIVE,
        provider_config_version="cfg-v1",
        endpoint=f"{domain.value}_endpoint",
        source_role=SourceRole.MARKET_DATA,
        started_at="2026-05-17T00:00:00Z",
        finished_at="2026-05-17T00:00:01Z",
        status=ProviderStatus.REMOTE_SUCCESS,
        required=True,
        attempt_required=True,
        coverage_group=domain.value,
        coverage_quorum=1,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=False,
        cache_status=None,
        single_flight_role="owner",
        shared_from_attempt_id=None,
        latency_ms=123,
        row_count=10,
        raw_ref=f"mongo://openbb_provider_raw/{worker_id}-raw",
        normalized_ref=f"mongo://openbb_normalized/{worker_id}-norm",
        error_code=None,
        error_message=None,
        schema_id=f"{domain.value}.v1",
        license_note="ok",
    )
    cache = CacheReceipt(
        cache_key=f"{worker_id}-cache",
        provider="openbb.us",
        endpoint=f"{domain.value}_endpoint",
        status=ProviderStatus.CACHE_MISS,
        hit=False,
        stale=False,
        cached_empty=False,
        created_at="2026-05-17T00:00:00Z",
        expires_at="2026-05-17T00:10:00Z",
        ttl_seconds=600,
        evidence_hash="sha256:" + "d" * 64,
        raw_ref=None,
        normalized_ref=None,
    )
    gap = DataGap(
        gap_id=f"{worker_id}-gap",
        domain=domain,
        severity=GapSeverity.WARN,
        reason=DataGapReason.FIELD_MISSING,
        field_path="sample.field",
        provider_candidates=("openbb.us",),
        attempt_ids=(attempt.attempt_id,),
        root_cause="field missing",
        next_action="补充",
    )
    readiness = Readiness(
        status=ReadinessStatus.PARTIAL,
        coverage={domain.value: "partial"},
        required_domains=(domain.value,),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=(gap.gap_id,),
        root_cause=None,
    )
    chart = ChartAsset(
        chart_id=f"{worker_id}-chart",
        title="图表",
        kind="line",
        image_ref=f"viking://resources/workflow/run-lineage/frontline/{worker_id}/call-{worker_id}/evidence/charts/line.png",
        data_ref=f"mongo://openbb_normalized/{worker_id}-chart",
        status=ReadinessStatus.READY,
        root_cause=None,
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="claw.v1",
        run_provider_plan_id="plan-1",
        call_specs=(),
        attempts=(attempt,),
        cache_receipts=(cache,),
        data_gaps=(gap,),
        conflicts=(),
        readiness=readiness,
        chart_assets=(chart,),
        raw_refs=(attempt.raw_ref or "",),
        normalized_refs=(attempt.normalized_ref or "",),
        normalized_bundle_ref="mongo://openbb_normalized/bundle",
        payload_hash="sha256:test",
        generated_at="2026-05-17T00:00:00Z",
    )
    return DomainPackResult(
        request=request,
        reader_brief_md="资料包摘要",
        compact_facts={},
        attempts=(attempt,),
        cache_receipts=(cache,),
        data_gaps=(gap,),
        conflicts=(),
        readiness=readiness,
        chart_assets=(chart,),
        raw_refs=(attempt.raw_ref or "",),
        normalized_refs=(attempt.normalized_ref or "",),
        normalized_bundle_ref="mongo://openbb_normalized/bundle",
        audit_ref=f"viking://resources/workflow/run-lineage/frontline/{worker_id}/call-{worker_id}/evidence/pack_audit.json",
        audit_payload_hash="sha256:audit",
        audit_payload=audit,
    )


def test_openviking_lineage_chain_closes_from_final_claim_to_provider_refs() -> None:
    backend = _Backend(linked=[])
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=backend))
    pm = _material("portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    market = _material("market_analyst", Stage.FRONTLINE, "call-market")
    claim = FinalReportClaim(
        claim_id="c-1",
        text="主趋势延续",
        section="结论",
        pm_material_id=pm.material_id,
        worker_material_ids=(market.material_id,),
    )
    relations = plane.link_final_report_chain(
        run_id="run-lineage",
        final_report_uri="viking://resources/workflow/run-lineage/final_report/report_polisher/call-final/report.md",
        final_claims=(claim,),
        pm_material=pm,
        upstream_materials=(market,),
        pack_results=(_pack("market_analyst", PackDomain.MARKET),),
    )
    kinds = {item.kind for item in relations}
    assert "final_report_claim_to_pm_l1" in kinds
    assert "pm_l1_to_worker_l1" in kinds
    assert "worker_l1_to_l2_evidence" in kinds
    assert "l2_evidence_to_pack_audit" in kinds
    assert "pack_audit_to_provider_attempt" in kinds
    assert "provider_attempt_to_raw_payload" in kinds
    assert "provider_attempt_to_normalized_result" in kinds
    assert "provider_attempt_to_cache_receipt" in kinds
    assert backend.linked
