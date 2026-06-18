from __future__ import annotations

from typing import Any, Mapping

from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient


def test_gateway_sessions_create_forwards_agent_id_contract() -> None:
    client = _RecordingGateway()

    session_key = client.sessions_create(
        metadata={
            "scope": "generic_worker_chat",
            "workerId": "market_analyst",
            "agentId": "market_analyst",
            "sessionKey": "agent:market_analyst:generic:conversation-1",
            "label": "agent:market_analyst:generic:conversation-1",
        }
    )

    assert session_key == "agent:market_analyst:generic:conversation-1"
    assert client.calls == [
        (
            "sessions.create",
            {
                "key": "agent:market_analyst:generic:conversation-1",
                "label": "agent:market_analyst:generic:conversation-1",
                "agentId": "market_analyst",
            },
        )
    ]


def test_gateway_worker_chat_send_uses_only_native_chat_send_fields() -> None:
    client = _RecordingGateway()

    reply = client.worker_chat_send(
        session_key="agent:risk_moderator:report:report-1:conversation-1",
        message="报告风险是什么？",
        idempotency_key="req-1",
    )

    assert reply == {"text": "worker answer"}
    assert client.calls == [
        (
            "chat.send",
            {
                "sessionKey": "agent:risk_moderator:report:report-1:conversation-1",
                "message": "报告风险是什么？",
                "deliver": False,
                "idempotencyKey": "req-1",
            },
        )
    ]


def test_gateway_worker_chat_send_does_not_send_unsupported_control_fields() -> None:
    client = _RecordingGateway()

    client.worker_chat_send(
        session_key="agent:market_analyst:generic:conversation-1",
        message="hello",
        idempotency_key="req-2",
    )

    params = client.calls[0][1]
    assert "promptProfile" not in params
    assert "promptVariables" not in params
    assert "toolPolicy" not in params
    assert "visibleTools" not in params
    assert "captureProviderPayload" not in params


def test_gateway_worker_chat_send_never_calls_run_single_worker() -> None:
    client = _RecordingGateway()

    client.worker_chat_send(
        session_key="agent:market_analyst:generic:conversation-1",
        message="hello",
        idempotency_key="req-3",
    )

    assert [method for method, _params in client.calls] == ["chat.send"]
    assert "agent.runSingleWorker" not in [method for method, _params in client.calls]


class _RecordingGateway(OpenClawGatewayRpcClient):
    def __init__(self) -> None:
        super().__init__(
            gateway_call_bin="openclaw",
            gateway_ws_url="ws://127.0.0.1:18789",
            timeout_ms=1000,
            token=None,
            password=None,
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        _ = (expect_final, timeout_ms)
        self.calls.append((method, dict(params or {})))
        if method == "sessions.create":
            return {"key": params["key"] if params else ""}
        if method == "chat.send":
            return {"text": "worker answer"}
        raise AssertionError(f"unexpected method: {method}")
