#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${ROOT_DIR}/.runtime/mongodb/run/mongod.pid"

log_info() {
  printf '[INFO] %s\n' "$*"
}

if [[ ! -f "${PID_FILE}" ]]; then
  log_info "MongoDB pid file not found; nothing to stop"
  exit 0
fi

pid="$(tr -d '[:space:]' < "${PID_FILE}" || true)"
if ! [[ "${pid}" =~ ^[0-9]+$ ]]; then
  log_info "MongoDB pid file is invalid; removing it"
  rm -f "${PID_FILE}"
  exit 0
fi

if kill -0 "${pid}" 2>/dev/null; then
  log_info "Stopping MongoDB pid=${pid}"
  kill "${pid}"
  for _ in $(seq 1 50); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${PID_FILE}"
      log_info "MongoDB stopped"
      exit 0
    fi
    sleep 0.1
  done
  log_info "MongoDB did not stop within 5s; leaving pid file for inspection"
  exit 1
fi

rm -f "${PID_FILE}"
log_info "MongoDB process was not running; pid file removed"
