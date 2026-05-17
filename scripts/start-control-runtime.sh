#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAW_TRADE_ENV_PATH="${CLAW_TRADE_ENV_PATH:-${ROOT_DIR}/.env.local}"
OPENCLAW_SOURCE_ENV_PATH="${OPENCLAW_SOURCE_ENV_PATH:-${HOME}/.openclaw/.env}"
RUNTIME_COMMAND=()
if [[ $# -gt 0 ]]; then
  if [[ "${1}" != "--" ]]; then
    printf '[ERROR] 用法：%s [-- <test-command> ...]\n' "$0" >&2
    exit 2
  fi
  shift
  if [[ $# -eq 0 ]]; then
    printf '[ERROR] -- 后必须提供要运行的测试或 live 命令\n' >&2
    exit 2
  fi
  RUNTIME_COMMAND=("$@")
fi

load_runtime_env_files_into_process_env() {
  local exported_count=0
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    export "${key}=${value}"
    exported_count=$(( exported_count + 1 ))
  done < <(
    OPENCLAW_SOURCE_ENV_PATH_VALUE="${OPENCLAW_SOURCE_ENV_PATH}" \
    CLAW_TRADE_ENV_PATH_VALUE="${CLAW_TRADE_ENV_PATH}" \
    node <<'NODE'
const fs = require("node:fs");

function parseEnvFile(envPath) {
  const result = new Map();
  let content = "";
  try {
    content = fs.readFileSync(envPath, "utf8");
  } catch {
    return result;
  }
  const lines = content.split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const normalized = line.startsWith("export ") ? line.slice(7).trim() : line;
    const splitIndex = normalized.indexOf("=");
    if (splitIndex <= 0) {
      continue;
    }
    const key = normalized.slice(0, splitIndex).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      continue;
    }
    let value = normalized.slice(splitIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
      (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
    ) {
      value = value.slice(1, -1);
    }
    result.set(key, value);
  }
  return result;
}

const sourceEnvPath = process.env.OPENCLAW_SOURCE_ENV_PATH_VALUE;
const clawTradeEnvPath = process.env.CLAW_TRADE_ENV_PATH_VALUE;
const originalKeys = new Set(Object.keys(process.env));
const merged = new Map();

for (const [key, value] of parseEnvFile(sourceEnvPath)) {
  if (!originalKeys.has(key)) {
    merged.set(key, value);
  }
}
for (const [key, value] of parseEnvFile(clawTradeEnvPath)) {
  if (!originalKeys.has(key)) {
    merged.set(key, value);
  }
}

for (const [key, value] of merged) {
  process.stdout.write(key);
  process.stdout.write("\u0000");
  process.stdout.write(value);
  process.stdout.write("\u0000");
}
NODE
  )
  if [[ -f "${CLAW_TRADE_ENV_PATH}" ]]; then
    printf '[INFO] 已加载 claw-trade .env.local：%s\n' "${CLAW_TRADE_ENV_PATH}"
  else
    printf '[WARN] claw-trade .env.local 不存在，跳过注入：%s\n' "${CLAW_TRADE_ENV_PATH}" >&2
  fi
  if [[ -f "${OPENCLAW_SOURCE_ENV_PATH}" ]]; then
    printf '[INFO] 已加载 OpenClaw source .env：%s\n' "${OPENCLAW_SOURCE_ENV_PATH}"
  else
    printf '[WARN] OpenClaw source .env 不存在，跳过注入：%s\n' "${OPENCLAW_SOURCE_ENV_PATH}" >&2
  fi
  printf '[INFO] 环境变量注入条目数：%s\n' "${exported_count}"
}

load_runtime_env_files_into_process_env

RUNTIME_DIR="${ROOT_DIR}/.runtime/dev-services"
LOG_DIR="${RUNTIME_DIR}/logs"
PID_DIR="${RUNTIME_DIR}/pids"
RUNTIME_ENV_PATH="${RUNTIME_DIR}/runtime.env"
RUNS_PROBE_DIR="${ROOT_DIR}/runs/probe"
LOCAL_MONGODB_START_SCRIPT="${ROOT_DIR}/scripts/start-local-mongodb.sh"
LOCAL_MONGODB_PID_FILE="${ROOT_DIR}/.runtime/mongodb/run/mongod.pid"
UV_CACHE_DIR="${UV_CACHE_DIR:-${RUNTIME_DIR}/uv-cache}"
UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_CACHE_DIR UV_LINK_MODE
OPENVIKING_RUNTIME_DIR="${RUNTIME_DIR}/openviking"
OPENVIKING_SOURCE_CONFIG_PATH="${OPENVIKING_SOURCE_CONFIG_PATH:-${HOME}/.openviking/ov.conf}"
OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${OPENVIKING_RUNTIME_DIR}/ov.conf}"
OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-${OPENVIKING_RUNTIME_DIR}/data}"
OPENVIKING_WRITE_LOCK_PATH="${OPENVIKING_WRITE_LOCK_PATH:-${RUNTIME_DIR}/openviking-write.lock}"
export OPENVIKING_CONFIG_FILE

OPENVIKING_ENDPOINT="${OPENVIKING_ENDPOINT:-http://127.0.0.1:1933}"
OPENVIKING_BASE_URL="${OPENVIKING_BASE_URL:-${OPENVIKING_ENDPOINT}}"
OPENVIKING_MCP_HOST="${OPENVIKING_MCP_HOST:-127.0.0.1}"
OPENVIKING_MCP_PORT="${OPENVIKING_MCP_PORT:-1944}"
OPENVIKING_MCP_URL="${OPENVIKING_MCP_URL:-http://${OPENVIKING_MCP_HOST}:${OPENVIKING_MCP_PORT}/mcp}"
OPENVIKING_WORKSPACE="${OPENVIKING_WORKSPACE:-workflow}"
OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18789}"
OPENCLAW_GATEWAY_CALL_BIN="${OPENCLAW_GATEWAY_CALL_BIN:-${ROOT_DIR}/third_party/openclaw/openclaw.mjs}"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-${RUNTIME_DIR}/openclaw-state}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${OPENCLAW_STATE_DIR}/openclaw.json}"
OPENCLAW_SOURCE_CONFIG_PATH="${OPENCLAW_SOURCE_CONFIG_PATH:-${HOME}/.openclaw/openclaw.json}"
OPENCLAW_GATEWAY_TIMEOUT_MS="${OPENCLAW_GATEWAY_TIMEOUT_MS:-600000}"
OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS="${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS:-600}"
OPENCLAW_MARKET_TOOL_PYTHON="${OPENCLAW_MARKET_TOOL_PYTHON:-}"
CLAW_TRADE_OPENVIKING_PROBE_RUN_ID="${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID:-probe-$(date -u +%Y%m%d%H%M%S)-$RANDOM}"
CLAW_TRADE_OPENCLAW_RUNNER="${CLAW_TRADE_OPENCLAW_RUNNER:-claw_trade.runtime.openclaw_local_runner:create_default_runner}"
CLAW_TRADE_OPENVIKING_BACKEND="${CLAW_TRADE_OPENVIKING_BACKEND:-claw_trade.artifacts.openviking_backend_http:create_default_backend}"
CLAW_TRADE_OPENVIKING_SERVER_BIN="${CLAW_TRADE_OPENVIKING_SERVER_BIN:-}"
CLAW_TRADE_OPENVIKING_SERVER_CWD="${CLAW_TRADE_OPENVIKING_SERVER_CWD:-}"

