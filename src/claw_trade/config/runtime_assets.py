from __future__ import annotations

import os
from pathlib import Path


ENV_AGENTS_ROOT = "CLAW_TRADE_AGENTS_ROOT"
ENV_OPENCLAW_PLUGINS_ROOT = "CLAW_TRADE_OPENCLAW_PLUGINS_ROOT"


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_agents_root() -> Path:
    configured = _env_path(ENV_AGENTS_ROOT)
    if configured is not None:
        return configured
    return resolve_repo_root() / "agents"


def resolve_openclaw_plugins_root() -> Path:
    configured = _env_path(ENV_OPENCLAW_PLUGINS_ROOT)
    if configured is not None:
        return configured
    return resolve_repo_root() / "openclaw_plugins"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()
