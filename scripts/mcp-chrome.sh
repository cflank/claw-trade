#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ENDPOINT="http://127.0.0.1:9222"
ENDPOINT="$DEFAULT_ENDPOINT"
ACTION="check"
POWERSHELL_EXE="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
CHROME_EXE_WIN='C:\Program Files\Google\Chrome\Application\chrome.exe'
USER_DATA_DIR_WIN='C:\Selenium\Chrome-MCP'

usage() {
  cat <<'EOF'
Usage:
  scripts/mcp-chrome.sh --check
  scripts/mcp-chrome.sh --start
  scripts/mcp-chrome.sh --endpoint <url> --check

Options:
  --check           Probe the Chrome DevTools endpoint and print status.
  --start           Launch Win11 Chrome with remote debugging enabled.
  --endpoint <url>  Override the default CDP endpoint (default: http://127.0.0.1:9222).
  --help            Show this help message.
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[mcp-chrome] Missing required command: $1" >&2
    exit 1
  fi
}

extract_port() {
  local stripped pathless port
  stripped="${1#*://}"
  pathless="${stripped%%/*}"
  if [[ "$pathless" == *:* ]]; then
    port="${pathless##*:}"
  else
    port="80"
  fi
  printf '%s\n' "$port"
}

probe_wsl_endpoint() {
  require_command curl
  curl -fsS --max-time 2 "$ENDPOINT/json/version" 2>/dev/null
}

probe_windows_local_endpoint() {
  local port windows_endpoint
  port="$(extract_port "$ENDPOINT")"
  windows_endpoint="http://127.0.0.1:${port}/json/version"

  if [[ ! -x "$POWERSHELL_EXE" ]]; then
    return 1
  fi

  "$POWERSHELL_EXE" -NoProfile -Command \
    "\$ProgressPreference = 'SilentlyContinue'; (Invoke-WebRequest -UseBasicParsing '$windows_endpoint' -TimeoutSec 2).Content" 2>/dev/null
}

print_probe_details() {
  local response="$1"
  local browser_line web_socket_line
  browser_line="$(printf '%s' "$response" | sed -n 's/.*"Browser"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  web_socket_line="$(printf '%s' "$response" | sed -n 's/.*"webSocketDebuggerUrl"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"

  if [[ -n "$browser_line" ]]; then
    echo "[mcp-chrome] Browser: $browser_line"
  fi
  if [[ -n "$web_socket_line" ]]; then
    echo "[mcp-chrome] WebSocket: $web_socket_line"
  fi
}

check_endpoint() {
  local response
  if response="$(probe_wsl_endpoint)"; then
    echo "[mcp-chrome] READY from WSL: $ENDPOINT"
    print_probe_details "$response"
    return 0
  fi

  if response="$(probe_windows_local_endpoint)"; then
    echo "[mcp-chrome] READY on Windows: http://127.0.0.1:$(extract_port "$ENDPOINT")"
    echo "[mcp-chrome] Note: WSL localhost forwarding is unavailable on this machine."
    echo "[mcp-chrome] Note: use a Windows-side MCP command to talk to Chrome."
    print_probe_details "$response"
    return 0
  fi

  echo "[mcp-chrome] NOT READY: Chrome DevTools is unreachable from both WSL and Windows-local probes."
  echo "[mcp-chrome] Hint: run 'scripts/mcp-chrome.sh --start' or launch Chrome manually on Win11."
  return 1
}

start_chrome() {
  local port
  port="$(extract_port "$ENDPOINT")"

  if probe_windows_local_endpoint >/dev/null 2>&1; then
    echo "[mcp-chrome] Chrome is already listening on Windows localhost port $port"
    check_endpoint
    return 0
  fi

  if [[ ! -x "$POWERSHELL_EXE" ]]; then
    echo "[mcp-chrome] Cannot find PowerShell at $POWERSHELL_EXE" >&2
    echo "[mcp-chrome] Start Chrome manually on Windows with:"
    echo "  \"$CHROME_EXE_WIN\" --remote-debugging-port=$port --user-data-dir=\"$USER_DATA_DIR_WIN\""
    return 1
  fi

  "$POWERSHELL_EXE" -NoProfile -Command \
    "& { Start-Process -FilePath '$CHROME_EXE_WIN' -ArgumentList '--remote-debugging-port=$port','--user-data-dir=$USER_DATA_DIR_WIN' }"

  for _ in $(seq 1 20); do
    if probe_windows_local_endpoint >/dev/null 2>&1; then
      echo "[mcp-chrome] Chrome launched successfully."
      check_endpoint
      return 0
    fi
    sleep 1
  done

  echo "[mcp-chrome] Chrome launch was attempted, but $ENDPOINT is still unreachable." >&2
  echo "[mcp-chrome] Try this in Windows manually:"
  echo "  \"$CHROME_EXE_WIN\" --remote-debugging-port=$port --user-data-dir=\"$USER_DATA_DIR_WIN\""
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      ACTION="check"
      shift
      ;;
    --start)
      ACTION="start"
      shift
      ;;
    --endpoint)
      if [[ $# -lt 2 ]]; then
        echo "[mcp-chrome] --endpoint requires a value" >&2
        exit 1
      fi
      ENDPOINT="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[mcp-chrome] Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$ACTION" in
  check)
    check_endpoint
    ;;
  start)
    start_chrome
    ;;
esac
