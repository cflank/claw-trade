from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from claw_trade.selection.models import SelectionMarket, SelectionProfile
from claw_trade.web.app import build_research_ui_app
from claw_trade.web.routes_ui import (
    ConfirmIntentDraftRequest,
    confirm_intent_draft,
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


def test_app_startup_starts_owned_selection_auto_refresh_by_default(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    services = SimpleNamespace(selection_refresh_service=refresh)
    monkeypatch.delenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", raising=False)
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert refresh.started == 1

    assert refresh.stopped == 1


def test_app_startup_does_not_start_owned_selection_auto_refresh_when_disabled(tmp_path: Path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    services = SimpleNamespace(selection_refresh_service=refresh)
    monkeypatch.setenv("CLAW_TRADE_SELECTION_AUTO_REFRESH", "0")
    monkeypatch.setattr("claw_trade.web.app.build_ui_http_services", lambda _settings: services)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200

    assert refresh.started == 0
    assert refresh.stopped == 0


def test_app_startup_does_not_start_injected_selection_auto_refresh(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    refresh = _RefreshServiceProbe()
    services = SimpleNamespace(selection_refresh_service=refresh)
    app = build_research_ui_app(settings=ResearchUiServerSettings(frontend_dist=dist), services=services)  # type: ignore[arg-type]

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200

    assert refresh.started == 0
    assert refresh.stopped == 0


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


class _SelectionControllerSnapshotProbe:
    def latest_progress_for_user(self) -> dict[str, object]:
        return {"selectionProgress": None}


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
