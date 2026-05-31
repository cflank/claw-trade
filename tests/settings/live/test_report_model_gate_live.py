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
        return "run-live-1"

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


def test_live_report_model_gate_blocks_report_when_model_not_ready(tmp_path: Path) -> None:
    bridge = LlmSettingsBridge(
        _rpc_client(),
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
    )
    loaded = bridge.load_llm_settings()
    draft = loaded["draft"]
    provider = str(draft.get("provider") or "").strip()
    model = str(draft.get("defaultModel") or "").strip()
    endpoint = draft.get("endpointUrl")
    assert draft["reportModelStatus"]["state"] in {"unconfigured", "saved_unverified", "failed", "ready"}

    if draft["reportModelStatus"]["state"] != "ready":
        with pytest.raises(UiBoundaryError) as exc:
            bridge.assert_report_model_ready()
        assert exc.value.code == "REPORT_MODEL_NOT_READY"

    if provider:
        tested = bridge.test_llm_via_openclaw(
            {"provider": provider, "model": model or None, "endpointUrl": endpoint},
            request_id="s01-live-test",
        )
        assert isinstance(tested["ok"], bool)
        assert tested["userMessage"]
        if not tested["ok"]:
            assert tested["error"]["code"] == "REPORT_MODEL_TEST_FAILED"

    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue, report_model_ready_checker=bridge.assert_report_model_ready),
        queue=queue,
        settings=ReportWorkflowSettings(),
        report_model_ready_checker=bridge.assert_report_model_ready,
    )
    result = controller.send_chat_message(request_id="s01-live-chat", context_id="ctx-live", text="/report TSLA")
    readiness_after = bridge.get_report_model_readiness()
    if readiness_after.ready:
        assert "error" not in result
        assert "confirmationCard" in result
    else:
        assert result["error"]["code"] == "REPORT_MODEL_NOT_READY"
        assert "默认模型已自动启用" not in result["error"]["message"]
