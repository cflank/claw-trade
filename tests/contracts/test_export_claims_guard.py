from __future__ import annotations

import json
from pathlib import Path

from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index
from claw_trade.guards.export_claims import (
    ExportClaim,
    ExportClaimMapping,
    parse_export_claim_mapping,
    validate_export_claims_are_supported,
    validate_export_mapping_identity,
)
from claw_trade.workflow.models import RunRequest, RunStatus, Stage, WorkflowState


def test_parse_export_mapping_passes(tmp_path: Path) -> None:
    path = tmp_path / "export-claims.json"
    path.write_text(json.dumps(sample_mapping_json(), ensure_ascii=False), encoding="utf-8")

    parsed = parse_export_claim_mapping(path)
    assert parsed.ok
    assert parsed.mapping is not None
    assert parsed.mapping.schema_version == "control.export_claims.v1"
    assert parsed.mapping.claims[0].kind == "rating"


def test_parse_export_mapping_missing_file_fails(tmp_path: Path) -> None:
    parsed = parse_export_claim_mapping(tmp_path / "not-exists.json")
    assert not parsed.ok


def test_parse_export_mapping_invalid_json_fails(tmp_path: Path) -> None:
    path = tmp_path / "export-claims.json"
    path.write_text("{bad json", encoding="utf-8")
    parsed = parse_export_claim_mapping(path)
    assert not parsed.ok


def test_parse_export_mapping_schema_mismatch_fails(tmp_path: Path) -> None:
    bad = sample_mapping_json(schema_version="control.export_claims.v0")
    path = tmp_path / "export-claims.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    parsed = parse_export_claim_mapping(path)
    assert not parsed.ok


def test_parse_export_mapping_type_mismatch_fails(tmp_path: Path) -> None:
    bad = sample_mapping_json()
    bad["claims"] = "not-a-list"
    path = tmp_path / "export-claims.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    parsed = parse_export_claim_mapping(path)
    assert not parsed.ok


