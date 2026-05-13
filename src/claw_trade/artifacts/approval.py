from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from claw_trade.artifacts.claims import MaterialClaimsEvidence, parse_material_claims_evidence
from claw_trade.artifacts.manifest import make_material_id
from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Index, MaterialReceipt
from claw_trade.guards.common import ApprovalResult, combine_guard_results, guard_passed
from claw_trade.guards.l1_l2 import validate_l1_l2_contract, validate_l2_entries
from claw_trade.guards.openviking_receipt import validate_openviking_receipt
from claw_trade.guards.pm_owner import pm_decision_required_for_profile, validate_pm_owner
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import WorkerCall


@dataclass(frozen=True)
class ApprovalCandidate:
    call: WorkerCall
    receipt: MaterialReceipt
    material_claims: MaterialClaimsEvidence
    l1_text: str
    l2_index: L2Index
    raw_output_path: Path


@dataclass(frozen=True)
class ApprovalCandidateResult:
    ok: bool
    candidate: ApprovalCandidate | None
    category: str | None
    reason: str | None
    paths: tuple[Path, ...]

    @classmethod
    def passed(cls, candidate: ApprovalCandidate) -> ApprovalCandidateResult:
        return cls(ok=True, candidate=candidate, category=None, reason=None, paths=())

    @classmethod
    def failed(cls, category: str, reason: str, paths: tuple[Path, ...]) -> ApprovalCandidateResult:
        return cls(ok=False, candidate=None, category=category, reason=reason, paths=paths)


def prepare_approval_candidate(
    call: WorkerCall,
    evidence: ProviderEvidence,
    openviking: OpenVikingClient,
) -> ApprovalCandidateResult:
    receipt_path = evidence.openviking_receipt_path
    if receipt_path is None:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="openviking_receipt_path 缺失",
            paths=(call.evidence_dir,),
        )
    if not receipt_path.exists() or not receipt_path.is_file():
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="openviking_receipt_path 不存在或不是文件",
            paths=(receipt_path,),
        )

    raw_output_path = evidence.raw_output_path
    if raw_output_path is None:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="raw_output_path 缺失",
            paths=(call.evidence_dir,),
        )
    if not raw_output_path.exists() or not raw_output_path.is_file():
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="raw_output_path 不存在或不是文件",
            paths=(raw_output_path,),
        )

    try:
        receipt = openviking.read_receipt(receipt_path)
    except Exception as exc:
        return ApprovalCandidateResult.failed(
            category="openviking_receipt",
            reason=f"读取 receipt 失败: {exc}",
            paths=(receipt_path,),
        )

    try:
        l1_read = openviking.read_for_receipt_verification(receipt, call.material_target)
    except Exception as exc:
        return ApprovalCandidateResult.failed(
            category="openviking_receipt",
            reason=f"L1 read 复核失败: {exc}",
            paths=(receipt_path,),
        )
    if not l1_read.ok or l1_read.content is None:
        error_category = l1_read.error_category or "unknown"
        error_message = l1_read.error_message or "L1 read 复核失败"
        return ApprovalCandidateResult.failed(
            category="openviking_receipt",
            reason=f"L1 read 复核失败: {error_category}:{error_message}",
            paths=(receipt_path,),
        )

    try:
        # 正式 L1 只能来自 OpenViking read_for_receipt_verification，不能退回 raw output。
        l1_text = l1_read.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ApprovalCandidateResult.failed(
            category="openviking_receipt",
            reason=f"L1 内容不是 UTF-8: {exc}",
            paths=(receipt_path,),
        )

    try:
        l2_index = openviking.read_l2_index_for_receipt_verification(receipt, call.material_target)
    except Exception as exc:
        return ApprovalCandidateResult.failed(
            category="openviking_receipt",
            reason=f"L2 index 复核失败: {exc}",
            paths=(receipt_path,),
        )

    claims_path = call.evidence_dir / "material-claims.json"
    claims_result = parse_material_claims_evidence(claims_path)
    if not claims_result.ok or claims_result.evidence is None:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason=claims_result.reason or "material-claims 解析失败",
            paths=(claims_path,),
        )
    material_claims = claims_result.evidence
    expected_material_id = make_material_id(call, receipt)
    if material_claims.run_id != call.run_id:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims run_id 与 call 不一致",
            paths=(claims_path,),
        )
    if material_claims.call_id != call.call_id:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims call_id 与 call 不一致",
            paths=(claims_path,),
        )
    if material_claims.worker_id != call.worker_id:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims worker_id 与 call 不一致",
            paths=(claims_path,),
        )
    if material_claims.stage != call.stage:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims stage 与 call 不一致",
            paths=(claims_path,),
        )
    if material_claims.material_id != expected_material_id:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims material_id 与 make_material_id(call, receipt) 不一致",
            paths=(claims_path,),
        )
    if material_claims.target_name != call.material_target.target_name:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims target_name 与 material_target 不一致",
            paths=(claims_path,),
        )
    if material_claims.l1_uri != receipt.uri:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims l1_uri 与 receipt 不一致",
            paths=(claims_path,),
        )
    if material_claims.l1_sha256 != receipt.sha256:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims l1_sha256 与 receipt 不一致",
            paths=(claims_path,),
        )
    if material_claims.l1_size_bytes != receipt.size_bytes:
        return ApprovalCandidateResult.failed(
            category="approval_candidate",
            reason="material-claims l1_size_bytes 与 receipt 不一致",
            paths=(claims_path,),
        )

    return ApprovalCandidateResult.passed(
        ApprovalCandidate(
            call=call,
            receipt=receipt,
            material_claims=material_claims,
            l1_text=l1_text,
            l2_index=l2_index,
            raw_output_path=raw_output_path,
        )
    )


