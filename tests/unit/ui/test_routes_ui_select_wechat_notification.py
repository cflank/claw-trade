from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from claw_trade.ui_backend.channel_text_inbound import ChannelTextInboundController, ChannelTextMessage
from claw_trade.web.app import build_research_ui_app
from claw_trade.web.settings import ResearchUiServerSettings


class _ChatControllerProbe:
    def __init__(self, *, context_id: str = "ui-select", selection_code: str = "completed") -> None:
        self.context_id = context_id
        self.selection_code = selection_code
        self.appended: list[dict[str, str]] = []
        self._result: dict[str, object] | None = None
        selection_kind = "selection_result" if selection_code == "completed" else "selection_failed"
        selection_text = "`/select` 网页测试结果" if selection_code == "completed" else "`/select` 网页测试失败"
        self._messages = [
            {
                "messageId": "m-user",
                "contextKind": "normal_chat",
                "actor": "user",
                "kind": "plain",
                "text": "/select",
                "createdAt": "2026-08-23T10:00:00Z",
            },
            {
                "messageId": "m-select",
                "contextKind": "normal_chat",
                "actor": "system",
                "kind": selection_kind,
                "text": selection_text,
                "createdAt": "2026-08-23T10:00:01Z",
                "selection": {
                    "workflowRunId": "select-ui-run",
                    "readerReportMarkdown": "# 选股结果\n\n## 候选事实表",
                },
            },
        ]

    def send_chat_message(self, *, request_id: str, context_id: str, text: str) -> dict[str, object]:
        assert request_id == "req-select-ui"
        assert context_id == self.context_id
        assert text == "/select"
        if self._result is None:
            self._result = {
                "context": {"contextId": context_id, "kind": "normal_chat", "title": "普通聊天"},
                "messages": list(self._messages),
                "selection": {
                    "code": self.selection_code,
                    "workflowRunId": "select-ui-run",
                    "readerReportMarkdown": "# 选股结果\n\n## 候选事实表",
                },
            }
        return self._result

    def append_channel_plain_message(self, *, context_id: str, actor: str, text: str, task_id=None) -> dict[str, object]:  # type: ignore[no-untyped-def]
        _ = task_id
        self.appended.append({"contextId": context_id, "actor": actor, "text": text})
        self._messages.append(
            {
                "messageId": f"m-appended-{len(self.appended)}",
                "contextKind": "normal_chat",
                "actor": actor,
                "kind": "plain",
                "text": text,
                "createdAt": "2026-08-23T10:00:02Z",
            }
        )
        return self.get_chat_session(context_id=context_id)

    def get_chat_session(self, *, context_id: str) -> dict[str, object]:
        return {
            "context": {"contextId": context_id, "kind": "normal_chat", "title": "普通聊天"},
            "messages": list(self._messages),
        }


class _NotificationProbe:
    def __init__(self, binding: dict[str, str] | None, *, fail: bool = False) -> None:
        self.binding = binding
        self.fail = fail
        self.calls = 0

    def resolve_and_store_current_notification_recipient(self) -> dict[str, str] | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("wechat unavailable")
        return self.binding


def _client(
    tmp_path: Path,
    *,
    binding: dict[str, str] | None,
    send_result: dict[str, object] | None = None,
    context_id: str = "ui-select",
    selection_code: str = "completed",
    notification_fails: bool = False,
):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    chat = _ChatControllerProbe(context_id=context_id, selection_code=selection_code)
    sent_texts: list[dict[str, object]] = []
    sent_files: list[dict[str, object]] = []
    background_jobs = []

    def _send_text(text, dedupe_key, target):  # type: ignore[no-untyped-def]
        sent_texts.append({"text": text, "dedupeKey": dedupe_key, "target": target})
        return send_result or {"sent": True}

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

    services = SimpleNamespace(
        chat_controller=chat,
        report_notification_service=_NotificationProbe(binding, fail=notification_fails),
        channel_text_inbound=ChannelTextInboundController(
            chat,
            send_channel_text=_send_text,
            request_selection_report_file=_send_selection_file,
            background_submitter=background_jobs.append,
        ),
    )
    app = build_research_ui_app(
        settings=ResearchUiServerSettings(frontend_dist=dist),
        services=services,  # type: ignore[arg-type]
    )
    return TestClient(app), services, sent_texts, sent_files, background_jobs


