from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.production import host_locks, paths
from claw_trade.selection.controller import SelectCommandCode, SelectCommandResult
from claw_trade.ui_backend.channel_text_inbound import (
    ChannelReplyTarget,
    ChannelTextInboundController,
    ChannelTextMessage,
    _looks_like_select_command,
)
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@pytest.fixture(autouse=True)
def installed_report_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_lock = tmp_path / "report-active.lock"
    report_lock.touch()
    report_lock.chmod(0o660)
    monkeypatch.setattr(paths, "REPORT_ACTIVE_LOCK_PATH", report_lock)
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid(), os.getgid()))


@dataclass
class _FakeState:
    status: str = "frontline_running"


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.requests.append(request)
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeState:
        _ = run_id
        return _FakeState()


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def chat_send(self, *, context_id: str, text: str, request_id: str):  # type: ignore[no-untyped-def]
        self.calls.append({"contextId": context_id, "text": text, "requestId": request_id})
        return {"text": f"echo:{text}"}


@dataclass
class _FakeSelectionController:
    calls: int = 0

    def handle_select_command(
        self,
        *,
        raw_text: str,
        request_id: str,
        user_id: str | None = None,
        wait_for_data_refresh: bool = False,
    ) -> SelectCommandResult:
        _ = wait_for_data_refresh
        self.calls += 1
        return SelectCommandResult(
            code=SelectCommandCode.COMPLETED,
            chat_text="`/select` 微信测试结果",
            select_workflow_run_id="select-wechat-test-run",
            evidence_path=Path("runs/selection/workflows/select-wechat-test-run/evidence.json"),
            reader_report_markdown="# 选股结果报告\n\n## 候选事实表\n\n| 股票代码 | 股票名称 |\n| --- | --- |\n| 600519.SH | 贵州茅台 |",
        )


def _controller(
    *,
    selection_controller: _FakeSelectionController | None = None,
    background_submitter=None,  # type: ignore[no-untyped-def]
    send_channel_text=None,  # type: ignore[no-untyped-def]
    request_full_report_file=None,  # type: ignore[no-untyped-def]
    request_selection_report_file=None,  # type: ignore[no-untyped-def]
    ask_report_question=None,  # type: ignore[no-untyped-def]
    bind_notification_recipient=None,  # type: ignore[no-untyped-def]
    remember_current_notification_recipient=None,  # type: ignore[no-untyped-def]
    resolve_latest_delivered_report=None,  # type: ignore[no-untyped-def]
    company_name_resolver=None,  # type: ignore[no-untyped-def]
) -> tuple[ChannelTextInboundController, _FakeRunner, _FakeChatTransport]:
    runner = _FakeRunner()
    chat_transport = _FakeChatTransport()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(chat_transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue, company_name_resolver=company_name_resolver),
        queue=queue,
        settings=ReportWorkflowSettings(),
        selection_controller=selection_controller,
    )
    return (
        ChannelTextInboundController(
            chat_controller,
            request_full_report_file=request_full_report_file,
            background_submitter=background_submitter,
            send_channel_text=send_channel_text,
            request_selection_report_file=request_selection_report_file,
            ask_report_question=ask_report_question,
            bind_notification_recipient=bind_notification_recipient,
            remember_current_notification_recipient=remember_current_notification_recipient,
            resolve_latest_delivered_report=resolve_latest_delivered_report,
        ),
        runner,
        chat_transport,
    )


def _message(request_id: str, text: str, *, sender_id: str = "sender-1") -> ChannelTextMessage:
    return ChannelTextMessage(
        request_id=request_id,
        channel_kind="wechat_clawbot",
        account_id="account-1",
        sender_id=sender_id,
        text=text,
        message_id=f"m-{request_id}",
    )


def test_notification_binding_command_uses_inbound_account_and_sender_before_chat_routing() -> None:
    calls: list[tuple[str, str | None, str]] = []
    controller, runner, chat_transport = _controller(
        bind_notification_recipient=lambda text, account_id, sender_id: (
            calls.append((text, account_id, sender_id))
            or {"bound": True, "message": "绑定成功，将发送待发送报告。"}
        )
    )

    result = controller.handle_message(_message("bind-1", "绑定通知 A1B2C3D4"))

    assert result == {
        "handled": True,
        "replyText": "绑定成功，将发送待发送报告。",
        "state": "notification_bound",
    }
    assert calls == [("绑定通知 A1B2C3D4", "account-1", "sender-1")]
    assert runner.calls == 0
    assert chat_transport.calls == []


