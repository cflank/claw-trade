#!/usr/bin/env bash
set -euo pipefail
install_root="${INSTALL_ROOT:-/opt/claw-trade}"
package="${1:-${CLAW_TRADE_FACTORY_PACKAGE:-}}"
control_log="${CONTROL_LOG:-/tmp/claw-trade-control.log}"
ui_log="${UI_LOG:-/tmp/claw-trade-ui.log}"
control_pid_file="${CONTROL_PID_FILE:-/tmp/claw-trade-control.pid}"
ui_pid_file="${UI_PID_FILE:-/tmp/claw-trade-ui.pid}"
log() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
tail_log() { [[ -f "$1" ]] && tail -n 160 "$1" >&2 || true; }
stop_pid_file() { local f="$1" pid=""; [[ -f "$f" ]] && pid="$(cat "$f" 2>/dev/null || true)"; if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; sleep 2; kill -9 "$pid" 2>/dev/null || true; fi; rm -f "$f"; }
stop_ui() { stop_pid_file "$ui_pid_file"; pkill -f "python3.12 -m claw_trade.web.app" 2>/dev/null || true; }
stop_control() { stop_pid_file "$control_pid_file"; pkill -f "${install_root}/current/runtime/claw-trade-control-runtime" 2>/dev/null || true; }
port_free() { local port="$1"; ! ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN; }
select_openclaw_gateway_port() { for port in $(seq 18789 18809); do port_free "$port" && { printf '%s\n' "$port"; return 0; }; done; fail "no free OpenClaw gateway port found in 18789-18809"; }
patch_runtime_control() {
  local root="${install_root}/current"
  local control_bin="${root}/bin/claw-trade-control"
  local runtime_script="${root}/runtime/claw-trade-control-runtime"
  [[ -f "$control_bin" ]] || fail "control wrapper not found: $control_bin"
  [[ -f "$runtime_script" ]] || fail "runtime control script not found: $runtime_script"
  HELPER_TEXT_B64='IyEvdXNyL2Jpbi9lbnYgbm9kZQppbXBvcnQgcmVhZGxpbmUgZnJvbSAibm9kZTpyZWFkbGluZSI7CmltcG9ydCB7IHNwYXduIH0gZnJvbSAibm9kZTpjaGlsZF9wcm9jZXNzIjsKCmZ1bmN0aW9uIHJlc3BvbmQoaWQsIHBheWxvYWQpIHsKICBwcm9jZXNzLnN0ZG91dC53cml0ZShKU09OLnN0cmluZ2lmeSh7IGlkLCAuLi5wYXlsb2FkIH0pICsgIlxuIik7Cn0KCmZ1bmN0aW9uIG5vcm1hbGl6ZVJlc3VsdChwYXJzZWQpIHsKICBpZiAocGFyc2VkICYmIHR5cGVvZiBwYXJzZWQgPT09ICJvYmplY3QiICYmICFBcnJheS5pc0FycmF5KHBhcnNlZCkpIHsKICAgIGlmIChwYXJzZWQub2sgPT09IHRydWUgJiYgT2JqZWN0LnByb3RvdHlwZS5oYXNPd25Qcm9wZXJ0eS5jYWxsKHBhcnNlZCwgInJlc3VsdCIpKSB7CiAgICAgIHJldHVybiB7IG9rOiB0cnVlLCByZXN1bHQ6IHBhcnNlZC5yZXN1bHQgfTsKICAgIH0KICAgIGlmIChwYXJzZWQub2sgPT09IGZhbHNlKSB7CiAgICAgIHJldHVybiB7IG9rOiBmYWxzZSwgZXJyb3I6IHBhcnNlZC5lcnJvciB8fCB7IG1lc3NhZ2U6ICJnYXRld2F5IGNhbGwgZmFpbGVkIiB9IH07CiAgICB9CiAgfQogIHJldHVybiB7IG9rOiB0cnVlLCByZXN1bHQ6IHBhcnNlZCB9Owp9CgpmdW5jdGlvbiBjYWxsR2F0ZXdheShyZXF1ZXN0KSB7CiAgcmV0dXJuIG5ldyBQcm9taXNlKChyZXNvbHZlKSA9PiB7CiAgICBjb25zdCBtZXRob2QgPSBTdHJpbmcocmVxdWVzdC5tZXRob2QgfHwgIiIpLnRyaW0oKTsKICAgIGlmICghbWV0aG9kKSB7CiAgICAgIHJlc29sdmUoeyBvazogZmFsc2UsIGVycm9yOiB7IG1lc3NhZ2U6ICJtaXNzaW5nIGdhdGV3YXkgbWV0aG9kIiB9IH0pOwogICAgICByZXR1cm47CiAgICB9CgogICAgY29uc3QgYmluID0gcHJvY2Vzcy5lbnYuT1BFTkNMQVdfR0FURVdBWV9DQUxMX0JJTiB8fCAib3BlbmNsYXciOwogICAgY29uc3QgYXJncyA9IFsiZ2F0ZXdheSIsICJjYWxsIiwgbWV0aG9kXTsKICAgIGlmIChyZXF1ZXN0LnVybCkgYXJncy5wdXNoKCItLXVybCIsIFN0cmluZyhyZXF1ZXN0LnVybCkpOwogICAgaWYgKHJlcXVlc3QudGltZW91dE1zKSBhcmdzLnB1c2goIi0tdGltZW91dCIsIFN0cmluZyhyZXF1ZXN0LnRpbWVvdXRNcykpOwogICAgaWYgKHJlcXVlc3QuZXhwZWN0RmluYWwpIGFyZ3MucHVzaCgiLS1leHBlY3QtZmluYWwiKTsKICAgIGFyZ3MucHVzaCgiLS1wYXJhbXMiLCBKU09OLnN0cmluZ2lmeShyZXF1ZXN0LnBhcmFtcyAmJiB0eXBlb2YgcmVxdWVzdC5wYXJhbXMgPT09ICJvYmplY3QiID8gcmVxdWVzdC5wYXJhbXMgOiB7fSkpOwogICAgYXJncy5wdXNoKCItLWpzb24iKTsKICAgIGlmIChyZXF1ZXN0LnRva2VuKSBhcmdzLnB1c2goIi0tdG9rZW4iLCBTdHJpbmcocmVxdWVzdC50b2tlbikpOwogICAgZWxzZSBpZiAocmVxdWVzdC5wYXNzd29yZCkgYXJncy5wdXNoKCItLXBhc3N3b3JkIiwgU3RyaW5nKHJlcXVlc3QucGFzc3dvcmQpKTsKCiAgICBjb25zdCB0aW1lb3V0TXMgPSBNYXRoLm1heChOdW1iZXIocmVxdWVzdC50aW1lb3V0TXMgfHwgcHJvY2Vzcy5lbnYuT1BFTkNMQVdfR0FURVdBWV9USU1FT1VUX01TIHx8IDEwMDAwKSwgMTAwMCkgKyAzMDAwOwogICAgY29uc3QgY2hpbGQgPSBzcGF3bihiaW4sIGFyZ3MsIHsgZW52OiBwcm9jZXNzLmVudiwgc3RkaW86IFsiaWdub3JlIiwgInBpcGUiLCAicGlwZSJdIH0pOwogICAgbGV0IHN0ZG91dCA9ICIiOwogICAgbGV0IHN0ZGVyciA9ICIiOwogICAgY29uc3QgdGltZXIgPSBzZXRUaW1lb3V0KCgpID0+IHsKICAgICAgY2hpbGQua2lsbCgiU0lHVEVSTSIpOwogICAgICBzZXRUaW1lb3V0KCgpID0+IGNoaWxkLmtpbGwoIlNJR0tJTEwiKSwgMTAwMCkudW5yZWYoKTsKICAgIH0sIHRpbWVvdXRNcyk7CiAgICB0aW1lci51bnJlZigpOwoKICAgIGNoaWxkLnN0ZG91dC5zZXRFbmNvZGluZygidXRmOCIpOwogICAgY2hpbGQuc3RkZXJyLnNldEVuY29kaW5nKCJ1dGY4Iik7CiAgICBjaGlsZC5zdGRvdXQub24oImRhdGEiLCAoY2h1bmspID0+IHsgc3Rkb3V0ICs9IGNodW5rOyB9KTsKICAgIGNoaWxkLnN0ZGVyci5vbigiZGF0YSIsIChjaHVuaykgPT4geyBzdGRlcnIgKz0gY2h1bms7IH0pOwogICAgY2hpbGQub24oImVycm9yIiwgKGVycm9yKSA9PiB7CiAgICAgIGNsZWFyVGltZW91dCh0aW1lcik7CiAgICAgIHJlc29sdmUoeyBvazogZmFsc2UsIGVycm9yOiB7IG1lc3NhZ2U6IFN0cmluZyhlcnJvciAmJiBlcnJvci5tZXNzYWdlID8gZXJyb3IubWVzc2FnZSA6IGVycm9yKSB9IH0pOwogICAgfSk7CiAgICBjaGlsZC5vbigiY2xvc2UiLCAoY29kZSwgc2lnbmFsKSA9PiB7CiAgICAgIGNsZWFyVGltZW91dCh0aW1lcik7CiAgICAgIGNvbnN0IG91dCA9IHN0ZG91dC50cmltKCk7CiAgICAgIGNvbnN0IGVyciA9IHN0ZGVyci50cmltKCk7CiAgICAgIGlmIChjb2RlICE9PSAwKSB7CiAgICAgICAgcmVzb2x2ZSh7IG9rOiBmYWxzZSwgZXJyb3I6IHsgbWVzc2FnZTogZXJyIHx8IG91dCB8fCBgZ2F0ZXdheSBjYWxsIGV4aXRlZCAke2NvZGV9JHtzaWduYWwgPyBgIHNpZ25hbD0ke3NpZ25hbH1gIDogIiJ9YCB9IH0pOwogICAgICAgIHJldHVybjsKICAgICAgfQogICAgICB0cnkgewogICAgICAgIHJlc29sdmUobm9ybWFsaXplUmVzdWx0KEpTT04ucGFyc2Uob3V0KSkpOwogICAgICB9IGNhdGNoIChlcnJvcikgewogICAgICAgIHJlc29sdmUoeyBvazogZmFsc2UsIGVycm9yOiB7IG1lc3NhZ2U6IGBpbnZhbGlkIGdhdGV3YXkgSlNPTjogJHtTdHJpbmcoZXJyb3IgJiYgZXJyb3IubWVzc2FnZSA/IGVycm9yLm1lc3NhZ2UgOiBlcnJvcil9YCB9IH0pOwogICAgICB9CiAgICB9KTsKICB9KTsKfQoKY29uc3QgcmwgPSByZWFkbGluZS5jcmVhdGVJbnRlcmZhY2UoeyBpbnB1dDogcHJvY2Vzcy5zdGRpbiwgY3JsZkRlbGF5OiBJbmZpbml0eSB9KTsKbGV0IGNoYWluID0gUHJvbWlzZS5yZXNvbHZlKCk7CnJsLm9uKCJsaW5lIiwgKGxpbmUpID0+IHsKICBjaGFpbiA9IGNoYWluLnRoZW4oYXN5bmMgKCkgPT4gewogICAgbGV0IHJlcXVlc3Q7CiAgICB0cnkgewogICAgICByZXF1ZXN0ID0gSlNPTi5wYXJzZShsaW5lKTsKICAgIH0gY2F0Y2ggKGVycm9yKSB7CiAgICAgIHJlc3BvbmQobnVsbCwgeyBvazogZmFsc2UsIGVycm9yOiB7IG1lc3NhZ2U6ICJpbnZhbGlkIHJlcXVlc3QgSlNPTiIgfSB9KTsKICAgICAgcmV0dXJuOwogICAgfQogICAgY29uc3QgcGF5bG9hZCA9IGF3YWl0IGNhbGxHYXRld2F5KHJlcXVlc3QpOwogICAgcmVzcG9uZChyZXF1ZXN0LmlkIHx8IG51bGwsIHBheWxvYWQpOwogIH0pOwp9KTsK' python3.12 - "$control_bin" "$runtime_script" <<'PY2'
from pathlib import Path
import base64, marshal, os, re, sys, types
control_bin = Path(sys.argv[1]); runtime_script = Path(sys.argv[2]); root = runtime_script.parents[1]
text = control_bin.read_text()
text = text.replace('CLAW_TRADE_SKIP_OPENCLAW_WEIXIN_PLUGIN_INSTALL="${CLAW_TRADE_SKIP_OPENCLAW_WEIXIN_PLUGIN_INSTALL:-1}"', 'CLAW_TRADE_SKIP_OPENCLAW_WEIXIN_PLUGIN_INSTALL="${CLAW_TRADE_SKIP_OPENCLAW_WEIXIN_PLUGIN_INSTALL:-0}"')
control_bin.write_text(text)
text = runtime_script.read_text()
text = text.replace('OPENCLAW_GATEWAY_PORT=18789\n', 'OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"\n')
text, removed = re.subn(r'\n  systemPromptOverride: \[\n(?:    .+\n)+?  \]\.join\("\\n"\),', '', text)
if removed not in (0, 2): raise SystemExit(f'unexpected systemPromptOverride removal count: {removed}')
text = text.replace('  bundledDiscovery: "compat",\n', '')
text = text.replace('plugins install "${OPENCLAW_WEIXIN_PLUGIN_SPEC}"', 'plugins install --force "${OPENCLAW_WEIXIN_PLUGIN_SPEC}"')
old = 'write_runtime_env_var "OPENCLAW_GATEWAY_URL" "${OPENCLAW_GATEWAY_URL}"\n'
new = old + 'write_runtime_env_var "OPENCLAW_GATEWAY_TOKEN" "${OPENCLAW_GATEWAY_TOKEN}"\n'
if new not in text and old in text: text = text.replace(old, new, 1)
runtime_script.write_text(text)
helper = root / 'app/scripts/openclaw-gateway-rpc-helper.mjs'
helper.parent.mkdir(parents=True, exist_ok=True)
helper.write_bytes(base64.b64decode(os.environ['HELPER_TEXT_B64']))
helper.chmod(0o755)
pyc = root / 'app/python/claw_trade/ui_backend/channel_bridge.pyc'
if pyc.exists():
    raw = pyc.read_bytes(); header, code = raw[:16], marshal.loads(raw[16:])
    def patch_code(c):
        changed = False; consts = []
        for x in c.co_consts:
            if isinstance(x, types.CodeType):
                y = patch_code(x); consts.append(y); changed = changed or y is not x
            elif c.co_name == '_request_qr_login' and x == 12000:
                consts.append(30000); changed = True
            else: consts.append(x)
        return c.replace(co_consts=tuple(consts)) if changed else c
    patched = patch_code(code)
    if patched is not code: pyc.write_bytes(header + marshal.dumps(patched))
PY2
}
if [[ -n "$package" ]]; then
  [[ -f "$package" ]] || fail "package not found: $package"
  log "installing package: $package"
  sudo mkdir -p "${install_root}/releases"
  top_dir="$(tar -tzf "$package" | head -1 | cut -d/ -f1)"; [[ -n "$top_dir" ]] || fail "cannot read package top directory"
  sudo tar -xzf "$package" -C "${install_root}/releases"
  sudo ln -sfn "${install_root}/releases/${top_dir}" "${install_root}/current"
  sudo chown -R "${USER}:${USER}" "${install_root}/releases/${top_dir}"
