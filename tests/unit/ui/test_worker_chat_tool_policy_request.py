from __future__ import annotations

import json
from types import SimpleNamespace

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient
from claw_trade.web.routes_ui import send_worker_chat


def test_report_worker_chat_openclaw_request_has_no_unsupported_policy_fields(tmp_path) -> None:  # type: ignore[no-untyped-def]
    reports_dir = tmp_path / "run-1" / "reports"
    asset_dir = reports_dir / "assets"
    asset_dir.mkdir(parents=True)
    appendix_dir = reports_dir / "worker-appendix"
    appendix_dir.mkdir()
    appendix_dir.joinpath("07-risk_moderator.md").write_text("风险经理附录：只看已保存报告。", encoding="utf-8")
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告\n\n风险段落：高杠杆风险。",
        asset_dir=asset_dir,
    )
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(repo, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-no-policy",
        mode="report_worker_chat",
        worker_id="risk_moderator",
        text="最大风险是什么？",
        conversation_id="reader",
        report_id="report-1",
    )

    chat_call = gateway.chat_calls[0]
    assert set(chat_call) == {"session_key", "message", "idempotency_key"}
    message = str(chat_call["message"])
    for forbidden in (
        "promptProfile",
        "promptVariables",
        "toolPolicy",
        "visibleTools",
        "captureProviderPayload",
    ):
        assert forbidden not in chat_call
        assert forbidden not in message


def test_missing_report_worker_material_maps_to_409_response() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                ui_services=SimpleNamespace(
                    worker_chat_controller=_FailingWorkerChatController(
                        UiProductError("REPORT_WORKER_MATERIAL_NOT_FOUND", "没有找到这个角色可供追问的报告材料，请重新生成报告。")
                    )
                )
            )
        )
    )

    response = send_worker_chat(
        {
            "requestId": "req-missing-material",
            "mode": "report_worker_chat",
            "workerId": "risk_moderator",
            "text": "最大风险是什么？",
            "conversationId": "reader",
            "reportId": "report-1",
        },
        request,
    )

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "REPORT_WORKER_MATERIAL_NOT_FOUND"


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


class _FailingWorkerChatController:
    def __init__(self, error: UiProductError) -> None:
        self._error = error

    def send_worker_chat(
        self,
        *,
        request_id: str,
        mode: str,
        worker_id: str,
        text: str,
        conversation_id: str,
        report_id: str | None,
    ) -> object:
        _ = (request_id, mode, worker_id, text, conversation_id, report_id)
        raise self._error
