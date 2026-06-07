from pathlib import Path

import pytest
from claw_trade.artifacts.manifest import ApprovedManifest, ArtifactFlowError
from claw_trade.artifacts.refs import (
    ApprovedMaterial,
    L1Claim,
    L2Entry,
    L2Index,
    make_material_target,
    validate_viking_uri_shape,
)
from claw_trade.workflow.models import Stage


def test_material_target_contains_call_id() -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")

    assert str(target.l1_uri) == "viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md"
    assert str(target.l2_prefix).endswith("/call-1/evidence/")


def test_validate_viking_uri_shape_rejects_latest_and_list_like_path() -> None:
    guard = validate_viking_uri_shape(
        "viking://resources/workflow/run-1/frontline/market_analyst/call-1/latest/report.md",
        "run-1",
        Stage.FRONTLINE,
        "market_analyst",
        "call-1",
    )
    assert not guard.ok

    guard = validate_viking_uri_shape(
        "viking://resources/workflow/run-1/frontline/market_analyst/call-1/list/report.md",
        "run-1",
        Stage.FRONTLINE,
        "market_analyst",
        "call-1",
    )
    assert not guard.ok


def test_validate_viking_uri_shape_passes_for_l1_and_l2() -> None:
    l1 = "viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md"
    l2 = "viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/ev-1.json"
    assert validate_viking_uri_shape(l1, "run-1", Stage.FRONTLINE, "market_analyst", "call-1").ok
    assert validate_viking_uri_shape(l2, "run-1", Stage.FRONTLINE, "market_analyst", "call-1").ok


def test_manifest_generates_read_capability_not_raw_uri(tmp_path: Path) -> None:
    manifest = (
        ApprovedManifest.empty()
        .add(fake_approved_material(tmp_path, "market_analyst", "call-1"))
        .add(fake_approved_material(tmp_path, "fundamental_analyst", "call-2"))
        .add(fake_approved_material(tmp_path, "news_analyst", "call-3"))
        .add(fake_approved_material(tmp_path, "social_analyst", "call-4"))
    )

    ref = manifest.for_downstream_stage(Stage.INVESTMENT_DEBATE)[0]

    assert ref.capability_id
    assert ref.l1_sha256


def test_manifest_for_downstream_stage_requires_full_upstream_coverage(tmp_path: Path) -> None:
    manifest = ApprovedManifest.empty().add(fake_approved_material(tmp_path))

    with pytest.raises(ArtifactFlowError, match="下游材料缺失"):
        manifest.for_downstream_stage(Stage.INVESTMENT_DECISION)


def fake_approved_material(
    tmp_path: Path,
    worker_id: str = "market_analyst",
    call_id: str = "call-1",
) -> ApprovedMaterial:
    l1_uri = f"viking://resources/workflow/run-1/frontline/{worker_id}/{call_id}/report.md"
    l2_index_uri = f"viking://resources/workflow/run-1/frontline/{worker_id}/{call_id}/evidence/index.json"
    evidence_uri = f"viking://resources/workflow/run-1/frontline/{worker_id}/{call_id}/evidence/ev-1.json"
    return ApprovedMaterial(
        material_id=f"mat-frontline-{worker_id}",
        run_id="run-1",
        call_id=call_id,
        worker_id=worker_id,
        stage=Stage.FRONTLINE,
        target_name="report",
        l1_uri=l1_uri,
        l1_sha256=f"sha-{worker_id}",
        l1_size_bytes=128,
        l2_index_uri=l2_index_uri,
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="ev-1",
                    uri=evidence_uri,
                    kind="market_data",
                    source="provider",
                    sha256="sha-ev-1",
                    size_bytes=12,
                ),
            ),
            empty_reason=None,
            index_uri=l2_index_uri,
            index_sha256="sha-index-1",
            index_size_bytes=32,
        ),
        l1_claims=(
            L1Claim(
                claim_id="claim-1",
                kind="valuation_metric",
                text="PE 为 15 倍",
                value="15",
                required_evidence_kinds=("market_data",),
                evidence_ids=("ev-1",),
            ),
        ),
        approved_at="2026-05-03T15:45:00Z",
        hard_gate_result_path=write_gate_result(tmp_path, f"{worker_id}-{call_id}.json"),
    )


def write_gate_result(
    tmp_path: Path,
    name: str,
    payload: str = '{"ok": true, "category": "runtime_guards"}',
) -> Path:
    path = tmp_path / "guards" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path
