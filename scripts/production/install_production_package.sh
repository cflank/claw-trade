#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?usage: install_production_package.sh <archive.tar> <version>}"
VERSION="${2:?usage: install_production_package.sh <archive.tar> <version>}"
ROOT="${CLAW_TRADE_HOME:-/opt/claw-trade}"
RELEASE="${ROOT}/releases/claw-trade-${VERSION}"

if [[ ! -f "${ARCHIVE}" ]]; then
  echo "archive not found: ${ARCHIVE}" >&2
  exit 1
fi

getent group clawtrade >/dev/null || sudo groupadd --system clawtrade
id -u clawtrade >/dev/null 2>&1 || sudo useradd --system --gid clawtrade --home "${ROOT}" --shell /usr/sbin/nologin clawtrade

sudo mkdir -p \
  "${ROOT}/releases" \
  "${ROOT}/shared/config" \
  "${ROOT}/shared/data" \
  "${ROOT}/shared/logs" \
  "${ROOT}/shared/license" \
  "${ROOT}/shared/reports" \
  "${ROOT}/shared/updates"

top_dir="$(tar -tf "${ARCHIVE}" | awk -F/ 'NR == 1 { first=$1 } END { if (first != "") print first; else exit 1 }')" || {
  echo "cannot read archive top directory: ${ARCHIVE}" >&2
  exit 1
}
RELEASE="${ROOT}/releases/${top_dir}"

sudo tar --no-same-owner --no-same-permissions -C "${ROOT}/releases" -xf "${ARCHIVE}"

if [[ ! -d "${RELEASE}" ]]; then
  echo "release directory missing after extraction: ${RELEASE}" >&2
  exit 1
fi

sudo ln -sfn "${RELEASE}" "${ROOT}/current.next"
sudo mv -Tf "${ROOT}/current.next" "${ROOT}/current"
sudo chmod +x "${ROOT}/current/bin/"*
sudo chown -R "$(id -un):$(id -gn)" "${RELEASE}" "${ROOT}/shared"
sudo chown -h "$(id -un):$(id -gn)" "${ROOT}/current"

echo "installed ${RELEASE}"
