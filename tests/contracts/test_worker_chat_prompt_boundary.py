from __future__ import annotations

from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient


def test_generic_worker_chat_sends_raw_user_text_without_report_envelope() -> None:
    gateway = _RecordingWorkerChatGateway()
    repository = _RecordingReportRepository()
    controller = WorkerChatController(repository, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-generic-prompt",
        mode="generic_worker_chat",
        worker_id="market_analyst",
        text="请直接判断今天市场结构。",
        conversation_id="main",
        report_id=None,
    )

    assert repository.calls == []
    message = str(gateway.chat_calls[0]["message"])
    assert message == "请直接判断今天市场结构。"
    for forbidden in (
        "【claw-trade report_worker_chat】",
        "【SavedReport】",
        "【UserQuestion】",
        "worker_display_name:",
        "PM 最终结论：",
        "报告正文：",
    ):
        assert forbidden not in message


def test_generic_worker_chat_mentions_are_not_worker_identity_inputs() -> None:
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(_RecordingReportRepository(), OpenClawWorkerChatClient(gateway))
    text = "@市场分析师 @news_analyst 请不要改变 structured workerId。"

    controller.send_worker_chat(
        request_id="req-mention-boundary",
        mode="generic_worker_chat",
        worker_id="risk_moderator",
        text=text,
        conversation_id="main",
        report_id=None,
    )

    assert gateway.session_metadata[0]["workerId"] == "risk_moderator"
    assert gateway.session_metadata[0]["agentId"] == "risk_moderator"
    assert gateway.session_metadata[0]["sessionKey"] == "agent:risk_moderator:generic:main"
    assert gateway.chat_calls[0]["message"] == text


def test_generic_worker_chat_uses_native_chat_not_report_qa_or_worker_run() -> None:
    gateway = _RecordingRpcGateway()
    controller = WorkerChatController(_RecordingReportRepository(), OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-native-chat",
        mode="generic_worker_chat",
        worker_id="news_analyst",
        text="只分析新闻。",
        conversation_id="main",
        report_id=None,
    )

    assert [method for method, _params in gateway.calls] == ["sessions.create", "chat.send"]
    assert "report_qa.chat_send" not in [method for method, _params in gateway.calls]
    assert "agent.runSingleWorker" not in [method for method, _params in gateway.calls]


class _RecordingReportRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_report(self, report_id: str) -> object:
        self.calls.append(f"get_report:{report_id}")
        raise AssertionError("generic worker chat must not read saved reports")

    def read_markdown(self, report_id: str) -> str:
        self.calls.append(f"read_markdown:{report_id}")
        raise AssertionError("generic worker chat must not read report markdown")


class _RecordingWorkerChatGateway:
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


class _RecordingRpcGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        session_key = str(metadata["sessionKey"])
        self.calls.append(
            (
                "sessions.create",
                {
                    "key": session_key,
                    "label": str(metadata["label"]),
                    "agentId": str(metadata["agentId"]),
                },
            )
        )
        return session_key

    def worker_chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        self.calls.append(
            (
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": message,
                    "deliver": False,
                    "idempotencyKey": idempotency_key,
                },
            )
        )
        return {"text": "worker answer"}

    def report_qa_chat_send(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(("report_qa.chat_send", dict(kwargs)))
        raise AssertionError("generic worker chat must not call legacy report QA")

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("agent.runSingleWorker", payload))
        raise AssertionError("generic worker chat must not call report workflow runner")
