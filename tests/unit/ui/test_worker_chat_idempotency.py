from __future__ import annotations

from dataclasses import replace

import pytest

from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.worker_chat_store import (
    WorkerChatTurn,
    WorkerChatTurnStore,
    compute_worker_chat_request_fingerprint,
)


def test_same_request_different_fingerprint_conflicts() -> None:
    store = WorkerChatTurnStore()
    turn = _turn()

    store.save_turn_if_absent(turn)

    with pytest.raises(UiProductError) as exc:
        store.save_turn_if_absent(replace(turn, request_fingerprint="different"))

    assert exc.value.code == "WORKER_CHAT_IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("mode", "generic_worker_chat"),
        ("worker_id", "market_analyst"),
        ("conversation_id", "workspace"),
        ("report_id", "report-2"),
        ("message", "还有什么风险？"),
    ],
)
def test_request_fingerprint_changes_when_payload_field_changes(field: str, changed_value: str) -> None:
    payload = {
        "mode": "report_worker_chat",
        "worker_id": "risk_moderator",
        "conversation_id": "reader",
        "report_id": "report-1",
        "message": "最大风险是什么？",
    }
    first = compute_worker_chat_request_fingerprint(
        mode=payload["mode"],
        worker_id=payload["worker_id"],
        conversation_id=payload["conversation_id"],
        report_id=payload["report_id"],
        message=payload["message"],
    )
    payload[field] = changed_value
    second = compute_worker_chat_request_fingerprint(
        mode=payload["mode"],
        worker_id=payload["worker_id"],
        conversation_id=payload["conversation_id"],
        report_id=payload["report_id"],
        message=payload["message"],
    )

    assert first != second


def test_request_fingerprint_is_stable_for_same_payload() -> None:
    first = compute_worker_chat_request_fingerprint(
        mode="report_worker_chat",
        worker_id="risk_moderator",
        conversation_id="reader",
        report_id="report-1",
        message="最大风险是什么？",
    )
    second = compute_worker_chat_request_fingerprint(
        mode="report_worker_chat",
        worker_id="risk_moderator",
        conversation_id="reader",
        report_id="report-1",
        message="最大风险是什么？",
    )

    assert first == second


def _turn() -> WorkerChatTurn:
    message = "最大风险是什么？"
    return WorkerChatTurn(
        request_id="req-1",
        request_fingerprint=compute_worker_chat_request_fingerprint(
            mode="report_worker_chat",
            worker_id="risk_moderator",
            conversation_id="reader",
            report_id="report-1",
            message=message,
        ),
        mode="report_worker_chat",
        worker_id="risk_moderator",
        conversation_id="reader",
        report_id="report-1",
        message=message,
        reply_text="worker answer",
    )