OPENVIKING_SERVER_PORT=1933
OPENCLAW_GATEWAY_PORT=18789

declare -a STARTED_PIDS=()
declare -a STARTED_NAMES=()
local_mongodb_started=0

log_info() {
  printf '[INFO] %s\n' "$*"
}

log_warn() {
  printf '[WARN] %s\n' "$*" >&2
}

log_error() {
  printf '[ERROR] %s\n' "$*" >&2
}

kill_pid_if_alive() {
  local pid="$1"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  if ! [[ "${pid}" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do
      if ! kill -0 "${pid}" 2>/dev/null; then
        return 0
      fi
      sleep 0.1
    done
    kill -9 "${pid}" 2>/dev/null || true
  fi
}

kill_by_pid_file() {
  local pid_file="$1"
  if [[ ! -f "${pid_file}" ]]; then
    return 0
  fi
  local pid
  pid="$(tr -d '[:space:]' < "${pid_file}" || true)"
  kill_pid_if_alive "${pid}"
  rm -f "${pid_file}"
}

kill_port_listener() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp 2>/dev/null | awk -v p=":${port}" '
      $4 ~ p"$" {
        if (match($0, /pid=[0-9]+/)) {
          pid = substr($0, RSTART + 4, RLENGTH - 4)
          print pid
        }
      }
    ' || true)"
  fi
  if [[ -z "${pids}" ]]; then
    return 0
  fi
  while IFS= read -r pid; do
    kill_pid_if_alive "${pid}"
  done <<< "${pids}"
}

pid_file_alive() {
  local pid_file="$1"
  if [[ ! -f "${pid_file}" ]]; then
    return 1
  fi
  local pid
  pid="$(tr -d '[:space:]' < "${pid_file}" || true)"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

stop_openclaw_gateway_service() {
  if [[ ! -x "${OPENCLAW_GATEWAY_CALL_BIN}" ]]; then
    return 0
  fi
  env -u OPENCLAW_STATE_DIR -u OPENCLAW_CONFIG_PATH \
    "${OPENCLAW_GATEWAY_CALL_BIN}" gateway stop >/dev/null 2>&1 || true
}

should_start_local_mongodb() {
  case "${CN_A_MONGODB_URI:-}" in
    mongodb://127.0.0.1:*|mongodb://localhost:*|mongodb://[::1]:*)
      return 0
      ;;
  esac
  return 1
}

wait_mongodb_ok() {
  local timeout_sec="$1"
  local uri="${CN_A_MONGODB_URI:-}"
  if [[ -z "${uri}" ]]; then
    log_error "CN_A_MONGODB_URI 未设置，无法检查 MongoDB"
    return 1
  fi
  MONGODB_URI_VALUE="${uri}" MONGODB_TIMEOUT_SEC_VALUE="${timeout_sec}" \
    uv run python - <<'PY'
import os
import sys
import time

from pymongo import MongoClient
from pymongo.errors import PyMongoError

uri = os.environ["MONGODB_URI_VALUE"]
deadline = time.monotonic() + int(os.environ["MONGODB_TIMEOUT_SEC_VALUE"])
last_error = ""
while time.monotonic() < deadline:
    try:
        client = MongoClient(uri, connectTimeoutMS=500, serverSelectionTimeoutMS=500)
        client.admin.command("ping")
        client.close()
        sys.exit(0)
    except PyMongoError as exc:
        last_error = exc.__class__.__name__
    except Exception as exc:
        last_error = exc.__class__.__name__
    time.sleep(0.5)
print(f"MongoDB ping failed: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

start_local_mongodb_if_needed() {
  if ! should_start_local_mongodb; then
    log_info "CN_A_MONGODB_URI 不是本地 dev Mongo，跳过本地 MongoDB 启动"
    return 0
  fi
  if [[ ! -x "${LOCAL_MONGODB_START_SCRIPT}" ]]; then
    log_error "本地 MongoDB 启动脚本不可执行：${LOCAL_MONGODB_START_SCRIPT}"
    exit 1
  fi

  local was_alive=0
  if pid_file_alive "${LOCAL_MONGODB_PID_FILE}"; then
    was_alive=1
  fi

  log_info "启动 MongoDB（本地 dev runtime）"
  "${LOCAL_MONGODB_START_SCRIPT}" >/dev/null
  if ! wait_mongodb_ok 30; then
    log_error "MongoDB health 检查失败：无法 ping CN_A_MONGODB_URI"
    exit 1
  fi

  if [[ "${was_alive}" == "0" ]] && pid_file_alive "${LOCAL_MONGODB_PID_FILE}"; then
    local pid
    pid="$(tr -d '[:space:]' < "${LOCAL_MONGODB_PID_FILE}" || true)"
    STARTED_PIDS+=("${pid}")
    STARTED_NAMES+=("mongodb")
    local_mongodb_started=1
  fi
}

normalize_ws_health_url() {
  local ws_url="$1"
  local no_trailing="${ws_url%/}"
  if [[ "${no_trailing}" == ws://* ]]; then
    printf 'http://%s/health\n' "${no_trailing#ws://}"
    return 0
  fi
  if [[ "${no_trailing}" == wss://* ]]; then
    printf 'https://%s/health\n' "${no_trailing#wss://}"
    return 0
  fi
  log_error "OPENCLAW_GATEWAY_URL 非法：${ws_url}"
  return 1
}

wait_http_ok_any() {
  local timeout_sec="$1"
  shift
  local urls=("$@")
  local waited=0
  while (( waited < timeout_sec )); do
    for url in "${urls[@]}"; do
      local code
      code="$(curl -sS -o /dev/null -w '%{http_code}' "${url}" || true)"
      if [[ "${code}" == "200" || "${code}" == "204" ]]; then
        return 0
      fi
    done
    sleep 1
    waited=$(( waited + 1 ))
  done
  return 1
}

cleanup_started_services() {
  # 只停止本脚本拉起的进程，避免误伤用户自己在其他终端维护的服务。
  for idx in "${!STARTED_PIDS[@]}"; do
    kill_pid_if_alive "${STARTED_PIDS[$idx]}"
  done
  if [[ ${#STARTED_NAMES[@]} -gt 0 ]]; then
    log_info "已停止服务：${STARTED_NAMES[*]}"
  fi
}

export_runtime_env_for_child_commands() {
  export CLAW_TRADE_OPENCLAW_RUNNER
  export CLAW_TRADE_OPENVIKING_BACKEND
  export CLAW_TRADE_OPENVIKING_MCP_STARTED="${openviking_mcp_started}"
  export CLAW_TRADE_OPENVIKING_PROBE_RUN_ID
  export OPENCLAW_GATEWAY_CALL_BIN
  export OPENCLAW_GATEWAY_URL
  export OPENCLAW_STATE_DIR
  export OPENCLAW_CONFIG_PATH
  export OPENCLAW_GATEWAY_TIMEOUT_MS
  export OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS
  export OPENCLAW_MARKET_TOOL_PYTHON
  export OPENVIKING_ENDPOINT
  export OPENVIKING_BASE_URL
  export OPENVIKING_WORKSPACE
  export OPENVIKING_CONFIG_FILE
  export OPENVIKING_DATA_DIR
  export OPENVIKING_WRITE_LOCK_PATH
  if [[ "${openviking_mcp_started}" == "1" ]]; then
    export OPENVIKING_MCP_URL
  else
    unset OPENVIKING_MCP_URL
  fi
}

on_script_exit() {
  local status="$1"
  # 防止 trap 重入，确保只清理一次，并把原始退出码传回调用方。
  trap - EXIT INT TERM
  cleanup_started_services
  return "${status}"
}

on_signal() {
  local signal_name="$1"
  if [[ "${signal_name}" == "INT" ]]; then
    log_warn "收到 Ctrl+C，正在停止服务..."
    exit 130
  fi
  log_warn "收到 ${signal_name}，正在停止服务..."
  exit 143
}

supervise_started_services() {
  log_info "脚本将持续运行并监控服务状态；按 Ctrl-C 可停止全部服务。"
  while true; do
    for idx in "${!STARTED_PIDS[@]}"; do
      local pid="${STARTED_PIDS[$idx]}"
      if ! kill -0 "${pid}" 2>/dev/null; then
        log_error "服务异常退出：${STARTED_NAMES[$idx]}(pid=${pid})"
        return 1
      fi
    done
    sleep 1
  done
}

prepare_openclaw_trade_agent_config() {
  local config_dir
  config_dir="$(dirname "${OPENCLAW_CONFIG_PATH}")"
  mkdir -p "${config_dir}"

  ROOT_DIR_VALUE="${ROOT_DIR}" \
  OPENCLAW_CONFIG_PATH_VALUE="${OPENCLAW_CONFIG_PATH}" \
  OPENCLAW_SOURCE_CONFIG_PATH_VALUE="${OPENCLAW_SOURCE_CONFIG_PATH}" \
  OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE="${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS}" \
    node <<'NODE'
const fs = require("node:fs");
const rootDir = process.env.ROOT_DIR_VALUE;
const outputPath = process.env.OPENCLAW_CONFIG_PATH_VALUE;
const sourcePath = process.env.OPENCLAW_SOURCE_CONFIG_PATH_VALUE;
const rawLlmIdleTimeoutSeconds = process.env.OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE;
const bbMcpServerPath = (process.env.BB_MCP_SERVER_PATH || "/mnt/d/src/BB/mcp/crypto-data-mcp/dist/server.js").trim();
const bbMcpCwd = (process.env.BB_MCP_CWD || "/mnt/d/src/BB/mcp/crypto-data-mcp").trim();
const workers = [
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
];

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

if (!sourcePath || !fs.existsSync(sourcePath)) {
  console.error(`[ERROR] OpenClaw source config 不存在：${sourcePath || "<empty>"}`);
  process.exit(1);
}
let sourceConfig;
try {
  sourceConfig = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
} catch (error) {
  console.error(`[ERROR] OpenClaw source config 解析失败：${sourcePath}`);
  console.error(String(error));
  process.exit(1);
}
if (!isPlainObject(sourceConfig)) {
  console.error(`[ERROR] OpenClaw source config 根节点不是 object：${sourcePath}`);
  process.exit(1);
}
const llmIdleTimeoutSeconds = Number.parseInt(String(rawLlmIdleTimeoutSeconds ?? ""), 10);
if (!Number.isInteger(llmIdleTimeoutSeconds) || llmIdleTimeoutSeconds <= 0) {
  console.error(
    `[ERROR] OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS 必须是正整数，当前值：${rawLlmIdleTimeoutSeconds ?? "<empty>"}`,
  );
  process.exit(1);
}

const sourceAgents = isPlainObject(sourceConfig.agents) ? sourceConfig.agents : {};
const sourceAgentList = Array.isArray(sourceAgents.list) ? sourceAgents.list : [];
const sourceAgentById = new Map();
for (const entry of sourceAgentList) {
  if (!isPlainObject(entry) || typeof entry.id !== "string" || entry.id.trim() === "") {
    continue;
  }
  sourceAgentById.set(entry.id.trim(), entry);
}

function readWorkerMountedSkills(workerId) {
  const manifestPath = `${rootDir}/agents/${workerId}/skills/manifest.yaml`;
  let manifestText = "";
  try {
    manifestText = fs.readFileSync(manifestPath, "utf8");
  } catch (error) {
    console.error(`[ERROR] worker skill manifest 不存在：${manifestPath}`);
    console.error(String(error));
    process.exit(1);
  }
  const skills = [];
  const seen = new Set();
  for (const line of manifestText.split(/\r?\n/)) {
    const match = line.match(/^\s*-\s+path:\s+(.+?)\s*$/);
    if (!match) {
      continue;
    }
    const skillPath = match[1].trim().replace(/^["']|["']$/g, "");
    if (skillPath.startsWith("/") || skillPath.includes("..")) {
      console.error(`[ERROR] worker skill path 非法：${manifestPath} -> ${skillPath}`);
      process.exit(1);
    }
    const skillId = skillPath.replace(/\/SKILL\.md$/i, "").trim();
    if (skillId && !seen.has(skillId)) {
      seen.add(skillId);
      skills.push(skillId);
    }
  }
  if (skills.length === 0) {
    console.error(`[ERROR] worker skill manifest 没有可挂载 skill：${manifestPath}`);
    process.exit(1);
  }
  return skills;
}

function envRecordFromProcess(keys) {
  const env = {};
  for (const key of keys) {
    const value = typeof process.env[key] === "string" ? process.env[key].trim() : "";
    if (value) {
      env[key] = value;
    }
  }
  return env;
}

const mergedWorkers = workers.map((workerId) => {
  const sourceEntry = sourceAgentById.get(workerId);
  const merged = isPlainObject(sourceEntry) ? { ...sourceEntry } : {};
  merged.id = workerId;
  merged.default = workerId === "market_analyst";
  merged.workspace = `${rootDir}/agents/${workerId}`;
  merged.skills = readWorkerMountedSkills(workerId);
  return merged;
});

const sourceDefaults = isPlainObject(sourceAgents.defaults) ? sourceAgents.defaults : {};
const sourceModels = isPlainObject(sourceConfig.models) ? sourceConfig.models : {};
const sourceProviders = isPlainObject(sourceModels.providers) ? sourceModels.providers : {};
const sourceDefaultModel = isPlainObject(sourceDefaults.model) ? sourceDefaults.model : {};
const sourcePrimaryModel = typeof sourceDefaultModel.primary === "string" ? sourceDefaultModel.primary.trim() : "";
const clawTradePrimaryModel = "deepseek/deepseek-chat";
const selectedPrimaryModel = clawTradePrimaryModel;
const selectedProviderModelId = selectedPrimaryModel.includes("/")
  ? selectedPrimaryModel.split("/").slice(1).join("/").trim()
  : "";
let primaryProviderId = "";
if (selectedPrimaryModel.includes("/")) {
  primaryProviderId = selectedPrimaryModel.split("/")[0].trim();
} else if (selectedPrimaryModel.length > 0 && selectedPrimaryModel in sourceProviders) {
  primaryProviderId = selectedPrimaryModel;
}
if (!primaryProviderId) {
  const knownProviderIds = Object.keys(sourceProviders);
  if (knownProviderIds.length > 0) {
    primaryProviderId = knownProviderIds[0];
  }
}
if (!primaryProviderId) {
  console.error(
    `[ERROR] 无法解析 primary model provider：agents.defaults.model.primary=${sourcePrimaryModel || "<empty>"} selected=${selectedPrimaryModel || "<empty>"}`,
  );
  process.exit(1);
}
if (!isPlainObject(sourceProviders[primaryProviderId])) {
  console.error(`[ERROR] OpenClaw source config 缺少 DeepSeek provider：${primaryProviderId}`);
  process.exit(1);
}

const sourcePrimaryProvider = isPlainObject(sourceProviders[primaryProviderId])
  ? sourceProviders[primaryProviderId]
  : {};
const selectedProviderModels = Array.isArray(sourcePrimaryProvider.models)
  ? sourcePrimaryProvider.models.filter((entry) => {
      if (!isPlainObject(entry) || typeof entry.id !== "string") {
        return false;
      }
      return selectedProviderModelId && entry.id.trim() === selectedProviderModelId;
    })
  : null;
if (Array.isArray(sourcePrimaryProvider.models) && selectedProviderModels.length === 0) {
  console.error(
    `[ERROR] OpenClaw source provider ${primaryProviderId} 缺少 primary model：${selectedProviderModelId || selectedPrimaryModel}`,
  );
  process.exit(1);
}
// 当前 OpenClaw 超时入口在 provider 配置的 timeoutSeconds，不再写旧的 agent 默认 llm 字段。
const mergedPrimaryProvider = {
  ...sourcePrimaryProvider,
  timeoutSeconds: llmIdleTimeoutSeconds,
};
if (selectedProviderModels) {
  mergedPrimaryProvider.models = selectedProviderModels;
}
const mergedProviders = {
  [primaryProviderId]: mergedPrimaryProvider,
};
const mergedModels = {
  ...sourceModels,
  providers: mergedProviders,
};
const mergedDefaults = {
  ...sourceDefaults,
  model: {
    ...sourceDefaultModel,
    primary: selectedPrimaryModel,
  },
  models: {
    [selectedPrimaryModel]: {
      alias: "DeepSeek Chat",
    },
  },
  skipBootstrap: true,
};
const sourcePlugins = isPlainObject(sourceConfig.plugins) ? sourceConfig.plugins : {};
const clawTradeFrontlinePluginPath = `${rootDir}/openclaw_plugins/claw-trade-frontline-tools`;
const mergedPlugins = {
  ...sourcePlugins,
  enabled: sourcePlugins.enabled === false ? false : true,
  load: {
    paths: [clawTradeFrontlinePluginPath],
  },
  entries: {
    "claw-trade-frontline-tools": {
      enabled: true,
    },
  },
};
const sourceMcp = isPlainObject(sourceConfig.mcp) ? sourceConfig.mcp : {};
const sourceMcpServers = isPlainObject(sourceMcp.servers) ? sourceMcp.servers : {};
const bbMcpEnv = envRecordFromProcess([
  "COINGECKO_PRO_API_KEY",
  "COINGECKO_DEMO_API_KEY",
  "COINGLASS_API_KEY",
  "COINGLASS_API_BASE",
  "COINGLASS_API_HEADER_NAME",
  "GLASSNODE_API_KEY",
  "FRED_API_KEY",
  "TAVILY_API_KEY",
  "EXA_API_KEY",
  "DEFILLAMA_API_KEY",
  "CMC_API_KEY",
  "CRYPTOQUANT_API_KEY",
  "ETHERSCAN_API_KEY",
  "THEGRAPH_ACCESS_TOKEN",
  "BB_PROVIDER_TIMEOUT_MS",
  "BB_PROVIDER_RETRY_ATTEMPTS",
  "BB_PROVIDER_RETRY_DELAY_MS",
  "BB_COINGLASS_CONTEXT_BUDGET",
  "BB_COINGLASS_RATE_LIMIT_PER_MINUTE",
  "BB_COINGLASS_RATE_LIMIT_WINDOW_MS",
]);
const mergedMcp = {
  ...sourceMcp,
  servers: {
    ...sourceMcpServers,
    bb_crypto_data: {
      command: "node",
      args: [bbMcpServerPath],
      cwd: bbMcpCwd,
      connectionTimeoutMs: 30000,
      env: bbMcpEnv,
    },
  },
};

const mergedConfig = {
  ...sourceConfig,
  gateway: {
    mode: "local",
    bind: "loopback",
  },
  agents: {
    ...sourceAgents,
    defaults: mergedDefaults,
    list: mergedWorkers,
  },
  models: mergedModels,
  plugins: mergedPlugins,
  mcp: mergedMcp,
};

fs.mkdirSync(require("node:path").dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(mergedConfig, null, 2)}\n`, "utf8");
NODE
}

prepare_openviking_runtime_config() {
  mkdir -p "${OPENVIKING_RUNTIME_DIR}" "${OPENVIKING_DATA_DIR}"
  if [[ "${OPENVIKING_CONFIG_FILE}" != "${OPENVIKING_RUNTIME_DIR}/ov.conf" ]]; then
    log_info "使用显式 OpenViking config：${OPENVIKING_CONFIG_FILE}"
    return 0
  fi
  if [[ ! -f "${OPENVIKING_SOURCE_CONFIG_PATH}" ]]; then
    log_error "OpenViking source config 不存在：${OPENVIKING_SOURCE_CONFIG_PATH}"
    exit 1
  fi

  OPENVIKING_SOURCE_CONFIG_PATH_VALUE="${OPENVIKING_SOURCE_CONFIG_PATH}" \
  OPENCLAW_CONFIG_PATH_VALUE="${OPENCLAW_CONFIG_PATH}" \
  OPENVIKING_CONFIG_FILE_VALUE="${OPENVIKING_CONFIG_FILE}" \
  OPENVIKING_DATA_DIR_VALUE="${OPENVIKING_DATA_DIR}" \
  OPENVIKING_SERVER_PORT_VALUE="${OPENVIKING_SERVER_PORT}" \
    node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

const sourcePath = process.env.OPENVIKING_SOURCE_CONFIG_PATH_VALUE;
const openClawConfigPath = process.env.OPENCLAW_CONFIG_PATH_VALUE;
const outputPath = process.env.OPENVIKING_CONFIG_FILE_VALUE;
const dataDir = process.env.OPENVIKING_DATA_DIR_VALUE;
const port = Number.parseInt(process.env.OPENVIKING_SERVER_PORT_VALUE || "1933", 10);

let source;
try {
  source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
} catch (error) {
  console.error(`[ERROR] OpenViking source config 解析失败：${sourcePath}`);
  console.error(String(error));
  process.exit(1);
}
if (!source || typeof source !== "object" || Array.isArray(source)) {
  console.error(`[ERROR] OpenViking source config 根节点不是 object：${sourcePath}`);
  process.exit(1);
}

let openClawConfig;
try {
  openClawConfig = JSON.parse(fs.readFileSync(openClawConfigPath, "utf8"));
} catch (error) {
  console.error(`[ERROR] OpenClaw runtime config 解析失败：${openClawConfigPath}`);
  console.error(String(error));
  process.exit(1);
}
if (!openClawConfig || typeof openClawConfig !== "object" || Array.isArray(openClawConfig)) {
  console.error(`[ERROR] OpenClaw runtime config 根节点不是 object：${openClawConfigPath}`);
  process.exit(1);
}

const openClawAgents = openClawConfig.agents && typeof openClawConfig.agents === "object" && !Array.isArray(openClawConfig.agents)
  ? openClawConfig.agents
  : {};
const openClawDefaults = openClawAgents.defaults && typeof openClawAgents.defaults === "object" && !Array.isArray(openClawAgents.defaults)
  ? openClawAgents.defaults
  : {};
const openClawDefaultModel = openClawDefaults.model && typeof openClawDefaults.model === "object" && !Array.isArray(openClawDefaults.model)
  ? openClawDefaults.model
  : {};
const primaryModel = typeof openClawDefaultModel.primary === "string" ? openClawDefaultModel.primary.trim() : "";
if (!primaryModel) {
  console.error(`[ERROR] OpenClaw runtime config 缺少 primary model：${openClawConfigPath}`);
  process.exit(1);
}

const next = {
  ...source,
  server: {
    ...(source.server && typeof source.server === "object" && !Array.isArray(source.server)
      ? source.server
      : {}),
    host: "127.0.0.1",
    port,
  },
  storage: {
    ...(source.storage && typeof source.storage === "object" && !Array.isArray(source.storage)
      ? source.storage
      : {}),
    workspace: dataDir,
  },
  embedding: {
    dense: {
      provider: "litellm",
      model: primaryModel,
      dimension: 2048,
    },
    max_concurrent: 1,
    max_retries: 0,
  },
};

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.mkdirSync(dataDir, { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
NODE
}

trap 'on_script_exit $?' EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

mkdir -p "${RUNTIME_DIR}" "${LOG_DIR}" "${PID_DIR}"
mkdir -p "${UV_CACHE_DIR}"
mkdir -p "${OPENVIKING_RUNTIME_DIR}" "${OPENVIKING_DATA_DIR}"
mkdir -p "${OPENCLAW_STATE_DIR}"

log_info "删除旧 runtime.env，避免复验读取到过期环境"
rm -f "${RUNTIME_ENV_PATH}"

log_info "清理旧 PID 与端口监听"
stop_openclaw_gateway_service
kill_by_pid_file "${PID_DIR}/openviking-server.pid"
kill_by_pid_file "${PID_DIR}/openviking-mcp-sidecar.pid"
kill_by_pid_file "${PID_DIR}/openclaw-gateway.pid"
kill_port_listener "${OPENVIKING_SERVER_PORT}"
kill_port_listener "${OPENVIKING_MCP_PORT}"
kill_port_listener "${OPENCLAW_GATEWAY_PORT}"
stop_openclaw_gateway_service

log_info "清理本地运行时审计目录（保留 runs 主目录）"
find "${RUNTIME_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
mkdir -p "${LOG_DIR}" "${PID_DIR}"
mkdir -p "${OPENCLAW_STATE_DIR}"
mkdir -p "${RUNS_PROBE_DIR}"
find "${RUNS_PROBE_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
prepare_openclaw_trade_agent_config
prepare_openviking_runtime_config
start_local_mongodb_if_needed

if [[ ! -x "${OPENCLAW_GATEWAY_CALL_BIN}" ]]; then
  log_error "OPENCLAW_GATEWAY_CALL_BIN 不可执行：${OPENCLAW_GATEWAY_CALL_BIN}"
  exit 1
fi

OPENVIKING_SERVER_LOG="${LOG_DIR}/openviking-server.log"
OPENVIKING_MCP_LOG="${LOG_DIR}/openviking-mcp-sidecar.log"
OPENCLAW_GATEWAY_LOG="${LOG_DIR}/openclaw-gateway.log"

log_info "启动 OpenViking server（${OPENVIKING_ENDPOINT}）"
if [[ -n "${CLAW_TRADE_OPENVIKING_SERVER_BIN}" ]]; then
  selected_server_cwd="${CLAW_TRADE_OPENVIKING_SERVER_CWD:-${ROOT_DIR}}"
  if [[ ! -d "${selected_server_cwd}" ]]; then
    log_error "CLAW_TRADE_OPENVIKING_SERVER_CWD 不存在：${selected_server_cwd}"
    exit 1
  fi

  selected_server_bin="${CLAW_TRADE_OPENVIKING_SERVER_BIN}"
  if [[ "${selected_server_bin}" == */* ]]; then
    if [[ "${selected_server_bin}" != /* ]]; then
      selected_server_bin="${selected_server_cwd}/${selected_server_bin}"
    fi
    if [[ ! -x "${selected_server_bin}" ]]; then
      log_error "CLAW_TRADE_OPENVIKING_SERVER_BIN 不可执行：${selected_server_bin}"
      exit 1
    fi
  else
    selected_server_bin_in_path="$(command -v "${selected_server_bin}" 2>/dev/null || true)"
    if [[ -z "${selected_server_bin_in_path}" || ! -x "${selected_server_bin_in_path}" ]]; then
      log_error "CLAW_TRADE_OPENVIKING_SERVER_BIN 不可执行或不存在：${selected_server_bin}"
      exit 1
    fi
    selected_server_bin="${selected_server_bin_in_path}"
  fi

  (
    cd "${selected_server_cwd}"
    "${selected_server_bin}" --config "${OPENVIKING_CONFIG_FILE}" --host 127.0.0.1 --port "${OPENVIKING_SERVER_PORT}" > "${OPENVIKING_SERVER_LOG}" 2>&1
  ) &
else
  # 默认使用本仓 uv 环境里锁定的 OpenViking wheel，避免 third_party 源码现场构建。
  (
    cd "${ROOT_DIR}"
    uv run python -m claw_trade.runtime.openviking_report_server --config "${OPENVIKING_CONFIG_FILE}" --host 127.0.0.1 --port "${OPENVIKING_SERVER_PORT}" > "${OPENVIKING_SERVER_LOG}" 2>&1
  ) &
fi
OPENVIKING_SERVER_PID=$!
STARTED_PIDS+=("${OPENVIKING_SERVER_PID}")
STARTED_NAMES+=("openviking-server")
printf '%s\n' "${OPENVIKING_SERVER_PID}" > "${PID_DIR}/openviking-server.pid"

if ! wait_http_ok_any 90 "${OPENVIKING_BASE_URL%/}/health" "${OPENVIKING_BASE_URL%/}/healthz"; then
  log_error "OpenViking health 检查失败：${OPENVIKING_BASE_URL%/}/health 或 /healthz 未就绪"
  exit 1
fi

resolved_mcp_module="${CLAW_TRADE_OPENVIKING_MCP_MODULE:-}"

openviking_mcp_started=0
if [[ -z "${resolved_mcp_module}" ]]; then
  # 默认不启 sidecar；正式 OpenViking 写读工具由 OpenClaw adapter 负责。
  log_info "未显式配置，跳过 MCP sidecar（CLAW_TRADE_OPENVIKING_MCP_MODULE 未设置）"
else
  mcp_cwd="${CLAW_TRADE_OPENVIKING_MCP_CWD:-${ROOT_DIR}}"
  if [[ ! -d "${mcp_cwd}" ]]; then
    log_error "CLAW_TRADE_OPENVIKING_MCP_CWD 不存在：${mcp_cwd}"
    exit 1
  fi

  log_info "启动 OpenViking MCP sidecar（module=${resolved_mcp_module}, url=${OPENVIKING_MCP_URL}）"
  opv_api_key="${OPV_API_KEY:-${OPENVIKING_API_KEY:-}}"
  (
    cd "${mcp_cwd}"
    OPENVIKING_ENDPOINT="${OPENVIKING_ENDPOINT}" \
    OPENVIKING_BASE_URL="${OPENVIKING_BASE_URL}" \
    OPV_ENDPOINT="${OPENVIKING_ENDPOINT}" \
    OPV_API_KEY="${opv_api_key}" \
    OPENVIKING_MCP_URL="${OPENVIKING_MCP_URL}" \
    uv run python -m "${resolved_mcp_module}" \
      --transport streamable-http \
      --host "${OPENVIKING_MCP_HOST}" \
      --port "${OPENVIKING_MCP_PORT}" > "${OPENVIKING_MCP_LOG}" 2>&1
  ) &
  OPENVIKING_MCP_PID=$!
  STARTED_PIDS+=("${OPENVIKING_MCP_PID}")
  STARTED_NAMES+=("openviking-mcp-sidecar")
  printf '%s\n' "${OPENVIKING_MCP_PID}" > "${PID_DIR}/openviking-mcp-sidecar.pid"

  mcp_url_no_slash="${OPENVIKING_MCP_URL%/}"
  mcp_origin="${mcp_url_no_slash}"
  if [[ "${mcp_url_no_slash}" == */mcp ]]; then
    mcp_origin="${mcp_url_no_slash%/mcp}"
  fi
  if ! wait_http_ok_any 90 "${mcp_origin}/healthz"; then
    log_error "OpenViking MCP healthz 检查失败：${mcp_origin}/healthz 未就绪"
    exit 1
  fi
  openviking_mcp_started=1
fi

# market skill 的 Python 依赖安装在项目虚拟环境中，OpenClaw 子进程必须用同一解释器。
if [[ -z "${OPENCLAW_MARKET_TOOL_PYTHON}" && -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  OPENCLAW_MARKET_TOOL_PYTHON="${ROOT_DIR}/.venv/bin/python"
fi

gateway_cmd=(
  "${OPENCLAW_GATEWAY_CALL_BIN}"
  gateway
  run
  --dev
  --port
  "${OPENCLAW_GATEWAY_PORT}"
  --force
  --bind
  loopback
)
if [[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  gateway_cmd+=(--auth token --token "${OPENCLAW_GATEWAY_TOKEN}")
else
  # 无 token 时明确使用仅本机可访问模式，保证启动策略可解释且不向外网暴露。
  gateway_cmd+=(--auth none)
  log_warn "未设置 OPENCLAW_GATEWAY_TOKEN：将以 loopback + auth none 启动，仅用于本机开发环境。"
fi

log_info "启动 OpenClaw gateway（${OPENCLAW_GATEWAY_URL}）"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
OPENCLAW_MARKET_TOOL_PYTHON="${OPENCLAW_MARKET_TOOL_PYTHON}" \
OPENVIKING_ENDPOINT="${OPENVIKING_ENDPOINT}" \
OPENVIKING_BASE_URL="${OPENVIKING_BASE_URL}" \
OPENVIKING_WRITE_LOCK_PATH="${OPENVIKING_WRITE_LOCK_PATH}" \
CLAW_TRADE_OPENVIKING_PROBE_RUN_ID="${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}" \
  "${gateway_cmd[@]}" > "${OPENCLAW_GATEWAY_LOG}" 2>&1 &
OPENCLAW_GATEWAY_PID=$!
STARTED_PIDS+=("${OPENCLAW_GATEWAY_PID}")
STARTED_NAMES+=("openclaw-gateway")
printf '%s\n' "${OPENCLAW_GATEWAY_PID}" > "${PID_DIR}/openclaw-gateway.pid"

gateway_health_url="$(normalize_ws_health_url "${OPENCLAW_GATEWAY_URL}")"
if ! wait_http_ok_any 90 "${gateway_health_url}"; then
  log_error "OpenClaw gateway health 检查失败：${gateway_health_url} 未就绪"
  exit 1
fi

cat > "${RUNTIME_ENV_PATH}" <<EOF
CLAW_TRADE_OPENCLAW_RUNNER=${CLAW_TRADE_OPENCLAW_RUNNER}
CLAW_TRADE_OPENVIKING_BACKEND=${CLAW_TRADE_OPENVIKING_BACKEND}
CLAW_TRADE_OPENVIKING_MCP_STARTED=${openviking_mcp_started}
CLAW_TRADE_OPENVIKING_PROBE_RUN_ID=${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}
OPENCLAW_GATEWAY_CALL_BIN=${OPENCLAW_GATEWAY_CALL_BIN}
OPENCLAW_GATEWAY_URL=${OPENCLAW_GATEWAY_URL}
OPENCLAW_STATE_DIR=${OPENCLAW_STATE_DIR}
OPENCLAW_CONFIG_PATH=${OPENCLAW_CONFIG_PATH}
OPENCLAW_GATEWAY_TIMEOUT_MS=${OPENCLAW_GATEWAY_TIMEOUT_MS}
OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS=${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS}
OPENCLAW_MARKET_TOOL_PYTHON=${OPENCLAW_MARKET_TOOL_PYTHON}
UV_CACHE_DIR=${UV_CACHE_DIR}
UV_LINK_MODE=${UV_LINK_MODE}
OPENVIKING_ENDPOINT=${OPENVIKING_ENDPOINT}
OPENVIKING_BASE_URL=${OPENVIKING_BASE_URL}
OPENVIKING_WORKSPACE=${OPENVIKING_WORKSPACE}
OPENVIKING_CONFIG_FILE=${OPENVIKING_CONFIG_FILE}
OPENVIKING_DATA_DIR=${OPENVIKING_DATA_DIR}
OPENVIKING_WRITE_LOCK_PATH=${OPENVIKING_WRITE_LOCK_PATH}
CLAW_TRADE_LOCAL_MONGODB_STARTED=${local_mongodb_started}
EOF
if [[ "${openviking_mcp_started}" == "1" ]]; then
  printf 'OPENVIKING_MCP_URL=%s\n' "${OPENVIKING_MCP_URL}" >> "${RUNTIME_ENV_PATH}"
fi

log_info "服务已就绪"
log_info "PID 文件目录：${PID_DIR}"
log_info "日志目录：${LOG_DIR}"
log_info "运行时环境文件：${RUNTIME_ENV_PATH}"

export_runtime_env_for_child_commands
if [[ ${#RUNTIME_COMMAND[@]} -gt 0 ]]; then
  log_info "运行测试命令：${RUNTIME_COMMAND[*]}"
  set +e
  "${RUNTIME_COMMAND[@]}"
  command_status=$?
  set -e
  if [[ "${command_status}" == "0" ]]; then
    log_info "测试命令完成：退出码 0"
  else
    log_error "测试命令失败：退出码 ${command_status}"
  fi
  exit "${command_status}"
fi

supervise_started_services
