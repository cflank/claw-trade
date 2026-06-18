from __future__ import annotations

import shlex
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


def test_start_control_runtime_health_polling_suppresses_expected_retry_noise() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'curl -s -o /dev/null -w' in text
    assert "2>/dev/null || true" in text
    assert 'curl -sS -o /dev/null -w' not in text


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
    assert "OPENCLAW_CONTROL_UI_INDEX" in text
    assert "ensure_openclaw_control_ui_assets" in text
    assert "OPENCLAW_WEIXIN_PLUGIN_ID" in text
    assert "OPENCLAW_WEIXIN_PLUGIN_SPEC" in text
    assert "ensure_openclaw_weixin_plugin_ready" in text
    assert "@tencent-weixin/openclaw-weixin@2.4.4" in text
    assert 'plugins install "${OPENCLAW_WEIXIN_PLUGIN_SPEC}"' in text
    assert "OPENCLAW_STATE_DIR" in text
    assert "OPENCLAW_CONFIG_PATH" in text
    assert "preauthorize_openclaw_gateway_cli_scopes" in text
    assert "CLAW_TRADE_ENV_PATH" in text
    assert "CLAW_TRADE_LLM_MODEL" in text
    assert "DEEPSEEK_API_KEY" in text
    assert "OPENCLAW_GATEWAY_TIMEOUT_MS" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS" in text
    assert 'OPENCLAW_GATEWAY_TOKEN="claw-trade-dev-${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}"' in text
    assert "export OPENCLAW_GATEWAY_TOKEN" in text
    assert 'CLAW_TRADE_UI_INBOUND_TIMEOUT_MS="${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS:-60000}"' in text
    assert "LOCAL_MONGODB_START_SCRIPT" in text
    assert "start_local_mongodb_if_needed" in text
    assert "wait_mongodb_ok" in text
    assert 'CN_A_MONGODB_URI="${CN_A_MONGODB_URI:-mongodb://${CN_A_MONGODB_BIND_IP}:${CN_A_MONGODB_PORT}}"' in text
    assert 'DATA_GATEWAY_MONGODB_URI="${DATA_GATEWAY_MONGODB_URI:-${CN_A_MONGODB_URI}}"' in text
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

    assert "write_runtime_env_var() {" in text
    assert "printf '%s=' \"${key}\" >> \"${RUNTIME_ENV_PATH}\"" in text
    assert "printf '%q\\n' \"${value}\" >> \"${RUNTIME_ENV_PATH}\"" in text
    assert 'write_runtime_env_var "CLAW_TRADE_OPENVIKING_MCP_STARTED" "${openviking_mcp_started}"' in text
    assert 'write_runtime_env_var "OPENCLAW_STATE_DIR" "${OPENCLAW_STATE_DIR}"' in text
    assert 'write_runtime_env_var "OPENCLAW_CONFIG_PATH" "${OPENCLAW_CONFIG_PATH}"' in text
    assert 'write_runtime_env_var "OPENCLAW_GATEWAY_TIMEOUT_MS" "${OPENCLAW_GATEWAY_TIMEOUT_MS}"' in text
    assert 'write_runtime_env_var "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS" "${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS}"' in text
    assert 'write_runtime_env_var "CN_A_MONGODB_URI" "${CN_A_MONGODB_URI}"' in text
    assert 'write_runtime_env_var "DATA_GATEWAY_MONGODB_URI" "${DATA_GATEWAY_MONGODB_URI}"' in text
    assert 'write_runtime_env_var "DATA_GATEWAY_SEED_MONGODB_URI" "${DATA_GATEWAY_SEED_MONGODB_URI}"' in text
    assert 'write_runtime_env_var "DATA_GATEWAY_SEED_MONGODB_DATABASE" "${DATA_GATEWAY_SEED_MONGODB_DATABASE}"' in text
    assert 'write_runtime_env_var "DATA_GATEWAY_COLUMNAR_ROOT" "${DATA_GATEWAY_COLUMNAR_ROOT}"' in text
    assert 'if [[ "${openviking_mcp_started}" == "1" ]]; then' in text
    assert 'write_runtime_env_var "OPENVIKING_MCP_URL" "${OPENVIKING_MCP_URL}"' in text


