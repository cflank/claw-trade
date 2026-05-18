from __future__ import annotations

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
)

_CANONICAL_PACK_TOOLS = (
    "claw_get_market_pack",
    "claw_get_fundamental_pack",
    "claw_get_news_pack",
    "claw_get_social_pack",
)

_LEGACY_FRONTLINE_TOOLS = (
    "market_market_data_pack",
    "crypto_market_data_pack",
    "fundamental_fundamentals_data_pack",
    "crypto_fundamental_data_pack",
    "news_news_data_pack",
    "crypto_news_data_pack",
    "social_social_sentiment_pack",
    "crypto_social_sentiment_pack",
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
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
