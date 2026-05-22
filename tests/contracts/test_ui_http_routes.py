from __future__ import annotations

import sys
from pathlib import Path

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.channel_bridge import ChannelBridge
from claw_trade.ui_backend.channel_text_inbound import ChannelTextInboundController
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.data_source_settings import DataSourceSettingsService
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_qa import ReportQaContextPolicy, ReportQuestionService
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_backend.settings_service import SettingsService
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.web.app import build_research_ui_app
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.web.state import UiHttpServices, build_ui_http_services
from fastapi.testclient import TestClient


class _FailChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str):  # type: ignore[no-untyped-def]
        _ = (context_id, text, request_id)
        raise RuntimeError("assistant_unavailable")


class _FailWorkflowRunner:
    def create_run(self, request):  # type: ignore[no-untyped-def]
        _ = request
        raise RuntimeError("assistant_unavailable")

    def load_state(self, run_id: str):  # type: ignore[no-untyped-def]
        _ = run_id
        raise RuntimeError("assistant_unavailable")


class _FakeSettingsGateway:
    def config_schema_lookup(self, *, path: str):
        return {"path": path, "version": "v1"}

    def config_get(self, *, paths):
        _ = paths
        return {
            "revision": "rev-1",
            "active_provider": "deepseek",
            "agents": {"defaults": {"model": "deepseek-chat"}},
            "models": {"providers": {"deepseek": {"api_key": "sk-test-1234", "endpoint_url": "https://api.example"}}},
        }

    def models_list(self, *, provider=None):
        _ = provider
        return [{"id": "deepseek-chat"}]

    def models_status(self, *, provider=None, as_json=True):
        _ = (provider, as_json)
        return {"ok": True, "message": "ready"}

    def models_auth_status(self, *, provider, model=None, endpoint_url=None, probe=True):
        _ = (provider, model, endpoint_url, probe)
        return {"ok": False, "message": "auth failed"}

    def config_patch(self, *, expected_settings_version=None, patch=None):
        _ = (expected_settings_version, patch)
        return {"newHash": "hash-new"}

    def plugins_list(self):
        return [{"id": "openclaw-weixin", "enabled": True, "channels": ["openclaw-weixin"]}]

    def channels_status(self, *, probe=False):
        _ = probe
        return {"channels": {"openclaw-weixin": {"state": "connected", "accountLabel": "测试号"}}}

    def channels_capabilities(self, *, channel):
        _ = channel
        return {"media": True}


class _FakeReportQaGateway:
    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        _ = metadata
        return "session-1"

    def chat_send(
        self,
        *,
        request_id: str,
        context_id: str,
        session_id: str,
        text: str,
    ) -> dict[str, object]:
        _ = (request_id, context_id, session_id, text)
        return {"text": "已收到追问"}


def _services(tmp_path: Path | None = None) -> UiHttpServices:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FailWorkflowRunner()))
    confirmation = ConfirmationController(queue)
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FailChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=confirmation,
        queue=queue,
        settings=ReportWorkflowSettings(),
    )
    repository = ReportRepository()
    asset_dir = tmp_path / "report-assets" if tmp_path is not None else None
    if asset_dir is not None:
        asset_dir.mkdir(parents=True)
        (asset_dir / "chart.png").write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
        )
    repository.save_succeeded_report(
        report_id="report-1",
        instrument_code="AAPL",
        instrument_name="Apple",
        market="US",
        title="AAPL 投研报告",
        markdown="# 报告\nBTC 的 L2 生态仍在早期，Layer 2 指标需要继续跟踪。\n\n![趋势图](assets/chart.png)",
        summary_snippet="这是摘要。",
        asset_dir=asset_dir,
    )
    summary_builder = CompletionSummaryBuilder(repository)
    report_notification_service = ReportNotificationService(
        repository,
        summary_builder,
        PdfExportService(repository),
        ChannelBridge(_FakeSettingsGateway()),
    )
    return UiHttpServices(
        report_settings=ReportWorkflowSettings(),
        chat_controller=chat_controller,
        queue=queue,
        repository=repository,
        summary_builder=summary_builder,
        pdf_export_service=PdfExportService(repository),
        report_question_service=ReportQuestionService(
            repository,
            _FakeReportQaGateway(),
            context_policy=ReportQaContextPolicy(max_total_chars=5),
        ),
        data_source_settings=DataSourceSettingsService(),
        channel_bridge=ChannelBridge(_FakeSettingsGateway()),
        channel_text_inbound=ChannelTextInboundController(chat_controller),
        llm_bridge=LlmSettingsBridge(_FakeSettingsGateway()),
        report_notification_service=report_notification_service,
        scheduler_service=SchedulerService(
            enqueue_report_task=lambda task, request_id: queue.enqueue_report_task(
                request_id=request_id,
                task_input=task,
                source="scheduled",
            ),
            queue_snapshot_provider=queue.get_report_queue_snapshot_for_user,
        ),
        price_alert_service=PriceAlertService(
            quote_provider=lambda _instrument, _market: {
                "current_price": 100.0,
                "percent_change": 1.0,
            }
        ),
        settings_service=SettingsService(),
    )


