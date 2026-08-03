from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from claw_trade.runtime import openclaw_local_runner
from claw_trade.runtime.openclaw_local_runner import OpenClawLocalRunner, create_default_runner


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format, args


@pytest.fixture
def health_server() -> str:
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ws_url = f"ws://127.0.0.1:{server.server_port}"
    try:
        yield ws_url
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_factory_builds_runner_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
    monkeypatch.setenv("OPENCLAW_GATEWAY_CALL_BIN", "/tmp/openclaw-custom")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "/tmp/openclaw-state")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", "/tmp/openclaw-state/openclaw.json")
    runner = create_default_runner()
    assert isinstance(runner, OpenClawLocalRunner)
    assert runner.gateway_ws_url == "ws://127.0.0.1:18789"
    assert runner.gateway_call_bin == "/tmp/openclaw-custom"
    assert runner.state_dir == "/tmp/openclaw-state"
    assert runner.config_path == "/tmp/openclaw-state/openclaw.json"
    assert callable(getattr(runner, "probe", None))
    assert callable(getattr(runner, "run_worker", None))


def test_factory_reads_openclaw_state_from_runtime_env_when_process_env_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    runtime_dir = tmp_path / ".runtime" / "dev-services"
    runtime_dir.mkdir(parents=True)
    runtime_env = runtime_dir / "runtime.env"
    runtime_env.write_text(
        "\n".join(
            [
                f"OPENCLAW_STATE_DIR={tmp_path}/runtime-state",
                f"OPENCLAW_CONFIG_PATH={tmp_path}/runtime-state/openclaw.json",
            ]
        ),
        encoding="utf-8",
    )

    runner = create_default_runner()

    assert runner.state_dir == f"{tmp_path}/runtime-state"
    assert runner.config_path == f"{tmp_path}/runtime-state/openclaw.json"


