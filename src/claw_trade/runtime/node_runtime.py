from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def resolve_openclaw_node_bin(env: Mapping[str, str] | None = None) -> str:
    values = env or os.environ
    explicit = values.get("OPENCLAW_NODE_BIN", "").strip()
    if explicit:
        return explicit

    package_dir = values.get("OPENCLAW_PACKAGE_DIR", "").strip()
    if package_dir:
        candidate = Path(package_dir).expanduser().resolve().parent / "node"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    gateway_call_bin = values.get("OPENCLAW_GATEWAY_CALL_BIN", "").strip()
    if gateway_call_bin:
        gateway_path = Path(gateway_call_bin).expanduser().resolve()
        for candidate in (gateway_path.parent / "node", gateway_path.parent.parent / "node"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

    return "node"
