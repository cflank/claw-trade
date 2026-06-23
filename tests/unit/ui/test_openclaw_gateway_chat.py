from __future__ import annotations

import json
from typing import Any, Mapping

from claw_trade.web import openclaw_gateway
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient


class _StartedChatGateway(OpenClawGatewayRpcClient):
    def __init__(self) -> None:
        super().__init__(
            gateway_call_bin="openclaw",
            gateway_ws_url="ws://127.0.0.1:18789",
            timeout_ms=1234,
            token=None,
            password=None,
        )
        self.calls: list[tuple[str, Mapping[str, Any] | None, bool, int | None]] = []

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        self.calls.append((method, params, expect_final, timeout_ms))
        if method == "chat.send":
            return {"runId": "run-chat-1", "status": "started"}
        if method == "agent.wait":
            return {"runId": "run-chat-1", "status": "ok"}
        if method == "chat.history":
            return {
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "追问 prompt"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "基于报告的回答"}]},
                ]
            }
        raise AssertionError(f"unexpected method: {method}")


def test_report_qa_chat_waits_for_started_run_and_reads_history() -> None:
    client = _StartedChatGateway()

    reply = client.report_qa_chat_send(
        request_id="req-1",
        context_id="report:r1",
        session_id="ui:report_qa:r1",
        text="追问 prompt",
    )

    assert reply == {"text": "基于报告的回答"}
    assert [call[0] for call in client.calls] == ["chat.send", "agent.wait", "chat.history"]
    assert client.calls[0][2] is True
    assert client.calls[1][1] == {"runId": "run-chat-1", "timeoutMs": 1234}


class _ExistingSessionGateway(OpenClawGatewayRpcClient):
    def __init__(self) -> None:
        super().__init__(
            gateway_call_bin="openclaw",
            gateway_ws_url="ws://127.0.0.1:18789",
            timeout_ms=1234,
            token=None,
            password=None,
        )

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        _ = (method, params, expect_final, timeout_ms)
        raise RuntimeError("GatewayClientRequestError: label already in use: ui:report_qa:r1")


def test_sessions_create_reuses_existing_openclaw_session_key() -> None:
    client = _ExistingSessionGateway()

    session_key = client.sessions_create(
        metadata={"scope": "report_qa", "sessionKey": "ui:report_qa:r1", "label": "ui:report_qa:r1"}
    )

    assert session_key == "ui:report_qa:r1"


class _UiChatGateway(OpenClawGatewayRpcClient):
    def __init__(self, *, timeout_ms: int = 1234) -> None:
        super().__init__(
            gateway_call_bin="openclaw",
            gateway_ws_url="ws://127.0.0.1:18789",
            timeout_ms=timeout_ms,
            token=None,
            password=None,
        )
        self.calls: list[tuple[str, Mapping[str, Any] | None, bool, int | None]] = []

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        _ = timeout_ms
        self.calls.append((method, params, expect_final, timeout_ms))
        if method == "sessions.create":
            return {"key": params["key"]}  # type: ignore[index]
        if method == "agent":
            return {"runId": "req-1", "status": "ok", "text": "普通聊天回复"}
        if method == "agent.wait":
            return {"runId": "req-1", "status": "done"}
        if method == "chat.history":
            return {
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "你好"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "普通聊天回复"}]},
                ]
            }
        raise AssertionError(f"unexpected method: {method}")


def test_ui_chat_uses_explicit_non_worker_agent_session() -> None:
    client = _UiChatGateway()

    reply = client.chat_send(context_id="normal-chat", text="你好", request_id="req-1")

    assert reply == {"text": "普通聊天回复"}
    assert [call[0] for call in client.calls] == ["sessions.create", "agent"]
    assert client.calls[0][:2] == (
        "sessions.create",
        {
            "key": "agent:ui_chat:v2:ui:normal-chat",
            "label": "ui:v2:normal-chat",
            "agentId": "ui_chat",
        },
    )
    assert client.calls[1][1]["sessionKey"] == "agent:ui_chat:v2:ui:normal-chat"  # type: ignore[index]
    assert client.calls[1][1]["agentId"] == "ui_chat"  # type: ignore[index]
    assert client.calls[1][1]["lane"] == "ui-interactive"  # type: ignore[index]
    assert client.calls[1][1]["waitForCompletion"] is True  # type: ignore[index]
    assert client.calls[1][1]["message"] == "你好"  # type: ignore[index]
    assert client.calls[1][2] is True
    assert client.calls[0][3] == 1234
    assert client.calls[1][3] == 1234


