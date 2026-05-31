from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, request_id)
        return {"text": f"chat:{text}"}


def _controller() -> ChatController:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    return ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
    )


def test_report_task_card_edit_symbol_and_market_revalidate_live() -> None:
    controller = _controller()
    artifact_dir = Path(".runtime/test-artifacts/settings-s06")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    draft = controller.send_chat_message(request_id="s06-live-edit-1", context_id="ctx-s06", text="/report BTC")
    card = draft["confirmationCard"]
    mismatch = controller.confirm_intent_draft(
        request_id="s06-live-edit-2",
        draft_id=card["draftId"],
        decision="confirm",
        overrides={"market": "US"},
    )
    matched = controller.confirm_intent_draft(
        request_id="s06-live-edit-3",
        draft_id=card["draftId"],
        decision="confirm",
        overrides={"instrumentCode": "TSLA"},
    )

    assert mismatch["error"]["code"] == "INVALID_INPUT"
    assert "不匹配" in mismatch["error"]["message"]
    task = matched["task"]
    assert task["instrumentCode"] == "TSLA"
    assert task["market"] == "US"
    assert task["companyName"] == "TSLA"

    (artifact_dir / "report-task-card-edit-symbol-live.json").write_text(
        json.dumps(
            {
                "initialCard": card,
                "mismatchResult": mismatch,
                "matchedResult": matched,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
