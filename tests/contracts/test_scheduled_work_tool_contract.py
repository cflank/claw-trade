from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "openclaw_plugins" / "claw-trade-scheduled-work-tools"
SCHEDULED_WORK_AGENT_IDS = (
    "price_alert_scan_worker",
    "scheduled_report_runner",
    "market_data_maintenance_worker",
)


def test_scheduled_work_tool_manifest_matches_worker_profile() -> None:
    manifest = json.loads((PLUGIN_DIR / "openclaw.plugin.json").read_text(encoding="utf-8"))

    assert manifest["contracts"]["tools"] == ["claw-trade-scheduled-work-wake"]
    for agent_id in SCHEDULED_WORK_AGENT_IDS:
        agent_dir = REPO_ROOT / "agents" / agent_id
        stages = yaml.safe_load((agent_dir / "STAGES.yaml").read_text(encoding="utf-8"))
        assert stages["worker"] == agent_id
        assert stages["skills"]["mounted"] == ["claw-trade-scheduled-work-wake"]
        for profile in stages["profiles"].values():
            assert profile["openviking_access"] == "none"
            assert profile["tools"] in ([], ["claw-trade-scheduled-work-wake"])
            if profile["tools"]:
                assert profile["tools"] == ["claw-trade-scheduled-work-wake"]


def test_scheduled_work_tool_accepts_all_scheduled_work_kinds_without_internal_endpoint_or_token() -> None:
    source = (PLUGIN_DIR / "index.js").read_text(encoding="utf-8")
    schema_start = source.index("const TOOL_INPUT_SCHEMA")
    schema_end = source.index("const TOOL_ERROR_CODES")
    schema_text = source[schema_start:schema_end]

    for kind in ("price_alert_scan", "scheduled_report", "selection_data_refresh", "data_maintenance"):
        assert kind in schema_text
    for field in (
        "bucketKey",
        "scheduledReportId",
        "market",
        "jobKind",
        "cronRunId",
        "requestId",
        "reason",
        "maintenanceJobId",
    ):
        assert field in schema_text
    assert "token" not in schema_text.lower()
    assert "url" not in schema_text.lower()
    assert "CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN" in source
    assert "CLAW_TRADE_UI_INTERNAL_BASE_URL" in source