def test_ordinary_wechat_text_is_left_to_openclaw_native_channel() -> None:
    background_jobs = []
    sent_texts = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    controller, runner, chat_transport = _controller(
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
    )
    result = controller.handle_message(_message("r-1", "你好"))
    assert result == {"handled": False}
    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["context"]["contextId"] == "wechat_clawbot:account-1:sender-1"
    assert snapshot["context"]["title"] == "微信聊天"
    assert snapshot["messages"] == []
    assert runner.calls == 0
    assert chat_transport.calls == []
    assert sent_texts == []
    assert background_jobs == []


def test_report_reading_wechat_question_uses_report_qa_instead_of_native_chat() -> None:
    background_jobs = []
    sent_texts = []
    question_calls = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    def _ask_report_question(report_id, text, request_id, context_id):  # type: ignore[no-untyped-def]
        question_calls.append(
            {
                "reportId": report_id,
                "text": text,
                "requestId": request_id,
                "contextId": context_id,
            }
        )
        return {"text": "报告里说，主要风险是估值波动。"}

    controller, runner, chat_transport = _controller(
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
        ask_report_question=_ask_report_question,
    )
    controller._chat_controller.append_report_completed_message(  # noqa: SLF001
        context_id="wechat_clawbot:account-1:sender-1",
        report_id="run-1",
        text="报告已完成。",
    )

    result = controller.handle_message(_message("r-report-q", "估值风险是什么？"))

    assert result == {
        "handled": True,
        "replyText": "收到，正在查这份报告。",
        "state": "report_question_processing",
        "deferFinalReply": True,
    }
    assert chat_transport.calls == []
    assert runner.calls == 0
    assert question_calls == []
    assert sent_texts == []
    assert len(background_jobs) == 1

    background_jobs[0]()

    assert question_calls == [
        {
            "reportId": "run-1",
            "text": "估值风险是什么？",
            "requestId": "r-report-q",
            "contextId": "wechat_clawbot:account-1:sender-1",
        }
    ]
    assert sent_texts == [
        {
            "text": "报告里说，主要风险是估值波动。",
            "dedupeKey": "channel-report-question:r-report-q",
            "target": ChannelReplyTarget(
                channel_kind="wechat_clawbot",
                account_id="account-1",
                sender_id="sender-1",
            ),
        }
    ]
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]][-4:] == [
        "报告已完成。",
        "估值风险是什么？",
        "收到，正在查这份报告。",
        "报告里说，主要风险是估值波动。",
    ]


def test_help_command_returns_usage_without_openclaw_chat() -> None:
    controller, runner, chat_transport = _controller()
    result = controller.handle_message(_message("r-help", "help $superpowers:using-superpowers"))
    assert result["handled"] is True
    assert result["state"] == "replied"
    assert "/report TSLA：美股报告" in result["replyText"]
    assert "/report 600519.SH：A股报告" in result["replyText"]
    assert "/select：A股选股结果" in result["replyText"]
    assert "/select 2：加密选股结果" in result["replyText"]
    assert "/select 2 刷新：刷新加密数据" in result["replyText"]
    assert "发送完整报告：发PDF" in result["replyText"]
    assert "$superpowers:using-superpowers" not in result["replyText"]
    lines = result["replyText"].splitlines()
    assert len(lines) <= 14
    assert max(len(line) for line in lines) <= 28
    assert runner.calls == 0
    assert chat_transport.calls == []
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]] == [
        "help $superpowers:using-superpowers",
        result["replyText"],
    ]


def test_latest_wechat_snapshot_reflects_cleared_chat_session() -> None:
    controller, _, _ = _controller()
    controller.handle_message(_message("r-help-clear", "help"))

    context_id = "wechat_clawbot:account-1:sender-1"
    assert controller.latest_conversation_snapshot()["messages"]

    controller._chat_controller.clear_chat_session(context_id=context_id)  # noqa: SLF001
    snapshot = controller.latest_conversation_snapshot()

    assert snapshot["context"]["contextId"] == context_id
    assert snapshot["context"]["title"] == "微信聊天"
    assert snapshot["messages"] == []


