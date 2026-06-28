#!/usr/bin/env bash
set -euo pipefail

package="${1:-}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bundle_root="$(cd "${script_dir}/.." && pwd)"
install_root="${CLAW_TRADE_INSTALL_ROOT:-/opt/claw-trade}"
runtime_owner="${CLAW_TRADE_RUNTIME_OWNER:-clawtrade}"
runtime_group="${CLAW_TRADE_RUNTIME_GROUP:-clawtrade}"
kiosk_owner="${CLAW_TRADE_KIOSK_OWNER:-clawkiosk}"
kiosk_group="${CLAW_TRADE_KIOSK_GROUP:-clawkiosk}"
kiosk_browser="${CLAW_TRADE_KIOSK_BROWSER_BIN:-/usr/bin/chromium-browser}"
virbox_status_sdk_archive="${VIRBOX_STATUS_SDK_ARCHIVE:-${bundle_root}/virbox/virbox-status-sdk-linux-x86_64.tgz}"
virbox_status_dir="${install_root}/shared/license/virbox-status-sdk"
virbox_status_command="${virbox_status_dir}/virbox_status_sdk"
virbox_license_id="${VIRBOX_LICENSE_ID:-16427}"
ui_pid_file="${CLAW_TRADE_UI_PID_FILE:-/tmp/claw-trade-ui.pid}"
control_pid_file="${CLAW_TRADE_CONTROL_PID_FILE:-/tmp/claw-trade-control.pid}"
control_log="${CLAW_TRADE_CONTROL_LOG:-/tmp/claw-trade-control.log}"
ui_log="${CLAW_TRADE_UI_LOG:-/tmp/claw-trade-ui.log}"

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[INFO] %s\n' "$*"
}

stop_ui() {
  if [[ -f "${ui_pid_file}" ]]; then
    pid="$(cat "${ui_pid_file}" 2>/dev/null || true)"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && ps -p "${pid}" >/dev/null 2>&1; then
      sudo kill "${pid}" 2>/dev/null || true
      sleep 2
    fi
    rm -f "${ui_pid_file}"
  fi
  sudo pkill -f 'python3.12 -m claw_trade.web.app' 2>/dev/null || true
}

stop_control() {
  if [[ -f "${control_pid_file}" ]]; then
    pid="$(cat "${control_pid_file}" 2>/dev/null || true)"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && ps -p "${pid}" >/dev/null 2>&1; then
      sudo kill "${pid}" 2>/dev/null || true
      sleep 2
    fi
    rm -f "${control_pid_file}"
  fi
}

ensure_system_identity() {
  local user="$1"
  local group="$2"
  if ! getent group "${group}" >/dev/null; then
    sudo groupadd --system "${group}"
  fi
  if ! id -u "${user}" >/dev/null 2>&1; then
    sudo useradd --system --no-create-home --gid "${group}" --shell /usr/sbin/nologin "${user}"
  fi
}

write_shared_env_var() {
  local key="$1"
  local value="$2"
  local env_file="${install_root}/shared/config/claw-trade.env"
  sudo install -d -m 0750 -o "${runtime_owner}" -g "${runtime_group}" "${install_root}/shared/config"
  sudo touch "${env_file}"
  sudo sed -i "/^${key}=/d" "${env_file}"
  printf '%s=%s\n' "${key}" "${value}" | sudo tee -a "${env_file}" >/dev/null
  sudo chown "${runtime_owner}:${runtime_group}" "${env_file}"
  sudo chmod 0640 "${env_file}"
}

install_virbox_status_sdk() {
  write_shared_env_var "CLAW_TRADE_LICENSE_REQUIRED" "1"
  if [[ ! -f "${virbox_status_sdk_archive}" ]]; then
    log "Virbox status SDK archive not found; license gate will fail closed until configured: ${virbox_status_sdk_archive}"
    return 0
  fi

  log "installing Virbox status SDK"
  sudo install -d -m 0750 -o root -g "${runtime_group}" "${virbox_status_dir}"
  sudo tar -xzf "${virbox_status_sdk_archive}" -C "${virbox_status_dir}"
  sudo chown -R root:"${runtime_group}" "${virbox_status_dir}"
  sudo chmod 0755 "${virbox_status_dir}" "${virbox_status_command}"
  [[ -f "${virbox_status_dir}/libslm_control.so" ]] && sudo chmod 0755 "${virbox_status_dir}/libslm_control.so"
  write_shared_env_var "CLAW_TRADE_VIRBOX_STATUS_COMMAND" "${virbox_status_command}"
  write_shared_env_var "VIRBOX_LICENSE_ID" "${virbox_license_id}"
}

