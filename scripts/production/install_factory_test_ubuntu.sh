#!/usr/bin/env bash
set -euo pipefail

package=""
license_key_file=""
update_public_key_file="${CLAW_TRADE_UPDATE_PUBLIC_KEY_FILE:-/tmp/update-signing-public.pem}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bundle_root="$(cd "${script_dir}/.." && pwd)"
install_root="${CLAW_TRADE_INSTALL_ROOT:-/opt/claw-trade}"
opt_root="/opt"
virbox_lcc_root="${VIRBOX_LCC_ROOT:-${opt_root}/senseshield}"
runtime_owner="${CLAW_TRADE_RUNTIME_OWNER:-clawtrade}"
runtime_group="${CLAW_TRADE_RUNTIME_GROUP:-clawtrade}"
kiosk_owner="${CLAW_TRADE_KIOSK_OWNER:-clawkiosk}"
kiosk_group="${CLAW_TRADE_KIOSK_GROUP:-clawkiosk}"
kiosk_browser="${CLAW_TRADE_KIOSK_BROWSER_BIN:-/usr/bin/chromium-browser}"
virbox_status_sdk_archive="${VIRBOX_STATUS_SDK_ARCHIVE:-}"
virbox_status_dir="${install_root}/shared/license/virbox-status-sdk"
virbox_status_command="${virbox_status_dir}/virbox_status_sdk"
virbox_license_id="${VIRBOX_LICENSE_ID:-16427}"
ui_pid_file="${CLAW_TRADE_UI_PID_FILE:-/tmp/claw-trade-ui.pid}"
control_pid_file="${CLAW_TRADE_CONTROL_PID_FILE:-/tmp/claw-trade-control.pid}"
control_log="${CLAW_TRADE_CONTROL_LOG:-/tmp/claw-trade-control.log}"
ui_log="${CLAW_TRADE_UI_LOG:-/tmp/claw-trade-ui.log}"
update_base_url="${CLAW_TRADE_UPDATE_BASE_URL:-https://download.cflank-trade.top/stable/}"
auto_update_install="${CLAW_TRADE_AUTO_UPDATE_INSTALL:-0}"

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[INFO] %s\n' "$*"
}

usage() {
  cat <<EOF
Usage: $0 [claw-trade-production-*.tar.gz] [--license-key-file /path/to/license.key]
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --license-key-file)
        [[ $# -ge 2 ]] || fail "missing value for --license-key-file"
        license_key_file="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --*)
        fail "unknown option: $1"
        ;;
      *)
        [[ -z "${package}" ]] || fail "unexpected argument: $1"
        package="$1"
        shift
        ;;
    esac
  done
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
  sudo pkill -f 'claw_trade.web.app' 2>/dev/null || true
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
  sudo pkill -f 'claw-trade-control-runtime' 2>/dev/null || true
  sudo pkill -f 'claw_trade.runtime.openviking_report_server' 2>/dev/null || true
  sudo pkill -f 'openclaw$' 2>/dev/null || true
}