def test_start_control_runtime_mounts_a_share_factory_seed_by_default() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'DATA_GATEWAY_SEED_MONGODB_URI="${DATA_GATEWAY_SEED_MONGODB_URI:-${CN_A_MONGODB_URI}}"' in text
    assert 'DATA_GATEWAY_SEED_MONGODB_DATABASE="${DATA_GATEWAY_SEED_MONGODB_DATABASE:-claw_trade_a_share_factory_seed}"' in text


def test_start_control_runtime_script_exports_runtime_env_before_child_command() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "export_runtime_env_for_child_commands() {" in text
    assert "export CLAW_TRADE_OPENCLAW_RUNNER" in text
    assert "export CLAW_TRADE_OPENVIKING_BACKEND" in text
    assert 'export CLAW_TRADE_OPENVIKING_MCP_STARTED="${openviking_mcp_started}"' in text
    assert "export OPENCLAW_GATEWAY_URL" in text
    assert "export OPENCLAW_GATEWAY_TOKEN" in text
    assert "export OPENVIKING_ENDPOINT" in text
    assert "export OPENVIKING_DATA_DIR" in text
    assert "export CN_A_MONGODB_URI" in text
    assert "export DATA_GATEWAY_MONGODB_URI" in text
    assert "DATA_GATEWAY_SEED_MONGODB_DATABASE" in text
    assert "DATA_GATEWAY_COLUMNAR_ROOT" in text
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


def test_start_control_runtime_script_blocks_factory_columnar_root_by_default() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "guard_factory_columnar_root() {" in text
    assert "data/crypto-history-full/*" in text
    assert "CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE" in text
    assert "运行时 provider 增量会写入该目录，已阻止" in text
    guard_call_index = text.index("\nguard_factory_columnar_root\n")
    cleanup_index = text.index('log_info "删除旧 runtime.env，避免复验读取到过期环境"')
    assert guard_call_index < cleanup_index


def test_start_control_runtime_script_blocks_factory_mongo_database_by_default() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "guard_factory_mongo_database() {" in text
    assert "claw_trade_crypto_history_*" in text
    assert "CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE" in text
    assert "DATA_GATEWAY_SEED_MONGODB_DATABASE" in text
    assert "运行时证据会写入该 Mongo 库，已阻止" in text
    guard_call_index = text.index("\nguard_factory_mongo_database\n")
    cleanup_index = text.index('log_info "删除旧 runtime.env，避免复验读取到过期环境"')
    assert guard_call_index < cleanup_index


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


def test_start_control_runtime_script_invalidates_missing_runtime_columnar_manifests_after_cleanup() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "prune_missing_runtime_columnar_manifests() {" in text
    assert "missing_columnar_file_after_runtime_cleanup" in text
    assert '"status": "invalid"' in text
    assert "运行时列式 manifest 清理失败，停止启动" in text
    assert "运行时列式 manifest 清理失败，继续启动" not in text
    delete_index = text.index('find "${RUNTIME_DIR}" -mindepth 1 -maxdepth 1')
    mongo_index = text.index("\nstart_local_mongodb_if_needed\n")
    prune_index = text.index("\nprune_missing_runtime_columnar_manifests\n")
    openviking_index = text.index("\nconfigure_openviking_embedding_runtime_flags\n")
    assert delete_index < mongo_index < prune_index < openviking_index


def test_start_control_runtime_script_gateway_run_uses_local_state_and_dev_mode() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-${RUNTIME_DIR}/openclaw-state}"' in text
    assert 'OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${OPENCLAW_STATE_DIR}/openclaw.json}"' in text
    assert 'mkdir -p "${OPENCLAW_STATE_DIR}"' in text
    assert "--dev" in text
    assert "gateway\n  run\n  --dev" in text
    assert "OPENCLAW_STATE_DIR=\"${OPENCLAW_STATE_DIR}\" \\\nOPENCLAW_CONFIG_PATH=\"${OPENCLAW_CONFIG_PATH}\" \\" in text
    assert "reset" not in text


