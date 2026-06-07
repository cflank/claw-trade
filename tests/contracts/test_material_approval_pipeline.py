from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claw_trade.artifacts.approval import approve_worker_material
from claw_trade.artifacts.manifest import ManifestStore, make_material_id
from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import (
    L2Entry,
    L2Index,
    MaterialReceipt,
    MaterialReceiptVerification,
    make_material_target,
)
from claw_trade.guards.common import ApprovalResult
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall
from claw_trade.workflow.runner import persist_approved_material_after_hard_gate

from tests.fakes.openviking_store import FakeOpenVikingStore


def test_approve_worker_material_fails_when_receipt_passes_but_claim_guard_fails(tmp_path: Path) -> None:
    call = _make_call(tmp_path, worker_id="market_analyst", stage=Stage.FRONTLINE)
    l1_text = _l1_report()
    evidence = _make_evidence(call, raw_output="不同的 raw output")
    l2_index = _make_l2_index(call, evidence_id="l2-present")
    client, receipt = _seed_openviking_client(call=call, evidence=evidence, l1_text=l1_text, l2_index=l2_index)
    _write_material_claims_evidence(call=call, receipt=receipt, claim_evidence_id="l2-missing")

    result = approve_worker_material(call=call, evidence=evidence, openviking=client)

    assert not result.ok
    assert result.material is None
    assert result.category == "claim"
    assert result.reason is not None
    assert not (call.evidence_dir / "approval-hard-gate.json").exists()


def test_approve_worker_material_returns_approved_material_when_all_gates_pass(tmp_path: Path) -> None:
    call = _make_call(tmp_path, worker_id="market_analyst", stage=Stage.FRONTLINE)
    l1_text = _l1_report()
    evidence = _make_evidence(call, raw_output="和 L1 不同的 raw output")
    l2_index = _make_l2_index(call, evidence_id="l2-1")
    client, receipt = _seed_openviking_client(call=call, evidence=evidence, l1_text=l1_text, l2_index=l2_index)
    _write_material_claims_evidence(call=call, receipt=receipt, claim_evidence_id="l2-1")

    result = approve_worker_material(call=call, evidence=evidence, openviking=client)

    assert result.ok
    assert result.material is not None
    assert result.material.run_id == call.run_id
    assert result.material.call_id == call.call_id
    assert result.material.worker_id == call.worker_id
    assert result.material.l1_uri == call.material_target.l1_uri
    assert result.material.hard_gate_result_path == call.evidence_dir / "approval-hard-gate.json"


def test_approve_worker_material_accepts_us_portfolio_manager_natural_language_report(tmp_path: Path) -> None:
    call = _make_call(tmp_path, worker_id="portfolio_manager", stage=Stage.PORTFOLIO_DECISION)
    l1_text = _pm_l1_report()
    evidence = _make_evidence(call, raw_output="和 L1 不同")
    l2_index = _make_l2_index(call, evidence_id="l2-1")
    client, receipt = _seed_openviking_client(call=call, evidence=evidence, l1_text=l1_text, l2_index=l2_index)
    _write_material_claims_evidence(call=call, receipt=receipt, claim_evidence_id="l2-1")

    result = approve_worker_material(call=call, evidence=evidence, openviking=client)

    assert result.ok
    assert result.material is not None
    assert result.material.worker_id == "portfolio_manager"


def test_approve_worker_material_accepts_cn_a_portfolio_manager_natural_language_report(tmp_path: Path) -> None:
    call = _make_call(
        tmp_path,
        worker_id="portfolio_manager",
        stage=Stage.PORTFOLIO_DECISION,
        profile="CN_A",
    )
    l1_text = _pm_l1_report()
    evidence = _make_evidence(call, raw_output="和 L1 不同")
    l2_index = _make_l2_index(call, evidence_id="l2-1")
    client, receipt = _seed_openviking_client(call=call, evidence=evidence, l1_text=l1_text, l2_index=l2_index)
    _write_material_claims_evidence(call=call, receipt=receipt, claim_evidence_id="l2-1")

    result = approve_worker_material(call=call, evidence=evidence, openviking=client)

    assert result.ok
    assert result.material is not None
    assert result.material.worker_id == "portfolio_manager"