def _client(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html>ui</html>", encoding="utf-8")
    app = build_research_ui_app(
        settings=ResearchUiServerSettings(frontend_dist=dist),
        services=_services(tmp_path),
    )
    return TestClient(app)


def test_api_routes_return_json_not_spa_html(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-1", "contextId": "ctx-1", "text": "你好"},
    )
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "ASSISTANT_UNAVAILABLE"

    confirm = client.post(
        "/api/ui/send-chat-message",
        json={"requestId": "req-2", "contextId": "ctx-1", "text": "/report AAPL"},
    )
    assert confirm.status_code == 200
    assert confirm.headers["content-type"].startswith("application/json")
    confirmation_card = confirm.json()["confirmationCard"]
    assert confirmation_card["id"].startswith("card-")
    assert "cardId" not in confirmation_card

    ignored_channel_message = client.post(
        "/api/ui/channel-inbound-message",
        json={
            "requestId": "req-channel-in-ignored",
            "channelKind": "wechat_clawbot",
            "accountId": "account-1",
            "senderId": "sender-1",
            "text": "你好",
        },
    )
    assert ignored_channel_message.status_code == 200
    assert ignored_channel_message.headers["content-type"].startswith("application/json")
    assert ignored_channel_message.json() == {"handled": False}

    report_channel_message = client.post(
        "/api/ui/channel-inbound-message",
        json={
            "requestId": "req-channel-in-report",
            "channelKind": "wechat_clawbot",
            "accountId": "account-1",
            "senderId": "sender-1",
            "text": "/report AAPL",
            "messageId": "m-1",
        },
    )
    assert report_channel_message.status_code == 200
    assert report_channel_message.headers["content-type"].startswith("application/json")
    assert report_channel_message.json()["handled"] is True
    assert "回复“确认”" in report_channel_message.json()["replyText"]

    endpoints = (
        "/api/ui/get-report-queue-snapshot",
        "/api/ui/list-saved-reports",
        "/api/ui/get-report-detail?reportId=report-1",
        "/api/ui/get-report-chart-evidence?reportId=report-1",
        "/api/ui/get-channel-status?probe=true",
        "/api/ui/load-llm-settings",
        "/api/ui/list-data-sources",
    )
    for path in endpoints:
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/json")
        assert isinstance(res.json(), dict)

    channel_save = client.post(
        "/api/ui/save-channel-config-via-openclaw",
        json={
            "requestId": "req-channel-save",
            "channelKind": "wechat_clawbot",
            "configPatch": {"enabled": True},
        },
    )
    assert channel_save.status_code == 200
    assert channel_save.headers["content-type"].startswith("application/json")
    assert channel_save.json()["status"]["channelKind"] == "wechat_clawbot"

    device_interface = client.get("/api/ui/open-device-interface", follow_redirects=False)
    assert device_interface.status_code == 307
    assert device_interface.headers["location"] == "http://127.0.0.1:18789/"

    ask = client.post(
        "/api/ui/ask-report-question",
        json={"requestId": "req-qa", "reportId": "report-1", "text": "请解释"},
    )
    assert ask.status_code == 413
    assert ask.headers["content-type"].startswith("application/json")
    assert ask.json()["code"] == "REPORT_CONTEXT_TOO_LONG"

    report_asset = client.get("/api/ui/get-report-asset?reportId=report-1&assetPath=assets/chart.png")
    assert report_asset.status_code == 200
    assert report_asset.headers["content-type"].startswith("image/png")
    assert report_asset.content.startswith(b"\x89PNG")

    outside_asset = client.get("/api/ui/get-report-asset?reportId=report-1&assetPath=../state.json")
    assert outside_asset.status_code == 404
    assert outside_asset.headers["content-type"].startswith("application/json")
    assert outside_asset.json()["code"] == "REPORT_NOT_FOUND"


def test_delete_saved_report_route_hides_report(tmp_path: Path) -> None:
    client = _client(tmp_path)

    delete = client.post(
        "/api/ui/delete-saved-report",
        json={"requestId": "req-delete-report", "reportId": "report-1"},
    )

    assert delete.status_code == 200
    assert delete.headers["content-type"].startswith("application/json")
    assert delete.json() == {"deleted": True, "reportId": "report-1"}
    assert client.get("/api/ui/list-saved-reports").json()["items"] == []
    detail = client.get("/api/ui/get-report-detail?reportId=report-1")
    assert detail.status_code == 404
    assert detail.json()["code"] == "REPORT_NOT_FOUND"


def test_extended_ui_api_routes_return_json_not_spa_html(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_draft = client.post(
        "/api/ui/create-intent-draft",
        json={
            "requestId": "req-draft-1",
            "sourceMessageId": "msg-1",
            "text": "/report AAPL",
        },
    )
    assert create_draft.status_code == 200
    assert create_draft.headers["content-type"].startswith("application/json")
    draft_id = create_draft.json()["draft"]["draftId"]

    confirm_draft = client.post(
        "/api/ui/confirm-intent-draft",
        json={
            "requestId": "req-confirm-1",
            "draftId": draft_id,
            "decision": "cancel",
        },
    )
    assert confirm_draft.status_code == 200
    assert confirm_draft.headers["content-type"].startswith("application/json")
    assert confirm_draft.json()["status"] == "cancelled"

    create_schedule = client.post(
        "/api/ui/create-scheduled-report",
        json={
            "requestId": "req-s-create",
            "instrumentCode": "AAPL",
            "market": "US",
            "frequency": "daily",
            "timeOfDay": "09:30",
            "notification": {"channel": "in_app", "enabled": True},
        },
    )
    assert create_schedule.status_code == 200
    assert create_schedule.headers["content-type"].startswith("application/json")
    scheduled_report_id = create_schedule.json()["scheduledReportId"]

    schedule_routes = (
        ("/api/ui/pause-scheduled-report", {"requestId": "req-s-1", "scheduledReportId": scheduled_report_id}),
        ("/api/ui/resume-scheduled-report", {"requestId": "req-s-2", "scheduledReportId": scheduled_report_id}),
        ("/api/ui/run-scheduled-report-now", {"requestId": "req-s-3", "scheduledReportId": scheduled_report_id}),
        ("/api/ui/delete-scheduled-report", {"requestId": "req-s-4", "scheduledReportId": scheduled_report_id}),
    )
    for path, body in schedule_routes:
        response = client.post(path, json=body)
        assert response.headers["content-type"].startswith("application/json")
        assert response.status_code in (200, 409, 503)

    create_alert = client.post(
        "/api/ui/create-price-alert",
        json={
            "requestId": "req-alert-1",
            "instrumentCode": "BTC",
            "market": "CRYPTO",
            "condition": {"type": "price_threshold", "operator": "above", "value": 70000},
            "notification": {"channel": "wechat_clawbot", "enabled": True},
        },
    )
    assert create_alert.status_code == 200
    alert_id = create_alert.json()["priceAlertId"]

    alert_routes = (
        ("/api/ui/pause-price-alert", {"requestId": "req-a-1", "priceAlertId": alert_id}),
        ("/api/ui/resume-price-alert", {"requestId": "req-a-2", "priceAlertId": alert_id}),
        ("/api/ui/run-price-alert-now", {"requestId": "req-a-3", "priceAlertId": alert_id}),
        ("/api/ui/delete-price-alert", {"requestId": "req-a-4", "priceAlertId": alert_id}),
    )
    for path, body in alert_routes:
        response = client.post(path, json=body)
        assert response.headers["content-type"].startswith("application/json")
        assert response.status_code in (200, 409, 503)

    data_source_test = client.post(
        "/api/ui/test-data-source",
        json={
            "requestId": "req-ds-1",
            "instanceDraft": {"supportedType": "tushare", "displayName": "Tushare"},
        },
    )
    assert data_source_test.status_code in {400, 409}
    assert data_source_test.headers["content-type"].startswith("application/json")
    assert data_source_test.json()["code"] in {"INVALID_INPUT", "DATASOURCE_TEST_FAILED"}

    data_source_save = client.post(
        "/api/ui/save-data-source-instance",
        json={
            "requestId": "req-ds-2",
            "instance": {
                "supportedType": "tushare",
                "displayName": "Tushare",
                "enabled": True,
                "state": "validated",
                "apiKeyReplacement": "masked-key",
            },
        },
    )
    assert data_source_save.status_code == 409
    assert data_source_save.headers["content-type"].startswith("application/json")
    assert data_source_save.json()["code"] == "DATASOURCE_TEST_FAILED"

    llm_save = client.post(
        "/api/ui/save-llm-config-via-openclaw",
        json={
            "requestId": "req-llm-save",
            "expectedSettingsVersion": "v_1",
            "draft": {"provider": "deepseek", "defaultModel": "deepseek-chat"},
        },
    )
    assert llm_save.status_code == 200
    assert llm_save.headers["content-type"].startswith("application/json")

    llm_test = client.post(
        "/api/ui/test-llm-via-openclaw",
        json={"requestId": "req-llm-test", "provider": "deepseek"},
    )
    assert llm_test.status_code == 200
    assert llm_test.headers["content-type"].startswith("application/json")

    pdf_export = client.post(
        "/api/ui/export-report-pdf",
        json={"requestId": "req-pdf-export", "reportId": "report-1"},
    )
    assert pdf_export.status_code == 200
    assert pdf_export.headers["content-type"].startswith("application/json")

    file_send = client.post(
        "/api/ui/send-report-file-via-channel",
        json={"requestId": "req-send-file", "reportId": "report-1", "channelKind": "wechat_clawbot"},
    )
    assert file_send.status_code in (409, 503)
    assert file_send.headers["content-type"].startswith("application/json")

    missing_post = client.post("/api/ui/not-exists", json={"foo": "bar"})
    assert missing_post.status_code == 404
    assert missing_post.headers["content-type"].startswith("application/json")


def test_ui_validation_error_is_mapped_to_product_error_json(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/ui/create-intent-draft",
        json={
            "requestId": "req-invalid",
            "text": "/report AAPL",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["code"] == "INVALID_INPUT"
    assert body["message"] == "请求参数不完整或格式不正确。"
    assert body["severity"] == "error"
    assert "detail" not in body


def test_build_ui_http_services_uses_runtime_quote_provider(monkeypatch, tmp_path: Path) -> None:
    from claw_trade.web import state as state_module

    def _fake_provider_builder():
        return lambda _instrument, _market: {"current_price": 101.0, "percent_change": 2.5}

    monkeypatch.setattr(state_module, "build_price_alert_quote_provider", _fake_provider_builder)
    services = build_ui_http_services(ResearchUiServerSettings(frontend_dist=tmp_path))
    created = services.price_alert_service.create_price_alert(
        request_id="req-alert-create",
        instrument_code="AAPL",
        market="US",
        condition={"type": "price_threshold", "operator": "above", "value": 100},
        notification={"channel": "in_app", "enabled": True},
    )
    payload = services.price_alert_service.run_price_alert_now(
        request_id="req-alert-run",
        price_alert_id=created.priceAlertId,
    )
    assert payload["triggered"] is True


def test_data_source_routes_reject_missing_key_and_do_not_persist(monkeypatch, tmp_path: Path) -> None:
    module = sys.modules[__name__]
    real_service_cls = DataSourceSettingsService
    strict_service = real_service_cls(
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )
    monkeypatch.setattr(module, "DataSourceSettingsService", lambda: strict_service)
    client = _client(tmp_path)

    test_resp = client.post(
        "/api/ui/test-data-source",
        json={
            "requestId": "req-ds-missing-key-test",
            "instanceDraft": {
                "supportedType": "tushare",
                "displayName": "Tushare",
            },
        },
    )
    assert test_resp.status_code == 400
    assert test_resp.json()["code"] == "INVALID_INPUT"

    save_resp = client.post(
        "/api/ui/save-data-source-instance",
        json={
            "requestId": "req-ds-missing-key-save",
            "instance": {
                "supportedType": "tushare",
                "displayName": "Tushare",
                "enabled": True,
                "state": "validated",
            },
        },
    )
    assert save_resp.status_code == 400
    assert save_resp.json()["code"] == "INVALID_INPUT"

    listed = client.get("/api/ui/list-data-sources")
    assert listed.status_code == 200
    instances = listed.json()["instances"]
    assert all(item["supportedType"] != "tushare" for item in instances)
