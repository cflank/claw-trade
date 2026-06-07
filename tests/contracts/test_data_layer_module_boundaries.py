from __future__ import annotations

from pathlib import Path

from claw_trade.data_gateway import execution, models

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_LAYER_ROOT = REPO_ROOT / "src/claw_trade/data_gateway"


def test_six_layer_packages_exist() -> None:
    expected = [
        DATA_LAYER_ROOT,
        DATA_LAYER_ROOT / "coordination",
        DATA_LAYER_ROOT / "warehouse",
        DATA_LAYER_ROOT / "execution",
        DATA_LAYER_ROOT / "providers",
        DATA_LAYER_ROOT / "ingest",
        DATA_LAYER_ROOT / "maintenance",
    ]
    for package in expected:
        assert package.exists()
        assert (package / "__init__.py").exists()


def test_new_data_layer_does_not_import_legacy_backup_path() -> None:
    legacy_backup_token = "data_" + "gateway_" + "bak"
    for path in DATA_LAYER_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert legacy_backup_token not in content


def test_coordination_layer_keeps_boundary_no_http_mongo_openviking() -> None:
    guarded_modules = [
        DATA_LAYER_ROOT / "api.py",
        DATA_LAYER_ROOT / "coordination/service.py",
        DATA_LAYER_ROOT / "coordination/query_planner.py",
    ]
    forbidden_tokens = (
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import pymongo",
        "from pymongo",
        "import openviking",
        "from openviking",
        "write_openviking_material",
    )
    for module in guarded_modules:
        content = module.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in content


def test_core_execution_models_are_single_source_from_models_module() -> None:
    assert execution.FetchResult is models.FetchResult
    assert execution.GateDecision is models.GateDecision
    assert execution.ResultRefs is models.ResultRefs