def test_start_control_runtime_script_writes_gateway_token_to_cli_config() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'OPENCLAW_GATEWAY_TOKEN_VALUE="${OPENCLAW_GATEWAY_TOKEN:-}"' in text
    assert 'const gatewayToken = String(process.env.OPENCLAW_GATEWAY_TOKEN_VALUE || "").trim();' in text
    assert "auth: { token: gatewayToken }" in text
    assert "remote: { token: gatewayToken }" in text


def test_start_control_runtime_script_removes_only_stale_openviking_data_lock() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "clear_stale_openviking_data_lock() {" in text
    assert 'local pid_file="${OPENVIKING_DATA_DIR}/.openviking.pid"' in text
    assert 'kill -0 "${lock_pid}"' in text
    assert "OpenViking data 目录仍被进程占用" in text
    assert "删除 stale OpenViking data lock" in text
    assert "clear_stale_openviking_data_lock\n\nif [[ ! -x" in text


def test_start_control_runtime_script_ensures_openclaw_control_ui_assets_before_gateway_boot() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "ensure_openclaw_control_ui_assets() {" in text
    assert 'OPENCLAW_CONTROL_UI_INDEX="${OPENCLAW_CONTROL_UI_INDEX:-${OPENCLAW_PACKAGE_DIR}/dist/control-ui/index.html}"' in text
    assert "pnpm ui:build" in text
    call_index = text.index("\nensure_openclaw_control_ui_assets\n")
    weixin_call_index = text.index("\nensure_openclaw_weixin_plugin_ready\n")
    prepare_index = text.index("\nprepare_openclaw_trade_agent_config\n")
    gateway_index = text.index("\ngateway_cmd=(")
    assert call_index < weixin_call_index < prepare_index < gateway_index


def test_start_control_runtime_script_preauthorizes_gateway_cli_admin_scope_with_approve_retry() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "preauthorize_openclaw_gateway_cli_scopes() {" in text
    assert "openclaw-gateway-scope-preauth.log" in text
    assert "openclaw-gateway-scope-preauth-retry.log" in text
    assert "openclaw-gateway-scope-approve.log" in text
    assert "openclaw-gateway-scope-approve-state.log" in text
    assert "update.status" in text
    assert "--scope" in text
    assert "operator.admin" in text
    assert '--url\n    "${OPENCLAW_GATEWAY_URL}"' in text
    assert 'OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}"' in text
    assert 'OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}"' in text
    assert "scope upgrade pending approval" in text
    assert "extract_openclaw_pairing_request_id" in text
    assert "approve_openclaw_pairing_request_from_state" in text
    assert '"${OPENCLAW_GATEWAY_CALL_BIN}" devices approve "${request_id}"' in text
    assert 'approveDevicePairing(requestId, { callerScopes: ["operator.admin"] }, stateDir)' in text
    assert '"${preauth_cmd[@]}" >"${preauth_retry_log}"' in text
    assert "agent.runSingleWorker" not in text
    assert "invalid params|invalid param|-32602|validation|required property|command" not in text
    assert "devices approve --latest" not in text
    health_index = text.index('if ! wait_http_ok_any 90 "${gateway_health_url}"; then')
    preauth_call_index = text.index("preauthorize_openclaw_gateway_cli_scopes", health_index)
    runtime_env_index = text.index("write_runtime_env_var() {")
    assert health_index < preauth_call_index < runtime_env_index


