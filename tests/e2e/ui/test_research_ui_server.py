from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from claw_trade.selection.models import SelectionMarket, SelectionProfile
from claw_trade.ui_backend.report_cleanup import ReportCleanupResult, ReportCleanupRunResult
from claw_trade.ui_backend.report_cleanup_settings import ReportCleanupSettingsService
from claw_trade.ui_backend.selection_auto_refresh_settings import SelectionAutoRefreshSettingsService
from claw_trade.web.app import build_research_ui_app, parse_args
from claw_trade.web.routes_ui import (
    CancelReportTaskRequest,
    CancelSelectionProgressRequest,
    ConfirmIntentDraftRequest,
    DeleteSavedReportRequest,
    DeleteSavedReportsRequest,
    cancel_report_task,
    cancel_selection_progress,
    confirm_intent_draft,
    delete_saved_report,
    delete_saved_reports,
    get_chat_session,
    get_selection_refresh_snapshot,
)
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.web.state import build_ui_http_services
from fastapi import Request
from fastapi.testclient import TestClient


def _app(tmp_path: Path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    (assets / "main.js").write_text("console.log('ok');", encoding="utf-8")
    settings = ResearchUiServerSettings(frontend_dist=dist)
    return build_research_ui_app(settings=settings, services=build_ui_http_services(settings))


def test_spa_fallback_is_last_and_api_prefix_keeps_json(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    spa = client.get("/some/page")
    assert spa.status_code == 200
    assert spa.headers["content-type"].startswith("text/html")
    assert spa.headers["cache-control"] == "no-store, max-age=0"

    api = client.get("/api/ui/list-saved-reports")
    assert api.status_code == 200
    assert api.headers["content-type"].startswith("application/json")
    assert "text/html" not in api.headers["content-type"]

    missing_api = client.get("/api/ui/not-exists")
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/json")

    missing_other_api = client.get("/api/other")
    assert missing_other_api.status_code == 404
    assert missing_other_api.headers["content-type"].startswith("application/json")


def test_open_device_interface_redirects_with_fragment_token(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    app = build_research_ui_app(
        settings=ResearchUiServerSettings(
            frontend_dist=dist,
            gateway_ws_url="ws://127.0.0.1:18789",
            gateway_token="runtime-token",
        ),
        services=SimpleNamespace(),  # type: ignore[arg-type]
    )

    response = TestClient(app).get("/api/ui/open-device-interface", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    assert location == (
        "http://127.0.0.1:18789/chat?"
        "session=agent%3Aui_chat%3Av2%3Aui%3Anormal-chat#token=runtime-token"
    )
    assert "?token=" not in location


def test_list_saved_reports_enables_forward_when_wechat_can_send_files(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    services = SimpleNamespace(
        queue=_SavedReportQueueProbe(),
        repository=_SavedReportRepositoryProbe(),
        channel_bridge=_ConnectedChannelBridgeProbe(),
    )
    app = build_research_ui_app(
        settings=ResearchUiServerSettings(frontend_dist=dist),
        services=services,  # type: ignore[arg-type]
    )

    response = TestClient(app).get("/api/ui/list-saved-reports")

    assert response.status_code == 200
    payload = response.json()
    assert [item["canForwardToChannel"] for item in payload["items"]] == [True, True]


def test_report_cleanup_settings_routes_and_reset(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    cleanup = ReportCleanupSettingsService(json_path=tmp_path / "runs" / ".ui-report-cleanup-settings.json")
    selection_auto_refresh = SelectionAutoRefreshSettingsService(
        json_path=tmp_path / "runs" / ".ui-selection-auto-refresh-settings.json"
    )
    services = SimpleNamespace(
        report_cleanup_settings=cleanup,
        selection_auto_refresh_settings=selection_auto_refresh,
        selection_refresh_service=_RefreshServiceProbe(),
        llm_bridge=_ResetLlmProbe(),
        data_source_settings=_ResetDataSourcesProbe(),
        channel_bridge=_ResetChannelProbe(),
    )
    app = build_research_ui_app(
        settings=ResearchUiServerSettings(frontend_dist=dist),
        services=services,  # type: ignore[arg-type]
    )
    client = TestClient(app)

    loaded = client.get("/api/ui/get-report-cleanup-settings")
    assert loaded.status_code == 200
    assert loaded.json()["reportCleanup"] == {"reportRetentionDays": 7}

    saved = client.post(
        "/api/ui/save-report-cleanup-settings",
        json={"requestId": "req-save-cleanup", "reportRetentionDays": 14},
    )
    assert saved.status_code == 200
    assert saved.json()["reportCleanup"] == {"reportRetentionDays": 14}

    rejected = client.post(
        "/api/ui/save-report-cleanup-settings",
        json={"requestId": "req-save-cleanup-invalid", "reportRetentionDays": 21},
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "INVALID_INPUT"

    selection_loaded = client.get("/api/ui/get-selection-auto-refresh-settings")
    assert selection_loaded.status_code == 200
    assert selection_loaded.json()["selectionAutoRefresh"] == {"enabled": True}

    selection_saved = client.post(
        "/api/ui/save-selection-auto-refresh-settings",
        json={"requestId": "req-save-selection-refresh", "enabled": False},
    )
    assert selection_saved.status_code == 200
    assert selection_saved.json()["selectionAutoRefresh"] == {"enabled": False}
    assert services.selection_refresh_service.stopped == 1

    reset = client.post("/api/ui/reset-settings-to-defaults", json={"requestId": "req-reset"})
    assert reset.status_code == 200
    assert reset.json()["reportCleanup"] == {"reportRetentionDays": 7}
    assert reset.json()["selectionAutoRefresh"] == {"enabled": True}
    assert cleanup.load_settings() == {"reportRetentionDays": 7}
    assert selection_auto_refresh.load_settings() == {"enabled": True}


def test_app_startup_starts_owned_selection_auto_refresh_by_default(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    cleanup = _CleanupSchedulerProbe()
    services = SimpleNamespace(selection_refresh_service=refresh, report_cleanup_scheduler=cleanup)
    monkeypatch.delenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", raising=False)
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert refresh.started == 1
        assert cleanup.started == 1

    assert refresh.stopped == 1
    assert cleanup.stopped == 1


def test_app_startup_starts_owned_selection_auto_refresh_when_enabled(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    cleanup = _CleanupSchedulerProbe()
    services = SimpleNamespace(selection_refresh_service=refresh, report_cleanup_scheduler=cleanup)
    monkeypatch.setenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", "1")
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert refresh.started == 1
        assert cleanup.started == 1

    assert refresh.stopped == 1
    assert cleanup.stopped == 1


def test_app_startup_does_not_start_selection_auto_refresh_when_setting_is_disabled(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    cleanup = _CleanupSchedulerProbe()
    services = SimpleNamespace(
        selection_refresh_service=refresh,
        selection_auto_refresh_settings=_SelectionAutoRefreshSettingsProbe(enabled=False),
        report_cleanup_scheduler=cleanup,
    )
    monkeypatch.delenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", raising=False)
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert refresh.started == 0
        assert cleanup.started == 1

    assert refresh.stopped == 0
    assert cleanup.stopped == 1


def test_app_startup_stops_selection_when_cleanup_scheduler_start_fails(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    cleanup = _CleanupSchedulerProbe(fail_start=True)
    services = SimpleNamespace(selection_refresh_service=refresh, report_cleanup_scheduler=cleanup)
    monkeypatch.setenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", "1")
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with pytest.raises(RuntimeError, match="cleanup scheduler failed"):
        with TestClient(app):
            pass

    assert refresh.started == 1
    assert refresh.stopped == 1
    assert cleanup.started == 1
    assert cleanup.stopped == 0


def test_app_startup_does_not_start_owned_selection_auto_refresh_when_disabled(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    cleanup = _CleanupSchedulerProbe()
    services = SimpleNamespace(selection_refresh_service=refresh, report_cleanup_scheduler=cleanup)
    monkeypatch.setenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", "0")
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert cleanup.started == 1

    assert refresh.started == 0
    assert refresh.stopped == 0
    assert cleanup.stopped == 1


def test_app_startup_does_not_start_injected_selection_auto_refresh(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    cleanup = _CleanupSchedulerProbe()
    services = SimpleNamespace(selection_refresh_service=refresh, report_cleanup_scheduler=cleanup)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist), services=services)  # type: ignore[arg-type]

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200

    assert refresh.started == 0
    assert refresh.stopped == 0
    assert cleanup.started == 0
    assert cleanup.stopped == 0


def test_parse_args_uses_runtime_gateway_token_when_env_is_missing(tmp_path: Path, monkeypatch) -> None:
    runtime_env = tmp_path / ".runtime" / "dev-services" / "runtime.env"
    runtime_env.parent.mkdir(parents=True)
    runtime_env.write_text("OPENCLAW_GATEWAY_TOKEN=runtime-token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)

    args = parse_args([])

    assert args.gateway_token == "runtime-token"


def test_selection_refresh_snapshot_falls_back_to_crypto_refresh_progress() -> None:
    refresh = _RefreshSnapshotProbe()
    services = SimpleNamespace(
        selection_controller=_SelectionControllerSnapshotProbe(),
        selection_refresh_service=refresh,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/ui/get-selection-refresh-snapshot",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(ui_services=services)),
        }
    )

    response = get_selection_refresh_snapshot(request)
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["selectionProgress"]["workflowRunId"] == "sel-auto-crypto-active"
    assert refresh.calls == [
        (None, None, None, False),
        (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO, None, False),
    ]


def test_confirm_intent_draft_uses_chat_context_when_provided() -> None:
    chat = _ChatControllerProbe()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/confirm-intent-draft",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(ui_services=SimpleNamespace(chat_controller=chat))),
        }
    )

    response = confirm_intent_draft(
        ConfirmIntentDraftRequest(
            requestId="req-confirm",
            draftId="draft-1",
            decision="confirm",
            contextId="normal-chat",
            overrides={"instrumentCode": "BTC", "market": "CRYPTO"},
        ),
        request,
    )

    assert response.status_code == 200
    assert chat.confirm_from_chat_calls == [
        {
            "request_id": "req-confirm",
            "context_id": "normal-chat",
            "draft_id": "draft-1",
            "decision": "confirm",
            "text": "确认",
            "overrides": {"instrumentCode": "BTC", "market": "CRYPTO"},
        }
    ]


def test_get_chat_session_returns_current_context_messages() -> None:
    chat = _ChatControllerProbe()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/ui/get-chat-session",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(ui_services=SimpleNamespace(chat_controller=chat))),
        }
    )

    response = get_chat_session(request, contextId="normal-chat")

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["context"]["contextId"] == "normal-chat"
    assert payload["messages"][0]["kind"] == "report_completed"
    assert chat.session_context_ids == ["normal-chat"]


def test_cancel_report_task_route_calls_queue() -> None:
    queue = _QueueProbe()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/cancel-report-task",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(ui_services=SimpleNamespace(queue=queue))),
        }
    )

    response = cancel_report_task(
        CancelReportTaskRequest(requestId="req-cancel", taskId="task-1"),
        request,
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["task"]["status"] == "cancelled"
    assert payload["queueSnapshot"]["runningTask"] is None
    assert queue.calls == [{"request_id": "req-cancel", "task_id": "task-1"}]


def test_delete_saved_report_route_calls_cleanup_service() -> None:
    cleanup = _ReportCleanupProbe(
        ReportCleanupResult(
            deletedRunIds=["run-delete-1"],
            skippedRunIds=[],
            failedRunIds=[],
            deletedBytesApprox=128,
            warnings=[],
            userMessage="已删除 1 份报告。",
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/delete-saved-report",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(ui_services=SimpleNamespace(report_cleanup_service=cleanup))
            ),
        }
    )

    response = delete_saved_report(
        DeleteSavedReportRequest(requestId="req-delete", reportId="run-delete-1"),
        request,
    )

    assert response.status_code == 200
    assert cleanup.calls == [["run-delete-1"]]
    assert json.loads(response.body) == {
        "deleted": True,
        "reportId": "run-delete-1",
        "userMessage": "已删除 1 份报告。",
        "cleanup": {
            "deletedRunIds": ["run-delete-1"],
            "skippedRunIds": [],
            "failedRunIds": [],
            "deletedBytesApprox": 128,
            "warnings": [],
        },
    }


def test_delete_saved_report_route_returns_partial_cleanup_details_without_error() -> None:
    cleanup = _ReportCleanupProbe(
        ReportCleanupResult(
            deletedRunIds=[],
            skippedRunIds=["run-delete-skipped"],
            failedRunIds=[],
            deletedBytesApprox=0,
            warnings=["运行仍受保护。"],
            userMessage="0 份报告已删除，1 份已跳过。",
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/delete-saved-report",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(ui_services=SimpleNamespace(report_cleanup_service=cleanup))
            ),
        }
    )

    response = delete_saved_report(
        DeleteSavedReportRequest(requestId="req-delete", reportId="run-delete-skipped"),
        request,
    )

    assert response.status_code == 200
    assert cleanup.calls == [["run-delete-skipped"]]
    assert json.loads(response.body) == {
        "deleted": False,
        "reportId": "run-delete-skipped",
        "userMessage": "0 份报告已删除，1 份已跳过。",
        "cleanup": {
            "deletedRunIds": [],
            "skippedRunIds": ["run-delete-skipped"],
            "failedRunIds": [],
            "deletedBytesApprox": 0,
            "warnings": ["运行仍受保护。"],
        },
    }


def test_delete_saved_report_route_returns_failed_cleanup_details_without_error() -> None:
    cleanup = _ReportCleanupProbe(
        ReportCleanupResult(
            deletedRunIds=[],
            skippedRunIds=[],
            failedRunIds=["run-delete-failed"],
            deletedBytesApprox=0,
            warnings=["删除 run-delete-failed 失败：permission denied"],
            userMessage="0 份报告已删除，1 份删除失败。",
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/delete-saved-report",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(ui_services=SimpleNamespace(report_cleanup_service=cleanup))
            ),
        }
    )

    response = delete_saved_report(
        DeleteSavedReportRequest(requestId="req-delete", reportId="run-delete-failed"),
        request,
    )

    assert response.status_code == 200
    assert cleanup.calls == [["run-delete-failed"]]
    assert json.loads(response.body) == {
        "deleted": False,
        "reportId": "run-delete-failed",
        "userMessage": "0 份报告已删除，1 份删除失败。",
        "cleanup": {
            "deletedRunIds": [],
            "skippedRunIds": [],
            "failedRunIds": ["run-delete-failed"],
            "deletedBytesApprox": 0,
            "warnings": ["删除 run-delete-failed 失败：permission denied"],
        },
    }


def test_delete_saved_reports_route_calls_cleanup_service_once_for_batch() -> None:
    cleanup = _ReportCleanupProbe(
        ReportCleanupResult(
            deletedRunIds=["run-delete-1"],
            skippedRunIds=["run-delete-2"],
            failedRunIds=[],
            deletedBytesApprox=128,
            warnings=[],
            userMessage="已删除 1 份报告，1 份已跳过。",
            runs=[
                ReportCleanupRunResult(
                    runId="run-delete-1",
                    status="deleted",
                    deletedBytesApprox=128,
                    userMessage="报告已硬删除。",
                ),
                ReportCleanupRunResult(
                    runId="run-delete-2",
                    status="skipped",
                    userMessage="运行仍受保护。",
                ),
            ],
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/delete-saved-reports",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(ui_services=SimpleNamespace(report_cleanup_service=cleanup))
            ),
        }
    )

    response = delete_saved_reports(
        DeleteSavedReportsRequest(
            requestId="req-delete-batch",
            reportIds=["run-delete-1", "run-delete-2"],
        ),
        request,
    )

    assert response.status_code == 200
    assert cleanup.calls == [["run-delete-1", "run-delete-2"]]
    assert json.loads(response.body) == {
        "deletedRunIds": ["run-delete-1"],
        "skippedRunIds": ["run-delete-2"],
        "failedRunIds": [],
        "deletedBytesApprox": 128,
        "warnings": [],
        "userMessage": "已删除 1 份报告，1 份已跳过。",
        "runs": [
            {
                "reportId": "run-delete-1",
                "status": "deleted",
                "deletedBytesApprox": 128,
                "warnings": [],
                "userMessage": "报告已硬删除。",
            },
            {
                "reportId": "run-delete-2",
                "status": "skipped",
                "deletedBytesApprox": 0,
                "warnings": [],
                "userMessage": "运行仍受保护。",
            },
        ],
    }


def test_cancel_selection_progress_route_cancels_workflow_and_refresh() -> None:
    selection = _SelectionCancelProbe(cancelled=False)
    refresh = _RefreshCancelProbe(cancelled=True)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ui/cancel-selection-progress",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    ui_services=SimpleNamespace(
                        selection_controller=selection,
                        selection_refresh_service=refresh,
                    )
                )
            ),
        }
    )

    response = cancel_selection_progress(
        CancelSelectionProgressRequest(requestId="req-cancel-select", workflowRunId="sel-refresh-active-1"),
        request,
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload == {
        "cancelled": True,
        "selectionProgress": None,
        "message": "已停止选股任务。",
    }
    assert selection.workflow_run_ids == ["sel-refresh-active-1"]
    assert refresh.selection_run_ids == ["sel-refresh-active-1"]


def test_module_entrypoint_help_for_claw_trade_web_app() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "claw_trade.web.app", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--frontend-dist" in completed.stdout


class _RefreshServiceProbe:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start_automatic_refresh_scheduler(self) -> None:
        self.started += 1

    def stop_automatic_refresh_scheduler(self) -> None:
        self.stopped += 1


class _SelectionAutoRefreshSettingsProbe:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled

    def load_settings(self) -> dict[str, bool]:
        return {"enabled": self._enabled}


class _CleanupSchedulerProbe:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.started = 0
        self.stopped = 0
        self._fail_start = fail_start

    def start(self) -> None:
        self.started += 1
        if self._fail_start:
            raise RuntimeError("cleanup scheduler failed")

    def stop(self) -> None:
        self.stopped += 1


class _SavedReportQueueProbe:
    def get_report_queue_snapshot_for_user(self) -> dict[str, object]:
        return {}


class _SavedReportRepositoryProbe:
    def list_saved_reports(self) -> list[dict[str, object]]:
        return [
            {
                "id": "run-1",
                "instrumentCode": "NEAR/USDT",
                "instrumentName": "NEAR",
                "market": "CRYPTO",
                "title": "NEAR/USDT 报告",
                "generatedAt": "2026-06-20T02:21:31Z",
                "summarySnippet": "完整结论请查看报告正文。",
                "canForwardToChannel": False,
            },
            {
                "id": "run-2",
                "instrumentCode": "601985.SH",
                "instrumentName": "中国核电",
                "market": "CN_A",
                "title": "601985.SH 报告",
                "generatedAt": "2026-06-19T21:10:38Z",
                "summarySnippet": "完整结论请查看报告正文。",
                "canForwardToChannel": False,
            },
        ]


class _ConnectedChannelBridgeProbe:
    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        _ = probe
        return {"state": "connected", "canSendFile": True}

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str]:
        raise AssertionError("list-saved-reports must not infer a send target")


