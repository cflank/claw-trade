#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CONFIG_FILE="${CONFIG_FILE:-${ROOT_DIR}/.runtime/virbox-protected-package.env}"
CONFIG_FILE_EXPLICIT=0

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "--config" ]]; then
    CONFIG_FILE="${args[$((i + 1))]:-}"
    CONFIG_FILE_EXPLICIT=1
  fi
done

if [[ -f "${CONFIG_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${CONFIG_FILE}"
  set +a
elif [[ "${CONFIG_FILE_EXPLICIT}" == "1" ]]; then
  printf '[ERROR] config file not found: %s\n' "${CONFIG_FILE}" >&2
  exit 1
fi

# Editable defaults. Prefer .runtime/virbox-protected-package.env for local changes.
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
INPUT_ARCHIVE="${INPUT_ARCHIVE:-}"
OUTPUT_DIR="${OUTPUT_DIR:-/mnt/d/claw-trade-virbox/delivery-output/virbox-protected-${STAMP}}"
WORK_DIR="${WORK_DIR:-}"
PROTECTED_PYTHON="${PROTECTED_PYTHON:-/mnt/d/claw-trade-virbox/resource-test/protect-python-ds/protected/python}"
PYTHON_SSP="${PYTHON_SSP:-/mnt/d/claw-trade-virbox/resource-test/protect-python-ds/python.ssp}"
PROTECTED_NODE="${PROTECTED_NODE:-/mnt/d/claw-trade-virbox/node-ds-test/protected/node.bin}"
NODE_SSP="${NODE_SSP:-/mnt/d/claw-trade-virbox/node-ds-test/input/node.ssp}"
DSPROTECTOR="${DSPROTECTOR:-/mnt/d/sw/senseshield/sdk/Tool/VirboxProtect/bin/dsprotector_con.exe}"
KEEP_WORK=0

usage() {
  cat <<'EOF'
Usage:
  scripts/production/build_virbox_protected_delivery_package.sh

One-time local config:
  cp scripts/production/virbox-protected-package.env.example .runtime/virbox-protected-package.env
  vim .runtime/virbox-protected-package.env
  scripts/production/build_virbox_protected_delivery_package.sh

Optional overrides:
  --config <env-file>
  --input <claw-trade-production-*.tar.gz>
  --output-dir <dir>

Environment overrides:
  CONFIG_FILE INPUT_ARCHIVE OUTPUT_DIR WORK_DIR PROTECTED_PYTHON PYTHON_SSP
  PROTECTED_NODE NODE_SSP DSPROTECTOR PYTHON_BIN
EOF
}

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[INFO] %s\n' "$*"
}

while (($# > 0)); do
  case "$1" in
    --input)
      INPUT_ARCHIVE="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_FILE="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --work-dir)
      WORK_DIR="${2:-}"
      shift 2
      ;;
    --protected-python)
      PROTECTED_PYTHON="${2:-}"
      shift 2
      ;;
    --python-ssp)
      PYTHON_SSP="${2:-}"
      shift 2
      ;;
    --protected-node)
      PROTECTED_NODE="${2:-}"
      shift 2
      ;;
    --node-ssp)
      NODE_SSP="${2:-}"
      shift 2
      ;;
    --dsprotector)
      DSPROTECTOR="${2:-}"
      shift 2
      ;;
    --keep-work)
      KEEP_WORK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

require_file() {
  local path="$1"
  local label="$2"
  [[ -f "${path}" ]] || fail "missing ${label}: ${path}"
}

require_executable() {
  local path="$1"
  local label="$2"
  [[ -x "${path}" ]] || fail "missing executable ${label}: ${path}"
}

tool_path() {
  local path="$1"
  if [[ "${DSPROTECTOR}" == *.exe && -x "$(command -v wslpath 2>/dev/null)" ]]; then
    wslpath -w "${path}"
  else
    printf '%s\n' "${path}"
  fi
}

encrypt_to() {
  local ssp="$1"
  local src="$2"
  local dst="$3"
  mkdir -p "$(dirname "${dst}")"
  "${DSPROTECTOR}" -s "$(tool_path "${ssp}")" -i "$(tool_path "${src}")" -o "$(tool_path "${dst}")" < /dev/null
}

