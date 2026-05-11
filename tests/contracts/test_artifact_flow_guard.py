from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index, make_material_target
from claw_trade.guards.artifact_flow import validate_artifact_flow
from claw_trade.runtime.request_builder import WorkerCall, default_read_policy
from claw_trade.workflow.models import Stage


def test_stage_missing_required_upstream_worker_fails() -> None:
    manifest = frontline_manifest()
    refs = manifest.for_downstream_stage(Stage.INVESTMENT_DEBATE)
    caps = manifest.capabilities_for_downstream_stage(Stage.INVESTMENT_DEBATE)
    removed_ref = refs[0]
    call = fake_call(
        stage=Stage.INVESTMENT_DEBATE,
        worker_id="bull_researcher",
        call_id="call-5",
        upstream_materials=refs[1:],
        upstream_capabilities=tuple(cap for cap in caps if cap.capability_id != removed_ref.capability_id),
    )
    guard = validate_artifact_flow(call, manifest)
    assert not guard.ok


def test_stage_contains_non_legal_upstream_worker_even_if_approved_fails() -> None:
    manifest = frontline_and_manager_manifest()
    refs = manifest.for_downstream_stage(Stage.INVESTMENT_DEBATE)
    caps = manifest.capabilities_for_downstream_stage(Stage.INVESTMENT_DEBATE)
    manager_entry = manifest.lookup("mat-investment-manager")
    assert manager_entry is not None
    extra_ref = manager_entry.to_read_ref()
    extra_cap = manager_entry.to_read_capability()
    call = fake_call(
        stage=Stage.INVESTMENT_DEBATE,
        worker_id="bull_researcher",
        call_id="call-5",
        upstream_materials=refs + (extra_ref,),
        upstream_capabilities=caps + (extra_cap,),
    )
    guard = validate_artifact_flow(call, manifest)
    assert not guard.ok


def test_frontline_call_with_any_upstream_material_or_capability_fails() -> None:
    manifest = trade_manifest()
    refs = manifest.for_downstream_stage(Stage.TRADE_DECISION)
    caps = manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION)
    call = fake_call(
        stage=Stage.FRONTLINE,
        worker_id="market_analyst",
        call_id="call-1",
        upstream_materials=refs,
        upstream_capabilities=caps,
    )
    guard = validate_artifact_flow(call, manifest)
    assert not guard.ok


def test_ref_l2_index_uri_tampered_fails() -> None:
    manifest = trade_manifest()
    call = fake_call(
        stage=Stage.TRADE_DECISION,
        worker_id="trader",
        call_id="call-8",
        upstream_materials=manifest.for_downstream_stage(Stage.TRADE_DECISION),
        upstream_capabilities=manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION),
    )
    tampered = replace(
        call,
        upstream_materials=(
            replace(
                call.upstream_materials[0],
                l2_index_uri="viking://resources/workflow/run-1/investment_decision/research_manager/call-7/evidence/other.json",
            ),
        ),
    )
    guard = validate_artifact_flow(tampered, manifest)
    assert not guard.ok


def test_ref_l2_allowed_prefix_tampered_fails() -> None:
    manifest = trade_manifest()
    call = fake_call(
        stage=Stage.TRADE_DECISION,
        worker_id="trader",
        call_id="call-8",
        upstream_materials=manifest.for_downstream_stage(Stage.TRADE_DECISION),
        upstream_capabilities=manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION),
    )
    tampered = replace(
        call,
        upstream_materials=(
            replace(
                call.upstream_materials[0],
                l2_allowed_prefix="viking://resources/workflow/run-1/investment_decision/research_manager/call-7/evidence-other/",
            ),
        ),
    )
    guard = validate_artifact_flow(tampered, manifest)
    assert not guard.ok


def test_validate_artifact_flow_passes_for_approved_refs() -> None:
    manifest = trade_manifest()
    call = fake_call(
        stage=Stage.TRADE_DECISION,
        worker_id="trader",
        call_id="call-8",
        upstream_materials=manifest.for_downstream_stage(Stage.TRADE_DECISION),
        upstream_capabilities=manifest.capabilities_for_downstream_stage(Stage.TRADE_DECISION),
    )
    guard = validate_artifact_flow(call, manifest)
    assert guard.ok


def fake_call(
    *,
    stage: Stage,
    worker_id: str,
    call_id: str,
    upstream_materials,
    upstream_capabilities,
) -> WorkerCall:
    target = make_material_target("run-1", stage, worker_id, call_id)
    return WorkerCall(
        call_id=target.call_id,
        run_id=target.run_id,
        worker_id=target.worker_id,
        stage=target.stage,
        profile="US",
        ticker="AAPL",
        company_name="Apple",
        market="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-03",
        start_date="2026-01-01",
        end_date="2026-05-03",
        allowed_tools=("market_data", "openviking_read_with_capability"),
        upstream_materials=upstream_materials,
        openviking_read_capabilities=upstream_capabilities,
        material_target=target,
        read_policy=default_read_policy(),
        evidence_dir=Path("runs/run-1/calls/call-8"),
        stop_after_first_response=False,
    )


def trade_manifest() -> ApprovedManifest:
    return ApprovedManifest.empty().add(
        fake_approved_material(
            material_id="mat-investment-manager",
            worker_id="research_manager",
            stage=Stage.INVESTMENT_DECISION,
            call_id="call-7",
        )
    )


def frontline_manifest() -> ApprovedManifest:
    return (
        ApprovedManifest.empty()
        .add(fake_approved_material("mat-frontline-market", "market_analyst", Stage.FRONTLINE, "call-1"))
        .add(fake_approved_material("mat-frontline-fundamental", "fundamental_analyst", Stage.FRONTLINE, "call-2"))
        .add(fake_approved_material("mat-frontline-news", "news_analyst", Stage.FRONTLINE, "call-3"))
        .add(fake_approved_material("mat-frontline-social", "social_analyst", Stage.FRONTLINE, "call-4"))
    )


def frontline_and_manager_manifest() -> ApprovedManifest:
    return frontline_manifest().add(
        fake_approved_material(
            material_id="mat-investment-manager",
            worker_id="research_manager",
            stage=Stage.INVESTMENT_DECISION,
            call_id="call-7",
        )
    )


def fake_approved_material(
    material_id: str,
    worker_id: str,
    stage: Stage,
    call_id: str,
) -> ApprovedMaterial:
    l1_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/report.md"
    l2_index_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/index.json"
    evidence_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/e1.json"
    return ApprovedMaterial(
        material_id=material_id,
        run_id="run-1",
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=l1_uri,
        l1_sha256=f"sha-{material_id}",
        l1_size_bytes=256,
        l2_index_uri=l2_index_uri,
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="e1",
                    uri=evidence_uri,
                    kind="source",
                    source="api",
                    sha256=f"sha-{material_id}-e1",
                    size_bytes=64,
                ),
            ),
            empty_reason=None,
            index_uri=l2_index_uri,
            index_sha256=f"sha-{material_id}-idx",
            index_size_bytes=42,
        ),
        l1_claims=(
            L1Claim(
                claim_id=f"claim-{material_id}",
                kind="source_claim",
                text="引用来源",
                value=None,
                required_evidence_kinds=("source",),
                evidence_ids=("e1",),
            ),
        ),
        approved_at="2026-05-03T16:10:00Z",
        hard_gate_result_path=Path("runs/run-1/evidence/guards/result.json"),
    )