def test_start_control_runtime_script_prepares_trade_worker_agent_config_before_gateway_boot() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "prepare_openclaw_trade_agent_config() {" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE" in text
    assert "const rawLlmIdleTimeoutSeconds = process.env.OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE;" in text
    assert "const configuredPrimaryModel = String(process.env.CLAW_TRADE_LLM_MODEL_VALUE" in text
    assert "function resolveProjectLlmConfig()" in text
    assert "function readExistingOpenClawConfig(outputPath)" in text
    assert "const existingConfig = readExistingOpenClawConfig(outputPath);" in text
    assert "DEEPSEEK_MODEL" in text
    assert "DEEPSEEK_API_KEY" in text
    assert "BB_MCP_SERVER_PATH" not in text
    assert "BB_MCP_CWD" not in text
    assert "const llmIdleTimeoutSeconds = Number.parseInt(String(rawLlmIdleTimeoutSeconds ?? \"\"), 10);" in text
    assert "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS 必须是正整数" in text
    assert "const workers = [" in text
    assert 'default: workerId === "market_analyst"' in text
    assert 'workspace: `${rootDir}/agents/${workerId}`' in text
    assert "function readWorkerMountedSkills(workerId) {" in text
    assert "skills/manifest.yaml" in text
    assert "worker skill manifest 不存在" in text
    assert "worker skill path 非法" in text
    assert "worker skill manifest 没有可挂载 skill" in text
    assert "skills: readWorkerMountedSkills(workerId)" in text
    assert "const mergedDefaults = {" in text
    assert "OpenClaw LLM 未配置：仅启动设置/诊断 UI" in text
    assert "OpenClaw LLM 使用已保存配置；.env.local 未覆盖。" in text
    assert ".env.local LLM provider 暂未接入 OpenClaw runtime 配置生成" in text
    assert ".env.local 缺少 DEEPSEEK_API_KEY" in text
    assert "const existingModelsConfig = isPlainObject(existingConfig.models) ? existingConfig.models : {};" in text
    assert "const existingProviders = isPlainObject(existingModelsConfig.providers) ? existingModelsConfig.providers : {};" in text
    assert "const existingDefaults = isPlainObject(existingAgentsConfig.defaults) ? existingAgentsConfig.defaults : {};" in text
    assert "const hasSavedLlmConfig = Object.keys(existingProviders).length > 0 || Boolean(existingDefaults.model);" in text
    assert "const mergedProviders = {" in text
    assert "...existingProviders," in text
    assert "[llm.providerId]: {" in text
    assert "apiKey: llm.apiKey" in text
    assert "if (llm) {" in text
    assert "primary: llm.model" in text
    assert "alias: llm.providerName" in text
    assert "timeoutSeconds: llmIdleTimeoutSeconds" in text
    assert "models: mergedModels," in text
    assert "openclaw_plugins/claw-trade-frontline-tools" in text
    assert "openclaw_plugins/claw-trade-selection-tools" in text
    assert "openclaw_plugins/claw-trade-scheduled-work-tools" in text
    assert "paths: [clawTradeFrontlinePluginPath, clawTradeSelectionPluginPath, clawTradeScheduledWorkPluginPath]" in text
    assert '"claw-trade-frontline-tools": {' in text
    assert '"claw-trade-selection-tools": {' in text
    assert '"claw-trade-scheduled-work-tools": {' in text
    assert "CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN=\"claw-trade-scheduled-${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}\"" in text
    assert "export OPENCLAW_GATEWAY_TOKEN CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN" in text
    assert 'write_runtime_env_var "CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN"' in text
    assert '"openclaw-weixin": {' in text
    assert "const existingPluginEntries = isPlainObject(existingPluginsConfig.entries) ? existingPluginsConfig.entries : {};" in text
    assert "const mergedPluginEntries = {" in text
    assert "...existingPluginEntries," in text
    assert "plugins: mergedPlugins," in text
    assert "const mergedChannels = {" in text
    assert "replyProgressMessages: true" in text
    assert "channels: mergedChannels," in text
    assert "...(isPlainObject(existingConfig.meta) ? { meta: existingConfig.meta } : {})," in text
    assert '"openclaw-weixin": {\n    enabled: true,\n  }' not in text
    assert "const mergedMcp = {" in text
    assert "mergedMcpServers.bb_crypto_data =" not in text
    assert "servers: {}" in text
    assert "mcp: mergedMcp," in text
    assert "const existingChannels = isPlainObject(existingConfig.channels) ? existingConfig.channels : {};" in text
    assert 'const existingWeixinChannelConfig = isPlainObject(existingChannels["openclaw-weixin"])' in text
    assert "const weixinChannelEnabled = existingWeixinChannelConfig.enabled === false ? false : true;" in text
    assert "  ...existingChannels," in text
    assert "    ...existingWeixinChannelConfig," in text
    assert "    enabled: weixinChannelEnabled," in text
    assert "idleTimeoutSeconds: llmIdleTimeoutSeconds" not in text
    assert "defaults.llm" not in text
    assert "skipBootstrap: true" in text
    assert "gateway: {" in text
    assert 'mode: "local"' in text
    assert 'bind: "loopback"' in text
    assert "OpenClaw source config" not in text
    workers = (
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "policy_analyst",
        "hot_money_tracker",
        "lockup_watcher",
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
        "report_polisher",
        "selection_strategist",
        "selection_skeptic",
        "selection_manager",
        "selection_portfolio_manager",
        "price_alert_scan_worker",
        "scheduled_report_runner",
        "market_data_maintenance_worker",
    )
    for worker in workers:
        assert f'"{worker}"' in text

    prepare_index = text.index("\nprepare_openclaw_trade_agent_config\n")
    weixin_call_index = text.index("\nensure_openclaw_weixin_plugin_ready\n")
    gateway_index = text.index("gateway_cmd=(")
    assert weixin_call_index < prepare_index < gateway_index


