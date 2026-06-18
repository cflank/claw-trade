from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import get_type_hints

from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.worker_chat_models import WorkerChatReplyForUser
from claw_trade.web.routes_ui import (
    AskReportQuestionRequest,
    ask_report_question,
    list_worker_chat_workers,
    send_worker_chat,
)


def test_send_worker_chat_missing_worker_id_returns_400() -> None:
    worker_chat = _FakeWorkerChatController()
    request = _request(worker_chat_controller=worker_chat)

    response = send_worker_chat(
        {
            "requestId": "req-1",
            "mode": "generic_worker_chat",
            "text": "hello",
            "conversationId": "conversation-1",
        },
        request,
    )

    assert response.status_code == 400
    assert _json(response)["code"] == "INVALID_INPUT"
    assert worker_chat.calls == []


def test_send_worker_chat_generic_mode_rejects_report_id() -> None:
    worker_chat = _FakeWorkerChatController()
    request = _request(worker_chat_controller=worker_chat)

    response = send_worker_chat(
        {
            "requestId": "req-1",
            "mode": "generic_worker_chat",
            "workerId": "portfolio_manager",
            "text": "hello",
            "conversationId": "conversation-1",
            "reportId": "report-1",
        },
        request,
    )

    assert response.status_code == 400
    assert _json(response)["code"] == "INVALID_INPUT"
    assert worker_chat.calls == []


def test_legacy_ask_report_question_cannot_return_worker_chat_reply_kind() -> None:
    report_question = _FakeReportQuestionService()
    request = _request(report_question_service=report_question)

    response = ask_report_question(
        AskReportQuestionRequest(
            requestId="req-1",
            reportId="report-1",
            text="报告里怎么看风险？",
            workerId="portfolio_manager",
        ),
        request,
    )

    assert response.status_code == 200
    payload = _json(response)
    assert payload == {"text": "legacy report answer"}
    assert payload.get("kind") != "worker_chat_reply"
    assert report_question.calls == [
        {
            "report_id": "report-1",
            "text": "报告里怎么看风险？",
            "request_id": "req-1",
            "context_id": "report:report-1",
        }
    ]


def test_list_worker_chat_workers_exposes_the_7_approved_workers() -> None:
    response = list_worker_chat_workers()

    assert response.status_code == 200
    workers = _json(response)["workers"]
    assert [worker["workerId"] for worker in workers] == [
        "portfolio_manager",
        "research_manager",
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "risk_moderator",
    ]
    assert len(workers) == 7


def test_send_worker_chat_success_returns_user_safe_worker_chat_reply() -> None:
    worker_chat = _FakeWorkerChatController(
        reply={
            "kind": "worker_chat_reply",
            "workerDisplayName": "组合经理",
            "text": "可以先看仓位风险。",
            "mode": "generic_worker_chat",
            "sessionId": "session-secret",
            "providerRequestId": "provider-secret",
            "workspacePath": "/tmp/secret",
        }
    )
    request = _request(worker_chat_controller=worker_chat)

    response = send_worker_chat(
        {
            "requestId": "req-1",
            "mode": "generic_worker_chat",
            "workerId": "portfolio_manager",
            "text": "@news_analyst 不应该改变 worker",
            "conversationId": "conversation-1",
        },
        request,
    )

    assert response.status_code == 200
    assert _json(response) == {
        "kind": "worker_chat_reply",
        "workerDisplayName": "组合经理",
        "text": "可以先看仓位风险。",
        "mode": "generic_worker_chat",
    }
    assert worker_chat.calls == [
        {
            "request_id": "req-1",
            "mode": "generic_worker_chat",
            "worker_id": "portfolio_manager",
            "text": "@news_analyst 不应该改变 worker",
            "conversation_id": "conversation-1",
            "report_id": None,
        }
    ]


def test_send_worker_chat_idempotency_conflict_returns_409() -> None:
    worker_chat = _FakeWorkerChatController(
        error=UiProductError(
            "WORKER_CHAT_IDEMPOTENCY_CONFLICT",
            "这次 worker 聊天请求与之前相同 requestId 的内容不一致，请刷新后重试。",
        )
    )
    request = _request(worker_chat_controller=worker_chat)

    response = send_worker_chat(
        {
            "requestId": "req-1",
            "mode": "generic_worker_chat",
            "workerId": "portfolio_manager",
            "text": "hello",
            "conversationId": "conversation-1",
        },
        request,
    )

    assert response.status_code == 409
    assert _json(response)["code"] == "WORKER_CHAT_IDEMPOTENCY_CONFLICT"


def test_send_worker_chat_accepts_raw_dict_payload_not_required_pydantic_model() -> None:
    signature = inspect.signature(send_worker_chat)
    payload_parameter = signature.parameters["payload"]

    assert get_type_hints(send_worker_chat)["payload"] == dict[str, object]
    assert payload_parameter.default is inspect.Parameter.empty


def _request(
    *,
    report_question_service: object | None = None,
    worker_chat_controller: object | None = None,
) -> object:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                ui_services=SimpleNamespace(
                    report_question_service=report_question_service or _FakeReportQuestionService(),
                    worker_chat_controller=worker_chat_controller or _FakeWorkerChatController(),
                )
            )
        )
    )


def _json(response: object) -> dict[str, object]:
    return json.loads(response.body)


class _FakeReportQuestionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ask_report_question(
        self,
        *,
        report_id: str,
        text: str,
        request_id: str,
        context_id: str,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "report_id": report_id,
                "text": text,
                "request_id": request_id,
                "context_id": context_id,
            }
        )
        return {"text": "legacy report answer"}


class _FakeWorkerChatController:
    def __init__(self, reply: object | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._error = error
        self._reply = reply or WorkerChatReplyForUser(
            kind="worker_chat_reply",
            workerDisplayName="组合经理",
            text="worker answer",
            mode="generic_worker_chat",
        )

    def send_worker_chat(
        self,
        *,
        request_id: str,
        mode: str,
        worker_id: str,
        text: str,
        conversation_id: str,
        report_id: str | None,
    ) -> object:
        if self._error is not None:
            raise self._error
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
        return self._reply
