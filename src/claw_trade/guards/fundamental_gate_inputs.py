from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import MaterialReceipt
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.guards.fundamental_claim_gate import (
    CLAIM_INPUT_INVALID,
    UNSUPPORTED_CLAIM,
    FundamentalReportClaim,
    UnsupportedClaim,
    evaluate_fundamental_report_claims_from_paths_v1,
)
from claw_trade.guards.fundamental_claim_rules import (
    CLAIM_DICTIONARY_REVISION_ID,
    CLAIM_RULES_VERSION,
)
from claw_trade.guards.openviking_receipt import validate_openviking_receipt
from claw_trade.guards.visible_tools import validate_visible_tools
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import WorkerCall

VISIBLE_TOOLS_INVALID = "visible_tools_invalid"
OPENVIKING_RECEIPT_INVALID = "openviking_receipt_invalid"


@dataclass(frozen=True)
class MaterialGateInput:
    visible_tools_ok: bool
    receipt_ok: bool
    reason_codes: tuple[str, ...]
    visible_tools_guard: GuardResult
    receipt_guard: GuardResult
    evidence_paths: tuple[Path, ...]


@dataclass(frozen=True)
class ClaimGateInput:
    report_path: Path | None
    evidence_path: Path | None
    rule_version: str
    dictionary_revision_id: str
    claims: tuple[FundamentalReportClaim, ...]
    unsupported_claims: tuple[UnsupportedClaim, ...]
    reason_codes: tuple[str, ...]
    claim_guard: GuardResult
    evidence_paths: tuple[Path, ...]


@dataclass(frozen=True)
class FundamentalGateInputs:
    material_gate: MaterialGateInput
    claim_gate: ClaimGateInput


def build_material_gate_input(
    call: WorkerCall,
    evidence: ProviderEvidence,
    openviking: OpenVikingClient,
) -> MaterialGateInput:
    visible_tools_guard = validate_visible_tools(call, evidence)
    receipt_guard = _validate_receipt_for_material_gate(call, evidence, openviking)

    reason_codes: list[str] = []
    if not visible_tools_guard.ok:
        reason_codes.append(VISIBLE_TOOLS_INVALID)
    if not receipt_guard.ok:
        reason_codes.append(OPENVIKING_RECEIPT_INVALID)

    return MaterialGateInput(
        visible_tools_ok=visible_tools_guard.ok,
        receipt_ok=receipt_guard.ok,
        reason_codes=tuple(reason_codes),
        visible_tools_guard=visible_tools_guard,
        receipt_guard=receipt_guard,
        evidence_paths=_merge_paths(visible_tools_guard.paths, receipt_guard.paths),
    )


def build_fundamental_gate_inputs(
    call: WorkerCall,
    evidence: ProviderEvidence,
    openviking: OpenVikingClient,
    *,
    report_path: Path | None = None,
    evidence_path: Path | None = None,
) -> FundamentalGateInputs:
    material_gate = build_material_gate_input(call=call, evidence=evidence, openviking=openviking)
    claim_gate = build_claim_gate_input(report_path=report_path, evidence_path=evidence_path)
    return FundamentalGateInputs(
        material_gate=material_gate,
        claim_gate=claim_gate,
    )


def build_claim_gate_input(
    *,
    report_path: Path | None,
    evidence_path: Path | None,
) -> ClaimGateInput:
    claim_paths = _merge_paths(
        (report_path,) if report_path is not None else (),
        (evidence_path,) if evidence_path is not None else (),
    )
    if report_path is None or evidence_path is None:
        guard = guard_failed(
            category="claim",
            reason="claim gate 输入缺失: report_path 或 evidence_path 为空",
            paths=claim_paths,
            early_stop=True,
        )
        return ClaimGateInput(
            report_path=report_path,
            evidence_path=evidence_path,
            rule_version=CLAIM_RULES_VERSION,
            dictionary_revision_id=CLAIM_DICTIONARY_REVISION_ID,
            claims=(),
            unsupported_claims=(),
            reason_codes=(CLAIM_INPUT_INVALID,),
            claim_guard=guard,
            evidence_paths=claim_paths,
        )
    if not report_path.exists() or not report_path.is_file():
        guard = guard_failed(
            category="claim",
            reason="claim gate report_path 不存在或不是文件",
            paths=claim_paths,
            early_stop=True,
        )
        return ClaimGateInput(
            report_path=report_path,
            evidence_path=evidence_path,
            rule_version=CLAIM_RULES_VERSION,
            dictionary_revision_id=CLAIM_DICTIONARY_REVISION_ID,
            claims=(),
            unsupported_claims=(),
            reason_codes=(CLAIM_INPUT_INVALID,),
            claim_guard=guard,
            evidence_paths=claim_paths,
        )
    if not evidence_path.exists() or not evidence_path.is_file():
        guard = guard_failed(
            category="claim",
            reason="claim gate evidence_path 不存在或不是文件",
            paths=claim_paths,
            early_stop=True,
        )
        return ClaimGateInput(
            report_path=report_path,
            evidence_path=evidence_path,
            rule_version=CLAIM_RULES_VERSION,
            dictionary_revision_id=CLAIM_DICTIONARY_REVISION_ID,
            claims=(),
            unsupported_claims=(),
            reason_codes=(CLAIM_INPUT_INVALID,),
            claim_guard=guard,
            evidence_paths=claim_paths,
        )

    try:
        evaluation = evaluate_fundamental_report_claims_from_paths_v1(
            report_path=report_path,
            evidence_path=evidence_path,
        )
    except Exception as exc:
        guard = guard_failed(
            category="claim",
            reason=f"claim gate 输入解析失败: {exc}",
            paths=claim_paths,
            early_stop=True,
        )
        return ClaimGateInput(
            report_path=report_path,
            evidence_path=evidence_path,
            rule_version=CLAIM_RULES_VERSION,
            dictionary_revision_id=CLAIM_DICTIONARY_REVISION_ID,
            claims=(),
            unsupported_claims=(),
            reason_codes=(CLAIM_INPUT_INVALID,),
            claim_guard=guard,
            evidence_paths=claim_paths,
        )

    reason_codes: list[str] = []
    if not evaluation.guard.ok and evaluation.unsupported_claims:
        reason_codes.append(UNSUPPORTED_CLAIM)
        claim_guard = guard_failed(
            category="claim",
            reason=evaluation.guard.reason or "unsupported_claim",
            paths=claim_paths,
            early_stop=True,
        )
    elif not evaluation.guard.ok:
        reason_codes.append(CLAIM_INPUT_INVALID)
        claim_guard = guard_failed(
            category="claim",
            reason=evaluation.guard.reason or "claim gate failed",
            paths=claim_paths,
            early_stop=True,
        )
    else:
        claim_guard = guard_passed(category="claim")

    return ClaimGateInput(
        report_path=report_path,
        evidence_path=evidence_path,
        rule_version=evaluation.rule_version,
        dictionary_revision_id=evaluation.dictionary_revision_id,
        claims=evaluation.claims,
        unsupported_claims=evaluation.unsupported_claims,
        reason_codes=tuple(reason_codes),
        claim_guard=claim_guard,
        evidence_paths=claim_paths,
    )


