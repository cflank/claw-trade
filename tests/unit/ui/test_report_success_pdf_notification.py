from __future__ import annotations

from types import SimpleNamespace

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder
from claw_trade.web.state import _handle_completed_workflow_report, _save_completed_workflow_report


class _FailPdfRenderer:
    def render(self, markdown: str) -> bytes:
        raise RuntimeError("pdf failed")


class _NoopChannelBridge:
    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        return {"state": "disconnected", "canSendText": False, "canSendFile": False}

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        return {"sent": False}

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes | None = None,
        file_path=None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        return {"sent": False}


def test_report_stays_saved_when_pdf_export_fails_and_completion_notification_still_works() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-ok",
        instrument_code="AAPL",
        market="US",
        title="AAPL 报告",
        markdown="# 报告\n正文",
    )
    notifications: list[tuple[str, str]] = []
    pdf_service = PdfExportService(repo, renderer=_FailPdfRenderer())
    pdf_record = pdf_service.export_saved_markdown_to_pdf("r-ok", request_id="pdf-fail")
    assert pdf_record.state == "failed"
    assert len(repo.list_saved_reports()) == 1

    summary_builder = CompletionSummaryBuilder(repo)
    service = ReportNotificationService(
        repo,
        summary_builder,
        pdf_service,
        _NoopChannelBridge(),
        in_app_notifier=lambda report_id, text: notifications.append((report_id, text)),
    )
    result = service.notify_report_completion("r-ok")
    assert result["sent"] is False
    assert any("报告已完成" in item[1] for item in notifications)


def test_completed_workflow_save_sends_notification_and_appends_origin_chat(tmp_path) -> None:
    run_dir = tmp_path / "run-1"
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "final-report.md").write_text(
        (
            "# BTC 报告\n\n"
            "## 投资建议\n"
            "维持观察，等待突破确认。\n\n"
            "核心理由：日线趋势改善\n\n"
            "主要风险：估值波动\n"
        ),
        encoding="utf-8",
    )
    repository = ReportRepository()

    class _NotificationSpy:
        def __init__(self) -> None:
            self.report_ids: list[str] = []
            self.targets: list[tuple[str | None, str | None, str]] = []

        def notify_report_completion(
            self,
            report_id: str,
            *,
            channel_kind: str = "wechat_clawbot",
            target: str | None = None,
            account_id: str | None = None,
        ) -> dict[str, object]:
            self.report_ids.append(report_id)
            self.targets.append((target, account_id, channel_kind))
            return {"sent": True}

    class _ChatSpy:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        def append_report_completed_message(self, **kwargs):  # type: ignore[no-untyped-def]
            self.messages.append(dict(kwargs))

    notification = _NotificationSpy()
    chat = _ChatSpy()
    task = SimpleNamespace(
        task_id="task-1",
        run_id="run-1",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
        origin_context_id="wechat_clawbot:account-1:sender-1",
    )
    workflow_state = SimpleNamespace(
        run_id="run-1",
        run_dir=run_dir,
        updated_at="2026-05-25T15:15:34Z",
    )

    _handle_completed_workflow_report(
        repository,
        notification,  # type: ignore[arg-type]
        chat,  # type: ignore[arg-type]
        task=task,
        workflow_state=workflow_state,
    )

    saved_reports = repository.list_saved_reports()
    assert [item["id"] for item in saved_reports] == ["run-1"]
    assert saved_reports[0]["summarySnippet"] == "维持观察，等待突破确认。"
    assert notification.report_ids == ["run-1"]
    assert notification.targets == [("sender-1", "account-1", "wechat_clawbot")]
    assert chat.messages == [
        {
            "context_id": "wechat_clawbot:account-1:sender-1",
            "report_id": "run-1",
            "task_id": "task-1",
            "text": (
                "报告已完成。\n"
                "最终结论：维持观察，等待突破确认。\n"
                "核心理由：日线趋势改善\n"
                "主要风险：估值波动\n"
                "查看完整报告以获取全部分析细节。\n"
                "完整报告可在设备界面查看。"
            ),
        }
    ]


def test_select_handoff_saved_report_includes_boundary_notice_without_polluting_summary(tmp_path) -> None:
    run_dir = tmp_path / "run-select-report"
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "final-report.md").write_text(
        "# 贵州茅台（600519.SH）投资研究报告\n\n"
        "## 一、投资结论与组合动作\n"
        "组合经理最终建议：卖出，等待基本面重新验证。\n",
        encoding="utf-8",
    )
    repository = ReportRepository()
    task = SimpleNamespace(
        task_id="task-select",
        run_id="run-select-report",
        instrument_code="600519.SH",
        instrument_name="贵州茅台",
        market="CN_A",
        selection_stage_marker="selection_report_handoff",
    )
    workflow_state = SimpleNamespace(
        run_id="run-select-report",
        run_dir=run_dir,
        updated_at="2026-06-17T10:00:00Z",
    )

    report_id = _save_completed_workflow_report(repository, task=task, workflow_state=workflow_state)

    saved = repository.get_report(report_id)
    assert saved is not None
    assert "本报告由 select 候选股票触发生成" in saved.markdown
    assert "不代表买入建议" in saved.markdown
    assert saved.summary_snippet == "组合经理最终建议：卖出，等待基本面重新验证。"


def test_regular_saved_report_does_not_include_select_boundary_notice(tmp_path) -> None:
    run_dir = tmp_path / "run-regular-report"
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "final-report.md").write_text(
        "# BTC 报告\n\n"
        "## 投资建议\n"
        "维持观察，等待突破确认。\n",
        encoding="utf-8",
    )
    repository = ReportRepository()
    task = SimpleNamespace(
        task_id="task-regular",
        run_id="run-regular-report",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
    )
    workflow_state = SimpleNamespace(
        run_id="run-regular-report",
        run_dir=run_dir,
        updated_at="2026-06-17T10:00:00Z",
    )

    report_id = _save_completed_workflow_report(repository, task=task, workflow_state=workflow_state)

    saved = repository.get_report(report_id)
    assert saved is not None
    assert "本报告由 select 候选股票触发生成" not in saved.markdown
