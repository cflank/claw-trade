#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAW_TRADE_ENV_PATH="${CLAW_TRADE_ENV_PATH:-${ROOT_DIR}/.env.local}"
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

const clawTradeEnvPath = process.env.CLAW_TRADE_ENV_PATH_VALUE;
const originalKeys = new Set(Object.keys(process.env));
const merged = new Map();

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
  printf '[INFO] 环境变量注入条目数：%s\n' "${exported_count}"
}

load_runtime_env_files_into_process_env

load_mongo_ui_settings_into_process_env() {
  local exported_count=0
  local import_env_data_sources="${CLAW_TRADE_IMPORT_ENV_DATA_SOURCES:-}"
  if [[ -z "${import_env_data_sources}" && ${#RUNTIME_COMMAND[@]} -gt 0 ]]; then
    import_env_data_sources=1
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    export "${key}=${value}"
    exported_count=$(( exported_count + 1 ))
  done < <(
    cd "${ROOT_DIR}"
    CLAW_TRADE_IMPORT_ENV_DATA_SOURCES="${import_env_data_sources}" uv run python -m claw_trade.runtime.settings_projection
  )
  printf '[INFO] 已加载 Mongo UI 设置：%s 项\n' "${exported_count}"
}

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
OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${OPENVIKING_RUNTIME_DIR}/ov.conf}"
OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-${OPENVIKING_RUNTIME_DIR}/data}"
OPENVIKING_WRITE_LOCK_PATH="${OPENVIKING_WRITE_LOCK_PATH:-${RUNTIME_DIR}/openviking-write.lock}"
export OPENVIKING_CONFIG_FILE
CN_A_MONGODB_BIND_IP="${CN_A_MONGODB_BIND_IP:-127.0.0.1}"
CN_A_MONGODB_PORT="${CN_A_MONGODB_PORT:-27017}"
CN_A_MONGODB_URI="${CN_A_MONGODB_URI:-mongodb://${CN_A_MONGODB_BIND_IP}:${CN_A_MONGODB_PORT}}"
CN_A_MONGODB_DATABASE="${CN_A_MONGODB_DATABASE:-claw_trade}"
CN_A_MONGODB_CACHE_COLLECTION="${CN_A_MONGODB_CACHE_COLLECTION:-cn_a_fundamental_cache}"
DATA_GATEWAY_MONGODB_URI="${DATA_GATEWAY_MONGODB_URI:-${CN_A_MONGODB_URI}}"
DATA_GATEWAY_MONGODB_DATABASE="${DATA_GATEWAY_MONGODB_DATABASE:-${CN_A_MONGODB_DATABASE}}"
DATA_GATEWAY_SEED_MONGODB_URI="${DATA_GATEWAY_SEED_MONGODB_URI:-${CN_A_MONGODB_URI}}"
DATA_GATEWAY_SEED_MONGODB_DATABASE="${DATA_GATEWAY_SEED_MONGODB_DATABASE:-claw_trade_a_share_factory_seed}"
DATA_GATEWAY_COLUMNAR_ROOT="${DATA_GATEWAY_COLUMNAR_ROOT:-}"
export CN_A_MONGODB_URI CN_A_MONGODB_DATABASE CN_A_MONGODB_CACHE_COLLECTION
export DATA_GATEWAY_MONGODB_URI DATA_GATEWAY_MONGODB_DATABASE
export DATA_GATEWAY_SEED_MONGODB_URI DATA_GATEWAY_SEED_MONGODB_DATABASE DATA_GATEWAY_COLUMNAR_ROOT

OPENVIKING_ENDPOINT="${OPENVIKING_ENDPOINT:-http://127.0.0.1:1933}"
OPENVIKING_BASE_URL="${OPENVIKING_BASE_URL:-${OPENVIKING_ENDPOINT}}"
OPENVIKING_MCP_HOST="${OPENVIKING_MCP_HOST:-127.0.0.1}"
OPENVIKING_MCP_PORT="${OPENVIKING_MCP_PORT:-1944}"
OPENVIKING_MCP_URL="${OPENVIKING_MCP_URL:-http://${OPENVIKING_MCP_HOST}:${OPENVIKING_MCP_PORT}/mcp}"
OPENVIKING_WORKSPACE="${OPENVIKING_WORKSPACE:-workflow}"
OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18789}"
OPENCLAW_GATEWAY_CALL_BIN="${OPENCLAW_GATEWAY_CALL_BIN:-${ROOT_DIR}/third_party/openclaw/openclaw.mjs}"
OPENCLAW_PACKAGE_DIR="${OPENCLAW_PACKAGE_DIR:-${ROOT_DIR}/third_party/openclaw}"
OPENCLAW_CONTROL_UI_INDEX="${OPENCLAW_CONTROL_UI_INDEX:-${OPENCLAW_PACKAGE_DIR}/dist/control-ui/index.html}"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-${RUNTIME_DIR}/openclaw-state}"
OPENCLAW_DEFAULT_STATE_DIR="${RUNTIME_DIR}/openclaw-state"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${OPENCLAW_STATE_DIR}/openclaw.json}"
OPENCLAW_WEIXIN_PLUGIN_ID="${OPENCLAW_WEIXIN_PLUGIN_ID:-openclaw-weixin}"
OPENCLAW_WEIXIN_PLUGIN_SPEC="${OPENCLAW_WEIXIN_PLUGIN_SPEC:-@tencent-weixin/openclaw-weixin@2.4.4}"
OPENCLAW_GATEWAY_TIMEOUT_MS="${OPENCLAW_GATEWAY_TIMEOUT_MS:-600000}"
OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS="${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS:-600}"
OPENCLAW_MARKET_TOOL_PYTHON="${OPENCLAW_MARKET_TOOL_PYTHON:-}"
CLAW_TRADE_LLM_PROVIDER="${CLAW_TRADE_LLM_PROVIDER:-}"
CLAW_TRADE_LLM_MODEL="${CLAW_TRADE_LLM_MODEL:-${DEEPSEEK_MODEL:-}}"
DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
QWEN_BASE_URL="${QWEN_BASE_URL:-${DASHSCOPE_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}}"
OPENVIKING_EMBEDDING_PROVIDER="${OPENVIKING_EMBEDDING_PROVIDER:-${CLAW_TRADE_OPENVIKING_EMBEDDING_PROVIDER:-}}"
OPENVIKING_EMBEDDING_MODEL="${OPENVIKING_EMBEDDING_MODEL:-${CLAW_TRADE_OPENVIKING_EMBEDDING_MODEL:-}}"
OPENVIKING_EMBEDDING_API_KEY="${OPENVIKING_EMBEDDING_API_KEY:-${CLAW_TRADE_OPENVIKING_EMBEDDING_API_KEY:-}}"
OPENVIKING_EMBEDDING_API_BASE="${OPENVIKING_EMBEDDING_API_BASE:-${CLAW_TRADE_OPENVIKING_EMBEDDING_API_BASE:-}}"
OPENVIKING_EMBEDDING_DIMENSION="${OPENVIKING_EMBEDDING_DIMENSION:-${CLAW_TRADE_OPENVIKING_EMBEDDING_DIMENSION:-}}"
OPENVIKING_EMBEDDING_QUERY_PARAM="${OPENVIKING_EMBEDDING_QUERY_PARAM:-${CLAW_TRADE_OPENVIKING_EMBEDDING_QUERY_PARAM:-}}"
OPENVIKING_EMBEDDING_DOCUMENT_PARAM="${OPENVIKING_EMBEDDING_DOCUMENT_PARAM:-${CLAW_TRADE_OPENVIKING_EMBEDDING_DOCUMENT_PARAM:-}}"
CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED="${CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED:-0}"
CLAW_TRADE_OPENVIKING_VECTORIZE="${CLAW_TRADE_OPENVIKING_VECTORIZE:-0}"
CLAW_TRADE_OPENVIKING_VECTORIZE_REASON="${CLAW_TRADE_OPENVIKING_VECTORIZE_REASON:-}"
export CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED CLAW_TRADE_OPENVIKING_VECTORIZE CLAW_TRADE_OPENVIKING_VECTORIZE_REASON
CLAW_TRADE_UI_INBOUND_URL="${CLAW_TRADE_UI_INBOUND_URL:-}"
CLAW_TRADE_UI_INBOUND_TIMEOUT_MS="${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS:-60000}"
CLAW_TRADE_OPENVIKING_PROBE_RUN_ID="${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID:-probe-$(date -u +%Y%m%d%H%M%S)-$RANDOM}"
if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  OPENCLAW_GATEWAY_TOKEN="claw-trade-dev-${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}"
fi
export OPENCLAW_GATEWAY_TOKEN
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

