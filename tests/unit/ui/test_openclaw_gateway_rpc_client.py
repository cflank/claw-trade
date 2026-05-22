from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient


def test_config_patch_uses_gateway_raw_patch_and_current_base_hash(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        method = command[3]
        params = json.loads(command[command.index("--params") + 1])
        calls.append({"method": method, "params": params})
        if method == "config.get":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"hash": "hash-1"}}), stderr="")
        if method == "config.patch":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"newHash": "hash-2"}}), stderr="")
        raise AssertionError(method)

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    result = client.config_patch(patch={"channels": {"openclaw-weixin": {"enabled": True}}})

    assert result["newHash"] == "hash-2"
    patch_call = calls[1]
    assert patch_call["method"] == "config.patch"
    params = patch_call["params"]
    assert isinstance(params, dict)
    assert "patch" not in params
    assert params["baseHash"] == "hash-1"
    assert json.loads(str(params["raw"])) == {"channels": {"openclaw-weixin": {"enabled": True}}}


def test_config_get_uses_current_gateway_shape_without_paths_param(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        method = command[3]
        params = json.loads(command[command.index("--params") + 1])
        calls.append({"method": method, "params": params})
        assert method == "config.get"
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": {"hash": "hash-1", "parsed": {"agents": {"defaults": {}}}}}),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    result = client.config_get(paths=("agents.defaults.model", "models.providers"))

    assert result["revision"] == "hash-1"
    assert calls == [{"method": "config.get", "params": {}}]


def test_plugins_list_uses_local_openclaw_cli_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env)
        calls.append({"command": command, "timeout": timeout})
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "plugins": [
                        {
                            "id": "openclaw-weixin",
                            "enabled": True,
                            "channels": ["openclaw-weixin"],
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    result = client.plugins_list()

    assert calls == [{"command": ["openclaw", "plugins", "list", "--json"], "timeout": 15.0}]
    assert result == [{"id": "openclaw-weixin", "enabled": True, "channels": ["openclaw-weixin"]}]


def test_web_login_wait_forwards_qr_session_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env)
        method = command[3]
        params = json.loads(command[command.index("--params") + 1])
        calls.append({"method": method, "params": params, "timeout": timeout})
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"connected": False}}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    client.web_login_wait(
        timeout_ms=1500,
        current_qr_data_url="data:image/png;base64,current",
        session_key="login-session-1",
    )

    assert calls == [
        {
            "method": "web.login.wait",
            "params": {
                "timeoutMs": 1500,
                "currentQrDataUrl": "data:image/png;base64,current",
                "sessionKey": "login-session-1",
            },
            "timeout": 50.0,
        }
    ]


def test_gateway_call_has_subprocess_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env)
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    with pytest.raises(RuntimeError, match="gateway cli timeout after 8000ms"):
        client.config_get(paths=("models.providers",))
