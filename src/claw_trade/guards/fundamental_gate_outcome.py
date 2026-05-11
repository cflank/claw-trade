from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from claw_trade.guards.fundamental_gate_inputs import ClaimGateInput, MaterialGateInput

RUNTIME_EVIDENCE_INVALID = "runtime_evidence_invalid"
UNSUPPORTED_CLAIM_WITH_RETRY = "unsupported_claim_with_retry"
UNSUPPORTED_CLAIM_RETRY_EXHAUSTED = "unsupported_claim_retry_exhausted"
FABRICATION_OR_UNTRUSTED_FACTS = "fabrication_or_untrusted_facts"
GATE_PASSED = "gate_passed"

HARD_FABRICATION_REASON_CODES = frozenset(
    {
        FABRICATION_OR_UNTRUSTED_FACTS,
        "hard_data_fabrication_signal",
        "hard_data_fabrication",
        "untrusted_facts",
    }
)


class GateOutcome(str, Enum):
    REJECT = "reject"
    RERUN = "rerun"
    TERMINATE = "terminate"
    CONTINUE = "continue"


@dataclass(frozen=True)
class RetryBudgetState:
    used: int
    limit: int

    @property
    def can_retry(self) -> bool:
        return self.used < self.limit


@dataclass(frozen=True)
class GateOutcomeDecision:
    outcome: GateOutcome
    reason_code: str
    material_reason_codes: tuple[str, ...]
    claim_reason_codes: tuple[str, ...]
    evidence_paths: tuple[Path, ...]


def decide_gate_outcome(
    material_gate: MaterialGateInput,
    claim_gate: ClaimGateInput,
    retry_budget_state: RetryBudgetState,
) -> GateOutcomeDecision:
    if not material_gate.receipt_ok or not material_gate.visible_tools_ok:
        return _decision(
            outcome=GateOutcome.TERMINATE,
            reason_code=RUNTIME_EVIDENCE_INVALID,
            material_gate=material_gate,
            claim_gate=claim_gate,
        )

    if _has_hard_fabrication_signal(claim_gate):
        return _decision(
            outcome=GateOutcome.REJECT,
            reason_code=FABRICATION_OR_UNTRUSTED_FACTS,
            material_gate=material_gate,
            claim_gate=claim_gate,
        )

    if claim_gate.unsupported_claims and retry_budget_state.can_retry:
        return _decision(
            outcome=GateOutcome.RERUN,
            reason_code=UNSUPPORTED_CLAIM_WITH_RETRY,
            material_gate=material_gate,
            claim_gate=claim_gate,
        )

    if claim_gate.unsupported_claims and not retry_budget_state.can_retry:
        return _decision(
            outcome=GateOutcome.TERMINATE,
            reason_code=UNSUPPORTED_CLAIM_RETRY_EXHAUSTED,
            material_gate=material_gate,
            claim_gate=claim_gate,
        )

    return _decision(
        outcome=GateOutcome.CONTINUE,
        reason_code=GATE_PASSED,
        material_gate=material_gate,
        claim_gate=claim_gate,
    )


def _has_hard_fabrication_signal(claim_gate: ClaimGateInput) -> bool:
    if any(code in HARD_FABRICATION_REASON_CODES for code in claim_gate.reason_codes):
        return True
    for unsupported in claim_gate.unsupported_claims:
        if any(code in HARD_FABRICATION_REASON_CODES for code in unsupported.reason_codes):
            return True
    return False


def _decision(
    *,
    outcome: GateOutcome,
    reason_code: str,
    material_gate: MaterialGateInput,
    claim_gate: ClaimGateInput,
) -> GateOutcomeDecision:
    return GateOutcomeDecision(
        outcome=outcome,
        reason_code=reason_code,
        material_reason_codes=material_gate.reason_codes,
        claim_reason_codes=claim_gate.reason_codes,
        evidence_paths=_merge_paths(material_gate.evidence_paths, claim_gate.evidence_paths),
    )


def _merge_paths(*path_groups: tuple[Path, ...]) -> tuple[Path, ...]:
    merged: list[Path] = []
    for paths in path_groups:
        for path in paths:
            if path not in merged:
                merged.append(path)
    return tuple(merged)