def test_runner_helper_writes_hard_gate_before_manifest_and_skips_failed_approval(tmp_path: Path) -> None:
    call = _make_call(tmp_path, worker_id="market_analyst", stage=Stage.FRONTLINE)
    l1_text = _l1_report()
    evidence = _make_evidence(call, raw_output="raw")
    l2_index = _make_l2_index(call, evidence_id="l2-1")
    client, receipt = _seed_openviking_client(call=call, evidence=evidence, l1_text=l1_text, l2_index=l2_index)
    _write_material_claims_evidence(call=call, receipt=receipt, claim_evidence_id="l2-1")
    approval = approve_worker_material(call=call, evidence=evidence, openviking=client)
    assert approval.ok and approval.material is not None

    store = _RecordingManifestStore(root_dir=tmp_path / "runs")
    hard_gate_path = persist_approved_material_after_hard_gate(call=call, approval=approval, manifest_store=store)
    assert hard_gate_path is not None
    assert hard_gate_path.exists()
    assert store.add_called is True
    assert store.add_seen_hard_gate_exists is True
    manifest_path = tmp_path / "runs" / call.run_id / "openviking" / "approved-manifest.json"
    assert manifest_path.exists()
    payload = json.loads(hard_gate_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["status"] == "passed"
    assert payload["category"] == "combined_hard_gate"

    failed = ApprovalResult.failed(category="claim", reason="claim failed", paths=(call.evidence_dir / "l1.md",))
    failed_call = _make_call(tmp_path, worker_id="news_analyst", stage=Stage.FRONTLINE, call_id="call-2")
    failed_store = _RecordingManifestStore(root_dir=tmp_path / "runs-failed")
    failed_path = persist_approved_material_after_hard_gate(
        call=failed_call,
        approval=failed,
        manifest_store=failed_store,
    )
    assert failed_path is None
    assert failed_store.add_called is False
    assert not (failed_call.evidence_dir / "approval-hard-gate.json").exists()
    assert not (tmp_path / "runs-failed" / failed_call.run_id / "openviking" / "approved-manifest.json").exists()


def _make_call(
    tmp_path: Path,
    *,
    worker_id: str,
    stage: Stage,
    call_id: str = "call-1",
    profile: str = "US",
) -> WorkerCall:
    target = make_material_target("run-1", stage, worker_id, call_id)
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / call_id / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return WorkerCall(
        call_id=call_id,
        run_id="run-1",
        worker_id=worker_id,
        stage=stage,
        profile=profile,
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


def _make_evidence(call: WorkerCall, *, raw_output: str) -> ProviderEvidence:
    _write_json(call.evidence_dir / "workspace-evidence.json")
    _write_json(call.evidence_dir / "provider-request.json")
    _write_json(call.evidence_dir / "visible-tools.json")
    _write_json(call.evidence_dir / "first-response.json")
    _write_json(call.evidence_dir / "tool-calls.json")
    raw_output_path = call.evidence_dir / "raw-output.txt"
    raw_output_path.write_text(raw_output, encoding="utf-8")
    receipt_path = call.evidence_dir / "openviking-receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")
    return ProviderEvidence(
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=call.evidence_dir / "workspace-evidence.json",
        provider_request_path=call.evidence_dir / "provider-request.json",
        visible_tools_path=call.evidence_dir / "visible-tools.json",
        first_response_path=call.evidence_dir / "first-response.json",
        tool_calls_status="completed",
        tool_calls_path=call.evidence_dir / "tool-calls.json",
        raw_output_path=raw_output_path,
        openviking_receipt_path=receipt_path,
    )


def _seed_openviking_client(
    *,
    call: WorkerCall,
    evidence: ProviderEvidence,
    l1_text: str,
    l2_index: L2Index,
) -> tuple[OpenVikingClient, MaterialReceipt]:
    l1_bytes = l1_text.encode("utf-8")
    receipt = _make_receipt(call, l1_bytes)
    store = FakeOpenVikingStore()
    store.seed_receipt(evidence.openviking_receipt_path, receipt)  # type: ignore[arg-type]
    store.seed_object(call.material_target.l1_uri, l1_bytes)
    index_uri = f"{call.material_target.l2_prefix}index.json"
    index_content = _l2_index_content(l2_index)
    store.seed_object(index_uri, index_content)
    store.seed_l2_index(index_uri, l2_index)
    for entry in l2_index.entries:
        content = entry.evidence_id.encode("utf-8")
        store.seed_object(entry.uri, content)
    return OpenVikingClient(_BackendAdapter(store)), receipt


def _make_receipt(call: WorkerCall, l1_content: bytes) -> MaterialReceipt:
    return MaterialReceipt(
        uri=call.material_target.l1_uri,
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage,
        target_name=call.material_target.target_name,
        sha256=hashlib.sha256(l1_content).hexdigest(),
        size_bytes=len(l1_content),
        written_at="2026-05-04T12:00:00Z",
        receipt_id="receipt-1",
        source="openviking_adapter_verified_receipt",
        receipt_label="verified_openviking_write_receipt",
        receipt_origin="adapter_verified_non_native",
        is_openviking_native_receipt=False,
        verification=MaterialReceiptVerification(
            verified=True,
            method="openviking_write_then_stat_then_readback_sha_size_identity_check",
        ),
    )


def _make_l2_index(call: WorkerCall, *, evidence_id: str) -> L2Index:
    entry_uri = f"{call.material_target.l2_prefix}{evidence_id}.json"
    entry_content = evidence_id.encode("utf-8")
    index_uri = f"{call.material_target.l2_prefix}index.json"
    index_content = _l2_index_content_from_ids((evidence_id,))
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
        index_uri=index_uri,
        index_sha256=hashlib.sha256(index_content).hexdigest(),
        index_size_bytes=len(index_content),
    )


