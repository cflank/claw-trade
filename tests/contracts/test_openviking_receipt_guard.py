from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import MaterialReceipt, MaterialReceiptVerification, make_material_target
from claw_trade.guards.openviking_receipt import validate_openviking_receipt
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall
from tests.fakes.openviking_store import FakeOpenVikingStore


def test_openviking_receipt_guard_passes_with_verified_receipt(tmp_path: Path) -> None:
    call = make_call(tmp_path=tmp_path)
    receipt = make_receipt(call.material_target, content=b"hello")
    store = FakeOpenVikingStore()
    store.seed_object(call.material_target.l1_uri, b"hello")
    client = OpenVikingClient(FakeBackendAdapter(store))
    result = validate_openviking_receipt(call=call, receipt=receipt, client=client)
    assert result.ok
    assert result.category == "openviking_receipt"


def test_openviking_receipt_guard_passes_with_downloadback_method(tmp_path: Path) -> None:
    call = make_call(tmp_path=tmp_path)
    receipt = make_receipt(call.material_target, content=b"hello")
    receipt = replace(
        receipt,
        verification=MaterialReceiptVerification(
            verified=True,
            method="openviking_write_then_stat_then_downloadback_sha_size_identity_check",
        ),
    )
    store = FakeOpenVikingStore()
    store.seed_object(call.material_target.l1_uri, b"hello")
    client = OpenVikingClient(FakeBackendAdapter(store))
    result = validate_openviking_receipt(call=call, receipt=receipt, client=client)
    assert result.ok
    assert result.category == "openviking_receipt"


def test_openviking_receipt_guard_rejects_material_target_mismatch(tmp_path: Path) -> None:
    call = make_call(tmp_path=tmp_path)
    receipt = make_receipt(call.material_target, content=b"hello")
    bad_receipt = replace(receipt, worker_id="news_analyst")
    store = FakeOpenVikingStore()
    client = OpenVikingClient(FakeBackendAdapter(store))
    result = validate_openviking_receipt(call=call, receipt=bad_receipt, client=client)
    assert not result.ok
    assert result.category == "openviking_receipt"
    assert result.reason is not None
    assert "receipt.worker_id 与 material_target 不一致" in result.reason


def test_openviking_receipt_guard_rejects_without_openviking_stat_read_evidence(tmp_path: Path) -> None:
    call = make_call(tmp_path=tmp_path)
    receipt = make_receipt(call.material_target, content=b"hello")
    store = FakeOpenVikingStore()
    client = OpenVikingClient(FakeBackendAdapter(store))
    result = validate_openviking_receipt(call=call, receipt=receipt, client=client)
    assert not result.ok
    assert result.category == "openviking_receipt"
    assert result.reason is not None
    assert "receipt stat 复核失败" in result.reason


def test_openviking_receipt_guard_rejects_missing_adapter_verified_fields(tmp_path: Path) -> None:
    call = make_call(tmp_path=tmp_path)
    receipt = make_receipt(call.material_target, content=b"hello")
    store = FakeOpenVikingStore()
    store.seed_object(call.material_target.l1_uri, b"hello")
    client = OpenVikingClient(FakeBackendAdapter(store))

    bad_receipts = (
        replace(receipt, source=None),
        replace(receipt, source="openviking_native_receipt"),
        replace(receipt, receipt_label=None),
        replace(receipt, receipt_label="wrong_label"),
        replace(receipt, receipt_origin=None),
        replace(receipt, receipt_origin="openviking_native"),
        replace(receipt, is_openviking_native_receipt=None),
        replace(receipt, is_openviking_native_receipt=True),
        replace(receipt, verification=None),
        replace(receipt, verification=MaterialReceiptVerification(verified=False, method=receipt.verification.method)),  # type: ignore[union-attr]
        replace(receipt, verification=MaterialReceiptVerification(verified=True, method="wrong_method")),
    )
    for bad_receipt in bad_receipts:
        result = validate_openviking_receipt(call=call, receipt=bad_receipt, client=client)
        assert not result.ok
        assert result.category == "openviking_receipt"
        assert result.reason is not None
        assert "receipt." in result.reason


def test_openviking_receipt_guard_allows_read_hash_mismatch_for_audit_only(tmp_path: Path) -> None:
    call = make_call(tmp_path=tmp_path)
    receipt = make_receipt(call.material_target, content=b"hello")
    store = FakeOpenVikingStore()
    store.seed_object(call.material_target.l1_uri, b"HELLO")
    store.seed_stat(call.material_target.l1_uri, size_bytes=5, sha256=receipt.sha256)
    client = OpenVikingClient(FakeBackendAdapter(store))
    result = validate_openviking_receipt(call=call, receipt=receipt, client=client)
    assert result.ok
    assert result.category == "openviking_receipt"


def make_receipt(target, content: bytes) -> MaterialReceipt:  # type: ignore[no-untyped-def]
    digest = hashlib.sha256(content).hexdigest()
    return MaterialReceipt(
        uri=target.l1_uri,
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        stage=target.stage,
        target_name=target.target_name,
        sha256=digest,
        size_bytes=len(content),
        written_at="2026-05-03T10:00:00Z",
        receipt_id="r-1",
        source="openviking_adapter_verified_receipt",
        receipt_label="verified_openviking_write_receipt",
        receipt_origin="adapter_verified_non_native",
        is_openviking_native_receipt=False,
        verification=MaterialReceiptVerification(
            verified=True,
            method="openviking_write_then_stat_then_readback_sha_size_identity_check",
        ),
    )


def make_call(tmp_path: Path) -> WorkerCall:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
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
        evidence_dir=tmp_path / "runs/run-1/calls/call-1/evidence",
        stop_after_first_response=False,
    )


class FakeBackendAdapter:
    def __init__(self, store: FakeOpenVikingStore) -> None:
        self._store = store

    def ensure_namespace(self, namespace: str) -> None:
        return None

    def fetch_receipt_by_path(self, receipt_path):  # type: ignore[no-untyped-def]
        return self._store.read_receipt(receipt_path)

    def fetch_stat_by_uri(self, uri: str):  # type: ignore[no-untyped-def]
        return self._store.stat(uri)

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return self._store.read(uri)

    def fetch_l2_index_by_uri(self, uri: str | None):
        return self._store.read_l2_index(uri)
