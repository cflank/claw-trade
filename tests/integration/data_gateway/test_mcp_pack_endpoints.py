from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_openbb_mcp_exposes_only_seven_pack_tools_and_routes() -> None:
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

        app = wrapper.create_pack_fastapi_app()
        claw_routes = sorted(
            route.path
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/v1/claw/")
        )

        mcp = wrapper.create_pack_mcp_server(
            MCPSettings(
                api_prefix="/api/v1",
                default_tool_categories=["claw"],
                allowed_tool_categories=["claw"],
                enable_tool_discovery=False,
            )
        )

        print(
            json.dumps(
                {
                    "tools": list_mcp_tool_names(mcp),
                    "routes": claw_routes,
                }
            )
        )
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
    assert result["routes"] == [
        "/api/v1/claw/get_fundamental_pack",
        "/api/v1/claw/get_hot_money_pack",
        "/api/v1/claw/get_lockup_pack",
        "/api/v1/claw/get_market_pack",
        "/api/v1/claw/get_news_pack",
        "/api/v1/claw/get_policy_pack",
        "/api/v1/claw/get_social_pack",
    ]
    assert result["tools"] == [
        "claw_get_fundamental_pack",
        "claw_get_hot_money_pack",
        "claw_get_lockup_pack",
        "claw_get_market_pack",
        "claw_get_news_pack",
        "claw_get_policy_pack",
        "claw_get_social_pack",
    ]