@pytest.mark.parametrize("text", ("/select 1", "/select 2", "/select crypto", "/select 刷新", "/select 2 刷新"))
def test_channel_select_detector_accepts_market_tokens(text: str) -> None:
    assert _looks_like_select_command(text)


@pytest.mark.parametrize("text", ("/select refresh 2", "/select refresh crypto"))
def test_channel_select_detector_keeps_refresh_before_market_invalid(text: str) -> None:
    assert not _looks_like_select_command(text)


def test_select_command_runs_in_background_and_updates_ui_snapshot_without_openclaw_chat() -> None:
    selection = _FakeSelectionController()
    background_jobs = []
    controller, runner, chat_transport = _controller(
        selection_controller=selection,
        background_submitter=background_jobs.append,
    )

    result = controller.handle_message(_message("r-select", "/select"))

    assert result == {
        "handled": True,
        "replyText": "收到，正在执行 /select 选股；完成后会显示在工作台。",
        "state": "selection_processing",
    }
    snapshot = controller.latest_conversation_snapshot()
    assert [item["kind"] for item in snapshot["messages"]] == ["plain", "selection_refreshing"]
    assert [item["text"] for item in snapshot["messages"]] == [
        "/select",
        "已收到 `/select`，正在执行选股；完成后会显示在这里。",
    ]
    assert selection.calls == 0
    assert len(background_jobs) == 1

    background_jobs[0]()

    snapshot = controller.latest_conversation_snapshot()
    assert [item["kind"] for item in snapshot["messages"]] == ["plain", "selection_refreshing", "selection_result"]
    assert snapshot["messages"][-1]["text"] == "`/select` 微信测试结果"
    assert snapshot["messages"][-1]["selection"]["workflowRunId"] == "select-wechat-test-run"
    assert "候选事实表" in snapshot["messages"][-1]["selection"]["readerReportMarkdown"]
    assert selection.calls == 1
    assert runner.calls == 0
    assert chat_transport.calls == []


def test_select_command_pushes_wechat_summary_and_sends_selection_pdf_on_request() -> None:
    selection = _FakeSelectionController()
    background_jobs = []
    sent_texts = []
    sent_files = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    def _send_selection_file(workflow_run_id, markdown, request_id, target):  # type: ignore[no-untyped-def]
        sent_files.append(
            {
                "workflowRunId": workflow_run_id,
                "markdown": markdown,
                "requestId": request_id,
                "target": target,
            }
        )
        return {"sent": True, "userMessage": "完整选股报告已发送。"}

    controller, runner, chat_transport = _controller(
        selection_controller=selection,
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
        request_selection_report_file=_send_selection_file,
    )

    result = controller.handle_message(_message("r-select-summary", "/select"))
    assert result["state"] == "selection_processing"
    assert sent_texts == []

    background_jobs[0]()

    assert len(sent_texts) == 1
    assert "`/select` 微信测试结果" in sent_texts[0]["text"]
    assert "回复“发送完整报告”" in sent_texts[0]["text"]
    assert sent_texts[0]["target"].sender_id == "sender-1"

    file_result = controller.handle_message(_message("r-select-file", "发送完整报告"))

    assert file_result == {"handled": True, "replyText": "收到，正在发送完整选股报告。", "state": "file_sending"}
    assert sent_files == []
    assert len(background_jobs) == 2
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["发送完整报告", "收到，正在发送完整选股报告。"]

    background_jobs[1]()

    assert len(sent_files) == 1
    assert sent_files[0]["workflowRunId"] == "select-wechat-test-run"
    assert "候选事实表" in sent_files[0]["markdown"]
    assert sent_files[0]["target"].sender_id == "sender-1"
    assert sent_texts[-1]["text"] == "完整选股报告已发送。"
    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["messages"][-1]["text"] == "完整选股报告已发送。"
    assert runner.calls == 0
    assert chat_transport.calls == []


def test_select_command_retries_known_wechat_send_failure_once() -> None:
    selection = _FakeSelectionController()
    background_jobs = []
    send_results = [
        {"sent": False, "resultKnown": True},
        {"sent": False, "resultKnown": True},
    ]
    sent_texts = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return send_results.pop(0)

    controller, _runner, _chat_transport = _controller(
        selection_controller=selection,
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
    )

    controller.handle_message(_message("r-select-retry", "/select 2"))
    background_jobs[0]()

    assert len(sent_texts) == 2
    assert sent_texts[0]["dedupeKey"] == sent_texts[1]["dedupeKey"]
    last_message = controller.latest_conversation_snapshot()["messages"][-1]
    assert last_message["kind"] == "plain"
    assert "发送到微信失败" in last_message["text"]


