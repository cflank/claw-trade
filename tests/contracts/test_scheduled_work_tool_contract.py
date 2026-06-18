from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "openclaw_plugins" / "claw-trade-scheduled-work-tools"
AGENT_DIR = REPO_ROOT / "agents" / "price_alert_scan_worker"


def test_scheduled_work_tool_manifest_matches_worker_profile() -> None:
    manifest = json.loads((PLUGIN_DIR / "openclaw.plugin.json").read_text(encoding="utf-8"))
    stages = (AGENT_DIR / "STAGES.yaml").read_text(encoding="utf-8")

    assert manifest["contracts"]["tools"] == ["claw-trade-scheduled-work-wake"]
    assert "claw-trade-scheduled-work-wake" in stages
    assert "price_alert_scan_worker" in stages


def test_scheduled_work_tool_input_does_not_accept_internal_endpoint_or_token() -> None:
    source = (PLUGIN_DIR / "index.js").read_text(encoding="utf-8")
    schema_start = source.index("const TOOL_INPUT_SCHEMA")
    schema_end = source.index("const TOOL_ERROR_CODES")
    schema_text = source[schema_start:schema_end]

    assert "bucketKey" in schema_text
    assert "cronRunId" in schema_text
    assert "kind" in schema_text
    assert "token" not in schema_text.lower()
    assert "url" not in schema_text.lower()
    assert "CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN" in source
    assert "CLAW_TRADE_UI_INTERNAL_BASE_URL" in source


def test_scheduled_work_tool_preserves_ui_api_prefix_for_internal_wake() -> None:
    source = (PLUGIN_DIR / "index.js").read_text(encoding="utf-8")

    assert 'new URL("/internal/scheduled-work/cron-wake"' not in source
    assert "resolveInternalWakeUrl" in source
    assert 'basePath.endsWith("/channel-inbound-message")' in source
    assert 'new URL("internal/scheduled-work/cron-wake"' in source
