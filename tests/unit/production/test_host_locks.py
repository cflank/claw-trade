from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from claw_trade.production import host_locks
from claw_trade.production.host_locks import hold_host_lock


@pytest.fixture(autouse=True)
def local_lock_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid(), os.getgid()))


def _lock_file(tmp_path: Path) -> Path:
    path = tmp_path / "host-operations.lock"
    path.write_bytes(b"")
    path.chmod(0o660)
    return path


def test_host_lock_requires_an_existing_safe_regular_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.lock"
    with pytest.raises(FileNotFoundError):
        with hold_host_lock(missing, exclusive=True, blocking=False):
            pass
    assert not missing.exists()

    directory = tmp_path / "directory.lock"
    directory.mkdir()
    directory.chmod(0o660)
    with pytest.raises(ValueError, match="普通文件"):
        with hold_host_lock(directory, exclusive=True, blocking=False):
            pass

    target = _lock_file(tmp_path)
    symlink = tmp_path / "symlink.lock"
    symlink.symlink_to(target)
    with pytest.raises(OSError):
        with hold_host_lock(symlink, exclusive=True, blocking=False):
            pass


def test_host_lock_validates_owner_group_and_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _lock_file(tmp_path)
    path.chmod(0o600)
    with pytest.raises(ValueError, match="0660"):
        with hold_host_lock(path, exclusive=True, blocking=False):
            pass

    path.chmod(0o660)
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid() + 1, os.getgid()))
    with pytest.raises(ValueError, match="root:clawtrade"):
        with hold_host_lock(path, exclusive=True, blocking=False):
            pass


def test_shared_locks_coexist_and_exclusive_nonblocking_fails(tmp_path: Path) -> None:
    path = _lock_file(tmp_path)
    with hold_host_lock(path, exclusive=False, blocking=False):
        with hold_host_lock(path, exclusive=False, blocking=False):
            with pytest.raises(BlockingIOError):
                with hold_host_lock(path, exclusive=True, blocking=False):
                    pass


def test_blocking_lock_waits_until_conflicting_lock_is_released(tmp_path: Path) -> None:
    path = _lock_file(tmp_path)
    started = threading.Event()
    acquired = threading.Event()

    def wait_for_lock() -> None:
        started.set()
        with hold_host_lock(path, exclusive=False, blocking=True):
            acquired.set()

    with hold_host_lock(path, exclusive=True, blocking=False):
        thread = threading.Thread(target=wait_for_lock)
        thread.start()
        assert started.wait(timeout=1)
        assert not acquired.wait(timeout=0.05)

    thread.join(timeout=1)
    assert acquired.is_set()
