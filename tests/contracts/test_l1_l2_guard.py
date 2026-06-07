from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from claw_trade.artifacts.openviking_client import OpenVikingClient, OpenVikingStat
from claw_trade.artifacts.refs import L2Entry, L2Index, MaterialTarget, make_material_target
from claw_trade.guards.l1_l2 import validate_l1_l2_contract, validate_l2_entries
from claw_trade.workflow.models import Stage


@dataclass(frozen=True)
class WorkerCallSample:
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    material_target: MaterialTarget
    evidence_dir: Path


def sample_call(tmp_path: Path) -> WorkerCallSample:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return WorkerCallSample(
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        stage=target.stage,
        material_target=target,
        evidence_dir=evidence_dir,
    )


@dataclass
class FakeBackend:
    content_by_uri: dict[str, bytes]

    def fetch_stat_by_uri(self, uri: str):  # type: ignore[no-untyped-def]
        content = self.content_by_uri[uri]
        return OpenVikingStat(uri=uri, sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return self.content_by_uri[uri]

    def fetch_l2_index_by_uri(self, uri: str | None):  # type: ignore[no-untyped-def]
        del uri
        raise NotImplementedError

    def fetch_receipt_by_path(self, path):  # type: ignore[no-untyped-def]
        del path
        raise NotImplementedError

    def ensure_namespace(self, namespace: str) -> None:
        del namespace


def sample_openviking_for_l2(index: L2Index) -> OpenVikingClient:
    content: dict[str, bytes] = {}
    if index.index_uri is not None:
        content[index.index_uri] = b'{"entries":[]}'
    for entry in index.entries:
        content[entry.uri] = entry.evidence_id.encode("utf-8")
    return OpenVikingClient(FakeBackend(content_by_uri=content))


def test_validate_l1_l2_contract_uses_material_claims_evidence(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    write_material_claims(call=call, evidence_id="l2-001")
    claims, guard = validate_l1_l2_contract(call, "# 正式报告", "任意 raw output", l2_with_entry("l2-001"))
    assert claims
    assert guard.ok


def test_validate_l1_l2_contract_rejects_missing_material_claims_evidence(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    claims, guard = validate_l1_l2_contract(call, "# 正式报告", "raw", l2_with_entry("l2-001"))
    assert not claims
    assert not guard.ok
    assert guard.reason is not None
    assert "material-claims.json" in guard.reason


def test_validate_l1_l2_contract_rejects_manual_claim_block_in_l1(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    write_material_claims(call=call, evidence_id="l2-001")
    manual = "```json\n{\"schema_version\":\"control.claims.v1\"}\n```"
    claims, guard = validate_l1_l2_contract(call, "# 正式报告\n\n" + manual, "raw", l2_with_entry("l2-001"))
    assert not claims
    assert not guard.ok


def test_validate_l1_l2_contract_rejects_missing_l2_for_high_risk_claim(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    write_material_claims(call=call, evidence_id="l2-missing")
    claims, guard = validate_l1_l2_contract(call, "# 正式报告", "raw output", empty_l2_with_reason())
    assert claims
    assert not guard.ok


def test_validate_l1_l2_contract_rejects_compact_l1(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    write_material_claims(call=call, evidence_id="l2-001")
    claims, guard = validate_l1_l2_contract(call, "# compact", "raw", l2_with_entry("l2-001"))
    assert not claims
    assert not guard.ok


@pytest.mark.parametrize(
    "l1",
    (
        '{"report":"这不是自然语言报告"}',
        '```json\n{"report":"这不是自然语言报告"}\n```',
    ),
)
def test_validate_l1_l2_contract_rejects_json_document_l1(tmp_path: Path, l1: str) -> None:
    call = sample_call(tmp_path)
    write_material_claims(call=call, evidence_id="l2-001")

    claims, guard = validate_l1_l2_contract(call, l1, "raw", l2_with_entry("l2-001"))

    assert not claims
    assert not guard.ok
    assert guard.reason is not None and "自然语言报告" in guard.reason


def test_validate_l1_l2_contract_allows_verified_l1_even_if_text_equals_raw_output(tmp_path: Path) -> None:
    call = sample_call(tmp_path)
    write_material_claims(call=call, evidence_id="l2-001")
    l1 = "# 正式报告"
    claims, guard = validate_l1_l2_contract(call, l1, l1, l2_with_entry("l2-001"))
    assert claims
    assert guard.ok


def test_validate_l2_entries_rejects_outside_prefix() -> None:
    l2 = L2Index(
        entries=(
            L2Entry(
                evidence_id="l2-001",
                uri="viking://resources/workflow/run-1/frontline/another_worker/call-2/evidence/l2-001.json",
                kind="source",
                source="provider",
                sha256="sha-l2-001",
                size_bytes=16,
            ),
        ),
        empty_reason=None,
        index_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/index.json",
        index_sha256=hashlib.sha256(b'{"entries":[]}').hexdigest(),
        index_size_bytes=len(b'{"entries":[]}'),
    )
    guard = validate_l2_entries(
        sample_openviking_for_l2(l2),
        l2,
        "viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/",
    )
    assert not guard.ok


def test_validate_l2_entries_verifies_stat_read_sha_size() -> None:
    l2 = l2_with_entry("l2-001")
    guard = validate_l2_entries(
        sample_openviking_for_l2(l2),
        l2,
        "viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/",
    )
    assert guard.ok


def write_material_claims(*, call: WorkerCallSample, evidence_id: str) -> None:
    payload = {
        "schema_version": "control.claims.v1",
        "source": "openclaw_openviking_write_material",
        "run_id": call.run_id,
        "call_id": call.call_id,
        "worker_id": call.worker_id,
        "stage": call.stage.value,
        "material_id": "mat-fake",
        "target_name": call.material_target.target_name,
        "l1_uri": call.material_target.l1_uri,
        "l1_sha256": "sha-l1",
        "l1_size_bytes": 16,
        "material_layer": "L1",
        "source_kind": "worker_report",
        "claims": [
            {
                "claim_id": "claim-001",
                "kind": "valuation",
                "text": "目标价 100 元",
                "value": None,
                "evidence_ids": [evidence_id],
                "source_worker_id": call.worker_id,
            }
        ],
    }
    (call.evidence_dir / "material-claims.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def empty_l2_with_reason() -> L2Index:
    return L2Index(entries=(), empty_reason="no_evidence", index_uri=None, index_sha256=None, index_size_bytes=None)


def l2_with_entry(evidence_id: str) -> L2Index:
    entry_uri = f"viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/{evidence_id}.json"
    entry_content = evidence_id.encode("utf-8")
    index_content = b'{"entries":[]}'
    return L2Index(
        entries=(
            L2Entry(
                evidence_id=evidence_id,
                uri=entry_uri,
                kind="source",
                source="provider",
                sha256=hashlib.sha256(entry_content).hexdigest(),
                size_bytes=len(entry_content),
            ),
        ),
        empty_reason=None,
        index_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/index.json",
        index_sha256=hashlib.sha256(index_content).hexdigest(),
        index_size_bytes=len(index_content),
    )
