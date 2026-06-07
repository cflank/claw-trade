from pathlib import Path

import pytest
from claw_trade.artifacts.openviking_client import (
    OPENVIKING_PROBE_RECEIPT_PATH,
    OPENVIKING_PROBE_STAT_URI,
    OpenVikingClient,
)
from claw_trade.artifacts.refs import L2Index, MaterialReceipt
from claw_trade.config.openviking_config import (
    load_openviking_config,
    probe_openviking_contract,
)
from claw_trade.workflow.models import Stage


def test_openviking_config_requires_endpoint_and_workspace(monkeypatch):
    monkeypatch.delenv("OPENVIKING_ENDPOINT", raising=False)
    monkeypatch.delenv("OPENVIKING_WORKSPACE", raising=False)
    result = load_openviking_config()
    assert result.ok is False
    assert "ENDPOINT" in (result.reason or "")

    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    monkeypatch.delenv("OPENVIKING_WORKSPACE", raising=False)
    result = load_openviking_config()
    assert result.ok is False
    assert "WORKSPACE" in (result.reason or "")


def test_openviking_config_accepts_minimum_required_fields(monkeypatch):
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    monkeypatch.setenv("OPENVIKING_WORKSPACE", "workflow")
    monkeypatch.delenv("OPENVIKING_LONG_TERM_MEMORY_CAPABILITY", raising=False)
    result = load_openviking_config()

    assert result.ok is True
    assert result.config is not None
    assert result.config.endpoint == "http://127.0.0.1:1933"
    assert result.config.workspace == "workflow"
    assert result.config.long_term_memory_capability is None


@pytest.mark.parametrize(
    "client",
    [
        type(
            "MissingRead",
            (),
            {
                "stat": lambda self, uri: {"uri": uri},
                "read_receipt": lambda self, receipt_path: {"receipt": str(receipt_path)},
            },
        )(),
        type(
            "MissingStat",
            (),
            {
                "read": lambda self, uri: b"ok",
                "read_receipt": lambda self, receipt_path: {"receipt": str(receipt_path)},
            },
        )(),
        type(
            "MissingReceipt",
            (),
            {
                "read": lambda self, uri: b"ok",
                "stat": lambda self, uri: {"uri": uri},
            },
        )(),
    ],
)
def test_probe_openviking_contract_blocks_when_any_method_missing(client) -> None:
    boot = probe_openviking_contract(client)
    assert boot.ok is False
    assert boot.category == "openviking_contract"


def test_probe_openviking_contract_blocks_when_real_probe_methods_raise() -> None:
    class RaiseAll:
        def read(self, uri: str) -> bytes:
            raise RuntimeError(f"read unavailable: {uri}")

        def stat(self, uri: str) -> object:
            raise RuntimeError(f"stat unavailable: {uri}")

        def read_receipt(self, receipt_path: Path) -> object:
            raise RuntimeError(f"receipt unavailable: {receipt_path}")

    boot = probe_openviking_contract(RaiseAll())
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "read" in (boot.reason or "")


def test_probe_openviking_contract_blocks_when_unified_probe_returns_failed() -> None:
    class UnifiedProbeFailed:
        def probe_read_stat_receipt(self):
            class Result:
                ok = False
                reason = "backend_unavailable"

            return Result()

        def read(self, uri: str) -> bytes:
            raise RuntimeError("should not be called")

        def stat(self, uri: str) -> object:
            raise RuntimeError("should not be called")

        def read_receipt(self, receipt_path: Path) -> object:
            raise RuntimeError("should not be called")

    boot = probe_openviking_contract(UnifiedProbeFailed())
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "backend_unavailable" in (boot.reason or "")


def test_probe_openviking_contract_blocks_when_unified_probe_raises() -> None:
    class UnifiedProbeRaises:
        def probe_read_stat_receipt(self):
            raise RuntimeError("probe crashed")

        def read(self, uri: str) -> bytes:
            raise RuntimeError("should not be called")

        def stat(self, uri: str) -> object:
            raise RuntimeError("should not be called")

        def read_receipt(self, receipt_path: Path) -> object:
            raise RuntimeError("should not be called")

    boot = probe_openviking_contract(UnifiedProbeRaises())
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "probe_read_stat_receipt" in (boot.reason or "")


