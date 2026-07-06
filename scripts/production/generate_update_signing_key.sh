#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${1:-${ROOT_DIR}}"
PRIVATE_KEY="${OUT_DIR}/update-signing-private.pem"
PUBLIC_KEY="${OUT_DIR}/update-signing-public.pem"

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[INFO] %s\n' "$*"
}

command -v openssl >/dev/null 2>&1 || fail "missing openssl"
mkdir -p "${OUT_DIR}"

if [[ -f "${PRIVATE_KEY}" ]]; then
  log "using existing private key: ${PRIVATE_KEY}"
else
  log "generating private key: ${PRIVATE_KEY}"
  openssl genpkey -algorithm Ed25519 -out "${PRIVATE_KEY}"
fi

chmod 600 "${PRIVATE_KEY}"
log "writing public key: ${PUBLIC_KEY}"
openssl pkey -in "${PRIVATE_KEY}" -pubout -out "${PUBLIC_KEY}"
chmod 0644 "${PUBLIC_KEY}"

ls -lh "${PRIVATE_KEY}" "${PUBLIC_KEY}"