encrypt_overwrite() {
  local ssp="$1"
  local src="$2"
  local tmp="${src}.dsprotect.$$.tmp"
  rm -f "${tmp}"
  encrypt_to "${ssp}" "${src}" "${tmp}"
  mv -f "${tmp}" "${src}"
}

replace_text() {
  local file="$1"
  local old="$2"
  local new="$3"
  "${PYTHON_BIN}" - "$file" "$old" "$new" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
old = sys.argv[2]
new = sys.argv[3]
text = path.read_text(encoding="utf-8")
if old not in text:
    raise SystemExit(f"missing expected text in {path}: {old}")
path.write_text(text.replace(old, new), encoding="utf-8")
PY
}

require_file "${INPUT_ARCHIVE}" "input production archive"
require_file "${PROTECTED_PYTHON}" "protected Python runtime"
require_file "${PYTHON_SSP}" "Python .ssp"
require_file "${PROTECTED_NODE}" "protected Node runtime"
require_file "${NODE_SSP}" "Node .ssp"
require_executable "${DSPROTECTOR}" "DSProtector CLI"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "missing ${PYTHON_BIN}"
command -v tar >/dev/null 2>&1 || fail "missing tar"
command -v sha256sum >/dev/null 2>&1 || fail "missing sha256sum"

