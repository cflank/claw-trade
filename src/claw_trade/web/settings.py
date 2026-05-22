from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchUiServerSettings:
    frontend_dist: Path
    host: str = "127.0.0.1"
    port: int = 5175
    gateway_call_bin: str = "openclaw"
    gateway_ws_url: str = "ws://127.0.0.1:18789"
    gateway_timeout_ms: int = 10_000
    gateway_token: str | None = None
    gateway_password: str | None = None
