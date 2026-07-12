from __future__ import annotations

import json
import multiprocessing
import os
import signal
from pathlib import Path

import pytest

from claw_trade.production.factory_reset import FACTORY_RESET_CONFIRMATION, FactoryResetService
from claw_trade.production import host_locks
from claw_trade.production.host_locks import hold_host_lock
from claw_trade.production.maintenance_lock import ProductionMaintenanceLock


@pytest.fixture(autouse=True)
def local_lock_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid(), os.getgid()))


def _install_host_lock(install_root: Path) -> Path:
    install_root.mkdir(parents=True, exist_ok=True)
    path = install_root / "host-operations.lock"
    path.write_bytes(b"")
    path.chmod(0o660)
    return path


def _hold_host_lock_until_released(path: Path, ready, release) -> None:
    host_locks._expected_identity = lambda: (os.getuid(), os.getgid())
    with hold_host_lock(path, exclusive=True, blocking=False):
        ready.set()
        release.wait()


def test_factory_reset_clears_application_state_and_preserves_release_license_and_updates(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    shared = install_root / "shared"
    preserved = [
        install_root / "current",
        install_root / "releases" / "claw-trade-production-0.1.0-20260626T120000Z",
        shared / "license" / "virbox.dat",
        shared / "updates" / "update-signing-public.pem",
    ]
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep", encoding="utf-8")
    for relative in ("config/app.env", "data/db.json", "reports/r.md", "cache/item", "queues/q.json", "sessions/s.json"):
        path = shared / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("delete", encoding="utf-8")
    _install_host_lock(install_root)

    result = FactoryResetService(install_root=install_root).run(
        request_id="reset-1",
        confirmation=FACTORY_RESET_CONFIRMATION,
    )

    assert result.status == "completed"
    for path in preserved:
        assert path.read_text(encoding="utf-8") == "keep"
    for relative in ("config", "data", "reports", "cache", "queues", "sessions"):
        assert (shared / relative).is_dir()
        assert list((shared / relative).iterdir()) == []
    audit = json.loads(Path(result.audit_log).read_text(encoding="utf-8"))
    assert audit["requestId"] == "reset-1"


def test_factory_reset_requires_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="二次确认"):
        FactoryResetService(install_root=tmp_path / "opt" / "claw-trade").run(
            request_id="reset-1",
            confirmation="wrong",
        )


def test_factory_reset_uses_maintenance_lock(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    _install_host_lock(install_root)
    lock = ProductionMaintenanceLock(install_root=install_root)
    seen_locked: list[bool] = []

    class ObservedFactoryResetService(FactoryResetService):
        def _ensure_required_shared_dirs(self) -> None:
            seen_locked.append(lock.is_locked())
            super()._ensure_required_shared_dirs()

    result = ObservedFactoryResetService(install_root=install_root, maintenance_lock=lock).run(
        request_id="reset-1",
        confirmation=FACTORY_RESET_CONFIRMATION,
    )

    assert result.status == "completed"
    assert seen_locked == [True]
    assert lock.is_locked() is False


def test_factory_reset_rejects_existing_maintenance_lock(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    _install_host_lock(install_root)
    lock = ProductionMaintenanceLock(install_root=install_root)

    with lock.hold(reason="test", request_id="busy"):
        with pytest.raises(ValueError, match="维护中"):
            FactoryResetService(install_root=install_root, maintenance_lock=lock).run(
                request_id="reset-1",
                confirmation=FACTORY_RESET_CONFIRMATION,
            )


def test_factory_reset_does_not_mutate_while_host_lock_is_held_and_recovers_after_holder_termination(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    host_lock = _install_host_lock(install_root)
    entered: list[bool] = []

    class ObservedFactoryResetService(FactoryResetService):
        def _ensure_required_shared_dirs(self) -> None:
            entered.append(True)
            super()._ensure_required_shared_dirs()

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_host_lock_until_released, args=(host_lock, ready, release))
    process.start()
    try:
        assert ready.wait(timeout=15), f"lock holder did not start; exitcode={process.exitcode}"
        with pytest.raises(ValueError, match="维护中"):
            ObservedFactoryResetService(install_root=install_root).run(
                request_id="reset-1",
                confirmation=FACTORY_RESET_CONFIRMATION,
            )
    finally:
        process.terminate()
        process.join(timeout=2)

    assert entered == []
    assert process.exitcode == -signal.SIGTERM
    assert ObservedFactoryResetService(install_root=install_root).run(
        request_id="reset-2",
        confirmation=FACTORY_RESET_CONFIRMATION,
    ).status == "completed"
    assert entered == [True]


def test_factory_reset_rejects_install_root_escape(tmp_path: Path) -> None:
    escaped_root = tmp_path / "opt" / "claw-trade" / ".."

    with pytest.raises(ValueError, match="claw-trade 应用目录"):
        FactoryResetService(install_root=escaped_root).run(
            request_id="reset-1",
            confirmation=FACTORY_RESET_CONFIRMATION,
        )


def test_factory_reset_rejects_shared_symlink_escape(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    outside = tmp_path / "outside"
    outside_reports = outside / "reports"
    outside_reports.mkdir(parents=True)
    secret = outside_reports / "must-not-delete.md"
    secret.write_text("keep", encoding="utf-8")
    install_root.mkdir(parents=True)
    (install_root / "shared").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="符号链接"):
        FactoryResetService(install_root=install_root).run(
            request_id="reset-1",
            confirmation=FACTORY_RESET_CONFIRMATION,
        )

    assert secret.read_text(encoding="utf-8") == "keep"


def test_factory_reset_rejects_reset_target_symlink_escape(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    shared = install_root / "shared"
    outside_reports = tmp_path / "outside-reports"
    outside_reports.mkdir(parents=True)
    secret = outside_reports / "must-not-delete.md"
    secret.write_text("keep", encoding="utf-8")
    shared.mkdir(parents=True)
    (shared / "reports").symlink_to(outside_reports, target_is_directory=True)

    with pytest.raises(ValueError, match="符号链接"):
        FactoryResetService(install_root=install_root).run(
            request_id="reset-1",
            confirmation=FACTORY_RESET_CONFIRMATION,
        )

    assert secret.read_text(encoding="utf-8") == "keep"
