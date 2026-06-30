from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.production.paths import ProductionPaths


@dataclass(frozen=True)
class PreflightFailure:
    code: str
    message: str


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    failures: tuple[PreflightFailure, ...]


def run_preflight(paths: ProductionPaths) -> PreflightResult:
    failures: list[PreflightFailure] = []
    _require_dir(paths.current, "current_missing", failures)
    _require_dir(paths.current / "bin", "bin_missing", failures)
    _require_file(paths.current / "web" / "dist" / "index.html", "frontend_dist_missing", failures)
    _require_file(paths.current / "runtime" / "claw-trade-control-runtime", "control_runtime_missing", failures)
    _require_file(paths.current / "runtime" / "openclaw" / "openclaw.mjs", "openclaw_launcher_missing", failures)
    _require_dir(paths.current / "runtime" / "openclaw" / "dist", "openclaw_dist_missing", failures)
    _require_file(paths.current / "runtime" / "assets" / "agents.tar", "agents_asset_missing", failures)
    _require_file(
        paths.current / "runtime" / "assets" / "openclaw_plugins.tar",
        "openclaw_plugins_asset_missing",
        failures,
    )
    _require_dir(paths.config, "config_missing", failures)
    _require_dir(paths.data, "data_missing", failures)
    _require_dir(paths.logs, "logs_missing", failures)
    return PreflightResult(ok=not failures, failures=tuple(failures))


def _require_dir(path: Path, code: str, failures: list[PreflightFailure]) -> None:
    if not path.is_dir():
        failures.append(PreflightFailure(code, f"目录不存在：{path}"))


def _require_file(path: Path, code: str, failures: list[PreflightFailure]) -> None:
    if not path.is_file():
        failures.append(PreflightFailure(code, f"文件不存在：{path}"))
