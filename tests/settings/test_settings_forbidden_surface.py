from __future__ import annotations

import re
from pathlib import Path

from claw_trade.ui_backend.data_source_settings import (
    SUPPORTED_DATA_SOURCE_TYPES,
    DataSourceSettingsService,
)
from tests.settings.s16_test_helpers import (
    PROJECT_ROOT,
    SETTINGS_S16_ARTIFACT_DIR,
    SETTINGS_UI_TEXT_DIR,
    ensure_s16_dirs,
    lowercase_text,
    read_json,
    read_text,
    write_json,
    write_text,
)


def _collect_ui_evidence() -> dict[str, object]:
    sections = read_json(PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s14" / "settings-sections-rendered-evidence.json")
    responsive = read_json(
        PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s15" / "settings-responsive-rendered-evidence.json"
    )
    provider_health = read_json(PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s08" / "provider-health-summary-live.json")
    runtime_status = read_json(
        PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s09" / "runtime-service-status-summary-live.json"
    )
    live_gap = read_json(PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s10" / "live-run-gap-summary-live.json")
    evidence_failure = read_json(
        PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s11" / "evidence-failure-reason-summary-live.json"
    )

    settings = sections.get("evidence", {}).get("settings", {})
    task = sections.get("evidence", {}).get("task", {})
    layouts = responsive.get("layouts", {})

    ordinary_text = "\n".join(
        [
            str(settings.get("settingsText") or ""),
            str(settings.get("sourceText") or ""),
            str(settings.get("wechatText") or ""),
            str(task.get("text") or ""),
            str(layouts.get("settings-desktop", {}).get("pageTextLower") or ""),
            str(layouts.get("settings-tablet-960", {}).get("pageTextLower") or ""),
            str(layouts.get("settings-mobile-560", {}).get("pageTextLower") or ""),
            str(layouts.get("onboarding-tablet-960", {}).get("pageTextLower") or ""),
        ]
    )
    diagnostics_summary_text = "\n".join(
        [
            str(provider_health.get("userMessage") or ""),
            str(runtime_status.get("userMessage") or ""),
            str(live_gap.get("userMessage") or ""),
            str(live_gap.get("recommendedAction") or ""),
            str(evidence_failure.get("userMessage") or ""),
            str(evidence_failure.get("recommendedAction") or ""),
        ]
    )
    return {
        "ordinary_text": ordinary_text,
        "diagnostics_summary_text": diagnostics_summary_text,
        "supported_types": DataSourceSettingsService().list_data_sources().get("supportedTypes"),
        "task_fields": task.get("fields"),
    }


def _assert_not_contains(text: str, terms: tuple[str, ...], *, label: str) -> None:
    lowered = lowercase_text(text)
    for term in terms:
        assert term.lower() not in lowered, f"{label} leaked forbidden term: {term}"


def _assert_contains_any(text: str, terms: tuple[str, ...], *, label: str) -> None:
    lowered = lowercase_text(text)
    assert any(term.lower() in lowered for term in terms), f"{label} missing expected evidence: {terms}"


def test_settings_forbidden_surface_uses_real_ui_artifacts_and_writes_text_snapshots() -> None:
    ensure_s16_dirs()
    evidence = _collect_ui_evidence()
    ordinary_text = str(evidence["ordinary_text"])
    diagnostics_summary_text = str(evidence["diagnostics_summary_text"])
    supported_types = evidence["supported_types"]
    task_fields = evidence["task_fields"]

    _assert_not_contains(
        ordinary_text,
        (
            "profile",
            "默认市场",
            "默认币种",
            "报告方案",
            "计价单位",
            "worker",
            "debate",
            "risk",
            "openclaw gateway",
            "channel id",
            "scope",
            "provider attempt",
            "proxyurl",
            "headername",
            "priority",
            "代理地址",
            "请求头名",
            "优先级",
            "任意 http",
            "json 映射",
            "provider 优先级",
            "wechat_clawbot",
            "openclaw_plugins",
            "@openclaw",
            "plugin_package",
            "/report",
            "报告指令",
            "hero",
            "marketing",
        ),
        label="ordinary-ui",
    )
    _assert_not_contains(
        diagnostics_summary_text,
        (
            "provider attempt",
            "raw payload",
            "request body",
            "uri=",
            " hash=",
            " l1",
            " l2",
            "receipt",
            "gateway",
            "scope",
            "18789",
            "1933",
            "/runtime/",
        ),
        label="advanced-diagnostics-summary",
    )

    _assert_contains_any(
        ordinary_text,
        ("未登录不阻断工作台使用", "未获取到二维码时，可先进入浏览器工作台", "可先继续配置报告模型"),
        label="wechat-non-blocking",
    )
    _assert_not_contains(
        ordinary_text,
        ("先连接微信才能使用工作台", "必须先连接微信", "微信未登录不可使用工作台"),
        label="wechat-hard-blocking",
    )
    _assert_not_contains(
        ordinary_text,
        ("登录成功", "已登录", "连接成功", "在线中", "已在线"),
        label="fake-login-success",
    )

    assert task_fields == ["标的", "名称", "市场"]
    supported_types_lower = {str(item).lower() for item in supported_types or []}
    assert supported_types_lower == {item.lower() for item in SUPPORTED_DATA_SOURCE_TYPES}
    for unapproved in (
        "custom_http",
        "custom_json",
        "unknown_http_json",
        "akshare",
        "eastmoney",
        "xueqiu",
        "baostock",
        "cninfo",
        "yahoo_finance",
        "csmar",
        "resset",
        "joinquant",
        "ricequant",
        "wind",
        "choice",
        "ifind",
        "iex_cloud",
        "twelve_data",
        "marketstack",
        "eodhd",
        "intrinio",
        "fmp",
        "polygon",
        "tiingo",
        "nasdaq_data_link",
        "bloomberg",
        "lseg_refinitiv",
        "factset",
        "morningstar",
        "sp_capital_iq",
        "tradingview",
        "barchart",
        "finazon",
        "benzinga",
        "newsapi",
        "reuters",
        "dow_jones",
        "x",
        "reddit",
        "stocktwits",
        "bea",
        "eia",
        "coingecko",
        "coinmarketcap",
        "binance",
        "okx",
        "ccxt",
        "sec_edgar",
        "cryptocompare",
        "messari",
        "santiment",
        "coinmetrics",
        "dune",
        "nansen",
        "token_terminal",
        "the_graph",
        "kaiko",
        "amberdata",
        "lunarcrush",
    ):
        assert unapproved not in supported_types_lower

    write_text(SETTINGS_UI_TEXT_DIR / "ordinary-settings-ui.txt", ordinary_text + "\n")
    write_text(SETTINGS_UI_TEXT_DIR / "advanced-diagnostics-summary.txt", diagnostics_summary_text + "\n")
    write_text(
        SETTINGS_UI_TEXT_DIR / "source-provider-snapshot.txt",
        "supported_types=" + ",".join(str(item) for item in supported_types or []) + "\n",
    )


def test_forbidden_surface_matrix_every_item_has_negative_coverage_mapping() -> None:
    ensure_s16_dirs()
    coverage = {
        "让用户选择 profile": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(profile)",
        "让用户设置默认市场或默认币种": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(默认市场/默认币种)",
        "让用户设置 worker / debate / risk 轮数": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(worker/debate/risk)",
        "暴露 OpenClaw 原始配置": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(openclaw gateway/配置词)",
        "暴露 OpenClaw gateway": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui+diagnostics 禁词断言(gateway)",
        "暴露 Channel ID": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(channel id)",
        "暴露插件包名": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(wechat_clawbot/openclaw_plugins/@openclaw)",
        "暴露配置路径": "tests/settings/test_settings_forbidden_surface.py::advanced diagnostics 禁词断言(/runtime/)",
        "暴露 scope": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui+diagnostics 禁词断言(scope)",
        "暴露 worker id": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(worker)",
        "暴露 runtime 内部细节": "tests/settings/test_settings_forbidden_surface.py::advanced diagnostics 禁词断言(1933/18789/runtime path)",
        "暴露 provider attempt": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui+diagnostics 禁词断言(provider attempt)",
        "暴露证据链细节": "tests/settings/test_settings_forbidden_surface.py::advanced diagnostics 禁词断言(uri/hash/l1/l2/receipt)",
        "把 OpenClaw 主界面入口放回设置页": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(openclaw 主界面)",
        "把微信设成浏览器工作台硬阻断": "tests/settings/test_settings_forbidden_surface.py::wechat-non-blocking 正向断言 + wechat-hard-blocking 负向断言",
        "在 UI 写微信端报告指令用法": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(/report/报告指令)",
        "允许任意 HTTP / JSON 数据源": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(任意 http/json 映射)",
        "允许用户调整 provider 优先级": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(provider 优先级)",
        "允许用户选择数据源适用范围": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 禁词断言(scope/适用范围)",
        "显示默认免费源": "tests/settings/test_settings_forbidden_surface.py::source-provider-snapshot 只显示需要 API 的内置源",
        "伪造登录成功或假在线": "tests/settings/test_settings_forbidden_surface.py::fake-login-success 禁词断言(登录成功/已登录/连接成功/在线中/已在线)",
        "在 approved scope 来源不明时继续实现或放行运行": "tests/settings/live/test_enhanced_source_approved_scope_live.py::fixed 12 + catalog/run-plan/coverage 元数据 approved scope 断言",
        "隐藏增强源失败或伪装成功": "tests/settings/live/test_enhanced_source_attempts_live.py::default-source success 且无 fake validated 断言",
        "把保存配置等同于测试通过": "tests/settings/test_report_model_gate.py::saved_unverified 断言",
        "模型测试失败后静默切换默认模型": "tests/settings/test_report_model_gate.py::失败后仍 blocked 断言",
        "普通 UI 出现未经确认的说明或猜测性承诺": "tests/settings/test_settings_forbidden_surface.py::ordinary-ui 文本禁词断言",
        "大渐变、hero、营销页、复杂后台风": "tests/settings/test_settings_forbidden_surface.py::styleForbiddenChecks 证据",
        "CSS 无关全站重构或脱离 ct-* 命名体系": "web/research-ui/src/__tests__/settings-css-onboarding.test.tsx::ct-* 样式契约",
        "用静态扫描/静态 render 替代真实 UI 证据": "tests/settings/ui/test_settings_sections_visual.py + test_settings_responsive_visual.py::playwright screenshot 证据",
    }
    assert len(coverage) == 29
    for item, evidence in coverage.items():
        assert item.strip()
        assert evidence.strip()

    styles_evidence = read_json(
        PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s15" / "settings-responsive-rendered-evidence.json"
    )
    checks = styles_evidence.get("styleForbiddenChecks")
    assert isinstance(checks, dict)
    for key in ("contains_hero", "contains_marketing", "contains_linear_gradient", "contains_radial_gradient"):
        assert checks.get(key) is False

    write_json(SETTINGS_S16_ARTIFACT_DIR / "forbidden-surface-matrix-coverage.json", coverage)
    lines = [f"- {item}: {evidence}" for item, evidence in coverage.items()]
    write_text(SETTINGS_S16_ARTIFACT_DIR / "forbidden-surface-matrix-coverage.md", "\n".join(lines) + "\n")


def test_memory_has_s01_to_s15_records_with_required_format() -> None:
    ensure_s16_dirs()
    memory_path = PROJECT_ROOT / "memory" / "2026-05-23.md"
    content = read_text(memory_path)
    missing: list[str] = []
    for task in (
        "s01",
        "s02",
        "s03",
        "s04",
        "s05",
        "s06",
        "s07",
        "s08",
        "s09",
        "s10",
        "s11",
        "s12",
        "s13",
        "s14",
        "s15",
    ):
        if f"[codex-{task}" not in content:
            missing.append(task)
    assert not missing, f"missing memory entries: {missing}"

    for match in re.finditer(r"^##\s+\d{2}:\d{2}\s+\[codex-s\d{2}[^\]]*\]$", content, re.MULTILINE):
        start = match.start()
        tail = content[start : start + 500]
        assert "-完成：" in tail

    write_text(SETTINGS_S16_ARTIFACT_DIR / "memory-format-check.txt", "memory_s01_to_s15=PASS\n")
