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

sudo mkdir -p \
  "${ROOT}/releases" \
  "${ROOT}/shared/config" \
  "${ROOT}/shared/data" \
  "${ROOT}/shared/logs" \
  "${ROOT}/shared/license" \
  "${ROOT}/shared/reports" \
  "${ROOT}/shared/updates"

sudo tar -C "${ROOT}/releases" -xf "${ARCHIVE}"

if [[ ! -d "${RELEASE}" ]]; then
  echo "release directory missing after extraction: ${RELEASE}" >&2
  exit 1
fi

sudo ln -sfn "${RELEASE}" "${ROOT}/current.next"
sudo mv -Tf "${ROOT}/current.next" "${ROOT}/current"
sudo chmod +x "${ROOT}/current/bin/"*
sudo chown -R clawtrade:clawtrade "${ROOT}/shared"

echo "installed ${RELEASE}"