def test_validate_export_mapping_identity_run_id_mismatch_fails(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    mapping = sample_mapping(run_id="run-2")
    guard = validate_export_mapping_identity(mapping, state)
    assert not guard.ok


def test_validate_export_mapping_identity_path_traversal_fails(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    mapping = sample_mapping(final_report_path="../outside.md")
    guard = validate_export_mapping_identity(mapping, state)
    assert not guard.ok


def test_validate_export_mapping_identity_outside_reports_fails(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    mapping = sample_mapping(final_report_path="runs/run-1/final-report.md")
    guard = validate_export_mapping_identity(mapping, state)
    assert not guard.ok


def test_validate_export_mapping_identity_passes_for_reports_path(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    mapping = sample_mapping(final_report_path="reports/final-report.md")
    guard = validate_export_mapping_identity(mapping, state)
    assert guard.ok


def test_validate_export_mapping_identity_reports_dir_fails(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    mapping = sample_mapping(final_report_path="reports")
    guard = validate_export_mapping_identity(mapping, state)
    assert not guard.ok


def test_validate_export_mapping_identity_reports_prefix_dir_fails(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    mapping = sample_mapping(final_report_path="reports_evil/final-report.md")
    guard = validate_export_mapping_identity(mapping, state)
    assert not guard.ok


def test_validate_export_mapping_identity_absolute_reports_path_passes(tmp_path: Path) -> None:
    state = sample_state(tmp_path, run_id="run-1")
    final_report_path = str((state.run_dir / "reports" / "final-report.md").resolve())
    mapping = sample_mapping(final_report_path=final_report_path)
    guard = validate_export_mapping_identity(mapping, state)
    assert guard.ok


def test_validate_export_claims_high_risk_without_sources_fails() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="rating",
        source_material_ids=(),
        source_claim_ids=(),
        source_l1_sha256=(),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert not guard.ok


def test_validate_export_claims_source_material_not_found_fails() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="rating",
        source_material_ids=("mat-not-found",),
        source_claim_ids=("claim-1",),
        source_l1_sha256=("sha-l1-1",),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert not guard.ok


def test_validate_export_claims_source_claim_not_found_fails() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="news",
        source_material_ids=("mat-1",),
        source_claim_ids=("claim-x",),
        source_l1_sha256=("sha-l1-1",),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert not guard.ok


def test_validate_export_claims_source_l1_sha_mismatch_fails() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="chart",
        source_material_ids=("mat-1",),
        source_claim_ids=("claim-1",),
        source_l1_sha256=("sha-other",),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert not guard.ok


def test_validate_export_claims_cross_material_claim_sha_mismatch_fails() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="source",
        source_material_ids=("mat-1",),
        source_claim_ids=("claim-1",),
        source_l1_sha256=("sha-l1-1", "sha-l1-2"),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(
        mapping,
        materials=(sample_material(), sample_material_2()),
    )
    assert not guard.ok


def test_validate_export_claims_supported_claim_passes() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="rating",
        source_material_ids=("mat-1",),
        source_claim_ids=("claim-1",),
        source_l1_sha256=("sha-l1-1",),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert guard.ok


def test_validate_export_claims_other_without_source_passes() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="文风润色说明",
        kind="other",
        source_material_ids=(),
        source_claim_ids=(),
        source_l1_sha256=(),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert guard.ok


def test_validate_export_claims_other_with_invalid_source_fails() -> None:
    claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="文风润色说明",
        kind="other",
        source_material_ids=("mat-1",),
        source_claim_ids=("claim-1",),
        source_l1_sha256=("sha-not-match",),
    )
    mapping = sample_mapping(claims=(claim,))
    guard = validate_export_claims_are_supported(mapping, materials=(sample_material(),))
    assert not guard.ok


def sample_state(tmp_path: Path, *, run_id: str) -> WorkflowState:
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-04-04",
        end_date="2026-05-04",
    )
    return WorkflowState(
        run_id=run_id,
        request=request,
        status=RunStatus.REPORT_EXPORTING,
        run_dir=run_dir,
        openviking_namespace=f"workflow/{run_id}",
        created_at="2026-05-04T12:00:00Z",
        updated_at="2026-05-04T12:00:00Z",
        active_stage=Stage.PORTFOLIO_DECISION,
    )


def sample_mapping(
    *,
    run_id: str = "run-1",
    final_report_path: str = "reports/final-report.md",
    claims: tuple[ExportClaim, ...] | None = None,
) -> ExportClaimMapping:
    default_claim = ExportClaim(
        export_claim_id="export-claim-1",
        text="建议买入",
        kind="rating",
        source_material_ids=("mat-1",),
        source_claim_ids=("claim-1",),
        source_l1_sha256=("sha-l1-1",),
    )
    return ExportClaimMapping(
        schema_version="control.export_claims.v1",
        run_id=run_id,
        final_report_path=final_report_path,
        claims=claims or (default_claim,),
    )


def sample_mapping_json(*, schema_version: str = "control.export_claims.v1") -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "run_id": "run-1",
        "final_report_path": "runs/run-1/reports/final-report.md",
        "claims": [
            {
                "export_claim_id": "export-claim-1",
                "text": "建议买入",
                "kind": "rating",
                "source_material_ids": ["mat-1"],
                "source_claim_ids": ["claim-1"],
                "source_l1_sha256": ["sha-l1-1"],
            }
        ],
    }


def sample_material() -> ApprovedMaterial:
    return ApprovedMaterial(
        material_id="mat-1",
        run_id="run-1",
        call_id="call-1",
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        target_name="report",
        l1_uri="viking://resources/workflow/run-1/investment_decision/research_manager/call-1/report.md",
        l1_sha256="sha-l1-1",
        l1_size_bytes=120,
        l2_index_uri="viking://resources/workflow/run-1/investment_decision/research_manager/call-1/evidence/index.json",
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="l2-1",
                    uri="viking://resources/workflow/run-1/investment_decision/research_manager/call-1/evidence/l2-1.json",
                    kind="source",
                    source="provider",
                    sha256="sha-l2-1",
                    size_bytes=64,
                ),
            ),
            empty_reason=None,
            index_uri="viking://resources/workflow/run-1/investment_decision/research_manager/call-1/evidence/index.json",
            index_sha256="sha-l2-index-1",
            index_size_bytes=88,
        ),
        l1_claims=(
            L1Claim(
                claim_id="claim-1",
                kind="rating",
                text="建议买入",
                value="buy",
                required_evidence_kinds=("source",),
                evidence_ids=("l2-1",),
            ),
        ),
        approved_at="2026-05-04T12:00:00Z",
        hard_gate_result_path=Path("runs/run-1/guards/guard-1.json"),
    )


def sample_material_2() -> ApprovedMaterial:
    return ApprovedMaterial(
        material_id="mat-2",
        run_id="run-1",
        call_id="call-2",
        worker_id="news_analyst",
        stage=Stage.FRONTLINE,
        target_name="report",
        l1_uri="viking://resources/workflow/run-1/frontline/news_analyst/call-2/report.md",
        l1_sha256="sha-l1-2",
        l1_size_bytes=96,
        l2_index_uri="viking://resources/workflow/run-1/frontline/news_analyst/call-2/evidence/index.json",
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="l2-2",
                    uri="viking://resources/workflow/run-1/frontline/news_analyst/call-2/evidence/l2-2.json",
                    kind="source",
                    source="provider",
                    sha256="sha-l2-2",
                    size_bytes=64,
                ),
            ),
            empty_reason=None,
            index_uri="viking://resources/workflow/run-1/frontline/news_analyst/call-2/evidence/index.json",
            index_sha256="sha-l2-index-2",
            index_size_bytes=80,
        ),
        l1_claims=(
            L1Claim(
                claim_id="claim-2",
                kind="news",
                text="公司公告利好",
                value="positive",
                required_evidence_kinds=("source",),
                evidence_ids=("l2-2",),
            ),
        ),
        approved_at="2026-05-04T12:00:00Z",
        hard_gate_result_path=Path("runs/run-1/guards/guard-2.json"),
    )
