#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/dist/production}"
WORK_DIR="${WORK_DIR:-${ROOT_DIR}/.runtime/production-package}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
BUILD_FRONTEND="${BUILD_FRONTEND:-1}"
OPENCLAW_WEIXIN_PLUGIN_SPEC="${OPENCLAW_WEIXIN_PLUGIN_SPEC:-@tencent-weixin/openclaw-weixin@2.4.4}"

version="$("${PYTHON_BIN}" - <<'PY' "${ROOT_DIR}/pyproject.toml"
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"]["version"])
PY
)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
package_name="claw-trade-production-${version}-${stamp}"
package_root="${WORK_DIR}/${package_name}"
archive_path="${OUT_DIR}/${package_name}.tar.gz"

log() {
  printf '[INFO] %s\n' "$*"
}

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

require_path() {
  test -e "$1" || fail "missing required path: $1"
}

prepare_openclaw_plugin_assets() {
  local plugins_root="$1"
  local npm_root="${WORK_DIR}/openclaw-weixin-npm"

  rm -rf "${npm_root}"
  mkdir -p "${npm_root}"
  log "installing bundled OpenClaw Weixin plugin: ${OPENCLAW_WEIXIN_PLUGIN_SPEC}"
  npm install \
    --prefix "${npm_root}" \
    --omit=dev \
    --legacy-peer-deps \
    --ignore-scripts \
    --package-lock=false \
    "${OPENCLAW_WEIXIN_PLUGIN_SPEC}"
  mkdir -p "${plugins_root}/node_modules"
  cp -a "${npm_root}/node_modules/." "${plugins_root}/node_modules/"
  require_path "${plugins_root}/node_modules/@tencent-weixin/openclaw-weixin/openclaw.plugin.json"
  require_path "${plugins_root}/node_modules/@tencent-weixin/openclaw-weixin/dist/index.js"
}

cd "${ROOT_DIR}"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "missing ${PYTHON_BIN}"
command -v tar >/dev/null 2>&1 || fail "missing tar"

if [[ "${BUILD_FRONTEND}" == "1" ]]; then
  log "building web/research-ui dist"
  pnpm --dir web/research-ui build
fi

require_path "web/research-ui/dist/index.html"
require_path "third_party/openclaw/dist"
require_path "third_party/openclaw/openclaw.mjs"
require_path "third_party/openclaw/node_modules"
require_path ".venv/lib/python3.12/site-packages"
resolved_python_bin="$(command -v "${PYTHON_BIN}")"
[[ -n "${resolved_python_bin}" ]] || fail "cannot resolve ${PYTHON_BIN}"

rm -rf "${package_root}"
mkdir -p \
  "${package_root}/app/python" \
  "${package_root}/runtime" \
  "${package_root}/runtime/assets" \
  "${package_root}/runtime/openclaw" \
  "${package_root}/web" \
  "${package_root}/data" \
  "${package_root}/data/crypto-history-full" \
  "${package_root}/scripts/crypto" \
  "${package_root}/scripts/selection" \
  "${OUT_DIR}"

log "copying runtime assets"
cp -a packaging/production/bin "${package_root}/bin"
cp -a packaging/production/systemd "${package_root}/systemd"
cp -a packaging/production/sudoers "${package_root}/sudoers"
cp -a packaging/production/root-helper "${package_root}/root-helper"
cp -a packaging/production/kiosk "${package_root}/kiosk"
cp -a packaging/production/runtime/. "${package_root}/runtime/"
cp -a packaging/production/README_FACTORY_TEST.md "${package_root}/README_FACTORY_TEST.md"
cp -a agents "${package_root}/agents"
cp -a openclaw_plugins "${package_root}/openclaw_plugins"
prepare_openclaw_plugin_assets "${package_root}/openclaw_plugins"
tar --exclude='agents/*/prompt-review.yaml' -C "${package_root}" -cf "${package_root}/runtime/assets/agents.tar" agents
tar -C "${package_root}" -cf "${package_root}/runtime/assets/openclaw_plugins.tar" openclaw_plugins
rm -rf "${package_root}/agents" "${package_root}/openclaw_plugins"
cp -a web/research-ui/dist "${package_root}/web/dist"
cp -a third_party/openclaw/dist "${package_root}/runtime/openclaw/dist"
cp -a third_party/openclaw/openclaw.mjs "${package_root}/runtime/openclaw/openclaw.mjs"
cp -a third_party/openclaw/package.json "${package_root}/runtime/openclaw/package.json"
cp -a third_party/openclaw/LICENSE "${package_root}/runtime/openclaw/LICENSE"
cp -a third_party/openclaw/node_modules "${package_root}/runtime/openclaw/node_modules"
cp -a scripts/openclaw-gateway-rpc-helper.mjs "${package_root}/scripts/openclaw-gateway-rpc-helper.mjs"
cp -a scripts/crypto/download_binance_public_data.py "${package_root}/scripts/crypto/download_binance_public_data.py"
cp -a scripts/crypto/import_crypto_prepackaged_to_mongo.py "${package_root}/scripts/crypto/import_crypto_prepackaged_to_mongo.py"
cp -a scripts/selection/restore_a_share_factory_seed.py "${package_root}/scripts/selection/restore_a_share_factory_seed.py"
require_path "data/crypto-history-full/normalized-columnar-usdt-only"
cp -a data/crypto-history-full/normalized-columnar-usdt-only "${package_root}/data/crypto-history-full/normalized-columnar-usdt-only"