tail_log() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    tail -n 120 "${path}" >&2 || true
  fi
}

wait_for_file_from_process() {
  local path="$1"
  local pid="$2"
  local timeout_sec="$3"
  local waited=0
  while (( waited < timeout_sec )); do
    if [[ -s "${path}" ]]; then
      return 0
    fi
    if ! ps -p "${pid}" >/dev/null 2>&1; then
      return 1
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  return 1
}

source_runtime_env() {
  local runtime_env="$1"
  [[ -f "${runtime_env}" ]] || fail "runtime env not found: ${runtime_env}"
  set -a
  # shellcheck disable=SC1090
  . "${runtime_env}"
  set +a
}

ensure_runtime_gateway_token() {
  if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" && -n "${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID:-}" ]]; then
    export OPENCLAW_GATEWAY_TOKEN="claw-trade-dev-${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}"
  fi
}

port_listening() {
  local port="$1"
  ss -ltn 2>/dev/null | awk -v p=":${port}" '$4 ~ p"$" { found=1 } END { exit found ? 0 : 1 }'
}

select_openclaw_gateway_port() {
  local port
  if [[ -n "${OPENCLAW_GATEWAY_PORT:-}" ]]; then
    if port_listening "${OPENCLAW_GATEWAY_PORT}"; then
      fail "configured OPENCLAW_GATEWAY_PORT is already listening: ${OPENCLAW_GATEWAY_PORT}"
    fi
    printf '%s\n' "${OPENCLAW_GATEWAY_PORT}"
    return 0
  fi
  for port in $(seq 18789 18809); do
    if ! port_listening "${port}"; then
      printf '%s\n' "${port}"
      return 0
    fi
  done
  fail "no free OpenClaw gateway port found in 18789-18809"
}

patch_runtime_control() {
  local release_dir="${1:-${install_root}/current}"
  local runtime_script="${release_dir}/runtime/claw-trade-control-runtime"
  [[ -f "${runtime_script}" ]] || fail "runtime control script not found: ${runtime_script}"
  sudo python3.12 - <<'INNER_PY' "${runtime_script}"
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = 'OPENCLAW_GATEWAY_PORT=18789\n'
new = 'OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"\n'
if old in text:
    path.write_text(text.replace(old, new, 1))
elif new not in text:
    raise SystemExit('unexpected OPENCLAW_GATEWAY_PORT assignment')
INNER_PY
}

