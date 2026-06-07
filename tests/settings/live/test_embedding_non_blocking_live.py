from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.settings_service import UiBoundaryError
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def create_run(self, request):  # type: ignore[no-untyped-def]
        _ = request
        return "run-live-s04"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, text, request_id)
        return {"text": "ok"}


def _rpc_client() -> OpenClawGatewayRpcClient:
    return OpenClawGatewayRpcClient(
        gateway_call_bin=os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "openclaw"),
        gateway_ws_url=os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789"),
        timeout_ms=int(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT_MS", "10000")),
        token=os.environ.get("OPENCLAW_GATEWAY_TOKEN") or None,
        password=os.environ.get("OPENCLAW_GATEWAY_PASSWORD") or None,
    )


def test_live_embedding_unavailable_does_not_become_report_hard_gate(tmp_path: Path) -> None:
    embedding_env = tmp_path / ".env.local"
    embedding_env.write_text(
        "OPENVIKING_EMBEDDING_PROVIDER=openai\nOPENVIKING_EMBEDDING_MODEL=\n",
        encoding="utf-8",
    )
    bridge = LlmSettingsBridge(
        _rpc_client(),
        embedding_env_path=embedding_env,
        report_model_status_path=tmp_path / "report-model-status.json",
    )
    loaded = bridge.load_llm_settings()
    draft = loaded["draft"]
    embedding = draft["embedding"]
    assert embedding["provider"] == "openai"
    assert embedding["model"] == ""
    assert embedding["enabled"] is False

    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue, report_model_ready_checker=bridge.assert_report_model_ready),
        queue=queue,
        settings=ReportWorkflowSettings(),
        report_model_ready_checker=bridge.assert_report_model_ready,
    )

    readiness = bridge.get_report_model_readiness()
    result = controller.send_chat_message(request_id="s04-live-chat", context_id="ctx-live-s04", text="/report TSLA")
    if readiness.ready:
        bridge.assert_report_model_ready()
        assert "error" not in result
        assert "confirmationCard" in result
    else:
        with pytest.raises(UiBoundaryError):
            bridge.assert_report_model_ready()
        assert result["error"]["code"] == "REPORT_MODEL_NOT_READY"
        assert "embedding" not in result["error"]["message"].lower()
