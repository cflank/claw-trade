from __future__ import annotations

from dataclasses import dataclass

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.ui_contracts.enums import ChatContextKind


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[object] = []
        self.cancelled_runs: list[str] = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.requests.append(request)
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()

    def cancel_run(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        return True


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls = 0

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, request_id)
        self.calls += 1
        return {"text": f"echo:{text}"}


class _FailPdfRenderer:
    def render(self, markdown: str) -> bytes:
        _ = markdown
        raise RuntimeError("pdf failed")


class _NoopChannelBridge:
    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        _ = probe
        return {"state": "connected", "canSendText": True, "canSendFile": True}

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        _ = (channel_kind, text, dedupe_key, target, account_id)
        return {"sent": True}

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
        _ = (request_id, report_id, channel_kind, file_name, payload, file_path, target, account_id)
        return {"sent": True, "messageId": "m1"}


def _build_controller() -> tuple[ChatController, ReportTaskQueue, _FakeWorkflowRunner, _FakeChatTransport]:
    runner = _FakeWorkflowRunner()
    transport = _FakeChatTransport()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    return controller, queue, runner, transport


def test_normal_chat_report_confirm_and_followup_do_not_mix_workflow() -> None:
    controller, _queue, runner, transport = _build_controller()

    chat = controller.send_chat_message(request_id="e2e-chat-1", context_id="ctx-1", text="你好")
    assert chat["assistantReply"] == "echo:你好"
    assert transport.calls == 1
    assert runner.calls == 0

    draft = controller.send_chat_message(request_id="e2e-chat-2", context_id="ctx-1", text="/report BTC")
    assert "confirmationCard" in draft
    assert runner.calls == 0

    confirm = controller.confirm_intent_draft(
        request_id="e2e-chat-3",
        draft_id=draft["confirmationCard"]["draftId"],
        decision="confirm",
    )
    assert confirm["status"] == "confirmed"
    assert runner.calls == 1

    controller.switch_chat_context(
        context_id="ctx-report-read",
        kind=ChatContextKind.REPORT_READING,
        active_report_id="report-1",
    )
    followup = controller.send_chat_message(
        request_id="e2e-chat-4",
        context_id="ctx-report-read",
        text="这份报告里最大的风险是什么？",
    )
    assert "confirmationCard" not in followup
    assert transport.calls == 2
    assert runner.calls == 1


def test_failed_report_not_in_history() -> None:
    _controller, queue, _runner, _transport = _build_controller()
    enqueued = queue.enqueue_report_task(
        request_id="e2e-q-1",
        task_input={
            "instrumentCode": "AAPL",
            "instrumentName": "Apple",
            "market": "US",
            "companyName": "Apple",
            "currencySymbol": "$",
            "startDate": "2026-05-01",
            "endDate": "2026-05-19",
            "currentDate": "2026-05-19",
            "workflowSettings": {
                "maxDebateRounds": 1,
                "maxRiskDiscussRounds": 1,
                "frontlineExecutionMode": "parallel",
                "defaultProfile": "US",
                "defaultMarket": "US",
                "defaultCurrency": "USD",
                "defaultCurrencySymbol": "$",
            },
        },
        source="manual",
    )
    queue.handle_report_failed(enqueued["task"]["taskId"], RuntimeError("workflow failed"))
    assert queue.list_saved_reports_for_user() == []


def test_confirm_scheduled_report_and_price_alert_return_real_dto() -> None:
    controller, _queue, _runner, _transport = _build_controller()
    schedule_draft = controller.create_intent_draft(
        request_id="e2e-s-1",
        source_message_id="m-1",
        text="每天 08:00 给我 BTC 报告",
    )
    schedule_confirm = controller.confirm_intent_draft(
        request_id="e2e-s-2",
        draft_id=schedule_draft["draft"]["draftId"],
        decision="confirm",
    )
    assert schedule_confirm["scheduledReport"].scheduledReportId.startswith("schedule-")

    alert_draft = controller.create_intent_draft(
        request_id="e2e-a-1",
        source_message_id="m-2",
        text="BTC 高于 70000 提醒我",
    )
    alert_confirm = controller.confirm_intent_draft(
        request_id="e2e-a-2",
        draft_id=alert_draft["draft"]["draftId"],
        decision="confirm",
    )
    assert alert_confirm["priceAlert"].priceAlertId.startswith("alert-")


def test_pdf_file_failure_returns_file_send_unsupported() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-pdf-fail",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n正文",
    )
    service = ReportNotificationService(
        repo,
        CompletionSummaryBuilder(repo),
        PdfExportService(repo, renderer=_FailPdfRenderer()),
        _NoopChannelBridge(),
    )
    result = service.request_full_report_file("r-pdf-fail", "req-pdf-fail", target="sender-1")
    assert result["sent"] is False
    assert result["code"] == "FILE_SEND_UNSUPPORTED"


def test_wechat_report_confirm_flow_and_running_cancel_stops_task() -> None:
    controller, queue, _runner, _transport = _build_controller()
    draft = controller.send_chat_message(request_id="wx-1", context_id="wx-ctx", text="/report TSLA")
    confirm = controller.confirm_intent_draft(
        request_id="wx-2",
        draft_id=draft["confirmationCard"]["draftId"],
        decision="confirm",
    )
    task_id = confirm["task"]["taskId"]
    cancelled = queue.cancel_report_task(request_id="wx-3", task_id=task_id)

    assert cancelled["task"]["status"] == "cancelled"
    assert cancelled["queueSnapshot"]["runningTask"] is None
