from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient


class _FakeGatewayHelper:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)
        self.requests: list[dict[str, object]] = []
        self.on_call = None

    def start(self) -> None:
        return None

    def call(
        self,
        method: str,
        params: dict[str, object] | None,
        *,
        expect_final: bool,
        timeout_ms: int | None,
        scopes: list[str] | None = None,
    ) -> dict[str, object]:
        self.requests.append(
            {
                "method": method,
                "params": dict(params or {}),
                "expect_final": expect_final,
                "timeout_ms": timeout_ms,
                "scopes": list(scopes) if scopes is not None else None,
            }
        )
        if self.on_call is not None:
            self.on_call(method, dict(params or {}))
        return {"sent": True}


def _capture_gateway_helpers(monkeypatch: pytest.MonkeyPatch) -> list[_FakeGatewayHelper]:
    helpers: list[_FakeGatewayHelper] = []

    def fake_helper(**kwargs: object) -> _FakeGatewayHelper:
        helper = _FakeGatewayHelper(**kwargs)
        helpers.append(helper)
        return helper

    monkeypatch.setattr("claw_trade.web.openclaw_gateway._GatewayRpcHelperProcess", fake_helper)
    return helpers


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


def test_config_patch_resolves_base_hash_for_opaque_ui_version(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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

    result = client.config_patch(expected_settings_version="v_opaque", patch={"models": {"providers": {}}})

    assert result["newHash"] == "hash-2"
    assert [call["method"] for call in calls] == ["config.get", "config.patch"]
    assert calls[1]["params"]["baseHash"] == "hash-1"


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


def test_default_local_gateway_with_token_does_not_pass_url_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    commands: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"status": "ok"}}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token="local-token",
        password=None,
    )

    assert client.cron_status(job_id="job-1") == {"status": "ok"}
    assert "--url" not in commands[0]
    assert commands[0][commands[0].index("--token") + 1] == "local-token"


def test_cron_add_does_not_unwrap_job_payload_field(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (command, capture_output, text, check, env, timeout)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "id": "job-1",
                    "payload": {"kind": "agentTurn", "message": "wake"},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token="local-token",
        password=None,
    )

    result = client.cron_add({"name": "job"})

    assert result == {"id": "job-1", "payload": {"kind": "agentTurn", "message": "wake"}}


def test_cron_methods_use_current_gateway_param_shapes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        method = command[3]
        params = json.loads(command[command.index("--params") + 1])
        calls.append({"method": method, "params": params})
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"ok": True}}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token="local-token",
        password=None,
    )

    client.cron_update({"jobId": "job-1", "enabled": False})
    client.cron_remove(job_id="job-1")
    client.cron_run(job_id="job-1", idempotency_key="ignored-by-openclaw-cron")
    client.cron_status(job_id="job-1")
    client.cron_runs(job_id="job-1", limit=3)

    assert calls == [
        {"method": "cron.update", "params": {"id": "job-1", "patch": {"enabled": False}}},
        {"method": "cron.remove", "params": {"id": "job-1"}},
        {"method": "cron.run", "params": {"id": "job-1", "mode": "force"}},
        {"method": "cron.status", "params": {}},
        {"method": "cron.runs", "params": {"id": "job-1", "limit": 3}},
    ]


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


def test_weixin_replacement_rpc_methods_use_frozen_contract(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )
    calls: list[tuple[str, dict[str, object], int | None]] = []

    def fake_call(method, params=None, *, expect_final=False, timeout_ms=None):  # type: ignore[no-untyped-def]
        _ = expect_final
        calls.append((method, dict(params or {}), timeout_ms))
        return {"ok": True}

    monkeypatch.setattr(client, "_call", fake_call)

    client.weixin_replacement_begin(operation_id="op-1", old_account_ids=["old-1"])
    client.weixin_replacement_login_start(operation_id="op-1")
    client.weixin_replacement_login_wait(operation_id="op-1", session_key="session-1", timeout_ms=1500)
    client.weixin_login_cancel(operation_id="op-1", session_key="session-1")
    client.weixin_replacement_commit(
        operation_id="op-1",
        candidate_account_id="candidate-1",
        login_receipt_id="receipt-1",
    )
    client.weixin_replacement_restore(operation_id="op-1")
    client.weixin_replacement_inspect(operation_id="op-1")

    assert calls == [
        ("weixin.replacement.begin", {"operationId": "op-1", "oldAccountIds": ["old-1"]}, 15_000),
        ("weixin.replacement.login.start", {"operationId": "op-1"}, 15_000),
        (
            "weixin.replacement.login.wait",
            {"operationId": "op-1", "sessionKey": "session-1", "timeoutMs": 1500},
            4_500,
        ),
        ("weixin.login.cancel", {"operationId": "op-1", "sessionKey": "session-1"}, 15_000),
        (
            "weixin.replacement.commit",
            {
                "operationId": "op-1",
                "candidateAccountId": "candidate-1",
                "loginReceiptId": "receipt-1",
            },
            30_000,
        ),
        ("weixin.replacement.restore", {"operationId": "op-1"}, 30_000),
        ("weixin.replacement.inspect", {"operationId": "op-1"}, 10_000),
    ]


def test_fresh_channel_status_bypasses_python_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )
    calls = 0

    def fake_call(method, params=None, *, expect_final=False, timeout_ms=None):  # type: ignore[no-untyped-def]
        nonlocal calls
        _ = (params, expect_final, timeout_ms)
        assert method == "channels.status"
        calls += 1
        return {"revision": calls}

    monkeypatch.setattr(client, "_call", fake_call)

    assert client.channels_status(probe=True) == {"revision": 1}
    assert client.channels_status(probe=True) == {"revision": 1}
    assert client.channels_status(probe=True, fresh=True) == {"revision": 2}
    assert client.channels_status(probe=True, fresh=True) == {"revision": 3}


