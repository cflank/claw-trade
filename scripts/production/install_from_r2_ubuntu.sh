#!/usr/bin/env bash
set -euo pipefail

release_name=""
base_url="${CLAW_TRADE_DELIVERY_BASE_URL:-https://download.cflank-trade.top/delivery}"
license_key_file=""
work_dir="${CLAW_TRADE_INSTALL_DOWNLOAD_DIR:-/tmp}"

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[INFO] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage: install_from_r2_ubuntu.sh claw-trade-production-版本-时间 [--license-key-file /path/to/license.key]
EOF
}

while (($#)); do
  case "$1" in
    --base-url)
      [[ $# -ge 2 ]] || fail "missing value for --base-url"
      base_url="$2"
      shift 2
      ;;
    --license-key-file)
      [[ $# -ge 2 ]] || fail "missing value for --license-key-file"
      license_key_file="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      fail "unknown option: $1"
      ;;
    *)
      [[ -z "${release_name}" ]] || fail "unexpected argument: $1"
      release_name="${1%.tar.gz}"
      shift
      ;;
  esac
done

[[ -n "${release_name}" ]] || {
  usage >&2
  exit 2
}
[[ "${release_name}" == claw-trade-production-* ]] || fail "bad release name: ${release_name}"

command -v curl >/dev/null 2>&1 || fail "missing curl"
command -v sha256sum >/dev/null 2>&1 || fail "missing sha256sum"
command -v sudo >/dev/null 2>&1 || fail "missing sudo"

mkdir -p "${work_dir}"
cd "${work_dir}"
base_url="${base_url%/}"

download() {
  local name="$1"
  log "downloading ${name}"
  curl -fL -o "${name}" "${base_url}/${name}"
}

download "${release_name}.tar.gz"
download "${release_name}.tar.gz.sha256"
download "install_factory_test_ubuntu.sh"
download "validate_production_archive.py"
download "update-signing-public.pem"
sha256sum -c "${release_name}.tar.gz.sha256"

if [[ -z "${license_key_file}" ]]; then
  license_key_file="${work_dir}/license.key"
  if [[ ! -f "${license_key_file}" ]]; then
    umask 077
    printf 'Paste Virbox license key, then press Enter: ' >&2
    IFS= read -r license_key
    [[ -n "${license_key}" ]] || fail "empty license key"
    printf '%s\n' "${license_key}" >"${license_key_file}"
  fi
fi

[[ -f "${license_key_file}" ]] || fail "license key file not found: ${license_key_file}"

sudo bash "${work_dir}/install_factory_test_ubuntu.sh" \
  "${work_dir}/${release_name}.tar.gz" \
  --license-key-file "${license_key_file}"
