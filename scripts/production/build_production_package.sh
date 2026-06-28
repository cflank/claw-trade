#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${CLAW_TRADE_VERSION:-$(date +%Y%m%d%H%M%S)}"
BUILD_DIR="${ROOT}/.runtime/production-build/${VERSION}"
RELEASE_DIR="${BUILD_DIR}/claw-trade-${VERSION}"
ARCHIVE="${ROOT}/.runtime/production-build/claw-trade-${VERSION}.tar"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OPENCLAW_NODE_MODULES_ROOT="${OPENCLAW_NODE_MODULES_ROOT:-${ROOT}/third_party/openclaw/node_modules}"
OPENCLAW_NODE_MODULES_PARENT="$(cd "$(dirname "${OPENCLAW_NODE_MODULES_ROOT}")" && pwd)"

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
require_dir "${OPENCLAW_NODE_MODULES_ROOT}" "OpenClaw node_modules missing; install OpenClaw JavaScript dependencies first"
require_dir "${ROOT}/packaging/production/systemd" "production systemd templates missing"
require_dir "${ROOT}/packaging/production/kiosk" "production kiosk templates missing"
require_dir "${ROOT}/agents" "agent runtime assets missing"
require_dir "${ROOT}/openclaw_plugins" "OpenClaw plugin runtime assets missing"

rm -rf "${BUILD_DIR}"
mkdir -p "${RELEASE_DIR}/app" "${RELEASE_DIR}/web" "${RELEASE_DIR}/runtime/bin" "${RELEASE_DIR}/runtime/assets" "${RELEASE_DIR}/bin"

cd "${ROOT}/web/research-ui"
if [[ -n "${VITE_BIN:-}" ]]; then
  "${VITE_BIN}" build
else
  pnpm build
fi

cd "${ROOT}"
"${PYTHON_BIN}" -m compileall -q src/claw_trade
if command -v uv >/dev/null 2>&1; then
  uv build --wheel --out-dir "${BUILD_DIR}/wheels" .
else
  "${PYTHON_BIN}" -m pip wheel . -w "${BUILD_DIR}/wheels"
fi

"${PYTHON_BIN}" -m venv "${RELEASE_DIR}/runtime/python"
"${RELEASE_DIR}/runtime/python/bin/python" -m pip install --no-index --find-links "${BUILD_DIR}/wheels" claw-trade
find "${RELEASE_DIR}/runtime/python" -type d \( -name tests -o -name docs \) -prune -exec rm -rf {} +

cp -a web/research-ui/dist "${RELEASE_DIR}/web/dist"
cp -a packaging/production/bin/. "${RELEASE_DIR}/bin/"
cp -a packaging/production/runtime/claw-trade-control-runtime "${RELEASE_DIR}/runtime/bin/claw-trade-control-runtime"
cp -a packaging/production/systemd "${RELEASE_DIR}/runtime/systemd"
cp -a packaging/production/kiosk "${RELEASE_DIR}/runtime/kiosk"
tar --exclude='*/prompt-review.yaml' --exclude='*/__pycache__' --exclude='*.pyc' \
  -C "${ROOT}" -cf "${RELEASE_DIR}/runtime/assets/agents.tar" agents
tar --exclude='*/node_modules' --exclude='*/__pycache__' --exclude='*.pyc' \
  -C "${ROOT}" -cf "${RELEASE_DIR}/runtime/assets/openclaw_plugins.tar" openclaw_plugins
mkdir -p "${RELEASE_DIR}/runtime/openclaw"
cp -a third_party/openclaw/openclaw.mjs "${RELEASE_DIR}/runtime/openclaw/openclaw.mjs"
cp -a third_party/openclaw/dist "${RELEASE_DIR}/runtime/openclaw/dist"
node - "${ROOT}" "${OPENCLAW_NODE_MODULES_ROOT}" "${BUILD_DIR}/openclaw-runtime-node-deps.list" "${BUILD_DIR}/openclaw-runtime-node-deps-missing.txt" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

const root = process.argv[2];
const nodeModulesRoot = process.argv[3];
const listPath = process.argv[4];
const missingPath = process.argv[5];
const openclawRoot = path.join(root, "third_party", "openclaw");
const base = path.resolve(nodeModulesRoot);
const rootPkg = JSON.parse(fs.readFileSync(path.join(openclawRoot, "package.json"), "utf8"));
const queue = [];
const topLinks = new Set();
const entries = new Set();
const missing = new Set();
const seenRealPkgDirs = new Set();

