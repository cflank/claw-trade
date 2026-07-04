#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="${ROOT_DIR}/scripts/start-research-ui.sh"
CONTROL_RUNTIME_SCRIPT="${ROOT_DIR}/scripts/start-control-runtime.sh"

RESEARCH_UI_HOST="${RESEARCH_UI_HOST:-0.0.0.0}"
RESEARCH_UI_PORT="${RESEARCH_UI_PORT:-}"
RESEARCH_UI_BUILD_FRONTEND="${RESEARCH_UI_BUILD_FRONTEND:-0}"
RESEARCH_UI_FRONTEND_DIR="${RESEARCH_UI_FRONTEND_DIR:-${ROOT_DIR}/web/research-ui}"
RESEARCH_UI_FRONTEND_DIST="${RESEARCH_UI_FRONTEND_DIST:-${RESEARCH_UI_FRONTEND_DIR}/dist}"
RESEARCH_UI_LOG_DIR="${RESEARCH_UI_LOG_DIR:-${ROOT_DIR}/.runtime/dev-services/logs}"
RESEARCH_UI_BACKEND_LOG="${RESEARCH_UI_BACKEND_LOG:-${RESEARCH_UI_LOG_DIR}/research-ui-backend.log}"
RESEARCH_UI_READY_ENDPOINT="${RESEARCH_UI_READY_ENDPOINT:-/api/ui/get-report-queue-snapshot}"
RESEARCH_UI_READY_TIMEOUT_SECONDS="${RESEARCH_UI_READY_TIMEOUT_SECONDS:-60}"
OPENCLAW_GATEWAY_RPC_HELPER_SCRIPT="${OPENCLAW_GATEWAY_RPC_HELPER_SCRIPT:-${ROOT_DIR}/scripts/openclaw-gateway-rpc-helper.mjs}"
CLAW_TRADE_UI_BACKEND_MODULE="${CLAW_TRADE_UI_BACKEND_MODULE:-claw_trade.web.app}"
CLAW_TRADE_UI_BACKEND_COMMAND="${CLAW_TRADE_UI_BACKEND_COMMAND:-}"
CLAW_TRADE_UI_INBOUND_URL="${CLAW_TRADE_UI_INBOUND_URL:-}"
CLAW_TRADE_UI_INBOUND_TIMEOUT_MS="${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS:-180000}"
RESEARCH_UI_INTERNAL_BACKEND_MARKER="${RESEARCH_UI_INTERNAL_BACKEND_MARKER:-CLAW_TRADE_UI_ALLOW_DIRECT_BACKEND_ENTRY}"
RESEARCH_UI_INTERNAL_BACKEND_MARKER_VALUE="${RESEARCH_UI_INTERNAL_BACKEND_MARKER_VALUE:-1}"
RESEARCH_UI_BUILD_FROM_CLI=0

started_backend_pid=""

log_info() {
  printf '[INFO] %s\n' "$*"
}

log_warn() {
  printf '[WARN] %s\n' "$*" >&2
}

log_error() {
  printf '[ERROR] %s\n' "$*" >&2
}

port_in_use_for() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"${port}" -sTCP:LISTEN -n -P >/dev/null 2>&1
    return $?
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :${port} )" | tail -n +2 | grep -q .
    return $?
  fi
  return 1
}

listener_pids_for_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | awk -v p=":${port}" '
      $4 ~ p"$" {
        if (match($0, /pid=[0-9]+/)) {
          pid = substr($0, RSTART + 4, RLENGTH - 4)
          print pid
        }
      }
    ' || true
    return 0
  fi
}

browser_hosts_for_research_ui() {
  if [[ "${RESEARCH_UI_HOST}" == "0.0.0.0" || "${RESEARCH_UI_HOST}" == "::" ]]; then
    printf '%s\n' "127.0.0.1"
    if command -v hostname >/dev/null 2>&1; then
      hostname -I 2>/dev/null | tr ' ' '\n' | awk '
        /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $0 != "127.0.0.1" { print }
      ' || true
    fi
    return 0
  fi
  if [[ "${RESEARCH_UI_HOST}" == "localhost" ]]; then
    printf '%s\n' "127.0.0.1"
    return 0
  fi
  printf '%s\n' "${RESEARCH_UI_HOST}"
}

