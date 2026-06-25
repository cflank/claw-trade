from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.controller import SelectionController, _parse_select_request
from claw_trade.selection.models import SelectionMarket
from claw_trade.selection.store import SelectionRunStore
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
        _ = request
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, text, request_id)
        return {"text": "fallback"}


def _controller() -> ChatController:
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    return ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
        selection_controller=SelectionController(store=SelectionRunStore()),
    )


@pytest.mark.parametrize(
    ("text", "market", "force_refresh"),
    (
        ("/select", SelectionMarket.CN_A, False),
        ("/select 1", SelectionMarket.CN_A, False),
        ("/select CN_A", SelectionMarket.CN_A, False),
        ("/select A", SelectionMarket.CN_A, False),
        ("/select A股", SelectionMarket.CN_A, False),
        ("/select 2", SelectionMarket.CRYPTO, False),
        ("/select CRYPTO", SelectionMarket.CRYPTO, False),
        ("/select crypto", SelectionMarket.CRYPTO, False),
        ("/select 加密", SelectionMarket.CRYPTO, False),
        ("/select US", SelectionMarket.US, False),
        ("/select 3", SelectionMarket.US, False),
        ("/select refresh", SelectionMarket.CN_A, True),
        ("/select 1 refresh", SelectionMarket.CN_A, True),
        ("/select 2 refresh", SelectionMarket.CRYPTO, True),
        ("/select CRYPTO refresh", SelectionMarket.CRYPTO, True),
        ("/select refresh 2026-05-26", SelectionMarket.CN_A, True),
        ("/select 2 refresh 2026-05-26", SelectionMarket.CRYPTO, True),
    ),
)
def test_select_command_parser_contract(text: str, market: SelectionMarket, force_refresh: bool) -> None:
    request = _parse_select_request(
        raw_text=text,
        request_id="sel-contract-parser",
        user_id="ctx-parser",
        now_fn=lambda: datetime(2026, 6, 17, tzinfo=UTC),
    )

    assert request.market == market
    assert request.profile.value == market.value
    assert request.force_refresh is force_refresh


def test_select_command_parser_keeps_legacy_date_form() -> None:
    request = _parse_select_request(
        raw_text="/select 2026-05-26",
        request_id="sel-contract-date",
        user_id="ctx-parser",
        now_fn=lambda: datetime(2026, 6, 17, tzinfo=UTC),
    )

    assert request.market == SelectionMarket.CN_A
    assert request.trade_date == "2026-05-26"


@pytest.mark.parametrize("text", ("/select refresh 2", "/select refresh CRYPTO"))
def test_select_command_rejects_invalid_refresh_order(text: str) -> None:
    with pytest.raises(ValueError, match="invalid_select_command"):
        _parse_select_request(
            raw_text=text,
            request_id="sel-contract-invalid",
            user_id="ctx-parser",
            now_fn=lambda: datetime(2026, 6, 17, tzinfo=UTC),
        )


@pytest.mark.parametrize("text", ("select 1", "select 2"))
def test_bare_select_number_does_not_start_selection(text: str) -> None:
    controller = _controller()

    result = controller.send_chat_message(
        request_id=f"sel-contract-bare-{text[-1]}",
        context_id=f"ctx-bare-{text[-1]}",
        text=text,
    )

    assert "selection" not in result
    assert result["assistantReply"] == "fallback"


def test_select_crypto_command_reaches_selection_controller() -> None:
    controller = _controller()

    result = controller.send_chat_message(
        request_id="sel-contract-crypto-chat",
        context_id="ctx-crypto-chat",
        text="/select 2",
    )

    assert result["selection"]["code"] == "unavailable"
    evidence_path = Path(result["selection"]["evidencePath"])
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["market"] == "CRYPTO"
    assert payload["profile"] == "CRYPTO"


def test_select_command_is_not_report_command() -> None:
    controller = _controller()

    select_result = controller.send_chat_message(
        request_id="sel-10-contract-select",
        context_id="ctx-select",
        text="/select",
    )
    assert "selection" in select_result
    assert select_result["selection"]["code"] == "unavailable"
    assert "confirmationCard" not in select_result

    report_result = controller.send_chat_message(
        request_id="sel-10-contract-report",
        context_id="ctx-report",
        text="/report 600519.SH",
    )
    assert "selection" not in report_result
    assert "confirmationCard" in report_result
    assert report_result["confirmationCard"]["instrumentCode"] == "600519.SH"


def test_select_command_uses_single_worker_minimal_policy_in_evidence() -> None:
    controller = _controller()

    result = controller.send_chat_message(
        request_id="sel-10-contract-policy",
        context_id="ctx-policy",
        text="/select",
    )

    evidence_path = Path(result["selection"]["evidencePath"])
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["entry_point"] == "select_command"
    assert payload["system_context_policy"] == "single_worker_minimal"
