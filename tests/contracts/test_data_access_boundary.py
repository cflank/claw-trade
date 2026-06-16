from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests/contracts/fixtures/data_access_boundary_known_violations.json"

GUARDED_ROOTS = (
    REPO_ROOT / "src/claw_trade/selection",
    REPO_ROOT / "src/claw_trade/reports",
    REPO_ROOT / "src/claw_trade/ui_backend",
    REPO_ROOT / "src/claw_trade/web",
    REPO_ROOT / "src/claw_trade/workflow",
)
AGENT_TOOL_SCRIPTS_ROOT = REPO_ROOT / "agents"
OPENCLAW_PLUGIN_ROOT = REPO_ROOT / "openclaw_plugins"
DATA_LAYER_BUSINESS_REF_RATCHET_FILES = (
    REPO_ROOT / "src/claw_trade/data_gateway/_selection_batch.py",
)

ALLOWED_DATA_LAYER_IMPORTS = {
    "claw_trade.data_gateway.api",
    "claw_trade.data_gateway.models",
    "claw_trade.data_gateway.refs",
    "claw_trade.data_gateway.report_evidence",
    "claw_trade.data_gateway.runtime",
    "claw_trade.data_gateway.selection_api",
    "claw_trade.data_gateway.selection_integrity",
    "claw_trade.data_gateway.settings_store",
    "claw_trade.data_gateway.source_probe",
}
ALLOWED_STORAGE_FILES = {
    "src/claw_trade/ui_backend/mongo_settings_store.py",
}
ALLOWED_EXTERNAL_IMPORTS_BY_FILE = {
    "src/claw_trade/ui_backend/llm_settings_bridge.py": {"urllib.request"},
}
FORBIDDEN_EXTERNAL_IMPORTS = {
    "akshare",
    "aiohttp",
    "baostock",
    "duckdb",
    "http.client",
    "httpx",
    "polars",
    "pymongo",
    "pyarrow",
    "requests",
    "sqlite3",
    "tushare",
    "urllib.request",
    "urllib3",
    "yfinance",
}
ALLOWED_TEXT_PATTERN_COUNTS = {
    ("src/claw_trade/ui_backend/mongo_settings_store.py", "storage_object_construction", "MongoClient("): 1,
    ("src/claw_trade/ui_backend/llm_settings_bridge.py", "runtime_health_probe", "urlopen("): 1,
    ("openclaw_plugins/claw-trade-frontline-tools/index.js", "js_direct_http_call", "fetch("): 1,
}
VALID_PHASES = {"phase_1", "phase_2", "phase_3", "phase_4", "phase_5", "phase_6"}

TEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("storage_object_construction", "DatasetRepository.from_database("),
    ("storage_object_construction", "ProviderRegistry("),
    ("storage_object_construction", "MongoDataSourceStore("),
    ("storage_object_construction", "MongoSecretStore("),
    ("storage_object_construction", "DataService("),
    ("storage_object_construction", "Warehouse("),
    ("storage_object_construction", "MongoClient("),
    ("selection_columnar_storage_call", "SelectionColumnarWarehouse.default("),
    ("columnar_full_materialization", "duckdb.connect("),
    ("columnar_full_materialization", "read_parquet("),
    ("columnar_full_materialization", ".fetch_df("),
    ("storage_specific_ref", "mongo://normalized_datasets/"),
    ("storage_specific_ref", "normalized://mongo/normalized_datasets/"),
    ("storage_specific_ref", "attempt://mongo/provider_attempts/"),
    ("storage_collection_name", "normalized_datasets"),
    ("storage_collection_name", "provider_attempts"),
    ("storage_collection_name", "raw_payloads"),
    ("storage_collection_name", "dataset_manifests"),
    ("js_direct_http_call", "fetch("),
    ("js_direct_http_call", "axios."),
    ("js_direct_http_call", "node:http"),
    ("js_direct_http_call", "node:https"),
    ("js_direct_http_call", "http.request("),
    ("js_direct_http_call", "https.request("),
    ("js_storage_access", "mongodb"),
    ("js_storage_access", "MongoClient"),
    ("js_storage_access", "duckdb"),
    ("js_storage_access", "parquet"),
)


@dataclass(frozen=True, order=True)
class BoundaryHit:
    path: str
    category: str
    pattern: str
    count: int


