from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claw_trade.artifacts.manifest import make_material_id
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index, MaterialReceipt, make_material_target
from claw_trade.guards.pm_owner import (
    PMDecision,
    compare_pm_decision_fields,
    load_pm_decision_for_material,
    parse_pm_decision_evidence,
    validate_export_does_not_rewrite_pm,
    validate_pm_owner,
)
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall


def test_parse_pm_decision_evidence_missing_file_fails(tmp_path: Path) -> None:
    parsed = parse_pm_decision_evidence(tmp_path / "pm-decision.json")
    assert not parsed.ok


def test_validate_pm_owner_missing_pm_decision_evidence_fails(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    receipt = sample_receipt(call, l1_text="# PM L1")
    decision, guard = validate_pm_owner(
        call=call,
        evidence=sample_evidence(call),
        receipt=receipt,
        claims=sample_claims(),
    )
    assert decision is None
    assert not guard.ok
    assert guard.reason is not None and "pm-decision.json" in guard.reason


def test_validate_pm_owner_rejects_identity_mismatch(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    receipt = sample_receipt(call, l1_text="# PM L1")
    write_pm_decision(call=call, receipt=receipt, overrides={"call_id": "call-x"})
    decision, guard = validate_pm_owner(
        call=call,
        evidence=sample_evidence(call),
        receipt=receipt,
        claims=sample_claims(),
    )
    assert decision is None
    assert not guard.ok


def test_validate_pm_owner_rejects_unknown_source_claim_id(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    receipt = sample_receipt(call, l1_text="# PM L1")
    write_pm_decision(call=call, receipt=receipt, overrides={"source_claim_ids": ["claim-x"]})
    decision, guard = validate_pm_owner(
        call=call,
        evidence=sample_evidence(call),
        receipt=receipt,
        claims=sample_claims(),
    )
    assert decision is None
    assert not guard.ok


def test_validate_pm_owner_passes_with_pm_decision_evidence(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    receipt = sample_receipt(call, l1_text="# PM L1")
    write_pm_decision(call=call, receipt=receipt)
    decision, guard = validate_pm_owner(
        call=call,
        evidence=sample_evidence(call),
        receipt=receipt,
        claims=sample_claims(),
    )
    assert decision is not None
    assert guard.ok
    assert decision.rating == "buy"


def test_load_pm_decision_for_material_reads_pm_decision_json(tmp_path: Path) -> None:
    material = sample_material(tmp_path)
    payload = sample_pm_payload(
        run_id=material.run_id,
        call_id=material.call_id,
        worker_id=material.worker_id,
        stage=material.stage.value,
        material_id=material.material_id,
        source_l1_sha256=material.l1_sha256,
        l1_uri=material.l1_uri,
        source_claim_ids=[material.l1_claims[0].claim_id],
    )
    (material.hard_gate_result_path.parent / "pm-decision.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    decision, guard = load_pm_decision_for_material(material)
    assert decision is not None
    assert guard.ok


def test_compare_pm_decision_fields_detects_rewrite() -> None:
    guard = compare_pm_decision_fields(sample_decision(), sample_decision(rating="sell"))
    assert not guard.ok


def test_validate_export_does_not_rewrite_pm_fails_on_rewrite() -> None:
    baseline = sample_decision()
    mapping = {
        "pm_decision": {
            "source_material_id": baseline.material_id,
            "rating": "hold",
            "final_conclusion": baseline.final_conclusion,
            "execution_conditions": list(baseline.execution_conditions),
            "risk_conditions": list(baseline.risk_conditions),
        }
    }
    guard = validate_export_does_not_rewrite_pm(baseline, mapping)
    assert not guard.ok


def sample_call(tmp_path: Path) -> WorkerCall:
    target = make_material_target("run-1", Stage.PORTFOLIO_DECISION, "portfolio_manager", "call-12")
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-12" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return WorkerCall(
        call_id="call-12",
        run_id="run-1",
        worker_id="portfolio_manager",
        stage=Stage.PORTFOLIO_DECISION,
        profile="us",
        ticker="AAPL",
        company_name="Apple Inc.",
        market="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-04-04",
        end_date="2026-05-04",
        allowed_tools=("write_material",),
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )


def sample_receipt(call: WorkerCall, *, l1_text: str) -> MaterialReceipt:
    content = l1_text.encode("utf-8")
    return MaterialReceipt(
        uri=call.material_target.l1_uri,
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        target_name=call.material_target.target_name,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        written_at="2026-05-04T12:00:00Z",
        receipt_id="receipt-1",
    )


def sample_evidence(call: WorkerCall) -> ProviderEvidence:
    p = call.evidence_dir
    for name in ("provider-request.json", "workspace.json", "visible.json", "first.json", "tools.json", "raw.txt"):
        (p / name).write_text("{}", encoding="utf-8")
    (p / "openviking-receipt.json").write_text("{}", encoding="utf-8")
    return ProviderEvidence(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=p / "workspace.json",
        provider_request_path=p / "provider-request.json",
        visible_tools_path=p / "visible.json",
        first_response_path=p / "first.json",
        tool_calls_status="completed",
        tool_calls_path=p / "tools.json",
        raw_output_path=p / "raw.txt",
        openviking_receipt_path=p / "openviking-receipt.json",
    )


def sample_claims() -> tuple[L1Claim, ...]:
    return (
        L1Claim(
            claim_id="claim-1",
            kind="rating",
            text="建议买入",
            value="buy",
            required_evidence_kinds=("l2_evidence",),
            evidence_ids=("l2-1",),
        ),
        L1Claim(
            claim_id="claim-2",
            kind="risk_condition",
            text="若业绩不及预期则止损",
            value=None,
            required_evidence_kinds=("l2_evidence",),
            evidence_ids=("l2-2",),
        ),
    )


def write_pm_decision(
    *,
    call: WorkerCall,
    receipt: MaterialReceipt,
    overrides: dict[str, object] | None = None,
) -> None:
    payload = sample_pm_payload(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage.value,
        material_id=make_material_id(call, receipt),
        source_l1_sha256=receipt.sha256,
        l1_uri=receipt.uri,
        source_claim_ids=["claim-1", "claim-2"],
    )
    if overrides:
        payload.update(overrides)
    (call.evidence_dir / "pm-decision.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sample_pm_payload(
    *,
    run_id: str,
    call_id: str,
    worker_id: str,
    stage: str,
    material_id: str,
    source_l1_sha256: str,
    l1_uri: str,
    source_claim_ids: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "control.pm_decision.v1",
        "run_id": run_id,
        "call_id": call_id,
        "worker_id": worker_id,
        "stage": stage,
        "material_id": material_id,
        "rating": "buy",
        "final_conclusion": "维持买入。",
        "execution_conditions": ["回调分批加仓"],
        "risk_conditions": ["若业绩不及预期则止损"],
        "source_claim_ids": source_claim_ids,
        "source_l1_sha256": source_l1_sha256,
        "l1_uri": l1_uri,
    }


def sample_material(tmp_path: Path) -> ApprovedMaterial:
    gate = tmp_path / "runs" / "run-1" / "calls" / "call-12" / "evidence" / "approval-hard-gate.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({"ok": True}), encoding="utf-8")
    return ApprovedMaterial(
        material_id="mat-pm-001",
        run_id="run-1",
        call_id="call-12",
        worker_id="portfolio_manager",
        stage=Stage.PORTFOLIO_DECISION,
        target_name="report",
        l1_uri="viking://resources/workflow/run-1/portfolio_decision/portfolio_manager/call-12/report.md",
        l1_sha256="sha-l1-001",
        l1_size_bytes=128,
        l2_index_uri="viking://resources/workflow/run-1/portfolio_decision/portfolio_manager/call-12/evidence/index.json",
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="l2-1",
                    uri="viking://resources/workflow/run-1/portfolio_decision/portfolio_manager/call-12/evidence/l2-1.json",
                    kind="source",
                    source="provider",
                    sha256="sha-l2-1",
                    size_bytes=64,
                ),
            ),
            empty_reason=None,
            index_uri="viking://resources/workflow/run-1/portfolio_decision/portfolio_manager/call-12/evidence/index.json",
            index_sha256="sha-index",
            index_size_bytes=80,
        ),
        l1_claims=sample_claims(),
        approved_at="2026-05-04T12:00:00Z",
        hard_gate_result_path=gate,
    )


def sample_decision(**overrides: object) -> PMDecision:
    base = {
        "schema_version": "control.pm_decision.v1",
        "run_id": "run-1",
        "call_id": "call-12",
        "worker_id": "portfolio_manager",
        "stage": Stage.PORTFOLIO_DECISION,
        "material_id": "mat-pm-001",
        "rating": "buy",
        "final_conclusion": "维持买入。",
        "execution_conditions": ("回调分批加仓",),
        "risk_conditions": ("若业绩不及预期则止损",),
        "source_claim_ids": ("claim-1",),
        "source_l1_sha256": "sha-l1-001",
        "l1_uri": "viking://resources/workflow/run-1/portfolio_decision/portfolio_manager/call-12/report.md",
    }
    base.update(overrides)
    return PMDecision(**base)
