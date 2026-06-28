#!/usr/bin/env bash
set -euo pipefail

install_root="${INSTALL_ROOT:-/opt/claw-trade}"
kill_extra_openclaw=0
purge_shared=0

for arg in "$@"; do
  case "$arg" in
    --kill-extra-openclaw) kill_extra_openclaw=1 ;;
    --purge-shared) purge_shared=1 ;;
    *)
      printf '[ERROR] unknown option: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done

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

stop_pid_file /tmp/claw-trade-ui.pid
stop_pid_file /tmp/claw-trade-control.pid
stop_pid_file "${install_root}/shared/run/mongod.pid"

if [[ -d "${install_root}/current/.runtime/dev-services/pids" ]]; then
  for file in "${install_root}/current/.runtime/dev-services/pids"/*.pid; do
    [[ -e "$file" ]] && stop_pid_file "$file"
  done
fi

pkill -f -- "-m claw_trade.web.app" 2>/dev/null || sudo pkill -f -- "-m claw_trade.web.app" 2>/dev/null || true
pkill -f "${install_root}/current/runtime/bin/claw-trade-control-runtime" 2>/dev/null || sudo pkill -f "${install_root}/current/runtime/bin/claw-trade-control-runtime" 2>/dev/null || true
pkill -f "${install_root}/current/.runtime" 2>/dev/null || sudo pkill -f "${install_root}/current/.runtime" 2>/dev/null || true

if [[ "$kill_extra_openclaw" == 1 ]]; then
  pkill -f openclaw 2>/dev/null || sudo pkill -f openclaw 2>/dev/null || true
else
  extras="$(ps -eo pid=,cmd= | awk -v root="${install_root}" '/openclaw/ && index($0, root)==0 {print}' || true)"
  [[ -z "$extras" ]] || printf '[WARN] external OpenClaw processes remain; rerun with --kill-extra-openclaw to stop them.\n%s\n' "$extras" >&2
fi

sudo rm -f "${install_root}/current" 2>/dev/null || true
sudo rm -rf "${install_root}/releases"/* 2>/dev/null || true
[[ "$purge_shared" == 1 ]] && sudo rm -rf "${install_root}/shared" 2>/dev/null || true
rm -f /tmp/claw-trade-ui.log /tmp/claw-trade-control.log /tmp/claw-trade-ui.pid /tmp/claw-trade-control.pid

printf '[OK] claw-trade uninstalled\nExtra OpenClaw kill enabled: %s\n' "$kill_extra_openclaw"
