from __future__ import annotations

from dataclasses import dataclass
from typing import Any

E_INVALID_INPUT = "E_INVALID_INPUT"
E_UNSUPPORTED_MARKET = "E_UNSUPPORTED_MARKET"
E_PROFILE_RESOLVE_FAILED = "E_PROFILE_RESOLVE_FAILED"
E_CONTEXT_MISMATCH = "E_CONTEXT_MISMATCH"
E_EVIDENCE_WRITE_FAILED = "E_EVIDENCE_WRITE_FAILED"
E_PROVIDER_LAYER_FAILED = "E_PROVIDER_LAYER_FAILED"

KNOWN_ERROR_CODES = frozenset(
    {
        E_INVALID_INPUT,
        E_UNSUPPORTED_MARKET,
        E_PROFILE_RESOLVE_FAILED,
        E_CONTEXT_MISMATCH,
        E_EVIDENCE_WRITE_FAILED,
        E_PROVIDER_LAYER_FAILED,
    }
)


@dataclass(frozen=True)
class NewsDataError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.code not in KNOWN_ERROR_CODES:
            raise ValueError(f"unknown error code: {self.code}")
        if self.message.strip() == "":
            raise ValueError("message must be non-empty")
        Exception.__init__(self, f"{self.code}: {self.message}")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            payload["details"] = self.details
        return payload
