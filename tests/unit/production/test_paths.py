from __future__ import annotations

from pathlib import Path

from claw_trade.production.paths import ProductionPaths


def test_production_paths_resolve_current_and_shared(tmp_path: Path) -> None:
    root = tmp_path / "opt" / "claw-trade"
    paths = ProductionPaths.from_root(root)

    assert paths.current == root / "current"
    assert paths.shared == root / "shared"
    assert paths.config == root / "shared" / "config"
    assert paths.logs == root / "shared" / "logs"
