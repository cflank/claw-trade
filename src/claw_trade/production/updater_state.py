from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claw_trade.production import paths


@dataclass(frozen=True)
class UpdaterStateStore:
    install_root: Path = paths.INSTALL_ROOT

    @property
    def path(self) -> Path:
        return self.install_root / "shared" / "updates" / "updater-state.json"

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "status": "idle",
                "userMessage": "尚未检查远程更新。",
                "updatedAt": None,
            }
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("updater-state.json 必须是 JSON object。")
        return payload

    def write(self, *, status: str, user_message: str, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "userMessage": user_message,
            "updatedAt": _now(),
            **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)
        return payload


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
