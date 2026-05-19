from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest


_FORBIDDEN_OPENBB_PATTERNS = (
    "provider.",
    "admin.",
    "discovery.",
    "activate_tools",
    "execute_prompt",
    "list_providers",
    "cache",
    "raw",
    "debug",
)

_FORBIDDEN_US_ATOMICS = {
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
}

_FORBIDDEN_LEGACY_ALIASES = {
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
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_openbb_mcp_visible_tools_are_pack_only() -> None:
    repo = _repo_root()
    script = textwrap.dedent(
        """
        import json

        from claw_trade.data_gateway.mcp.runtime_wrapper import (
            OpenBBRuntimeWrapper,
            list_mcp_tool_names,
        )
        from claw_trade.data_gateway.models import GatewaySettings
        from openbb_mcp_server.models.settings import MCPSettings

        wrapper = OpenBBRuntimeWrapper(
            settings=GatewaySettings(
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
            ),
            adapters=(),
            pack_service=None,
        )

        mcp = wrapper.create_pack_mcp_server(
            MCPSettings(
                api_prefix="/api/v1",
                default_tool_categories=["claw"],
                allowed_tool_categories=["claw"],
                enable_tool_discovery=False,
            )
        )
        print(json.dumps({"tools": list_mcp_tool_names(mcp)}))
        """
    ).strip()

    completed = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "./third_party/openbb/openbb_platform/core",
            "--with",
            "./third_party/openbb/openbb_platform/extensions/mcp_server",
            "--with",
            "fastapi",
            "python",
            "-c",
            script,
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    result = json.loads(lines[-1])
    tools = tuple(result["tools"])
    assert tools == (
        "claw_get_fundamental_pack",
        "claw_get_market_pack",
        "claw_get_news_pack",
        "claw_get_social_pack",
    )
    assert set(tools).isdisjoint(_FORBIDDEN_US_ATOMICS)
    assert set(tools).isdisjoint(_FORBIDDEN_LEGACY_ALIASES)
    for name in tools:
        assert name.startswith("claw_get_")
        assert name.endswith("_pack")
        assert not any(pattern in name for pattern in _FORBIDDEN_OPENBB_PATTERNS)


def test_frontline_plugin_contract_includes_canonical_openbb_pack_tools() -> None:
    manifest_path = _repo_root() / "openclaw_plugins" / "claw-trade-frontline-tools" / "openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tools = set(manifest["contracts"]["tools"])
    assert {
        "claw_get_market_pack",
        "claw_get_fundamental_pack",
        "claw_get_news_pack",
        "claw_get_social_pack",
    }.issubset(tools)


def test_openclaw_llm_provider_payload_scan_accepts_messages_and_tool_schema_only() -> None:
    payload = {
        "source": "provider_request_capture",
        "provider": "openai",
        "request_id": "req-1",
        "runtime_markers": {
            "run_id": "run-1",
            "call_id": "call-1",
            "worker_id": "market_analyst",
            "stage": "frontline",
            "profile": "US",
            "openclaw_run_id": "oc-1",
        },
        "payload": {
            "messages": [
                {"role": "system", "content": "你是 market_analyst"},
                {"role": "user", "content": "分析 AAPL"},
            ],
            "tools": [
                {"type": "function", "function": {"name": "claw_get_market_pack"}},
            ],
        },
    }

    visible_tools = _scan_openclaw_llm_provider_payload(payload)
    assert visible_tools == ("claw_get_market_pack",)


@pytest.mark.parametrize("source", ("openbb_provider_http_evidence", "openbb_provider_raw_evidence"))
def test_openclaw_llm_provider_payload_scan_rejects_openbb_http_raw_evidence(source: str) -> None:
    payload = {
        "source": source,
        "provider": "openbb",
        "payload": {"status": 200},
    }
    with pytest.raises(ValueError, match="provider_request_capture"):
        _scan_openclaw_llm_provider_payload(payload)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_US_ATOMICS))
def test_openclaw_llm_provider_payload_scan_rejects_us_atomic_tools(forbidden: str) -> None:
    payload = {
        "source": "provider_request_capture",
        "runtime_markers": {
            "run_id": "run-1",
            "call_id": "call-1",
            "worker_id": "market_analyst",
            "stage": "frontline",
            "profile": "US",
            "openclaw_run_id": "oc-1",
        },
        "payload": {
            "messages": [{"role": "user", "content": "分析"}],
            "tools": [{"name": forbidden}],
        },
    }
    with pytest.raises(ValueError, match="forbidden tool"):
        _scan_openclaw_llm_provider_payload(payload)


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_LEGACY_ALIASES))
def test_openclaw_llm_provider_payload_scan_rejects_legacy_alias_tools(forbidden: str) -> None:
    payload = {
        "source": "provider_request_capture",
        "runtime_markers": {
            "run_id": "run-1",
            "call_id": "call-1",
            "worker_id": "research_manager",
            "stage": "investment_decision",
            "profile": "CRYPTO",
            "openclaw_run_id": "oc-1",
        },
        "payload": {
            "messages": [{"role": "user", "content": "分析"}],
            "tools": [{"name": forbidden}],
        },
    }
    with pytest.raises(ValueError, match="forbidden tool"):
        _scan_openclaw_llm_provider_payload(payload)


def _scan_openclaw_llm_provider_payload(payload: dict[str, object]) -> tuple[str, ...]:
    source = payload.get("source")
    if source != "provider_request_capture":
        raise ValueError("openclaw_llm_provider_payload source must be provider_request_capture")

    body = payload.get("payload")
    if not isinstance(body, dict):
        raise ValueError("payload must be JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("payload.messages must be non-empty list")
    tools = body.get("tools")
    if not isinstance(tools, list):
        raise ValueError("payload.tools must be list")

    names: list[str] = []
    for item in tools:
        name = _tool_name(item)
        if not name:
            raise ValueError("invalid tool item in payload.tools")
        if name in _FORBIDDEN_US_ATOMICS:
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {name}")
        if name in _FORBIDDEN_LEGACY_ALIASES:
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {name}")
        if not (name.startswith("claw_get_") and name.endswith("_pack")):
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {name}")
        if any(pattern in name for pattern in _FORBIDDEN_OPENBB_PATTERNS):
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {name}")
        names.append(name)
    return tuple(sorted(set(names)))


def _tool_name(item: object) -> str | None:
    if isinstance(item, dict):
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        function = item.get("function")
        if isinstance(function, dict):
            function_name = function.get("name")
            if isinstance(function_name, str) and function_name.strip():
                return function_name.strip()
    return None
