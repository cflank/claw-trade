from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from claw_trade.ui_backend.report_repository import UiProductError

WorkerChatMode = Literal["generic_worker_chat", "report_worker_chat"]


@dataclass(frozen=True)
class WorkerChatTurn:
    request_id: str
    request_fingerprint: str
    mode: WorkerChatMode
    worker_id: str
    conversation_id: str
    report_id: str | None
    message: str
    reply_text: str


class WorkerChatTurnStore:
    def __init__(self) -> None:
        self._turns_by_request_id: dict[str, WorkerChatTurn] = {}

    def replay_turn_if_present(self, *, request_id: str, request_fingerprint: str) -> WorkerChatTurn | None:
        existing = self._turns_by_request_id.get(request_id)
        if existing is None:
            return None
        if existing.request_fingerprint != request_fingerprint:
            _raise_idempotency_conflict()
        return existing

    def save_turn_if_absent(self, turn: WorkerChatTurn) -> WorkerChatTurn:
        existing = self.replay_turn_if_present(
            request_id=turn.request_id,
            request_fingerprint=turn.request_fingerprint,
        )
        if existing is not None:
            return existing
        self._turns_by_request_id[turn.request_id] = turn
        return turn


def compute_worker_chat_request_fingerprint(
    *,
    mode: WorkerChatMode,
    worker_id: str,
    conversation_id: str,
    report_id: str | None,
    message: str,
) -> str:
    payload = {
        "mode": mode,
        "worker_id": worker_id,
        "conversation_id": conversation_id,
        "report_id": report_id,
        "message": message,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _raise_idempotency_conflict() -> None:
    raise UiProductError(
        "WORKER_CHAT_IDEMPOTENCY_CONFLICT",
        "这次 worker 聊天请求与之前相同 requestId 的内容不一致，请刷新后重试。",
    )
