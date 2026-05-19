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

    assert "RUNTIME_COMMAND=()" in text
    assert '[ERROR] 用法：%s [-- <test-command> ...]' in text
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
    assert "LOCAL_MONGODB_START_SCRIPT" in text
    assert "start_local_mongodb_if_needed" in text
    assert "wait_mongodb_ok" in text
    assert "CLAW_TRADE_LOCAL_MONGODB_STARTED" in text
    assert "CLAW_TRADE_OPENVIKING_SERVER_BIN" in text
    assert "CLAW_TRADE_OPENVIKING_SERVER_CWD" in text
    assert "CLAW_TRADE_OPENVIKING_MCP_MODULE" in text
    assert "supervise_started_services" in text
    assert "脚本将持续运行并监控服务状态" in text
    assert "运行测试命令" in text


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


def test_start_control_runtime_script_exports_runtime_env_before_child_command() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "export_runtime_env_for_child_commands() {" in text
    assert "export CLAW_TRADE_OPENCLAW_RUNNER" in text
    assert "export CLAW_TRADE_OPENVIKING_BACKEND" in text
    assert 'export CLAW_TRADE_OPENVIKING_MCP_STARTED="${openviking_mcp_started}"' in text
    assert "export OPENCLAW_GATEWAY_URL" in text
    assert "export OPENVIKING_ENDPOINT" in text
    assert "export OPENVIKING_DATA_DIR" in text
    export_index = text.index("export_runtime_env_for_child_commands")
    command_index = text.index('if [[ ${#RUNTIME_COMMAND[@]} -gt 0 ]]; then')
    supervise_index = text.rindex("\nsupervise_started_services")
    assert export_index < command_index
    assert command_index < supervise_index


def test_start_control_runtime_script_child_command_mode_uses_cleanup_trap() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'if [[ "${1}" != "--" ]]; then' in text
    assert 'RUNTIME_COMMAND=("$@")' in text
    assert '"${RUNTIME_COMMAND[@]}"' in text
    assert 'exit "${command_status}"' in text
    trap_index = text.index("trap 'on_script_exit $?' EXIT")
    command_index = text.index('"${RUNTIME_COMMAND[@]}"')
    assert trap_index < command_index


def test_start_control_runtime_script_uses_trade_openviking_wheel_and_keeps_override_bin_branch() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'cd "${ROOT_DIR}"' in text
    assert 'cd "${ROOT_DIR}/third_party/openviking"' not in text
    assert 'if [[ -n "${CLAW_TRADE_OPENVIKING_SERVER_BIN}" ]]; then' in text
    assert "uv run python -m claw_trade.runtime.openviking_report_server" in text
    assert (
        '"${selected_server_bin}" --config "${OPENVIKING_CONFIG_FILE}" --host 127.0.0.1 --port "${OPENVIKING_SERVER_PORT}"'
        in text
    )


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
    assert "BB_MCP_SERVER_PATH" not in text
    assert "BB_MCP_CWD" not in text
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
    assert 'const clawTradePrimaryModel = "deepseek/deepseek-chat";' in text
    assert "const selectedPrimaryModel = clawTradePrimaryModel;" in text
    assert "const selectedProviderModelId = selectedPrimaryModel.includes(\"/\")" in text
    assert "primaryProviderId = selectedPrimaryModel.split(\"/\")[0].trim();" in text
    assert "const selectedProviderModels = Array.isArray(sourcePrimaryProvider.models)" in text
    assert "OpenClaw source provider ${primaryProviderId} 缺少 primary model" in text
    assert "const mergedProviders = {" in text
    assert "[primaryProviderId]: mergedPrimaryProvider" in text
    assert "primary: selectedPrimaryModel" in text
    assert 'alias: "DeepSeek Chat"' in text
    assert "timeoutSeconds: llmIdleTimeoutSeconds" in text
    assert "models: mergedModels," in text
    assert "const sourcePlugins = isPlainObject(sourceConfig.plugins) ? sourceConfig.plugins : {};" in text
    assert "openclaw_plugins/claw-trade-frontline-tools" in text
    assert "paths: [clawTradeFrontlinePluginPath]" in text
    assert '"claw-trade-frontline-tools": {' in text
    assert "plugins: mergedPlugins," in text
    assert "const mergedMcpServers = {" in text
    assert "delete mergedMcpServers.bb_crypto_data;" in text
    assert "const mergedMcp = {" in text
    assert "mergedMcpServers.bb_crypto_data =" not in text
    assert "servers: mergedMcpServers," in text
    assert "mcp: mergedMcp," in text
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
        "report_polisher",
    )
    for worker in workers:
        assert f'"{worker}"' in text

    prepare_index = text.index("prepare_openclaw_trade_agent_config")
    gateway_index = text.index("gateway_cmd=(")
    assert prepare_index < gateway_index


def test_start_control_runtime_script_unifies_openviking_embedding_to_openclaw_primary() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'OPENCLAW_CONFIG_PATH_VALUE="${OPENCLAW_CONFIG_PATH}"' in text
    assert "const openClawConfigPath = process.env.OPENCLAW_CONFIG_PATH_VALUE;" in text
    assert "const primaryModel = typeof openClawDefaultModel.primary === \"string\"" in text
    assert "OpenClaw runtime config 缺少 primary model" in text
    assert "embedding: {" in text
    assert 'provider: "litellm"' in text
    assert "model: primaryModel" in text
    assert "max_concurrent: 1" in text
    assert "max_retries: 0" in text


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
