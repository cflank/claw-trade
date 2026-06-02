from __future__ import annotations

import contextlib
import json
import socket
import struct
import subprocess
import time
from pathlib import Path
from urllib.request import Request
from urllib.request import urlopen

from claw_trade.ui_backend.data_source_settings import SUPPORTED_DATA_SOURCE_TYPES


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http_ok(url: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with contextlib.suppress(Exception):
            with urlopen(url, timeout=2) as response:  # nosec B310: test-only localhost call
                if response.status == 200:
                    return
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {url}")


def _read_text(url: str) -> str:
    with urlopen(url, timeout=5) as response:  # nosec B310: test-only localhost call
        return response.read().decode("utf-8", errors="replace")


def _read_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=60) as response:  # nosec B310: test-only localhost call
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body)
    assert isinstance(parsed, dict), f"unexpected response payload for {url}"
    return parsed


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:  # nosec B310: test-only localhost call
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body)
    assert isinstance(parsed, dict), f"unexpected response payload for {url}"
    return parsed


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"invalid png: {path}"
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _capture_with_playwright(*, base_url: str, artifact_dir: Path) -> dict[str, object]:
    capture_script = """
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

payload = json.loads(sys.argv[1])
base_url = payload["base_url"]
artifact_dir = Path(payload["artifact_dir"])
artifact_dir.mkdir(parents=True, exist_ok=True)

def section_png(name: str) -> str:
    return str(artifact_dir / name)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
    page = context.new_page()

    page.goto(f"{base_url}/settings", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('[data-testid="settings-page"]', timeout=30000)
    page.wait_for_timeout(600)

    report_model = page.locator('[data-testid="settings-section-report-model"]')
    embedding = page.locator('[data-testid="settings-section-embedding"]')
    source = page.locator('[data-testid="settings-section-data-sources"]')
    wechat = page.locator('[data-testid="settings-section-wechat"]')
    reset = page.locator('[data-testid="settings-section-reset"]')
    report_model.screenshot(path=section_png("settings-report-model-section.png"))
    embedding.screenshot(path=section_png("settings-embedding-section.png"))
    source.screenshot(path=section_png("settings-data-source-section.png"))
    wechat.screenshot(path=section_png("settings-wechat-section.png"))
    reset.screenshot(path=section_png("settings-reset-section.png"))

    settings_evidence = page.evaluate(
        '''() => {
            const root = document.querySelector('[data-testid="settings-page"]');
            const sectionTitles = Array.from(
                document.querySelectorAll('[data-testid="settings-page"] .ct-settings-section h2')
            ).map((item) => item.textContent?.trim() ?? "");
            const sourceRows = document.querySelectorAll('.ct-source-row').length;
            const sourceCards = document.querySelectorAll('[data-testid="settings-section-data-sources"] .ct-source-category-card').length;
            const tabCount = document.querySelectorAll('[role="tab"]').length;
            const sourceText = (document.querySelector('[data-testid="settings-section-data-sources"]')?.textContent ?? "").toLowerCase();
            const wechatText = (document.querySelector('[data-testid="settings-section-wechat"]')?.textContent ?? "").toLowerCase();
            return {
                sectionTitles,
                sourceRows,
                sourceCards,
                tabCount,
                settingsText: (root?.textContent ?? "").toLowerCase(),
                sourceText,
                wechatText,
                qrImageCount: document.querySelectorAll('[data-testid="settings-section-wechat"] .ct-qr-frame img').length,
                qrFrameRect: (() => {
                    const frame = document.querySelector('[data-testid="settings-section-wechat"] .ct-qr-frame');
                    if (!frame) return null;
                    const rect = frame.getBoundingClientRect();
                    return {
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                    };
                })(),
            };
        }'''
    )

    page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('[data-testid="workspace-layout"]', timeout=30000)
    page.get_by_label("输入消息").fill("/report BTC")
    page.get_by_role("button", name="发送").click()
    page.wait_for_selector('[data-testid^="confirmation-card-"]', timeout=30000)
    card = page.locator('[data-testid^="confirmation-card-"]').first
    card.screenshot(path=section_png("report-task-card-section.png"))
    task_evidence = page.evaluate(
        '''() => {
            const card = document.querySelector('[data-testid^="confirmation-card-"]');
            if (!card) {
                return null;
            }
            const fields = Array.from(card.querySelectorAll('.ct-task-field-label')).map(
                (item) => item.textContent?.trim() ?? ""
            );
            const text = (card.textContent ?? "").toLowerCase();
            const outerMessage = card.closest('.ct-message');
            const outerStyle = outerMessage ? window.getComputedStyle(outerMessage) : null;
            return {
                fields,
                text,
                hasTaskClass: card.classList.contains('ct-task-confirm'),
                outerMessageClass: outerMessage ? outerMessage.className : "",
                outerMessageBorderStyle: outerStyle?.borderStyle ?? "",
            };
        }'''
    )
    context.close()
    browser.close()

print(json.dumps({"settings": settings_evidence, "task": task_evidence}, ensure_ascii=False))
"""
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            capture_script,
            json.dumps({"base_url": base_url, "artifact_dir": str(artifact_dir)}, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            "playwright capture failed: "
            f"code={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    evidence = json.loads(result.stdout.strip())
    assert isinstance(evidence, dict), "invalid playwright evidence payload"
    return evidence


def test_settings_sections_visual_surface_live() -> None:
    project_root = Path(__file__).resolve().parents[3]
    frontend_dist = project_root / "web" / "research-ui" / "dist"
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s14"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    assert frontend_dist.exists(), "frontend dist missing, run `pnpm --dir web/research-ui build` first"

    port = _find_free_port()
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "claw_trade.web.app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--frontend-dist",
            str(frontend_dist),
        ],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_http_ok(f"{base_url}/healthz")
        settings_html = _read_text(f"{base_url}/settings")
        home_html = _read_text(base_url)
        assert "id=\"root\"" in settings_html
        assert "id=\"root\"" in home_html
        llm_result = _read_json(f"{base_url}/api/ui/load-llm-settings")
        llm_draft = llm_result.get("draft")
        assert isinstance(llm_draft, dict)
        model_status = llm_draft.get("reportModelStatus")
        assert isinstance(model_status, dict), "missing reportModelStatus payload"
        report_state = str(model_status.get("state") or "").strip()
        assert report_state == "ready", (
            "S-14 precondition not met: load-llm-settings reportModelStatus.state must be ready "
            "before real /report task-card capture; do not fake readiness."
        )
        save_result = _post_json(
            f"{base_url}/api/ui/save-data-source-instance",
            {
                "requestId": "s14-sections-save-data-source",
                "instance": {
                    "supportedType": "tushare",
                    "displayName": "Tushare",
                    "enabled": False,
                    "state": "draft",
                    "endpointUrl": "https://api.tushare.pro",
                },
            },
        )
        assert str(save_result.get("supportedType", "")).lower() == "tushare"
        assert str(save_result.get("displayName", "")) == "Tushare"
        assert bool(save_result.get("enabled")) is False
        listed_sources = _read_json(f"{base_url}/api/ui/list-data-sources")
        assert listed_sources.get("supportedTypes") == list(SUPPORTED_DATA_SOURCE_TYPES)
        source_instances = listed_sources.get("instances")
        assert isinstance(source_instances, list)
        assert len(source_instances) == len(SUPPORTED_DATA_SOURCE_TYPES)
        listed_types = {str(item.get("supportedType", "")).lower() for item in source_instances if isinstance(item, dict)}
        for expected_type in ("tushare", "alpha_vantage", "finnhub", "fred", "coingecko_pro", "coinglass"):
            assert expected_type in listed_types
        for hidden_type in ("openbb", "akshare", "coingecko", "binance", "okx", "ccxt", "imf", "longport"):
            assert hidden_type not in listed_types
        assert any(
            isinstance(item, dict)
            and str(item.get("supportedType", "")).lower() == "tushare"
            and str(item.get("displayName", "")) == "Tushare"
            for item in source_instances
        )

        evidence = _capture_with_playwright(base_url=base_url, artifact_dir=artifact_dir)
        settings = evidence.get("settings")
        assert isinstance(settings, dict), "missing settings evidence"
        assert settings.get("sectionTitles") == ["报告模型", "Embedding", "增强数据源", "微信通知", "恢复默认设置"]
        assert int(settings.get("tabCount", -1)) == 5
        assert int(settings.get("sourceRows", 0)) == 0
        assert int(settings.get("sourceCards", 0)) == 3

        qr_rect = settings.get("qrFrameRect")
        if isinstance(qr_rect, dict):
            assert int(qr_rect.get("width", 0)) >= 160
            assert int(qr_rect.get("height", 0)) >= 160

        source_text = str(settings.get("sourceText", ""))
        for forbidden in (
            "报告方案",
            "计价单位",
            "profile",
            "默认币种",
            "worker",
            "debate",
            "risk",
            "tab",
        ):
            assert forbidden.lower() not in source_text

        wechat_text = str(settings.get("wechatText", ""))
        for forbidden in ("channel id", "plugin", "openclaw", "provider attempt", "runtime"):
            assert forbidden not in wechat_text

        task = evidence.get("task")
        assert isinstance(task, dict), "missing task card evidence"
        assert task.get("hasTaskClass") is True
        assert task.get("fields") == ["标的", "名称", "市场"]
        assert "none" in str(task.get("outerMessageBorderStyle", "")).lower()
        task_text = str(task.get("text", ""))
        for forbidden in (
            "报告方案",
            "计价单位",
            "profile",
            "默认币种",
            "worker",
            "debate",
            "risk",
            "provider attempt",
            "runtime",
            "openclaw channel id",
        ):
            assert forbidden.lower() not in task_text

        png_names = [
            "settings-report-model-section.png",
            "settings-embedding-section.png",
            "settings-data-source-section.png",
            "settings-wechat-section.png",
            "report-task-card-section.png",
        ]
        png_sizes: dict[str, dict[str, int]] = {}
        for name in png_names:
            path = artifact_dir / name
            assert path.exists(), f"missing screenshot: {path}"
            width, height = _png_size(path)
            assert width > 0 and height > 0
            png_sizes[name] = {"width": width, "height": height}

        (artifact_dir / "settings-sections-home.html").write_text(home_html, encoding="utf-8")
        (artifact_dir / "settings-sections-page.html").write_text(settings_html, encoding="utf-8")
        (artifact_dir / "settings-sections-rendered-evidence.json").write_text(
            json.dumps(
                {
                    "llmSetup": {"loadResult": llm_result},
                    "dataSourceSetup": {"saveResult": save_result, "listResult": listed_sources},
                    "evidence": evidence,
                    "pngSizes": png_sizes,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "settings-sections-rendered-evidence.txt").write_text(
            "\n".join(
                [
                    "section_order=报告模型,Embedding,增强数据源,微信通知,恢复默认设置",
                    f"source_rows={settings.get('sourceRows')}",
                    f"tab_count={settings.get('tabCount')}",
                    f"qr_image_count={settings.get('qrImageCount')}",
                    f"task_fields={task.get('fields')}",
                    f"task_outer_border_style={task.get('outerMessageBorderStyle')}",
                    f"report_model_png={png_sizes['settings-report-model-section.png']['width']}x{png_sizes['settings-report-model-section.png']['height']}",
                    f"embedding_png={png_sizes['settings-embedding-section.png']['width']}x{png_sizes['settings-embedding-section.png']['height']}",
                    f"source_png={png_sizes['settings-data-source-section.png']['width']}x{png_sizes['settings-data-source-section.png']['height']}",
                    f"wechat_png={png_sizes['settings-wechat-section.png']['width']}x{png_sizes['settings-wechat-section.png']['height']}",
                    f"task_png={png_sizes['report-task-card-section.png']['width']}x{png_sizes['report-task-card-section.png']['height']}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=8)
        if process.poll() is None:
            process.kill()
