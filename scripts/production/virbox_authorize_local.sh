#!/usr/bin/env bash
set -euo pipefail

LCC_URL="${VIRBOX_LCC_URL:-http://127.0.0.1:12339}"
SSCLT="${VIRBOX_SSCLT:-$(command -v ssclt || true)}"
opt_root="/opt"
default_ssclt="${VIRBOX_DEFAULT_SSCLT:-${opt_root}/senseshield/ssclt}"
if [[ -z "${SSCLT}" && -x "${default_ssclt}" ]]; then
  SSCLT="${default_ssclt}"
fi
if [[ -n "${SSCLT}" && ! -x "${SSCLT}" ]]; then
  SSCLT=""
fi

show_response() {
  local body_file
  body_file="$(mktemp)"
  local status
  status="$(curl -sS -o "${body_file}" -w '%{http_code}' "$@")"
  cat "${body_file}"
  rm -f "${body_file}"
  echo
  if [[ "${status}" != 2* ]]; then
    echo "Virbox 返回 HTTP ${status}，上面是具体原因。" >&2
    exit 1
  fi
}

curl -fsS -X POST "${LCC_URL}/v1/license/enumLicense" \
  -H 'Content-Type: application/json' \
  -d '{}' >/dev/null || {
  echo "Virbox 本地服务没开：${LCC_URL}" >&2
  echo "先启动 Virbox LCC，再运行本脚本。" >&2
  exit 1
}

echo "1) 绑定授权码（推荐）"
echo "2) 登录云账号"
read -rp "选 1 或 2: " choice

case "${choice}" in
  1)
    read -rp "授权码: " license_key
    read -rsp "授权码密码，没有就直接回车: " license_password
    echo
    url="${LCC_URL}/v1/license/bindLicenseKey?licenseKey=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "${license_key}")"
    if [[ -n "${license_password}" ]]; then
      url="${url}&password=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "${license_password}")"
    fi
    show_response "${url}"
    ;;
  2)
    read -rp "Virbox账号: " account
    read -rsp "Virbox密码: " password
    echo
    payload="$(python3 -c 'import json, sys; print(json.dumps({"account": sys.argv[1], "password": sys.argv[2]}))' "${account}" "${password}")"
    show_response -X POST "${LCC_URL}/v1/license/accountLogin" \
      -H 'Content-Type: application/json' \
      -d "${payload}"
    ;;
  *)
    echo "只接受 1 或 2" >&2
    exit 1
    ;;
esac

echo
echo "当前许可："
if [[ -n "${SSCLT}" ]]; then
  "${SSCLT}" -l all || true
else
  curl -sS -X POST "${LCC_URL}/v1/license/enumLicense" \
    -H 'Content-Type: application/json' \
    -d '{}'
  echo
fi
