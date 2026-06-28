#!/usr/bin/env bash
set -euo pipefail

install_root="${CLAW_TRADE_INSTALL_ROOT:-/opt/claw-trade}"
purge_shared=0
ui_pid_file="${CLAW_TRADE_UI_PID_FILE:-/tmp/claw-trade-ui.pid}"

if [[ "${1:-}" == "--purge-shared" ]]; then
  purge_shared=1
fi

log() {
  printf '[INFO] %s\n' "$*"
}

case "${install_root}" in
  ""|"/"|"/opt"|"/opt/"|"/usr"|"/usr/"|"/home"|"/home/")
    printf '[ERROR] unsafe CLAW_TRADE_INSTALL_ROOT: %s\n' "${install_root}" >&2
    exit 1
    ;;
  /opt/claw-trade|/opt/claw-trade/*) ;;
  *)
    printf '[ERROR] refusing to uninstall outside /opt/claw-trade: %s\n' "${install_root}" >&2
    exit 1
    ;;
esac

stop_ui() {
  if [[ -f "${ui_pid_file}" ]]; then
    pid="$(cat "${ui_pid_file}" 2>/dev/null || true)"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && ps -p "${pid}" >/dev/null 2>&1; then
      kill "${pid}" 2>/dev/null || true
      sleep 2
    fi
    rm -f "${ui_pid_file}"
  fi
  pkill -f 'python3.12 -m claw_trade.web.app' 2>/dev/null || true
}

log "stopping claw-trade UI"
stop_ui

if [[ -L "${install_root}/current" || -e "${install_root}/current" ]]; then
  log "removing current symlink"
  sudo rm -f "${install_root}/current"
fi

if [[ -L "${install_root}/rescue-current" || -e "${install_root}/rescue-current" ]]; then
  log "removing rescue-current symlink"
  sudo rm -f "${install_root}/rescue-current"
fi

if [[ -d "${install_root}/releases" ]]; then
  log "removing releases"
  sudo rm -rf "${install_root}/releases"
fi

if [[ "${purge_shared}" == "1" && -d "${install_root}/shared" ]]; then
  log "removing shared data"
  sudo rm -rf "${install_root}/shared"
fi

rm -f /tmp/claw-trade-ui.log "${ui_pid_file}"
sudo rm -f /etc/sudoers.d/claw-trade-update
sudo rm -f /usr/local/lib/claw-trade/claw-trade-apply-update

cat <<EOF
[OK] claw-trade uninstalled
Removed:
  ${install_root}/current
  ${install_root}/rescue-current
  ${install_root}/releases
  /usr/local/lib/claw-trade/claw-trade-apply-update
Shared data removed: ${purge_shared}
EOF
