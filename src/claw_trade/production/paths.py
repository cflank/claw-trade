from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProductionPaths:
    root: Path
    current: Path
    shared: Path
    config: Path
    data: Path
    logs: Path
    license: Path
    reports: Path
    updates: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProductionPaths":
        shared = root / "shared"
        return cls(
            root=root,
            current=root / "current",
            shared=shared,
            config=shared / "config",
            data=shared / "data",
            logs=shared / "logs",
            license=shared / "license",
            reports=shared / "reports",
            updates=shared / "updates",
        )
