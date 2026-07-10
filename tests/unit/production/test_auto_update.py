from __future__ import annotations

from claw_trade.production.auto_update import AutoUpdateScheduler, _current_release_version, _env_flag, auto_update_enabled, run_auto_update
from claw_trade.production.remote_update import RemoteManifestCheck, RemoteUpdateInstall
from claw_trade.production.update_manifest import UpdateManifest


def test_auto_update_checks_without_installing() -> None:
    service = _FakeUpdateService(check_status="update_available")

    result = run_auto_update(service, auto_install=False)

    assert result["status"] == "update_available"
    assert service.checked is True
    assert service.installed is False


def test_auto_update_installs_when_enabled() -> None:
    service = _FakeUpdateService(check_status="update_available")

    result = run_auto_update(service, auto_install=True)

    assert result["status"] == "restart_scheduled"
    assert result["version"] == "1.2.3"
    assert service.checked is True
    assert service.installed is True


def test_auto_update_does_not_install_when_up_to_date() -> None:
    service = _FakeUpdateService(check_status="up_to_date")

    result = run_auto_update(service, auto_install=True)

    assert result["status"] == "up_to_date"
    assert service.installed is False


def test_env_flag_accepts_common_true_values() -> None:
    assert _env_flag({"CLAW_TRADE_AUTO_UPDATE_INSTALL": "1"}, "CLAW_TRADE_AUTO_UPDATE_INSTALL") is True
    assert _env_flag({"CLAW_TRADE_AUTO_UPDATE_INSTALL": "true"}, "CLAW_TRADE_AUTO_UPDATE_INSTALL") is True
    assert _env_flag({"CLAW_TRADE_AUTO_UPDATE_INSTALL": "0"}, "CLAW_TRADE_AUTO_UPDATE_INSTALL") is False
    assert _env_flag({}, "CLAW_TRADE_AUTO_UPDATE_INSTALL") is False


def test_auto_update_enabled_requires_update_source_unless_disabled() -> None:
    assert auto_update_enabled({"CLAW_TRADE_UPDATE_BASE_URL": "https://updates.example.com/stable/"}) is True
    assert auto_update_enabled({"CLAW_TRADE_UPDATE_BASE_URL": ""}) is False
    assert (
        auto_update_enabled(
            {
                "CLAW_TRADE_UPDATE_BASE_URL": "https://updates.example.com/stable/",
                "CLAW_TRADE_AUTO_UPDATE_ENABLED": "0",
            }
        )
        is False
    )


def test_current_release_version_prefers_process_version(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_VERSION", "1.2.4")
    monkeypatch.setenv("CLAW_TRADE_RELEASE_ROOT", "/opt/claw-trade/releases/claw-trade-production-1.2.3-20260626T120000Z")

    assert _current_release_version() == "1.2.4"


def test_current_release_version_reads_release_root(monkeypatch) -> None:
    monkeypatch.delenv("CLAW_TRADE_VERSION", raising=False)
    monkeypatch.setenv("CLAW_TRADE_RELEASE_ROOT", "/opt/claw-trade/releases/claw-trade-production-1.2.3-20260626T120000Z")

    assert _current_release_version() == "1.2.3"


def test_current_release_version_falls_back_for_invalid_release_root(monkeypatch) -> None:
    monkeypatch.delenv("CLAW_TRADE_VERSION", raising=False)
    monkeypatch.setenv("CLAW_TRADE_RELEASE_ROOT", "/opt/claw-trade/current")

    assert _current_release_version() == "0.1.0"


def test_scheduler_run_once_uses_fresh_service_factory() -> None:
    services = [_FakeUpdateService(check_status="update_available"), _FakeUpdateService(check_status="up_to_date")]
    scheduler = AutoUpdateScheduler(service_factory=lambda: services.pop(0), auto_install=False, startup_delay_seconds=0)

    first = scheduler.run_once()
    second = scheduler.run_once()

    assert first["status"] == "update_available"
    assert second["status"] == "up_to_date"
    assert services == []


class _FakeUpdateService:
    def __init__(self, *, check_status: str) -> None:
        self._check_status = check_status
        self.checked = False
        self.installed = False

    def check_manifest(self) -> RemoteManifestCheck:
        self.checked = True
        manifest = (
            UpdateManifest(
                product="claw-trade",
                channel="stable",
                version="1.2.3",
                arch="linux-x86_64",
                archive="claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
                sha256="a" * 64,
                created_at="2026-06-26T00:00:00Z",
                min_current_version="1.0.0",
            )
            if self._check_status == "update_available"
            else None
        )
        return RemoteManifestCheck(
            status=self._check_status,
            manifest=manifest,
            user_message="发现可安装版本 1.2.3。" if manifest else "当前已是最新版本。",
        )

    def install_checked_update(self) -> RemoteUpdateInstall:
        self.installed = True
        return RemoteUpdateInstall(
            status="restart_scheduled",
            version="1.2.3",
            user_message="已安装版本 1.2.3；正在重启服务并检查健康状态。",
        )
