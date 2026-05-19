from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

from claw_trade.config.tool_names import load_tool_registry


_OLD_PROVIDER_MODULES = (
    "frontline_data_pack",
    "frontline_data_pack.provider_executor",
    "crypto_market_data_pack",
    "claw_trade.providers",
)

_FORBIDDEN_SOURCE_TOKENS = (
    "frontline_data_pack",
    "provider_executor",
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_global_news",
    "bb_crypto_data",
)

_CANONICAL_PACK_TOOLS = (
    "claw_get_market_pack",
    "claw_get_fundamental_pack",
    "claw_get_news_pack",
    "claw_get_social_pack",
)

_LEGACY_FRONTLINE_TOOLS = (
    "market_market_data_pack",
    "us_market_data_pack",
    "cn_a_market_data_pack",
    "crypto_market_data_pack",
    "fundamental_fundamentals_data_pack",
    "us_fundamentals_data_pack",
    "cn_a_fundamentals_data_pack",
    "crypto_fundamental_data_pack",
    "news_news_data_pack",
    "us_news_data_pack",
    "cn_a_news_data_pack",
    "crypto_news_data_pack",
    "social_social_sentiment_pack",
    "us_social_sentiment_pack",
    "cn_a_social_sentiment_pack",
    "crypto_social_sentiment_pack",
    "bb_crypto_data",
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
)

_PROVIDER_IMPORT_BOUNDARY_ROOTS = (
    "src/claw_trade/workflow",
    "src/claw_trade/cli",
    "src/claw_trade/reports",
)

_ALLOWED_PROVIDER_IMPORT_MODULES = {
    "claw_trade.data_gateway.providers.defaults",
    "claw_trade.data_gateway.providers.registry",
    "claw_trade.data_gateway.providers.run_plan",
}

_FORBIDDEN_PROVIDER_IMPORT_MODULE_PREFIXES = (
    "claw_trade.data_gateway.providers.market_adapters",
    "claw_trade.data_gateway.providers.execution",
    "claw_trade.data_gateway.providers.market",
    "claw_trade.data_gateway.providers.fundamental",
    "claw_trade.data_gateway.providers.news",
    "claw_trade.data_gateway.providers.social",
    "claw_trade.data_gateway.providers.tushare_client",
    "claw_trade.providers",
    "frontline_data_pack",
    "provider_executor",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_openbb_pack_runtime_works_when_legacy_provider_modules_are_blocked() -> None:
    repo = _repo_root()
    script = textwrap.dedent(
        """
        import importlib.abc
        import json
        import os
        import sys

        os.environ["CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED"] = "true"

        BLOCKED = (
            "frontline_data_pack",
            "frontline_data_pack.provider_executor",
            "crypto_market_data_pack",
            "claw_trade.providers",
        )

        class BlockOldProviders(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                del path, target
                if any(fullname == name or fullname.startswith(name + ".") for name in BLOCKED):
                    raise ImportError(f"blocked old provider import: {fullname}")
                return None

        sys.meta_path.insert(0, BlockOldProviders())

        from fastapi.testclient import TestClient

        from claw_trade.config.tool_names import load_tool_registry
        from claw_trade.data_gateway.mcp.runtime_wrapper import OpenBBRuntimeWrapper
        from claw_trade.data_gateway.models import GatewaySettings, Market, PackDomain, RunProviderPlan
        from claw_trade.data_gateway.packs.service import DomainPackService

        settings = GatewaySettings(
            openbb_runtime_url="http://127.0.0.1:8001",
            openbb_home="/tmp/openbb-home",
            mongo_uri="mongodb://127.0.0.1:27017",
            provider_config_version="cfg-v1",
            provider_catalog_path="/tmp/provider-catalog.json",
            provider_catalog={},
            provider_settings={},
            secret_store_uri="env://",
            object_store_uri="file:///tmp/openbb-evidence",
            single_flight_lease_seconds=60,
            raw_payload_inline_max_bytes=4096,
            allowed_declarative_provider_domains=("example.com",),
        )

        class Store:
            def load(self, run_id):
                return RunProviderPlan(
                    run_id=run_id,
                    provider_config_version="cfg-v1",
                    market=Market.HK,
                    ticker="00700.HK",
                    domains=(PackDomain.MARKET,),
                    call_specs=(),
                    shared_call_keys=(),
                    cache_keys=(),
                    rate_limit_plan=(),
                    initial_gaps=(),
                    generated_at="2026-05-17T00:00:00+00:00",
                    remote_prefetch_allowed=False,
                )

        registry = load_tool_registry().registry
        tools = registry.resolve_intent("cn_a_market_data")
        assert tools == ("claw_get_market_pack",)

        wrapper = OpenBBRuntimeWrapper(
            settings=settings,
            adapters=(),
            pack_service=DomainPackService(settings=settings, adapters=()),
            run_provider_plan_store=Store(),
        )
        client = TestClient(wrapper.create_pack_fastapi_app())
        response = client.post(
            "/api/v1/claw/get_market_pack",
            json={
                "ticker": "00700.HK",
                "market": "HK",
                "profile": "HK",
                "company_name": "腾讯控股",
                "start_date": "2026-05-01",
                "end_date": "2026-05-17",
                "current_date": "2026-05-17",
                "currency": "HKD",
                "run_id": "run-import-block",
                "call_id": "call-market",
                "worker_id": "market_analyst",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"reader_brief_md", "status"}
        assert body["status"] in {"blocked", "insufficient"}
        assert "来源未配置" in body["reader_brief_md"]
        print(json.dumps({"status": body["status"], "tools": list(tools)}, ensure_ascii=False))
        """
    ).strip()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout.splitlines()[-1])
    assert payload["tools"] == ["claw_get_market_pack"]


