from __future__ import annotations

import json
import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from claw_trade.production import paths
from claw_trade.production.host_locks import hold_host_lock


@dataclass(frozen=True)
class ProductionMaintenanceLock:
    install_root: Path = paths.INSTALL_ROOT

    @property
    def path(self) -> Path:
        return self.install_root / "shared" / "maintenance.lock"

    @property
    def host_lock_path(self) -> Path:
        return self.install_root / paths.HOST_OPERATIONS_LOCK_PATH.name

    def is_locked(self) -> bool:
        return self.path.exists()

    def user_message(self) -> str:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return "系统正在维护中，请稍后再试。"
        reason = str(payload.get("reason") or "")
        if reason == "install_update":
            return "正在下载或安装更新，请稍后再试。"
        if reason == "factory_reset":
            return "正在恢复出厂设置，请稍后再试。"
        return "系统正在维护中，请稍后再试。"

    @contextmanager
    def hold(self, *, reason: str, request_id: str) -> Iterator[None]:
        host_lock = ExitStack()
        try:
            host_lock.enter_context(hold_host_lock(self.host_lock_path, exclusive=True, blocking=False))
        except (OSError, KeyError, ValueError) as exc:
            raise ValueError("系统正在维护中，请稍后再试。") from exc
        with host_lock:
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
