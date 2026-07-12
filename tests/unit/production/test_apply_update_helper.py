from __future__ import annotations

import importlib.machinery
import importlib.util
import grp
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "packaging" / "production" / "root-helper" / "claw-trade-apply-update"
TEST_LOCK_PATH_ENV = "CLAW_TRADE_TEST_HOST_OPERATION_LOCK_PATH"
TEST_REPORT_LOCK_PATH_ENV = "CLAW_TRADE_TEST_REPORT_ACTIVE_LOCK_PATH"


@pytest.fixture(autouse=True)
def isolated_host_operation_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host_lock = tmp_path / "host-operations.lock"
    report_lock = tmp_path / "report-active.lock"
    for path in (host_lock, report_lock):
        path.touch(mode=0o660)
        path.chmod(0o660)
    monkeypatch.setenv(TEST_LOCK_PATH_ENV, str(host_lock))
    monkeypatch.setenv(TEST_REPORT_LOCK_PATH_ENV, str(report_lock))


def test_apply_update_helper_switches_current_after_health_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    installed_releases: list[Path] = []
    health_checks: list[dict[str, object]] = []
    restart_calls: list[str] = []
    monkeypatch.setattr(module, "install_host_files", lambda release_dir: installed_releases.append(release_dir))
    monkeypatch.setattr(module, "restart_main_services", lambda: restart_calls.append("restart") or True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda **kwargs: health_checks.append(kwargs) or True)

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 0
    assert installed_releases == [new_release]
    assert restart_calls == ["restart"]
    assert health_checks == [{"version": "1.2.3", "release_dir": new_release}]
    assert (install_root / "current").resolve(strict=False) == new_release
    assert json.loads((install_root / "shared" / "updates" / "updater-state.json").read_text())["status"] == "installed"
    assert request_path.exists() is False
    assert not (install_root / "shared" / "updates" / "apply.lock").exists()