log_research_ui_browser_urls() {
  local host
  log_info "浏览器访问地址："
  while IFS= read -r host; do
    if [[ -n "${host}" ]]; then
      log_info "  http://${host}:${RESEARCH_UI_PORT}/"
    fi
  done < <(browser_hosts_for_research_ui | awk '!seen[$0]++')
  log_info "服务绑定地址：http://${RESEARCH_UI_HOST}:${RESEARCH_UI_PORT}/"
}

pid_command_line() {
  local pid="$1"
  ps -p "${pid}" -o args= 2>/dev/null || true
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

clear_stale_openclaw_gateway_helpers() {
  if [[ ! -f "${OPENCLAW_GATEWAY_RPC_HELPER_SCRIPT}" ]]; then
    return 0
  fi

  local helper_pids
  helper_pids="$(ps -eo pid=,args= 2>/dev/null | awk -v helper="${OPENCLAW_GATEWAY_RPC_HELPER_SCRIPT}" '
    index($0, helper) > 0 && index($0, "node ") > 0 { print $1 }
  ')"
  if [[ -z "${helper_pids}" ]]; then
    return 0
  fi

  local pid
  while IFS= read -r pid; do
    if [[ -z "${pid}" || "${pid}" == "$$" ]]; then
      continue
    fi
    log_warn "清理旧 OpenClaw gateway helper：pid=${pid}"
    kill_pid_if_alive "${pid}"
  done <<< "${helper_pids}"
}

is_research_ui_backend_pid() {
  local pid="$1"
  local cmd
  cmd="$(pid_command_line "${pid}")"
  [[ "${cmd}" == *"-m ${CLAW_TRADE_UI_BACKEND_MODULE}"* ]] &&
    [[ "${cmd}" == *"--frontend-dist ${RESEARCH_UI_FRONTEND_DIST}"* ]]
}

clear_stale_research_ui_port() {
  local port="$1"
  local pids
  pids="$(listener_pids_for_port "${port}")"
  if [[ -z "${pids}" ]]; then
    return 0
  fi

  local cleared=0
  while IFS= read -r pid; do
    if [[ -z "${pid}" ]]; then
      continue
    fi
    if is_research_ui_backend_pid "${pid}"; then
      log_warn "清理旧 Research UI 后端：pid=${pid} port=${port}"
      kill_pid_if_alive "${pid}"
      cleared=1
    fi
  done <<< "${pids}"

  if [[ "${cleared}" == "1" ]]; then
    sleep 0.2
  fi
}

choose_research_ui_port() {
  if [[ -n "${RESEARCH_UI_PORT}" ]]; then
    if ! [[ "${RESEARCH_UI_PORT}" =~ ^[0-9]+$ ]]; then
      log_error "RESEARCH_UI_PORT 必须是数字端口，当前值：${RESEARCH_UI_PORT}"
      exit 1
    fi
    clear_stale_research_ui_port "${RESEARCH_UI_PORT}"
    if port_in_use_for "${RESEARCH_UI_PORT}"; then
      log_error "指定的 UI 端口已被占用：${RESEARCH_UI_PORT}"
      exit 1
    fi
    return 0
  fi

  local candidate
  for candidate in 5175 8787; do
    clear_stale_research_ui_port "${candidate}"
    if ! port_in_use_for "${candidate}"; then
      RESEARCH_UI_PORT="${candidate}"
      return 0
    fi
  done

  log_error "5175/8787 都被占用，无法为 UI 后端分配端口。默认不使用 5173（保留给 Vite）。"
  exit 1
}

validate_backend_entry() {
  if [[ -n "${CLAW_TRADE_UI_BACKEND_COMMAND}" ]]; then
    return 0
  fi

  if uv run python - <<'PY' "${CLAW_TRADE_UI_BACKEND_MODULE}"
import importlib.util
import sys

module_name = sys.argv[1]
try:
    spec = importlib.util.find_spec(module_name)
except ModuleNotFoundError:
    spec = None
sys.exit(0 if spec is not None else 1)
PY
  then
    return 0
  fi

  log_error "未找到 UI 后端入口模块：${CLAW_TRADE_UI_BACKEND_MODULE}"
  log_error "请先实现后端入口（承载前端 dist + /api/ui/*），或设置 CLAW_TRADE_UI_BACKEND_MODULE / CLAW_TRADE_UI_BACKEND_COMMAND。"
  exit 1
}

build_frontend_if_needed() {
  if [[ "${RESEARCH_UI_BUILD_FRONTEND}" != "1" ]]; then
    if [[ ! -d "${RESEARCH_UI_FRONTEND_DIST}" ]]; then
      log_error "前端 dist 目录不存在：${RESEARCH_UI_FRONTEND_DIST}"
      log_error "默认启动不会自动构建前端。请先运行：pnpm --dir web/research-ui build"
      log_error "如需由启动脚本先构建，可使用：scripts/start-research-ui.sh --build 或 RESEARCH_UI_BUILD_FRONTEND=1"
      exit 1
    fi
    log_info "使用已有前端 dist（默认不构建）：${RESEARCH_UI_FRONTEND_DIST}"
    return 0
  fi

  if [[ ! -d "${RESEARCH_UI_FRONTEND_DIR}" ]]; then
    log_error "前端目录不存在：${RESEARCH_UI_FRONTEND_DIR}"
    exit 1
  fi
  if [[ "${RESEARCH_UI_BUILD_FROM_CLI}" == "1" ]]; then
    log_info "检测到 --build，优先按命令行参数执行前端构建。"
  fi
  log_info "构建前端 dist：${RESEARCH_UI_FRONTEND_DIR}"
  pnpm --dir "${RESEARCH_UI_FRONTEND_DIR}" build
}

parse_main_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --build)
        RESEARCH_UI_BUILD_FRONTEND="1"
        RESEARCH_UI_BUILD_FROM_CLI=1
        ;;
      *)
        log_error "未知参数：$1"
        log_error "支持参数：--build"
        exit 1
        ;;
    esac
    shift
  done
}