if [[ -z "${package}" ]]; then
  shopt -s nullglob
  packages=(/tmp/claw-trade-production-*.tar.gz)
  shopt -u nullglob
  ((${#packages[@]} > 0)) || fail "missing package argument and no /tmp/claw-trade-production-*.tar.gz found"
  package="${packages[$((${#packages[@]} - 1))]}"
fi
[[ -f "${package}" ]] || fail "package not found: ${package}"

log "installing OS dependencies"
sudo apt-get update
sudo apt-get install -y ca-certificates curl tar python3.12

node_major="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true)"
if [[ -z "${node_major}" || "${node_major}" -lt 22 ]]; then
  log "installing Node.js 22 (requires internet access)"
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
node --version
python3.12 --version
ensure_system_identity "${runtime_owner}" "${runtime_group}"
ensure_system_identity "${kiosk_owner}" "${kiosk_group}"
sudo apt-get install -y chromium-browser
[[ -x "${kiosk_browser}" ]] || fail "missing kiosk browser: ${kiosk_browser}"

log "installing package: ${package}"
sudo install -d -m 0755 "${install_root}/releases"
  sudo install -d -m 0750 \
    "${install_root}/shared" \
    "${install_root}/shared"/{cache,config,data,license,logs,openclaw,queues,reports,runs,sessions,tmp,updates} \
  "${install_root}/shared/logs"/{diagnostics,factory-reset} \
  "${install_root}/shared/updates"/{downloads,logs}
top_dir="$(python3.12 "${script_dir}/validate_production_archive.py" "${package}")"
sudo tar --no-same-owner --no-same-permissions -xzf "${package}" -C "${install_root}/releases"
patch_runtime_control "${install_root}/releases/${top_dir}"
sudo chmod 0755 "${install_root}/releases/${top_dir}"
sudo chown root:root "${install_root}/releases"
sudo chown -R root:root "${install_root}/releases/${top_dir}"
sudo chown -R "${runtime_owner}:${runtime_group}" "${install_root}/shared"
install_virbox_status_sdk

"${install_root}/releases/${top_dir}/bin/claw-trade-preflight"
tmp_current="${install_root}/.current.${top_dir}.$$"
sudo ln -sfn "${install_root}/releases/${top_dir}" "${tmp_current}"
sudo mv -Tf "${tmp_current}" "${install_root}/current"
sudo chown -h root:root "${install_root}/current"
tmp_rescue_current="${install_root}/.rescue-current.${top_dir}.$$"
sudo ln -sfn "${install_root}/releases/${top_dir}" "${tmp_rescue_current}"
sudo mv -Tf "${tmp_rescue_current}" "${install_root}/rescue-current"
sudo chown -h root:root "${install_root}/rescue-current"
export PYTHONPATH="${install_root}/current/app/python:${install_root}/current/runtime/python-site-packages${PYTHONPATH:+:${PYTHONPATH}}"

runtime_env="${install_root}/current/.runtime/dev-services/runtime.env"

log "starting runtime control"
stop_ui
stop_control
openclaw_gateway_port="$(select_openclaw_gateway_port)"
export OPENCLAW_GATEWAY_PORT="${openclaw_gateway_port}"
export OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:${openclaw_gateway_port}}"
log "using OpenClaw gateway: ${OPENCLAW_GATEWAY_URL}"
rm -f "${control_log}" "${runtime_env}"
nohup sudo -u "${runtime_owner}" -g "${runtime_group}" env \
  OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT}" \
  OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL}" \
  "${install_root}/current/bin/claw-trade-control" >"${control_log}" 2>&1 &
echo "$!" >"${control_pid_file}"
control_pid="$(cat "${control_pid_file}")"
if ! wait_for_file_from_process "${runtime_env}" "${control_pid}" 120; then
  tail_log "${control_log}"
  fail "runtime control did not become ready"
fi

log "verifying factory seed restore"
source_runtime_env "${runtime_env}"
ensure_runtime_gateway_token
python3.12 -c 'import os; from pymongo import MongoClient; db=os.environ["DATA_GATEWAY_SEED_MONGODB_DATABASE"]; c=MongoClient(os.environ["DATA_GATEWAY_SEED_MONGODB_URI"], serverSelectionTimeoutMS=5000)[db]; counts=(c.dataset_manifests.count_documents({}), c.provider_attempts.count_documents({}), c.raw_payloads.count_documents({})); print(db, *counts); raise SystemExit(0 if all(value > 0 for value in counts) else 1)'

log "starting UI"
stop_ui
rm -f "${ui_log}"
nohup sudo -u "${runtime_owner}" -g "${runtime_group}" bash -c 'set -euo pipefail; runtime_env="$1"; ui_bin="$2"; set -a; . "${runtime_env}"; set +a; if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" && -n "${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID:-}" ]]; then export OPENCLAW_GATEWAY_TOKEN="claw-trade-dev-${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}"; fi; exec "${ui_bin}"' bash "${runtime_env}" "${install_root}/current/bin/claw-trade-ui" >"${ui_log}" 2>&1 &
echo "$!" >"${ui_pid_file}"
sleep 5
if ! curl -fsS http://127.0.0.1:5175/ >/dev/null; then
  tail_log "${ui_log}"
  fail "UI did not respond on 127.0.0.1:5175"
fi

cat <<EOF
[OK] claw-trade installed
Package: ${package}
Current: ${install_root}/current
UI: http://127.0.0.1:5175/
Log: /tmp/claw-trade-ui.log
Control log: /tmp/claw-trade-control.log
EOF