class _ResetLlmProbe:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def reset_llm_settings_to_defaults(self, *, request_id: str) -> dict[str, object]:
        self.request_ids.append(request_id)
        return {"status": "reset"}


class _ResetDataSourcesProbe:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def reset_to_defaults(self, request_id: str) -> dict[str, object]:
        self.request_ids.append(request_id)
        return {"status": "reset"}


class _ResetChannelProbe:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def save_channel_config_via_openclaw(self, **kwargs) -> dict[str, object]:  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        return {"status": "disabled"}


class _ReportCleanupProbe:
    def __init__(self, result: ReportCleanupResult) -> None:
        self._result = result
        self.calls: list[list[str]] = []

    def delete_report_runs(self, run_ids) -> ReportCleanupResult:  # type: ignore[no-untyped-def]
        self.calls.append(list(run_ids))
        return self._result


class _SelectionControllerSnapshotProbe:
    def latest_progress_for_user(self) -> dict[str, object]:
        return {"selectionProgress": None}


class _SelectionCancelProbe:
    def __init__(self, *, cancelled: bool) -> None:
        self.cancelled = cancelled
        self.workflow_run_ids: list[str] = []

    def cancel_progress(self, *, workflow_run_id: str) -> bool:
        self.workflow_run_ids.append(workflow_run_id)
        return self.cancelled