ensure_system_identity() {
  local user="$1"
  local group="$2"
  local home
  if ! getent group "${group}" >/dev/null; then
    sudo groupadd --system "${group}"
  fi
  if ! id -u "${user}" >/dev/null 2>&1; then
    sudo useradd --system --create-home --gid "${group}" --shell /usr/sbin/nologin "${user}"
  fi
  home="$(getent passwd "${user}" | awk -F: '{print $6}')"
  if [[ -n "${home}" && "${home}" != "/" && "${home}" != "/nonexistent" ]]; then
    sudo install -d -m 0750 -o "${user}" -g "${group}" "${home}"
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
    fail "missing Virbox status SDK archive: ${virbox_status_sdk_archive}"
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

url_encode() {
  python3.12 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

read_license_key_file() {
  local path="$1"
  [[ -f "${path}" ]] || fail "license key file not found: ${path}"
  local license_key
  license_key="$(tr -d '[:space:]' < "${path}")"
  [[ -n "${license_key}" ]] || fail "license key file is empty: ${path}"
  [[ "${license_key}" =~ ^[A-Za-z0-9-]+$ ]] || fail "license key file contains invalid characters: ${path}"
  printf '%s' "${license_key}"
}

wait_for_virbox_lcc_ready() {
  local lcc_url="${VIRBOX_LCC_URL:-http://127.0.0.1:12339}"
  local body
  for _ in $(seq 1 30); do
    if systemctl is-active --quiet senseshield && systemctl is-active --quiet virboxlcc; then
      body="$(curl -fsS --max-time 5 "${lcc_url}/v1/ss/queryStatus" 2>/dev/null || true)"
      if grep -q '"serviceStatusFlag" : 0' <<<"${body}" && grep -q '"status" : 0' <<<"${body}"; then
        sleep 3
        return 0
      fi
    fi
    sleep 1
  done
  fail "Virbox local service did not become ready"
}

configure_virbox_message_timeout() {
  local path json_config cloud_config changed=0
  for path in "${virbox_lcc_root}/etc/ss_service/ss_config.xml" "${virbox_lcc_root}/ss_config.xml"; do
    [[ -f "${path}" ]] || continue
    if ! grep -q '<MSG_TIMEOUT>60000</MSG_TIMEOUT>' "${path}"; then
      sudo sed -i -E 's#<MSG_TIMEOUT>[0-9]+</MSG_TIMEOUT>#<MSG_TIMEOUT>60000</MSG_TIMEOUT>#' "${path}"
      changed=1
    fi
  done

  json_config="${virbox_lcc_root}/etc/license_control_center/virboxlcc_config.json"
  if [[ -f "${json_config}" ]] && grep -Eq '"(timeout|operationTimeout|longTimeout)" : (5000|7000|90000)' "${json_config}"; then
    sudo sed -i -E 's/"timeout" : 5000/"timeout" : 60000/g; s/"operationTimeout" : 7000/"operationTimeout" : 60000/g; s/"longTimeout" : 90000/"longTimeout" : 120000/g' "${json_config}"
    changed=1
  fi

  cloud_config="${virbox_lcc_root}/etc/common/ss_cloud.xml"
  if [[ -f "${cloud_config}" ]] && ! grep -q '<SERVER_GROUP Name="ChinaVBPGlobalCDN" Default=1>' "${cloud_config}"; then
    sudo sed -i -E '/Name="China"/s/Default=1/Default=0/; /Name="International"/s/Default=1/Default=0/; /Name="ChinaVBPGlobalCDN"/s/Default=0/Default=1/' "${cloud_config}"
    changed=1
  fi

  if (( changed )); then
    log "configured Virbox message timeout and cloud route"
    sudo systemctl daemon-reload || true
    sudo systemctl restart senseshield virboxlcc || true
  fi
  wait_for_virbox_lcc_ready
}

virbox_license_available() {
  local enum_file="$1"
  local suffix="$2"
  python3.12 - "${enum_file}" "${suffix}" "${virbox_license_id}" <<'PY'
import json
import sys

items = json.loads(open(sys.argv[1], encoding="utf-8").read() or "[]")
suffix = sys.argv[2]
license_id = sys.argv[3]
for item in items if isinstance(items, list) else []:
    ids = [str(item.get("licenseSrc", "")), str(item.get("deviceInfo", {}).get("id", ""))]
    if str(item.get("licenseId", "")) == license_id and item.get("licenseAvailable") and any(value.endswith(suffix) for value in ids):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

bind_virbox_license_key_file() {
  [[ -n "${license_key_file}" ]] || return 0
  local license_key suffix lcc_url encoded enum_file body_file body_text status attempt max_attempts
  license_key="$(read_license_key_file "${license_key_file}")"
  suffix="${license_key: -4}"
  lcc_url="${VIRBOX_LCC_URL:-http://127.0.0.1:12339}"
  log "binding Virbox license key from file: ${license_key_file} (suffix: ${suffix})"

  enum_file="$(mktemp)"
  curl -fsS -X POST "${lcc_url}/v1/license/enumLicense" \
    -H 'Content-Type: application/json' \
    -d '{}' -o "${enum_file}" || fail "Virbox local service not reachable: ${lcc_url}"
  if virbox_license_available "${enum_file}" "${suffix}"; then
    rm -f "${enum_file}"
    if [[ -x "${virbox_status_command}" ]]; then
      VIRBOX_LICENSE_ID="${virbox_license_id}" "${virbox_status_command}" >/dev/null \
        || fail "Virbox status check failed for existing license; key suffix: ${suffix}"
    fi
    log "Virbox license already available (suffix: ${suffix})"
    return 0
  fi
  rm -f "${enum_file}"

  encoded="$(url_encode "${license_key}")"
  max_attempts=12
  for attempt in $(seq 1 "${max_attempts}"); do
    body_file="$(mktemp)"
    if status="$(curl -sS --max-time 90 -o "${body_file}" -w '%{http_code}' "${lcc_url}/v1/license/bindLicenseKey?licenseKey=${encoded}")"; then
      if [[ "${status}" == 2* ]]; then
        rm -f "${body_file}"
        break
      fi
      body_text="$(tr '\n' ' ' < "${body_file}" | sed -E 's/[[:space:]]+/ /g' | cut -c1-300)"
    else
      status="curl"
      body_text="request failed"
    fi
    rm -f "${body_file}"
    if (( attempt == max_attempts )); then
      fail "Virbox license bind failed HTTP ${status}: ${body_text}; key suffix: ${suffix}"
    fi
    log "Virbox license bind failed attempt ${attempt}/${max_attempts}: HTTP ${status}: ${body_text}; retrying"
    sleep 5
  done

  if [[ -x "${virbox_status_command}" ]]; then
    VIRBOX_LICENSE_ID="${virbox_license_id}" "${virbox_status_command}" >/dev/null \
      || fail "Virbox status check failed after license bind; key suffix: ${suffix}"
  fi
  log "Virbox license bound (suffix: ${suffix})"
}

install_update_public_key_file() {
  [[ -f "${update_public_key_file}" ]] || {
    log "update public key not found; remote update will fail until installed: ${update_public_key_file}"
    return 0
  }
  sudo install -d -m 0755 /etc/claw-trade
  sudo install -m 0644 "${update_public_key_file}" /etc/claw-trade/update-signing-public.pem
  log "installed update public key: /etc/claw-trade/update-signing-public.pem"
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
export_new = 'export OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"\n'
if old in text:
    path.write_text(text.replace(old, new, 1))
elif new not in text and export_new not in text:
    raise SystemExit('unexpected OPENCLAW_GATEWAY_PORT assignment')
INNER_PY
}

warm_up_protected_python() {
  local release_dir="$1"
  local python_bin="${release_dir}/runtime/python/bin/python"
  local pythonpath="${release_dir}/app/python:${release_dir}/runtime/python-site-packages"
  local attempt status

  [[ -x "${python_bin}" ]] || fail "protected Python runtime missing: ${python_bin}"
  log "warming protected Python runtime"
  for attempt in 1 2 3; do
    if sudo -u "${runtime_owner}" -g "${runtime_group}" env PYTHONPATH="${pythonpath}" \
      "${python_bin}" "${release_dir}/scripts/selection/restore_a_share_factory_seed.py" --help >/dev/null; then
      return 0
    else
      status="$?"
    fi
    if (( attempt < 3 )); then
      log "protected Python warmup failed (attempt ${attempt}/3, exit ${status}); retrying"
      sleep 3
    else
      log "protected Python warmup failed (attempt ${attempt}/3, exit ${status})"
    fi
  done
  fail "protected Python warmup failed"
}

parse_args "$@"

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
sudo apt-get install -y ca-certificates curl tar python3.12 wkhtmltopdf fontconfig fonts-noto-cjk

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
if [[ -z "${virbox_status_sdk_archive}" ]]; then
  virbox_status_sdk_archive="${install_root}/releases/${top_dir}/virbox/virbox-status-sdk-linux-x86_64.tgz"
fi
patch_runtime_control "${install_root}/releases/${top_dir}"
sudo chmod 0755 "${install_root}/releases/${top_dir}"
sudo chown root:root "${install_root}/releases"
sudo chown -R root:root "${install_root}/releases/${top_dir}"
sudo chown -R "${runtime_owner}:${runtime_group}" "${install_root}/shared"
install_virbox_status_sdk
write_shared_env_var "CLAW_TRADE_UPDATE_BASE_URL" "${update_base_url}"
write_shared_env_var "CLAW_TRADE_AUTO_UPDATE_INSTALL" "${auto_update_install}"
install_update_public_key_file
configure_virbox_message_timeout
bind_virbox_license_key_file
warm_up_protected_python "${install_root}/releases/${top_dir}"

"${install_root}/releases/${top_dir}/bin/claw-trade-preflight"
tmp_current="${install_root}/.current.${top_dir}.$$"
sudo ln -sfn "${install_root}/releases/${top_dir}" "${tmp_current}"
sudo mv -Tf "${tmp_current}" "${install_root}/current"
sudo chown -h root:root "${install_root}/current"
tmp_rescue_current="${install_root}/.rescue-current.${top_dir}.$$"
sudo ln -sfn "${install_root}/releases/${top_dir}" "${tmp_rescue_current}"
sudo mv -Tf "${tmp_rescue_current}" "${install_root}/rescue-current"
sudo chown -h root:root "${install_root}/rescue-current"
sudo install -d -m 0755 /usr/local/lib/claw-trade
sudo install -m 0755 "${install_root}/current/root-helper/claw-trade-apply-update" /usr/local/lib/claw-trade/claw-trade-apply-update
sudo install -m 0644 "${install_root}/current/systemd/"*.service /etc/systemd/system/
sudo install -m 0644 "${install_root}/current/systemd/"*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo install -m 0440 "${install_root}/current/sudoers/claw-trade-update" /etc/sudoers.d/claw-trade-update
sudo visudo -cf /etc/sudoers.d/claw-trade-update >/dev/null
sudo systemctl disable --now claw-trade-auto-update.timer >/dev/null 2>&1 || true
export PYTHONPATH="${install_root}/current/app/python:${install_root}/current/runtime/python-site-packages${PYTHONPATH:+:${PYTHONPATH}}"

runtime_env="${install_root}/shared/tmp/dev-services/runtime.env"

log "starting runtime control"
stop_ui
stop_control
openclaw_gateway_port="$(select_openclaw_gateway_port)"
export OPENCLAW_GATEWAY_PORT="${openclaw_gateway_port}"
export OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:${openclaw_gateway_port}}"
log "using OpenClaw gateway: ${OPENCLAW_GATEWAY_URL}"
rm -f "${control_log}" "${runtime_env}"
nohup sudo -u "${runtime_owner}" -g "${runtime_group}" bash -c 'cd "$1"; shift; exec env "$@"' bash "${install_root}/shared" \
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
nohup sudo -u "${runtime_owner}" -g "${runtime_group}" bash -c 'set -euo pipefail; runtime_env="$1"; ui_bin="$2"; shared_root="$3"; cd "${shared_root}"; set -a; . "${runtime_env}"; set +a; export CLAW_TRADE_UI_HOST="${CLAW_TRADE_UI_HOST:-0.0.0.0}"; if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" && -n "${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID:-}" ]]; then export OPENCLAW_GATEWAY_TOKEN="claw-trade-dev-${CLAW_TRADE_OPENVIKING_PROBE_RUN_ID}"; fi; exec "${ui_bin}"' bash "${runtime_env}" "${install_root}/current/bin/claw-trade-ui" "${install_root}/shared" >"${ui_log}" 2>&1 &
echo "$!" >"${ui_pid_file}"
ui_ready=0
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:5175/ >/dev/null 2>&1; then
    ui_ready=1
    break
  fi
  sleep 1
done
if [[ "${ui_ready}" != "1" ]]; then
  tail_log "${ui_log}"
  fail "UI did not respond on 127.0.0.1:5175"
fi

log "verifying auto update command"
sudo systemctl reset-failed claw-trade-auto-update.service claw-trade-control.service claw-trade-rescue.service >/dev/null 2>&1 || true
sudo -u "${runtime_owner}" -g "${runtime_group}" bash -c 'set -euo pipefail; set -a; . "$1"; set +a; exec "$2"' \
  bash "${install_root}/shared/config/claw-trade.env" "${install_root}/current/bin/claw-trade-auto-update" >/dev/null \
  || fail "auto update command did not run successfully"
sudo systemctl reset-failed claw-trade-auto-update.service claw-trade-control.service claw-trade-rescue.service >/dev/null 2>&1 || true

ui_lan_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<NF; i++) if ($i == "src") {print $(i+1); exit}}')"
if [[ -z "${ui_lan_ip}" ]]; then
  ui_lan_ip="$(ip -o -4 addr show scope global 2>/dev/null | awk '{sub(/\/.*/, "", $4); print $4; exit}')"
fi
ui_lan_url=""
if [[ -n "${ui_lan_ip}" ]]; then
  ui_lan_url="UI(LAN): http://${ui_lan_ip}:5175/"
fi

cat <<EOF
[OK] claw-trade installed
Package: ${package}
Current: ${install_root}/current
UI(local): http://127.0.0.1:5175/
${ui_lan_url}
Log: /tmp/claw-trade-ui.log
Control log: /tmp/claw-trade-control.log
EOF
