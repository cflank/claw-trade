#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/.runtime/mongodb"
DOWNLOAD_DIR="${RUNTIME_DIR}/downloads"
DATA_DIR="${RUNTIME_DIR}/data"
LOG_DIR="${RUNTIME_DIR}/log"
RUN_DIR="${RUNTIME_DIR}/run"

MONGO_VERSION="${MONGO_VERSION:-8.0.12}"
MONGO_PLATFORM="${MONGO_PLATFORM:-ubuntu2404}"
MONGO_ARCHIVE="mongodb-linux-x86_64-${MONGO_PLATFORM}-${MONGO_VERSION}.tgz"
MONGO_URL="${MONGO_URL:-https://fastdl.mongodb.org/linux/${MONGO_ARCHIVE}}"
MONGO_EXTRACTED_DIR="${RUNTIME_DIR}/mongodb-linux-x86_64-${MONGO_PLATFORM}-${MONGO_VERSION}"
MONGO_CURRENT_DIR="${RUNTIME_DIR}/current"
MONGOD_BIN="${MONGO_CURRENT_DIR}/bin/mongod"

CN_A_MONGODB_BIND_IP="${CN_A_MONGODB_BIND_IP:-127.0.0.1}"
CN_A_MONGODB_PORT="${CN_A_MONGODB_PORT:-27017}"
CN_A_MONGODB_URI="${CN_A_MONGODB_URI:-mongodb://${CN_A_MONGODB_BIND_IP}:${CN_A_MONGODB_PORT}}"
CN_A_MONGODB_DATABASE="${CN_A_MONGODB_DATABASE:-claw_trade}"
CN_A_MONGODB_CACHE_COLLECTION="${CN_A_MONGODB_CACHE_COLLECTION:-cn_a_fundamental_cache}"
CLAW_TRADE_MONGODB_CACHE_SIZE_GB="${CLAW_TRADE_MONGODB_CACHE_SIZE_GB:-1.0}"

PID_FILE="${RUN_DIR}/mongod.pid"
LOG_FILE="${LOG_DIR}/mongod.log"

log_info() {
  printf '[INFO] %s\n' "$*"
}

log_error() {
  printf '[ERROR] %s\n' "$*" >&2
}

pid_is_alive() {
  local pid="$1"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

system_memory_quarter_gb() {
  awk '
    /MemTotal/ {
      gb = $2 / 1024 / 1024 / 4
      if (gb < 0.25) {
        gb = 0.25
      }
      printf "%.2f", gb
      found = 1
    }
    END {
      if (!found) {
        printf "1.00"
      }
    }
  ' /proc/meminfo 2>/dev/null || printf '1.00'
}

mongodb_cache_size_gb() {
  local requested="$1"
  local quarter
  quarter="$(system_memory_quarter_gb)"
  awk -v requested="${requested}" -v quarter="${quarter}" '
    BEGIN {
      value = requested + 0
      cap = quarter + 0
      if (value <= 0) {
        value = 1.0
      }
      if (value > cap) {
        value = cap
      }
      if (value < 0.25) {
        value = 0.25
      }
      printf "%.2f", value
    }
  '
}

ensure_mongodb_binary() {
  mkdir -p "${DOWNLOAD_DIR}" "${DATA_DIR}" "${LOG_DIR}" "${RUN_DIR}"

  if [[ -x "${MONGOD_BIN}" ]]; then
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    log_error "curl is required to download MongoDB"
    return 1
  fi
  if ! command -v tar >/dev/null 2>&1; then
    log_error "tar is required to extract MongoDB"
    return 1
  fi

  log_info "Downloading MongoDB ${MONGO_VERSION} for ${MONGO_PLATFORM}"
  curl -fL -o "${DOWNLOAD_DIR}/${MONGO_ARCHIVE}" "${MONGO_URL}"
  tar -xzf "${DOWNLOAD_DIR}/${MONGO_ARCHIVE}" -C "${RUNTIME_DIR}"
  ln -sfn "$(basename "${MONGO_EXTRACTED_DIR}")" "${MONGO_CURRENT_DIR}"
}

main() {
  ensure_mongodb_binary

  if [[ -f "${PID_FILE}" ]]; then
    pid="$(tr -d '[:space:]' < "${PID_FILE}" || true)"
    if pid_is_alive "${pid}"; then
      log_info "MongoDB already running: pid=${pid}, uri=${CN_A_MONGODB_URI}"
      return 0
    fi
  fi

  local cache_size_gb
  cache_size_gb="$(mongodb_cache_size_gb "${CLAW_TRADE_MONGODB_CACHE_SIZE_GB}")"

  log_info "Starting MongoDB on ${CN_A_MONGODB_URI}"
  log_info "MongoDB WiredTiger cache limit: ${cache_size_gb}GB"
  "${MONGOD_BIN}" \
    --dbpath "${DATA_DIR}" \
    --logpath "${LOG_FILE}" \
    --pidfilepath "${PID_FILE}" \
    --bind_ip "${CN_A_MONGODB_BIND_IP}" \
    --port "${CN_A_MONGODB_PORT}" \
    --wiredTigerCacheSizeGB "${cache_size_gb}" \
    --fork

  log_info "MongoDB started"
  log_info "Set these environment variables for claw-trade:"
  printf 'export CN_A_MONGODB_URI=%q\n' "${CN_A_MONGODB_URI}"
  printf 'export CN_A_MONGODB_DATABASE=%q\n' "${CN_A_MONGODB_DATABASE}"
  printf 'export CN_A_MONGODB_CACHE_COLLECTION=%q\n' "${CN_A_MONGODB_CACHE_COLLECTION}"
  printf 'export CLAW_TRADE_MONGODB_CACHE_SIZE_GB=%q\n' "${cache_size_gb}"
}

main "$@"