def test_select_command_records_unknown_wechat_send_result_without_duplicate_retry() -> None:
    background_jobs = []
    sent_texts = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": False, "resultKnown": False}

    controller, _runner, _chat_transport = _controller(
        selection_controller=_FakeSelectionController(),
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
    )

    controller.handle_message(_message("r-select-unknown", "/select 2"))
    background_jobs[0]()

    assert len(sent_texts) == 1
    last_message = controller.latest_conversation_snapshot()["messages"][-1]
    assert last_message["kind"] == "plain"
    assert "是否送达微信无法确认" in last_message["text"]


def test_select_command_records_wechat_send_exception_without_duplicate_retry() -> None:
    background_jobs = []
    send_calls = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        send_calls.append((text, dedupe_key, target))
        raise RuntimeError("gateway result unknown")

    controller, _runner, _chat_transport = _controller(
        selection_controller=_FakeSelectionController(),
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
    )

    controller.handle_message(_message("r-select-exception", "/select 2"))
    background_jobs[0]()

    assert len(send_calls) == 1
    last_message = controller.latest_conversation_snapshot()["messages"][-1]
    assert last_message["kind"] == "plain"
    assert "是否送达微信无法确认" in last_message["text"]


@pytest.mark.parametrize("second_result", ({"sent": False, "resultKnown": False}, RuntimeError()))
def test_select_command_records_unknown_after_known_failure(second_result: object) -> None:
    background_jobs = []
    send_results = iter(({"sent": False, "resultKnown": True}, second_result))
    send_calls = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        send_calls.append((text, dedupe_key, target))
        result = next(send_results)
        if isinstance(result, Exception):
            raise result
        return result

    controller, _runner, _chat_transport = _controller(
        selection_controller=_FakeSelectionController(),
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
    )

    controller.handle_message(_message("r-select-mixed", "/select 2"))
    background_jobs[0]()

    assert len(send_calls) == 2
    last_message = controller.latest_conversation_snapshot()["messages"][-1]
    assert last_message["kind"] == "plain"
    assert "是否送达微信无法确认" in last_message["text"]


def test_report_message_returns_confirmation_without_starting_workflow() -> None:
    controller, runner, _ = _controller()
    result = controller.handle_message(_message("r-2", "/report TSLA"))
    assert result["handled"] is True
    assert result["state"] == "awaiting_confirmation"
    assert "标的：TSLA" in result["replyText"]
    assert "回复“确认”" in result["replyText"]
    snapshot = controller.latest_conversation_snapshot()
    assert [item["kind"] for item in snapshot["messages"]] == ["plain", "confirmation_card"]
    assert snapshot["messages"][0]["text"] == "/report TSLA"
    assert snapshot["messages"][1]["cardId"] == "card-draft-1"
    assert snapshot["confirmationCards"]["card-draft-1"]["title"] == "请确认是否创建完整报告"
    assert runner.calls == 0


def test_report_message_acknowledges_before_sending_slow_confirmation_card() -> None:
    background_jobs = []
    sent_texts = []

    def _resolver(*, market: str, symbol_ids: tuple[str, ...]):
        assert market == "CN_A"
        assert symbol_ids == ("600638.SH",)
        return {"600638.SH": "新黄浦"}

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    controller, runner, _ = _controller(
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
        company_name_resolver=_resolver,
    )

    result = controller.handle_message(_message("r-report-slow-name", "/report 600638"))

    assert result == {
        "handled": True,
        "replyText": "收到，正在识别标的，请稍等。",
        "state": "confirmation_processing",
        "deferFinalReply": True,
    }
    assert sent_texts == []
    assert len(background_jobs) == 1
    assert runner.calls == 0

    background_jobs[0]()

    assert len(sent_texts) == 1
    assert "请确认是否创建完整报告" in sent_texts[0]["text"]
    assert "标的：600638.SH" in sent_texts[0]["text"]
    assert "名称：新黄浦" in sent_texts[0]["text"]
    assert sent_texts[0]["dedupeKey"] == "channel-confirmation-result:r-report-slow-name"
    snapshot = controller.latest_conversation_snapshot()
    assert [item["kind"] for item in snapshot["messages"]] == ["plain", "confirmation_card"]
    assert snapshot["confirmationCards"]["card-draft-1"]["instrumentName"] == "新黄浦"
    assert runner.calls == 0


