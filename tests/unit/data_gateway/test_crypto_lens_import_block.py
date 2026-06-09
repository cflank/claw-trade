from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "aiohttp",
    "pymongo",
    "motor",
    "open" + "bb",
    "socket",
)

_FORBIDDEN_SOURCE_TOKENS = (
    "/mnt/d/src/BB",
    "D:\\src\\BB",
    "BB_MCP_",
    "os.environ",
    "getenv(",
    "mongodb://",
    "MongoClient",
    "http://",
    "https://",
    "open" + "bb",
    "Open" + "BB",
)


def _crypto_lens_root() -> Path:
    return Path(__file__).resolve().parents[3] / "src" / "claw_trade" / "data_gateway" / "analysis" / "crypto_lens"


def _iter_python_files() -> list[Path]:
    return sorted(path for path in _crypto_lens_root().rglob("*.py"))


def test_crypto_lens_source_tree_exists() -> None:
    files = _iter_python_files()
    assert _crypto_lens_root().is_dir()
    assert files


def test_crypto_lens_has_no_forbidden_network_or_db_imports() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert not module.startswith(_FORBIDDEN_IMPORT_PREFIXES), f"forbidden import {module} in {path}"


def test_crypto_lens_source_does_not_include_runtime_forbidden_tokens() -> None:
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_SOURCE_TOKENS:
            assert token not in text, f"forbidden token {token} in {path}"
