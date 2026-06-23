from __future__ import annotations

import pytest

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient


def test_worker_chat_controller_uses_worker_chat_seam_not_report_qa() -> None:
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(ReportRepository(), OpenClawWorkerChatClient(gateway))

    reply = controller.send_worker_chat(
        request_id="req-1",
        mode="generic_worker_chat",
        worker_id="market_analyst",
        text="怎么看今天盘面？",
        conversation_id="main",
        report_id=None,
    )

    assert reply.kind == "worker_chat_reply"
    assert reply.workerDisplayName == "市场分析师"
    assert reply.text == "worker answer"
    assert gateway.session_metadata == [
        {
            "scope": "generic_worker_chat",
            "workerId": "market_analyst",
            "agentId": "ui_worker_chat",
            "sessionKey": "agent:ui_worker_chat:generic:market_analyst:main",
            "label": "agent:ui_worker_chat:generic:market_analyst:main",
        }
    ]
    assert gateway.chat_calls[0]["session_key"] == "agent:ui_worker_chat:generic:market_analyst:main"
    assert gateway.chat_calls[0]["idempotency_key"] == "req-1"
    assert "【claw-trade generic_worker_chat】" in str(gateway.chat_calls[0]["message"])
    assert "worker_id: market_analyst" in str(gateway.chat_calls[0]["message"])
    assert "worker_display_name: 市场分析师" in str(gateway.chat_calls[0]["message"])
    assert "不要输出 NO_REPLY、ANNOUNCE_SKIP 或 REPLY_SKIP" in str(gateway.chat_calls[0]["message"])
    assert "【UserMessage】\n怎么看今天盘面？" in str(gateway.chat_calls[0]["message"])
    assert gateway.report_qa_calls == 0


def test_report_worker_chat_sends_report_context_through_worker_session(tmp_path) -> None:  # type: ignore[no-untyped-def]
    reports_dir = tmp_path / "run-1" / "reports"
    asset_dir = reports_dir / "assets"
    asset_dir.mkdir(parents=True)
    appendix_dir = reports_dir / "worker-appendix"
    appendix_dir.mkdir()
    appendix_dir.joinpath("07-risk_moderator.md").write_text("风险经理附录：链上风险需要关注。", encoding="utf-8")
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n链上风险需要关注。",
        pm_final_conclusion="谨慎观察",
        asset_dir=asset_dir,
    )
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(repo, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-2",
        mode="report_worker_chat",
        worker_id="risk_moderator",
        text="最大风险是什么？",
        conversation_id="reader",
        report_id="report-1",
    )

    assert gateway.session_metadata[0]["agentId"] == "risk_moderator"
    assert gateway.session_metadata[0]["sessionKey"] == "agent:risk_moderator:report:report-1:reader"
    sent = gateway.chat_calls[0]
    assert sent["session_key"] == "agent:risk_moderator:report:report-1:reader"
    message = str(sent["message"])
    assert "【claw-trade report_worker_chat】" in message
    assert "worker_display_name: 风险经理" in message
    assert "不要输出 NO_REPLY、ANNOUNCE_SKIP 或 REPLY_SKIP" in message
    assert "【SavedReport】" in message
    assert "链上风险需要关注" in message
    assert "【UserQuestion】\n最大风险是什么？" in message
    assert gateway.report_qa_calls == 0


def test_same_request_and_same_payload_replays_saved_worker_reply() -> None:
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(ReportRepository(), OpenClawWorkerChatClient(gateway))

    first = controller.send_worker_chat(
        request_id="req-repeat",
        mode="generic_worker_chat",
        worker_id="market_analyst",
        text="怎么看今天盘面？",
        conversation_id="main",
        report_id=None,
    )
    second = controller.send_worker_chat(
        request_id="req-repeat",
        mode="generic_worker_chat",
        worker_id="market_analyst",
        text="怎么看今天盘面？",
        conversation_id="main",
        report_id=None,
    )

    assert second == first
    assert len(gateway.chat_calls) == 1


def test_same_request_different_payload_conflicts_instead_of_reusing_reply_cache() -> None:
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(ReportRepository(), OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-conflict",
        mode="generic_worker_chat",
        worker_id="market_analyst",
        text="怎么看今天盘面？",
        conversation_id="main",
        report_id=None,
    )

    with pytest.raises(UiProductError) as exc:
        controller.send_worker_chat(
            request_id="req-conflict",
            mode="generic_worker_chat",
            worker_id="market_analyst",
            text="换一个问题",
            conversation_id="main",
            report_id=None,
        )

    assert exc.value.code == "WORKER_CHAT_IDEMPOTENCY_CONFLICT"
    assert len(gateway.chat_calls) == 1


def test_worker_chat_retries_once_when_openclaw_returns_no_visible_reply() -> None:
    gateway = _RecordingWorkerChatGateway()
    gateway.worker_chat_errors.append(RuntimeError("assistant_unavailable"))
    gateway.worker_chat_replies.append({"text": "visible worker answer"})
    controller = WorkerChatController(ReportRepository(), OpenClawWorkerChatClient(gateway))

    reply = controller.send_worker_chat(
        request_id="req-visible-retry",
        mode="generic_worker_chat",
        worker_id="market_analyst",
        text="怎么看今天盘面？",
        conversation_id="main",
        report_id=None,
    )

    assert reply.text == "visible worker answer"
    assert len(gateway.chat_calls) == 2
    assert gateway.chat_calls[0]["idempotency_key"] == "req-visible-retry"
    assert gateway.chat_calls[1]["idempotency_key"] == "req-visible-retry:visible-reply-retry"
    assert "上一次没有返回可显示回答" in str(gateway.chat_calls[1]["message"])
    assert "不要输出 NO_REPLY、ANNOUNCE_SKIP 或 REPLY_SKIP" in str(gateway.chat_calls[1]["message"])


class _RecordingWorkerChatGateway:
    def __init__(self) -> None:
        self.session_metadata: list[dict[str, object]] = []
        self.chat_calls: list[dict[str, object]] = []
        self.worker_chat_errors: list[Exception] = []
        self.worker_chat_replies: list[dict[str, str]] = []
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
        if self.worker_chat_errors:
            raise self.worker_chat_errors.pop(0)
        if self.worker_chat_replies:
            return self.worker_chat_replies.pop(0)
        return {"text": "worker answer"}

    def report_qa_chat_send(self, **kwargs: object) -> dict[str, str]:
        _ = kwargs
        self.report_qa_calls += 1
        return {"text": "wrong path"}
