from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlopen

from tests.settings.s16_test_helpers import (
    PROJECT_ROOT,
    SETTINGS_S16_ARTIFACT_DIR,
    ensure_s16_dirs,
    read_text,
    write_json,
    write_text,
)


def _read_text_bundle(directory: Path) -> str:
    chunks: list[str] = []
    for path in sorted(directory.glob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".md", ".log", ".json", ".html"}:
            continue
        chunks.append(read_text(path))
    return "\n".join(chunks).lower()


def _load_runtime_env(runtime_env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not runtime_env_path.exists():
        return values
    for raw in runtime_env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _runtime_value(key: str, runtime_env: dict[str, str]) -> str | None:
    value = os.environ.get(key)
    if value is not None:
        return value
    return runtime_env.get(key)


def _health_status(url: str) -> tuple[bool, int | None]:
    try:
        with urlopen(url, timeout=2) as response:  # nosec B310: localhost health check
            return response.status == 200, int(response.status)
    except Exception:
        return False, None


def test_settings_preflight_evidence_contract_and_hitl_matrix_coverage() -> None:
    ensure_s16_dirs()
    artifacts_root = PROJECT_ROOT / ".runtime" / "test-artifacts"
    runtime_env_path = PROJECT_ROOT / ".runtime" / "dev-services" / "runtime.env"
    runtime_env = _load_runtime_env(runtime_env_path)
    health_1933_ok, health_1933_status = _health_status("http://127.0.0.1:1933/health")
    health_18789_ok, health_18789_status = _health_status("http://127.0.0.1:18789/health")
    expected_config = str((PROJECT_ROOT / ".runtime" / "dev-services" / "openviking" / "ov.conf").resolve())
    expected_data = str((PROJECT_ROOT / ".runtime" / "dev-services" / "openviking" / "data").resolve())

    rows = [
        ("runtime.env exists", runtime_env_path.exists(), str(runtime_env_path.exists())),
        (
            "CLAW_TRADE_OPENVIKING_MCP_MODULE unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_MCP_MODULE", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_MCP_MODULE", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_MCP_CWD unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_MCP_CWD", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_MCP_CWD", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_SERVER_BIN unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_SERVER_BIN", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_SERVER_BIN", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_SERVER_CWD unset",
            _runtime_value("CLAW_TRADE_OPENVIKING_SERVER_CWD", runtime_env) in {None, ""},
            str(_runtime_value("CLAW_TRADE_OPENVIKING_SERVER_CWD", runtime_env)),
        ),
        (
            "OPENVIKING_CONFIG_FILE points to .runtime/dev-services/openviking/ov.conf",
            _runtime_value("OPENVIKING_CONFIG_FILE", runtime_env) == expected_config,
            str(_runtime_value("OPENVIKING_CONFIG_FILE", runtime_env)),
        ),
        (
            "OPENVIKING_DATA_DIR points to .runtime/dev-services/openviking/data",
            _runtime_value("OPENVIKING_DATA_DIR", runtime_env) == expected_data,
            str(_runtime_value("OPENVIKING_DATA_DIR", runtime_env)),
        ),
        (
            "CLAW_TRADE_OPENVIKING_MCP_STARTED=0",
            _runtime_value("CLAW_TRADE_OPENVIKING_MCP_STARTED", runtime_env) == "0",
            str(_runtime_value("CLAW_TRADE_OPENVIKING_MCP_STARTED", runtime_env)),
        ),
        ("1933 /health", health_1933_ok, str(health_1933_status)),
        ("18789 /health", health_18789_ok, str(health_18789_status)),
    ]
    preflight_lines = [
        "runtime_profile=scripts/start-control-runtime.sh; repo uv; OpenViking 1933; OpenClaw gateway 18789; no invest sidecar; .runtime/dev-services",
        "",
        "| check | pass | observed |",
        "| --- | --- | --- |",
    ]
    preflight_lines.extend([f"| {name} | {'PASS' if ok else 'FAIL'} | {observed} |" for name, ok, observed in rows])
    write_text(SETTINGS_S16_ARTIFACT_DIR / "runtime-profile-and-preflight.md", "\n".join(preflight_lines) + "\n")
    for name, ok, observed in rows:
        assert ok, f"{name} failed: {observed}"

    setting_dirs = [
        "settings-s01",
        "settings-s02",
        "settings-s03",
        "settings-s04",
        "settings-s05",
        "settings-s06",
        "settings-s07",
        "settings-s08",
        "settings-s09",
        "settings-s10",
        "settings-s11",
        "settings-s12",
        "settings-s13",
        "settings-s14",
        "settings-s15",
        "settings-s16",
    ]

    preflight_results: dict[str, dict[str, bool]] = {}
    for name in setting_dirs:
        directory = artifacts_root / name
        assert directory.exists(), f"missing evidence directory: {directory}"
        blob = _read_text_bundle(directory)
        checks = {
            "runtime_profile": ("runtime_profile" in blob) or ("runtime profile" in blob),
            "openviking_1933": "1933" in blob,
            "openclaw_18789": "18789" in blob,
            "runtime_env": "runtime.env" in blob,
            "ov_conf": "ov.conf" in blob,
            "openviking_data_dir": "openviking/data" in blob,
            "mcp_started_zero": ("claw_trade_openviking_mcp_started" in blob and "=0" in blob)
            or ("mcp_started_zero" in blob),
        }
        for check_name, ok in checks.items():
            assert ok, f"{name} missing preflight evidence: {check_name}"
        preflight_results[name] = checks

    tasks_doc = read_text(PROJECT_ROOT / "docs" / "设置模块任务清单.md")
    design_doc = read_text(PROJECT_ROOT / "docs" / "设置模块设计文档.md")
    agents_doc = read_text(PROJECT_ROOT / "AGENTS.md")
    approved_scope_live_test = read_text(
        PROJECT_ROOT / "tests" / "settings" / "live" / "test_enhanced_source_approved_scope_live.py"
    )
    report_model_gate_test = read_text(PROJECT_ROOT / "tests" / "settings" / "test_report_model_gate.py")
    forbidden_surface_test = read_text(PROJECT_ROOT / "tests" / "settings" / "test_settings_forbidden_surface.py")
    no_fake_success_live_test = read_text(
        PROJECT_ROOT / "tests" / "settings" / "live" / "test_settings_no_fake_success_live.py"
    )
    frontend_forbidden_surface_test = read_text(
        PROJECT_ROOT / "web" / "research-ui" / "src" / "__tests__" / "settings-forbidden-surface.test.tsx"
    )
    routes_ui_source = read_text(PROJECT_ROOT / "src" / "claw_trade" / "web" / "routes_ui.py")
    ui_backend_source = "\n".join(
        [
            read_text(PROJECT_ROOT / "src" / "claw_trade" / "ui_backend" / "llm_settings_bridge.py"),
            read_text(PROJECT_ROOT / "src" / "claw_trade" / "ui_backend" / "workflow_bridge.py"),
            read_text(PROJECT_ROOT / "src" / "claw_trade" / "ui_backend" / "chat_controller.py"),
        ]
    )

    hitl_matrix = {
        "需改 OpenClaw source 且超出通用 runtime seam": [
            ("docs/设置模块任务清单.md", "需改 OpenClaw source 且超出通用 runtime seam"),
            ("AGENTS.md", "Changing OpenClaw source outside the approved generic runtime seam scope."),
        ],
        "需改变 PM 最终决策权或让 Python 改写 PM 结论/评级": [
            ("docs/设置模块任务清单.md", "需改变 PM 最终决策权或让 Python 改写 PM 结论/评级"),
            ("AGENTS.md", "Changing PM final decision authority."),
        ],
        "需保留/新增 direct LLM 报告路径": [
            ("docs/设置模块任务清单.md", "需保留/新增 direct LLM 报告路径"),
            ("AGENTS.md", "Keeping or adding direct LLM report path."),
        ],
        "需新增 fallback prompt/tool、fake provider 结果、fake artifact 成功": [
            ("docs/设置模块任务清单.md", "需新增 fallback prompt/tool、fake provider 结果、fake artifact 成功"),
            ("AGENTS.md", "Adding fallback prompt, fallback tool, fake provider result, or fake artifact success."),
        ],
        "需放松 unsafe/fabrication/chart 硬门禁": [
            ("docs/设置模块任务清单.md", "需放松 unsafe/fabrication/chart 硬门禁"),
            ("AGENTS.md", "Relaxing unsafe/fabrication/chart hard gates."),
        ],
        "需在要求 provider payload 的场景不采集真实 provider payload": [
            ("docs/设置模块任务清单.md", "需在要求 provider payload 的场景不采集真实 provider payload"),
            ("AGENTS.md", "Running without true provider payload capture where provider payload is required."),
        ],
        "HK/CRYPTO prompt 策略不清且影响实现": [
            ("docs/设置模块任务清单.md", "HK/CRYPTO prompt 策略不清且影响实现"),
            ("AGENTS.md", "Choosing HK or CRYPTO prompt strategy when the strategy is unclear and affects implementation."),
        ],
        "需改 worker 调度、执行归属、重试 owner/budget、Python vs OpenClaw 责任边界": [
            ("docs/设置模块任务清单.md", "需改 worker 调度、执行归属、重试 owner/budget、Python vs OpenClaw 责任边界"),
            (
                "AGENTS.md",
                "Changing worker scheduling, execution authority, retry owner, retry budget, or Python vs OpenClaw responsibility boundary.",
            ),
        ],
        "同一 gate 类别连续两次 focused 修复仍失败": [
            ("docs/设置模块任务清单.md", "同一 gate 类别连续两次 focused 修复仍失败"),
            ("AGENTS.md", "Continuing after the same gate category fails twice after focused fixes."),
        ],
        "docs/设置模块设计文档.md:280 边界": [
            ("docs/设置模块设计文档.md", "如果实现需要改变 OpenClaw 非通用 runtime seam、PM 权限、direct LLM 路径"),
            ("docs/设置模块任务清单.md", "docs/设置模块设计文档.md:280"),
        ],
        "增强源 approved scope 来源不明、冲突或无法举证": [
            ("docs/设置模块任务清单.md", "approved scope/系统批准范围"),
            ("tests/settings/live/test_enhanced_source_approved_scope_live.py", "approved scope evidence missing"),
        ],
    }

    source_map = {
        "docs/设置模块任务清单.md": tasks_doc,
        "docs/设置模块设计文档.md": design_doc,
        "AGENTS.md": agents_doc,
        "tests/settings/live/test_enhanced_source_approved_scope_live.py": approved_scope_live_test,
    }
    for item, checks in hitl_matrix.items():
        for source, phrase in checks:
            assert phrase in source_map[source], f"missing HITL mapping evidence for `{item}` in `{source}`"

    behavior_contract = {
        "report_model_gate_hard_block_exists": "test_report_command_is_hard_blocked_when_model_not_ready" in report_model_gate_test
        and "blocked is True" in report_model_gate_test,
        "wechat_not_logged_in_non_blocking_assert_exists": "未登录不阻断工作台使用" in forbidden_surface_test
        and "未登录不阻断工作台使用" in frontend_forbidden_surface_test,
        "wechat_hard_blocking_negative_assert_exists": "wechat-hard-blocking" in forbidden_surface_test
        and "必须先连接微信" in frontend_forbidden_surface_test,
        "fake_success_negative_assert_exists": "fake-login-success" in forbidden_surface_test
        and "登录成功" in frontend_forbidden_surface_test,
        "no_fake_success_scan_contract_exists": "\"src\" / \"claw_trade\" / \"ui_backend\"" in no_fake_success_live_test
        and "\"src\" / \"claw_trade\" / \"web\"" in no_fake_success_live_test
        and "\"web\" / \"research-ui\" / \"src\"" in no_fake_success_live_test
        and "\"tests\" / \"settings\"" in no_fake_success_live_test
        and "implementation_risk" in no_fake_success_live_test,
        "approved_scope_stop_assert_exists": "approved scope evidence missing" in approved_scope_live_test,
        "no_direct_llm_entry_in_ui_backend_or_routes": "direct_llm" not in routes_ui_source.lower()
        and "direct_llm" not in ui_backend_source.lower(),
    }
    for item, ok in behavior_contract.items():
        assert ok, f"missing HITL behavioral contract evidence: {item}"

    write_json(SETTINGS_S16_ARTIFACT_DIR / "preflight-evidence-contract.json", preflight_results)
    write_json(
        SETTINGS_S16_ARTIFACT_DIR / "hitl-stop-and-ask-matrix-coverage.json",
        {"doc_matrix": hitl_matrix, "behavior_contract": behavior_contract},
    )
    write_text(
        SETTINGS_S16_ARTIFACT_DIR / "preflight-evidence-contract.md",
        "\n".join(
            [
                f"- {name}: runtime_profile={checks['runtime_profile']}, 1933={checks['openviking_1933']}, "
                f"18789={checks['openclaw_18789']}, runtime.env={checks['runtime_env']}, ov.conf={checks['ov_conf']}, "
                f"openviking/data={checks['openviking_data_dir']}, mcp_started_zero={checks['mcp_started_zero']}"
                for name, checks in preflight_results.items()
            ]
        )
        + "\n",
    )
