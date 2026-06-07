from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claw_trade.artifacts.approval import (
    build_approved_material_from_passed_gates,
    prepare_approval_candidate,
)
from claw_trade.artifacts.manifest import make_material_id
from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import (
    L1Claim,
    L2Entry,
    L2Index,
    MaterialReceipt,
    make_material_target,
)
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall

from tests.fakes.openviking_store import FakeOpenVikingStore


def test_prepare_approval_candidate_fails_without_receipt_path(tmp_path: Path) -> None:
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=False, include_raw_output_path=True)
    client = OpenVikingClient(_BackendAdapter(FakeOpenVikingStore()))
    result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)
    assert not result.ok
    assert result.category == "approval_candidate"
    assert result.reason is not None
    assert "openviking_receipt_path 缺失" in result.reason


def test_prepare_approval_candidate_fails_without_raw_output_path(tmp_path: Path) -> None:
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=True, include_raw_output_path=False)
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, _make_receipt(call.material_target, b"L1"))  # type: ignore[arg-type]
    client = OpenVikingClient(_BackendAdapter(store))
    result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)
    assert not result.ok
    assert result.category == "approval_candidate"
    assert result.reason is not None
    assert "raw_output_path 缺失" in result.reason


def test_prepare_approval_candidate_uses_openviking_l1_not_raw_output(tmp_path: Path) -> None:
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=True, include_raw_output_path=True)
    l1_text = "这是来自 OpenViking 的正式 L1"
    raw_text = "这是 raw output，不应被当作正式 L1"
    evidence.raw_output_path.write_text(raw_text, encoding="utf-8")  # type: ignore[union-attr]
    index = _make_l2_index(call.material_target)
    receipt = _make_receipt(call.material_target, l1_text.encode("utf-8"))
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, receipt)  # type: ignore[arg-type]
    store.seed_object(call.material_target.l1_uri, l1_text.encode("utf-8"))
    store.seed_l2_index(f"{call.material_target.l2_prefix}index.json", index)
    _write_material_claims(call=call, receipt=receipt, evidence_id="l2-1")
    client = OpenVikingClient(_BackendAdapter(store))

    result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)

    assert result.ok
    assert result.candidate is not None
    assert result.candidate.l1_text == l1_text
    assert result.candidate.l1_text != raw_text
    assert result.candidate.raw_output_path == evidence.raw_output_path
    assert result.candidate.l2_index == index


def test_prepare_approval_candidate_allows_l1_hash_mismatch_for_audit_only(tmp_path: Path) -> None:
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=True, include_raw_output_path=True)
    receipt = _make_receipt(call.material_target, b"expected-l1")
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, receipt)  # type: ignore[arg-type]
    store.seed_object(call.material_target.l1_uri, b"mismatched-l1")
    store.seed_l2_index(f"{call.material_target.l2_prefix}index.json", _make_l2_index(call.material_target))
    _write_material_claims(call=call, receipt=receipt, evidence_id="l2-1")
    client = OpenVikingClient(_BackendAdapter(store))

    result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)

    assert result.ok
    assert result.candidate is not None


def test_prepare_approval_candidate_returns_failed_when_l1_read_raises(tmp_path: Path) -> None:
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=True, include_raw_output_path=True)
    receipt = _make_receipt(call.material_target, b"expected-l1")
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, receipt)  # type: ignore[arg-type]
    client = OpenVikingClient(_RaiseOnL1ReadBackendAdapter(store))

    result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)

    assert not result.ok
    assert result.category == "openviking_receipt"
    assert result.reason is not None
    assert "L1 read 复核失败" in result.reason
    assert "backend down for L1" in result.reason


def test_prepare_approval_candidate_returns_failed_when_l2_index_read_fails(tmp_path: Path) -> None:
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=True, include_raw_output_path=True)
    receipt = _make_receipt(call.material_target, b"l1-content")
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, receipt)  # type: ignore[arg-type]
    store.seed_object(call.material_target.l1_uri, b"l1-content")
    client = OpenVikingClient(_BackendAdapter(store))

    result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)

    assert not result.ok
    assert result.category == "openviking_receipt"
    assert result.reason is not None
    assert "L2 index 复核失败" in result.reason


def test_build_approved_material_from_passed_gates_does_not_write_manifest_and_fields_correct(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    call = _make_call(tmp_path)
    evidence = _make_evidence(call, include_receipt_path=True, include_raw_output_path=True)
    l1_text = "L1 正文"
    receipt = _make_receipt(call.material_target, l1_text.encode("utf-8"))
    l2_index = _make_l2_index(call.material_target)
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, receipt)  # type: ignore[arg-type]
    store.seed_object(call.material_target.l1_uri, l1_text.encode("utf-8"))
    store.seed_l2_index(f"{call.material_target.l2_prefix}index.json", l2_index)
    _write_material_claims(call=call, receipt=receipt, evidence_id="l2-1")
    client = OpenVikingClient(_BackendAdapter(store))
    candidate_result = prepare_approval_candidate(call=call, evidence=evidence, openviking=client)
    assert candidate_result.ok
    assert candidate_result.candidate is not None
    candidate = candidate_result.candidate
    claims = (
        L1Claim(
            claim_id="claim-1",
            kind="source",
            text="引用来源",
            value=None,
            required_evidence_kinds=("source",),
            evidence_ids=("l2-1",),
        ),
    )
    manifest_path = tmp_path / "runs" / call.run_id / "openviking" / "approved-manifest.json"
    assert not manifest_path.exists()

    material = build_approved_material_from_passed_gates(call=call, candidate=candidate, claims=claims)

    assert material.material_id == make_material_id(call, candidate.receipt)
    assert material.run_id == call.run_id
    assert material.call_id == call.call_id
    assert material.worker_id == call.worker_id
    assert material.stage == call.stage
    assert material.target_name == call.material_target.target_name
    assert material.l1_uri == candidate.receipt.uri
    assert material.l1_sha256 == candidate.receipt.sha256
    assert material.l1_size_bytes == candidate.receipt.size_bytes
    assert material.l2_index_uri == l2_index.index_uri
    assert material.l2_index == l2_index
    assert material.l1_claims == claims
    assert material.hard_gate_result_path == call.evidence_dir / "approval-hard-gate.json"
    assert material.approved_at.endswith("Z")
    assert not manifest_path.exists()
    assert not material.hard_gate_result_path.exists()


