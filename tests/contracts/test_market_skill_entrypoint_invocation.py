from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PYTHON_ENTRYPOINT_RUNNER = """
import importlib.util
import json
import pathlib
import re
import sys

def _load_runtime_package(package_name: str, scripts_dir: pathlib.Path):
    init_path = scripts_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[str(scripts_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"runtime package load failed: {scripts_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)

def _load_entrypoint_module(module_name: str, entrypoint_path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(module_name, entrypoint_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"entrypoint module load failed: {entrypoint_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

def main():
    if len(sys.argv) != 3:
        raise RuntimeError("usage: <entrypoint_path> <json_argv>")
    entrypoint_path = pathlib.Path(sys.argv[1]).resolve()
    scripts_dir = entrypoint_path.parent
    skill_name = scripts_dir.parent.name
    package_suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", skill_name)
    package_name = f"_openclaw_market_runtime_{package_suffix}"
    _load_runtime_package(package_name, scripts_dir)
    module_name = f"{package_name}.{entrypoint_path.stem}"
    module = _load_entrypoint_module(module_name, entrypoint_path)
    entrypoint_argv = json.loads(sys.argv[2])
    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        raise RuntimeError(f"entrypoint missing callable main: {entrypoint_path}")
    exit_code = main_fn(entrypoint_argv)
    raise SystemExit(0 if exit_code is None else int(exit_code))

if __name__ == "__main__":
    main()
""".strip()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _worker_root() -> Path:
    return _repo_root() / "agents" / "market_analyst"


def _run_entrypoint(entrypoint_path: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            PYTHON_ENTRYPOINT_RUNNER,
            str(entrypoint_path),
            json.dumps(argv, ensure_ascii=False),
        ],
        cwd=_worker_root(),
        text=True,
        capture_output=True,
        check=False,
    )


def _parse_last_json_line(stdout: str) -> dict[str, object]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    assert lines, "stdout 为空，缺少 JSON 结果"
    return json.loads(lines[-1])


def test_stock_entrypoint_runs_via_importlib_wrapper() -> None:
    entrypoint = _worker_root() / "skills" / "alphaear-stock" / "scripts" / "stock_entrypoint.py"
    completed = _run_entrypoint(
        entrypoint,
        [
            "--skip-auto-update",
            "price",
            "--ticker",
            "AAPL",
            "--start-date",
            "2026-05-03",
            "--end-date",
            "2026-05-03",
        ],
    )
    if completed.returncode == 0:
        payload = _parse_last_json_line(completed.stdout)
        assert payload.get("ok") is True
        rows = payload.get("rows")
        assert isinstance(rows, list) and rows
    else:
        payload = _parse_last_json_line(completed.stdout)
        assert payload.get("ok") is False
        error = payload.get("error")
        assert isinstance(error, dict)
        message = str(error.get("message") or "")
        assert "DATA_GATEWAY_MONGODB_URI" in message or "data_gateway" in message
    assert "attempted relative import with no known parent package" not in completed.stderr


def test_stock_entrypoint_has_no_legacy_database_argument() -> None:
    skill_root = _worker_root() / "skills" / "alphaear-stock"
    entrypoint = skill_root / "scripts" / "stock_entrypoint.py"
    stock_tools = skill_root / "scripts" / "stock_tools.py"

    assert not (skill_root / "scripts" / "database_manager.py").exists()
    for path in (entrypoint, stock_tools):
        text = path.read_text(encoding="utf-8")
        assert "--db-path" not in text
        assert "database_manager" not in text
        assert "DatabaseManager" not in text


def test_techlab_entrypoint_runs_via_importlib_wrapper_without_relative_import_error() -> None:
    entrypoint = _worker_root() / "skills" / "alphaear-techlab" / "scripts" / "techlab_entrypoint.py"
    completed = _run_entrypoint(
        entrypoint,
        [
            "analyze",
            "--ticker",
            "AAPL",
            "--start-date",
            "2026-05-03",
            "--end-date",
            "2026-05-03",
            "--output-dir",
            "/tmp/invalid-absolute-output-dir",
        ],
    )
    assert completed.returncode == 1
    if completed.stdout.strip():
        payload = _parse_last_json_line(completed.stdout)
        assert payload.get("ok") is False
        error = payload.get("error")
        assert isinstance(error, dict)
        assert error.get("code") == "invalid_output_dir"
    else:
        assert "No module named" in completed.stderr
    assert "attempted relative import with no known parent package" not in completed.stderr
