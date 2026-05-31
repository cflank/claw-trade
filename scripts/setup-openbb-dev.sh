#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENBB_RUNTIME_DIR="${ROOT_DIR}/.runtime/dev-services/openbb"
OPENBB_TEMPLATE_PATH="${OPENBB_RUNTIME_DIR}/openbb.env.template"
OPENBB_ENV_PATH="${OPENBB_RUNTIME_DIR}/openbb.env"
INSTALL_DEPS=0

ensure_env_line() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "${file_path}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${file_path}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${file_path}"
  fi
}

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
OPENBB_AUTO_BUILD=0
EOF
fi

if [[ ! -f "${OPENBB_ENV_PATH}" ]]; then
  cp "${OPENBB_TEMPLATE_PATH}" "${OPENBB_ENV_PATH}"
fi
ensure_env_line "${OPENBB_TEMPLATE_PATH}" "OPENBB_AUTO_BUILD" "0"
ensure_env_line "${OPENBB_ENV_PATH}" "OPENBB_AUTO_BUILD" "0"

if [[ "${INSTALL_DEPS}" == "1" ]]; then
  OPENBB_GENERATED_SNAPSHOT_DIR="$(mktemp -d)"
  trap 'rm -rf "${OPENBB_GENERATED_SNAPSHOT_DIR}"' EXIT
  OPENBB_TRACKED_GENERATED_FILES=(
    "openbb_platform/core/openbb/assets/reference.json"
    "openbb_platform/core/openbb/package/__init__.py"
  )
  for relative_path in "${OPENBB_TRACKED_GENERATED_FILES[@]}"; do
    mkdir -p "${OPENBB_GENERATED_SNAPSHOT_DIR}/$(dirname "${relative_path}")"
    cp "${ROOT_DIR}/third_party/openbb/${relative_path}" "${OPENBB_GENERATED_SNAPSHOT_DIR}/${relative_path}"
  done

  uv sync --locked
  OPENBB_AUTO_BUILD=0 uv run python -c "import openbb; openbb.build(lint=True, verbose=False)"

  for relative_path in "${OPENBB_TRACKED_GENERATED_FILES[@]}"; do
    cp "${OPENBB_GENERATED_SNAPSHOT_DIR}/${relative_path}" "${ROOT_DIR}/third_party/openbb/${relative_path}"
  done
  rm -f "${ROOT_DIR}/third_party/openbb/openbb_platform/core/openbb/.build.lock"
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
