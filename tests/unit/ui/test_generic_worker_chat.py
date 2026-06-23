from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient
from claw_trade.web.routes_ui import send_worker_chat


@pytest.mark.parametrize("worker_id", ("", "   "))
def test_generic_worker_chat_requires_structured_worker_id_without_default_fallback(worker_id: str) -> None:
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(_ExplodingReportRepository(), OpenClawWorkerChatClient(gateway))

    with pytest.raises(UiProductError) as exc:
        controller.send_worker_chat(
            request_id="req-missing-worker",
            mode="generic_worker_chat",
            worker_id=worker_id,
            text="怎么看今天盘面？",
            conversation_id="main",
            report_id=None,
        )

    assert exc.value.code == "INVALID_INPUT"
    assert gateway.session_metadata == []
    assert gateway.chat_calls == []
    assert gateway.report_qa_calls == 0


@pytest.mark.parametrize(
    "payload",
    (
        {
            "requestId": "req-missing-worker",
            "mode": "generic_worker_chat",
            "text": "hello",
            "conversationId": "conversation-1",
        },
        {
            "requestId": "req-empty-worker",
            "mode": "generic_worker_chat",
            "workerId": "   ",
            "text": "hello",
            "conversationId": "conversation-1",
        },
    ),
)
def test_send_worker_chat_api_requires_worker_id_without_using_default_worker(payload: dict[str, object]) -> None:
    worker_chat = _FakeWorkerChatController()
    request = _request(worker_chat_controller=worker_chat)

    response = send_worker_chat(payload, request)

    assert response.status_code == 400
    assert _json(response)["code"] == "INVALID_INPUT"
    assert worker_chat.calls == []


def test_generic_worker_chat_uses_payload_worker_id_and_keeps_typed_mentions_in_text() -> None:
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(_ExplodingReportRepository(), OpenClawWorkerChatClient(gateway))
    user_text = "@市场分析师 和 @news_analyst 都不要改变 worker_id。"

    reply = controller.send_worker_chat(
        request_id="req-mentions",
        mode="generic_worker_chat",
        worker_id="portfolio_manager",
        text=user_text,
        conversation_id="main",
        report_id=None,
    )

    assert reply.workerDisplayName == "组合经理"
    assert gateway.session_metadata == [
        {
            "scope": "generic_worker_chat",
            "workerId": "portfolio_manager",
            "agentId": "ui_worker_chat",
            "sessionKey": "agent:ui_worker_chat:generic:portfolio_manager:main",
            "label": "agent:ui_worker_chat:generic:portfolio_manager:main",
        }
    ]
    assert gateway.chat_calls[0]["session_key"] == "agent:ui_worker_chat:generic:portfolio_manager:main"
    assert gateway.chat_calls[0]["idempotency_key"] == "req-mentions"
    message = str(gateway.chat_calls[0]["message"])
    assert message.startswith("你现在以「组合经理」的视角进行普通聊天。")
    assert "generic_worker_chat" not in message
    assert "worker_id:" not in message
    assert user_text in message


def test_generic_worker_chat_does_not_read_report_repository_or_fallback_to_legacy_report_qa() -> None:
    repository = _ExplodingReportRepository()
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(repository, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-no-report-path",
        mode="generic_worker_chat",
        worker_id="news_analyst",
        text="只聊新闻，不读取报告。",
        conversation_id="main",
        report_id=None,
    )

    assert repository.calls == []
    assert gateway.report_qa_calls == 0
    assert len(gateway.chat_calls) == 1
    message = str(gateway.chat_calls[0]["message"])
    assert "你现在以「新闻分析师」的视角进行普通聊天。" in message
    assert "只聊新闻，不读取报告。" in message


def _request(*, worker_chat_controller: object) -> object:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                ui_services=SimpleNamespace(worker_chat_controller=worker_chat_controller)
            )
        )
    )


def _json(response: object) -> dict[str, object]:
    return json.loads(response.body)


class _ExplodingReportRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_report(self, report_id: str) -> object:
        self.calls.append(f"get_report:{report_id}")
        raise AssertionError("generic worker chat must not read saved reports")

    def read_markdown(self, report_id: str) -> str:
        self.calls.append(f"read_markdown:{report_id}")
        raise AssertionError("generic worker chat must not read report markdown")

    def list_saved_reports(self) -> list[dict[str, object]]:
        self.calls.append("list_saved_reports")
        raise AssertionError("generic worker chat must not list saved reports")


class _RecordingWorkerChatGateway:
    def __init__(self) -> None:
        self.session_metadata: list[dict[str, object]] = []
        self.chat_calls: list[dict[str, object]] = []
        self.report_qa_calls = 0

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

    def report_qa_chat_send(self, **kwargs: object) -> dict[str, str]:
        _ = kwargs
        self.report_qa_calls += 1
        raise AssertionError("generic worker chat must not call legacy report QA")

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        _ = payload
        raise AssertionError("generic worker chat must not call report workflow runner")


class _FakeWorkerChatController:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_worker_chat(
        self,
        *,
        request_id: str,
        mode: str,
        worker_id: str,
        text: str,
        conversation_id: str,
        report_id: str | None,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "request_id": request_id,
                "mode": mode,
                "worker_id": worker_id,
                "text": text,
                "conversation_id": conversation_id,
                "report_id": report_id,
            }
        )
        return {
            "kind": "worker_chat_reply",
            "workerDisplayName": "组合经理",
            "text": "worker answer",
            "mode": "generic_worker_chat",
        }
