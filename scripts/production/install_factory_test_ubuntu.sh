#!/usr/bin/env bash
set -euo pipefail

install_root="${INSTALL_ROOT:-/opt/claw-trade}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bundle_root="$(cd "${script_dir}/.." && pwd)"
package="${1:-${CLAW_TRADE_FACTORY_PACKAGE:-}}"
control_log="${CONTROL_LOG:-/tmp/claw-trade-control.log}"
ui_log="${UI_LOG:-/tmp/claw-trade-ui.log}"
control_pid_file="${CONTROL_PID_FILE:-/tmp/claw-trade-control.pid}"
ui_pid_file="${UI_PID_FILE:-/tmp/claw-trade-ui.pid}"
mongod_pid_file="${MONGOD_PID_FILE:-${install_root}/shared/run/mongod.pid}"
virbox_status_sdk_archive="${VIRBOX_STATUS_SDK_ARCHIVE:-${bundle_root}/virbox/virbox-status-sdk-linux-x86_64.tgz}"
virbox_status_dir="${install_root}/shared/license/virbox-status-sdk"
virbox_status_command="${virbox_status_dir}/virbox_status_sdk"
virbox_license_id="${VIRBOX_LICENSE_ID:-16427}"
virbox_status_installed=0

log() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
tail_log() { [[ -f "$1" ]] && tail -n 160 "$1" >&2 || true; }

stop_pid_file() {
  local file="$1" pid=""
  [[ -f "$file" ]] && pid="$(cat "$file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && ps -p "$pid" >/dev/null 2>&1; then
    kill "$pid" 2>/dev/null || sudo kill "$pid" 2>/dev/null || true
    sleep 2
    kill -9 "$pid" 2>/dev/null || sudo kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

stop_ui() {
  stop_pid_file "$ui_pid_file"
  pkill -f -- "-m claw_trade.web.app" 2>/dev/null || sudo pkill -f -- "-m claw_trade.web.app" 2>/dev/null || true
}

stop_control() {
  stop_pid_file "$control_pid_file"
  pkill -f "${install_root}/current/runtime/bin/claw-trade-control-runtime" 2>/dev/null || sudo pkill -f "${install_root}/current/runtime/bin/claw-trade-control-runtime" 2>/dev/null || true
}

port_free() {
  local port="$1"
  ! ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN
}

select_openclaw_gateway_port() {
  for port in $(seq 18789 18809); do
    port_free "$port" && { printf '%s\n' "$port"; return 0; }
  done
  fail "no free OpenClaw gateway port found in 18789-18809"
}

wait_http_any() {
  local timeout="$1"
  shift
  for _ in $(seq 1 "$timeout"); do
    for url in "$@"; do
      curl -fsS "$url" >/dev/null 2>&1 && return 0
    done
    sleep 1
  done
  return 1
}

mongodb_ready() {
  "${install_root}/current/runtime/python/bin/python" - <<'PY' >/dev/null 2>&1
from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=1000)
client.admin.command("ping")
PY
}

ensure_mongodb() {
  local archive="${install_root}/current/runtime/mongodb/mongodb-linux-x86_64-ubuntu2404-8.0.12-claw-test.tgz"
  local mongo_root="${install_root}/shared/mongodb"
  local mongo_data="${install_root}/shared/mongodb-data"
  local mongo_run="${install_root}/shared/run"
  local mongo_log="${install_root}/shared/logs/mongod.log"
  local top_dir=""

  if mongodb_ready; then
    log "using existing MongoDB on 127.0.0.1:27017"
    return 0
  fi
  if ss -ltn "sport = :27017" 2>/dev/null | grep -q LISTEN; then
    fail "127.0.0.1:27017 is occupied but MongoDB ping failed"
  fi
  [[ -f "$archive" ]] || fail "bundled MongoDB runtime missing and no MongoDB is running: $archive"

  log "starting bundled MongoDB"
  sudo mkdir -p "$mongo_root" "$mongo_data" "$mongo_run" "${install_root}/shared/logs"
  sudo chown -R "$(id -un):$(id -gn)" "$mongo_root" "$mongo_data" "$mongo_run" "${install_root}/shared/logs"
  top_dir="$(tar -tzf "$archive" | awk -F/ 'NR == 1 { first=$1 } END { if (first != "") print first; else exit 1 }')" || fail "cannot read MongoDB archive top directory"
  if [[ ! -x "${mongo_root}/${top_dir}/bin/mongod" ]]; then
    tar -xzf "$archive" -C "$mongo_root"
  fi
  ln -sfn "${mongo_root}/${top_dir}" "${mongo_root}/current"
  sudo rm -f /tmp/mongodb-27017.sock
  "${mongo_root}/current/bin/mongod" \
    --dbpath "$mongo_data" \
    --logpath "$mongo_log" \
    --pidfilepath "$mongod_pid_file" \
    --bind_ip 127.0.0.1 \
    --port 27017 \
    --wiredTigerCacheSizeGB 1 \
    --fork

  for _ in $(seq 1 30); do
    mongodb_ready && return 0
    sleep 1
  done
  fail "bundled MongoDB did not become ready"
}