def test_start_control_runtime_script_allows_missing_llm_for_settings_ui_startup() -> None:
    text = _script_path().read_text(encoding="utf-8")

    missing_config_index = text.index("if (!selectedModel) {")
    no_llm_warning_index = text.index("OpenClaw LLM 未配置：仅启动设置/诊断 UI")
    saved_config_info_index = text.index("OpenClaw LLM 使用已保存配置；.env.local 未覆盖。")
    providers_index = text.index("const mergedProviders = {")
    defaults_index = text.index("const mergedDefaults = {")
    missing_config_block = text[missing_config_index : text.index("  }", missing_config_index)]
    assert "return null;" in missing_config_block
    assert "process.exit(1)" not in missing_config_block
    assert no_llm_warning_index < saved_config_info_index < providers_index < defaults_index
    assert "...existingProviders," in text
    assert "...existingDefaults," in text
    assert "openclaw_report_llm_configured() {" not in text
    assert "跳过 CLI scope 预授权" not in text
    assert "if ! openclaw_report_llm_configured; then" not in text
    assert "OpenClaw CLI scope 预授权完成。" in text


def test_start_control_runtime_script_uses_explicit_openviking_embedding_config_only() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "OPENVIKING_EMBEDDING_PROVIDER" in text
    assert "OPENVIKING_EMBEDDING_MODEL" in text
    assert "已配置 OPENVIKING_EMBEDDING_PROVIDER，但缺少 OPENVIKING_EMBEDDING_MODEL" in text
    assert "已配置 OPENVIKING_EMBEDDING_MODEL，但缺少 OPENVIKING_EMBEDDING_PROVIDER" in text
    assert "OpenViking embedding LLM 已配置，语义检索已启用。" in text
    assert "未配置 embedding LLM，OpenViking 只保存和读取材料，不启用语义检索。" in text
    assert 'provider: "openai"' in text
    assert 'model: "claw-trade-report-no-vectorization"' in text
    assert 'model: "text-embedding-v4"' not in text
    assert "resolveQwenEmbeddingApiBase" not in text
    assert "DeepSeek；DeepSeek 官方 API 没有同厂商 embedding 模型" not in text
    assert "当前不能把 chat 模型当 embedding 用" not in text
    assert 'model: llm.model' not in text
    assert 'provider: "litellm"' not in text
    assert "claw-trade-disabled-embedding" not in text
    assert "configure OpenViking embedding explicitly in .env.local" not in text
    assert "OpenViking source config" not in text
    assert "source.embedding" not in text
    assert "~/.openviking" not in text


def test_start_control_runtime_script_supports_qwen_chat_config_without_embedding_derivation() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "QWEN_API_KEY" in text
    assert "MODELSTUDIO_API_KEY" in text
    assert "DASHSCOPE_API_KEY" in text
    assert 'return "qwen/qwen3.5-plus";' in text
    assert 'providerId: "qwen"' in text
    assert 'model: `qwen/${providerModelId}`' in text
    assert 'model: "text-embedding-v4"' not in text


def test_start_control_runtime_script_loads_only_claw_trade_env_without_logging_secret_values() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "load_runtime_env_files_into_process_env() {" in text
    assert 'CLAW_TRADE_ENV_PATH="${CLAW_TRADE_ENV_PATH:-${ROOT_DIR}/.env.local}"' in text
    assert "const originalKeys = new Set(Object.keys(process.env));" in text
    assert "parseEnvFile(clawTradeEnvPath)" in text
    assert "process.stdout.write(\"\\u0000\")" in text
    assert "claw-trade .env.local 不存在，跳过注入" in text
    assert "OpenClaw source .env" not in text
    assert 'printf \'[INFO] 环境变量注入条目数：%s\\n\' "${exported_count}"' in text
    load_index = text.index("load_runtime_env_files_into_process_env")
    defaults_index = text.index('OPENVIKING_ENDPOINT="${OPENVIKING_ENDPOINT:-http://127.0.0.1:1933}"')
    gateway_index = text.index("gateway_cmd=(")
    assert load_index < defaults_index
    assert load_index < gateway_index


