from __future__ import annotations

import pytest
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawAgentChatRequest, OpenClawWorkerChatClient


def test_missing_session_key_stops_before_gateway_and_does_not_fallback_to_report_qa() -> None:
    gateway = _NoFallbackGateway()
    client = OpenClawWorkerChatClient(gateway)

    with pytest.raises(ValueError, match="session_key"):
        client.send_agent_chat(
            OpenClawAgentChatRequest(
                mode="generic_worker_chat",
                worker_id="market_analyst",
                session_key="",
                user_message="hello",
                idempotency_key="req-1",
            )
        )

    assert gateway.calls == []


def test_missing_worker_id_stops_before_gateway_and_does_not_fallback_to_report_qa() -> None:
    gateway = _NoFallbackGateway()
    client = OpenClawWorkerChatClient(gateway)

    with pytest.raises(ValueError, match="worker_id"):
        client.send_worker_chat(
            mode="generic_worker_chat",
            worker_id="",
            user_message="hello",
            conversation_id="conversation-1",
            idempotency_key="req-1",
        )

    assert gateway.calls == []


class _NoFallbackGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        self.calls.append("sessions.create")
        return str(metadata["sessionKey"])

    def worker_chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        _ = (session_key, message, idempotency_key)
        self.calls.append("worker_chat_send")
        return {"text": "should not be used"}

    def report_qa_chat_send(self, **kwargs: object) -> dict[str, str]:
        self.calls.append("report_qa_chat_send")
        raise AssertionError(f"unexpected report_qa fallback: {kwargs}")