def test_probe_fails_when_gateway_method_missing(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["openclaw"],
            returncode=1,
            stdout="",
            stderr="Gateway call failed: Method not found: agent.runSingleWorker",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    probe = runner.probe()
    assert probe.ok is False
    assert "runSingleWorker" in (probe.reason or "")


def test_run_worker_calls_gateway_run_single_worker(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)
    seen_commands: list[list[str]] = []
    gateway_payload = {
        "status": "succeeded",
        "openclaw_run_id": "oc-run-1",
        "provider_request_id": "req-1",
        "provider_request_id_status": "returned",
        "workspace_evidence_path": "/tmp/workspace-evidence.json",
        "provider_request_path": "/tmp/provider-request.json",
        "visible_tools_path": "/tmp/visible-tools.json",
        "first_response_path": "/tmp/first-response.json",
        "tool_calls_status": "recorded",
        "tool_calls_path": "/tmp/tool-calls.json",
        "raw_output_path": "/tmp/raw-output.md",
        "openviking_receipt_path": "/tmp/openviking-receipt.json",
        "failure_reason": None,
    }

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        seen_commands.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(gateway_payload),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = runner.run_worker({"worker_id": "market_analyst"})

    assert result["status"] == "succeeded"
    assert seen_commands
    rendered = " ".join(seen_commands[0])
    assert "agent.runSingleWorker" in rendered
    assert seen_commands[0].count("--scope") == 2
    scope_indexes = [index for index, value in enumerate(seen_commands[0]) if value == "--scope"]
    assert [seen_commands[0][index + 1] for index in scope_indexes] == [
        "operator.read",
        "operator.write",
    ]
    params_raw = seen_commands[0][seen_commands[0].index("--params") + 1]
    params = json.loads(params_raw)
    assert params["command"]["worker_id"] == "market_analyst"


def test_run_worker_uses_params_file_for_large_payload(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)
    seen_params_files: list[str] = []

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        command = list(cmd)
        assert "--params" not in command
        assert "--params-file" in command
        params_file = command[command.index("--params-file") + 1]
        seen_params_files.append(params_file)
        with open(params_file, encoding="utf-8") as handle:
            params = json.load(handle)
        assert params["command"]["worker_id"] == "report_polisher"
        assert params["command"]["large_material"].startswith("x")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps({"status": "succeeded"}), stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    runner.run_worker({"worker_id": "report_polisher", "large_material": "x" * 70_000})

    assert seen_params_files
    assert not Path(seen_params_files[0]).exists()


def test_run_worker_default_local_gateway_without_credentials_omits_url_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = OpenClawLocalRunner(
        gateway_ws_url="ws://127.0.0.1:18789",
        state_dir="/tmp/runtime-openclaw-state",
        config_path="/tmp/runtime-openclaw-state/openclaw.json",
    )
    seen_commands: list[list[str]] = []
    seen_envs: list[dict[str, str] | None] = []
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args
        seen_commands.append(list(cmd))
        seen_envs.append(kwargs.get("env"))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps({"status": "succeeded"}), stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    runner.run_worker({"worker_id": "market_analyst"})

    assert seen_commands
    assert "--url" not in seen_commands[0]
    assert seen_envs
    assert isinstance(seen_envs[0], dict)
    assert "OPENCLAW_GATEWAY_URL" not in seen_envs[0]
    assert seen_envs[0]["OPENCLAW_STATE_DIR"] == "/tmp/runtime-openclaw-state"
    assert seen_envs[0]["OPENCLAW_CONFIG_PATH"] == "/tmp/runtime-openclaw-state/openclaw.json"


def test_run_worker_custom_gateway_with_token_keeps_url_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url="ws://127.0.0.1:28888", token="token-1")
    seen_commands: list[list[str]] = []

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        seen_commands.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps({"status": "succeeded"}), stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    runner.run_worker({"worker_id": "market_analyst"})

    assert seen_commands
    assert "--url" in seen_commands[0]
    assert seen_commands[0][seen_commands[0].index("--url") + 1] == "ws://127.0.0.1:28888"
    assert "--token" in seen_commands[0]
    assert seen_commands[0][seen_commands[0].index("--token") + 1] == "token-1"


def test_run_worker_cancel_terminates_only_its_process_group(tmp_path: Path) -> None:
    gateway = tmp_path / "fake-gateway.sh"
    gateway.write_text(
        "#!/bin/sh\n"
        "if [ \"$3\" = \"sessions.abort\" ]; then printf '{\"ok\":true,\"abortedRunId\":\"oc-cancel-1\"}'; exit 0; fi\n"
        "trap 'exit 130' TERM\n"
        "sleep 30\n"
        "printf '{\"status\":\"succeeded\"}'\n",
        encoding="utf-8",
    )
    gateway.chmod(0o755)
    runner = OpenClawLocalRunner(
        gateway_ws_url="ws://127.0.0.1:18789",
        gateway_call_bin=str(gateway),
        timeout_ms=600_000,
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            runner.run_worker({"openclaw_run_id": "oc-cancel-1", "worker_id": "market_analyst"})
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    for _ in range(100):
        if "oc-cancel-1" in runner._active_calls:  # noqa: SLF001
            break
        thread.join(timeout=0.01)
    assert "oc-cancel-1" in runner._active_calls  # noqa: SLF001
    assert runner.cancel_worker("oc-cancel-1") is True
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), RuntimeError)