function pkgRel(name) {
  return name.startsWith("@") ? name.split("/").slice(0, 2).join("/") : name.split("/")[0];
}

function candidatePkgDir(fromDir, dep) {
  const rel = pkgRel(dep);
  const local = path.join(fromDir, "node_modules", rel);
  if (fs.existsSync(path.join(local, "package.json")) || fs.existsSync(local)) return local;
  const top = path.join(base, rel);
  if (fs.existsSync(path.join(top, "package.json")) || fs.existsSync(top)) return top;
  return null;
}

function pnpmEntry(realPkgDir) {
  const marker = `${base}/.pnpm/`;
  if (realPkgDir.startsWith(marker)) {
    return path.join(base, ".pnpm", realPkgDir.slice(marker.length).split("/")[0]);
  }
  return realPkgDir;
}

function addPkgDir(pkgDir, reason) {
  const pkgJson = path.join(pkgDir, "package.json");
  if (!fs.existsSync(pkgJson)) {
    missing.add(`${reason}:${pkgDir}`);
    return;
  }
  const realPkgDir = fs.realpathSync(path.dirname(pkgJson));
  if (seenRealPkgDirs.has(realPkgDir)) return;
  seenRealPkgDirs.add(realPkgDir);
  const entry = pnpmEntry(realPkgDir);
  if (entry.startsWith(base)) entries.add(entry);
  queue.push({ pkgDir: path.dirname(pkgJson), realPkgDir, entry });
}

for (const dep of Object.keys(rootPkg.dependencies || {}).sort()) {
  const rel = pkgRel(dep);
  topLinks.add(`node_modules/${rel}`);
  const pkgDir = path.join(base, rel);
  if (fs.existsSync(pkgDir)) addPkgDir(pkgDir, "root");
  else missing.add(`root:${rel}`);
}

while (queue.length) {
  const item = queue.shift();
  const pkgJson = path.join(item.realPkgDir, "package.json");
  let pkg = {};
  try {
    pkg = JSON.parse(fs.readFileSync(pkgJson, "utf8"));
  } catch {}
  for (const section of ["dependencies", "optionalDependencies"]) {
    for (const dep of Object.keys(pkg[section] || {})) {
      const pkgDir = candidatePkgDir(item.realPkgDir, dep) || candidatePkgDir(item.pkgDir, dep);
      if (pkgDir) addPkgDir(pkgDir, pkg.name || item.realPkgDir);
      else missing.add(`${pkg.name || item.realPkgDir}:${dep}`);
    }
  }
  const nested = path.join(item.realPkgDir, "node_modules");
  if (!fs.existsSync(nested)) continue;
  for (const scopeOrName of fs.readdirSync(nested)) {
    if (scopeOrName === ".bin") continue;
    const first = path.join(nested, scopeOrName);
    let stat;
    try {
      stat = fs.lstatSync(first);
    } catch {
      continue;
    }
    if (scopeOrName.startsWith("@") && stat.isDirectory() && !stat.isSymbolicLink()) {
      for (const name of fs.readdirSync(first)) {
        const candidate = path.join(first, name);
        if (fs.existsSync(path.join(candidate, "package.json")) || fs.existsSync(candidate)) {
          addPkgDir(candidate, "nested");
        }
      }
    } else if (stat.isSymbolicLink() || fs.existsSync(path.join(first, "package.json"))) {
      addPkgDir(first, "nested");
    }
  }
}

const include = new Set(topLinks);
for (const entry of entries) {
  include.add(`node_modules/${path.relative(base, entry)}`);
}

fs.writeFileSync(listPath, `${[...include].sort().join("\n")}\n`);
fs.writeFileSync(missingPath, `${[...missing].sort().join("\n")}\n`);
console.log(`OpenClaw runtime node deps: ${seenRealPkgDirs.size} packages, ${include.size} archive paths`);
if (missing.size) {
  console.log(`OpenClaw optional/platform deps not present: ${missing.size}; see ${missingPath}`);
}
NODE
tar -C "${OPENCLAW_NODE_MODULES_PARENT}" -cf "${BUILD_DIR}/openclaw-runtime-node-deps.tar" -T "${BUILD_DIR}/openclaw-runtime-node-deps.list"
tar -C "${RELEASE_DIR}/runtime/openclaw" -xf "${BUILD_DIR}/openclaw-runtime-node-deps.tar"

tar -C "${BUILD_DIR}" -cf "${ARCHIVE}" "claw-trade-${VERSION}"
uv run python scripts/production/audit_production_package.py "${ARCHIVE}"
echo "${ARCHIVE}"
