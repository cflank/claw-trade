from __future__ import annotations

from pathlib import Path

from claw_trade.guards.common import guard_passed
from claw_trade.guards.fundamental_claim_gate import FundamentalReportClaim, UnsupportedClaim
from claw_trade.guards.fundamental_claim_rules import (
    CLAIM_DICTIONARY_REVISION_ID,
    CLAIM_RULES_VERSION,
)
from claw_trade.guards.fundamental_gate_inputs import ClaimGateInput, MaterialGateInput
from claw_trade.guards.fundamental_gate_outcome import (
    FABRICATION_OR_UNTRUSTED_FACTS,
    GATE_PASSED,
    RUNTIME_EVIDENCE_INVALID,
    UNSUPPORTED_CLAIM_RETRY_EXHAUSTED,
    UNSUPPORTED_CLAIM_WITH_RETRY,
    GateOutcome,
    RetryBudgetState,
    decide_gate_outcome,
)


def test_decide_gate_outcome_terminates_when_runtime_evidence_invalid(tmp_path: Path) -> None:
    material = _material_gate(
        visible_tools_ok=True,
        receipt_ok=False,
        reason_codes=("openviking_receipt_invalid",),
        evidence_paths=(tmp_path / "receipt.json",),
    )
    claim = _claim_gate(
        unsupported_claims=(),
        reason_codes=(),
        evidence_paths=(tmp_path / "report.md",),
    )

    decision = decide_gate_outcome(material, claim, RetryBudgetState(used=0, limit=1))

    assert decision.outcome is GateOutcome.TERMINATE
    assert decision.reason_code == RUNTIME_EVIDENCE_INVALID
    assert decision.material_reason_codes == ("openviking_receipt_invalid",)
    assert decision.claim_reason_codes == ()
    assert decision.evidence_paths == (tmp_path / "receipt.json", tmp_path / "report.md")


def test_decide_gate_outcome_reruns_when_unsupported_claim_and_retry_budget_available(tmp_path: Path) -> None:
    material = _material_gate(
        visible_tools_ok=True,
        receipt_ok=True,
        reason_codes=(),
        evidence_paths=(tmp_path / "visible-tools.json",),
    )
    claim = _claim_gate(
        unsupported_claims=(_unsupported_claim(),),
        reason_codes=("unsupported_claim",),
        evidence_paths=(tmp_path / "report.md", tmp_path / "pack.json"),
    )

    decision = decide_gate_outcome(material, claim, RetryBudgetState(used=0, limit=2))

    assert decision.outcome is GateOutcome.RERUN
    assert decision.reason_code == UNSUPPORTED_CLAIM_WITH_RETRY
    assert decision.claim_reason_codes == ("unsupported_claim",)


def test_decide_gate_outcome_rejects_when_unsupported_claim_contains_hard_fabrication_signal(
    tmp_path: Path,
) -> None:
    material = _material_gate(
        visible_tools_ok=True,
        receipt_ok=True,
        reason_codes=(),
        evidence_paths=(tmp_path / "visible-tools.json",),
    )
    claim = _claim_gate(
        unsupported_claims=(_unsupported_claim(reason_codes=("hard_data_fabrication_signal",)),),
        reason_codes=("unsupported_claim",),
        evidence_paths=(tmp_path / "report.md", tmp_path / "pack.json"),
    )

    decision = decide_gate_outcome(material, claim, RetryBudgetState(used=0, limit=2))

    assert decision.outcome is GateOutcome.REJECT
    assert decision.reason_code == FABRICATION_OR_UNTRUSTED_FACTS


def test_decide_gate_outcome_terminates_when_unsupported_claim_retry_exhausted(tmp_path: Path) -> None:
    material = _material_gate(
        visible_tools_ok=True,
        receipt_ok=True,
        reason_codes=(),
        evidence_paths=(tmp_path / "visible-tools.json",),
    )
    claim = _claim_gate(
        unsupported_claims=(_unsupported_claim(),),
        reason_codes=("unsupported_claim",),
        evidence_paths=(tmp_path / "report.md",),
    )

    decision = decide_gate_outcome(material, claim, RetryBudgetState(used=1, limit=1))

    assert decision.outcome is GateOutcome.TERMINATE
    assert decision.reason_code == UNSUPPORTED_CLAIM_RETRY_EXHAUSTED


def test_decide_gate_outcome_rejects_when_hard_fabrication_signal_exists(tmp_path: Path) -> None:
    material = _material_gate(
        visible_tools_ok=True,
        receipt_ok=True,
        reason_codes=(),
        evidence_paths=(tmp_path / "visible-tools.json",),
    )
    claim = _claim_gate(
        unsupported_claims=(),
        reason_codes=("hard_data_fabrication_signal",),
        evidence_paths=(tmp_path / "report.md",),
    )

    decision = decide_gate_outcome(material, claim, RetryBudgetState(used=0, limit=1))

    assert decision.outcome is GateOutcome.REJECT
    assert decision.reason_code == FABRICATION_OR_UNTRUSTED_FACTS


def test_decide_gate_outcome_continues_when_all_gates_pass(tmp_path: Path) -> None:
    material = _material_gate(
        visible_tools_ok=True,
        receipt_ok=True,
        reason_codes=(),
        evidence_paths=(tmp_path / "visible-tools.json",),
    )
    claim = _claim_gate(
        unsupported_claims=(),
        reason_codes=(),
        evidence_paths=(tmp_path / "report.md",),
    )

    decision = decide_gate_outcome(material, claim, RetryBudgetState(used=0, limit=1))

    assert decision.outcome is GateOutcome.CONTINUE
    assert decision.reason_code == GATE_PASSED


def _material_gate(
    *,
    visible_tools_ok: bool,
    receipt_ok: bool,
    reason_codes: tuple[str, ...],
    evidence_paths: tuple[Path, ...],
) -> MaterialGateInput:
    guard = guard_passed(category="material")
    return MaterialGateInput(
        visible_tools_ok=visible_tools_ok,
        receipt_ok=receipt_ok,
        reason_codes=reason_codes,
        visible_tools_guard=guard,
        receipt_guard=guard,
        evidence_paths=evidence_paths,
    )


def _claim_gate(
    *,
    unsupported_claims: tuple[UnsupportedClaim, ...],
    reason_codes: tuple[str, ...],
    evidence_paths: tuple[Path, ...],
) -> ClaimGateInput:
    return ClaimGateInput(
        report_path=None,
        pack_path=None,
        rule_version=CLAIM_RULES_VERSION,
        dictionary_revision_id=CLAIM_DICTIONARY_REVISION_ID,
        claims=(),
        unsupported_claims=unsupported_claims,
        reason_codes=reason_codes,
        claim_guard=guard_passed(category="claim"),
        evidence_paths=evidence_paths,
    )


def _unsupported_claim(
    *,
    reason_codes: tuple[str, ...] = ("required_fact_missing",),
) -> UnsupportedClaim:
    claim = FundamentalReportClaim(
        claim_type="metric",
        claim_key="pb",
        text="PB约5倍",
        span_start=0,
        span_end=6,
        needs_trend=False,
    )
    return UnsupportedClaim(claim=claim, reason_codes=reason_codes)
