from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAINTENANCE_DIR = REPO_ROOT / "src" / "claw_trade" / "data_gateway" / "maintenance"


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_maintenance_modules_do_not_cross_report_material_boundaries() -> None:
    banned_tokens = (
        "openviking",
        "worker material",
        "worker_material",
        "report_polisher",
        "select decision",
        "write_openviking",
    )
    for path in MAINTENANCE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in banned_tokens:
            assert token not in text, f"{path.name} must not reference {token}"


def test_incremental_and_repair_are_data_api_batch_driven() -> None:
    for filename in ("incremental.py", "repair.py"):
        tree = _parse(MAINTENANCE_DIR / filename)

        has_get_data_batch_call = False
        has_file_read = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get_data_batch":
                    has_get_data_batch_call = True
                if node.func.attr in {"read_text", "read_bytes"}:
                    has_file_read = True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open":
                    has_file_read = True

        assert has_get_data_batch_call, f"{filename} must call DataAPI.get_data_batch"
        assert not has_file_read, f"{filename} must not read local files directly"


def test_incremental_module_does_not_depend_on_warehouse_gap_method() -> None:
    text = (MAINTENANCE_DIR / "incremental.py").read_text(encoding="utf-8")
    assert "warehouse.find_incremental_gaps" not in text


def test_maintenance_jobs_write_manifest_on_success_path() -> None:
    for filename in ("seed.py", "incremental.py", "repair.py"):
        tree = _parse(MAINTENANCE_DIR / filename)
        has_manifest_call = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "write_success_manifest":
                    has_manifest_call = True
        assert has_manifest_call, f"{filename} must write dataset manifest on success path"
