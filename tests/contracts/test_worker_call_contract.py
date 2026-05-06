from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index
from claw_trade.runtime.request_builder import build_request_context, build_worker_call_from_context
from claw_trade.workflow.models import RunRequest, RunStatus, Stage, StopPoint, WorkflowState


def test_frontline_first_batch_requires_empty_upstream_and_capabilities(tmp_path: Path) -> None:
    state = _state(tmp_path=tmp_path, run_id="run-1", profile="US")
    result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        manifest=ApprovedManifest.empty(),
    )
    assert result.ok is True
    assert result.context is not None
    assert result.context.upstream_materials == ()
    assert result.context.openviking_read_capabilities == ()


def test_downstream_worker_call_reads_only_from_approved_manifest(tmp_path: Path) -> None:
    state = _state(tmp_path=tmp_path, run_id="run-1", profile="US")
    manifest = frontline_manifest(tmp_path)
    context_result = build_request_context(
        state=state,
        worker_id="bull_researcher",
        stage=Stage.INVESTMENT_DEBATE,
        manifest=manifest,
    )

    assert context_result.ok is True
    assert context_result.context is not None
    context = context_result.context
    assert context.upstream_materials
    assert context.openviking_read_capabilities
    for ref in context.upstream_materials:
        assert ref.worker_id
        assert ref.stage == Stage.FRONTLINE
        assert ref.material_id
        assert ref.capability_id
        assert ref.l1_uri.startswith("viking://resources/workflow/")
        assert ref.l1_sha256
        assert ref.call_id
    call_result = build_worker_call_from_context(context)
    assert call_result.ok is True
    assert call_result.call is not None
    payload = json.dumps(asdict(call_result.call), ensure_ascii=False, sort_keys=True, default=str)
    assert "投资建议正文" not in payload
    assert "market_summary_text" not in payload


def test_material_target_is_unique_per_call_even_same_worker_stage(tmp_path: Path) -> None:
    state = _state(
        tmp_path=tmp_path,
        run_id="run-1",
        profile="US",
        stop_point=StopPoint.FIRST_RESPONSE,
    )
    context_result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        manifest=ApprovedManifest.empty(),
    )
    assert context_result.ok is True
    assert context_result.context is not None

    first_call = build_worker_call_from_context(context_result.context)
    second_call = build_worker_call_from_context(context_result.context)
    assert first_call.ok is True and first_call.call is not None
    assert second_call.ok is True and second_call.call is not None

    call_a = first_call.call
    call_b = second_call.call
    assert call_a.call_id != call_b.call_id
    assert call_a.material_target.l1_uri != call_b.material_target.l1_uri
    assert call_a.material_target.l2_prefix != call_b.material_target.l2_prefix
    assert call_a.material_target.run_id == state.run_id
    assert call_a.material_target.stage == Stage.FRONTLINE
    assert call_a.material_target.worker_id == "market_analyst"
    assert call_a.evidence_dir == state.run_dir / "calls" / call_a.call_id
    assert call_a.evidence_dir.name == call_a.call_id
    assert "/evidence" not in str(call_a.evidence_dir).replace("\\", "/")
    assert call_a.stop_after_first_response is True


def _state(
    *,
    tmp_path: Path,
    run_id: str,
    profile: str,
    stop_point: StopPoint = StopPoint.NONE,
) -> WorkflowState:
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile=profile,
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-03",
        start_date="2026-01-01",
        end_date="2026-05-03",
        stop_point=stop_point,
    )
    return WorkflowState(
        run_id=run_id,
        request=request,
        status=RunStatus.CREATED,
        run_dir=tmp_path / run_id,
        openviking_namespace=f"workflow/{run_id}",
        created_at="2026-05-03T12:00:00Z",
        updated_at="2026-05-03T12:00:00Z",
    )


def frontline_manifest(tmp_path: Path) -> ApprovedManifest:
    return (
        ApprovedManifest.empty()
        .add(fake_approved_material(tmp_path, "mat-frontline-market", "market_analyst", Stage.FRONTLINE, "call-1"))
        .add(
            fake_approved_material(
                tmp_path,
                "mat-frontline-fundamental",
                "fundamental_analyst",
                Stage.FRONTLINE,
                "call-2",
            )
        )
        .add(fake_approved_material(tmp_path, "mat-frontline-news", "news_analyst", Stage.FRONTLINE, "call-3"))
        .add(fake_approved_material(tmp_path, "mat-frontline-social", "social_analyst", Stage.FRONTLINE, "call-4"))
    )


def fake_approved_material(
    tmp_path: Path,
    material_id: str,
    worker_id: str,
    stage: Stage,
    call_id: str,
) -> ApprovedMaterial:
    l1_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/report.md"
    l2_index_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/index.json"
    evidence_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/e1.json"
    hard_gate_path = tmp_path / "runs" / "run-1" / "evidence" / "guards" / "result.json"
    hard_gate_path.parent.mkdir(parents=True, exist_ok=True)
    hard_gate_path.write_text('{"ok": true, "category": "ok", "status": "passed"}', encoding="utf-8")

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
        hard_gate_result_path=hard_gate_path,
    )
