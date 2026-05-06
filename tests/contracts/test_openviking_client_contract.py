from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

import pytest

from claw_trade.artifacts.openviking_client import (
    OPENVIKING_PROBE_NAMESPACE_URI,
    OPENVIKING_PROBE_RECEIPT_PATH,
    OPENVIKING_PROBE_STAT_URI,
    OpenVikingAccessError,
    OpenVikingClient,
)
from claw_trade.artifacts.refs import L2Entry, L2Index, MaterialReceipt, MaterialReceiptVerification, make_material_target
from claw_trade.workflow.models import Stage


@dataclass
class FakeBackend:
    content_by_uri: dict[str, bytes]
    stat_by_uri: dict[str, tuple[str | None, int]]
    receipt_by_path: dict[Path, MaterialReceipt]
    l2_index_by_uri: dict[str, L2Index]
    auto_prepare_probe_receipt: bool = False
    prepare_raises_busy: bool = False
    prepare_calls: int = 0

    def ensure_namespace(self, namespace: str) -> None:
        if namespace == "bad":
            raise RuntimeError("backend down")

    def fetch_receipt_by_path(self, receipt_path: Path) -> MaterialReceipt:
        return self.receipt_by_path[receipt_path]

    def fetch_stat_by_uri(self, uri: str):  # type: ignore[no-untyped-def]
        from claw_trade.artifacts.openviking_client import OpenVikingStat

        if uri == OPENVIKING_PROBE_NAMESPACE_URI:
            return OpenVikingStat(uri=uri, exists=True, is_dir=True, sha256=None, size_bytes=None)
        sha256, size_bytes = self.stat_by_uri[uri]
        return OpenVikingStat(uri=uri, sha256=sha256, size_bytes=size_bytes)

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return self.content_by_uri[uri]

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        if uri is None:
            return L2Index(entries=(), empty_reason="missing_l2_index_uri", index_uri=None, index_sha256=None, index_size_bytes=None)
        return self.l2_index_by_uri[uri]

    def prepare_probe_receipt(self, *, receipt_path: Path, stat_uri: str, read_uri: str | None = None) -> None:
        self.prepare_calls += 1
        if self.prepare_raises_busy:
            raise OpenVikingAccessError(
                "resource is busy and cannot be written now: viking://resources/workflow/probe/frontline/probe_worker/probe_call/report.md"
            )
        if not self.auto_prepare_probe_receipt:
            return
        self.receipt_by_path[receipt_path] = MaterialReceipt(
            uri=stat_uri,
            run_id="probe",
            call_id="probe_call",
            worker_id="probe_worker",
            stage=Stage.FRONTLINE,
            target_name="report",
            sha256=self.stat_by_uri[stat_uri][0],
            size_bytes=self.stat_by_uri[stat_uri][1],
            written_at="2026-05-05T00:00:00Z",
            receipt_id="probe-auto",
            source="openviking_adapter_verified_receipt",
            receipt_label="verified_openviking_write_receipt",
            receipt_origin="adapter_verified_non_native",
            is_openviking_native_receipt=False,
            verification=MaterialReceiptVerification(
                verified=True,
                method="openviking_write_then_stat_then_readback_sha_size_identity_check",
            ),
        )


def test_client_exposes_contract_methods_without_generic_read_methods() -> None:
    backend = fake_backend()
    client = OpenVikingClient(backend)

    assert hasattr(client, "ensure_namespace")
    assert hasattr(client, "probe_read_stat_receipt")
    assert hasattr(client, "read_receipt")
    assert hasattr(client, "stat_for_receipt_verification")
    assert hasattr(client, "read_for_receipt_verification")
    assert hasattr(client, "read_l2_index_for_receipt_verification")
    assert hasattr(client, "stat_with_capability")
    assert hasattr(client, "read_with_capability")
    assert hasattr(client, "read_l2_index_with_capability")
    assert not hasattr(client, "read")
    assert not hasattr(client, "read_text")
    assert not hasattr(client, "read_latest")
    assert not hasattr(client, "latest_material")
    assert not hasattr(client, "compact_read")


def test_probe_read_stat_receipt_checks_backend_capability() -> None:
    client = OpenVikingClient(fake_backend())
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


def test_probe_read_stat_receipt_passes_with_newline_terminated_probe_bytes() -> None:
    probe_content = b"# openviking probe report\n"
    backend = fake_backend(probe_content=probe_content)
    probe_receipt = backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH]
    assert probe_receipt.size_bytes == len(probe_content)
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


def test_probe_namespace_stat_does_not_require_probe_receipt() -> None:
    client = OpenVikingClient(fake_backend(include_probe_receipt=False))
    probe = client.probe_namespace_stat()
    assert probe.ok
    assert probe.reason is None


def test_probe_read_stat_receipt_can_prepare_receipt_when_backend_supports_it() -> None:
    backend = fake_backend(include_probe_receipt=False, auto_prepare_probe_receipt=True)
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None
    assert backend.prepare_calls == 1