def test_probe_openviking_contract_blocks_when_unified_probe_returns_mapping_without_ok() -> None:
    class UnifiedProbeMissingOk:
        def probe_read_stat_receipt(self):
            return {"status": "pass"}

    boot = probe_openviking_contract(UnifiedProbeMissingOk())
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "missing bool ok" in (boot.reason or "")


def test_probe_openviking_contract_blocks_when_unified_probe_returns_object_without_ok() -> None:
    class UnifiedProbeUnknownObject:
        def probe_read_stat_receipt(self):
            class Result:
                reason = "not-structured"

            return Result()

    boot = probe_openviking_contract(UnifiedProbeUnknownObject())
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "missing bool ok" in (boot.reason or "")


def test_probe_openviking_contract_blocks_when_unified_probe_returns_non_bool_ok() -> None:
    class UnifiedProbeNonBool:
        def probe_read_stat_receipt(self):
            return {"ok": "yes"}

    boot = probe_openviking_contract(UnifiedProbeNonBool())
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "not bool" in (boot.reason or "")


def test_probe_openviking_contract_prefers_unified_probe_when_available() -> None:
    class UnifiedProbePassed:
        calls = 0

        def probe_read_stat_receipt(self):
            self.calls += 1

            class Result:
                ok = True
                reason = None

            return Result()

        def read(self, uri: str) -> bytes:
            raise RuntimeError("fallback path should not run")

        def stat(self, uri: str) -> object:
            raise RuntimeError("fallback path should not run")

        def read_receipt(self, receipt_path: Path) -> object:
            raise RuntimeError("fallback path should not run")

    client = UnifiedProbePassed()
    boot = probe_openviking_contract(client)
    assert boot.ok is True
    assert client.calls == 1


def test_probe_openviking_contract_passes_only_when_real_read_stat_receipt_probe_calls_succeed() -> None:
    class FullClient:
        read_calls = 0
        stat_calls = 0
        receipt_calls = 0

        def read(self, uri: str) -> bytes:
            self.read_calls += 1
            return b"ok"

        def stat(self, uri: str) -> object:
            self.stat_calls += 1
            return {"uri": uri}

        def read_receipt(self, receipt_path: Path) -> object:
            self.receipt_calls += 1
            return {"receipt": str(receipt_path)}

    client = FullClient()
    boot = probe_openviking_contract(client)
    assert boot.ok is True
    assert client.read_calls == 1
    assert client.stat_calls == 1
    assert client.receipt_calls == 1


def test_probe_openviking_contract_blocks_when_openviking_client_probe_hits_bad_backend() -> None:
    class BadBackend:
        def ensure_namespace(self, namespace: str) -> None:
            return None

        def fetch_receipt_by_path(self, receipt_path: Path) -> MaterialReceipt:
            return MaterialReceipt(
                uri=OPENVIKING_PROBE_STAT_URI,
                run_id="probe",
                call_id="probe_call",
                worker_id="probe_worker",
                stage=Stage.FRONTLINE,
                target_name="report",
                sha256="probe-sha",
                size_bytes=10,
                written_at="2026-05-04T00:00:00Z",
                receipt_id="probe-receipt",
            )

        def fetch_stat_by_uri(self, uri: str) -> object:
            raise RuntimeError(f"stat unavailable: {uri}")

        def fetch_content_by_uri(self, uri: str) -> bytes:
            raise RuntimeError(f"read unavailable: {uri}")

        def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
            return L2Index(entries=(), empty_reason="no_evidence", index_uri=uri, index_sha256="probe-index", index_size_bytes=1)

    boot = probe_openviking_contract(OpenVikingClient(BadBackend()))
    assert boot.ok is False
    assert boot.category == "openviking_contract"
    assert "stat probe failed" in (boot.reason or "")
