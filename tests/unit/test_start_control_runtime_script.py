from __future__ import annotations

import subprocess
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "start-control-runtime.sh"


def test_start_control_runtime_script_is_bash_valid() -> None:
    script = _script_path()
    completed = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_start_control_runtime_script_contains_required_guards() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "1933" in text
    assert "1944" in text
    assert "18789" in text
    assert "runs/probe" in text
    assert ".runtime/dev-services" in text
    assert "CLAW_TRADE_OPENVIKING_MCP_MODULE" in text
    assert "CLAW_TRADE_OPENVIKING_MCP_CWD" in text
    assert "runtime.env" in text
    assert "OPENVIKING_MCP_URL" in text
    assert "OPENVIKING_BASE_URL" in text
    assert "OPV_ENDPOINT" in text
    assert "OPV_API_KEY" in text
    assert "OPENCLAW_GATEWAY_URL" in text
    assert "OPENCLAW_GATEWAY_CALL_BIN" in text
    assert "OPENCLAW_STATE_DIR" in text
    assert "OPENCLAW_CONFIG_PATH" in text
    assert "CLAW_TRADE_ENV_PATH" in text
    assert "OPENCLAW_SOURCE_CONFIG_PATH" in text
    assert "OPENCLAW_SOURCE_ENV_PATH" in text
    assert "OPENCLAW_GATEWAY_TIMEOUT_MS" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS" in text
    assert "CLAW_TRADE_OPENVIKING_SERVER_BIN" in text
    assert "CLAW_TRADE_OPENVIKING_SERVER_CWD" in text
    assert "CLAW_TRADE_OPENVIKING_MCP_MODULE" in text
    assert "supervise_started_services" in text
    assert "脚本将持续运行并监控服务状态" in text


def test_start_control_runtime_script_has_explicit_mcp_sidecar_args() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "--transport streamable-http" in text
    assert '--host "${OPENVIKING_MCP_HOST}"' in text
    assert '--port "${OPENVIKING_MCP_PORT}"' in text


def test_start_control_runtime_script_supports_default_skip_mcp_sidecar() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "未显式配置，跳过 MCP sidecar" in text
    assert 'resolved_mcp_module="${CLAW_TRADE_OPENVIKING_MCP_MODULE:-}"' in text
    assert 'if [[ -z "${resolved_mcp_module}" ]]; then' in text
    assert 'resolved_mcp_module="$(detect_native_mcp_module)"' not in text


def test_start_control_runtime_script_writes_mcp_started_status_and_conditional_url() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "CLAW_TRADE_OPENVIKING_MCP_STARTED=${openviking_mcp_started}" in text
    assert "OPENCLAW_STATE_DIR=${OPENCLAW_STATE_DIR}" in text
    assert "OPENCLAW_CONFIG_PATH=${OPENCLAW_CONFIG_PATH}" in text
    assert "OPENCLAW_GATEWAY_TIMEOUT_MS=${OPENCLAW_GATEWAY_TIMEOUT_MS}" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS=${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS}" in text
    assert 'if [[ "${openviking_mcp_started}" == "1" ]]; then' in text
    assert "printf 'OPENVIKING_MCP_URL=%s\\n' \"${OPENVIKING_MCP_URL}\"" in text


def test_start_control_runtime_script_uses_trade_openviking_wheel_and_keeps_override_bin_branch() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'cd "${ROOT_DIR}"' in text
    assert 'cd "${ROOT_DIR}/third_party/openviking"' not in text
    assert 'if [[ -n "${CLAW_TRADE_OPENVIKING_SERVER_BIN}" ]]; then' in text
    assert "uv run openviking-server" in text
    assert '"${selected_server_bin}" --host 127.0.0.1 --port "${OPENVIKING_SERVER_PORT}"' in text


def test_start_control_runtime_script_stops_gateway_service_before_and_after_listener_cleanup() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "stop_openclaw_gateway_service() {" in text
    assert "env -u OPENCLAW_STATE_DIR -u OPENCLAW_CONFIG_PATH \\" in text
    assert '"${OPENCLAW_GATEWAY_CALL_BIN}" gateway stop >/dev/null 2>&1 || true' in text
    assert "gateway stop --port" not in text
    assert "stop_openclaw_gateway_service\nkill_by_pid_file" in text
    assert 'kill_port_listener "${OPENCLAW_GATEWAY_PORT}"\nstop_openclaw_gateway_service' in text


def test_start_control_runtime_script_removes_stale_runtime_env_before_service_cleanup() -> None:
    text = _script_path().read_text(encoding="utf-8")

    rm_index = text.index('rm -f "${RUNTIME_ENV_PATH}"')
    cleanup_index = text.index('log_info "清理旧 PID 与端口监听"')
    assert rm_index < cleanup_index


def test_start_control_runtime_script_gateway_run_uses_local_state_and_dev_mode() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-${RUNTIME_DIR}/openclaw-state}"' in text
    assert 'OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${OPENCLAW_STATE_DIR}/openclaw.json}"' in text
    assert 'mkdir -p "${OPENCLAW_STATE_DIR}"' in text
    assert "--dev" in text
    assert "gateway\n  run\n  --dev" in text
    assert "OPENCLAW_STATE_DIR=\"${OPENCLAW_STATE_DIR}\" \\\nOPENCLAW_CONFIG_PATH=\"${OPENCLAW_CONFIG_PATH}\" \\" in text
    assert "reset" not in text


