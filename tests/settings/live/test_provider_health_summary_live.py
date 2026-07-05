from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient
from claw_trade.web.routes_ui import router as ui_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _rpc_client() -> OpenClawGatewayRpcClient:
    return OpenClawGatewayRpcClient(
        gateway_call_bin=os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "openclaw"),
        gateway_ws_url=os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789"),
        timeout_ms=int(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT_MS", "10000")),
        token=os.environ.get("OPENCLAW_GATEWAY_TOKEN") or None,
        password=os.environ.get("OPENCLAW_GATEWAY_PASSWORD") or None,
    )


def _load_runtime_env(runtime_env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not runtime_env_path.exists():
        return values
    for raw in runtime_env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _health_status(url: str) -> tuple[bool, int | None]:
    try:
        with urlopen(url, timeout=2) as response:  # nosec B310: localhost health check
            return response.status == 200, int(response.status)
    except Exception:
        return False, None


def _runtime_value(key: str, runtime_env: dict[str, str]) -> str | None:
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
    return runtime_env.get(key)


def test_live_provider_health_summary_has_real_source_and_no_internal_leak(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s08"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    runtime_env_path = project_root / ".runtime" / "dev-services" / "runtime.env"
    runtime_env = _load_runtime_env(runtime_env_path)
    health_1933_ok, health_1933_status = _health_status("http://127.0.0.1:1933/health")
    health_18789_ok, health_18789_status = _health_status("http://127.0.0.1:18789/health")
    expected_config = str((project_root / ".runtime" / "dev-services" / "openviking" / "ov.conf").resolve())
    expected_data = str((project_root / ".runtime" / "dev-services" / "openviking" / "data").resolve())

    rows = [
        ("runtime.env exists", runtime_env_path.exists(), str(runtime_env_path.exists())),
        (
            "CLAW_TRADE_OPENVIKING_MCP_MODULE unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_MCP_MODULE", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_MCP_MODULE", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_MCP_CWD unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_MCP_CWD", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_MCP_CWD", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_SERVER_BIN unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_SERVER_BIN", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_SERVER_BIN", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_SERVER_CWD unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_SERVER_CWD", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_SERVER_CWD", runtime_env)),
        ),
        (
            "OPENVIKING_CONFIG_FILE points to .runtime/dev-services/openviking/ov.conf",
            _runtime_value("OPENVIKING_CONFIG_FILE", runtime_env) == expected_config,
            str(_runtime_value("OPENVIKING_CONFIG_FILE", runtime_env)),
        ),
        (
            "OPENVIKING_DATA_DIR points to .runtime/dev-services/openviking/data",
            _runtime_value("OPENVIKING_DATA_DIR", runtime_env) == expected_data,
            str(_runtime_value("OPENVIKING_DATA_DIR", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_MCP_STARTED=0",
            _runtime_value("CLAW_TRADE_OPENVIKING_MCP_STARTED", runtime_env) == "0",
            str(_runtime_value("CLAW_TRADE_OPENVIKING_MCP_STARTED", runtime_env)),
        ),
        ("1933 /health", health_1933_ok, str(health_1933_status)),
        ("18789 /health", health_18789_ok, str(health_18789_status)),
    ]
    preflight_lines = [
        "runtime_profile=scripts/start-control-runtime.sh; repo uv; OpenViking 1933; OpenClaw gateway 18789; no invest sidecar; .runtime/dev-services",
        "",
        "| check | pass | observed |",
        "| --- | --- | --- |",
    ]
    preflight_lines.extend([f"| {name} | {'PASS' if ok else 'FAIL'} | {observed} |" for name, ok, observed in rows])
    preflight_text = "\n".join(preflight_lines) + "\n"
    (artifact_dir / "runtime-profile-and-preflight.md").write_text(preflight_text, encoding="utf-8")

    for name, ok, observed in rows:
        assert ok, f"{name} failed: {observed}"

    bridge = LlmSettingsBridge(
        _rpc_client(),
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
    )
    readiness_before = bridge.get_report_model_readiness()
    summary = bridge.get_provider_health_summary()
    readiness_after = bridge.get_report_model_readiness()

    app = FastAPI()
    app.state.ui_services = SimpleNamespace(llm_bridge=bridge)
    app.include_router(ui_router, prefix="/api/ui")
    with TestClient(app) as client:
        response = client.get("/api/ui/get-advanced-diagnostics-provider-health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] in {"not_configured", "healthy", "degraded"}
    assert payload["source"] == "openclaw.models.probeStatus"
    assert payload["checkedAt"]

    lowered = str(payload["userMessage"]).lower()
    assert "provider attempt" not in lowered
    assert "raw payload" not in lowered
    assert "request body" not in lowered
    assert "provider request" not in lowered

    assert readiness_before.state == readiness_after.state
    assert readiness_before.blocked == readiness_after.blocked
    assert readiness_before.ready == readiness_after.ready

    assert summary["state"] == payload["state"]
    (artifact_dir / "provider-health-summary-live.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
