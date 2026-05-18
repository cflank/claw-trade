from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient, OpenVikingStat
from claw_trade.artifacts.refs import L2Index
from claw_trade.data_gateway.openviking import OpenVikingMaterialPlane


@dataclass
class _BackendBase:
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

    def runtime_metrics(self) -> dict[str, str]:
        return {"status": "ok", "raw_ref": "file:///tmp/metrics.txt"}

    def runtime_observer(self) -> dict[str, str]:
        return {"status": "ok", "raw_ref": "file:///tmp/observer.json"}

    def runtime_locks(self) -> dict[str, str]:
        return {"status": "ok", "raw_ref": "file:///tmp/locks.json"}

    def runtime_semantic_queue(self) -> dict[str, str]:
        return {"status": "ok", "raw_ref": "file:///tmp/queue.json"}


@dataclass
class _BackendObserverBlocked(_BackendBase):
    def runtime_observer(self) -> dict[str, str]:
        return {"status": "blocked", "reason": "observer queue backlog"}


def test_runtime_health_marks_recovery_unavailable_when_public_api_missing() -> None:
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=_BackendBase()))

    health = plane.runtime_health()

    assert health.metrics_status == "ok"
    assert health.observer_status == "ok"
    assert health.lock_status == "ok"
    assert health.queue_status == "ok"
    assert health.recovery_status == "unavailable"
    assert health.status == "unavailable"
    assert "public recovery API" in (health.root_cause or "")


def test_runtime_health_is_blocked_when_observer_is_blocked() -> None:
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=_BackendObserverBlocked()))

    health = plane.runtime_health()

    assert health.observer_status == "blocked"
    assert health.status == "blocked"
