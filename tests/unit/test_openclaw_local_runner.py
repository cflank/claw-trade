from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import threading

import pytest

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
    runner = create_default_runner()
    assert isinstance(runner, OpenClawLocalRunner)
    assert runner.gateway_ws_url == "ws://127.0.0.1:18789"
    assert runner.gateway_call_bin == "/tmp/openclaw-custom"
    assert callable(getattr(runner, "probe", None))
    assert callable(getattr(runner, "run_worker", None))


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
    params_raw = seen_commands[0][seen_commands[0].index("--params") + 1]
    params = json.loads(params_raw)
    assert params["command"]["worker_id"] == "market_analyst"


def test_run_worker_default_local_gateway_without_credentials_omits_url_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = OpenClawLocalRunner(gateway_ws_url="ws://127.0.0.1:18789")
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