def test_start_control_runtime_script_loads_mongo_ui_settings_before_runtime_config() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "load_mongo_ui_settings_into_process_env() {" in text
    assert "uv run python -m claw_trade.runtime.settings_projection" in text
    assert 'local import_env_data_sources="${CLAW_TRADE_IMPORT_ENV_DATA_SOURCES:-}"' in text
    assert 'if [[ -z "${import_env_data_sources}" && ${#RUNTIME_COMMAND[@]} -gt 0 ]]; then' in text
    assert 'CLAW_TRADE_IMPORT_ENV_DATA_SOURCES="${import_env_data_sources}" uv run python -m claw_trade.runtime.settings_projection' in text
    assert "CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_VALUE" in text
    assert "resolveMongoReportModelConfig() || resolveProjectLlmConfig()" in text
    mongo_index = text.index("load_mongo_ui_settings_into_process_env")
    embedding_flags_index = text.index("configure_openviking_embedding_runtime_flags")
    openclaw_config_index = text.index("prepare_openclaw_trade_agent_config")
    assert mongo_index < embedding_flags_index
    assert mongo_index < openclaw_config_index


def test_start_control_runtime_script_no_dynamic_expression_execution_usage() -> None:
    text = _script_path().read_text(encoding="utf-8")
    assert ("ev" + "al") not in text


def test_start_control_runtime_script_does_not_write_secrets_or_remove_runs_root() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "OPENVIKING_API_KEY=" not in text
    assert "OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}" not in text
    assert 'write_runtime_env_var "OPENCLAW_GATEWAY_TOKEN"' not in text
    forbidden_plain = "rm -rf " + "runs"
    forbidden_quote = 'rm -rf "' + "runs"
    assert forbidden_plain not in text
    assert forbidden_quote not in text


def test_start_control_runtime_script_preserves_openviking_data_on_restart() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "保留 runs 主目录、OpenViking data 与 OpenClaw state" in text
    assert '! -path "${OPENVIKING_RUNTIME_DIR}"' in text
    assert '! -path "${OPENVIKING_DATA_DIR}"' in text


def test_start_control_runtime_script_preserves_openclaw_state_on_restart() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'OPENCLAW_DEFAULT_STATE_DIR="${RUNTIME_DIR}/openclaw-state"' in text
    runtime_cleanup_line = (
        'find "${RUNTIME_DIR}" -mindepth 1 -maxdepth 1 '
        '! -path "${OPENVIKING_RUNTIME_DIR}" ! -path "${OPENCLAW_STATE_DIR}" '
        '! -path "${OPENCLAW_DEFAULT_STATE_DIR}" ! -path "${RUNTIME_DIR}/data-gateway" -exec rm -rf {} +'
    )
    assert runtime_cleanup_line in text
    assert 'rm -rf "${OPENCLAW_STATE_DIR}"' not in text
    assert 'rm -rf ${OPENCLAW_STATE_DIR}' not in text