def test_openbb_gateway_modules_do_not_import_legacy_provider_paths() -> None:
    from claw_trade.config import tool_names
    from claw_trade.data_gateway.mcp import runtime_wrapper
    from claw_trade.data_gateway.packs import service
    from claw_trade.data_gateway.providers import defaults

    openbb_only_sources = "\n".join(
        (
            inspect.getsource(runtime_wrapper),
            inspect.getsource(service),
            inspect.getsource(defaults),
        )
    )
    for token in _OLD_PROVIDER_MODULES:
        assert token not in openbb_only_sources

    os.environ["CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED"] = "true"
    registry = tool_names.load_tool_registry().registry
    visible = {
        *registry.resolve_intent("cn_a_market_data"),
        *registry.resolve_intent("us_market_data"),
        *registry.resolve_intent("crypto_market_data"),
        *registry.resolve_intent("cn_a_fundamentals_data"),
        *registry.resolve_intent("us_fundamentals_data"),
        *registry.resolve_intent("crypto_fundamentals_data"),
        *registry.resolve_intent("cn_a_news_data"),
        *registry.resolve_intent("us_news_data"),
        *registry.resolve_intent("crypto_news_data"),
        *registry.resolve_intent("cn_a_social_sentiment"),
        *registry.resolve_intent("us_social_sentiment"),
        *registry.resolve_intent("crypto_social_sentiment"),
    }
    for token in _FORBIDDEN_SOURCE_TOKENS:
        assert token not in visible


