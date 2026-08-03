from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProductionPaths:
    root: Path
    current: Path
    shared: Path
    config: Path
    data: Path
    logs: Path
    license: Path
    reports: Path
    updates: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProductionPaths":
        shared = root / "shared"
        return cls(
            root=root,
            current=root / "current",
            shared=shared,
            config=shared / "config",
            data=shared / "data",
            logs=shared / "logs",
            license=shared / "license",
            reports=shared / "reports",
            updates=shared / "updates",
        )


INSTALL_ROOT = Path("/opt/claw-trade")
HOST_OPERATIONS_LOCK_PATH = INSTALL_ROOT / "host-operations.lock"
REPORT_ACTIVE_LOCK_PATH = INSTALL_ROOT / "report-active.lock"
DATA_WORK_ACTIVE_LOCK_PATH = INSTALL_ROOT / "data-work-active.lock"
RELEASES_DIR = INSTALL_ROOT / "releases"
CURRENT_LINK = INSTALL_ROOT / "current"
RESCUE_CURRENT_LINK = INSTALL_ROOT / "rescue-current"
SHARED_DIR = INSTALL_ROOT / "shared"

FORMAL_PACKAGE_PREFIX = "claw-trade-production"
FORMAL_PACKAGE_ARCHIVE_GLOB = f"{FORMAL_PACKAGE_PREFIX}-*.tar.gz"
RELEASE_DIR_NAME_REGEX = rf"{FORMAL_PACKAGE_PREFIX}-[0-9]+\.[0-9]+\.[0-9]+-[0-9]{{8}}T[0-9]{{6}}Z"
FORMAL_PACKAGE_ARCHIVE_REGEX = rf"{RELEASE_DIR_NAME_REGEX}\.tar\.gz"
RELEASE_DIR_NAME_RULE = "archive top-level directory must equal archive basename without .tar.gz"

PRODUCTION_USER = "clawtrade"
PRODUCTION_GROUP = "clawtrade"
RELEASE_OWNER = "root"
RELEASE_GROUP = "root"
SHARED_OWNER = PRODUCTION_USER
SHARED_GROUP = PRODUCTION_GROUP
KIOSK_USER = "clawkiosk"
KIOSK_GROUP = "clawkiosk"

RELEASE_DIR_MODE = "0755"
SHARED_DIR_MODE = "0750"
UPDATE_PUBLIC_KEY_MODE = "0644"

UPDATE_PUBLIC_KEY_PATH = Path("/etc/claw-trade/update-signing-public.pem")
UPDATES_DIR = SHARED_DIR / "updates"
UPDATER_STATE_PATH = UPDATES_DIR / "updater-state.json"
UPDATER_LOCK_PATH = UPDATES_DIR / "updater.lock"
UPDATE_APPLY_LOCK_PATH = UPDATES_DIR / "apply.lock"
UPDATE_APPLY_REQUEST_PATH = UPDATES_DIR / "apply-request.json"

MAIN_UI_SERVICE_NAME = "claw-trade-ui.service"
CONTROL_SERVICE_NAME = "claw-trade-control.service"
UPDATE_APPLY_SERVICE_NAME = "claw-trade-apply-update.service"
AUTO_UPDATE_SERVICE_NAME = "claw-trade-auto-update.service"
AUTO_UPDATE_TIMER_NAME = "claw-trade-auto-update.timer"
WATCHDOG_SERVICE_NAME = "claw-trade-watchdog.service"
WATCHDOG_TIMER_NAME = "claw-trade-watchdog.timer"
WATCHDOG_HELPER_PATH = Path("/usr/local/lib/claw-trade/claw-trade-watchdog")
RESCUE_SERVICE_NAME = "claw-trade-rescue.service"
RESCUE_TRIGGER_SERVICE_NAME = "claw-trade-rescue-trigger.service"
RESCUE_BIN_NAME = "claw-trade-rescue"
RESCUE_BIND_HOST = "127.0.0.1"
MAIN_UI_PORT = 5175
RESCUE_UI_PORT = MAIN_UI_PORT
LOCAL_UI_URL = f"http://{RESCUE_BIND_HOST}:{MAIN_UI_PORT}/"
RESCUE_TAKEOVER_CONTRACT = (
    f"{RESCUE_SERVICE_NAME} conflicts with {MAIN_UI_SERVICE_NAME}; only one service may bind "
    f"{RESCUE_BIND_HOST}:{MAIN_UI_PORT}"
)

REQUIRED_SHARED_DIRS = (
    "cache",
    "config",
    "data",
    "license",
    "logs",
    "logs/diagnostics",
    "logs/factory-reset",
    "openclaw",
    "queues",
    "reports",
    "runs",
    "sessions",
    "tmp",
    "updates",
    "updates/downloads",
    "updates/logs",
)


def required_shared_paths(root: Path = INSTALL_ROOT) -> tuple[Path, ...]:
    shared = root / "shared"
    return tuple(shared / relative for relative in REQUIRED_SHARED_DIRS)
