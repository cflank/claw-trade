from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claw_trade.web.openclaw_gateway import _GatewayRpcHelperProcess


def test_gateway_rpc_helper_uses_configured_openclaw_node_bin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_bin = "/opt/claw-trade/current/runtime/node"
    helper_script = tmp_path / "openclaw-gateway-rpc-helper.mjs"
    helper_script.write_text("process.stdin.resume();\n", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_NODE_BIN", node_bin)
    calls: list[list[str]] = []

    class _FakeProcess:
        stdin = None
        stdout = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

    def _fake_popen(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append(list(cmd))
        return _FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    helper = _GatewayRpcHelperProcess(
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=10_000,
        token=None,
        password=None,
        helper_script=helper_script,
    )
    helper.start()

    assert calls
    assert calls[0][0] == node_bin