def test_start_control_runtime_cleanup_preserves_default_state_when_override_state_dir_is_external(
    tmp_path: Path,
) -> None:
    text = _script_path().read_text(encoding="utf-8")
    runtime_cleanup_line = next(
        line
        for line in text.splitlines()
        if line.startswith('find "${RUNTIME_DIR}" -mindepth 1 -maxdepth 1 ')
    )

    runtime_dir = tmp_path / "runtime"
    openviking_runtime_dir = runtime_dir / "openviking"
    openviking_data_dir = openviking_runtime_dir / "data"
    default_state_dir = runtime_dir / "openclaw-state"
    external_override_state_dir = tmp_path / "external-openclaw-state"

    (default_state_dir / "keep.txt").parent.mkdir(parents=True, exist_ok=True)
    (default_state_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (openviking_data_dir / "keep.txt").parent.mkdir(parents=True, exist_ok=True)
    (openviking_data_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (runtime_dir / "should-delete.txt").parent.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "should-delete.txt").write_text("delete", encoding="utf-8")
    external_override_state_dir.mkdir(parents=True, exist_ok=True)

    shell_script = f"""
set -euo pipefail
RUNTIME_DIR={shlex.quote(str(runtime_dir))}
OPENVIKING_RUNTIME_DIR={shlex.quote(str(openviking_runtime_dir))}
OPENCLAW_STATE_DIR={shlex.quote(str(external_override_state_dir))}
OPENCLAW_DEFAULT_STATE_DIR={shlex.quote(str(default_state_dir))}
{runtime_cleanup_line}
"""
    completed = subprocess.run(
        ["bash", "-c", shell_script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert (default_state_dir / "keep.txt").exists()
    assert (openviking_data_dir / "keep.txt").exists()
    assert not (runtime_dir / "should-delete.txt").exists()


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


def test_prepare_openclaw_trade_agent_config_preserves_existing_weixin_channel_fields(tmp_path: Path) -> None:
    text = _script_path().read_text(encoding="utf-8")
    start = text.index("prepare_openclaw_trade_agent_config() {")
    end = text.index("\n}\n\nprepare_openviking_runtime_config()", start) + 3
    function_text = text[start:end]

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "openclaw.json"
    config_path.write_text(
        """
{
  "channels": {
    "openclaw-weixin": {
      "enabled": false,
      "channelConfigUpdatedAt": "2026-05-24T10:54:00Z",
      "accounts": [{"id": "acc-1"}],
      "botAgent": "wechat-bot-a",
      "replyProgressMessages": false
    },
    "other-channel": {
      "foo": "bar"
    }
  },
  "meta": {
    "source": "existing"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    shell_script = f"""
set -euo pipefail
ROOT_DIR={shlex.quote(str(Path(__file__).resolve().parents[2]))}
OPENCLAW_CONFIG_PATH={shlex.quote(str(config_path))}
OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS=600
CLAW_TRADE_LLM_PROVIDER=
CLAW_TRADE_LLM_MODEL=
DEEPSEEK_BASE_URL=https://api.deepseek.com
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
{function_text}
prepare_openclaw_trade_agent_config
"""
    completed = subprocess.run(
        ["bash", "-c", shell_script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    rendered = config_path.read_text(encoding="utf-8")
    assert '"channelConfigUpdatedAt": "2026-05-24T10:54:00Z"' in rendered
    assert '"botAgent": "wechat-bot-a"' in rendered
    assert '"accounts": [' in rendered
    assert '"other-channel": {' in rendered
    assert '"enabled": false' in rendered
    assert '"replyProgressMessages": true' in rendered


def test_runtime_env_writer_is_shell_safe_for_chinese_spaces_and_empty_values(tmp_path: Path) -> None:
    text = _script_path().read_text(encoding="utf-8")
    start = text.index("write_runtime_env_var() {")
    end = text.index('\nrm -f "${RUNTIME_ENV_PATH}"', start)
    function_text = text[start:end]

    runtime_env_path = tmp_path / "runtime.env"
    shell_script = f"""
set -euo pipefail
RUNTIME_ENV_PATH={shlex.quote(str(runtime_env_path))}
{function_text}
write_runtime_env_var "CHINESE_WITH_SPACES" "中文 值 with space"
write_runtime_env_var "PATH_WITH_SPACE" "/tmp/路径 with space/file.txt"
write_runtime_env_var "APOSTROPHE_VALUE" "O'Reilly 中文"
write_runtime_env_var "EMPTY_VALUE" ""
set -euo pipefail
source "$RUNTIME_ENV_PATH"
[[ "$CHINESE_WITH_SPACES" == "中文 值 with space" ]]
[[ "$PATH_WITH_SPACE" == "/tmp/路径 with space/file.txt" ]]
[[ "$APOSTROPHE_VALUE" == "O'Reilly 中文" ]]
[[ -z "$EMPTY_VALUE" ]]
"""
    completed = subprocess.run(
        ["bash", "-c", shell_script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