class _RefreshSnapshotProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[SelectionMarket | None, SelectionProfile | None, str | None, bool]] = []

    def latest_progress_for_user(
        self,
        *,
        market: SelectionMarket | None = None,
        profile: SelectionProfile | None = None,
        trade_date: str | None = None,
        include_terminal: bool = True,
    ) -> dict[str, object]:
        self.calls.append((market, profile, trade_date, include_terminal))
        if (market, profile) == (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO):
            return {
                "selectionProgress": {
                    "kind": "data_refresh",
                    "workflowRunId": "sel-auto-crypto-active",
                }
            }
        return {"selectionProgress": None}


class _RefreshCancelProbe:
    def __init__(self, *, cancelled: bool) -> None:
        self.cancelled = cancelled
        self.selection_run_ids: list[str] = []

    def cancel_refresh(self, *, selection_run_id: str) -> bool:
        self.selection_run_ids.append(selection_run_id)
        return self.cancelled


class _ChatControllerProbe:
    def __init__(self) -> None:
        self.confirm_from_chat_calls: list[dict[str, object]] = []
        self.session_context_ids: list[str] = []

    def confirm_intent_draft_from_chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.confirm_from_chat_calls.append(dict(kwargs))
        return {"status": "confirmed", "context": {"contextId": kwargs["context_id"]}, "messages": []}

    def confirm_intent_draft(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("context-aware UI confirmations must use the chat confirmation path")

    def get_chat_session(self, *, context_id: str) -> dict[str, object]:
        self.session_context_ids.append(context_id)
        return {
            "context": {
                "contextId": context_id,
                "kind": "report_reading",
                "title": "BTC 报告",
                "activeTaskId": "task-1",
                "activeReportId": "run-1",
            },
            "messages": [
                {
                    "messageId": "m-1",
                    "contextKind": "report_reading",
                    "actor": "system",
                    "kind": "report_completed",
                    "text": "报告已完成。",
                    "reportId": "run-1",
                    "createdAt": "2026-06-18T10:00:00Z",
                }
            ],
        }


class _QueueProbe:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def cancel_report_task(self, *, request_id: str, task_id: str) -> dict[str, object]:
        self.calls.append({"request_id": request_id, "task_id": task_id})
        return {
            "task": {"taskId": task_id, "status": "cancelled"},
            "queueSnapshot": {
                "runningTask": None,
                "queuedTasks": [],
                "queueLimit": 10,
                "queuedCount": 0,
                "isFull": False,
            },
            "message": "已停止报告任务。",
        }