def test_start_control_runtime_script_prepares_trade_worker_agent_config_before_gateway_boot() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "prepare_openclaw_trade_agent_config() {" in text
    assert "OPENCLAW_SOURCE_CONFIG_PATH_VALUE" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE" in text
    assert "const sourcePath = process.env.OPENCLAW_SOURCE_CONFIG_PATH_VALUE;" in text
    assert "const rawLlmIdleTimeoutSeconds = process.env.OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE;" in text
    assert "const llmIdleTimeoutSeconds = Number.parseInt(String(rawLlmIdleTimeoutSeconds ?? \"\"), 10);" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS 必须是正整数" in text
    assert "const workers = [" in text
    assert 'merged.default = workerId === "market_analyst";' in text
    assert 'merged.workspace = `${rootDir}/agents/${workerId}`;' in text
    assert "function readWorkerMountedSkills(workerId) {" in text
    assert "skills/manifest.yaml" in text
    assert "worker skill manifest 不存在" in text
    assert "worker skill path 非法" in text
    assert "worker skill manifest 没有可挂载 skill" in text
    assert "merged.skills = readWorkerMountedSkills(workerId);" in text
    assert "const mergedDefaults = {" in text
    assert "const sourceModels = isPlainObject(sourceConfig.models) ? sourceConfig.models : {};" in text
    assert "const sourceProviders = isPlainObject(sourceModels.providers) ? sourceModels.providers : {};" in text
    assert "const sourceDefaultModel = isPlainObject(sourceDefaults.model) ? sourceDefaults.model : {};" in text
    assert "const sourcePrimaryModel = typeof sourceDefaultModel.primary === \"string\" ? sourceDefaultModel.primary.trim() : \"\";" in text
    assert "primaryProviderId = sourcePrimaryModel.split(\"/\")[0].trim();" in text
    assert "timeoutSeconds: llmIdleTimeoutSeconds" in text
    assert "models: mergedModels," in text
    assert "const sourcePlugins = isPlainObject(sourceConfig.plugins) ? sourceConfig.plugins : {};" in text
    assert "openclaw_plugins/claw-trade-frontline-tools" in text
    assert "const mergedPluginLoadPaths = sourcePluginLoadPaths.includes(clawTradeFrontlinePluginPath)" in text
    assert '"claw-trade-frontline-tools": {' in text
    assert "plugins: mergedPlugins," in text
    assert "idleTimeoutSeconds: llmIdleTimeoutSeconds" not in text
    assert "defaults.llm" not in text
    assert "skipBootstrap: true" in text
    assert "gateway: {" in text
    assert 'mode: "local"' in text
    assert 'bind: "loopback"' in text
    assert "const merged = isPlainObject(sourceEntry) ? { ...sourceEntry } : {};" in text
    assert "console.error(`[ERROR] OpenClaw source config 不存在" in text
    workers = (
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
    )
    for worker in workers:
        assert f'"{worker}"' in text

    prepare_index = text.index("prepare_openclaw_trade_agent_config")
    gateway_index = text.index("gateway_cmd=(")
    assert prepare_index < gateway_index


def test_start_control_runtime_script_loads_claw_trade_env_before_source_env_without_logging_secret_values() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "load_runtime_env_files_into_process_env() {" in text
    assert 'CLAW_TRADE_ENV_PATH="${CLAW_TRADE_ENV_PATH:-${ROOT_DIR}/.env.local}"' in text
    assert 'OPENCLAW_SOURCE_ENV_PATH="${OPENCLAW_SOURCE_ENV_PATH:-${HOME}/.openclaw/.env}"' in text
    assert "const originalKeys = new Set(Object.keys(process.env));" in text
    assert "parseEnvFile(sourceEnvPath)" in text
    assert "parseEnvFile(clawTradeEnvPath)" in text
    assert "process.stdout.write(\"\\u0000\")" in text
    assert "claw-trade .env.local 不存在，跳过注入" in text
    assert "OpenClaw source .env 不存在，跳过注入" in text
    assert 'printf \'[INFO] 环境变量注入条目数：%s\\n\' "${exported_count}"' in text
    load_index = text.index("load_runtime_env_files_into_process_env")
    defaults_index = text.index('OPENVIKING_ENDPOINT="${OPENVIKING_ENDPOINT:-http://127.0.0.1:1933}"')
    gateway_index = text.index("gateway_cmd=(")
    assert load_index < defaults_index
    assert load_index < gateway_index


def test_start_control_runtime_script_no_dynamic_expression_execution_usage() -> None:
    text = _script_path().read_text(encoding="utf-8")
    assert ("ev" + "al") not in text


def test_start_control_runtime_script_does_not_write_secrets_or_remove_runs_root() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "OPENVIKING_API_KEY=" not in text
    assert "OPENCLAW_GATEWAY_TOKEN=" not in text
    forbidden_plain = "rm -rf " + "runs"
    forbidden_quote = 'rm -rf "' + "runs"
    assert forbidden_plain not in text
    assert forbidden_quote not in text


def test_start_control_runtime_script_has_no_forbidden_success_patterns() -> None:
    text = _script_path().read_text(encoding="utf-8")
    forbidden_patterns = (
        "mo" + "ck",
        "st" + "ub",
        "fa" + "ke",
        "fall" + "back",
        "direct" + "_llm",
        "material" + "izer",
    )
    for pattern in forbidden_patterns:
        assert pattern not in text
