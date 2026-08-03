from __future__ import annotations

import errno
import fcntl
import os
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from claw_trade.production import paths
from claw_trade.production.host_locks import hold_host_lock


_EXCLUSIVE_LOCK_HELD: ContextVar[bool] = ContextVar("claw_trade_data_work_lock_exclusive", default=False)
# ponytail: one host-wide lock keeps report data isolated; split by market only if measured throughput needs it.


def data_work_lock_path() -> Path:
    configured = os.environ.get("CLAW_TRADE_DATA_WORK_LOCK_PATH", "").strip()
    return Path(configured).expanduser() if configured else paths.DATA_WORK_ACTIVE_LOCK_PATH


def data_work_lock_enabled() -> bool:
    path = data_work_lock_path()
    if path != paths.DATA_WORK_ACTIVE_LOCK_PATH and paths.INSTALL_ROOT.exists():
        if os.environ.get("CLAW_TRADE_ALLOW_DATA_WORK_LOCK_OVERRIDE") != "1":
            raise RuntimeError("生产环境禁止覆盖数据工作锁路径")
    if path != paths.DATA_WORK_ACTIVE_LOCK_PATH:
        return True
    return paths.INSTALL_ROOT.exists()


def data_work_lock_exclusive_held() -> bool:
    return _EXCLUSIVE_LOCK_HELD.get()


@contextmanager
def hold_data_work_lock(*, exclusive: bool, blocking: bool) -> Iterator[int]:
    if not exclusive and data_work_lock_exclusive_held():
        yield -1
        return
    path = data_work_lock_path()
    token = _EXCLUSIVE_LOCK_HELD.set(True) if exclusive else None
    try:
        if path == paths.DATA_WORK_ACTIVE_LOCK_PATH:
            with hold_host_lock(path, exclusive=exclusive, blocking=blocking) as fd:
                yield fd
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o660)
        try:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, operation)
            except OSError as exc:
                if not blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise BlockingIOError(exc.errno, "数据工作锁正在使用", path) from exc
                raise
            yield fd
        finally:
            os.close(fd)
    finally:
        if token is not None:
            _EXCLUSIVE_LOCK_HELD.reset(token)