def test_scheduled_work_tool_executes_real_validation_and_forwarding_contract(tmp_path: Path) -> None:
    temp_root = tmp_path / "plugin-runtime"
    temp_plugin_dir = temp_root / "openclaw_plugins" / "claw-trade-scheduled-work-tools"
    temp_sdk_dir = temp_root / "third_party" / "openclaw" / "dist" / "plugin-sdk"
    temp_plugin_dir.mkdir(parents=True)
    temp_sdk_dir.mkdir(parents=True)
    (temp_root / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    shutil.copy2(PLUGIN_DIR / "index.js", temp_plugin_dir / "index.js")
    (temp_sdk_dir / "plugin-entry.js").write_text(
        "export function definePluginEntry(entry) { return entry; }\n",
        encoding="utf-8",
    )

    script = tmp_path / "scheduled_work_tool_contract.mjs"
    script.write_text(
        textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import plugin from "__PLUGIN_URL__";

            let tool;
            plugin.register({
              registerTool(factory, registration) {
                tool = factory();
                assert.deepEqual(registration, { name: "claw-trade-scheduled-work-wake", optional: true });
              },
            });

            assert.equal(tool.name, "claw-trade-scheduled-work-wake");
            process.env.CLAW_TRADE_UI_INTERNAL_BASE_URL = "http://127.0.0.1:18080/api/ui/channel-inbound-message";
            process.env.CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN = "test-internal-token";

            const requests = [];
            globalThis.fetch = async (url, options) => {
              requests.push({
                url: String(url),
                method: options.method,
                headers: options.headers,
                body: JSON.parse(options.body),
              });
              return {
                ok: true,
                status: 200,
                async json() {
                  return { status: "ok" };
                },
              };
            };

            async function execute(params) {
              const result = await tool.execute("call", params);
              assert.equal(result.isError, false);
              assert.equal(result.details.status, "ok");
              const request = requests.at(-1);
              assert.equal(request.url, "http://127.0.0.1:18080/api/ui/internal/scheduled-work/cron-wake");
              assert.equal(request.method, "POST");
              assert.equal(request.headers["content-type"], "application/json");
              assert.equal(request.headers["x-claw-trade-internal-token"], "test-internal-token");
              return request.body;
            }

            async function expectToolParamsInvalidWithoutFetch(params) {
              const requestCount = requests.length;
              const result = await tool.execute("call", params);
              assert.equal(result.isError, true);
              assert.equal(result.details.error.code, "TOOL_PARAMS_INVALID");
              assert.equal(requests.length, requestCount);
            }

            const priceAlert = await execute({
              kind: "price_alert_scan",
              bucketKey: "CRYPTO:3m",
              cronRunId: "auto",
            });
            assert.equal(priceAlert.kind, "price_alert_scan");
            assert.equal(priceAlert.bucketKey, "CRYPTO:3m");
            assert.match(priceAlert.cronRunId, /^price-alert-scan:CRYPTO:3m:\\d+:[0-9a-f-]+$/);

            const scheduledReport = await execute({
              kind: "scheduled_report",
              scheduledReportId: "scheduled-report-1",
              cronRunId: "auto",
            });
            assert.equal(scheduledReport.kind, "scheduled_report");
            assert.equal(scheduledReport.scheduledReportId, "scheduled-report-1");
            assert.match(scheduledReport.cronRunId, /^scheduled-report:scheduled-report-1:\\d+:[0-9a-f-]+$/);

            const selectionRefresh = await execute({
              kind: "selection_data_refresh",
              cronRunId: "auto",
            });
            assert.equal(selectionRefresh.kind, "selection_data_refresh");
            assert.equal(selectionRefresh.reason, "scheduled_data_refresh");
            assert.match(
              selectionRefresh.cronRunId,
              /^selection-data-refresh:scheduled_data_refresh:\\d+:[0-9a-f-]+$/,
            );

            const dataMaintenance = await execute({
              kind: "data_maintenance",
              market: "CN_A",
              jobKind: "provider_cache_refresh",
              maintenanceJobId: "maintenance-1",
              cronRunId: "auto",
            });
            assert.equal(dataMaintenance.kind, "data_maintenance");
            assert.equal(dataMaintenance.market, "CN_A");
            assert.equal(dataMaintenance.jobKind, "provider_cache_refresh");
            assert.equal(dataMaintenance.maintenanceJobId, "maintenance-1");
            assert.match(dataMaintenance.cronRunId, /^data-maintenance:CN_A:provider_cache_refresh:\\d+:[0-9a-f-]+$/);

            await expectToolParamsInvalidWithoutFetch({
              kind: "price_alert_scan",
              bucketKey: "CRYPTO:3m",
              cronRunId: "cron-run-1",
              token: "must-not-be-model-facing",
            });

            for (const missingCronRunId of [
              { kind: "price_alert_scan", bucketKey: "CRYPTO:3m" },
              { kind: "scheduled_report", scheduledReportId: "scheduled-report-1" },
              { kind: "selection_data_refresh" },
              { kind: "data_maintenance", market: "CN_A", jobKind: "provider_cache_refresh" },
            ]) {
              await expectToolParamsInvalidWithoutFetch(missingCronRunId);
            }

            await expectToolParamsInvalidWithoutFetch({
              kind: "scheduled_report",
              scheduledReportId: "scheduled-report-1",
              cronRunId: "cron-run-1",
              bucketKey: "CRYPTO:3m",
            });
            await expectToolParamsInvalidWithoutFetch({
              kind: "scheduled_report",
              scheduledReportId: "scheduled-report-1",
              cronRunId: "cron-run-1",
              market: "CN_A",
            });
            """
        ).replace("__PLUGIN_URL__", (temp_plugin_dir / "index.js").as_uri()),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(script)],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_scheduled_work_tool_preserves_ui_api_prefix_for_internal_wake() -> None:
    source = (PLUGIN_DIR / "index.js").read_text(encoding="utf-8")

    assert 'new URL("/internal/scheduled-work/cron-wake"' not in source
    assert "resolveInternalWakeUrl" in source
    assert 'basePath.endsWith("/channel-inbound-message")' in source
    assert 'new URL("internal/scheduled-work/cron-wake"' in source