def test_probe_read_stat_receipt_skips_prepare_when_existing_receipt_is_valid() -> None:
    backend = fake_backend(prepare_raises_busy=True)
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None
    assert backend.prepare_calls == 0


def test_probe_read_stat_receipt_skips_prepare_when_receipt_sha_is_only_audit_field() -> None:
    backend = fake_backend(prepare_raises_busy=True)
    original = backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH]
    backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH] = replace(original, sha256="f" * 64)
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None
    assert backend.prepare_calls == 0


def test_probe_read_stat_receipt_fails_when_backend_method_raises() -> None:
    class BadBackend(FakeBackend):
        def fetch_stat_by_uri(self, uri: str):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"stat unavailable: {uri}")

    client = OpenVikingClient(BadBackend(**fake_backend().__dict__))
    probe = client.probe_read_stat_receipt()
    assert not probe.ok
    assert "stat probe failed" in (probe.reason or "")


def test_probe_read_stat_receipt_blocks_when_receipt_uri_is_tampered() -> None:
    backend = fake_backend()
    original = backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH]
    backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH] = replace(
        original,
        uri="viking://resources/workflow/probe/frontline/probe_worker/probe_call/wrong.md",
    )
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert not probe.ok
    assert "receipt.uri" in (probe.reason or "")


@pytest.mark.parametrize("field", ["sha256", "size_bytes"])
def test_probe_read_stat_receipt_allows_receipt_integrity_mismatch_for_audit_only(field: str) -> None:
    backend = fake_backend()
    original = backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH]
    if field == "sha256":
        backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH] = replace(original, sha256="f" * 64)
    else:
        backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH] = replace(original, size_bytes=original.size_bytes + 1)
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


def test_probe_read_stat_receipt_allows_read_content_mismatch_with_receipt_hash() -> None:
    backend = fake_backend()
    backend.content_by_uri[OPENVIKING_PROBE_STAT_URI] = b"tampered-probe-content"
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


@pytest.mark.parametrize("verification_key", ["expected_write_size_bytes", "stat_size_bytes"])
def test_probe_read_stat_receipt_passes_when_stat_sha_missing_but_size_matches_verification(
    verification_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    backend = fake_backend()
    probe_receipt = backend.receipt_by_path.pop(OPENVIKING_PROBE_RECEIPT_PATH)
    patched_receipt_path = tmp_path / "probe-receipt.json"
    backend.receipt_by_path[patched_receipt_path] = probe_receipt
    monkeypatch.setattr("claw_trade.artifacts.openviking_client.OPENVIKING_PROBE_RECEIPT_PATH", patched_receipt_path)

    stat_size = probe_receipt.size_bytes + 1
    backend.stat_by_uri[OPENVIKING_PROBE_STAT_URI] = (None, stat_size)
    patched_receipt_path.write_text(
        json.dumps({"verification": {verification_key: stat_size}}),
        encoding="utf-8",
    )

    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


def test_probe_read_stat_receipt_allows_stat_sha_mismatch_with_receipt_hash() -> None:
    backend = fake_backend()
    probe_receipt = backend.receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH]
    backend.stat_by_uri[OPENVIKING_PROBE_STAT_URI] = ("f" * 64, probe_receipt.size_bytes)
    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


def test_probe_read_stat_receipt_allows_stat_size_mismatch_with_receipt_for_audit_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    backend = fake_backend()
    probe_receipt = backend.receipt_by_path.pop(OPENVIKING_PROBE_RECEIPT_PATH)
    patched_receipt_path = tmp_path / "probe-receipt.json"
    backend.receipt_by_path[patched_receipt_path] = probe_receipt
    monkeypatch.setattr("claw_trade.artifacts.openviking_client.OPENVIKING_PROBE_RECEIPT_PATH", patched_receipt_path)

    patched_receipt_path.write_text(
        json.dumps({"verification": {"expected_write_size_bytes": probe_receipt.size_bytes + 1, "stat_size_bytes": probe_receipt.size_bytes + 2}}),
        encoding="utf-8",
    )
    backend.stat_by_uri[OPENVIKING_PROBE_STAT_URI] = (None, probe_receipt.size_bytes + 3)

    client = OpenVikingClient(backend)
    probe = client.probe_read_stat_receipt()
    assert probe.ok
    assert probe.reason is None


@pytest.mark.parametrize("field", ["sha256", "size_bytes"])
def test_receipt_verification_stat_allows_hash_and_size_mismatch(field: str) -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    receipt = MaterialReceipt(
        uri=target.l1_uri,
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        stage=target.stage,
        target_name=target.target_name,
        sha256="sha-good",
        size_bytes=4,
        written_at="2026-05-04T00:00:00Z",
        receipt_id="r-1",
    )
    backend = fake_backend(l1_content=b"good", l1_sha="sha-good", l1_size=4, receipt=receipt)
    client = OpenVikingClient(backend)
    if field == "sha256":
        backend.stat_by_uri[target.l1_uri] = ("sha-other", 4)
    else:
        backend.stat_by_uri[target.l1_uri] = ("sha-good", 5)
    stat = client.stat_for_receipt_verification(receipt, target)
    assert stat.ok
    assert stat.error_category is None


