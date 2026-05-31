from __future__ import annotations

import contextlib
import json
import socket
import struct
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen


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


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"invalid png: {path}"
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _capture_with_playwright(
    *,
    url: str,
    wait_selector: str,
    screenshot_path: Path,
    mode: str,
) -> dict[str, object]:
    capture_script = """
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

payload = json.loads(sys.argv[1])
url = payload["url"]
wait_selector = payload["wait_selector"]
screenshot_path = Path(payload["screenshot_path"])
mode = payload["mode"]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(wait_selector, timeout=30000)
    page.wait_for_timeout(500)
    page.wait_for_timeout(500)
    evidence = page.evaluate(
        '''() => {
            const controls = Array.from(document.querySelectorAll(".ct-field input, .ct-field select"));
            const controlHeights = controls.map((item) => Math.round(item.getBoundingClientRect().height * 100) / 100);
            const buttonNodes = Array.from(document.querySelectorAll(".ct-button"));
            const buttonHeights = buttonNodes.map((item) => Math.round(item.getBoundingClientRect().height * 100) / 100);
            const buttonMeta = buttonNodes.map((item) => ({
                text: (item.textContent || "").trim(),
                className: item.className,
            }));
            const statusNodes = Array.from(document.querySelectorAll(".ct-status-pill"));
            const statusMeta = statusNodes.map((item) => ({
                text: (item.textContent || "").trim(),
                className: item.className,
                width: Math.round(item.getBoundingClientRect().width * 100) / 100,
                height: Math.round(item.getBoundingClientRect().height * 100) / 100,
            }));
            const alertNodes = Array.from(document.querySelectorAll(".ct-inline-alert"));
            const alerts = alertNodes.map((item) => ({
                text: (item.textContent || "").trim(),
                className: item.className,
                width: Math.round(item.getBoundingClientRect().width * 100) / 100,
            }));
                return {
                    viewport: { width: window.innerWidth, height: window.innerHeight },
                controlHeights,
                buttonHeights,
                buttonMeta,
                statusMeta,
                alerts,
                allText: document.body.textContent || "",
            };
        }'''
    )
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_path), full_page=True)
    context.close()
    browser.close()
print(json.dumps(evidence, ensure_ascii=False))
"""
    payload = {
        "url": url,
        "wait_selector": wait_selector,
        "screenshot_path": str(screenshot_path),
        "mode": mode,
    }
    result = subprocess.run(
        ["/usr/bin/python3", "-c", capture_script, json.dumps(payload, ensure_ascii=False)],
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


@contextlib.contextmanager
def _temporary_report_model_status_unready(project_root: Path):
    status_path = project_root / ".runtime" / "ui" / "report-model-status.json"
    backup = status_path.read_bytes() if status_path.exists() else None
    with contextlib.suppress(FileNotFoundError):
        status_path.unlink()
    try:
        yield
    finally:
        if backup is None:
            with contextlib.suppress(FileNotFoundError):
                status_path.unlink()
            return
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_bytes(backup)


def test_settings_controls_visual_surface_live() -> None:
    project_root = Path(__file__).resolve().parents[3]
    frontend_dist = project_root / "web" / "research-ui" / "dist"
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s13"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    assert frontend_dist.exists(), "frontend dist missing, run `pnpm --dir web/research-ui build` first"

    with _temporary_report_model_status_unready(project_root):
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
            _wait_http_ok(f"http://127.0.0.1:{port}/healthz")
            home_html = _read_text(f"http://127.0.0.1:{port}/")
            settings_html = _read_text(f"http://127.0.0.1:{port}/settings")
            assert "id=\"root\"" in home_html
            assert "id=\"root\"" in settings_html

            css_files = sorted(frontend_dist.glob("assets/*.css"))
            assert css_files, "unable to find built css asset"
            css_text = css_files[0].read_text(encoding="utf-8")
            for expected in (
                ".ct-field",
                ".ct-button",
                ".ct-button-secondary",
                ".ct-button-row",
                ".ct-status-ready",
                ".ct-status-pending",
                ".ct-status-error",
                ".ct-inline-alert",
            ):
                assert expected in css_text
            assert "radial-gradient" not in css_text
            assert "conic-gradient" not in css_text
            assert "hero" not in css_text.lower()

            home_png = artifact_dir / "controls-home-desktop.png"
            settings_png = artifact_dir / "controls-settings-desktop.png"
            home_evidence = _capture_with_playwright(
                url=f"http://127.0.0.1:{port}/",
                wait_selector='.ct-onboarding-dialog',
                screenshot_path=home_png,
                mode="home",
            )
            settings_evidence = _capture_with_playwright(
                url=f"http://127.0.0.1:{port}/settings",
                wait_selector='[data-testid="settings-page"]',
                screenshot_path=settings_png,
                mode="settings",
            )

            for evidence in (home_evidence, settings_evidence):
                control_heights = evidence.get("controlHeights")
                assert isinstance(control_heights, list), "missing control heights"
                if control_heights:
                    numeric_control_heights = [float(v) for v in control_heights]
                    assert max(numeric_control_heights) - min(numeric_control_heights) <= 2.1

                button_heights = evidence.get("buttonHeights")
                assert isinstance(button_heights, list), "missing button heights"
                if button_heights:
                    numeric_button_heights = [float(v) for v in button_heights]
                    assert max(numeric_button_heights) - min(numeric_button_heights) <= 2.1

                status_meta = evidence.get("statusMeta")
                assert isinstance(status_meta, list) and status_meta, "missing status pills"
                for item in status_meta:
                    assert isinstance(item, dict)
                    pill_height = float(item.get("height", 0))
                    pill_width = float(item.get("width", 0))
                    assert pill_height <= 28.0
                    assert pill_width <= 140.0

                all_text = str(evidence.get("allText", "")).lower()
                for forbidden in ("provider attempt", "runtime", "gateway", "channel id", "scope"):
                    assert forbidden not in all_text

            allowed_primary = {"测试报告模型连接", "测试 Embedding 连接", "保存配置", "测试数据源", "保存数据源", "确认", "生成报告", "测试并保存"}
            for evidence in (home_evidence, settings_evidence):
                button_meta = evidence.get("buttonMeta")
                assert isinstance(button_meta, list)
                for item in button_meta:
                    assert isinstance(item, dict)
                    text = str(item.get("text", "")).strip()
                    class_name = str(item.get("className", ""))
                    if "ct-button-secondary" not in class_name:
                        assert text in allowed_primary

            settings_buttons = settings_evidence.get("buttonMeta")
            assert isinstance(settings_buttons, list)
            secondary_texts = {
                str(item.get("text", "")).strip()
                for item in settings_buttons
                if isinstance(item, dict) and "ct-button-secondary" in str(item.get("className", ""))
            }
            for expected in ("重新连接", "解除连接", "刷新二维码", "稍后设置"):
                assert expected in secondary_texts

            home_size = _png_size(home_png)
            settings_size = _png_size(settings_png)
            rendered_evidence = {
                "home_screenshot": {"path": str(home_png), "width": home_size[0], "height": home_size[1]},
                "settings_screenshot": {"path": str(settings_png), "width": settings_size[0], "height": settings_size[1]},
                "home_render": home_evidence,
                "settings_render": settings_evidence,
                "forbidden_style_check": {
                    "contains_hero": "hero" in css_text.lower(),
                    "contains_radial_gradient": "radial-gradient" in css_text,
                    "contains_conic_gradient": "conic-gradient" in css_text,
                },
            }

            (artifact_dir / "controls-home.html").write_text(home_html, encoding="utf-8")
            (artifact_dir / "controls-settings.html").write_text(settings_html, encoding="utf-8")
            (artifact_dir / "controls-rendered-evidence.json").write_text(
                json.dumps(rendered_evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (artifact_dir / "controls-rendered-evidence.txt").write_text(
                "\n".join(
                    [
                        f"home_png={home_png.name} size={home_size[0]}x{home_size[1]}",
                        f"settings_png={settings_png.name} size={settings_size[0]}x{settings_size[1]}",
                        f"home_control_heights={home_evidence.get('controlHeights')}",
                        f"settings_control_heights={settings_evidence.get('controlHeights')}",
                        f"home_button_heights={home_evidence.get('buttonHeights')}",
                        f"settings_button_heights={settings_evidence.get('buttonHeights')}",
                        f"contains_hero={'hero' in css_text.lower()}",
                        f"contains_radial_gradient={'radial-gradient' in css_text}",
                        f"contains_conic_gradient={'conic-gradient' in css_text}",
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