wait_backend_ready() {
  local url="$1"
  local timeout_seconds="$2"
  local waited=0

  while (( waited < timeout_seconds )); do
    if [[ -n "${started_backend_pid}" ]] && ! kill -0 "${started_backend_pid}" >/dev/null 2>&1; then
      log_error "UI 后端进程提前退出（pid=${started_backend_pid}）。日志：${RESEARCH_UI_BACKEND_LOG}"
      if [[ -s "${RESEARCH_UI_BACKEND_LOG}" ]]; then
        tail -n 30 "${RESEARCH_UI_BACKEND_LOG}" >&2 || true
      fi
      return 1
    fi
    local code
    code="$(curl -s -m 2 -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null || true)"
    if [[ "${code}" == "200" || "${code}" == "204" ]]; then
      return 0
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  return 1
}

cleanup_backend_on_exit() {
  if [[ -z "${started_backend_pid}" ]]; then
    return 0
  fi
  if kill -0 "${started_backend_pid}" >/dev/null 2>&1; then
    kill "${started_backend_pid}" >/dev/null 2>&1 || true
    wait "${started_backend_pid}" >/dev/null 2>&1 || true
  fi
  clear_stale_openclaw_gateway_helpers
}

run_backend_inside_runtime() {
  mkdir -p "${RESEARCH_UI_LOG_DIR}"
  local ready_url="http://${RESEARCH_UI_HOST}:${RESEARCH_UI_PORT}${RESEARCH_UI_READY_ENDPOINT}"

  trap cleanup_backend_on_exit EXIT INT TERM

  export CLAW_TRADE_UI_HOST="${RESEARCH_UI_HOST}"
  export CLAW_TRADE_UI_PORT="${RESEARCH_UI_PORT}"
  export CLAW_TRADE_UI_FRONTEND_DIST="${RESEARCH_UI_FRONTEND_DIST}"

  if [[ -n "${CLAW_TRADE_UI_BACKEND_COMMAND}" ]]; then
    (cd "${ROOT_DIR}" && bash -lc "${CLAW_TRADE_UI_BACKEND_COMMAND}") >"${RESEARCH_UI_BACKEND_LOG}" 2>&1 &
  else
    (cd "${ROOT_DIR}" && uv run python -m "${CLAW_TRADE_UI_BACKEND_MODULE}" \
      --host "${RESEARCH_UI_HOST}" \
      --port "${RESEARCH_UI_PORT}" \
      --frontend-dist "${RESEARCH_UI_FRONTEND_DIST}") >"${RESEARCH_UI_BACKEND_LOG}" 2>&1 &
  fi
  started_backend_pid="$!"

  log_info "UI 后端已启动，等待可用：pid=${started_backend_pid}"
  log_research_ui_browser_urls
  log_info "后端日志: ${RESEARCH_UI_BACKEND_LOG}"

  if ! wait_backend_ready "${ready_url}" "${RESEARCH_UI_READY_TIMEOUT_SECONDS}"; then
    log_error "UI 后端健康检查失败：${ready_url}"
    exit 1
  fi

  log_info "Research UI 已就绪。"
  log_research_ui_browser_urls
  wait "${started_backend_pid}"
}