def _l2_index_content(index: L2Index) -> bytes:
    return _l2_index_content_from_ids(tuple(entry.evidence_id for entry in index.entries))


def _l2_index_content_from_ids(evidence_ids: tuple[str, ...]) -> bytes:
    return json.dumps({"entries": [{"evidence_id": evidence_id} for evidence_id in evidence_ids]}, sort_keys=True).encode(
        "utf-8"
    )


def _l1_report() -> str:
    return "# 正式报告\n\n本报告面向读者展示，不在正文拼接机器 JSON。"


def _pm_l1_report() -> str:
    return (
        "# Portfolio Manager Final Decision\n\n"
        "**Rating**: Hold\n\n"
        "Here is my call: Hold. Maintain the current position and do not initiate a new trade today.\n\n"
        "**Execution conditions**: Reassess after the next earnings catalyst or a confirmed technical breakdown.\n\n"
        "**Risk conditions**: If revenue growth or margin evidence deteriorates, revisit the thesis."
    )


def _write_material_claims_evidence(
    *,
    call: WorkerCall,
    receipt: MaterialReceipt,
    claim_evidence_id: str,
) -> None:
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
                "claim_id": "claim-001",
                "kind": "valuation",
                "text": "目标价 100 元",
                "value": None,
                "evidence_ids": [claim_evidence_id],
                "source_worker_id": call.worker_id,
            }
        ],
    }
    (call.evidence_dir / "material-claims.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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


class _RecordingManifestStore:
    def __init__(self, root_dir: Path) -> None:
        self._store = ManifestStore(root_dir=root_dir)
        self.add_called = False
        self.add_seen_hard_gate_exists = False

    def add(self, run_id: str, material) -> None:  # type: ignore[no-untyped-def]
        self.add_called = True
        self.add_seen_hard_gate_exists = material.hard_gate_result_path.exists()
        self._store.add(run_id, material)