install_package() {
  [[ -n "$package" ]] || return 0
  [[ -f "$package" ]] || fail "package not found: $package"

  getent group clawtrade >/dev/null || sudo groupadd --system clawtrade
  id -u clawtrade >/dev/null 2>&1 || sudo useradd --system --gid clawtrade --home "$install_root" --shell /usr/sbin/nologin clawtrade

  log "installing package: $package"
  sudo mkdir -p \
    "${install_root}/releases" \
    "${install_root}/shared/config" \
    "${install_root}/shared/data" \
    "${install_root}/shared/logs" \
    "${install_root}/shared/license" \
    "${install_root}/shared/reports" \
    "${install_root}/shared/updates"

  top_dir="$(tar -tf "$package" | awk -F/ 'NR == 1 { first=$1 } END { if (first != "") print first; else exit 1 }')" || fail "cannot read package top directory"
  sudo tar --no-same-owner --no-same-permissions -xf "$package" -C "${install_root}/releases"
  sudo ln -sfn "${install_root}/releases/${top_dir}" "${install_root}/current.next"
  sudo mv -Tf "${install_root}/current.next" "${install_root}/current"
  sudo chmod +x "${install_root}/current/bin/"*
  sudo chown -R "$(id -un):$(id -gn)" "${install_root}/releases/${top_dir}" "${install_root}/shared"
  sudo chown -h "$(id -un):$(id -gn)" "${install_root}/current"
}

install_virbox_status_sdk() {
  if [[ ! -f "$virbox_status_sdk_archive" ]]; then
    log "Virbox status SDK archive not found; license gate will fail closed until configured: $virbox_status_sdk_archive"
    return 0
  fi

  log "installing Virbox status SDK"
  sudo mkdir -p "$virbox_status_dir"
  sudo tar -xzf "$virbox_status_sdk_archive" -C "$virbox_status_dir"
  sudo chown -R root:clawtrade "$virbox_status_dir"
  sudo chmod 0755 "$virbox_status_dir" "$virbox_status_command"
  [[ -f "${virbox_status_dir}/libslm_control.so" ]] && sudo chmod 0755 "${virbox_status_dir}/libslm_control.so"
  virbox_status_installed=1
}