run_main() {
  parse_main_args "$@"
  if [[ ! -x "${CONTROL_RUNTIME_SCRIPT}" ]]; then
    log_error "缺少可执行 fixed runtime 入口：${CONTROL_RUNTIME_SCRIPT}"
    exit 1
  fi
  choose_research_ui_port
  if [[ -z "${CLAW_TRADE_UI_INBOUND_URL}" ]]; then
    CLAW_TRADE_UI_INBOUND_URL="http://${RESEARCH_UI_HOST}:${RESEARCH_UI_PORT}/api/ui/channel-inbound-message"
  fi
  validate_backend_entry
  build_frontend_if_needed
  mkdir -p "${RESEARCH_UI_LOG_DIR}"
  clear_stale_openclaw_gateway_helpers

  log_info "将通过 fixed runtime 启动 UI 全栈（OpenViking/OpenClaw/Mongo + UI 后端）"
  log_info "默认避开 5173；当前 UI 端口：${RESEARCH_UI_PORT}"

  env \
    RESEARCH_UI_HOST="${RESEARCH_UI_HOST}" \
    RESEARCH_UI_PORT="${RESEARCH_UI_PORT}" \
    RESEARCH_UI_FRONTEND_DIST="${RESEARCH_UI_FRONTEND_DIST}" \
    RESEARCH_UI_BACKEND_LOG="${RESEARCH_UI_BACKEND_LOG}" \
    RESEARCH_UI_LOG_DIR="${RESEARCH_UI_LOG_DIR}" \
    RESEARCH_UI_READY_ENDPOINT="${RESEARCH_UI_READY_ENDPOINT}" \
    RESEARCH_UI_READY_TIMEOUT_SECONDS="${RESEARCH_UI_READY_TIMEOUT_SECONDS}" \
    CLAW_TRADE_UI_BACKEND_MODULE="${CLAW_TRADE_UI_BACKEND_MODULE}" \
    CLAW_TRADE_UI_BACKEND_COMMAND="${CLAW_TRADE_UI_BACKEND_COMMAND}" \
    CLAW_TRADE_UI_INBOUND_URL="${CLAW_TRADE_UI_INBOUND_URL}" \
    CLAW_TRADE_UI_INBOUND_TIMEOUT_MS="${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS}" \
    CLAW_TRADE_SKIP_ENV_DATA_SOURCE_IMPORT=1 \
    "${RESEARCH_UI_INTERNAL_BACKEND_MARKER}=${RESEARCH_UI_INTERNAL_BACKEND_MARKER_VALUE}" \
    "${CONTROL_RUNTIME_SCRIPT}" -- "${SCRIPT_PATH}" --run-backend
}

if [[ "${1:-}" == "--run-backend" ]]; then
  shift
  marker_value="$(printenv "${RESEARCH_UI_INTERNAL_BACKEND_MARKER}" || true)"
  if [[ "${marker_value}" != "${RESEARCH_UI_INTERNAL_BACKEND_MARKER_VALUE}" ]]; then
    log_error "禁止直接调用 --run-backend。请运行 scripts/start-research-ui.sh（不带 --run-backend），由 scripts/start-control-runtime.sh 统一拉起固定 runtime。"
    exit 2
  fi
  run_backend_inside_runtime "$@"
else
  run_main "$@"
fi
