from __future__ import annotations

import errno
import fcntl
import grp
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from claw_trade.production import paths


def _expected_identity() -> tuple[int, int]:
    return 0, grp.getgrnam(paths.PRODUCTION_GROUP).gr_gid


@contextmanager
def hold_host_lock(
    path: Path,
    *,
    exclusive: bool,
    blocking: bool,
) -> Iterator[int]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        owner_uid, group_gid = _expected_identity()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"宿主锁不是普通文件：{path}")
        if metadata.st_uid != owner_uid or metadata.st_gid != group_gid:
            raise ValueError(f"宿主锁所有者必须是 root:{paths.PRODUCTION_GROUP}：{path}")
        if stat.S_IMODE(metadata.st_mode) != 0o660:
            raise ValueError(f"宿主锁权限必须是 0660：{path}")

        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, operation)
        except OSError as exc:
            if not blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BlockingIOError(exc.errno, "宿主锁正在使用", path) from exc
            raise
        yield fd
    finally:
        os.close(fd)