guard_factory_columnar_root() {
  local root="${DATA_GATEWAY_COLUMNAR_ROOT:-}"
  if [[ -z "${root}" ]]; then
    return 0
  fi
  case "${root}" in
    data/crypto-history-full/*|"${ROOT_DIR}/data/crypto-history-full"/*)
      if [[ "${CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE:-0}" != "1" ]]; then
        log_error "DATA_GATEWAY_COLUMNAR_ROOT 指向项目出厂数据目录：${root}"
        log_error "运行时 provider 增量会写入该目录，已阻止。请留空使用 runtime 默认写入根，或仅在维护导入时显式设置 CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE=1。"
        exit 1
      fi
      ;;
  esac
}

guard_factory_mongo_database() {
  local database="${DATA_GATEWAY_MONGODB_DATABASE:-}"
  case "${database}" in
    claw_trade_crypto_history_*)
      if [[ "${CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE:-0}" != "1" ]]; then
        log_error "DATA_GATEWAY_MONGODB_DATABASE 指向加密出厂 seed 库：${database}"
        log_error "运行时证据会写入该 Mongo 库，已阻止。请把 DATA_GATEWAY_MONGODB_DATABASE 设为运行时写库，并用 DATA_GATEWAY_SEED_MONGODB_DATABASE 只读挂载 seed 库。"
        exit 1
      fi
      ;;
  esac
}

configure_openviking_embedding_runtime_flags() {
  if [[ -n "${OPENVIKING_EMBEDDING_PROVIDER}" && -z "${OPENVIKING_EMBEDDING_MODEL}" ]]; then
    log_error "已配置 OPENVIKING_EMBEDDING_PROVIDER，但缺少 OPENVIKING_EMBEDDING_MODEL。"
    exit 1
  fi
  if [[ -z "${OPENVIKING_EMBEDDING_PROVIDER}" && -n "${OPENVIKING_EMBEDDING_MODEL}" ]]; then
    log_error "已配置 OPENVIKING_EMBEDDING_MODEL，但缺少 OPENVIKING_EMBEDDING_PROVIDER。"
    exit 1
  fi

  if [[ -n "${OPENVIKING_EMBEDDING_PROVIDER}" && -n "${OPENVIKING_EMBEDDING_MODEL}" ]]; then
    CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED=1
    CLAW_TRADE_OPENVIKING_VECTORIZE=1
    CLAW_TRADE_OPENVIKING_VECTORIZE_REASON="OpenViking embedding LLM 已配置，语义检索已启用。"
  else
    CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED=0
    CLAW_TRADE_OPENVIKING_VECTORIZE=0
    CLAW_TRADE_OPENVIKING_VECTORIZE_REASON="未配置 embedding LLM，OpenViking 只保存和读取材料，不启用语义检索。"
  fi
  export CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED CLAW_TRADE_OPENVIKING_VECTORIZE CLAW_TRADE_OPENVIKING_VECTORIZE_REASON
  log_info "${CLAW_TRADE_OPENVIKING_VECTORIZE_REASON}"
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

prune_missing_runtime_columnar_manifests() {
  local runtime_columnar_root="${RUNTIME_DIR}/data-gateway/normalized"
  local result
  if ! result="$(
    RUNTIME_COLUMNAR_ROOT_VALUE="${runtime_columnar_root}" \
    ROOT_DIR_VALUE="${ROOT_DIR}" \
    DATA_GATEWAY_MONGODB_URI_VALUE="${DATA_GATEWAY_MONGODB_URI}" \
    DATA_GATEWAY_MONGODB_DATABASE_VALUE="${DATA_GATEWAY_MONGODB_DATABASE}" \
      uv run python - <<'PY'
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from pymongo import MongoClient

root_dir = Path(os.environ["ROOT_DIR_VALUE"]).resolve()
runtime_root = Path(os.environ["RUNTIME_COLUMNAR_ROOT_VALUE"]).resolve()
mongo_uri = os.environ["DATA_GATEWAY_MONGODB_URI_VALUE"]
database_name = os.environ.get("DATA_GATEWAY_MONGODB_DATABASE_VALUE", "").strip()
if not database_name:
    database_name = urlparse(mongo_uri).path.strip("/").split("/", 1)[0]
if not database_name:
    raise RuntimeError("DATA_GATEWAY_MONGODB_DATABASE is required")


def candidate_paths(manifest):
    paths = []
    raw_path = str(manifest.get("path") or "").strip()
    if raw_path:
        path = Path(raw_path)
        paths.append(path if path.is_absolute() else root_dir / path)
    relative_path = str(manifest.get("relative_path") or "").strip()
    if relative_path:
        paths.append(runtime_root / relative_path)
    return tuple(dict.fromkeys(path.resolve() for path in paths))


def under_runtime_root(path):
    try:
        path.relative_to(runtime_root)
    except ValueError:
        return False
    return True


client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
try:
    collection = client[database_name]["dataset_manifests"]
    checked = 0
    invalidated = 0
    now = datetime.now(tz=UTC)
    for manifest in collection.find({"storage": "parquet", "status": "active"}):
        manifest_ref = str(manifest.get("manifest_ref") or "").strip()
        if not manifest_ref:
            continue
        runtime_paths = tuple(path for path in candidate_paths(manifest) if under_runtime_root(path))
        if not runtime_paths:
            continue
        checked += 1
        if any(path.exists() for path in runtime_paths):
            continue
        result = collection.update_one(
            {"manifest_ref": manifest_ref, "status": "active"},
            {
                "$set": {
                    "status": "invalid",
                    "invalid_reason": "missing_columnar_file_after_runtime_cleanup",
                    "invalidated_at": now,
                    "invalidated_by": "scripts/start-control-runtime.sh",
                }
            },
        )
        invalidated += int(result.modified_count or 0)
    print(f"checked={checked} invalidated={invalidated}")
finally:
    client.close()
PY
  )"; then
    log_error "运行时列式 manifest 清理失败，停止启动：${result}"
    exit 1
  fi
  log_info "运行时列式 manifest 清理：${result}"
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

ensure_openclaw_control_ui_assets() {
  if [[ -f "${OPENCLAW_CONTROL_UI_INDEX}" ]]; then
    return 0
  fi
  if [[ ! -d "${OPENCLAW_PACKAGE_DIR}" ]]; then
    log_error "OpenClaw 目录不存在，无法构建设备界面：${OPENCLAW_PACKAGE_DIR}"
    exit 1
  fi
  if ! command -v pnpm >/dev/null 2>&1; then
    log_error "OpenClaw 设备界面资源不存在，且未找到 pnpm，无法执行 pnpm ui:build"
    exit 1
  fi

  log_info "OpenClaw 设备界面资源不存在，执行 pnpm ui:build"
  (
    cd "${OPENCLAW_PACKAGE_DIR}"
    pnpm ui:build
  )
  if [[ ! -f "${OPENCLAW_CONTROL_UI_INDEX}" ]]; then
    log_error "OpenClaw 设备界面构建后仍未找到：${OPENCLAW_CONTROL_UI_INDEX}"
    exit 1
  fi
}

read_openclaw_plugin_state() {
  local plugins_json_path="$1"
  local plugin_id="$2"
  OPENCLAW_PLUGINS_JSON_PATH_VALUE="${plugins_json_path}" \
  OPENCLAW_PLUGIN_ID_VALUE="${plugin_id}" \
    node <<'NODE'
const fs = require("node:fs");
const pluginsJsonPath = process.env.OPENCLAW_PLUGINS_JSON_PATH_VALUE;
const pluginId = process.env.OPENCLAW_PLUGIN_ID_VALUE;

let raw = "";
try {
  raw = fs.readFileSync(pluginsJsonPath, "utf8");
} catch {
  process.stdout.write("missing");
  process.exit(0);
}

let parsed = null;
try {
  parsed = JSON.parse(raw);
} catch {
  process.stdout.write("invalid");
  process.exit(0);
}

let plugins = [];
if (Array.isArray(parsed)) {
  plugins = parsed;
} else if (parsed && Array.isArray(parsed.plugins)) {
  plugins = parsed.plugins;
} else if (parsed && Array.isArray(parsed.items)) {
  plugins = parsed.items;
}

const plugin = plugins.find((item) => String(item?.id || "").trim() === pluginId);
if (!plugin) {
  process.stdout.write("missing");
  process.exit(0);
}
if (plugin.enabled === false) {
  process.stdout.write("disabled");
  process.exit(0);
}
process.stdout.write("enabled");
NODE
}

ensure_openclaw_weixin_plugin_ready() {
  if [[ ! -x "${OPENCLAW_GATEWAY_CALL_BIN}" ]]; then
    log_error "OPENCLAW_GATEWAY_CALL_BIN 不可执行：${OPENCLAW_GATEWAY_CALL_BIN}"
    exit 1
  fi

  local plugin_list_json="${LOG_DIR}/openclaw-plugins-list.json"
  local plugin_list_log="${LOG_DIR}/openclaw-plugins-list.log"
  local plugin_state="missing"
  set +e
  OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
  OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
    "${OPENCLAW_GATEWAY_CALL_BIN}" plugins list --json >"${plugin_list_json}" 2>"${plugin_list_log}"
  local list_status=$?
  set -e
  if [[ "${list_status}" == "0" ]]; then
    plugin_state="$(read_openclaw_plugin_state "${plugin_list_json}" "${OPENCLAW_WEIXIN_PLUGIN_ID}")"
  else
    log_warn "无法读取 OpenClaw 插件列表（退出码 ${list_status}），继续尝试安装微信插件。日志：${plugin_list_log}"
  fi

  if [[ "${plugin_state}" == "enabled" ]]; then
    log_info "微信插件已安装并启用：${OPENCLAW_WEIXIN_PLUGIN_ID}"
    return 0
  fi

  if [[ "${plugin_state}" == "disabled" ]]; then
    log_info "微信插件已安装但未启用，执行启用：${OPENCLAW_WEIXIN_PLUGIN_ID}"
    set +e
    OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
    OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
      "${OPENCLAW_GATEWAY_CALL_BIN}" config set "plugins.entries.${OPENCLAW_WEIXIN_PLUGIN_ID}.enabled" true \
      >"${LOG_DIR}/openclaw-weixin-plugin-enable.log" 2>&1
    local enable_status=$?
    set -e
    if [[ "${enable_status}" != "0" ]]; then
      log_warn "微信插件启用命令失败（退出码 ${enable_status}），继续启动。日志：${LOG_DIR}/openclaw-weixin-plugin-enable.log"
    fi
    return 0
  fi

  log_info "安装微信插件：${OPENCLAW_WEIXIN_PLUGIN_SPEC}"
  set +e
  OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
  OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
    "${OPENCLAW_GATEWAY_CALL_BIN}" plugins install "${OPENCLAW_WEIXIN_PLUGIN_SPEC}" \
    >"${LOG_DIR}/openclaw-weixin-plugin-install.log" 2>&1
  local install_status=$?
  set -e
  if [[ "${install_status}" != "0" ]]; then
    log_warn "微信插件安装失败（退出码 ${install_status}），继续启动。日志：${LOG_DIR}/openclaw-weixin-plugin-install.log"
    return 0
  fi

  set +e
  OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
  OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
    "${OPENCLAW_GATEWAY_CALL_BIN}" config set "plugins.entries.${OPENCLAW_WEIXIN_PLUGIN_ID}.enabled" true \
    >"${LOG_DIR}/openclaw-weixin-plugin-enable.log" 2>&1
  local enable_after_install_status=$?
  set -e
  if [[ "${enable_after_install_status}" != "0" ]]; then
    log_warn "微信插件安装后启用失败（退出码 ${enable_after_install_status}），继续启动。日志：${LOG_DIR}/openclaw-weixin-plugin-enable.log"
    return 0
  fi
  log_info "微信插件安装并启用完成：${OPENCLAW_WEIXIN_PLUGIN_ID}"
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
      code="$(curl -s -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null || true)"
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
  export OPENCLAW_GATEWAY_TOKEN
  export OPENCLAW_STATE_DIR
  export OPENCLAW_CONFIG_PATH
  export OPENCLAW_GATEWAY_TIMEOUT_MS
  export OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS
  export OPENCLAW_MARKET_TOOL_PYTHON
  export CLAW_TRADE_LLM_PROVIDER
  export CLAW_TRADE_LLM_MODEL
  export DEEPSEEK_BASE_URL
  export QWEN_BASE_URL
  export CLAW_TRADE_UI_INBOUND_URL
  export CLAW_TRADE_UI_INBOUND_TIMEOUT_MS
  export OPENVIKING_ENDPOINT
  export OPENVIKING_BASE_URL
  export OPENVIKING_WORKSPACE
  export OPENVIKING_CONFIG_FILE
  export OPENVIKING_DATA_DIR
  export OPENVIKING_WRITE_LOCK_PATH
  export CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED
  export CLAW_TRADE_OPENVIKING_VECTORIZE
  export CLAW_TRADE_OPENVIKING_VECTORIZE_REASON
  export CN_A_MONGODB_URI
  export CN_A_MONGODB_DATABASE
  export CN_A_MONGODB_CACHE_COLLECTION
  export DATA_GATEWAY_MONGODB_URI
  export DATA_GATEWAY_MONGODB_DATABASE
  export DATA_GATEWAY_SEED_MONGODB_URI
  export DATA_GATEWAY_SEED_MONGODB_DATABASE
  export DATA_GATEWAY_COLUMNAR_ROOT
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

preauthorize_openclaw_gateway_cli_scopes() {
  local preauth_log="${LOG_DIR}/openclaw-gateway-scope-preauth.log"
  local preauth_retry_log="${LOG_DIR}/openclaw-gateway-scope-preauth-retry.log"
  local approve_log="${LOG_DIR}/openclaw-gateway-scope-approve.log"
  local approve_state_log="${LOG_DIR}/openclaw-gateway-scope-approve-state.log"
  local -a preauth_cmd=(
    "${OPENCLAW_GATEWAY_CALL_BIN}"
    gateway
    call
    update.status
    --timeout
    "10000"
    --params
    '{}'
    --json
    --scope
    operator.admin
    --url
    "${OPENCLAW_GATEWAY_URL}"
  )
  if [[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
    preauth_cmd+=(--token "${OPENCLAW_GATEWAY_TOKEN}")
  elif [[ -n "${OPENCLAW_GATEWAY_PASSWORD:-}" ]]; then
    preauth_cmd+=(--password "${OPENCLAW_GATEWAY_PASSWORD}")
  fi

  set +e
  OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
  OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
    "${preauth_cmd[@]}" >"${preauth_log}" 2>&1
  local status=$?
  set -e
  if [[ "${status}" == "0" ]] || grep -Eq '"sentinel"[[:space:]]*:' "${preauth_log}"; then
    log_info "OpenClaw CLI scope 预授权完成。"
    return 0
  fi
  if ! grep -Eiq 'pairing required|scope upgrade pending approval' "${preauth_log}"; then
    log_warn "OpenClaw CLI scope 预授权首次失败，等待 gateway 稳定后重试一次。日志：${preauth_log}"
    sleep 2
    set +e
    OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
    OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
      "${preauth_cmd[@]}" >"${preauth_retry_log}" 2>&1
    status=$?
    set -e
    if [[ "${status}" == "0" ]] || grep -Eq '"sentinel"[[:space:]]*:' "${preauth_retry_log}"; then
      log_info "OpenClaw CLI scope 预授权完成。"
      return 0
    fi
    if grep -Eiq 'pairing required|scope upgrade pending approval' "${preauth_retry_log}"; then
      preauth_log="${preauth_retry_log}"
    else
      log_error "OpenClaw CLI scope 预授权失败。日志：${preauth_retry_log}"
      tail -n 40 "${preauth_retry_log}" >&2 || true
      exit 1
    fi
  fi
  if grep -Eiq 'pairing required|scope upgrade pending approval' "${preauth_log}"; then
    local request_id
    request_id="$(extract_openclaw_pairing_request_id "${preauth_log}")"
    if [[ -z "${request_id}" ]]; then
      log_error "OpenClaw CLI scope 预授权需要设备批准，但日志中没有可批准的 requestId。日志：${preauth_log}"
      tail -n 40 "${preauth_log}" >&2 || true
      exit 1
    fi
    log_info "OpenClaw CLI scope 预授权需要本机设备批准，正在批准 requestId=${request_id}"
    set +e
    OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
    OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
      "${OPENCLAW_GATEWAY_CALL_BIN}" devices approve "${request_id}" >"${approve_log}" 2>&1
    local approve_status=$?
    set -e
    if [[ "${approve_status}" != "0" ]]; then
      local approval_request_id
      approval_request_id="$(extract_openclaw_pairing_request_id "${approve_log}")"
      if [[ -z "${approval_request_id}" ]]; then
        log_error "OpenClaw 设备批准失败（requestId=${request_id}），且没有新的可批准 requestId。日志：${approve_log}"
        tail -n 40 "${approve_log}" >&2 || true
        exit 1
      fi
      log_warn "OpenClaw 设备批准命令自身需要本机权限升级，正在直接批准当前 requestId=${approval_request_id}"
      approve_openclaw_pairing_request_from_state "${approval_request_id}" "${approve_state_log}"
    fi
    set +e
    OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR}" \
    OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH}" \
      "${preauth_cmd[@]}" >"${preauth_retry_log}" 2>&1
    local retry_status=$?
    set -e
    if [[ "${retry_status}" == "0" ]]; then
      log_info "OpenClaw CLI scope 预授权完成。"
      return 0
    fi
    log_error "OpenClaw CLI scope 预授权批准后重试仍失败。日志：${preauth_retry_log}"
    tail -n 40 "${preauth_retry_log}" >&2 || true
    exit 1
  fi
  log_error "OpenClaw CLI scope 预授权失败。日志：${preauth_log}"
  tail -n 40 "${preauth_log}" >&2 || true
  exit 1
}

approve_openclaw_pairing_request_from_state() {
  local request_id="$1"
  local output_log="$2"
  local modules=("${OPENCLAW_PACKAGE_DIR}"/dist/device-pairing-*.js)
  if [[ ! -f "${modules[0]}" ]]; then
    log_error "OpenClaw device-pairing dist module 不存在，请先构建 OpenClaw。"
    exit 1
  fi
  set +e
  OPENCLAW_DEVICE_PAIRING_MODULE_VALUE="${modules[0]}" \
  OPENCLAW_PAIRING_REQUEST_ID_VALUE="${request_id}" \
  OPENCLAW_PAIRING_STATE_DIR_VALUE="${OPENCLAW_STATE_DIR}" \
    node --input-type=module - >"${output_log}" 2>&1 <<'NODE'
const mod = await import(process.env.OPENCLAW_DEVICE_PAIRING_MODULE_VALUE);
const approveDevicePairing = mod.approveDevicePairing ?? mod.n ?? mod.t;
if (typeof approveDevicePairing !== "function") {
  throw new Error("approveDevicePairing export not found");
}
const requestId = process.env.OPENCLAW_PAIRING_REQUEST_ID_VALUE;
const stateDir = process.env.OPENCLAW_PAIRING_STATE_DIR_VALUE;
const result = await approveDevicePairing(requestId, { callerScopes: ["operator.admin"] }, stateDir);
if (!result || result.status !== "approved") {
  throw new Error(`approval failed: ${JSON.stringify(result ?? null)}`);
}
process.stdout.write(JSON.stringify({ status: result.status, requestId: result.requestId ?? requestId }));
NODE
  local status=$?
  set -e
  if [[ "${status}" != "0" ]]; then
    log_error "OpenClaw 本机设备批准失败（requestId=${request_id}）。日志：${output_log}"
    tail -n 40 "${output_log}" >&2 || true
    exit 1
  fi
}

extract_openclaw_pairing_request_id() {
  local input_log="$1"
  OPENCLAW_PAIRING_LOG_PATH_VALUE="${input_log}" node <<'NODE'
const fs = require("node:fs");
const logPath = process.env.OPENCLAW_PAIRING_LOG_PATH_VALUE;
let text = "";
try {
  text = fs.readFileSync(logPath, "utf8");
} catch {
  process.exit(0);
}
const patterns = [
  /requestId["']?\s*[:=]\s*["']?([0-9A-Za-z-]{16,})/i,
  /request id\s*[:=]\s*["']?([0-9A-Za-z-]{16,})/i,
];
for (const pattern of patterns) {
  const match = text.match(pattern);
  if (match?.[1]) {
    process.stdout.write(match[1]);
    process.exit(0);
  }
}
NODE
}

prepare_openclaw_trade_agent_config() {
  local config_dir
  config_dir="$(dirname "${OPENCLAW_CONFIG_PATH}")"
  mkdir -p "${config_dir}"

  ROOT_DIR_VALUE="${ROOT_DIR}" \
  OPENCLAW_CONFIG_PATH_VALUE="${OPENCLAW_CONFIG_PATH}" \
  OPENCLAW_GATEWAY_TOKEN_VALUE="${OPENCLAW_GATEWAY_TOKEN:-}" \
  OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE="${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER:-}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_MODEL_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_MODEL:-}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_API_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_API:-}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY:-}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_BASE_URL_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_BASE_URL:-}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_MODEL_ID_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_MODEL_ID:-}" \
  CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_NAME_VALUE="${CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_NAME:-}" \
  CLAW_TRADE_LLM_PROVIDER_VALUE="${CLAW_TRADE_LLM_PROVIDER}" \
  CLAW_TRADE_LLM_MODEL_VALUE="${CLAW_TRADE_LLM_MODEL}" \
  DEEPSEEK_BASE_URL_VALUE="${DEEPSEEK_BASE_URL}" \
  QWEN_BASE_URL_VALUE="${QWEN_BASE_URL}" \
    node <<'NODE'
const fs = require("node:fs");
const rootDir = process.env.ROOT_DIR_VALUE;
const outputPath = process.env.OPENCLAW_CONFIG_PATH_VALUE;
const gatewayToken = String(process.env.OPENCLAW_GATEWAY_TOKEN_VALUE || "").trim();
const rawLlmIdleTimeoutSeconds = process.env.OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS_VALUE;
const mongoReportProvider = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_VALUE || "").trim().toLowerCase();
const mongoReportModel = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_MODEL_VALUE || "").trim();
const mongoReportApi = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_API_VALUE || "").trim();
const mongoReportApiKey = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY_VALUE || "").trim();
const mongoReportBaseUrl = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_BASE_URL_VALUE || "").trim();
const mongoReportProviderModelId = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_MODEL_ID_VALUE || "").trim();
const mongoReportProviderName = String(process.env.CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_NAME_VALUE || "").trim();
const configuredProvider = String(process.env.CLAW_TRADE_LLM_PROVIDER_VALUE || "").trim().toLowerCase();
const configuredPrimaryModel = String(process.env.CLAW_TRADE_LLM_MODEL_VALUE || "").trim();
const deepseekBaseUrl = String(process.env.DEEPSEEK_BASE_URL_VALUE || "https://api.deepseek.com").trim();
const qwenBaseUrl = String(process.env.QWEN_BASE_URL_VALUE || "https://dashscope.aliyuncs.com/compatible-mode/v1").trim();
const workers = [
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
];

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readExistingOpenClawConfig(outputPath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(outputPath, "utf8"));
    return isPlainObject(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

const existingConfig = readExistingOpenClawConfig(outputPath);
const llmIdleTimeoutSeconds = Number.parseInt(String(rawLlmIdleTimeoutSeconds ?? ""), 10);
if (!Number.isInteger(llmIdleTimeoutSeconds) || llmIdleTimeoutSeconds <= 0) {
  console.error(
    `[ERROR] OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS 必须是正整数，当前值：${rawLlmIdleTimeoutSeconds ?? "<empty>"}`,
  );
  process.exit(1);
}

function resolveProjectLlmConfig() {
  const deepseekApiKey = String(process.env.DEEPSEEK_API_KEY || "").trim();
  const deepseekModel = String(process.env.DEEPSEEK_MODEL || "").trim();
  const qwenApiKey = firstNonEmpty(
    process.env.QWEN_API_KEY,
    process.env.MODELSTUDIO_API_KEY,
    process.env.DASHSCOPE_API_KEY,
  );
  const qwenModel = firstNonEmpty(process.env.QWEN_MODEL, process.env.MODELSTUDIO_MODEL, process.env.DASHSCOPE_MODEL);
  const selectedModel = selectProjectModel({
    configuredPrimaryModel,
    configuredProvider,
    deepseekModel,
    deepseekApiKey,
    qwenModel,
    qwenApiKey,
  });
  if (!selectedModel) {
    return null;
  }
  if (selectedModel.startsWith("deepseek/")) {
    if (!deepseekApiKey) {
      console.error("[ERROR] .env.local 缺少 DEEPSEEK_API_KEY，无法生成 OpenClaw runtime LLM 配置。");
      process.exit(1);
    }
    if (!deepseekBaseUrl) {
      console.error("[ERROR] DEEPSEEK_BASE_URL 不能为空。");
      process.exit(1);
    }
    const providerModelId = selectedModel.split("/").slice(1).join("/").trim();
    if (!providerModelId) {
      console.error(`[ERROR] DeepSeek model 配置非法：${selectedModel}`);
      process.exit(1);
    }
    return {
      providerId: "deepseek",
      model: selectedModel,
      providerModelId,
      providerName: providerModelId === "deepseek-chat" ? "DeepSeek Chat" : providerModelId,
      apiKey: deepseekApiKey,
      baseUrl: deepseekBaseUrl,
      contextWindow: 131072,
      maxTokens: 8192,
    };
  }
  if (selectedModel.startsWith("qwen/") || selectedModel.startsWith("dashscope/") || selectedModel.startsWith("modelstudio/") || selectedModel.startsWith("qwencloud/")) {
    if (!qwenApiKey) {
      console.error("[ERROR] .env.local 缺少 QWEN_API_KEY / MODELSTUDIO_API_KEY / DASHSCOPE_API_KEY，无法生成 OpenClaw runtime LLM 配置。");
      process.exit(1);
    }
    if (!qwenBaseUrl) {
      console.error("[ERROR] QWEN_BASE_URL 不能为空。");
      process.exit(1);
    }
    const providerModelId = selectedModel.split("/").slice(1).join("/").trim();
    if (!providerModelId) {
      console.error(`[ERROR] Qwen model 配置非法：${selectedModel}`);
      process.exit(1);
    }
    return {
      providerId: "qwen",
      model: `qwen/${providerModelId}`,
      providerModelId,
      providerName: providerModelId,
      apiKey: qwenApiKey,
      baseUrl: qwenBaseUrl,
      contextWindow: 1000000,
      maxTokens: 65536,
    };
  }
  {
    console.error(`[ERROR] 当前 .env.local LLM provider 暂未接入 OpenClaw runtime 配置生成：${selectedModel}`);
    process.exit(1);
  }
}

function resolveMongoReportModelConfig() {
  if (!mongoReportProvider && !mongoReportModel && !mongoReportApiKey) {
    return null;
  }
  if (!mongoReportProvider || !mongoReportModel || !mongoReportApiKey) {
    console.error("[ERROR] Mongo 报告模型配置不完整：缺少 provider/model/apiKey。");
    process.exit(1);
  }
  const model = normalizeProviderModel(mongoReportProvider, mongoReportModel);
  const providerModelId = mongoReportProviderModelId || model.split("/").slice(1).join("/").trim();
  if (!providerModelId) {
    console.error(`[ERROR] Mongo 报告模型 model 配置非法：${mongoReportModel}`);
    process.exit(1);
  }
  return {
    providerId: mongoReportProvider,
    model,
    providerModelId,
    providerName: mongoReportProviderName || providerModelId,
    apiKey: mongoReportApiKey,
    baseUrl: mongoReportBaseUrl,
    api: mongoReportApi || "openai-completions",
  };
}

function firstNonEmpty(...values) {
  for (const value of values) {
    const text = String(value || "").trim();
    if (text) {
      return text;
    }
  }
  return "";
}

function selectProjectModel(params) {
  if (params.configuredPrimaryModel) {
    return normalizeConfiguredModel(params.configuredPrimaryModel, params);
  }
  if (params.deepseekModel) {
    return normalizeProviderModel("deepseek", params.deepseekModel);
  }
  if (params.qwenModel) {
    return normalizeProviderModel("qwen", params.qwenModel);
  }
  if (params.deepseekApiKey) {
    return "deepseek/deepseek-chat";
  }
  if (params.qwenApiKey) {
    return "qwen/qwen3.5-plus";
  }
  return "";
}

function normalizeConfiguredModel(model, params) {
  if (model.includes("/")) {
    return model;
  }
  if (params.configuredProvider) {
    return normalizeProviderModel(params.configuredProvider, model);
  }
  if (params.deepseekApiKey && !params.qwenApiKey) {
    return normalizeProviderModel("deepseek", model);
  }
  if (params.qwenApiKey && !params.deepseekApiKey) {
    return normalizeProviderModel("qwen", model);
  }
  console.error("[ERROR] CLAW_TRADE_LLM_MODEL 没有 provider 前缀，且无法从唯一 API key 推断 provider；请写成 deepseek/... 或 qwen/...。");
  process.exit(1);
}

function normalizeProviderModel(provider, model) {
  const trimmedProvider = String(provider || "").trim().toLowerCase();
  const trimmedModel = String(model || "").trim();
  if (!trimmedModel) {
    return "";
  }
  if (trimmedModel.includes("/")) {
    return trimmedModel;
  }
  if (trimmedProvider === "dashscope" || trimmedProvider === "modelstudio" || trimmedProvider === "qwencloud") {
    return `qwen/${trimmedModel}`;
  }
  return `${trimmedProvider}/${trimmedModel}`;
}
const llm = resolveMongoReportModelConfig() || resolveProjectLlmConfig();
const existingModelsConfig = isPlainObject(existingConfig.models) ? existingConfig.models : {};
const existingProviders = isPlainObject(existingModelsConfig.providers) ? existingModelsConfig.providers : {};
const existingAgentsConfig = isPlainObject(existingConfig.agents) ? existingConfig.agents : {};
const existingDefaults = isPlainObject(existingAgentsConfig.defaults) ? existingAgentsConfig.defaults : {};
const existingPluginsConfig = isPlainObject(existingConfig.plugins) ? existingConfig.plugins : {};
const existingPluginEntries = isPlainObject(existingPluginsConfig.entries) ? existingPluginsConfig.entries : {};
const hasSavedLlmConfig = Object.keys(existingProviders).length > 0 || Boolean(existingDefaults.model);
if (!llm && !hasSavedLlmConfig) {
  console.error("[WARN] OpenClaw LLM 未配置：仅启动设置/诊断 UI；报告执行会继续由报告模型 gate 阻断。");
} else if (!llm) {
  console.error("[INFO] OpenClaw LLM 使用已保存配置；.env.local 未覆盖。");
} else if (mongoReportProvider && mongoReportModel && mongoReportApiKey) {
  console.error("[INFO] OpenClaw LLM 使用 Mongo UI 设置。");
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

const mergedWorkers = workers.map((workerId) => {
  return {
    id: workerId,
    default: workerId === "market_analyst",
    workspace: `${rootDir}/agents/${workerId}`,
    skills: readWorkerMountedSkills(workerId),
  };
});

const mergedProviders = {
  ...existingProviders,
  ...(llm
    ? {
      [llm.providerId]: {
        ...(llm.baseUrl ? { baseUrl: llm.baseUrl } : {}),
        apiKey: llm.apiKey,
        api: llm.api || "openai-completions",
        timeoutSeconds: llmIdleTimeoutSeconds,
        models: [
          {
            id: llm.providerModelId,
            name: llm.providerName,
            reasoning: false,
            input: ["text"],
            ...(llm.contextWindow ? { contextWindow: llm.contextWindow } : {}),
            ...(llm.maxTokens ? { maxTokens: llm.maxTokens } : {}),
          },
        ],
      },
    }
    : {}),
};
const mergedModels = {
  ...existingModelsConfig,
  providers: mergedProviders,
};
const mergedDefaults = {
  ...existingDefaults,
  workspace: `${rootDir}/agents`,
  skipBootstrap: true,
};
if (llm) {
  mergedDefaults.model = {
    primary: llm.model,
  };
  mergedDefaults.models = {
    [llm.model]: {
      alias: llm.providerName,
    },
  };
}
const clawTradeFrontlinePluginPath = `${rootDir}/openclaw_plugins/claw-trade-frontline-tools`;
const clawTradeSelectionPluginPath = `${rootDir}/openclaw_plugins/claw-trade-selection-tools`;
const existingFrontlinePluginEntry = isPlainObject(existingPluginEntries["claw-trade-frontline-tools"])
  ? existingPluginEntries["claw-trade-frontline-tools"]
  : {};
const existingSelectionPluginEntry = isPlainObject(existingPluginEntries["claw-trade-selection-tools"])
  ? existingPluginEntries["claw-trade-selection-tools"]
  : {};
const existingWeixinPluginEntry = isPlainObject(existingPluginEntries["openclaw-weixin"])
  ? existingPluginEntries["openclaw-weixin"]
  : {};
const mergedPluginEntries = {
  ...existingPluginEntries,
  "claw-trade-frontline-tools": {
    ...existingFrontlinePluginEntry,
    enabled: true,
  },
  "claw-trade-selection-tools": {
    ...existingSelectionPluginEntry,
    enabled: true,
  },
  "openclaw-weixin": {
    ...existingWeixinPluginEntry,
    enabled: true,
  },
};
if (llm) {
  const existingLlmPluginEntry = isPlainObject(existingPluginEntries[llm.providerId])
    ? existingPluginEntries[llm.providerId]
    : {};
  mergedPluginEntries[llm.providerId] = {
    ...existingLlmPluginEntry,
    enabled: true,
  };
}
const mergedPlugins = {
  enabled: true,
  load: {
    paths: [clawTradeFrontlinePluginPath, clawTradeSelectionPluginPath],
  },
  entries: mergedPluginEntries,
};
const mergedMcp = {
  servers: {},
};
const existingChannels = isPlainObject(existingConfig.channels) ? existingConfig.channels : {};
const existingWeixinChannelConfig = isPlainObject(existingChannels["openclaw-weixin"])
  ? existingChannels["openclaw-weixin"]
  : {};
const weixinChannelEnabled = existingWeixinChannelConfig.enabled === false ? false : true;
const mergedChannels = {
  ...existingChannels,
  "openclaw-weixin": {
    ...existingWeixinChannelConfig,
    enabled: weixinChannelEnabled,
    replyProgressMessages: true,
  },
};

const mergedConfig = {
  gateway: {
    mode: "local",
    bind: "loopback",
    ...(gatewayToken ? { auth: { token: gatewayToken }, remote: { token: gatewayToken } } : {}),
  },
  agents: {
    defaults: mergedDefaults,
    list: mergedWorkers,
  },
  models: mergedModels,
  plugins: mergedPlugins,
  channels: mergedChannels,
  mcp: mergedMcp,
  ...(isPlainObject(existingConfig.meta) ? { meta: existingConfig.meta } : {}),
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

  OPENVIKING_CONFIG_FILE_VALUE="${OPENVIKING_CONFIG_FILE}" \
  OPENVIKING_DATA_DIR_VALUE="${OPENVIKING_DATA_DIR}" \
  OPENVIKING_SERVER_PORT_VALUE="${OPENVIKING_SERVER_PORT}" \
  OPENVIKING_EMBEDDING_PROVIDER_VALUE="${OPENVIKING_EMBEDDING_PROVIDER}" \
  OPENVIKING_EMBEDDING_MODEL_VALUE="${OPENVIKING_EMBEDDING_MODEL}" \
  OPENVIKING_EMBEDDING_API_KEY_VALUE="${OPENVIKING_EMBEDDING_API_KEY}" \
  OPENVIKING_EMBEDDING_API_BASE_VALUE="${OPENVIKING_EMBEDDING_API_BASE}" \
  OPENVIKING_EMBEDDING_DIMENSION_VALUE="${OPENVIKING_EMBEDDING_DIMENSION}" \
  OPENVIKING_EMBEDDING_QUERY_PARAM_VALUE="${OPENVIKING_EMBEDDING_QUERY_PARAM}" \
  OPENVIKING_EMBEDDING_DOCUMENT_PARAM_VALUE="${OPENVIKING_EMBEDDING_DOCUMENT_PARAM}" \
  JINA_API_KEY_VALUE="${JINA_API_KEY:-}" \
    node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

const outputPath = process.env.OPENVIKING_CONFIG_FILE_VALUE;
const dataDir = process.env.OPENVIKING_DATA_DIR_VALUE;
const port = Number.parseInt(process.env.OPENVIKING_SERVER_PORT_VALUE || "1933", 10);
const embeddingProvider = String(process.env.OPENVIKING_EMBEDDING_PROVIDER_VALUE || "").trim();
const embeddingModel = String(process.env.OPENVIKING_EMBEDDING_MODEL_VALUE || "").trim();
const embeddingApiBase = String(process.env.OPENVIKING_EMBEDDING_API_BASE_VALUE || "").trim();
const embeddingDimensionRaw = String(process.env.OPENVIKING_EMBEDDING_DIMENSION_VALUE || "").trim();
const embeddingQueryParam = String(process.env.OPENVIKING_EMBEDDING_QUERY_PARAM_VALUE || "").trim();
const embeddingDocumentParam = String(process.env.OPENVIKING_EMBEDDING_DOCUMENT_PARAM_VALUE || "").trim();

function resolveEmbeddingDimension(defaultValue) {
  if (!embeddingDimensionRaw) {
    return defaultValue;
  }
  const dimension = Number.parseInt(embeddingDimensionRaw, 10);
  if (!Number.isInteger(dimension) || dimension <= 0) {
    console.error(`[ERROR] OPENVIKING_EMBEDDING_DIMENSION 必须是正整数，当前值：${embeddingDimensionRaw}`);
    process.exit(1);
  }
  return dimension;
}

function buildEmbedding() {
  if (!embeddingProvider && !embeddingModel) {
    return {
      dense: {
        provider: "openai",
        model: "claw-trade-report-no-vectorization",
        api_base: "http://127.0.0.1",
        dimension: resolveEmbeddingDimension(1024),
      },
      max_concurrent: 1,
      max_retries: 0,
    };
  }
  if (!embeddingProvider || !embeddingModel) {
    console.error("[ERROR] OPENVIKING_EMBEDDING_PROVIDER 和 OPENVIKING_EMBEDDING_MODEL 必须同时配置在 .env.local。");
    process.exit(1);
  }
  const dense = {
    provider: embeddingProvider,
    model: embeddingModel,
  };
  let apiKey = String(process.env.OPENVIKING_EMBEDDING_API_KEY_VALUE || "").trim();
  if (!apiKey && embeddingProvider === "jina") {
    apiKey = String(process.env.JINA_API_KEY_VALUE || "").trim();
  }
  if (apiKey) {
    dense.api_key = apiKey;
  }
  if (embeddingApiBase) {
    dense.api_base = embeddingApiBase;
  }
  if (embeddingDimensionRaw) {
    dense.dimension = resolveEmbeddingDimension(1024);
  }
  if (embeddingQueryParam) {
    dense.query_param = embeddingQueryParam;
  }
  if (embeddingDocumentParam) {
    dense.document_param = embeddingDocumentParam;
  }
  return {
    dense,
    max_concurrent: 1,
    max_retries: 0,
  };
}

const next = {
  server: {
    host: "127.0.0.1",
    port,
  },
  storage: {
    workspace: dataDir,
    agfs: {
      backend: "local",
    },
    vectordb: {
      backend: "local",
    },
  },
  embedding: buildEmbedding(),
  log: {
    level: "INFO",
    output: "stdout",
  },
};

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.mkdirSync(dataDir, { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
NODE
}

clear_stale_openviking_data_lock() {
  local pid_file="${OPENVIKING_DATA_DIR}/.openviking.pid"
  if [[ ! -f "${pid_file}" ]]; then
    return 0
  fi
  local lock_pid
  lock_pid="$(tr -cd '0-9' < "${pid_file}" || true)"
  if [[ -z "${lock_pid}" ]]; then
    log_warn "删除无效 OpenViking data lock：${pid_file}"
    rm -f "${pid_file}"
    return 0
  fi
  if kill -0 "${lock_pid}" 2>/dev/null; then
    log_error "OpenViking data 目录仍被进程占用：pid=${lock_pid}, pid_file=${pid_file}"
    exit 1
  fi
  log_warn "删除 stale OpenViking data lock：${pid_file} (pid=${lock_pid} 已不存在)"
  rm -f "${pid_file}"
}

trap 'on_script_exit $?' EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

guard_factory_columnar_root
guard_factory_mongo_database

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

log_info "清理本地运行时审计目录（保留 runs 主目录、OpenViking data 与 OpenClaw state）"
mkdir -p "${OPENVIKING_RUNTIME_DIR}" "${OPENVIKING_DATA_DIR}"
find "${RUNTIME_DIR}" -mindepth 1 -maxdepth 1 ! -path "${OPENVIKING_RUNTIME_DIR}" ! -path "${OPENCLAW_STATE_DIR}" ! -path "${OPENCLAW_DEFAULT_STATE_DIR}" -exec rm -rf {} +
find "${OPENVIKING_RUNTIME_DIR}" -mindepth 1 -maxdepth 1 ! -path "${OPENVIKING_DATA_DIR}" -exec rm -rf {} +
mkdir -p "${LOG_DIR}" "${PID_DIR}"
mkdir -p "${OPENCLAW_STATE_DIR}"
mkdir -p "${RUNS_PROBE_DIR}"
find "${RUNS_PROBE_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
start_local_mongodb_if_needed
load_mongo_ui_settings_into_process_env
prune_missing_runtime_columnar_manifests
configure_openviking_embedding_runtime_flags
ensure_openclaw_control_ui_assets
ensure_openclaw_weixin_plugin_ready
prepare_openclaw_trade_agent_config
prepare_openviking_runtime_config
clear_stale_openviking_data_lock

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
CLAW_TRADE_UI_INBOUND_URL="${CLAW_TRADE_UI_INBOUND_URL}" \
CLAW_TRADE_UI_INBOUND_TIMEOUT_MS="${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS}" \
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
preauthorize_openclaw_gateway_cli_scopes

write_runtime_env_var() {
  local key="$1"
  local value="${2-}"
  printf '%s=' "${key}" >> "${RUNTIME_ENV_PATH}"
  printf '%q\n' "${value}" >> "${RUNTIME_ENV_PATH}"
}

rm -f "${RUNTIME_ENV_PATH}"
write_runtime_env_var "CLAW_TRADE_OPENCLAW_RUNNER" "${CLAW_TRADE_OPENCLAW_RUNNER}"
write_runtime_env_var "CLAW_TRADE_OPENVIKING_BACKEND" "${CLAW_TRADE_OPENVIKING_BACKEND}"
write_runtime_env_var "CLAW_TRADE_OPENVIKING_MCP_STARTED" "${openviking_mcp_started}"
write_runtime_env_var "CLAW_TRADE_OPENVIKING_PROBE_RUN_ID" "${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}"
write_runtime_env_var "OPENCLAW_GATEWAY_CALL_BIN" "${OPENCLAW_GATEWAY_CALL_BIN}"
write_runtime_env_var "OPENCLAW_GATEWAY_URL" "${OPENCLAW_GATEWAY_URL}"
write_runtime_env_var "OPENCLAW_STATE_DIR" "${OPENCLAW_STATE_DIR}"
write_runtime_env_var "OPENCLAW_CONFIG_PATH" "${OPENCLAW_CONFIG_PATH}"
write_runtime_env_var "OPENCLAW_GATEWAY_TIMEOUT_MS" "${OPENCLAW_GATEWAY_TIMEOUT_MS}"
write_runtime_env_var "OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS" "${OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS}"
write_runtime_env_var "OPENCLAW_MARKET_TOOL_PYTHON" "${OPENCLAW_MARKET_TOOL_PYTHON}"
write_runtime_env_var "CLAW_TRADE_LLM_PROVIDER" "${CLAW_TRADE_LLM_PROVIDER}"
write_runtime_env_var "CLAW_TRADE_LLM_MODEL" "${CLAW_TRADE_LLM_MODEL}"
write_runtime_env_var "DEEPSEEK_BASE_URL" "${DEEPSEEK_BASE_URL}"
write_runtime_env_var "QWEN_BASE_URL" "${QWEN_BASE_URL}"
write_runtime_env_var "CLAW_TRADE_UI_INBOUND_URL" "${CLAW_TRADE_UI_INBOUND_URL}"
write_runtime_env_var "CLAW_TRADE_UI_INBOUND_TIMEOUT_MS" "${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS}"
write_runtime_env_var "UV_CACHE_DIR" "${UV_CACHE_DIR}"
write_runtime_env_var "UV_LINK_MODE" "${UV_LINK_MODE}"
write_runtime_env_var "OPENVIKING_ENDPOINT" "${OPENVIKING_ENDPOINT}"
write_runtime_env_var "OPENVIKING_BASE_URL" "${OPENVIKING_BASE_URL}"
write_runtime_env_var "OPENVIKING_WORKSPACE" "${OPENVIKING_WORKSPACE}"
write_runtime_env_var "OPENVIKING_CONFIG_FILE" "${OPENVIKING_CONFIG_FILE}"
write_runtime_env_var "OPENVIKING_DATA_DIR" "${OPENVIKING_DATA_DIR}"
write_runtime_env_var "OPENVIKING_WRITE_LOCK_PATH" "${OPENVIKING_WRITE_LOCK_PATH}"
write_runtime_env_var "CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED" "${CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED}"
write_runtime_env_var "CLAW_TRADE_OPENVIKING_VECTORIZE" "${CLAW_TRADE_OPENVIKING_VECTORIZE}"
write_runtime_env_var "CLAW_TRADE_OPENVIKING_VECTORIZE_REASON" "${CLAW_TRADE_OPENVIKING_VECTORIZE_REASON}"
write_runtime_env_var "CN_A_MONGODB_URI" "${CN_A_MONGODB_URI}"
write_runtime_env_var "CN_A_MONGODB_DATABASE" "${CN_A_MONGODB_DATABASE}"
write_runtime_env_var "CN_A_MONGODB_CACHE_COLLECTION" "${CN_A_MONGODB_CACHE_COLLECTION}"
write_runtime_env_var "DATA_GATEWAY_MONGODB_URI" "${DATA_GATEWAY_MONGODB_URI}"
write_runtime_env_var "DATA_GATEWAY_MONGODB_DATABASE" "${DATA_GATEWAY_MONGODB_DATABASE}"
write_runtime_env_var "DATA_GATEWAY_SEED_MONGODB_URI" "${DATA_GATEWAY_SEED_MONGODB_URI}"
write_runtime_env_var "DATA_GATEWAY_SEED_MONGODB_DATABASE" "${DATA_GATEWAY_SEED_MONGODB_DATABASE}"
write_runtime_env_var "DATA_GATEWAY_COLUMNAR_ROOT" "${DATA_GATEWAY_COLUMNAR_ROOT}"
write_runtime_env_var "CLAW_TRADE_LOCAL_MONGODB_STARTED" "${local_mongodb_started}"
if [[ "${openviking_mcp_started}" == "1" ]]; then
  write_runtime_env_var "OPENVIKING_MCP_URL" "${OPENVIKING_MCP_URL}"
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