if [[ -z "${INPUT_ARCHIVE}" ]]; then
  shopt -s nullglob
  archives=("${ROOT_DIR}"/dist/production/claw-trade-production-*.tar.gz)
  shopt -u nullglob
  ((${#archives[@]} > 0)) || fail "missing input archive; set INPUT_ARCHIVE in ${CONFIG_FILE} or pass --input"
  INPUT_ARCHIVE="${archives[0]}"
  for candidate in "${archives[@]}"; do
    if [[ "${candidate}" -nt "${INPUT_ARCHIVE}" ]]; then
      INPUT_ARCHIVE="${candidate}"
    fi
  done
  log "using latest input archive: ${INPUT_ARCHIVE}"
fi

release_name="$("${PYTHON_BIN}" "${ROOT_DIR}/scripts/production/validate_production_archive.py" "${INPUT_ARCHIVE}")"
[[ -n "${release_name}" ]] || fail "could not detect release name"

if [[ -z "${WORK_DIR}" ]]; then
  WORK_DIR="${ROOT_DIR}/.runtime/virbox-delivery-build/${release_name}"
fi
release_dir="${WORK_DIR}/${release_name}"
output_archive="${OUTPUT_DIR}/${release_name}.tar.gz"

if [[ "${KEEP_WORK}" != "1" ]]; then
  rm -rf "${WORK_DIR}"
fi
mkdir -p "${WORK_DIR}" "${OUTPUT_DIR}"

log "extracting ${INPUT_ARCHIVE}"
tar -xzf "${INPUT_ARCHIVE}" -C "${WORK_DIR}"
[[ -d "${release_dir}" ]] || fail "release directory missing after extraction: ${release_dir}"

require_file "${release_dir}/runtime/python/bin/python" "release Python runtime"
require_file "${release_dir}/runtime/openclaw/openclaw.mjs" "release OpenClaw entry"
require_file "${release_dir}/runtime/assets/agents.tar" "release agents asset"
require_file "${release_dir}/runtime/assets/openclaw_plugins.tar" "release OpenClaw plugins asset"
require_file "${release_dir}/bin/claw-trade-ui" "release UI launcher"
require_file "${release_dir}/bin/claw-trade-control" "release control launcher"
require_file "${release_dir}/runtime/claw-trade-control-runtime" "release control runtime"

log "replacing Python and Node runtimes"
cp -a "${PROTECTED_PYTHON}" "${release_dir}/runtime/python/bin/python"
chmod 0755 "${release_dir}/runtime/python/bin/python"
cp -a "${PROTECTED_NODE}" "${release_dir}/runtime/node"
chmod 0755 "${release_dir}/runtime/node"

log "encrypting Python bytecode"
py_tmp="${WORK_DIR}/_ds_python_out"
rm -rf "${py_tmp}"
mkdir -p "${py_tmp}"
py_count=0
while IFS= read -r -d '' src; do
  rel="${src#"${release_dir}/"}"
  encrypt_to "${PYTHON_SSP}" "${src}" "${py_tmp}/${rel}"
  py_count=$((py_count + 1))
done < <(find "${release_dir}/app/python/claw_trade" -type f -name '*.pyc' -print0)
((py_count > 0)) || fail "no .pyc files found under app/python/claw_trade"
cp -a "${py_tmp}/app/python/claw_trade/." "${release_dir}/app/python/claw_trade/"

log "encrypting OpenClaw entry"
encrypt_overwrite "${NODE_SSP}" "${release_dir}/runtime/openclaw/openclaw.mjs"

assets_tmp="${WORK_DIR}/_runtime_assets"
rm -rf "${assets_tmp}"
mkdir -p "${assets_tmp}"

log "encrypting agent markdown asset"
tar -C "${assets_tmp}" -xf "${release_dir}/runtime/assets/agents.tar"
agent_count=0
while IFS= read -r -d '' src; do
  encrypt_overwrite "${NODE_SSP}" "${src}"
  agent_count=$((agent_count + 1))
done < <(find "${assets_tmp}/agents" -type f -name '*.md' -print0)
((agent_count > 0)) || fail "no agent markdown files found in agents asset"
if grep -R -a -q 'TradingAgents' "${assets_tmp}/agents"; then
  fail "agent asset still contains plaintext TradingAgents after DS protection"
fi
tar -C "${assets_tmp}" -cf "${release_dir}/runtime/assets/agents.tar" agents

log "encrypting OpenClaw plugin javascript asset"
rm -rf "${assets_tmp:?}/"*
tar -C "${assets_tmp}" -xf "${release_dir}/runtime/assets/openclaw_plugins.tar"
plugin_count=0
while IFS= read -r -d '' src; do
  encrypt_overwrite "${NODE_SSP}" "${src}"
  plugin_count=$((plugin_count + 1))
done < <(find "${assets_tmp}/openclaw_plugins" -path '*/node_modules/*' -prune -o -type f -name '*.js' -print0)
((plugin_count > 0)) || fail "no plugin javascript files found in OpenClaw plugins asset"
tar -C "${assets_tmp}" -cf "${release_dir}/runtime/assets/openclaw_plugins.tar" openclaw_plugins

log "creating protected OpenClaw launcher"
cat > "${release_dir}/runtime/openclaw-protected" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT_DIR}/runtime/node" "${ROOT_DIR}/runtime/openclaw/openclaw.mjs" "$@"
SH
chmod 0755 "${release_dir}/runtime/openclaw-protected"

log "patching launchers to use protected OpenClaw"
replace_text "${release_dir}/bin/claw-trade-ui" \
  '${ROOT_DIR}/runtime/openclaw/openclaw.mjs' \
  '${ROOT_DIR}/runtime/openclaw-protected'
replace_text "${release_dir}/bin/claw-trade-control" \
  '${ROOT_DIR}/runtime/openclaw/openclaw.mjs' \
  '${ROOT_DIR}/runtime/openclaw-protected'
replace_text "${release_dir}/runtime/claw-trade-control-runtime" \
  'OPENCLAW="${CURRENT}/runtime/openclaw/openclaw.mjs"' \
  'OPENCLAW="${CURRENT}/runtime/openclaw-protected"'

if find "${release_dir}" -name '*.ssp' -print -quit | grep -q .; then
  fail ".ssp leaked into release"
fi

log "auditing protected release tree"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/production/audit_production_package.py" "${release_dir}"

log "creating ${output_archive}"
tar --dereference --hard-dereference -C "${WORK_DIR}" -czf "${output_archive}" "${release_name}"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/production/validate_production_archive.py" "${output_archive}" >/dev/null
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/production/audit_production_package.py" "${output_archive}"
(
  cd "${OUTPUT_DIR}"
  sha256sum "$(basename "${output_archive}")" > "$(basename "${output_archive}").sha256"
)

log "done"
printf '%s\n' "${output_archive}"
