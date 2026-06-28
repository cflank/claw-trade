#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SDK_API_DIR="${1:-${VIRBOX_SDK_API_DIR:-/mnt/c/Program Files (x86)/senseshield/sdk/API}}"
SDK_C_DIR="${SDK_API_DIR}/linux/C"
OUT="${2:-${VIRBOX_STATUS_SDK_OUT:-${ROOT}/.runtime/virbox-status-sdk/virbox_status_sdk}}"
OUT_DIR="$(dirname "${OUT}")"

[[ -d "${SDK_C_DIR}" ]] || {
  echo "Virbox Linux C SDK not found: ${SDK_C_DIR}" >&2
  exit 1
}

mkdir -p "${OUT_DIR}"

gcc \
  "${ROOT}/techlab/virbox_probe/virbox_status_sdk.c" \
  "${SDK_C_DIR}/samples/lib/cJSON/cJSON.c" \
  -I"${SDK_C_DIR}/include" \
  -I"${SDK_C_DIR}/samples/lib/cJSON" \
  -L"${SDK_C_DIR}/lib64" \
  -lslm_control \
  -lm \
  -Wl,-rpath,'$ORIGIN' \
  -o "${OUT}"

cp -f "${SDK_C_DIR}/lib64/libslm_control.so" "${OUT_DIR}/"
chmod 0755 "${OUT}" "${OUT_DIR}/libslm_control.so"
printf '%s\n' "${OUT}"