def test_data_access_boundary_known_violations_manifest_is_current() -> None:
    known = _load_known_violations()
    current = _scan_current_violations()

    unexpected = sorted(set(current) - set(known))
    stale = sorted(set(known) - set(current))
    count_mismatches = sorted(
        (key, known[key], current[key])
        for key in sorted(set(current) & set(known))
        if known[key] != current[key]
    )

    assert not unexpected, "Unexpected data-access boundary violations:\n" + _format_hits(
        [current[key] for key in unexpected]
    )
    assert not stale, "Stale known violations must be removed from fixture:\n" + _format_hits(
        [known[key] for key in stale]
    )
    assert not count_mismatches, "Known violation counts changed:\n" + "\n".join(
        f"- {key}: expected {old.count}, current {new.count}" for key, old, new in count_mismatches
    )


def test_selection_batch_fetcher_is_private_implementation() -> None:
    old_public_module = REPO_ROOT / "src/claw_trade/data_gateway/selection_batch.py"
    assert not old_public_module.exists()

    checked_paths = (
        REPO_ROOT / "src/claw_trade/web/state.py",
        REPO_ROOT / "src/claw_trade/selection/data_need_refresh.py",
        REPO_ROOT / "src/claw_trade/data_gateway/selection_api.py",
    )
    for path in checked_paths:
        text = path.read_text(encoding="utf-8")
        assert "claw_trade.data_gateway.selection_batch" not in text

    api_text = (REPO_ROOT / "src/claw_trade/data_gateway/selection_api.py").read_text(encoding="utf-8")
    assert "claw_trade.data_gateway._selection_batch" in api_text

    web_state_tree = ast.parse((REPO_ROOT / "src/claw_trade/web/state.py").read_text(encoding="utf-8"))
    selection_api_imports: set[str] = set()
    provider_batch_imports: set[str] = set()
    for node in ast.walk(web_state_tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_names = {alias.name for alias in node.names}
        if node.module == "claw_trade.data_gateway.selection_api":
            selection_api_imports.update(imported_names)
        if node.module == "claw_trade.selection.data_need_refresh":
            provider_batch_imports.update(imported_names)

    data_need_fetch_names = {
        "build_selection_data_need_audit",
        "fetch_selection_batch_from_data_gateway",
    }
    assert data_need_fetch_names <= selection_api_imports
    assert not provider_batch_imports


def test_normalized_mongo_row_access_is_maintenance_only() -> None:
    repository_text = (REPO_ROOT / "src/claw_trade/data_gateway/warehouse/repository.py").read_text(encoding="utf-8")
    assert "def get_normalized_document(" not in repository_text
    assert "def count_normalized_documents(" not in repository_text
    assert "def delete_normalized_documents(" not in repository_text
    assert "maintenance_mode" not in repository_text

    allowed_files = {
        "src/claw_trade/data_gateway/warehouse/repository.py",
        "src/claw_trade/data_gateway/maintenance/normalized_rows.py",
    }
    forbidden_tokens = {
        "get_normalized_document_for_maintenance(",
        "count_normalized_documents_for_maintenance(",
        "delete_normalized_documents_for_maintenance(",
    }
    hits: list[str] = []
    for root in (REPO_ROOT / "src/claw_trade", REPO_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            rel = _rel(path)
            if rel in allowed_files:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                if token in text:
                    hits.append(f"{rel}: {token}")

    assert not hits, "Normalized Mongo row access must stay inside maintenance modules:\n" + "\n".join(hits)


def test_market_probe_validation_script_uses_public_request_not_legacy_data_request_remote() -> None:
    script_text = (REPO_ROOT / "scripts/validation/data_layer_full_chain_market_probe.py").read_text(encoding="utf-8")

    assert "api.request_data(" in script_text
    assert "api.get_data_needs(" not in script_text
    assert "api.get_data_batch(" not in script_text
    assert "DataAPI -> DataService.plan_batch" not in script_text


def test_data_access_boundary_fixture_has_removal_plan() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "data_access_boundary_known_violations.v1"
    violations = payload.get("violations")
    assert isinstance(violations, list)
    seen: set[tuple[str, str, str]] = set()
    for item in violations:
        assert isinstance(item.get("path"), str) and item["path"]
        assert isinstance(item.get("category"), str) and item["category"]
        assert isinstance(item.get("pattern"), str) and item["pattern"]
        assert isinstance(item.get("count"), int) and item["count"] > 0
        assert isinstance(item.get("reason"), str) and item["reason"]
        assert isinstance(item.get("planned_removal_phase"), str) and item["planned_removal_phase"]
        assert item["planned_removal_phase"] in VALID_PHASES
        key = (item["path"], item["category"], item["pattern"])
        assert key not in seen, f"duplicate known violation: {key}"
        seen.add(key)


def _load_known_violations() -> dict[tuple[str, str, str], BoundaryHit]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    known: dict[tuple[str, str, str], BoundaryHit] = {}
    for item in payload.get("violations", ()):
        hit = BoundaryHit(
            path=item["path"],
            category=item["category"],
            pattern=item["pattern"],
            count=int(item["count"]),
        )
        known[(hit.path, hit.category, hit.pattern)] = hit
    return known


def _scan_current_violations() -> dict[tuple[str, str, str], BoundaryHit]:
    counts: dict[tuple[str, str, str], int] = {}
    for path in _guarded_python_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        _scan_imports(path=path, rel=rel, counts=counts)
        _scan_text_patterns(rel=rel, text=text, counts=counts)
    for path in DATA_LAYER_BUSINESS_REF_RATCHET_FILES:
        rel = _rel(path)
        _scan_data_layer_business_ref_patterns(rel=rel, text=path.read_text(encoding="utf-8"), counts=counts)
    _apply_allowed_counts(counts)
    return {
        key: BoundaryHit(path=key[0], category=key[1], pattern=key[2], count=count)
        for key, count in counts.items()
        if count > 0
    }


def _guarded_python_files() -> tuple[Path, ...]:
    paths: list[Path] = []
    for root in GUARDED_ROOTS:
        paths.extend(path for path in root.rglob("*.py") if path.is_file())
    if AGENT_TOOL_SCRIPTS_ROOT.exists():
        paths.extend(
            path
            for path in AGENT_TOOL_SCRIPTS_ROOT.glob("*/skills/*/scripts/**/*.py")
            if path.is_file()
        )
    if OPENCLAW_PLUGIN_ROOT.exists():
        paths.extend(path for path in OPENCLAW_PLUGIN_ROOT.rglob("*.js") if path.is_file())
    return tuple(sorted(paths))


def _scan_imports(*, path: Path, rel: str, counts: dict[tuple[str, str, str], int]) -> None:
    try:
        if path.suffix != ".py":
            return
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise AssertionError(f"Cannot parse {rel}: {exc}") from exc

    for module in _imported_modules(tree):
        if _is_forbidden_external_import(module) and not _is_allowed_external_import(rel=rel, module=module):
            _add(counts, rel, "forbidden_external_import", _external_import_pattern(module))
        if _is_forbidden_data_gateway_import(module):
            _add(counts, rel, "data_gateway_internal_import", module)
        if module == "claw_trade.data_gateway.warehouse.selection_columnar":
            _add(counts, rel, "selection_columnar_import", module)
        if module == "claw_trade.ui_backend.mongo_settings_store" and rel != "src/claw_trade/ui_backend/mongo_settings_store.py":
            _add(counts, rel, "mongo_settings_store_import", module)
    _scan_calls(tree=tree, rel=rel, counts=counts)


def _imported_modules(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
            if node.module == "urllib":
                modules.extend("urllib.request" for alias in node.names if alias.name == "request")
    return tuple(modules)


def _scan_calls(*, tree: ast.AST, rel: str, counts: dict[tuple[str, str, str], int]) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name == "importlib.import_module" and node.args:
            provider = _string_arg(node.args[0])
            if provider in {"akshare", "baostock", "tushare", "yfinance"}:
                _add(counts, rel, "direct_provider_probe", f'importlib.import_module("{provider}")')
            elif provider is None and rel.startswith("agents/"):
                _add(counts, rel, "indirect_worker_data_tool_load", "importlib.import_module(<dynamic>)")
        elif call_name in {
            "requests.get",
            "requests.post",
            "requests.request",
            "requests.Session",
            "_requests.get",
            "_requests.post",
            "_requests.request",
            "_requests.Session",
        }:
            _add(counts, rel, "direct_provider_probe", "requests.get(")
        elif call_name in {"urllib.request.Request", "request.Request"}:
            _add(counts, rel, "direct_provider_probe", "urllib.request.Request(")
        elif call_name in {"urllib.request.urlopen", "request.urlopen"}:
            _add(counts, rel, "direct_provider_probe", "urllib.request.urlopen(")
        elif call_name in {"urllib3.PoolManager", "aiohttp.ClientSession"}:
            _add(counts, rel, "direct_provider_probe", f"{call_name}(")
        elif call_name in {"http.client.HTTPConnection", "http.client.HTTPSConnection"}:
            _add(counts, rel, "direct_provider_probe", f"{call_name}(")
        elif call_name == "urlopen":
            _add(counts, rel, "runtime_health_probe", "urlopen(")
        elif call_name == "sqlite3.connect":
            _add(counts, rel, "local_data_file_reader", "sqlite3.connect(")
        elif call_name in {
            "read_csv",
            "read_excel",
            "read_parquet",
            "pd.read_csv",
            "pd.read_excel",
            "pd.read_parquet",
            "pandas.read_csv",
            "pandas.read_excel",
            "pandas.read_parquet",
        }:
            _add(counts, rel, "local_data_file_reader", f"{call_name}(")
        elif call_name == "importlib.util.spec_from_file_location" and rel.startswith("agents/"):
            _add(counts, rel, "indirect_worker_data_tool_load", "spec_from_file_location(")
        elif call_name == "load_price_frame" and rel.startswith("agents/"):
            _add(counts, rel, "indirect_worker_data_tool_load", "load_price_frame(")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _string_arg(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_allowed_external_import(*, rel: str, module: str) -> bool:
    if rel in ALLOWED_STORAGE_FILES and module.split(".", 1)[0] == "pymongo":
        return True
    return module in ALLOWED_EXTERNAL_IMPORTS_BY_FILE.get(rel, set())


def _is_forbidden_external_import(module: str) -> bool:
    root = module.split(".", 1)[0]
    return module in FORBIDDEN_EXTERNAL_IMPORTS or root in FORBIDDEN_EXTERNAL_IMPORTS


def _external_import_pattern(module: str) -> str:
    root = module.split(".", 1)[0]
    return module if module in FORBIDDEN_EXTERNAL_IMPORTS else root


def _is_forbidden_data_gateway_import(module: str) -> bool:
    if module == "claw_trade.data_gateway":
        return True
    if not module.startswith("claw_trade.data_gateway."):
        return False
    return module not in ALLOWED_DATA_LAYER_IMPORTS


def _scan_text_patterns(*, rel: str, text: str, counts: dict[tuple[str, str, str], int]) -> None:
    for category, pattern in TEXT_PATTERNS:
        if category.startswith("js_") and not rel.endswith(".js"):
            continue
        count = text.count(pattern)
        if count:
            _add(counts, rel, category, pattern, count=count)


def _scan_data_layer_business_ref_patterns(*, rel: str, text: str, counts: dict[tuple[str, str, str], int]) -> None:
    for category, pattern in TEXT_PATTERNS:
        if category not in {"storage_specific_ref", "storage_collection_name"}:
            continue
        count = text.count(pattern)
        if count:
            _add(counts, rel, category, pattern, count=count)


def _apply_allowed_counts(counts: dict[tuple[str, str, str], int]) -> None:
    for key, allowed_count in ALLOWED_TEXT_PATTERN_COUNTS.items():
        if key not in counts:
            continue
        remaining = counts[key] - allowed_count
        if remaining > 0:
            counts[key] = remaining
        else:
            del counts[key]


def _add(
    counts: dict[tuple[str, str, str], int],
    path: str,
    category: str,
    pattern: str,
    *,
    count: int = 1,
) -> None:
    key = (path, category, pattern)
    counts[key] = counts.get(key, 0) + count


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _format_hits(hits: list[BoundaryHit]) -> str:
    payload: list[dict[str, Any]] = [
        {
            "path": hit.path,
            "category": hit.category,
            "pattern": hit.pattern,
            "count": hit.count,
            "reason": "Current known violation; remove or reduce during the planned data-layer migration.",
            "planned_removal_phase": _default_phase(hit),
        }
        for hit in hits
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _default_phase(hit: BoundaryHit) -> str:
    if hit.path.startswith("src/claw_trade/selection/"):
        if hit.category in {"selection_columnar_import", "selection_columnar_storage_call", "columnar_full_materialization"}:
            return "phase_2"
        return "phase_3"
    if hit.path == "src/claw_trade/reports/data_need_bridge.py":
        return "phase_1"
    if hit.path == "src/claw_trade/data_gateway/_selection_batch.py":
        return "phase_3"
    if hit.path == "src/claw_trade/ui_backend/report_context.py":
        return "phase_1"
    if hit.path == "src/claw_trade/ui_backend/data_source_runtime_checks.py":
        return "phase_1"
    if hit.path.startswith("src/claw_trade/web/"):
        return "phase_1"
    if hit.path.startswith("agents/"):
        return "phase_1"
    return "phase_6"
