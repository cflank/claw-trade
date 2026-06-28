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


def test_preflight_fails_when_runtime_assets_missing(tmp_path: Path) -> None:
    paths = ProductionPaths.from_root(tmp_path / "opt" / "claw-trade")
    _write_minimal_release(paths, include_assets=False)

    result = run_preflight(paths)

    assert not result.ok
    assert any(item.code == "agents_asset_missing" for item in result.failures)
    assert any(item.code == "openclaw_plugins_asset_missing" for item in result.failures)


def test_preflight_accepts_release_with_runtime_assets(tmp_path: Path) -> None:
    paths = ProductionPaths.from_root(tmp_path / "opt" / "claw-trade")
    _write_minimal_release(paths, include_assets=True)

    result = run_preflight(paths)

    assert result.ok


def test_production_entry_templates_exist_and_are_executable() -> None:
    for path in [
        Path("packaging/production/bin/claw-trade-control"),
        Path("packaging/production/bin/claw-trade-ui"),
        Path("packaging/production/bin/claw-trade-preflight"),
        Path("packaging/production/runtime/claw-trade-control-runtime"),
    ]:
        assert path.is_file()
        assert path.stat().st_mode & 0o111


def _write_minimal_release(paths: ProductionPaths, *, include_assets: bool) -> None:
    for directory in (
        paths.current / "bin",
        paths.current / "web" / "dist",
        paths.current / "runtime" / "bin",
        paths.current / "runtime" / "openclaw" / "dist",
        paths.config,
        paths.data,
        paths.logs,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    for file_path in (
        paths.current / "web" / "dist" / "index.html",
        paths.current / "runtime" / "bin" / "claw-trade-control-runtime",
        paths.current / "runtime" / "openclaw" / "openclaw.mjs",
    ):
        file_path.write_text("x\n", encoding="utf-8")
    if include_assets:
        asset_dir = paths.current / "runtime" / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "agents.tar").write_text("x\n", encoding="utf-8")
        (asset_dir / "openclaw_plugins.tar").write_text("x\n", encoding="utf-8")
