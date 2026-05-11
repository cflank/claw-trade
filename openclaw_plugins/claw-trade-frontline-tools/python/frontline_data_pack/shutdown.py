from __future__ import annotations

import signal
import threading
from types import FrameType


PACK_TERMINATED = "PACK_TERMINATED"

_termination_requested = threading.Event()
_handler_lock = threading.Lock()
_handler_installed = False


def install_sigterm_handler() -> None:
    global _handler_installed
    with _handler_lock:
        if _handler_installed:
            return
        signal.signal(signal.SIGTERM, _sigterm_handler)
        _handler_installed = True


def _sigterm_handler(signum: int, frame: FrameType | None) -> None:
    _ = signum, frame
    _termination_requested.set()


def request_termination() -> None:
    _termination_requested.set()


def is_termination_requested() -> bool:
    return _termination_requested.is_set()


def reset_termination_flag() -> None:
    _termination_requested.clear()
