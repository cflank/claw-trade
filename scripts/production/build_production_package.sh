#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${CLAW_TRADE_VERSION:-$(date +%Y%m%d%H%M%S)}"
BUILD_DIR="${ROOT}/.runtime/production-build/${VERSION}"
RELEASE_DIR="${BUILD_DIR}/claw-trade-${VERSION}"
ARCHIVE="${ROOT}/.runtime/production-build/claw-trade-${VERSION}.tar"
PYTHON_BIN="${PYTHON_BIN:-python3}"

require_file() {
  local path="$1"
  local message="$2"
  if [[ ! -f "${path}" ]]; then
    echo "${message}: ${path}" >&2
    exit 1
  fi
}

require_dir() {
  local path="$1"
  local message="$2"
  if [[ ! -d "${path}" ]]; then
    echo "${message}: ${path}" >&2
    exit 1
  fi
}

require_file "${ROOT}/third_party/openclaw/openclaw.mjs" "OpenClaw launcher missing; initialize the submodule first"
require_dir "${ROOT}/third_party/openclaw/dist" "OpenClaw dist missing; build OpenClaw first"
require_dir "${ROOT}/packaging/production/systemd" "production systemd templates missing"
require_dir "${ROOT}/packaging/production/kiosk" "production kiosk templates missing"

rm -rf "${BUILD_DIR}"
mkdir -p "${RELEASE_DIR}/app" "${RELEASE_DIR}/web" "${RELEASE_DIR}/runtime/bin" "${RELEASE_DIR}/bin"

cd "${ROOT}/web/research-ui"
if [[ -n "${VITE_BIN:-}" ]]; then
  "${VITE_BIN}" build
else
  pnpm build
fi

cd "${ROOT}"
"${PYTHON_BIN}" -m compileall -q src/claw_trade
"${PYTHON_BIN}" -m pip wheel . -w "${BUILD_DIR}/wheels"

"${PYTHON_BIN}" -m venv "${RELEASE_DIR}/runtime/python"
"${RELEASE_DIR}/runtime/python/bin/python" -m pip install --no-index --find-links "${BUILD_DIR}/wheels" claw-trade
find "${RELEASE_DIR}/runtime/python" -type d \( -name tests -o -name docs \) -prune -exec rm -rf {} +

cp -a web/research-ui/dist "${RELEASE_DIR}/web/dist"
cp -a packaging/production/bin/. "${RELEASE_DIR}/bin/"
cp -a packaging/production/runtime/claw-trade-control-runtime "${RELEASE_DIR}/runtime/bin/claw-trade-control-runtime"
cp -a packaging/production/systemd "${RELEASE_DIR}/runtime/systemd"
cp -a packaging/production/kiosk "${RELEASE_DIR}/runtime/kiosk"
mkdir -p "${RELEASE_DIR}/runtime/openclaw"
cp -a third_party/openclaw/openclaw.mjs "${RELEASE_DIR}/runtime/openclaw/openclaw.mjs"
cp -a third_party/openclaw/dist "${RELEASE_DIR}/runtime/openclaw/dist"

tar -C "${BUILD_DIR}" -cf "${ARCHIVE}" "claw-trade-${VERSION}"
uv run python scripts/production/audit_production_package.py "${ARCHIVE}"
echo "${ARCHIVE}"