def test_ui_chat_reads_reply_from_openclaw_state(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sessions_dir = tmp_path / "state" / "agents" / "ui_chat" / "sessions"
    sessions_dir.mkdir(parents=True)
    run_id = "643a95b5-0da6-4286-be0a-db5f685ea9b6"
    (sessions_dir / "session-1.trajectory.jsonl").write_text(f'{{"runId":"{run_id}"}}\n', encoding="utf-8")
    (sessions_dir / "session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "[Mon] 你好"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "新回复"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "state"))

    reply = openclaw_gateway._read_reply_from_agent_state(  # type: ignore[attr-defined]
        agent_id="ui_chat",
        run_id=run_id,
        user_text="你好",
        timeout_ms=1000,
    )

    assert reply == "新回复"


class _StateOnlyUiChatGateway(_UiChatGateway):
    def __init__(self, *, sessions_dir: Path) -> None:
        super().__init__(timeout_ms=1000)
        self._sessions_dir = sessions_dir

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        self.calls.append((method, params, expect_final, timeout_ms))
        if method == "sessions.create":
            return {"key": params["key"]}  # type: ignore[index]
        if method == "agent":
            assert params is not None
            run_id = str(params["idempotencyKey"])
            user_text = str(params["message"])
            (self._sessions_dir / "session-req.trajectory.jsonl").write_text(
                f'{{"runId":"{run_id}"}}\n',
                encoding="utf-8",
            )
            (self._sessions_dir / "session-req.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": [{"type": "text", "text": user_text}],
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "状态里的真实回复"}],
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            return {"runId": run_id, "status": "ok"}
        raise AssertionError(f"unexpected method: {method}")


def test_ui_chat_reads_state_reply_for_frontend_request_id(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sessions_dir = tmp_path / "state" / "agents" / "ui_chat" / "sessions"
    sessions_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "state"))
    client = _StateOnlyUiChatGateway(sessions_dir=sessions_dir)

    reply = client.chat_send(context_id="normal-chat", text="你好", request_id="req-1782161297181-908278b34ffd48")

    assert reply == {"text": "状态里的真实回复"}


def test_report_worker_chat_does_not_use_raw_model_run() -> None:
    client = _StartedChatGateway()

    reply = client.worker_chat_send(
        session_key="agent:portfolio_manager:report:run-1:default",
        message="报告追问",
        idempotency_key="req-report-1",
    )

    assert reply == {"text": "基于报告的回答"}
    assert [call[0] for call in client.calls] == ["chat.send", "agent.wait", "chat.history"]
    chat_params = client.calls[0][1]
    assert chat_params is not None
    assert "modelRun" not in chat_params
    assert "promptMode" not in chat_params


def test_ui_chat_caps_long_runtime_timeout_for_start_calls() -> None:
    client = _UiChatGateway(timeout_ms=600_000)

    reply = client.chat_send(context_id="normal-chat", text="你好", request_id="req-1")

    assert reply == {"text": "普通聊天回复"}
    assert client.calls[0][0] == "sessions.create"
    assert client.calls[1][0] == "agent"
    assert client.calls[0][3] == 60_000
    assert client.calls[1][3] == 60_000


class _StartedUiChatGateway(OpenClawGatewayRpcClient):
    def __init__(self, *, timeout_ms: int = 600_000) -> None:
        super().__init__(
            gateway_call_bin="openclaw",
            gateway_ws_url="ws://127.0.0.1:18789",
            timeout_ms=timeout_ms,
            token=None,
            password=None,
        )
        self.calls: list[tuple[str, Mapping[str, Any] | None, bool, int | None]] = []

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        self.calls.append((method, params, expect_final, timeout_ms))
        if method == "sessions.create":
            return {"key": params["key"]}  # type: ignore[index]
        if method == "agent":
            return {"runId": "run-ui-1", "status": "ok", "text": "普通聊天回复"}
        if method == "agent.wait":
            return {"runId": "run-ui-1", "status": "done"}
        if method == "chat.history":
            return {
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "你好"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "普通聊天回复"}]},
                ]
            }
        raise AssertionError(f"unexpected method: {method}")


def test_ui_chat_caps_long_runtime_timeout_for_started_run() -> None:
    client = _StartedUiChatGateway()

    reply = client.chat_send(context_id="normal-chat", text="你好", request_id="req-1")

    assert reply == {"text": "普通聊天回复"}
    assert [call[0] for call in client.calls] == ["sessions.create", "agent"]
    assert client.calls[0][3] == 60_000
    assert client.calls[1][3] == 60_000


class _FakeHelperStdout:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def readline(self) -> str:
        if not self.lines:
            raise AssertionError("unexpected helper read")
        return self.lines.pop(0)