def test_report_message_name_lookup_failure_sends_error_without_pending_confirmation() -> None:
    background_jobs = []
    sent_texts = []

    def _resolver(*, market: str, symbol_ids: tuple[str, ...]):
        assert market == "CN_A"
        assert symbol_ids == ("600638.SH",)
        return {}

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    controller, runner, _ = _controller(
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
        company_name_resolver=_resolver,
    )

    result = controller.handle_message(_message("r-report-bad-name", "/report 600638"))

    assert result["state"] == "confirmation_processing"
    background_jobs[0]()

    assert len(sent_texts) == 1
    assert "名称解析失败" in sent_texts[0]["text"]
    assert "回复“确认”" not in sent_texts[0]["text"]
    assert sent_texts[0]["dedupeKey"] == "channel-confirmation-result:r-report-bad-name"
    snapshot = controller.latest_conversation_snapshot()
    assert "confirmationCards" not in snapshot or snapshot["confirmationCards"] == {}
    assert snapshot["messages"][-1]["text"] == sent_texts[0]["text"]
    assert runner.calls == 0


def test_sched_alias_returns_confirmation_without_starting_workflow() -> None:
    controller, runner, _ = _controller()
    result = controller.handle_message(_message("r-sched-1", "/sched TSLA 每天 08:00"))
    assert result["handled"] is True
    assert result["state"] == "awaiting_confirmation"
    assert "请确认是否创建定时报告" in result["replyText"]
    assert "标的：TSLA" in result["replyText"]
    assert runner.calls == 0


def test_confirmed_wechat_schedule_becomes_current_notification_recipient() -> None:
    remembered: list[tuple[str | None, str]] = []
    controller, runner, _ = _controller(
        remember_current_notification_recipient=lambda account_id, sender_id: remembered.append(
            (account_id, sender_id)
        ),
        company_name_resolver=lambda **_kwargs: {"TSLA": "Tesla"},
    )

    controller.handle_message(_message("r-sched-bind-1", "/sched TSLA 每天 08:00"))
    result = controller.handle_message(_message("r-sched-bind-2", "确认"))

    assert result["state"] == "confirmed"
    assert remembered == [("account-1", "sender-1")]
    assert runner.calls == 0


def test_confirm_reply_starts_existing_report_workflow() -> None:
    controller, runner, _ = _controller()
    controller.handle_message(_message("r-3", "/report TSLA"))
    result = controller.handle_message(_message("r-4", "确认"))
    assert result["handled"] is True
    assert result["state"] == "confirmed"
    assert "报告任务已提交" in result["replyText"]
    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["context"]["kind"] == "task_following"
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["确认", "报告任务已启动，正在生成。"]
    assert runner.calls == 1
    assert runner.requests[0].entry_point.value == "report_command"


@pytest.mark.parametrize(("text", "suffix"), (("hi", "plain"), ("进度到哪了", "progress")))
def test_running_task_wechat_message_replies_inline_without_agent_or_background(text: str, suffix: str) -> None:
    background_jobs = []
    sent_texts = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    controller, runner, chat_transport = _controller(
        background_submitter=background_jobs.append,
        send_channel_text=_send_text,
    )
    controller.handle_message(_message(f"r-running-inline-report-{suffix}", "/report TSLA"))
    if background_jobs:
        background_jobs.pop(0)()
    controller.handle_message(_message(f"r-running-inline-confirm-{suffix}", "确认"))
    background_jobs.clear()
    sent_texts.clear()

    result = controller.handle_message(_message(f"r-running-inline-{suffix}", text))

    assert result["handled"] is True
    assert result["state"] == "replied"
    assert result["replyText"].startswith("报告正在生成中")
    if text == "hi":
        assert "普通聊天暂时无法处理" in result["replyText"]
    else:
        assert "普通聊天暂时无法处理" not in result["replyText"]
    assert "deferFinalReply" not in result
    assert background_jobs == []
    assert sent_texts == []
    assert chat_transport.calls == []
    assert runner.calls == 1


