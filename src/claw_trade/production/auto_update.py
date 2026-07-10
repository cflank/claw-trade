from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from claw_trade.production import paths
from claw_trade.production.remote_update import RemoteUpdateService

_LOGGER = logging.getLogger(__name__)
DEFAULT_AUTO_UPDATE_INTERVAL_SECONDS = 6 * 60 * 60
DEFAULT_AUTO_UPDATE_STARTUP_DELAY_SECONDS = 10 * 60


def main() -> int:
    base_url = os.environ.get("CLAW_TRADE_UPDATE_BASE_URL")
    if not base_url:
        print(json.dumps({"status": "not_configured", "userMessage": "远程更新源未配置。"}, ensure_ascii=False))
        return 0

    result = run_auto_update(
        RemoteUpdateService(base_url=base_url, current_version=_current_release_version()),
        auto_install=_env_flag(os.environ, "CLAW_TRADE_AUTO_UPDATE_INSTALL"),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run_auto_update(service: RemoteUpdateService, *, auto_install: bool) -> dict[str, object]:
    check = service.check_manifest()
    if check.status != "update_available" or not auto_install:
        return check.to_user_dict()
    return service.install_checked_update().to_user_dict()


class AutoUpdateScheduler:
    def __init__(
        self,
        *,
        service_factory: Callable[[], RemoteUpdateService] | None = None,
        auto_install: bool | None = None,
        interval_seconds: int | None = None,
        startup_delay_seconds: int | None = None,
    ) -> None:
        self._service_factory = service_factory or _default_service
        self._auto_install = auto_install
        self._interval_seconds = interval_seconds or _env_int(
            os.environ,
            "CLAW_TRADE_AUTO_UPDATE_INTERVAL_SECONDS",
            DEFAULT_AUTO_UPDATE_INTERVAL_SECONDS,
        )
        self._startup_delay_seconds = startup_delay_seconds if startup_delay_seconds is not None else _env_int(
            os.environ,
            "CLAW_TRADE_AUTO_UPDATE_STARTUP_DELAY_SECONDS",
            DEFAULT_AUTO_UPDATE_STARTUP_DELAY_SECONDS,
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="claw-trade-auto-update", daemon=True)
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_seconds)

    def run_once(self) -> dict[str, object]:
        return run_auto_update(
            self._service_factory(),
            auto_install=self._auto_install
            if self._auto_install is not None
            else _env_flag(os.environ, "CLAW_TRADE_AUTO_UPDATE_INSTALL"),
        )

    def _loop(self) -> None:
        if self._stop_event.wait(max(self._startup_delay_seconds, 0)):
            return
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                _LOGGER.exception("automatic update check failed")
            if self._stop_event.wait(max(self._interval_seconds, 1)):
                return


def auto_update_enabled(env: Mapping[str, str] = os.environ) -> bool:
    if env.get("CLAW_TRADE_AUTO_UPDATE_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return bool(env.get("CLAW_TRADE_UPDATE_BASE_URL", "").strip())


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, ""))
    except ValueError:
        return default


def _default_service() -> RemoteUpdateService:
    return RemoteUpdateService(
        base_url=os.environ.get("CLAW_TRADE_UPDATE_BASE_URL"),
        current_version=_current_release_version(),
    )


def _current_release_version() -> str:
    env_version = os.environ.get("CLAW_TRADE_VERSION")
    if env_version:
        return env_version
    env_release_root = os.environ.get("CLAW_TRADE_RELEASE_ROOT")
    if env_release_root:
        release_name = Path(env_release_root).resolve(strict=False).name
        match = re.match(r"^claw-trade-production-(\d+\.\d+\.\d+)-", release_name)
        return match.group(1) if match else "0.1.0"
    try:
        release_name = paths.CURRENT_LINK.resolve(strict=False).name
    except OSError:
        return "0.1.0"
    match = re.match(r"^claw-trade-production-(\d+\.\d+\.\d+)-", release_name)
    return match.group(1) if match else "0.1.0"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "auto_update_failed", "userMessage": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