def test_channel_text_send_uses_gateway_send_target_and_message_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        method = command[3]
        params = json.loads(command[command.index("--params") + 1])
        calls.append({"method": method, "params": params})
        assert method == "send"
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"sent": True}}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    client.channels_send_text(
        channel="openclaw-weixin",
        to="sender-1",
        account_id="account-1",
        text="报告已完成",
        dedupe_key="completion:r-1",
    )

    assert calls == [
        {
            "method": "send",
            "params": {
                "channel": "openclaw-weixin",
                "to": "sender-1",
                "accountId": "account-1",
                "message": "报告已完成",
                "idempotencyKey": "completion:r-1",
            },
        }
    ]


def test_channel_file_send_uses_local_temp_media_path_and_cleans_up(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    helpers = _capture_gateway_helpers(monkeypatch)

    def fail_subprocess_run(*args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise AssertionError("channel file send must not spawn openclaw gateway call")

    monkeypatch.setattr("subprocess.run", fail_subprocess_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )
    seen_media_paths: list[Path] = []

    def assert_temp_file_ready(method: str, params: dict[str, object]) -> None:
        assert method == "send"
        media_path = Path(str(params["mediaUrl"]))
        seen_media_paths.append(media_path)
        assert media_path.name == "完整报告.pdf"
        assert media_path.exists()
        assert media_path.read_bytes() == b"%PDF-1.4 test payload"

    helpers[1].on_call = assert_temp_file_ready

    client.channels_send_file(
        channel="openclaw-weixin",
        to="sender-1",
        account_id="account-1",
        file_name="完整报告.pdf",
        payload=b"%PDF-1.4 test payload",
        dedupe_key="file:r-1",
    )

    assert len(helpers) == 2
    request = helpers[1].requests[0]
    params = request["params"]
    assert isinstance(params, dict)
    captured_media_path = Path(str(params["mediaUrl"]))
    assert seen_media_paths == [captured_media_path]
    assert captured_media_path.name == "完整报告.pdf"
    assert not captured_media_path.exists()
    assert helpers[1].requests == [
        {
            "method": "send",
            "params": {
                "channel": "openclaw-weixin",
                "to": "sender-1",
                "accountId": "account-1",
                "message": "完整报告.pdf",
                "mediaUrl": str(captured_media_path),
                "idempotencyKey": "file:r-1",
                "skipQueue": True,
            },
            "expect_final": False,
            "timeout_ms": 1000,
            "scopes": ["operator.admin"],
        }
    ]


def test_channel_file_send_sanitizes_temp_media_basename(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    helpers = _capture_gateway_helpers(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no cli")))
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    client.channels_send_file(
        channel="openclaw-weixin",
        to="sender-1",
        file_name="../CON.pdf",
        payload=b"%PDF-1.4 test payload",
        dedupe_key="file:r-safe-name",
    )

    params = helpers[1].requests[0]["params"]
    assert isinstance(params, dict)
    assert Path(str(params["mediaUrl"])).name == "_CON.pdf"
    assert params["message"] == "../CON.pdf"
    assert params["skipQueue"] is True


def test_channel_file_send_requires_target() -> None:
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    with pytest.raises(TypeError, match="required keyword-only argument: 'to'"):
        client.channels_send_file(
            channel="openclaw-weixin",
            file_name="完整报告.pdf",
            payload=b"%PDF-1.4 test payload",
            dedupe_key="file:r-current",
        )


def test_channel_file_send_uses_supplied_file_path_without_temp_copy(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    pdf_path = tmp_path / "already-generated.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 existing file")
    calls: list[dict[str, object]] = []

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        method = command[3]
        params = json.loads(command[command.index("--params") + 1])
        calls.append({"method": method, "params": params})
        assert method == "send"
        assert params["mediaUrl"] == str(pdf_path)
        assert pdf_path.exists()
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"sent": True}}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1000,
        token=None,
        password=None,
    )

    client.channels_send_file(
        channel="openclaw-weixin",
        to="sender-1",
        account_id="account-1",
        file_name="完整报告.pdf",
        file_path=pdf_path,
        dedupe_key="file:r-2",
    )

    assert pdf_path.exists()
    assert calls == [
        {
            "method": "send",
            "params": {
                "channel": "openclaw-weixin",
                "to": "sender-1",
                "accountId": "account-1",
                "message": "完整报告.pdf",
                "mediaUrl": str(pdf_path),
                "idempotencyKey": "file:r-2",
            },
        }
    ]


def test_channel_send_caps_long_gateway_timeout_by_media_type(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    timeouts: list[str] = []
    helpers = _capture_gateway_helpers(monkeypatch)

    def fake_run(command, capture_output, text, check, env, timeout):  # type: ignore[no-untyped-def]
        _ = (capture_output, text, check, env, timeout)
        assert command[3] == "send"
        timeouts.append(command[command.index("--timeout") + 1])
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"sent": True}}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=600_000,
        token=None,
        password=None,
    )

    client.channels_send_text(
        channel="openclaw-weixin",
        to="sender-1",
        text="报告完成",
        dedupe_key="text:r-1",
    )
    client.channels_send_file(
        channel="openclaw-weixin",
        to="sender-1",
        file_name="完整报告.pdf",
        payload=b"%PDF-1.4 test payload",
        dedupe_key="file:r-1",
    )

    assert timeouts == ["15000"]
    assert [(request["timeout_ms"], request["scopes"]) for request in helpers[1].requests] == [
        (180000, ["operator.admin"])
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
