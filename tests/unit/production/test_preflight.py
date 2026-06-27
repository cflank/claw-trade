from __future__ import annotations

from pathlib import Path

from claw_trade.production.paths import ProductionPaths
from claw_trade.production.preflight import run_preflight


def test_preflight_fails_when_frontend_dist_missing(tmp_path: Path) -> None:
    paths = ProductionPaths.from_root(tmp_path / "opt" / "claw-trade")
    (paths.current / "bin").mkdir(parents=True)
    (paths.current / "runtime" / "bin").mkdir(parents=True)
    (paths.shared / "config").mkdir(parents=True)
    result = run_preflight(paths)

    assert not result.ok
    assert any(item.code == "frontend_dist_missing" for item in result.failures)
