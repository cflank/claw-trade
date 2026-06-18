from __future__ import annotations

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.report_worker_chat_context import ReportWorkerChatContextResolver
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient


def test_pm_worker_uses_pm_final_conclusion_when_pm_appendix_is_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo = _repo_with_saved_report(tmp_path, pm_final_conclusion="PM 结论：等待回撤后分批建仓。")

    context = ReportWorkerChatContextResolver(repo).build_report_context(
        report_id="report-1",
        worker_id="portfolio_manager",
        question="最终怎么执行？",
    )

    assert "PM 最终结论" in context.text
    assert "等待回撤后分批建仓" in context.text


def test_pm_worker_missing_appendix_does_not_block_controller_when_conclusion_exists(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo = _repo_with_saved_report(tmp_path, pm_final_conclusion="PM 结论：维持观察。")
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(repo, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-pm",
        mode="report_worker_chat",
        worker_id="portfolio_manager",
        text="结论有什么缺口？",
        conversation_id="reader",
        report_id="report-1",
    )

    message = str(gateway.chat_calls[0]["message"])
    assert "PM 结论：维持观察。" in message
    assert gateway.session_metadata[0]["agentId"] == "portfolio_manager"


def test_pm_worker_missing_appendix_and_pm_conclusion_still_uses_saved_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo = _repo_with_saved_report(tmp_path, pm_final_conclusion=None)

    context = ReportWorkerChatContextResolver(repo).build_report_context(
        report_id="report-1",
        worker_id="portfolio_manager",
        question="最终执行建议是什么？",
    )

    assert "已保存正式报告片段" in context.text
    assert "正式报告：PM 已给出最终执行建议。" in context.text


def _repo_with_saved_report(tmp_path, *, pm_final_conclusion: str | None) -> ReportRepository:  # type: ignore[no-untyped-def]
    asset_dir = tmp_path / "run-1" / "reports" / "assets"
    asset_dir.mkdir(parents=True)
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告\n\n正式报告：PM 已给出最终执行建议。",
        pm_final_conclusion=pm_final_conclusion,
        asset_dir=asset_dir,
    )
    return repo


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
