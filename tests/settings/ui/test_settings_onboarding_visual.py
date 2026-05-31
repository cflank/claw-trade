from __future__ import annotations

import contextlib
import json
import re
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


def _read_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=5) as response:  # nosec B310: test-only localhost call
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    assert isinstance(payload, dict), "unexpected non-object json payload"
    return payload


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

def rect_data(node):
    if node is None:
        return None
    rect = node.getBoundingClientRect()
    return {
        "left": round(rect.left, 2),
        "top": round(rect.top, 2),
        "right": round(rect.right, 2),
        "bottom": round(rect.bottom, 2),
        "width": round(rect.width, 2),
        "height": round(rect.height, 2),
    }

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(wait_selector, timeout=30000)
    page.wait_for_timeout(1000)
    if mode == "home":
        evidence = page.evaluate(
            '''() => {
                const onboarding = document.querySelector(".ct-onboarding");
                const dialog = document.querySelector('[data-testid="report-model-onboarding-dialog"]');
                const panel = document.querySelector(".ct-onboarding-panel");
                const style = onboarding ? window.getComputedStyle(onboarding) : null;
                const pageTitle = document.querySelector("#report-model-onboarding-title")?.textContent?.trim() ?? "";
                const pageSubtitle = document.querySelector(".ct-page-subtitle")?.textContent?.trim() ?? "";
                const panelTitles = Array.from(document.querySelectorAll(".ct-onboarding-panel-title")).map(
                    (item) => item.textContent?.trim() ?? ""
                );
                const fieldLabels = Array.from(document.querySelectorAll(".ct-form-grid .ct-field > span")).map(
                    (item) => item.textContent?.trim() ?? ""
                );
                const actionButtons = Array.from(document.querySelectorAll(".ct-settings-actions button")).map(
                    (item) => item.textContent?.trim() ?? ""
                );
                const hasOnboarding = Boolean(document.querySelector('[data-testid="report-model-onboarding"]'));
                const hasDialog = Boolean(dialog);
                const hasWorkspace = Boolean(document.querySelector('[data-testid="workspace-layout"]'));
                const dialogRect = dialog ? dialog.getBoundingClientRect() : null;
                const panelRect = panel ? panel.getBoundingClientRect() : null;
                return {
                    "viewport": {"width": window.innerWidth, "height": window.innerHeight},
                    "pageTitle": pageTitle,
                    "pageSubtitle": pageSubtitle,
                    "panelTitles": panelTitles,
                    "fieldLabels": fieldLabels,
                    "actionButtons": actionButtons,
                    "gridTemplateColumns": style?.gridTemplateColumns ?? "",
                    "gridColumnTrackCount": style?.gridTemplateColumns
                        ? style.gridTemplateColumns.split(" ").filter(Boolean).length
                        : 0,
                    "onboardingVisible": hasOnboarding,
                    "dialogVisible": hasDialog,
                    "workspaceVisible": hasWorkspace,
                    "dialogRect": dialogRect
                        ? {
                              "left": Math.round(dialogRect.left * 100) / 100,
                              "top": Math.round(dialogRect.top * 100) / 100,
                              "right": Math.round(dialogRect.right * 100) / 100,
                              "bottom": Math.round(dialogRect.bottom * 100) / 100,
                              "width": Math.round(dialogRect.width * 100) / 100,
                              "height": Math.round(dialogRect.height * 100) / 100,
                          }
                        : null,
                    "panelRect": panelRect
                        ? {
                              "left": Math.round(panelRect.left * 100) / 100,
                              "top": Math.round(panelRect.top * 100) / 100,
                              "right": Math.round(panelRect.right * 100) / 100,
                              "bottom": Math.round(panelRect.bottom * 100) / 100,
                              "width": Math.round(panelRect.width * 100) / 100,
                              "height": Math.round(panelRect.height * 100) / 100,
                          }
                        : null,
                };
            }'''
        )
    else:
        evidence = page.evaluate(
            '''() => {
                const pageHead = document.querySelector(".ct-page-head");
                const title = document.querySelector(".ct-page-title")?.textContent?.trim() ?? "";
                const subtitle = document.querySelector(".ct-page-subtitle")?.textContent?.trim() ?? "";
                const sections = Array.from(document.querySelectorAll(".ct-settings-section h2")).map(
                    (item) => item.textContent?.trim() ?? ""
                );
                const headRect = pageHead ? pageHead.getBoundingClientRect() : null;
                return {
                    "viewport": {"width": window.innerWidth, "height": window.innerHeight},
                    "pageTitle": title,
                    "pageSubtitle": subtitle,
                    "sectionTitles": sections,
                    "pageHeadRect": headRect
                        ? {
                              "left": Math.round(headRect.left * 100) / 100,
                              "top": Math.round(headRect.top * 100) / 100,
                              "right": Math.round(headRect.right * 100) / 100,
                              "bottom": Math.round(headRect.bottom * 100) / 100,
                              "width": Math.round(headRect.width * 100) / 100,
                              "height": Math.round(headRect.height * 100) / 100,
                          }
                        : null,
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


def test_settings_onboarding_visual_surface_live() -> None:
    project_root = Path(__file__).resolve().parents[3]
    frontend_dist = project_root / "web" / "research-ui" / "dist"
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s12"
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

            settings_payload = _read_json(f"http://127.0.0.1:{port}/api/ui/load-llm-settings")
            draft = settings_payload.get("draft")
            assert isinstance(draft, dict), "missing draft in load-llm-settings payload"
            model_status = draft.get("reportModelStatus")
            assert isinstance(model_status, dict), "missing reportModelStatus payload"
            report_state = str(model_status.get("state") or "").strip()
            assert report_state != "ready", "test expects unready model state for onboarding surface proof"

            css_match = re.search(r"<link[^>]+href=(['\"])([^'\"]*assets/[^'\"]+\.css)\1", home_html)
            assert css_match, "unable to find built css asset"
            css_path = css_match.group(2)
            css_text = _read_text(f"http://127.0.0.1:{port}{css_path}")
            assert ".ct-onboarding" in css_text
            assert re.search(
                r"\.ct-onboarding\s*{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.08fr\)\s*minmax\(360px,\s*(?:0?\.)?92fr\);",
                css_text,
                flags=re.S,
            )
            assert ".ct-page-head" in css_text
            assert ".ct-page-title" in css_text
            assert ".ct-page-subtitle" in css_text

            onboarding_block = re.search(
                r"\.ct-onboarding\s*{[\s\S]*?\.ct-onboarding-panel-title\s*{[\s\S]*?}",
                css_text,
                flags=re.S,
            )
            assert onboarding_block, "missing onboarding css block"
            onboarding_css = onboarding_block.group(0)
            lowered = onboarding_css.lower()
            assert "hero" not in lowered
            assert "radial-gradient" not in lowered
            assert "conic-gradient" not in lowered
            assert "url(" not in onboarding_css

            home_png = artifact_dir / "onboarding-home-desktop.png"
            settings_png = artifact_dir / "settings-shell-desktop.png"
            home_evidence = _capture_with_playwright(
                url=f"http://127.0.0.1:{port}/",
                wait_selector='[data-testid="report-model-onboarding"]',
                screenshot_path=home_png,
                mode="home",
            )
            settings_evidence = _capture_with_playwright(
                url=f"http://127.0.0.1:{port}/settings",
                wait_selector=".ct-settings-wrap .ct-page-title",
                screenshot_path=settings_png,
                mode="settings",
            )

            assert home_evidence.get("onboardingVisible") is True
            assert home_evidence.get("dialogVisible") is True
            assert home_evidence.get("workspaceVisible") is True
            assert home_evidence.get("pageTitle") == "配置报告模型"
            field_labels = home_evidence.get("fieldLabels")
            assert isinstance(field_labels, list)
            for expected in ("服务商", "模型", "接口地址", "API Key"):
                assert expected in field_labels
            panel_titles = home_evidence.get("panelTitles")
            assert isinstance(panel_titles, list)
            assert "报告模型" in panel_titles
            action_buttons = home_evidence.get("actionButtons")
            assert isinstance(action_buttons, list)
            assert "稍后配置" in action_buttons
            assert home_evidence.get("gridColumnTrackCount") == 1

            dialog_rect = home_evidence.get("dialogRect")
            panel_rect = home_evidence.get("panelRect")
            assert isinstance(dialog_rect, dict)
            assert isinstance(panel_rect, dict)
            assert float(dialog_rect["width"]) < 1440
            assert float(panel_rect["width"]) <= float(dialog_rect["width"])

            assert settings_evidence.get("pageTitle") == "设置"
            page_subtitle = settings_evidence.get("pageSubtitle")
            assert isinstance(page_subtitle, str)
            assert "管理报告模型" in page_subtitle
            assert settings_evidence.get("pageHeadRect")

            home_size = _png_size(home_png)
            settings_size = _png_size(settings_png)
            rendered_evidence = {
                "report_model_state": report_state,
                "home_screenshot": {"path": str(home_png), "width": home_size[0], "height": home_size[1]},
                "settings_screenshot": {"path": str(settings_png), "width": settings_size[0], "height": settings_size[1]},
                "home_render": home_evidence,
                "settings_render": settings_evidence,
                "forbidden_style_check": {
                    "contains_hero": "hero" in lowered,
                    "contains_radial_gradient": "radial-gradient" in onboarding_css,
                    "contains_conic_gradient": "conic-gradient" in onboarding_css,
                    "contains_url": "url(" in onboarding_css,
                },
            }

            (artifact_dir / "home.html").write_text(home_html, encoding="utf-8")
            (artifact_dir / "settings.html").write_text(settings_html, encoding="utf-8")
            (artifact_dir / "onboarding.css").write_text(onboarding_css + "\n", encoding="utf-8")
            (artifact_dir / "onboarding-rendered-evidence.json").write_text(
                json.dumps(rendered_evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (artifact_dir / "onboarding-rendered-evidence.txt").write_text(
                "\n".join(
                    [
                        f"report_model_state={report_state}",
                        f"home_png={home_png.name} size={home_size[0]}x{home_size[1]}",
                        f"settings_png={settings_png.name} size={settings_size[0]}x{settings_size[1]}",
                            f"home_page_title={home_evidence.get('pageTitle')}",
                            f"home_grid_columns={home_evidence.get('gridTemplateColumns')}",
                            f"home_grid_track_count={home_evidence.get('gridColumnTrackCount')}",
                            f"home_dialog_width={dialog_rect.get('width')}",
                            f"home_panel_width={panel_rect.get('width')}",
                            f"settings_page_title={settings_evidence.get('pageTitle')}",
                        f"settings_page_subtitle={page_subtitle}",
                        f"contains_forbidden_hero={'hero' in lowered}",
                        f"contains_forbidden_radial_gradient={'radial-gradient' in onboarding_css}",
                        f"contains_forbidden_conic_gradient={'conic-gradient' in onboarding_css}",
                        f"contains_forbidden_url={'url(' in onboarding_css}",
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
