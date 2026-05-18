#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENBB_RUNTIME_DIR="${ROOT_DIR}/.runtime/dev-services/openbb"
OPENBB_TEMPLATE_PATH="${OPENBB_RUNTIME_DIR}/openbb.env.template"
OPENBB_ENV_PATH="${OPENBB_RUNTIME_DIR}/openbb.env"
INSTALL_DEPS=0

for arg in "$@"; do
  case "${arg}" in
    --install)
      INSTALL_DEPS=1
      ;;
    *)
      printf '[ERROR] unknown argument: %s\n' "${arg}" >&2
      printf 'usage: %s [--install]\n' "$0" >&2
      exit 2
      ;;
  esac
done

mkdir -p "${OPENBB_RUNTIME_DIR}"

if [[ ! -f "${OPENBB_TEMPLATE_PATH}" ]]; then
  cat > "${OPENBB_TEMPLATE_PATH}" <<'EOF'
# OpenBB local runtime template for claw-trade dev only.
# Do not start MCP/provider services from this template.
OPENBB_HOME=.runtime/dev-services/openbb/home
OPENBB_USER_SETTINGS_DIRECTORY=.runtime/dev-services/openbb/user_settings
OPENBB_LOG_DIRECTORY=.runtime/dev-services/openbb/logs
EOF
fi

if [[ ! -f "${OPENBB_ENV_PATH}" ]]; then
  cp "${OPENBB_TEMPLATE_PATH}" "${OPENBB_ENV_PATH}"
fi

if [[ "${INSTALL_DEPS}" == "1" ]]; then
  uv pip install \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/core" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/extensions/mcp_server" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/extensions/equity" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/extensions/crypto" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/extensions/news" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/extensions/economy" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/providers/yfinance" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/providers/fmp" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/providers/sec" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/providers/fred" \
    -e "${ROOT_DIR}/third_party/openbb/openbb_platform/providers/deribit"
fi

cat <<EOF
[openbb-dev-setup] prepared:
- runtime dir: ${OPENBB_RUNTIME_DIR}
- template: ${OPENBB_TEMPLATE_PATH}
- env file: ${OPENBB_ENV_PATH}
- install deps: ${INSTALL_DEPS}

next:
1) review ${OPENBB_ENV_PATH}
2) source it when needed: set -a; source ${OPENBB_ENV_PATH}; set +a
3) install local OpenBB runtime deps when needed: ${0} --install
4) run OpenBB commands explicitly from approved task scripts
EOF