class _FakeHelperStdin:
    def __init__(self, process: "_FakeHelperProcess") -> None:
        self._process = process

    def write(self, raw: str) -> None:
        request = json.loads(raw)
        self._process.requests.append(request)
        method = request["method"]
        if method == "sessions.create":
            result = {"key": request["params"]["key"]}
        elif method == "agent":
            result = {"runId": request["params"]["idempotencyKey"], "status": "ok", "text": "OK"}
        elif method == "chat.history":
            result = {
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "你好"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "OK"}]},
                    {"role": "user", "content": [{"type": "text", "text": "再说一次"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "OK"}]},
                ]
            }
        elif method == "channels.status":
            result = {"channels": []}
        else:
            raise AssertionError(method)
        self._process.stdout.lines.append(json.dumps({"id": request["id"], "ok": True, "result": result}) + "\n")

    def flush(self) -> None:
        pass


class _FakeHelperProcess:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.stdout = _FakeHelperStdout()
        self.stdin = _FakeHelperStdin(self)

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        pass


def test_ui_chat_reuses_gateway_helper_without_cli_subprocess(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    helpers: list[_FakeHelperProcess] = []

    def fake_popen(*args: object, **kwargs: object) -> _FakeHelperProcess:
        _ = args
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["OPENCLAW_GATEWAY_URL"] == "ws://127.0.0.1:18789"
        assert env["OPENCLAW_GATEWAY_TIMEOUT_MS"] == "1234"
        assert env["OPENCLAW_GATEWAY_TOKEN"] == "token-1"
        helper = _FakeHelperProcess()
        helpers.append(helper)
        return helper

    def fail_subprocess_run(*args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise AssertionError("chat path must not spawn openclaw gateway call")

    monkeypatch.setattr(openclaw_gateway.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(openclaw_gateway, "_readline_with_timeout", lambda stream, timeout_ms: stream.readline())
    monkeypatch.setattr("subprocess.run", fail_subprocess_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1234,
        token="token-1",
        password=None,
    )

    assert client.chat_send(context_id="normal-chat", text="你好", request_id="req-1") == {"text": "OK"}
    assert client.chat_send(context_id="normal-chat", text="再说一次", request_id="req-2") == {"text": "OK"}

    assert len(helpers) == 1
    methods = [request["method"] for request in helpers[0].requests]
    assert methods == ["sessions.create", "agent", "agent"]
    assert helpers[0].requests[1]["expectFinal"] is True
    assert helpers[0].requests[1]["token"] == "token-1"


def test_ui_chat_prewarm_creates_normal_chat_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    helpers: list[_FakeHelperProcess] = []

    def fake_popen(*args: object, **kwargs: object) -> _FakeHelperProcess:
        _ = (args, kwargs)
        helper = _FakeHelperProcess()
        helpers.append(helper)
        return helper

    monkeypatch.setattr(openclaw_gateway.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(openclaw_gateway, "_readline_with_timeout", lambda stream, timeout_ms: stream.readline())
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1234,
        token=None,
        password=None,
    )

    client.prewarm_ui_chat()

    assert len(helpers) == 1
    assert [request["method"] for request in helpers[0].requests] == ["sessions.create"]
    assert helpers[0].requests[0]["params"] == {
        "key": "agent:ui_chat:v2:ui:normal-chat",
        "label": "ui:v2:normal-chat",
        "agentId": "ui_chat",
    }


def test_channel_status_reuses_gateway_helper_without_cli_subprocess(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    helpers: list[_FakeHelperProcess] = []

    def fake_popen(*args: object, **kwargs: object) -> _FakeHelperProcess:
        _ = args
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["OPENCLAW_GATEWAY_TIMEOUT_MS"] == "1234"
        helper = _FakeHelperProcess()
        helpers.append(helper)
        return helper

    def fail_subprocess_run(*args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        raise AssertionError("status polling must not spawn openclaw gateway call")

    monkeypatch.setattr(openclaw_gateway.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(openclaw_gateway, "_readline_with_timeout", lambda stream, timeout_ms: stream.readline())
    monkeypatch.setattr("subprocess.run", fail_subprocess_run)
    client = OpenClawGatewayRpcClient(
        gateway_call_bin="openclaw",
        gateway_ws_url="ws://127.0.0.1:18789",
        timeout_ms=1234,
        token="token-1",
        password=None,
    )

    light_status = client.channels_status()
    light_status["channels"].append({"id": "mutated"})

    assert client.channels_status() == {"channels": []}
    assert client.channels_status(probe=True) == {"channels": []}
    assert client.channels_status(probe=True) == {"channels": []}

    assert len(helpers) == 1
    assert [request["method"] for request in helpers[0].requests] == ["channels.status", "channels.status"]
    assert helpers[0].requests[0]["params"] == {"probe": False, "timeoutMs": 3000}
    assert helpers[0].requests[1]["params"] == {"probe": True, "timeoutMs": 8000}
