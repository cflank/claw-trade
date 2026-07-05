#!/usr/bin/env bash
set -euo pipefail

archive="${1:-}"
if [[ -z "${archive}" || ! -f "${archive}" ]]; then
  printf 'Usage: %s <claw-trade-production-*.tar.gz>\n' "$0" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_root="${CLAW_TRADE_INSTALL_ROOT:-/opt/claw-trade}"
release_root="${install_root}/releases"
runtime_owner="${CLAW_TRADE_RUNTIME_OWNER:-clawtrade}"
runtime_group="${CLAW_TRADE_RUNTIME_GROUP:-clawtrade}"
kiosk_owner="${CLAW_TRADE_KIOSK_OWNER:-clawkiosk}"
kiosk_group="${CLAW_TRADE_KIOSK_GROUP:-clawkiosk}"
kiosk_browser="${CLAW_TRADE_KIOSK_BROWSER_BIN:-/usr/bin/chromium-browser}"
update_base_url="${CLAW_TRADE_UPDATE_BASE_URL:-https://download.cflank-trade.top/stable/}"

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  local message="$2"
  command -v "${command_name}" >/dev/null 2>&1 || fail "${message}"
}

require_noto_cjk_font() {
  local family
  family="$(fc-match -f '%{family}\n' 'Noto Sans CJK SC' 2>/dev/null || true)"
  if [[ ! "${family,,}" =~ noto.*cjk ]]; then
    fail "missing PDF Chinese font: install fonts-noto-cjk"
  fi
}

ensure_system_identity() {
  local user="$1"
  local group="$2"
  if ! getent group "${group}" >/dev/null; then
    groupadd --system "${group}"
  fi
  if ! id -u "${user}" >/dev/null 2>&1; then
    useradd --system --no-create-home --gid "${group}" --shell /usr/sbin/nologin "${user}"
  fi
}

write_shared_env_var() {
  local key="$1"
  local value="$2"
  local env_file="${install_root}/shared/config/claw-trade.env"
  install -d -m 0750 -o "${runtime_owner}" -g "${runtime_group}" "${install_root}/shared/config"
  touch "${env_file}"
  sed -i "/^${key}=/d" "${env_file}"
  printf '%s=%s\n' "${key}" "${value}" >>"${env_file}"
  chown "${runtime_owner}:${runtime_group}" "${env_file}"
  chmod 0640 "${env_file}"
}

ensure_system_identity "${runtime_owner}" "${runtime_group}"
ensure_system_identity "${kiosk_owner}" "${kiosk_group}"
[[ -x "${kiosk_browser}" ]] || fail "missing kiosk browser: ${kiosk_browser}"
require_command wkhtmltopdf "missing PDF renderer: install wkhtmltopdf"
require_command fc-match "missing PDF font runtime: install fontconfig"
require_noto_cjk_font

top_dir="$(python3 "${script_dir}/validate_production_archive.py" "${archive}")"
install -d -m 0755 "${release_root}"
install -d -m 0750 \
  "${install_root}/shared" \
  "${install_root}/shared"/{cache,config,data,license,logs,openclaw,queues,reports,runs,sessions,tmp,updates} \
  "${install_root}/shared/logs"/{diagnostics,factory-reset} \
  "${install_root}/shared/updates"/{downloads,logs}
tar --no-same-owner --no-same-permissions -xzf "${archive}" -C "${release_root}"
chmod 0755 "${release_root}/${top_dir}"
chown root:root "${release_root}"
chown -R root:root "${release_root}/${top_dir}"
chown -R "${runtime_owner}:${runtime_group}" "${install_root}/shared"
write_shared_env_var "CLAW_TRADE_UPDATE_BASE_URL" "${update_base_url}"

"${release_root}/${top_dir}/bin/claw-trade-preflight"
install -d -m 0755 /usr/local/lib/claw-trade
install -m 0755 "${release_root}/${top_dir}/root-helper/claw-trade-apply-update" /usr/local/lib/claw-trade/claw-trade-apply-update
install -m 0644 "${release_root}/${top_dir}/systemd/"*.service /etc/systemd/system/
install -m 0644 "${release_root}/${top_dir}/systemd/"*.timer /etc/systemd/system/
systemctl daemon-reload
install -m 0440 "${release_root}/${top_dir}/sudoers/claw-trade-update" /etc/sudoers.d/claw-trade-update
visudo -cf /etc/sudoers.d/claw-trade-update >/dev/null
tmp_current="${install_root}/.current.${top_dir}.$$"
ln -sfn "${release_root}/${top_dir}" "${tmp_current}"
mv -Tf "${tmp_current}" "${install_root}/current"
chown -h root:root "${install_root}/current"
tmp_rescue_current="${install_root}/.rescue-current.${top_dir}.$$"
ln -sfn "${release_root}/${top_dir}" "${tmp_rescue_current}"
mv -Tf "${tmp_rescue_current}" "${install_root}/rescue-current"
chown -h root:root "${install_root}/rescue-current"
systemctl enable --now claw-trade-auto-update.timer
printf '[OK] installed %s -> %s/current\n' "${top_dir}" "${install_root}"
