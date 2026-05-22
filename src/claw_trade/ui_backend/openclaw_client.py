from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class OpenClawChatReply:
    text: str


class OpenClawChatTransport(Protocol):
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> Any: ...


class OpenClawGatewayClient:
    def __init__(self, transport: OpenClawChatTransport) -> None:
        self._transport = transport

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> OpenClawChatReply:
        payload = self._transport.chat_send(context_id=context_id, text=text, request_id=request_id)
        reply = _extract_reply_text(payload)
        return OpenClawChatReply(text=reply)


def _extract_reply_text(payload: Any) -> str:
    if isinstance(payload, str):
        text = payload.strip()
        if text:
            return text
    if isinstance(payload, dict):
        for key in ("text", "content", "reply", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ValueError("assistant_unavailable")
