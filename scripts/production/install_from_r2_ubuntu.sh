#!/usr/bin/env bash
set -euo pipefail

release_name=""
base_url="${CLAW_TRADE_DELIVERY_BASE_URL:-https://download.cflank-trade.top/delivery}"
stable_base_url="${CLAW_TRADE_STABLE_BASE_URL:-https://download.cflank-trade.top/stable}"
license_key_file=""
work_dir="${CLAW_TRADE_INSTALL_DOWNLOAD_DIR:-/tmp}"
lcc_deb="${CLAW_TRADE_LCC_DEB:-senseshield-lcc-2.7.5.69040-amd64.deb}"

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[INFO] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage: install_from_r2_ubuntu.sh [claw-trade-production-版本-时间] [--license-key-file /path/to/license.key]

Without a release argument, the script reads delivery/latest.txt first, then stable/manifest.json.
EOF
}

ensure_split_lock_off() {
  if grep -qw 'split_lock_detect=off' /proc/cmdline; then
    log "split_lock_detect=off already active"
    return 0
  fi

  [[ -f /etc/default/grub ]] || fail "missing /etc/default/grub; cannot configure split_lock_detect=off"
  log "configuring split_lock_detect=off; machine will reboot"
  sudo cp -a /etc/default/grub "/etc/default/grub.bak-claw-trade-$(date -u +%Y%m%dT%H%M%SZ)"
  if ! grep -q 'split_lock_detect=off' /etc/default/grub; then
    if grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub; then
      sudo sed -i -E '/^GRUB_CMDLINE_LINUX_DEFAULT=/ s/"$/ split_lock_detect=off"/' /etc/default/grub
    else
      printf '%s\n' 'GRUB_CMDLINE_LINUX_DEFAULT="split_lock_detect=off"' | sudo tee -a /etc/default/grub >/dev/null
    fi
  fi
  sudo update-grub
  rerun_cmd="curl -fsSL ${base_url%/}/install_from_r2_ubuntu.sh | bash -s -- --base-url ${base_url%/}"
  [[ -z "${release_name}" ]] || rerun_cmd="${rerun_cmd} ${release_name}"
  [[ -z "${license_key_file}" ]] || rerun_cmd="${rerun_cmd} --license-key-file ${license_key_file}"
  log "rebooting now; after SSH comes back, run:"
  log "  ${rerun_cmd}"
  sudo reboot
  exit 75
}

resolve_release_name() {
  local latest manifest archive
  base_url="${base_url%/}"
  stable_base_url="${stable_base_url%/}"

  if [[ -n "${release_name}" ]]; then
    release_name="${release_name%.tar.gz}"
  else
    latest="$(curl -fsS "${base_url}/latest.txt" 2>/dev/null | awk 'NF {print $1; exit}' || true)"
    if [[ -n "${latest}" ]]; then
      release_name="${latest%.tar.gz}"
    else
      manifest="$(curl -fsS "${stable_base_url}/manifest.json" 2>/dev/null || true)"
      archive="$(printf '%s\n' "${manifest}" | sed -nE 's/.*"archive"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n 1)"
      release_name="${archive%.tar.gz}"
    fi
  fi

  [[ -n "${release_name}" ]] || fail "missing release; upload ${base_url}/latest.txt or pass claw-trade-production-版本-时间"
  [[ "${release_name}" == claw-trade-production-* ]] || fail "bad release name: ${release_name}"
  log "using release: ${release_name}"
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

command -v curl >/dev/null 2>&1 || fail "missing curl"
command -v sha256sum >/dev/null 2>&1 || fail "missing sha256sum"
command -v sudo >/dev/null 2>&1 || fail "missing sudo"

if [[ -z "${license_key_file}" && ! -t 0 ]]; then
  fail "missing --license-key-file; create a license file first, then pass --license-key-file /path/to/license.key on the same curl | bash command"
fi

sudo -v
ensure_split_lock_off
resolve_release_name

mkdir -p "${work_dir}"
cd "${work_dir}"

download() {
  local name="$1"
  log "downloading ${name}"
  curl --http1.1 --retry 8 --retry-delay 5 --retry-all-errors -fL -o "${name}" "${base_url}/${name}"
}

download_resumable() {
  local name="$1"
  log "downloading ${name}"
  if [[ -s "${name}" ]]; then
    log "resuming existing ${name}"
    if curl --http1.1 --retry 8 --retry-delay 5 --retry-all-errors -fL -C - -o "${name}" "${base_url}/${name}"; then
      return 0
    fi
    log "resume failed for ${name}; restarting full download"
    rm -f "${name}"
  fi
  curl --http1.1 --retry 8 --retry-delay 5 --retry-all-errors -fL -o "${name}" "${base_url}/${name}"
}

download "${release_name}.tar.gz.sha256"
if [[ -f "${release_name}.tar.gz" ]] && sha256sum -c "${release_name}.tar.gz.sha256"; then
  log "using cached ${release_name}.tar.gz"
else
  download_resumable "${release_name}.tar.gz"
  sha256sum -c "${release_name}.tar.gz.sha256"
fi
download "${lcc_deb}"
download "install_factory_test_ubuntu.sh"
download "validate_production_archive.py"
download "update-signing-public.pem"
sudo apt-get install -y "${work_dir}/${lcc_deb}"

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