def build_approved_material_from_passed_gates(
    call: WorkerCall,
    candidate: ApprovalCandidate,
    claims: tuple[L1Claim, ...],
) -> ApprovedMaterial:
    # 这里只构造 ApprovedMaterial；manifest 写入由后续 T49A/runner 在 hard gate 证据落盘后负责。
    return ApprovedMaterial(
        material_id=make_material_id(call, candidate.receipt),
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        target_name=candidate.receipt.target_name,
        l1_uri=candidate.receipt.uri,
        l1_sha256=candidate.receipt.sha256,
        l1_size_bytes=candidate.receipt.size_bytes,
        l2_index_uri=candidate.l2_index.index_uri,
        l2_index=candidate.l2_index,
        l1_claims=claims,
        approved_at=_utc_now_iso_text(),
        hard_gate_result_path=call.evidence_dir / "approval-hard-gate.json",
        turn_index=call.turn_index,
        round_index=call.round_index,
        role_turn_index=call.role_turn_index,
    )


def approve_worker_material(
    call: WorkerCall,
    evidence: ProviderEvidence,
    openviking: OpenVikingClient,
) -> ApprovalResult:
    candidate_result = prepare_approval_candidate(call=call, evidence=evidence, openviking=openviking)
    if not candidate_result.ok or candidate_result.candidate is None:
        return ApprovalResult.failed(
            category=candidate_result.category or "approval_candidate",
            reason=candidate_result.reason or "approval candidate 准备失败",
            paths=candidate_result.paths or (call.evidence_dir,),
        )
    candidate = candidate_result.candidate

    raw_output, raw_output_error = _read_raw_output_text(candidate.raw_output_path)
    if raw_output_error is not None:
        return ApprovalResult.failed(
            category="approval_candidate",
            reason=raw_output_error,
            paths=(candidate.raw_output_path,),
        )

    receipt_guard = validate_openviking_receipt(call=call, receipt=candidate.receipt, client=openviking)
    l2_entries_guard = validate_l2_entries(
        client=openviking,
        l2_index=candidate.l2_index,
        allowed_prefix=call.material_target.l2_prefix,
    )
    claims, l1_l2_guard = validate_l1_l2_contract(
        call=call,
        l1_text=candidate.l1_text,
        raw_output=raw_output,
        l2_index=candidate.l2_index,
    )
    # PM owner 边界：
    # - CN_A: PM 自然语言 L1 可作为最终裁决来源，不强制 pm-decision.json。
    # - 其余 profile: 继续要求并校验 pm-decision.json 与 receipt/claims 一致。
    pm_guard = guard_passed(category="pm_owner")
    if call.worker_id == "portfolio_manager":
        _, pm_guard = validate_pm_owner(
            call=call,
            evidence=evidence,
            receipt=candidate.receipt,
            claims=claims,
            require_structured_decision=pm_decision_required_for_profile(call.profile),
        )

    combined = combine_guard_results((receipt_guard, l2_entries_guard, l1_l2_guard, pm_guard))
    if not combined.ok:
        return ApprovalResult.failed(
            category=combined.category,
            reason=combined.reason or "材料批准 hard gate 失败",
            paths=combined.paths,
        )
    material = build_approved_material_from_passed_gates(call=call, candidate=candidate, claims=claims)
    return ApprovalResult.ok_result(material)


def _utc_now_iso_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_raw_output_text(path: Path) -> tuple[str, str | None]:
    try:
        raw_output = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"读取 raw output 失败: {exc}"
    return raw_output, None
