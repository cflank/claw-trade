from __future__ import annotations

import subprocess
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "start-research-ui.sh"


def test_start_research_ui_script_is_bash_valid() -> None:
    script = _script_path()
    completed = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_start_research_ui_script_contains_fixed_runtime_entry() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'CONTROL_RUNTIME_SCRIPT="${ROOT_DIR}/scripts/start-control-runtime.sh"' in text
    assert '"${CONTROL_RUNTIME_SCRIPT}" -- "${SCRIPT_PATH}" --run-backend' in text
    assert 'RESEARCH_UI_INTERNAL_BACKEND_MARKER="${RESEARCH_UI_INTERNAL_BACKEND_MARKER:-CLAW_TRADE_UI_ALLOW_DIRECT_BACKEND_ENTRY}"' in text
    assert '"${RESEARCH_UI_INTERNAL_BACKEND_MARKER}=${RESEARCH_UI_INTERNAL_BACKEND_MARKER_VALUE}"' in text
    assert "CLAW_TRADE_SKIP_ENV_DATA_SOURCE_IMPORT=1" in text
    assert "OpenViking/OpenClaw/Mongo + UI 后端" in text
    assert 'CLAW_TRADE_UI_INBOUND_TIMEOUT_MS="${CLAW_TRADE_UI_INBOUND_TIMEOUT_MS:-60000}"' in text


def test_start_research_ui_script_requires_real_backend_entry_and_api_health_check() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'CLAW_TRADE_UI_BACKEND_MODULE="${CLAW_TRADE_UI_BACKEND_MODULE:-claw_trade.web.app}"' in text
    assert 'importlib.util.find_spec(module_name)' in text
    assert "未找到 UI 后端入口模块" in text
    assert 'RESEARCH_UI_READY_ENDPOINT="${RESEARCH_UI_READY_ENDPOINT:-/api/ui/get-report-queue-snapshot}"' in text
    assert 'cd "${ROOT_DIR}" && uv run python -m "${CLAW_TRADE_UI_BACKEND_MODULE}" \\' in text


def test_start_research_ui_script_runs_backend_from_repo_root() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'cd "${ROOT_DIR}" && bash -lc "${CLAW_TRADE_UI_BACKEND_COMMAND}"' in text
    assert 'cd "${ROOT_DIR}" && uv run python -m "${CLAW_TRADE_UI_BACKEND_MODULE}"' in text


def test_start_research_ui_script_is_not_vite_dev_only() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'pnpm --dir "${RESEARCH_UI_FRONTEND_DIR}" build' in text
    assert "pnpm --dir web/research-ui dev" not in text
    assert "vite dev" not in text
    assert 'curl -s -m 2 -o /dev/null -w' in text
    assert "2>/dev/null || true" in text


def test_start_research_ui_script_defaults_to_skip_frontend_build() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'RESEARCH_UI_BUILD_FRONTEND="${RESEARCH_UI_BUILD_FRONTEND:-0}"' in text
    assert "默认启动不会自动构建前端。请先运行：pnpm --dir web/research-ui build" in text
    assert "使用已有前端 dist（默认不构建）" in text


def test_start_research_ui_script_supports_explicit_build_flag_and_precedence() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "parse_main_args() {" in text
    assert "--build)" in text
    assert 'RESEARCH_UI_BUILD_FROM_CLI=1' in text
    assert "检测到 --build，优先按命令行参数执行前端构建。" in text


def test_start_research_ui_script_prints_dist_missing_hint_without_auto_build() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "前端 dist 目录不存在" in text
    assert "默认启动不会自动构建前端。请先运行：pnpm --dir web/research-ui build" in text
    assert "scripts/start-research-ui.sh --build 或 RESEARCH_UI_BUILD_FRONTEND=1" in text


def test_start_research_ui_script_uses_non_5173_default_ports() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "for candidate in 5175 8787;" in text
    assert "默认不使用 5173（保留给 Vite）" in text


def test_start_research_ui_script_exposes_ui_for_windows_browser_by_default() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'RESEARCH_UI_HOST="${RESEARCH_UI_HOST:-0.0.0.0}"' in text
    assert '--host "${RESEARCH_UI_HOST}"' in text
    assert "browser_hosts_for_research_ui() {" in text
    assert 'printf \'%s\\n\' "127.0.0.1"' in text
    assert "hostname -I" in text
    assert "浏览器访问地址：" in text
    assert 'http://${host}:${RESEARCH_UI_PORT}/' in text
    assert "服务绑定地址：http://${RESEARCH_UI_HOST}:${RESEARCH_UI_PORT}/" in text


def test_start_research_ui_script_cleans_stale_project_backend_before_port_fallback() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert "clear_stale_research_ui_port() {" in text
    assert '[[ "${cmd}" == *"-m ${CLAW_TRADE_UI_BACKEND_MODULE}"* ]]' in text
    assert '[[ "${cmd}" == *"--frontend-dist ${RESEARCH_UI_FRONTEND_DIST}"* ]]' in text
    assert 'log_warn "清理旧 Research UI 后端：pid=${pid} port=${port}"' in text
    assert 'clear_stale_research_ui_port "${RESEARCH_UI_PORT}"' in text
    assert 'clear_stale_research_ui_port "${candidate}"' in text

    clear_index = text.index('clear_stale_research_ui_port "${candidate}"')
    probe_index = text.index('if ! port_in_use_for "${candidate}"; then')
    assert clear_index < probe_index


def test_start_research_ui_script_blocks_direct_run_backend_without_internal_marker() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'marker_value="$(printenv "${RESEARCH_UI_INTERNAL_BACKEND_MARKER}" || true)"' in text
    assert 'if [[ "${marker_value}" != "${RESEARCH_UI_INTERNAL_BACKEND_MARKER_VALUE}" ]]; then' in text
    assert "禁止直接调用 --run-backend" in text