def test_frontline_plugin_canonical_openbb_bridge_does_not_call_legacy_executor() -> None:
    source = (_repo_root() / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js").read_text(encoding="utf-8")

    assert "openbbPackScriptConfig" in source
    assert "OpenBBRuntimeWrapper" in source
    assert "DomainPackService" in source
    assert "MongoRunProviderPlanStore" in source
    assert "Path(evidence_root) / 'techlab' / 'charts-local'" in source

    canonical_slice = source[source.index("function openbbPackScriptConfig"):]
    assert "frontline_data_pack.provider_executor" not in canonical_slice
    assert "provider_executor" not in canonical_slice
    assert "frontline_data_pack." not in canonical_slice
    assert "crypto_market_data_pack" not in canonical_slice
    assert "bb_crypto_data" not in canonical_slice


def test_start_control_runtime_config_does_not_require_legacy_bb_mcp_by_default() -> None:
    source = (_repo_root() / "scripts" / "start-control-runtime.sh").read_text(encoding="utf-8")
    assert 'process.env.BB_MCP_SERVER_PATH || "/mnt/d/src/BB/mcp/crypto-data-mcp/dist/server.js"' not in source
    assert 'process.env.BB_MCP_CWD || "/mnt/d/src/BB/mcp/crypto-data-mcp"' not in source
    assert "BB_MCP_SERVER_PATH" not in source
    assert "BB_MCP_CWD" not in source
    assert "delete mergedMcpServers.bb_crypto_data;" in source
    assert "if (enableBbMcp) {" not in source
    assert "mergedMcpServers.bb_crypto_data =" not in source


def test_workflow_controller_cli_exporter_do_not_import_or_call_provider_fetch_paths() -> None:
    repo = _repo_root()
    provider_prefix = "claw_trade.data_gateway.providers"
    python_files = _iter_provider_boundary_python_files(repo)
    for source_path in python_files:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        imported_modules = _provider_import_modules_from_ast(tree, provider_prefix)
        disallowed = sorted(
            module
            for module in imported_modules
            if module.startswith(provider_prefix) and module not in _ALLOWED_PROVIDER_IMPORT_MODULES
        )
        assert not disallowed, f"{source_path} imported disallowed provider modules: {disallowed}"
        for forbidden_prefix in _FORBIDDEN_PROVIDER_IMPORT_MODULE_PREFIXES:
            assert forbidden_prefix not in source, (
                f"{source_path} must not reference provider fetch path: {forbidden_prefix}"
            )


def _iter_provider_boundary_python_files(repo: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative_root in _PROVIDER_IMPORT_BOUNDARY_ROOTS:
        root = repo / relative_root
        files.extend(sorted(path for path in root.rglob("*.py") if path.is_file()))
    return tuple(files)


def _provider_import_modules_from_ast(tree: ast.AST, provider_prefix: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.strip()
                if name.startswith(provider_prefix):
                    modules.add(name)
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").strip()
            if not module:
                continue
            if module.startswith(provider_prefix + "."):
                modules.add(module)
                continue
            if module == provider_prefix:
                for alias in node.names:
                    child = alias.name.strip()
                    if child == "*":
                        modules.add(module + ".*")
                    elif child:
                        modules.add(f"{module}.{child}")
    return modules


def test_openbb_flag_uses_canonical_frontline_tools_without_legacy_fallback() -> None:
    original_openbb = os.environ.get("CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED")
    original_legacy = os.environ.get("CLAW_TRADE_LEGACY_ROLLBACK_ENABLED")
    try:
        os.environ["CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED"] = "true"
        os.environ["CLAW_TRADE_LEGACY_ROLLBACK_ENABLED"] = "true"
        registry = load_tool_registry().registry
        assert registry is not None
        visible = {
            *registry.resolve_intent("cn_a_market_data"),
            *registry.resolve_intent("us_market_data"),
            *registry.resolve_intent("crypto_market_data"),
            *registry.resolve_intent("cn_a_fundamentals_data"),
            *registry.resolve_intent("us_fundamentals_data"),
            *registry.resolve_intent("crypto_fundamentals_data"),
            *registry.resolve_intent("cn_a_news_data"),
            *registry.resolve_intent("us_news_data"),
            *registry.resolve_intent("crypto_news_data"),
            *registry.resolve_intent("cn_a_social_sentiment"),
            *registry.resolve_intent("us_social_sentiment"),
            *registry.resolve_intent("crypto_social_sentiment"),
        }
        assert visible == set(_CANONICAL_PACK_TOOLS)
        assert visible.isdisjoint(_LEGACY_FRONTLINE_TOOLS)
    finally:
        if original_openbb is None:
            os.environ.pop("CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED", None)
        else:
            os.environ["CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED"] = original_openbb
        if original_legacy is None:
            os.environ.pop("CLAW_TRADE_LEGACY_ROLLBACK_ENABLED", None)
        else:
            os.environ["CLAW_TRADE_LEGACY_ROLLBACK_ENABLED"] = original_legacy
