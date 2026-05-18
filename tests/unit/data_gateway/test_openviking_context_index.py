from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient, OpenVikingStat
from claw_trade.artifacts.refs import L2Index
from claw_trade.data_gateway.openviking import OpenVikingMaterialPlane


@dataclass
class _BackendNoSemantic:
    def ensure_namespace(self, namespace: str) -> None:
        return None

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return b"ok"

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        return L2Index(entries=(), empty_reason=None, index_uri=uri, index_sha256=None, index_size_bytes=None)

    def fetch_stat_by_uri(self, uri: str) -> OpenVikingStat:
        return OpenVikingStat(uri=uri, ok=True, sha256="0" * 64, size_bytes=2, exists=True, is_dir=False)

    def fetch_receipt_by_path(self, receipt_path: Path):  # pragma: no cover - not used
        raise FileNotFoundError(receipt_path)


@dataclass
class _BackendSemanticBlocked(_BackendNoSemantic):
    def semantic_index_status(self, uri: str) -> dict[str, str]:
        del uri
        return {"status": "blocked", "reason": "semantic queue disabled"}


@dataclass
class _BackendSemanticOk(_BackendNoSemantic):
    def semantic_index_status(self, uri: str) -> dict[str, str]:
        del uri
        return {"status": "ok", "reason": "semantic queue reported ok"}


def test_index_run_context_marks_semantic_unavailable_when_wrapper_not_implemented() -> None:
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=_BackendNoSemantic()))

    receipts = plane.index_run_context("run-1")

    assert len(receipts) == 3
    assert receipts[0].index_level == "L0"
    assert receipts[0].status == "ok"
    assert receipts[1].index_level == "L1"
    assert receipts[1].status == "ok"
    assert receipts[2].index_level == "semantic"
    assert receipts[2].status == "unavailable"
    assert receipts[2].searchable_by_control_plane is False
    assert receipts[2].visible_to_worker is False


def test_index_run_context_keeps_semantic_blocked_when_queue_disabled() -> None:
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=_BackendSemanticBlocked()))

    receipts = plane.index_run_context("run-2")
    semantic = receipts[2]

    assert semantic.index_level == "semantic"
    assert semantic.status == "blocked"
    assert "semantic" in (semantic.reason or "")


def test_index_run_context_never_marks_semantic_ok_when_runtime_queue_is_disabled() -> None:
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=_BackendSemanticOk()))

    semantic = plane.index_run_context("run-3")[2]

    assert semantic.index_level == "semantic"
    assert semantic.status == "blocked"
    assert semantic.vectorized is False
    assert semantic.visible_to_worker is False