def _validate_receipt_for_material_gate(
    call: WorkerCall,
    evidence: ProviderEvidence,
    openviking: OpenVikingClient,
) -> GuardResult:
    receipt_path = evidence.openviking_receipt_path
    if receipt_path is None:
        return guard_failed(
            category="openviking_receipt",
            reason="openviking_receipt_path 缺失",
            paths=(call.evidence_dir,),
        )
    if not receipt_path.exists() or not receipt_path.is_file():
        return guard_failed(
            category="openviking_receipt",
            reason="openviking_receipt_path 不存在或不是文件",
            paths=(receipt_path,),
        )

    try:
        receipt = openviking.read_receipt(receipt_path)
    except Exception as exc:
        return guard_failed(
            category="openviking_receipt",
            reason=f"读取 receipt 失败: {exc}",
            paths=(receipt_path,),
        )

    base_guard = validate_openviking_receipt(call=call, receipt=receipt, client=openviking)
    if not base_guard.ok:
        return base_guard
    return _validate_receipt_hash_size_with_stat_readback(call=call, receipt=receipt, openviking=openviking)


def _validate_receipt_hash_size_with_stat_readback(
    call: WorkerCall,
    receipt: MaterialReceipt,
    openviking: OpenVikingClient,
) -> GuardResult:
    stat = openviking.stat_for_receipt_verification(receipt, call.material_target)
    if not stat.ok:
        reason = stat.error_message or "receipt stat 复核失败"
        return guard_failed(
            category="openviking_receipt",
            reason=f"receipt stat 复核失败: {stat.error_category}:{reason}",
            paths=(call.evidence_dir / "openviking-receipt.json",),
        )

    if stat.size_bytes is not None and stat.size_bytes != receipt.size_bytes:
        return guard_failed(
            category="openviking_receipt",
            reason=(
                "receipt size 与 stat/readback 不一致: "
                f"receipt={receipt.size_bytes} stat={stat.size_bytes}"
            ),
            paths=(call.evidence_dir / "openviking-receipt.json",),
        )
    if stat.sha256 is not None and stat.sha256 != receipt.sha256:
        return guard_failed(
            category="openviking_receipt",
            reason=(
                "receipt sha256 与 stat/readback 不一致: "
                f"receipt={receipt.sha256} stat={stat.sha256}"
            ),
            paths=(call.evidence_dir / "openviking-receipt.json",),
        )

    read_result = openviking.read_for_receipt_verification(receipt, call.material_target)
    if not read_result.ok:
        reason = read_result.error_message or "receipt read 复核失败"
        return guard_failed(
            category="openviking_receipt",
            reason=f"receipt read 复核失败: {read_result.error_category}:{reason}",
            paths=(call.evidence_dir / "openviking-receipt.json",),
        )

    read_size = read_result.size_bytes
    read_sha256 = read_result.sha256
    if read_size is None or read_size != receipt.size_bytes:
        return guard_failed(
            category="openviking_receipt",
            reason=(
                "receipt size 与 stat/readback 不一致: "
                f"receipt={receipt.size_bytes} read={read_size}"
            ),
            paths=(call.evidence_dir / "openviking-receipt.json",),
        )
    if read_sha256 is None or read_sha256 != receipt.sha256:
        return guard_failed(
            category="openviking_receipt",
            reason=(
                "receipt sha256 与 stat/readback 不一致: "
                f"receipt={receipt.sha256} read={read_sha256}"
            ),
            paths=(call.evidence_dir / "openviking-receipt.json",),
        )
    return guard_passed(category="openviking_receipt")


def _merge_paths(*path_groups: tuple[Path, ...]) -> tuple[Path, ...]:
    merged: list[Path] = []
    for paths in path_groups:
        for path in paths:
            if path not in merged:
                merged.append(path)
    return tuple(merged)
