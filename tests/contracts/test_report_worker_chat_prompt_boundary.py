from __future__ import annotations

from pathlib import Path

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_catalog import ALLOWED_WORKER_CHAT_CATALOG
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient


def test_report_worker_chat_rules_are_in_chat_worker_bootstrap_agents() -> None:
    required_phrases = (
        "## 报告追问模式",
        "【claw-trade report_worker_chat】",
        "不重新执行报告工作流",
        "不改写报告",
        "不要调用数据、搜索、交易或消息工具",
        "回答必须保持当前 worker 身份和职责边界",
    )

    for worker in ALLOWED_WORKER_CHAT_CATALOG:
        text = (Path("agents") / worker.worker_id / "AGENTS.md").read_text(encoding="utf-8")
        for phrase in required_phrases:
            assert phrase in text, f"{worker.worker_id} AGENTS.md missing {phrase!r}"


def test_report_worker_chat_message_is_agent_marker_envelope(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
        request_id="req-1",
        mode="report_worker_chat",
        worker_id="risk_moderator",
        text="最大风险是什么？",
        conversation_id="reader",
        report_id="report-1",
    )

    message = str(gateway.chat_calls[0]["message"])
    assert "【claw-trade report_worker_chat】" in message
    assert "【SavedReport】" in message
    assert "链上风险需要关注" in message
    assert "【UserQuestion】\n最大风险是什么？" in message
    for forbidden in (
        "RuntimeTarget",
        "[ApprovedMaterials]",
        "single_worker_minimal",
        "promptProfile",
        "promptVariables",
        "toolPolicy",
    ):
        assert forbidden not in message


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
