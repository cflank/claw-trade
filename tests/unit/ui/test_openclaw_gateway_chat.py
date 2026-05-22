from __future__ import annotations

from typing import Any, Mapping

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