def test_cancel_reply_does_not_start_workflow() -> None:
    controller, runner, _ = _controller()
    controller.handle_message(_message("r-5", "/report TSLA"))
    result = controller.handle_message(_message("r-6", "取消"))
    assert result == {"handled": True, "replyText": "已取消。", "state": "cancelled"}
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["取消", "已取消。"]
    assert runner.calls == 0


def test_wechat_report_completion_appends_completed_card_to_same_conversation(tmp_path: Path) -> None:
    runner = _FakeRunner()
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    def load_completed_state(run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            status="completed",
            run_id=run_id,
            run_dir=run_dir,
            updated_at="2026-05-25T15:15:34Z",
        )

    runner.load_state = load_completed_state  # type: ignore[method-assign]
    chat_ref: dict[str, ChatController] = {}

    def on_completed(task, workflow_state):  # type: ignore[no-untyped-def]
        assert task.origin_context_id == "wechat_clawbot:account-1:sender-1"
        chat_ref["controller"].append_report_completed_message(
            context_id=task.origin_context_id,
            report_id=workflow_state.run_id,
            task_id=task.task_id,
            text="报告已完成。\n最终结论：维持观察，等待突破确认。\n核心理由：日线趋势改善\n主要风险：估值波动",
        )

    queue = ReportTaskQueue(
        ReportWorkflowBridge(runner),
        completed_report_writer=on_completed,
    )
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    chat_ref["controller"] = chat_controller
    background_jobs = []
    sent_texts = []
    full_report_requests: list[tuple[str, str, ChannelReplyTarget]] = []

    def send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return {"sent": True}

    def request_full_report(report_id: str, request_id: str, target: ChannelReplyTarget) -> dict[str, object]:
        full_report_requests.append((report_id, request_id, target))
        return {"sent": True, "userMessage": "完整报告已发送。"}

    controller = ChannelTextInboundController(
        chat_controller,
        request_full_report_file=request_full_report,
        background_submitter=background_jobs.append,
        send_channel_text=send_text,
    )

    controller.handle_message(_message("r-7", "/report TSLA"))
    background_jobs.pop(0)()
    controller.handle_message(_message("r-8", "确认"))
    queue.get_report_queue_snapshot_for_user()

    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["context"]["kind"] == "report_reading"
    assert snapshot["context"]["activeReportId"] == "run-1"
    completed = snapshot["messages"][-1]
    assert completed["kind"] == "report_completed"
    assert completed["reportId"] == "run-1"
    assert "最终结论：维持观察" in completed["text"]

    full = controller.handle_message(_message("r-9", "发送完整报告"))
    assert full == {"handled": True, "replyText": "收到，正在发送完整报告。", "state": "file_sending"}
    assert full_report_requests == []
    assert len(background_jobs) == 1
    snapshot = controller.latest_conversation_snapshot()
    assert [item["text"] for item in snapshot["messages"]][-2:] == ["发送完整报告", "收到，正在发送完整报告。"]

    background_jobs[0]()

    assert [(report_id, target.sender_id, target.account_id) for report_id, _, target in full_report_requests] == [
        ("run-1", "sender-1", "account-1")
    ]
    assert sent_texts[-1]["text"] == "完整报告已发送。"
    snapshot = controller.latest_conversation_snapshot()
    assert snapshot["messages"][-1]["text"] == "完整报告已发送。"


def test_full_report_reply_uses_latest_report_delivered_to_same_wechat() -> None:
    background_jobs = []
    full_report_requests: list[tuple[str, str, ChannelReplyTarget]] = []

    def request_full_report(report_id: str, request_id: str, target: ChannelReplyTarget) -> dict[str, object]:
        full_report_requests.append((report_id, request_id, target))
        return {"sent": True, "userMessage": "完整报告已发送。"}

    controller, _runner, _transport = _controller(
        request_full_report_file=request_full_report,
        background_submitter=background_jobs.append,
        resolve_latest_delivered_report=lambda account_id, sender_id: "run-scheduled-1",
    )

    result = controller.handle_message(_message("scheduled-pdf-1", "发送完整报告"))

    assert result == {"handled": True, "replyText": "收到，正在发送完整报告。", "state": "file_sending"}
    background_jobs.pop(0)()
    assert [(report_id, target.account_id, target.sender_id) for report_id, _, target in full_report_requests] == [
        ("run-scheduled-1", "account-1", "sender-1")
    ]
