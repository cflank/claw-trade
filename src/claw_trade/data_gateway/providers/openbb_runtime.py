from __future__ import annotations

import os
from typing import Any, Sequence


def import_openbb_obb(*, context: str, required_attrs: Sequence[str] = ()) -> Any:
    os.environ.setdefault("OPENBB_AUTO_BUILD", "0")
    try:
        from openbb import obb  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"openbb import failed for {context}: {exc}") from exc
    missing = tuple(attr for attr in required_attrs if not hasattr(obb, attr))
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"openbb generated package missing required module(s) for {context}: {joined}; "
            "run scripts/setup-openbb-dev.sh --install"
        )
    return obb