write_runtime_env() {
  local gateway_port="$1"
  local shared_env="${install_root}/shared/config/runtime.env"
  local current_env="${install_root}/current/.runtime/dev-services/runtime.env"

  sudo mkdir -p "$(dirname "$shared_env")" "$(dirname "$current_env")" "${install_root}/shared/data/runtime-overlay/normalized"
  sudo tee "$shared_env" >/dev/null <<EOF
DATA_GATEWAY_MONGODB_URI=${DATA_GATEWAY_MONGODB_URI:-mongodb://127.0.0.1:27017}
DATA_GATEWAY_MONGODB_DATABASE=${DATA_GATEWAY_MONGODB_DATABASE:-claw_trade}
DATA_GATEWAY_SEED_MONGODB_URI=${DATA_GATEWAY_SEED_MONGODB_URI:-mongodb://127.0.0.1:27017}
DATA_GATEWAY_SEED_MONGODB_DATABASE=${DATA_GATEWAY_SEED_MONGODB_DATABASE:-claw_trade_factory_seed}
DATA_GATEWAY_COLUMNAR_ROOT=${DATA_GATEWAY_COLUMNAR_ROOT:-${install_root}/shared/data/runtime-overlay/normalized}
OPENCLAW_GATEWAY_PORT=${gateway_port}
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:${gateway_port}
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENVIKING_BASE_URL=http://127.0.0.1:1933
CLAW_TRADE_LICENSE_REQUIRED=${CLAW_TRADE_LICENSE_REQUIRED:-1}
EOF
  if [[ "$virbox_status_installed" == 1 ]]; then
    sudo tee -a "$shared_env" >/dev/null <<EOF
CLAW_TRADE_VIRBOX_STATUS_COMMAND=${virbox_status_command}
VIRBOX_LICENSE_ID=${virbox_license_id}
EOF
  fi
  sudo cp "$shared_env" "$current_env"
  sudo chown "$(id -un):$(id -gn)" "$shared_env" "$current_env"
  sudo chmod 0644 "$shared_env" "$current_env"
}

start_control() {
  local runtime_env="$1" control_pid gateway_port="$2"
  log "starting runtime control"
  stop_control
  rm -f "$control_log"
  setsid nohup bash -c 'set -euo pipefail; root="$1"; runtime_env="$2"; cd "${root}/current"; set -a; . "${runtime_env}"; set +a; export CLAW_TRADE_HOME="${root}"; exec "${root}/current/bin/claw-trade-control"' bash "$install_root" "$runtime_env" >"$control_log" 2>&1 < /dev/null &
  echo "$!" >"$control_pid_file"
  control_pid="$(cat "$control_pid_file")"

  for _ in $(seq 1 120); do
    if curl -fsS http://127.0.0.1:1933/health >/dev/null 2>&1 || curl -fsS http://127.0.0.1:1933/healthz >/dev/null 2>&1; then
      break
    fi
    ps -p "$control_pid" >/dev/null 2>&1 || { tail_log "$control_log"; fail "runtime control exited before OpenViking was ready"; }
    sleep 1
  done
  wait_http_any 90 "http://127.0.0.1:${gateway_port}/health" || { tail_log "$control_log"; fail "OpenClaw gateway did not become ready"; }
}

start_ui() {
  local runtime_env="$1" ui_pid ui_ready=0
  log "starting UI"
  stop_ui
  rm -f "$ui_log"
  setsid nohup bash -c 'set -euo pipefail; root="$1"; runtime_env="$2"; cd "${root}/current"; set -a; . "${runtime_env}"; set +a; export CLAW_TRADE_HOME="${root}"; exec "${root}/current/bin/claw-trade-ui"' bash "$install_root" "$runtime_env" >"$ui_log" 2>&1 < /dev/null &
  echo "$!" >"$ui_pid_file"
  ui_pid="$(cat "$ui_pid_file")"

  for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:5175/ >/dev/null; then
      ui_ready=1
      break
    fi
    ps -p "$ui_pid" >/dev/null 2>&1 || { tail_log "$ui_log"; fail "UI process exited before responding"; }
    sleep 1
  done
  [[ "$ui_ready" == 1 ]] || { tail_log "$ui_log"; fail "UI did not respond on 127.0.0.1:5175 within 90s"; }
}

if [[ -n "$package" ]]; then
  install_package
elif [[ -x "${install_root}/current/bin/claw-trade-control" ]]; then
  log "package missing; using existing current release: ${install_root}/current"
else
  fail "no package supplied and ${install_root}/current is not installed"
fi
install_virbox_status_sdk

if [[ "${RESTORE_HISTORY:-1}" == 1 ]]; then
  ensure_mongodb
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -x "${script_dir}/restore-history-data.sh" ]]; then
    bash "${script_dir}/restore-history-data.sh"
  fi
fi

"${install_root}/current/bin/claw-trade-preflight"
gateway_port="$(select_openclaw_gateway_port)"
runtime_env="${install_root}/shared/config/runtime.env"
write_runtime_env "$gateway_port"
start_control "$runtime_env" "$gateway_port"
start_ui "$runtime_env"

cat <<EOF
[OK] claw-trade installed
Current: ${install_root}/current
UI: http://$(hostname -I | awk '{print $1}'):5175/
Log: ${ui_log}
Control log: ${control_log}
EOF