def test_read_with_capability_rejects_uri_out_of_scope() -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    backend = fake_backend()
    client = OpenVikingClient(backend)
    capability = frontend_capability(target)
    result = client.read_with_capability(
        capability,
        "viking://resources/workflow/run-1/frontline/market_analyst/call-2/report.md",
        expected_sha256=capability.allowed_l1_sha256,
    )
    assert not result.ok
    assert result.error_category == "capability_mismatch"


def test_read_with_capability_allows_expected_sha_mismatch_for_audit_only() -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    backend = fake_backend(l1_content=b"real-content")
    client = OpenVikingClient(backend)
    capability = frontend_capability(target, l1_sha="expected-sha")
    result = client.read_with_capability(
        capability,
        target.l1_uri,
        expected_sha256="expected-sha",
    )
    assert result.ok
    assert result.error_category is None


def test_read_l2_index_with_capability_reads_fixed_index_uri() -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    capability = frontend_capability(target)
    index_uri = f"{target.l2_prefix}index.json"
    backend = fake_backend()
    backend.l2_index_by_uri[index_uri] = L2Index(
        entries=(
            L2Entry(
                evidence_id="e1",
                uri=f"{target.l2_prefix}e1.json",
                kind="source",
                source="api",
                sha256="sha-e1",
                size_bytes=8,
            ),
        ),
        empty_reason=None,
        index_uri=index_uri,
        index_sha256="sha-index",
        index_size_bytes=16,
    )
    client = OpenVikingClient(backend)
    index = client.read_l2_index_with_capability(capability)
    assert index.index_uri == index_uri


def frontend_capability(target, l1_sha: str = "sha-l1"):  # type: ignore[no-untyped-def]
    from claw_trade.artifacts.refs import OpenVikingReadCapability

    return OpenVikingReadCapability(
        capability_id="cap-1",
        material_id="mat-1",
        allowed_l1_uri=target.l1_uri,
        allowed_l1_sha256=l1_sha,
        allowed_l2_prefix=target.l2_prefix,
        manifest_entry_sha256="entry-sha",
    )


def fake_backend(
    *,
    l1_content: bytes = b"good",
    l1_sha: str = "sha-good",
    l1_size: int = 4,
    receipt: MaterialReceipt | None = None,
    include_probe_receipt: bool = True,
    auto_prepare_probe_receipt: bool = False,
    prepare_raises_busy: bool = False,
    probe_content: bytes = b"openviking-probe-report",
) -> FakeBackend:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    index_uri = f"{target.l2_prefix}index.json"
    probe_sha = hashlib.sha256(probe_content).hexdigest()
    probe_size = len(probe_content)
    probe_receipt = MaterialReceipt(
        uri=OPENVIKING_PROBE_STAT_URI,
        run_id="probe",
        call_id="probe_call",
        worker_id="probe_worker",
        stage=Stage.FRONTLINE,
        target_name="report",
        sha256=probe_sha,
        size_bytes=probe_size,
        written_at="2026-05-04T00:00:00Z",
        receipt_id="probe-receipt",
        source="openviking_adapter_verified_receipt",
        receipt_label="verified_openviking_write_receipt",
        receipt_origin="adapter_verified_non_native",
        is_openviking_native_receipt=False,
        verification=MaterialReceiptVerification(
            verified=True,
            method="openviking_write_then_stat_then_readback_sha_size_identity_check",
        ),
    )
    receipt_by_path: dict[Path, MaterialReceipt] = {}
    if include_probe_receipt:
        receipt_by_path[OPENVIKING_PROBE_RECEIPT_PATH] = probe_receipt
    if receipt is not None:
        receipt_by_path[Path("runs/run-1/openviking/receipts/r-1.json")] = receipt

    return FakeBackend(
        content_by_uri={
            target.l1_uri: l1_content,
            OPENVIKING_PROBE_STAT_URI: probe_content,
        },
        stat_by_uri={
            target.l1_uri: (l1_sha, l1_size),
            OPENVIKING_PROBE_STAT_URI: (probe_sha, probe_size),
        },
        receipt_by_path=receipt_by_path,
        l2_index_by_uri={
            index_uri: L2Index(
                entries=(),
                empty_reason="no_evidence",
                index_uri=index_uri,
                index_sha256="sha-index",
                index_size_bytes=8,
            )
        },
        auto_prepare_probe_receipt=auto_prepare_probe_receipt,
        prepare_raises_busy=prepare_raises_busy,
    )