def test_cancel_worker_bounds_abort_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url="ws://127.0.0.1:18789", timeout_ms=2_000)
    seen: dict[str, object] = {}

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args
        seen["cmd"] = list(cmd)
        seen.update(kwargs)
        raise subprocess.TimeoutExpired("openclaw", kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert runner.cancel_worker("oc-timeout") is False
    assert seen["timeout"] == 2.0
    command = seen["cmd"]
    assert isinstance(command, list)
    assert command[3] == "sessions.abort"
    assert command[command.index("--scope") + 1] == "operator.write"


def test_cancel_worker_treats_missing_remote_run_as_idempotent_success(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url="ws://127.0.0.1:18789", timeout_ms=2_000)

    monkeypatch.setattr(
        runner,
        "_call_gateway",
        lambda *args, **kwargs: {"ok": True, "status": "no-active-run", "abortedRunId": None},
    )

    assert runner.cancel_worker("oc-already-stopped") is True


def test_run_worker_approves_local_scope_upgrade_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    monkeypatch.setattr(
        openclaw_local_runner,
        "_openclaw_device_pairing_dist_module_path",
        lambda: tmp_path / "device-pairing.js",
    )
    runner = OpenClawLocalRunner(gateway_ws_url="ws://127.0.0.1:18789")
    calls: list[list[str]] = []
    request_id = "5cbaaad0-5154-497a-8429-9fe8a66d20f3"

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        command = list(cmd)
        calls.append(command)
        if command[0] == "node":
            assert request_id in command
            assert str(state_dir.resolve()) in command
            assert json.loads(command[-1]) == ["operator.read", "operator.write"]
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps({"status": "approved"}),
                stderr="",
            )
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr=(
                    "scope upgrade pending approval "
                    f"(requestId: {request_id})\n"
                    "gateway closed (1008): pairing required"
                ),
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps({"status": "succeeded", "openclaw_run_id": "oc-1"}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = runner.run_worker({"worker_id": "market_analyst"})

    assert result["status"] == "succeeded"
    assert [call[0] for call in calls] == ["openclaw", "node", "openclaw"]


def test_scope_upgrade_uses_configured_openclaw_node_bin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_bin = "/opt/claw-trade/current/runtime/node"
    monkeypatch.setenv("OPENCLAW_NODE_BIN", node_bin)
    monkeypatch.setattr(
        openclaw_local_runner,
        "_openclaw_device_pairing_dist_module_path",
        lambda: tmp_path / "device-pairing.js",
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps({"status": "approved"}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    openclaw_local_runner._approve_local_scope_upgrade_request(
        request_id="5cbaaad0-5154-497a-8429-9fe8a66d20f3",
        requested_scopes=("operator.read", "operator.write"),
        state_dir=tmp_path / "openclaw-state",
    )

    assert calls
    assert calls[0][0] == node_bin


def test_probe_and_run_worker_fail_when_gateway_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url="ws://127.0.0.1:9", timeout_ms=500)
    probe = runner.probe()
    assert probe.ok is False

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return subprocess.CompletedProcess(args=["openclaw"], returncode=1, stdout="", stderr="connection refused")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(RuntimeError, match="connection refused"):
        runner.run_worker({"worker_id": "market_analyst"})


def test_probe_treats_run_single_worker_invalid_params_as_method_available(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["openclaw"],
            returncode=1,
            stdout="",
            stderr="Invalid params for agent.runSingleWorker: required property worker_id",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    probe = runner.probe()
    assert probe.ok is True


def test_probe_rejects_runtime_error_even_if_contains_missing_and_command(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["openclaw"],
            returncode=1,
            stdout="",
            stderr="worker runtime failed: missing market data command output",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    probe = runner.probe()
    assert probe.ok is False
    assert "method 探测失败" in (probe.reason or "")


def test_probe_fails_on_auth_error(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["openclaw"],
            returncode=1,
            stdout="",
            stderr="HTTP 401 Unauthorized: invalid token",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    probe = runner.probe()
    assert probe.ok is False
    assert "鉴权失败" in (probe.reason or "")


def test_probe_fails_when_gateway_cli_missing(
    monkeypatch: pytest.MonkeyPatch,
    health_server: str,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url=health_server)

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["openclaw"],
            returncode=1,
            stdout="",
            stderr="No such file or directory: openclaw",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    probe = runner.probe()
    assert probe.ok is False
    assert "CLI 不可用" in (probe.reason or "")