shopt -s nullglob
current_seed_packages=(data/current-seed-*.tar)
shopt -u nullglob
if (( ${#current_seed_packages[@]} > 0 )); then
  current_seed="${current_seed_packages[$((${#current_seed_packages[@]} - 1))]}"
  cp -a "${current_seed}" "${package_root}/data/"
  cp -a "${current_seed}.sha256" "${package_root}/data/" 2>/dev/null || true
  if [[ ! -f "${package_root}/data/$(basename "${current_seed}").sha256" ]]; then
    (cd "${package_root}/data" && sha256sum "$(basename "${current_seed}")" > "$(basename "${current_seed}").sha256")
  fi
else
  fail "missing data/current-seed-*.tar; production package requires the current CN_A + CRYPTO seed package"
fi

if [[ -x .runtime/mongodb/current/bin/mongod ]]; then
  log "copying local MongoDB runtime"
  mkdir -p "${package_root}/.runtime/mongodb"
  cp -aL .runtime/mongodb/current "${package_root}/.runtime/mongodb/current"
fi

log "copying Python site-packages"
mkdir -p "${package_root}/runtime/python/bin"
cp -aL "${resolved_python_bin}" "${package_root}/runtime/python/bin/python"
chmod 0755 "${package_root}/runtime/python/bin/python"
cp -a .venv/lib/python3.12/site-packages "${package_root}/runtime/python-site-packages"
rm -f "${package_root}"/runtime/python-site-packages/*claw_trade*.pth
rm -rf "${package_root}/runtime/python-site-packages/claw_trade-0.1.0.dist-info"
find "${package_root}/runtime/python-site-packages" \
  \( -type d \( -name tests -o -name test -o -name docs \) \) \
  -prune -exec rm -rf {} +
find "${package_root}/runtime/python-site-packages" \
  -type d -name __pycache__ -prune -exec rm -rf {} +

log "building sourceless claw_trade app tree"
"${PYTHON_BIN}" - <<'PY' "${ROOT_DIR}/src/claw_trade" "${package_root}/app/python/claw_trade"
from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
if dst.exists():
    shutil.rmtree(dst)
dst.mkdir(parents=True)

for path in src.rglob("*"):
    rel = path.relative_to(src)
    if "__pycache__" in rel.parts:
        continue
    target = dst / rel
    if path.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".py":
        py_compile.compile(str(path), cfile=str(target.with_suffix(".pyc")), doraise=True, optimize=2)
    else:
        shutil.copy2(path, target)
PY

log "preparing runtime scripts"
cp -a scripts/start-local-mongodb.sh "${package_root}/runtime/start-local-mongodb"
chmod +x "${package_root}/bin/"* "${package_root}/runtime/"*
find "${package_root}" -type d -name __pycache__ -prune -exec rm -rf {} +

log "running production preflight"
"${package_root}/bin/claw-trade-preflight"

log "auditing package root"
"${PYTHON_BIN}" scripts/production/audit_production_package.py "${package_root}"

log "creating archive ${archive_path}"
tar --dereference --hard-dereference -C "${WORK_DIR}" -czf "${archive_path}" "${package_name}"
"${PYTHON_BIN}" scripts/production/validate_production_archive.py "${archive_path}"
"${PYTHON_BIN}" scripts/production/audit_production_package.py "${archive_path}"
(
  cd "${OUT_DIR}"
  sha256sum "$(basename "${archive_path}")" > "$(basename "${archive_path}").sha256"
)

log "done"
printf '%s\n' "${archive_path}"
