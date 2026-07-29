#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(python3.12 - <<'PY' "${ROOT}/pyproject.toml"
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"]["version"])
PY
)"
INPUT_ARCHIVE="${CLAW_TRADE_INPUT_ARCHIVE:-}"
BUILD_DIR="${ROOT}/.runtime/virbox-protected-build/${VERSION}"
STAGING_DIR="${BUILD_DIR}/staging"
PROTECTED_DIR="${BUILD_DIR}/protected-claw-trade"
OUTPUT_ARCHIVE="${ROOT}/.runtime/production-build/claw-trade-${VERSION}-virbox-protected.tar"
PROTECTOR_BIN="${VIRBOX_PROTECTOR_BIN:-/usr/share/virboxprotector-trial/bin/pyprotector_con}"
PYTHON_EXTENSION_DIR="${VIRBOX_PYTHON_EXTENSION_DIR:-${HOME}/virboxLMProtector/python_extension}"
PYTHON_EXTENSION_ZIP="${VIRBOX_PYTHON_EXTENSION_ZIP:-}"
PLATFORMS="${VIRBOX_TARGET_PLATFORMS:-linux-x64}"

fail() {
  echo "error: $*" >&2
  exit 1
}

[[ "${VERSION}" =~ ^[A-Za-z0-9._-]+$ ]] || fail "invalid version; allowed characters: A-Z a-z 0-9 . _ -"

require_file() {
  local path="$1"
  local message="$2"
  [[ -f "${path}" ]] || fail "${message}: ${path}"
}

require_dir() {
  local path="$1"
  local message="$2"
  [[ -d "${path}" ]] || fail "${message}: ${path}"
}

if [[ -z "${INPUT_ARCHIVE}" ]]; then
  "${ROOT}/scripts/production/build_production_package.sh" >/dev/null
  INPUT_ARCHIVE="${ROOT}/.runtime/production-build/claw-trade-${VERSION}.tar"
fi

require_file "${INPUT_ARCHIVE}" "input production archive missing"
[[ -x "${PROTECTOR_BIN}" ]] || fail "Virbox pyprotector_con missing or not executable: ${PROTECTOR_BIN}"

if [[ ! -d "${PYTHON_EXTENSION_DIR}" ]]; then
  if [[ -n "${PYTHON_EXTENSION_ZIP}" ]]; then
    require_file "${PYTHON_EXTENSION_ZIP}" "Virbox Python extension zip missing"
    "${PROTECTOR_BIN}" --install="${PYTHON_EXTENSION_ZIP}"
  fi
fi
require_dir "${PYTHON_EXTENSION_DIR}" "Virbox Python extension directory missing; install python_extension.zip first"

rm -rf "${BUILD_DIR}"
mkdir -p "${STAGING_DIR}" "${PROTECTED_DIR}" "$(dirname "${OUTPUT_ARCHIVE}")"
tar -C "${STAGING_DIR}" -xf "${INPUT_ARCHIVE}"

RELEASE_DIR="${STAGING_DIR}/claw-trade-${VERSION}"
require_dir "${RELEASE_DIR}" "release directory missing after archive extraction"
PYTHON_BIN="${RELEASE_DIR}/runtime/python/bin/python"
require_file "${PYTHON_BIN}" "release Python missing"

SITE_PACKAGE="$(
  find "${RELEASE_DIR}/runtime/python" \
    -type d \
    -path '*/site-packages/claw_trade' \
    -print \
    -quit
)"
[[ -n "${SITE_PACKAGE}" ]] || fail "release claw_trade site-package not found"

TARGET_PYTHON_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

PROTECT_OUTPUT="${BUILD_DIR}/pyprotector.out"
set +e
LD_LIBRARY_PATH="$(dirname "${PROTECTOR_BIN}")${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
  "${PROTECTOR_BIN}" \
    --target-python-version="${TARGET_PYTHON_VERSION}" \
    --platforms="${PLATFORMS}" \
    --function-check=1 \
    --module-check=1 \
    --str-enc=1 \
    --bc-dyn-enc=1 \
    --pack=1 \
    --interpreter="${PYTHON_BIN}" \
    -o "${PROTECTED_DIR}" \
    "${SITE_PACKAGE}" >"${PROTECT_OUTPUT}" 2>&1
PROTECT_STATUS=$?
set -e

if [[ "${PROTECT_STATUS}" -ne 0 ]]; then
  cat "${PROTECT_OUTPUT}" >&2
  if grep -q "License not found" "${PROTECT_OUTPUT}"; then
    fail "Virbox Protector license not found; log in or bind a valid Protector license in Virbox LCC before building the protected package"
  fi
  fail "Virbox protection failed with exit code ${PROTECT_STATUS}"
fi

if [[ -d "${PROTECTED_DIR}/claw_trade" ]]; then
  PROTECTED_PACKAGE="${PROTECTED_DIR}/claw_trade"
else
  PROTECTED_PACKAGE="${PROTECTED_DIR}"
fi

find "${PROTECTED_PACKAGE}" -type d -name virbox_pyruntime -print -quit | grep -q . \
  || fail "Virbox protection output missing virbox_pyruntime; refusing to package"

mv "${SITE_PACKAGE}" "${SITE_PACKAGE}.unprotected"
cp -a "${PROTECTED_PACKAGE}" "${SITE_PACKAGE}"
rm -rf "${SITE_PACKAGE}.unprotected"

tar -C "${STAGING_DIR}" -cf "${OUTPUT_ARCHIVE}" "claw-trade-${VERSION}"
"${AUDIT_PYTHON:-python3}" "${ROOT}/scripts/production/audit_production_package.py" "${OUTPUT_ARCHIVE}"
sha256sum "${OUTPUT_ARCHIVE}" >"${OUTPUT_ARCHIVE}.sha256"
echo "${OUTPUT_ARCHIVE}"
