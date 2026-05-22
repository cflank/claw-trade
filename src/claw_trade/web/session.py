from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UiSession:
    context_id: str


def resolve_context_id(raw_context_id: str | None) -> str:
    text = (raw_context_id or "").strip()
    if text:
        return text
    return "ctx-normal"
