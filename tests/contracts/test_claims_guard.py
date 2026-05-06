from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from claw_trade.artifacts.claims import high_risk_claim_kinds, parse_l1_claim_block, require_l1_claim_block, validate_claims
from claw_trade.artifacts.refs import L2Entry, L2Index, MaterialTarget, make_material_target
from claw_trade.workflow.models import Stage


@dataclass(frozen=True)
class WorkerCallSample:
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    material_target: MaterialTarget
    evidence_dir: Path


def sample_call() -> WorkerCallSample:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    return WorkerCallSample(
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        stage=target.stage,
        material_target=target,
        evidence_dir=Path("runs/run-1/calls/call-1"),
    )


def test_claim_block_missing_hard_fail() -> None:
    parsed = parse_l1_claim_block("# 正式报告\n\n正文提到目标价 100 元。")
    assert not parsed.ok


def test_claim_block_json_invalid_hard_fail() -> None:
    l1 = "```json\n{\"schema_version\": \"control.claims.v1\", bad}\n```"
    parsed = parse_l1_claim_block(l1)
    assert not parsed.ok


def test_claim_block_identity_mismatch_hard_fail() -> None:
    call = sample_call()
    block = claim_block_json(worker_id="news_analyst")
    claims, guard = require_l1_claim_block(with_claim_block(block), call)
    assert not claims
    assert not guard.ok


def test_high_risk_claim_requires_l2_mapping() -> None:
    call = sample_call()
    block = claim_block_json(evidence_ids=("l2-001",))
    claims, guard = require_l1_claim_block(with_claim_block(block), call)
    assert guard.ok
    claim_guard = validate_claims(claims, empty_l2_with_reason())
    assert not claim_guard.ok


def test_high_risk_claim_with_l2_mapping_passes() -> None:
    call = sample_call()
    block = claim_block_json(evidence_ids=("l2-001",))
    claims, guard = require_l1_claim_block(with_claim_block(block), call)
    assert guard.ok
    claim_guard = validate_claims(claims, l2_with_entry("l2-001"))
    assert claim_guard.ok


def test_plain_text_claim_is_not_parsed() -> None:
    call = sample_call()
    l1 = "# 正式报告\n\n我们建议买入，目标价 100 元。"
    claims, guard = require_l1_claim_block(l1, call)
    assert not claims
    assert not guard.ok


@pytest.mark.parametrize(
    "kind",
    ("news", "valuation", "target_price", "rating", "trade_action", "risk_condition", "sentiment", "chart", "tool_success", "source"),
)
def test_all_high_risk_kinds_require_evidence(kind: str) -> None:
    call = sample_call()
    block = claim_block_json(kind=kind, evidence_ids=())
    claims, guard = require_l1_claim_block(with_claim_block(block), call)
    assert guard.ok
    claim_guard = validate_claims(claims, empty_l2_with_reason())
    assert not claim_guard.ok


def test_high_risk_claim_kinds_contains_required_kinds() -> None:
    assert high_risk_claim_kinds() == (
        "valuation",
        "target_price",
        "rating",
        "trade_action",
        "risk_condition",
        "news",
        "sentiment",
        "chart",
        "tool_success",
        "source",
    )


def claim_block_json(
    *,
    worker_id: str = "market_analyst",
    stage: str = "frontline",
    kind: str = "valuation",
    evidence_ids: tuple[str, ...] = ("l2-001",),
) -> dict[str, object]:
    return {
        "schema_version": "control.claims.v1",
        "run_id": "run-1",
        "call_id": "call-1",
        "worker_id": worker_id,
        "stage": stage,
        "material_id": "mat-claim-001",
        "claims": [
            {
                "claim_id": "claim-001",
                "kind": kind,
                "text": "目标价 100 元",
                "value": None,
                "evidence_ids": list(evidence_ids),
                "source_worker_id": worker_id,
            }
        ],
    }


def with_claim_block(block: dict[str, object]) -> str:
    return "# 正式报告\n\n```json\n" + json.dumps(block, ensure_ascii=False, indent=2) + "\n```"


def empty_l2_with_reason() -> L2Index:
    return L2Index(entries=(), empty_reason="no_evidence", index_uri=None, index_sha256=None, index_size_bytes=None)


def l2_with_entry(evidence_id: str) -> L2Index:
    return L2Index(
        entries=(
            L2Entry(
                evidence_id=evidence_id,
                uri=f"viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/{evidence_id}.json",
                kind="source",
                source="provider",
                sha256=f"sha-{evidence_id}",
                size_bytes=16,
            ),
        ),
        empty_reason=None,
        index_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/index.json",
        index_sha256="sha-index",
        index_size_bytes=24,
    )