def _make_call(tmp_path: Path) -> WorkerCall:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return WorkerCall(
        call_id="call-1",
        run_id="run-1",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="us",
        ticker="AAPL",
        company_name="Apple Inc.",
        market="US",
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-04-04",
        end_date="2026-05-04",
        allowed_tools=("fetch_market_data",),
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )


def _make_evidence(
    call: WorkerCall,
    *,
    include_receipt_path: bool,
    include_raw_output_path: bool,
) -> ProviderEvidence:
    raw_output_path = call.evidence_dir / "raw-output.txt"
    receipt_path = call.evidence_dir / "openviking-receipt.json"
    _write_json(call.evidence_dir / "workspace-evidence.json")
    _write_json(call.evidence_dir / "provider-request.json")
    _write_json(call.evidence_dir / "visible-tools.json")
    _write_json(call.evidence_dir / "first-response.json")
    _write_json(call.evidence_dir / "tool-calls.json")
    if include_raw_output_path:
        raw_output_path.write_text("raw output", encoding="utf-8")
    if include_receipt_path:
        receipt_path.write_text("{}", encoding="utf-8")
    return ProviderEvidence(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        openclaw_run_id="openclaw-run-1",
        provider_request_id="provider-req-1",
        provider_request_id_status="provided",
        workspace_evidence_path=call.evidence_dir / "workspace-evidence.json",
        provider_request_path=call.evidence_dir / "provider-request.json",
        visible_tools_path=call.evidence_dir / "visible-tools.json",
        first_response_path=call.evidence_dir / "first-response.json",
        tool_calls_status="executed",
        tool_calls_path=call.evidence_dir / "tool-calls.json",
        raw_output_path=raw_output_path if include_raw_output_path else None,
        openviking_receipt_path=receipt_path if include_receipt_path else None,
    )


def _make_receipt(target, l1_content: bytes) -> MaterialReceipt:  # type: ignore[no-untyped-def]
    return MaterialReceipt(
        uri=target.l1_uri,
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        stage=target.stage,
        target_name=target.target_name,
        sha256=hashlib.sha256(l1_content).hexdigest(),
        size_bytes=len(l1_content),
        written_at="2026-05-04T12:00:00Z",
        receipt_id="receipt-1",
    )


def _make_l2_index(target) -> L2Index:  # type: ignore[no-untyped-def]
    index_uri = f"{target.l2_prefix}index.json"
    entry_uri = f"{target.l2_prefix}l2-1.json"
    entry_content = b"evidence"
    index_content = b'{"entries":[{"evidence_id":"l2-1"}]}'
    return L2Index(
        entries=(
            L2Entry(
                evidence_id="l2-1",
                uri=entry_uri,
                kind="source",
                source="provider",
                sha256=hashlib.sha256(entry_content).hexdigest(),
                size_bytes=len(entry_content),
            ),
        ),
        empty_reason=None,
        index_uri=index_uri,
        index_sha256=hashlib.sha256(index_content).hexdigest(),
        index_size_bytes=len(index_content),
    )


def _write_json(path: Path) -> None:
    path.write_text(json.dumps({"ok": True}), encoding="utf-8")


class _BackendAdapter:
    def __init__(self, store: FakeOpenVikingStore) -> None:
        self._store = store

    def ensure_namespace(self, namespace: str) -> None:
        del namespace
        return None

    def fetch_receipt_by_path(self, receipt_path):  # type: ignore[no-untyped-def]
        return self._store.read_receipt(receipt_path)

    def fetch_stat_by_uri(self, uri: str):  # type: ignore[no-untyped-def]
        return self._store.stat(uri)

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return self._store.read(uri)

    def fetch_l2_index_by_uri(self, uri: str | None):
        return self._store.read_l2_index(uri)


class _RaiseOnL1ReadBackendAdapter(_BackendAdapter):
    def fetch_content_by_uri(self, uri: str) -> bytes:
        del uri
        raise RuntimeError("backend down for L1")


def _write_material_claims(*, call: WorkerCall, receipt: MaterialReceipt, evidence_id: str) -> None:
    payload = {
        "schema_version": "control.claims.v1",
        "source": "openclaw_openviking_write_material",
        "run_id": call.run_id,
        "call_id": call.call_id,
        "worker_id": call.worker_id,
        "stage": call.stage.value,
        "material_id": make_material_id(call, receipt),
        "target_name": call.material_target.target_name,
        "l1_uri": receipt.uri,
        "l1_sha256": receipt.sha256,
        "l1_size_bytes": receipt.size_bytes,
        "material_layer": "L1",
        "source_kind": "worker_report",
        "claims": [
            {
                "claim_id": "claim-1",
                "kind": "source",
                "text": "引用来源",
                "value": None,
                "evidence_ids": [evidence_id],
                "source_worker_id": call.worker_id,
            }
        ],
    }
    (call.evidence_dir / "material-claims.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
