from __future__ import annotations

import threading
from pathlib import Path

import pytest

from claw_trade.production.data_work_lock import hold_data_work_lock


def test_exclusive_data_work_lock_blocks_shared_consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "data-work-active.lock"
    monkeypatch.setenv("CLAW_TRADE_DATA_WORK_LOCK_PATH", str(lock_path))

    result: list[str] = []

    def _try_shared() -> None:
        try:
            with hold_data_work_lock(exclusive=False, blocking=False):
                result.append("acquired")
        except BlockingIOError:
            result.append("blocked")

    with hold_data_work_lock(exclusive=True, blocking=False):
        thread = threading.Thread(target=_try_shared)
        thread.start()
        thread.join(timeout=2)

    assert result == ["blocked"]


def test_exclusive_data_work_lock_is_reentrant_for_nested_same_thread_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "data-work-active.lock"
    monkeypatch.setenv("CLAW_TRADE_DATA_WORK_LOCK_PATH", str(lock_path))

    with hold_data_work_lock(exclusive=True, blocking=False):
        with hold_data_work_lock(exclusive=False, blocking=False):
            pass


def test_shared_data_work_lock_allows_shared_consumers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "data-work-active.lock"
    monkeypatch.setenv("CLAW_TRADE_DATA_WORK_LOCK_PATH", str(lock_path))

    with hold_data_work_lock(exclusive=False, blocking=False):
        with hold_data_work_lock(exclusive=False, blocking=False):
            pass
