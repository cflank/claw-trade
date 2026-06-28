from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from claw_trade.production import paths


@dataclass(frozen=True)
class ProductionMaintenanceLock:
    install_root: Path = paths.INSTALL_ROOT

    @property
    def path(self) -> Path:
        return self.install_root / "shared" / "maintenance.lock"

    def is_locked(self) -> bool:
        return self.path.exists()

    @contextmanager
    def hold(self, *, reason: str, request_id: str) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "reason": reason,
            "requestId": request_id,
            "createdAt": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
        }
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ValueError("系统正在维护中，请稍后再试。") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
            yield
        finally:
            self.path.unlink(missing_ok=True)
