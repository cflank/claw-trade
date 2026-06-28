from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from claw_trade.production import paths
from claw_trade.production.maintenance_lock import ProductionMaintenanceLock


FACTORY_RESET_CONFIRMATION = "RESET_CLAW_TRADE"
INSTALL_ROOT_BASENAME = "claw-trade"

RESET_SHARED_DIRS = (
    "cache",
    "config",
    "data",
    "openclaw",
    "queues",
    "reports",
    "sessions",
    "tmp",
)


@dataclass(frozen=True)
class FactoryResetResult:
    status: str
    reset_paths: tuple[str, ...]
    preserved_paths: tuple[str, ...]
    audit_log: str
    finished_at: str

    def to_user_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "resetPaths": list(self.reset_paths),
            "preservedPaths": list(self.preserved_paths),
            "auditLog": self.audit_log,
            "finishedAt": self.finished_at,
            "userMessage": "应用状态已按第一版基线清空；授权和更新文件已保留。请手动重启服务完成后续恢复。",
        }


class FactoryResetService:
    def __init__(self, *, install_root: Path = paths.INSTALL_ROOT, maintenance_lock: ProductionMaintenanceLock | None = None) -> None:
        self._install_root = install_root
        self._shared = install_root / "shared"
        self._maintenance_lock = maintenance_lock or ProductionMaintenanceLock(install_root=install_root)

    def status_for_user(self) -> dict[str, object]:
        return {
            "installRoot": str(self._install_root),
            "sharedRoot": str(self._shared),
            "resetPaths": [str(self._shared / item) for item in RESET_SHARED_DIRS],
            "preservedPaths": [
                str(self._install_root / "current"),
                str(self._install_root / "releases"),
                str(self._shared / "license"),
                str(self._shared / "updates"),
                str(self._shared / "logs" / "factory-reset"),
            ],
            "confirmation": FACTORY_RESET_CONFIRMATION,
            "maintenanceLocked": self._maintenance_lock.is_locked(),
        }

    def run(self, *, request_id: str, confirmation: str) -> FactoryResetResult:
        safe_request_id = _safe_request_id(request_id)
        if confirmation != FACTORY_RESET_CONFIRMATION:
            raise ValueError("恢复出厂需要二次确认。")

        self._validate_delete_boundaries()
        with self._maintenance_lock.hold(reason="factory_reset", request_id=safe_request_id):
            audit_dir = self._shared / "logs" / "factory-reset"
            audit_dir.mkdir(parents=True, exist_ok=True)
            started_at = _now()
            audit_log = audit_dir / f"factory-reset-{safe_request_id}.json"
            reset_paths: list[str] = []

            for relative in RESET_SHARED_DIRS:
                target = self._shared / relative
                _delete_path(target)
                target.mkdir(parents=True, exist_ok=True)
                reset_paths.append(str(target))

            self._reset_logs_preserving_factory_reset(audit_dir)
            self._ensure_required_shared_dirs()

            finished_at = _now()
            result = FactoryResetResult(
                status="completed",
                reset_paths=tuple(reset_paths),
                preserved_paths=tuple(str(item) for item in self.status_for_user()["preservedPaths"]),  # type: ignore[index]
                audit_log=str(audit_log),
                finished_at=finished_at,
            )
            audit_log.write_text(
                json.dumps(
                    {
                        "requestId": safe_request_id,
                        "startedAt": started_at,
                        "finishedAt": finished_at,
                        "result": result.to_user_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return result

    def _ensure_required_shared_dirs(self) -> None:
        for target in paths.required_shared_paths(self._install_root):
            _reject_symlink(target)
            _assert_inside(self._shared, target)
            target.mkdir(parents=True, exist_ok=True)

    def _reset_logs_preserving_factory_reset(self, audit_dir: Path) -> None:
        logs_dir = self._shared / "logs"
        _reject_symlink(logs_dir)
        _reject_symlink(audit_dir)
        _assert_inside(self._shared, logs_dir)
        _assert_inside(logs_dir, audit_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        for child in logs_dir.iterdir():
            if child == audit_dir:
                continue
            _reject_symlink(child)
            _delete_path(child)
        audit_dir.mkdir(parents=True, exist_ok=True)

    def _validate_delete_boundaries(self) -> None:
        install_root = self._install_root
        if not install_root.is_absolute():
            raise ValueError("恢复出厂安装根必须是绝对路径。")
        install_root_resolved = install_root.resolve(strict=False)
        if install_root_resolved.name != INSTALL_ROOT_BASENAME:
            raise ValueError("恢复出厂安装根必须指向 claw-trade 应用目录。")
        _reject_symlink(install_root)
        if install_root.exists() and not install_root.is_dir():
            raise ValueError("恢复出厂安装根不是目录。")
        if self._shared.exists() and not self._shared.is_dir():
            raise ValueError("恢复出厂 shared 根不是目录。")
        _reject_symlink(self._shared)
        _assert_inside(install_root_resolved, self._shared)
        for relative in RESET_SHARED_DIRS:
            target = self._shared / relative
            _reject_symlink(target)
            _assert_inside(self._shared, target)
        logs_dir = self._shared / "logs"
        audit_dir = logs_dir / "factory-reset"
        _reject_symlink(logs_dir)
        _reject_symlink(audit_dir)
        _assert_inside(self._shared, logs_dir)
        _assert_inside(logs_dir, audit_dir)


def _delete_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def _assert_inside(root: Path, path: Path) -> None:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if path_resolved == root_resolved:
        return
    if not path_resolved.is_relative_to(root_resolved):
        raise ValueError("恢复出厂路径越界。")


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("恢复出厂路径不能是符号链接。")


def _safe_request_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    return safe[:80] or "factory-reset"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