def test_apply_update_helper_rolls_back_when_health_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    health_results = iter([False, True])
    restart_calls: list[str] = []
    monkeypatch.setattr(module, "install_host_files", lambda _release_dir: None)
    monkeypatch.setattr(module, "restart_main_services", lambda: restart_calls.append("restart") or True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda **_kwargs: next(health_results))
    real_run = module.subprocess.run

    def fake_run(args, **kwargs):
        if args == ["/bin/systemctl", "start", "claw-trade-rescue-trigger.service"]:
            return None
        return real_run(args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 1
    assert restart_calls == ["restart", "restart"]
    assert (install_root / "current").resolve(strict=False) == old_release
    assert json.loads((install_root / "shared" / "updates" / "updater-state.json").read_text())["status"] == "rollback_succeeded"
    assert request_path.exists() is False
    assert not (install_root / "shared" / "updates" / "apply.lock").exists()


def test_apply_update_helper_ignores_request_previous_for_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, _new_release = _apply_request(tmp_path)
    other_release = install_root / "releases" / "claw-trade-production-1.2.1-20260501T000000Z"
    other_release.mkdir(parents=True)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["previous"] = str(other_release)
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    (install_root / "current").symlink_to(old_release)
    health_results = iter([False, True])
    monkeypatch.setattr(module, "install_host_files", lambda _release_dir: None)
    monkeypatch.setattr(module, "restart_main_services", lambda: True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda **_kwargs: next(health_results))

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 1
    assert (install_root / "current").resolve(strict=False) == old_release


def test_apply_update_helper_adopts_pending_apply_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    lock_path = install_root / "shared" / "updates" / "apply.lock"
    lock_path.write_text("pending:123", encoding="utf-8")
    monkeypatch.setattr(module, "install_host_files", lambda _release_dir: None)
    monkeypatch.setattr(module, "restart_main_services", lambda: True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda **_kwargs: True)

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 0
    assert (install_root / "current").resolve(strict=False) == new_release
    assert request_path.exists() is False
    assert lock_path.exists() is False


def test_apply_update_helper_rejects_existing_apply_lock(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, _old_release, _new_release = _apply_request(tmp_path)
    lock_path = install_root / "shared" / "updates" / "apply.lock"
    lock_path.write_text("busy", encoding="utf-8")

    with pytest.raises(ValueError, match="已有更新应用任务"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_symlinked_updates_directory(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, _old_release, _new_release = _apply_request(tmp_path)
    updates = install_root / "shared" / "updates"
    outside = tmp_path / "outside-updates"
    outside.mkdir()
    for child in updates.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    updates.rmdir()
    updates.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="updates 不能是符号链接"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_request_manifest_mismatch(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, old_release, _new_release = _apply_request(tmp_path)
    request_path.write_text(
        json.dumps(
            {
                "version": "9.9.9",
                "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
                "archiveSha256": "a" * 64,
                "target": str(install_root / "releases" / "claw-trade-production-9.9.9-20260626T120000Z"),
                "previous": str(old_release),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="apply request 与 manifest 不匹配"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_manifest_archive_version_mismatch(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, old_release, _new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["archive"] = "claw-trade-production-1.2.2-20260626T120000Z.tar.gz"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    manifest_path = install_root / "shared" / "updates" / "downloads" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive"] = payload["archive"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive = _build_release_archive("claw-trade-production-1.2.2-20260626T120000Z")
    downloads = install_root / "shared" / "updates" / "downloads"
    (downloads / payload["archive"]).write_bytes(archive)
    key = Ed25519PrivateKey.generate()
    _public_key_path(install_root).write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    (downloads / "manifest.json.sig").write_bytes(key.sign(manifest_path.read_bytes()))
    (downloads / f"{payload['archive']}.sig").write_bytes(key.sign(archive))

    with pytest.raises(ValueError, match="archive 版本与 manifest version 不匹配"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_version_not_newer_than_current(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, _old_release, new_release = _apply_request(tmp_path)
    new_release.mkdir(parents=True)
    (install_root / "current").symlink_to(new_release)

    with pytest.raises(ValueError, match="manifest version 不高于当前版本"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_stops_legacy_pid_file_process(tmp_path: Path) -> None:
    module = _load_helper()
    pid_file = tmp_path / "claw-trade-ui.pid"
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "claw_trade.web.app"])
    try:
        pid_file.write_text(str(process.pid), encoding="utf-8")

        module.stop_legacy_pid_file(pid_file)

        assert process.wait(timeout=5) != 0
        assert not pid_file.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_apply_update_helper_does_not_kill_pid_without_marker(tmp_path: Path) -> None:
    module = _load_helper()
    pid_file = tmp_path / "claw-trade-ui.pid"
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file.write_text(str(process.pid), encoding="utf-8")

        module.stop_legacy_pid_file(pid_file)

        assert process.poll() is None
        assert not pid_file.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_apply_update_helper_stops_legacy_processes_before_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, _new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    events: list[str] = []
    monkeypatch.setattr(module, "install_host_files", lambda _release_dir: None)
    monkeypatch.setattr(module, "stop_legacy_processes", lambda: events.append("stop_legacy"))
    monkeypatch.setattr(module, "restart_main_services", lambda: events.append("restart") or True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda **_kwargs: True)

    assert module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root)) == 0

    assert events == ["stop_legacy", "restart"]


def test_apply_waits_for_host_operation_lock_without_losing_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    lock_path = tmp_path / "host-operations.lock"
    request_path = tmp_path / "apply-request.json"
    request_path.write_text("{}", encoding="utf-8")
    entered = threading.Event()
    finished = threading.Event()
    results: list[int] = []
    monkeypatch.setattr(module, "HOST_OPERATION_LOCK_PATH", lock_path)
    monkeypatch.setattr(module, "run_locked", lambda **_kwargs: entered.set() or 0)

    def apply() -> None:
        results.append(module.run(install_root=tmp_path, request_path=request_path))
        finished.set()

    with module.host_operation_lock():
        thread = threading.Thread(target=apply)
        thread.start()
        assert entered.wait(0.1) is False
        assert request_path.exists()
    assert finished.wait(2)
    thread.join(timeout=2)

    assert entered.is_set()
    assert results == [0]


def test_apply_locks_host_before_opening_report_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    events: list[str] = []
    real_open = module.open_verified_lock
    real_flock = module.fcntl.flock

    def tracked_open(path: Path) -> int:
        events.append(f"open:{path.name}")
        return real_open(path)

    def tracked_flock(fd: int, operation: int) -> None:
        events.append("flock:host")
        real_flock(fd, operation)

    monkeypatch.setattr(module, "open_verified_lock", tracked_open)
    monkeypatch.setattr(module.fcntl, "flock", tracked_flock)

    with module.host_operation_lock():
        pass

    assert events == ["open:host-operations.lock", "flock:host", "open:report-active.lock"]


def test_apply_rejects_missing_report_lock_without_creating_it(tmp_path: Path) -> None:
    module = _load_helper()
    module.REPORT_ACTIVE_LOCK_PATH.unlink()

    with pytest.raises(ValueError, match="锁文件不存在或无法打开"):
        module.run(install_root=tmp_path, request_path=tmp_path / "apply-request.json")

    assert module.REPORT_ACTIVE_LOCK_PATH.exists() is False


def test_install_host_files_copies_units_and_restores_enabled_active_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    release_dir = tmp_path / "release"
    (release_dir / "root-helper").mkdir(parents=True)
    (release_dir / "systemd").mkdir()
    (release_dir / "sudoers").mkdir()
    (release_dir / "root-helper" / "claw-trade-apply-update").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (release_dir / "root-helper" / "claw-trade-watchdog").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (release_dir / "systemd" / "claw-trade-control.service").write_text("[Service]\n", encoding="utf-8")
    (release_dir / "systemd" / "claw-trade-ui.service").write_text("[Service]\n", encoding="utf-8")
    (release_dir / "systemd" / "claw-trade-auto-update.timer").write_text("[Timer]\n", encoding="utf-8")
    (release_dir / "sudoers" / "claw-trade-update").write_text("clawtrade ALL=(root) NOPASSWD: /bin/systemctl start --no-block claw-trade-apply-update.service\n", encoding="utf-8")
    host_helper = tmp_path / "host" / "claw-trade-apply-update"
    watchdog_helper = tmp_path / "host" / "claw-trade-watchdog"
    systemd_dir = tmp_path / "systemd"
    sudoers_path = tmp_path / "sudoers.d" / "claw-trade-update"
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "HOST_HELPER_PATH", host_helper)
    monkeypatch.setattr(module, "WATCHDOG_HELPER_PATH", watchdog_helper)
    monkeypatch.setattr(module, "SYSTEMD_DIR", systemd_dir)
    monkeypatch.setattr(module, "SUDOERS_PATH", sudoers_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.install_host_files(release_dir)

    assert host_helper.read_text(encoding="utf-8") == "#!/usr/bin/env python3\n"
    assert watchdog_helper.read_text(encoding="utf-8") == "#!/usr/bin/env python3\n"
    assert (systemd_dir / "claw-trade-control.service").read_text(encoding="utf-8") == "[Service]\n"
    assert (systemd_dir / "claw-trade-ui.service").read_text(encoding="utf-8") == "[Service]\n"
    assert (systemd_dir / "claw-trade-auto-update.timer").read_text(encoding="utf-8") == "[Timer]\n"
    assert oct(host_helper.stat().st_mode & 0o777) == "0o755"
    assert oct(watchdog_helper.stat().st_mode & 0o777) == "0o755"
    assert oct(sudoers_path.stat().st_mode & 0o777) == "0o440"
    visudo_calls = [call for call in calls if call[:2] == ["/usr/sbin/visudo", "-cf"]]
    assert len(visudo_calls) == 1
    assert visudo_calls[0][2] != str(sudoers_path)
    assert not Path(visudo_calls[0][2]).exists()
    assert ["/bin/systemctl", "daemon-reload"] in calls
    assert ["/bin/systemctl", "enable", "claw-trade-control.service", "claw-trade-ui.service", "claw-trade-auto-update.timer"] in calls
    assert ["/bin/systemctl", "enable", "claw-trade-watchdog.timer"] in calls
    assert ["/bin/systemctl", "start", "claw-trade-watchdog.timer"] in calls
    assert ["/bin/systemctl", "disable", "claw-trade-watchdog.timer"] not in calls
    assert ["/bin/systemctl", "stop", "claw-trade-watchdog.timer"] not in calls


def test_install_host_files_restores_disabled_inactive_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    release_dir = tmp_path / "release"
    (release_dir / "root-helper").mkdir(parents=True)
    (release_dir / "systemd").mkdir()
    (release_dir / "sudoers").mkdir()
    (release_dir / "root-helper" / "claw-trade-apply-update").write_text("helper\n", encoding="utf-8")
    (release_dir / "root-helper" / "claw-trade-watchdog").write_text("watchdog\n", encoding="utf-8")
    (release_dir / "systemd" / "claw-trade-watchdog.service").write_text("[Service]\n", encoding="utf-8")
    (release_dir / "sudoers" / "claw-trade-update").write_text("sudoers\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        returncode = 1 if args[1:3] == ["is-enabled", "--quiet"] else 3 if args[1:3] == ["is-active", "--quiet"] else 0
        return subprocess.CompletedProcess(args, returncode)

    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "HOST_HELPER_PATH", tmp_path / "host" / "claw-trade-apply-update")
    monkeypatch.setattr(module, "WATCHDOG_HELPER_PATH", tmp_path / "host" / "claw-trade-watchdog")
    monkeypatch.setattr(module, "SYSTEMD_DIR", tmp_path / "systemd-host")
    monkeypatch.setattr(module, "SUDOERS_PATH", tmp_path / "sudoers.d" / "claw-trade-update")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.install_host_files(release_dir)

    assert ["/bin/systemctl", "disable", "claw-trade-watchdog.timer"] in calls
    assert ["/bin/systemctl", "stop", "claw-trade-watchdog.timer"] in calls
    assert ["/bin/systemctl", "enable", "claw-trade-watchdog.timer"] not in calls
    assert ["/bin/systemctl", "start", "claw-trade-watchdog.timer"] not in calls


def test_install_host_files_does_not_replace_sudoers_when_validation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    release_dir = tmp_path / "release"
    (release_dir / "root-helper").mkdir(parents=True)
    (release_dir / "systemd").mkdir()
    (release_dir / "sudoers").mkdir()
    (release_dir / "root-helper" / "claw-trade-apply-update").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (release_dir / "root-helper" / "claw-trade-watchdog").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (release_dir / "systemd" / "claw-trade-control.service").write_text("[Service]\n", encoding="utf-8")
    (release_dir / "sudoers" / "claw-trade-update").write_text("broken sudoers\n", encoding="utf-8")
    host_helper = tmp_path / "host" / "claw-trade-apply-update"
    watchdog_helper = tmp_path / "host" / "claw-trade-watchdog"
    systemd_dir = tmp_path / "systemd"
    sudoers_path = tmp_path / "sudoers.d" / "claw-trade-update"
    sudoers_path.parent.mkdir(parents=True)
    sudoers_path.write_text("old sudoers\n", encoding="utf-8")

    def fake_run(args, **_kwargs):
        if args[:2] == ["/usr/sbin/visudo", "-cf"]:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "HOST_HELPER_PATH", host_helper)
    monkeypatch.setattr(module, "WATCHDOG_HELPER_PATH", watchdog_helper)
    monkeypatch.setattr(module, "SYSTEMD_DIR", systemd_dir)
    monkeypatch.setattr(module, "SUDOERS_PATH", sudoers_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        module.install_host_files(release_dir)

    assert sudoers_path.read_text(encoding="utf-8") == "old sudoers\n"
    assert not list(sudoers_path.parent.glob(".claw-trade-update.*.tmp"))


def test_stop_legacy_processes_only_uses_pid_files(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    seen_pid_files: list[Path] = []
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "LEGACY_PID_FILES", (Path("/tmp/a.pid"), Path("/tmp/b.pid")))
    monkeypatch.setattr(module, "stop_legacy_pid_file", lambda pid_file: seen_pid_files.append(pid_file))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.subprocess, "run", lambda args, **_kwargs: calls.append(list(args)) or subprocess.CompletedProcess(args, 0))

    module.stop_legacy_processes()

    assert seen_pid_files == [Path("/tmp/a.pid"), Path("/tmp/b.pid")]
    assert calls == []


def test_apply_update_helper_rejects_stale_health_without_target_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    release_dir = _write_release_tree(tmp_path / "claw-trade-production-1.2.3-20260626T120000Z")
    server = _start_fake_ui(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/api/ui/get-production-maintenance-status": (200, b'{"update":{"status":"up_to_date"}}'),
            "/": (200, b'<script type="module" src="/assets/index-new.js"></script>'),
            "/assets/index-new.js": (200, b"console.log('new');"),
        }
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    try:
        assert not module.wait_for_ui_health(
            version="1.2.3",
            release_dir=release_dir,
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout_seconds=0.01,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_apply_update_helper_accepts_matching_version_and_frontend_asset(tmp_path: Path) -> None:
    module = _load_helper()
    release_dir = _write_release_tree(tmp_path / "claw-trade-production-1.2.3-20260626T120000Z")
    server = _start_fake_ui(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/api/ui/get-production-maintenance-status": (200, b'{"update":{"currentVersion":"1.2.3"}}'),
            "/": (200, b'<script type="module" src="/assets/index-new.js"></script>'),
            "/assets/index-new.js": (200, b"console.log('new');"),
        }
    )
    try:
        assert module.wait_for_ui_health(
            version="1.2.3",
            release_dir=release_dir,
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout_seconds=1,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_apply_update_helper_rejects_served_html_with_old_frontend_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    release_dir = _write_release_tree(tmp_path / "claw-trade-production-1.2.3-20260626T120000Z")
    server = _start_fake_ui(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/api/ui/get-production-maintenance-status": (200, b'{"update":{"currentVersion":"1.2.3"}}'),
            "/": (200, b'<script type="module" src="/assets/index-old.js"></script>'),
            "/assets/index-new.js": (200, b"console.log('new');"),
        }
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    try:
        assert not module.wait_for_ui_health(
            version="1.2.3",
            release_dir=release_dir,
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout_seconds=0.01,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_apply_update_helper_rejects_frontend_asset_hash_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    release_dir = _write_release_tree(tmp_path / "claw-trade-production-1.2.3-20260626T120000Z")
    server = _start_fake_ui(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/api/ui/get-production-maintenance-status": (200, b'{"update":{"currentVersion":"1.2.3"}}'),
            "/": (200, b'<script type="module" src="/assets/index-new.js"></script>'),
            "/assets/index-new.js": (200, b"console.log('old');"),
        }
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    try:
        assert not module.wait_for_ui_health(
            version="1.2.3",
            release_dir=release_dir,
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout_seconds=0.01,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_apply_update_helper_rejects_release_without_frontend_asset(tmp_path: Path) -> None:
    module = _load_helper()
    release_dir = tmp_path / "claw-trade-production-1.2.3-20260626T120000Z"
    (release_dir / "web" / "dist" / "assets").mkdir(parents=True)
    (release_dir / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    assert not module.wait_for_ui_health(version="1.2.3", release_dir=release_dir, timeout_seconds=0.01)


def _load_helper() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("claw_trade_apply_update_helper", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.HOST_OPERATION_LOCK_PATH = Path(os.environ[TEST_LOCK_PATH_ENV])
    module.REPORT_ACTIVE_LOCK_PATH = Path(os.environ[TEST_REPORT_LOCK_PATH_ENV])
    module.ROOT_UID = os.getuid()
    module.PRODUCTION_GROUP = grp.getgrgid(os.getgid()).gr_name
    return module


def _apply_request(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    install_root = tmp_path / "opt" / "claw-trade"
    old_release = install_root / "releases" / "claw-trade-production-1.2.2-20260601T000000Z"
    new_release = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    old_release.mkdir(parents=True)
    (install_root / "releases").mkdir(parents=True, exist_ok=True)
    request_path = install_root / "shared" / "updates" / "apply-request.json"
    request_path.parent.mkdir(parents=True)
    key = Ed25519PrivateKey.generate()
    public_key_path = _public_key_path(install_root)
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key_path.chmod(0o644)
    archive = _build_release_archive(new_release.name)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": f"{new_release.name}.tar.gz",
            "sha256": archive_sha256,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    downloads = install_root / "shared" / "updates" / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "manifest.json").write_bytes(manifest)
    (downloads / "manifest.json.sig").write_bytes(key.sign(manifest))
    (downloads / f"{new_release.name}.tar.gz").write_bytes(archive)
    (downloads / f"{new_release.name}.tar.gz.sig").write_bytes(key.sign(archive))
    request_path.write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "archive": f"{new_release.name}.tar.gz",
                "archiveSha256": archive_sha256,
                "target": str(new_release),
                "previous": str(old_release),
            }
        ),
        encoding="utf-8",
    )
    return install_root, request_path, old_release, new_release


def _public_key_path(install_root: Path) -> Path:
    return install_root / "root-owned" / "update-signing-public.pem"


def _build_release_archive(release_name: str) -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in (
            release_name,
            f"{release_name}/bin",
            f"{release_name}/root-helper",
            f"{release_name}/systemd",
            f"{release_name}/sudoers",
            f"{release_name}/web",
            f"{release_name}/web/dist",
            f"{release_name}/web/dist/assets",
        ):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        _add_file(archive, f"{release_name}/bin/claw-trade-preflight", b"#!/usr/bin/env bash\nexit 0\n", mode=0o755)
        _add_file(archive, f"{release_name}/root-helper/claw-trade-apply-update", b"#!/usr/bin/env python3\n", mode=0o755)
        _add_file(archive, f"{release_name}/systemd/claw-trade-control.service", b"[Service]\n")
        _add_file(archive, f"{release_name}/systemd/claw-trade-ui.service", b"[Service]\n")
        _add_file(archive, f"{release_name}/systemd/claw-trade-auto-update.timer", b"[Timer]\n")
        _add_file(archive, f"{release_name}/sudoers/claw-trade-update", b"clawtrade ALL=(root) NOPASSWD: /bin/systemctl start --no-block claw-trade-apply-update.service\n")
        _add_file(archive, f"{release_name}/web/dist/index.html", b'<script type="module" src="/assets/index-new.js"></script>\n')
        _add_file(archive, f"{release_name}/web/dist/assets/index-new.js", b"console.log('new');")
    return buffer.getvalue()


def _write_release_tree(release_dir: Path) -> Path:
    (release_dir / "web" / "dist" / "assets").mkdir(parents=True)
    (release_dir / "web" / "dist" / "index.html").write_text(
        '<script type="module" src="/assets/index-new.js"></script>\n',
        encoding="utf-8",
    )
    (release_dir / "web" / "dist" / "assets" / "index-new.js").write_text("console.log('new');", encoding="utf-8")
    return release_dir


def _add_file(archive: tarfile.TarFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.size = len(data)
    archive.addfile(info, BytesIO(data))


def _start_fake_ui(routes: dict[str, tuple[int, bytes]]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = routes.get(self.path, (404, b""))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
