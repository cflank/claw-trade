from __future__ import annotations

import pytest
from claw_trade.ui_backend.worker_chat_openclaw import (
    OpenClawAgentChatRequest,
    OpenClawWorkerChatClient,
    build_session_key,
)


def test_generic_worker_chat_creates_agent_scoped_session_and_sends_native_chat_message() -> None:
    gateway = _FakeWorkerChatGateway()
    client = OpenClawWorkerChatClient(gateway)

    response = client.send_worker_chat(
        mode="generic_worker_chat",
        worker_id="market_analyst",
        user_message="怎么看今天盘面？",
        conversation_id="conversation 1",
        idempotency_key="req-1",
    )

    assert response.text == "worker answer"
    assert gateway.session_metadata == [
        {
            "scope": "generic_worker_chat",
            "workerId": "market_analyst",
            "agentId": "ui_worker_chat",
            "sessionKey": "agent:ui_worker_chat:generic:market_analyst:conversation-1",
            "label": "agent:ui_worker_chat:generic:market_analyst:conversation-1",
        }
    ]
    assert gateway.chat_calls[0]["session_key"].startswith("agent:ui_worker_chat:generic:market_analyst:")
    assert gateway.chat_calls[0] == {
        "session_key": "agent:ui_worker_chat:generic:market_analyst:conversation-1",
        "message": "怎么看今天盘面？",
        "idempotency_key": "req-1",
    }


def test_report_worker_chat_uses_report_scoped_agent_session() -> None:
    gateway = _FakeWorkerChatGateway()
    client = OpenClawWorkerChatClient(gateway)

    client.send_worker_chat(
        mode="report_worker_chat",
        worker_id="risk_moderator",
        user_message="这份报告最大风险是什么？",
        conversation_id="conversation/2",
        report_id="report/7",
        idempotency_key="req-2",
    )

    assert gateway.session_metadata[0]["agentId"] == "risk_moderator"
    assert gateway.session_metadata[0]["sessionKey"] == "agent:risk_moderator:report:report-7:conversation-2"
    assert gateway.chat_calls == [
        {
            "session_key": "agent:risk_moderator:report:report-7:conversation-2",
            "message": "这份报告最大风险是什么？",
            "idempotency_key": "req-2",
        }
    ]


def test_worker_chat_reuses_created_session_for_followup() -> None:
    gateway = _FakeWorkerChatGateway()
    client = OpenClawWorkerChatClient(gateway)

    for request_id, message in (("req-1", "第一句"), ("req-2", "第二句")):
        client.send_worker_chat(
            mode="generic_worker_chat",
            worker_id="market_analyst",
            user_message=message,
            conversation_id="conversation 1",
            idempotency_key=request_id,
        )

    assert len(gateway.session_metadata) == 1
    assert [call["idempotency_key"] for call in gateway.chat_calls] == ["req-1", "req-2"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("worker_id", ""),
        ("session_key", ""),
        ("user_message", ""),
        ("idempotency_key", ""),
    ),
)
def test_agent_chat_request_fails_fast_for_required_structured_fields(field: str, value: str) -> None:
    gateway = _FakeWorkerChatGateway()
    client = OpenClawWorkerChatClient(gateway)
    kwargs = {
        "mode": "generic_worker_chat",
        "worker_id": "market_analyst",
        "session_key": "agent:ui_worker_chat:generic:market_analyst:conversation-1",
        "user_message": "hello",
        "idempotency_key": "req-1",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        client.send_agent_chat(OpenClawAgentChatRequest(**kwargs))

    assert gateway.session_metadata == []
    assert gateway.chat_calls == []


def test_build_session_key_uses_worker_report_and_safe_conversation_parts() -> None:
    assert (
        build_session_key(
            mode="report_worker_chat",
            worker_id="market_analyst",
            report_id="report/1",
            conversation_id="conversation 1",
        )
        == "agent:market_analyst:report:report-1:conversation-1"
    )


class _FakeWorkerChatGateway:
    def __init__(self) -> None:
        self.session_metadata: list[dict[str, object]] = []
        self.chat_calls: list[dict[str, object]] = []

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        self.session_metadata.append(metadata)
        return str(metadata["sessionKey"])

    def worker_chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        self.chat_calls.append(
            {
                "session_key": session_key,
                "message": message,
                "idempotency_key": idempotency_key,
            }
        )
        return {"text": "worker answer"}
