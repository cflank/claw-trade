from __future__ import annotations

from dataclasses import replace

from claw_trade.ui_backend.worker_chat_store import (
    WorkerChatTurn,
    WorkerChatTurnStore,
    compute_worker_chat_request_fingerprint,
)


def test_same_request_and_same_fingerprint_replays_existing_turn() -> None:
    store = WorkerChatTurnStore()
    turn = _turn()

    saved = store.save_turn_if_absent(turn)
    replay = store.save_turn_if_absent(replace(turn, reply_text="late duplicate reply"))

    assert replay is saved
    assert replay == turn


def test_request_fingerprint_is_stable_for_same_payload() -> None:
    first = compute_worker_chat_request_fingerprint(
        mode="generic_worker_chat",
        worker_id="market_analyst",
        conversation_id="main",
        report_id=None,
        message="怎么看今天盘面？",
    )
    second = compute_worker_chat_request_fingerprint(
        mode="generic_worker_chat",
        worker_id="market_analyst",
        conversation_id="main",
        report_id=None,
        message="怎么看今天盘面？",
    )

    assert first == second


def _turn() -> WorkerChatTurn:
    message = "怎么看今天盘面？"
    return WorkerChatTurn(
        request_id="req-1",
        request_fingerprint=compute_worker_chat_request_fingerprint(
            mode="generic_worker_chat",
            worker_id="market_analyst",
            conversation_id="main",
            report_id=None,
            message=message,
        ),
        mode="generic_worker_chat",
        worker_id="market_analyst",
        conversation_id="main",
        report_id=None,
        message=message,
        reply_text="worker answer",
    )