def test_send_chat_select_pushes_bound_wechat_summary(tmp_path: Path) -> None:
    client, services, sent_texts, sent_files, background_jobs = _client(
        tmp_path,
        binding={"account_id": "account-bound", "sender_id": "sender-bound"},
    )

    response = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-select-ui", "contextId": "ui-select", "text": "/select"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["channelDelivery"] == {"status": "sent"}
    assert services.report_notification_service.calls == 1
    assert len(sent_texts) == 1
    assert "`/select` 网页测试结果" in sent_texts[0]["text"]
    assert "回复“发送完整报告”" in sent_texts[0]["text"]
    assert sent_texts[0]["target"].account_id == "account-bound"
    assert sent_texts[0]["target"].sender_id == "sender-bound"

    file_result = services.channel_text_inbound.handle_message(
        ChannelTextMessage(
            request_id="req-select-file",
            channel_kind="wechat_clawbot",
            account_id="account-bound",
            sender_id="sender-bound",
            text="发送完整报告",
        )
    )

    assert file_result["state"] == "file_sending"
    assert sent_files == []
    background_jobs[0]()
    assert sent_files == [
        {
            "workflowRunId": "select-ui-run",
            "markdown": "# 选股结果\n\n## 候选事实表",
            "requestId": "channel-selection-report:req-select-file",
            "target": sent_texts[0]["target"],
        }
    ]


def test_send_chat_select_retry_does_not_push_duplicate_wechat_summary(tmp_path: Path) -> None:
    client, _services, sent_texts, _sent_files, _background_jobs = _client(
        tmp_path,
        binding={"account_id": "account-bound", "sender_id": "sender-bound"},
    )

    for _ in range(2):
        response = client.post(
            "/api/ui/send-chat-message",
            json={"requestId": "req-select-ui", "contextId": "ui-select", "text": "/select"},
        )
        assert response.status_code == 200
        assert response.json()["channelDelivery"] == {"status": "sent"}

    assert len(sent_texts) == 1


def test_send_chat_select_keeps_result_visible_when_wechat_is_unbound(tmp_path: Path) -> None:
    client, services, sent_texts, _sent_files, _background_jobs = _client(tmp_path, binding=None)

    response = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-select-ui", "contextId": "ui-select", "text": "/select"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"]["workflowRunId"] == "select-ui-run"
    assert payload["channelDelivery"] == {"reason": "wechat_recipient_unavailable", "status": "unavailable"}
    assert sent_texts == []
    assert services.chat_controller.appended == [
        {
            "contextId": "ui-select",
            "actor": "system",
            "text": "选股结果未发送到微信：当前没有可用的微信收件人；结果已保留在工作台。",
        }
    ]
    assert payload["messages"][-1]["text"] == "选股结果未发送到微信：当前没有可用的微信收件人；结果已保留在工作台。"


def test_send_chat_select_keeps_result_visible_when_wechat_send_fails(tmp_path: Path) -> None:
    client, services, sent_texts, _sent_files, _background_jobs = _client(
        tmp_path,
        binding={"account_id": "account-bound", "sender_id": "sender-bound"},
        send_result={"sent": False, "resultKnown": True},
    )

    response = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-select-ui", "contextId": "ui-select", "text": "/select"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"]["workflowRunId"] == "select-ui-run"
    assert payload["channelDelivery"] == {"status": "failed"}
    assert len(sent_texts) == 2
    assert services.chat_controller.appended == [
        {
            "contextId": "ui-select",
            "actor": "system",
            "text": "选股结果发送到微信失败；结果已保留在工作台。",
        }
    ]
    assert payload["messages"][-1]["text"] == "选股结果发送到微信失败；结果已保留在工作台。"


def test_send_chat_select_keeps_result_visible_when_notification_lookup_fails(tmp_path: Path) -> None:
    client, services, sent_texts, _sent_files, _background_jobs = _client(
        tmp_path,
        binding={"account_id": "account-bound", "sender_id": "sender-bound"},
        notification_fails=True,
    )

    response = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-select-ui", "contextId": "ui-select", "text": "/select"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"]["workflowRunId"] == "select-ui-run"
    assert payload["channelDelivery"] == {
        "reason": "wechat_recipient_lookup_failed",
        "status": "unavailable",
    }
    assert sent_texts == []
    assert services.chat_controller.appended == [
        {
            "contextId": "ui-select",
            "actor": "system",
            "text": "选股结果未发送到微信：无法确认当前微信收件人；结果已保留在工作台。",
        }
    ]


def test_send_chat_select_does_not_notify_failed_selection_result(tmp_path: Path) -> None:
    client, services, sent_texts, _sent_files, _background_jobs = _client(
        tmp_path,
        binding={"account_id": "account-bound", "sender_id": "sender-bound"},
        selection_code="failed",
    )

    response = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-select-ui", "contextId": "ui-select", "text": "/select"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"]["code"] == "failed"
    assert "channelDelivery" not in payload
    assert services.report_notification_service.calls == 0
    assert sent_texts == []