elif [[ -x "${install_root}/current/bin/claw-trade-control" ]]; then
  log "package missing; using existing current release: ${install_root}/current"
else
  fail "no package supplied and ${install_root}/current is not installed"
fi
patch_runtime_control
"${install_root}/current/bin/claw-trade-preflight"
runtime_env="${install_root}/current/.runtime/dev-services/runtime.env"
log "starting runtime control"
stop_ui; stop_control
rm -f "$control_log" "$runtime_env"
export OPENCLAW_GATEWAY_PORT="$(select_openclaw_gateway_port)"
export OPENCLAW_GATEWAY_URL="ws://127.0.0.1:${OPENCLAW_GATEWAY_PORT}"
setsid nohup "${install_root}/current/bin/claw-trade-control" >"$control_log" 2>&1 < /dev/null & echo "$!" >"$control_pid_file"
control_pid="$(cat "$control_pid_file")"
for _ in $(seq 1 180); do [[ -f "$runtime_env" ]] && break; kill -0 "$control_pid" 2>/dev/null || { tail_log "$control_log"; fail "runtime control exited before ready"; }; sleep 1; done
[[ -f "$runtime_env" ]] || { tail_log "$control_log"; fail "runtime control did not become ready"; }
log "starting UI"
stop_ui; rm -f "$ui_log"
setsid nohup bash -c 'set -euo pipefail; runtime_env="$1"; ui_bin="$2"; set -a; . "${runtime_env}"; set +a; exec "${ui_bin}"' bash "$runtime_env" "${install_root}/current/bin/claw-trade-ui" >"$ui_log" 2>&1 < /dev/null & echo "$!" >"$ui_pid_file"
ui_pid="$(cat "$ui_pid_file")"; ui_ready=0
for _ in $(seq 1 90); do if curl -fsS http://127.0.0.1:5175/ >/dev/null; then ui_ready=1; break; fi; kill -0 "$ui_pid" 2>/dev/null || { tail_log "$ui_log"; fail "UI process exited before responding"; }; sleep 1; done
[[ "$ui_ready" == 1 ]] || { tail_log "$ui_log"; fail "UI did not respond on 127.0.0.1:5175 within 90s"; }
cat <<EOF
[OK] claw-trade installed
Current: ${install_root}/current
UI: http://$(hostname -I | awk '{print $1}'):5175/
Log: ${ui_log}
Control log: ${control_log}
EOF
