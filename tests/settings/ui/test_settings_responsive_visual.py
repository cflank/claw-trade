from __future__ import annotations

import contextlib
import json
import socket
import struct
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen


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

scenarios = [
    {
        "name": "settings-desktop",
        "path": "/settings",
        "viewport": {"width": 1440, "height": 960},
        "wait_selector": '[data-testid="settings-page"]',
        "reduced_motion": "no-preference",
    },
    {
        "name": "settings-tablet-960",
        "path": "/settings",
        "viewport": {"width": 960, "height": 900},
        "wait_selector": '[data-testid="settings-page"]',
        "reduced_motion": "no-preference",
    },
    {
        "name": "settings-mobile-560",
        "path": "/settings",
        "viewport": {"width": 560, "height": 900},
        "wait_selector": '[data-testid="settings-page"]',
        "reduced_motion": "no-preference",
    },
    {
        "name": "onboarding-tablet-960",
        "path": "/",
        "viewport": {"width": 960, "height": 900},
        "wait_selector": '[data-testid="report-model-onboarding"]',
        "reduced_motion": "no-preference",
    },
]

def collect_layout(page, scenario_name):
    return page.evaluate(
        '''(name) => {
            const wrap =
                document.querySelector(".ct-settings-wrap") ||
                document.querySelector(".ct-onboarding-wrap");
            const modelGrid = document.querySelector(".ct-model-grid");
            const formGrid = document.querySelector(".ct-form-grid");
            const sourceRow = document.querySelector(".ct-source-row");
            const sourceActions = document.querySelector(".ct-source-actions");
            const onboarding = document.querySelector(".ct-onboarding");
            const firstButton = document.querySelector(".ct-button-row .ct-button");
            const buttonParent = firstButton?.parentElement ?? null;
            const taskField = document.querySelector(".ct-task-field");

            const splitCount = (value) =>
                value ? value.split(" ").filter((item) => item.trim().length > 0).length : 0;
            const trackCount = (node) => {
                if (!node) {
                    return 0;
                }
                return splitCount(window.getComputedStyle(node).gridTemplateColumns);
            };
            const toRect = (node) => {
                if (!node) {
                    return null;
                }
                const rect = node.getBoundingClientRect();
                return {
                    width: Math.round(rect.width * 100) / 100,
                    height: Math.round(rect.height * 100) / 100,
                };
            };

            const overflowNodes = Array.from(
                document.querySelectorAll(".ct-settings-wrap, .ct-settings-section, .ct-onboarding-wrap, .ct-onboarding-panel")
            ).filter((node) => node.scrollWidth - node.clientWidth > 1);

            return {
                scenario: name,
                viewport: { width: window.innerWidth, height: window.innerHeight },
                wrapPaddingLeft: wrap ? window.getComputedStyle(wrap).paddingLeft : "",
                wrapPaddingRight: wrap ? window.getComputedStyle(wrap).paddingRight : "",
                modelGridTrackCount: trackCount(modelGrid),
                formGridTrackCount: trackCount(formGrid),
                sourceRowTrackCount: trackCount(sourceRow),
                sourceActionsJustify: sourceActions ? window.getComputedStyle(sourceActions).justifyContent : "",
                onboardingTrackCount: trackCount(onboarding),
                taskFieldTrackCount: trackCount(taskField),
                buttonRect: toRect(firstButton),
                buttonParentRect: toRect(buttonParent),
                pageHasHorizontalOverflow:
                    document.documentElement.scrollWidth - window.innerWidth > 1,
                overflowNodeCount: overflowNodes.length,
                pageTextLower: (document.body.textContent || "").toLowerCase(),
            };
        }''',
        scenario_name,
    )

def collect_motion(page):
    return page.evaluate(
        '''() => {
            const button = document.querySelector(".ct-button");
            const fieldInput = document.querySelector(".ct-field input");
            const statusPill = document.querySelector(".ct-status-pill");
            const read = (node) => {
                if (!node) {
                    return null;
                }
                const style = window.getComputedStyle(node);
                return {
                    transitionProperty: style.transitionProperty,
                    transitionDuration: style.transitionDuration,
                };
            };
            return {
                button: read(button),
                input: read(fieldInput),
                status: read(statusPill),
            };
        }'''
    )

out = {"screenshots": {}, "layouts": {}, "motion": {}}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for scenario in scenarios:
        context = browser.new_context(
            viewport=scenario["viewport"],
            locale="zh-CN",
            reduced_motion=scenario["reduced_motion"],
        )
        page = context.new_page()
        page.goto(f"{base_url}{scenario['path']}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(scenario["wait_selector"], timeout=30000)
        page.wait_for_timeout(600)
        screenshot_path = artifact_dir / f"{scenario['name']}.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        out["screenshots"][scenario["name"]] = str(screenshot_path)
        out["layouts"][scenario["name"]] = collect_layout(page, scenario["name"])
        context.close()

    motion_context = browser.new_context(
        viewport={"width": 960, "height": 900},
        locale="zh-CN",
        reduced_motion="no-preference",
    )
    motion_page = motion_context.new_page()
    motion_page.goto(f"{base_url}/settings", wait_until="domcontentloaded", timeout=30000)
    motion_page.wait_for_selector('[data-testid="settings-page"]', timeout=30000)
    motion_page.wait_for_timeout(400)
    out["motion"]["no_preference"] = collect_motion(motion_page)
    motion_context.close()

    reduce_context = browser.new_context(
        viewport={"width": 960, "height": 900},
        locale="zh-CN",
        reduced_motion="reduce",
    )
    reduce_page = reduce_context.new_page()
    reduce_page.goto(f"{base_url}/settings", wait_until="domcontentloaded", timeout=30000)
    reduce_page.wait_for_selector('[data-testid="settings-page"]', timeout=30000)
    reduce_page.wait_for_timeout(400)
    out["motion"]["reduce"] = collect_motion(reduce_page)
    reduce_context.close()

    browser.close()

print(json.dumps(out, ensure_ascii=False))
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
    payload = json.loads(result.stdout.strip())
    assert isinstance(payload, dict), "invalid playwright capture payload"
    return payload


def test_settings_responsive_visual_surface_live() -> None:
    project_root = Path(__file__).resolve().parents[3]
    frontend_dist = project_root / "web" / "research-ui" / "dist"
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s15"
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
            base_url = f"http://127.0.0.1:{port}"
            _wait_http_ok(f"{base_url}/healthz")
            settings_html = _read_text(f"{base_url}/settings")
            home_html = _read_text(f"{base_url}/")
            assert "id=\"root\"" in settings_html
            assert "id=\"root\"" in home_html
            save_result = _post_json(
                f"{base_url}/api/ui/save-data-source-instance",
                {
                    "requestId": "s15-responsive-save-data-source",
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

            css_match = next(iter(sorted(frontend_dist.glob("assets/*.css"))), None)
            assert css_match is not None, "unable to find built css asset"
            css_text = css_match.read_text(encoding="utf-8")

            assert "@media (max-width: 960px)" in css_text
            assert "@media (max-width: 560px)" in css_text
            assert "@media (prefers-reduced-motion: no-preference)" in css_text
            assert "@keyframes" not in css_text
            assert "animation:" not in css_text
            assert "linear-gradient" not in css_text
            assert "radial-gradient" not in css_text
            assert "hero" not in css_text.lower()
            assert "marketing" not in css_text.lower()

            evidence = _capture_with_playwright(base_url=base_url, artifact_dir=artifact_dir)
            layouts = evidence.get("layouts")
            motion = evidence.get("motion")
            screenshots = evidence.get("screenshots")
            assert isinstance(layouts, dict)
            assert isinstance(motion, dict)
            assert isinstance(screenshots, dict)

            desktop = layouts.get("settings-desktop")
            tablet = layouts.get("settings-tablet-960")
            mobile = layouts.get("settings-mobile-560")
            onboarding = layouts.get("onboarding-tablet-960")
            assert isinstance(desktop, dict)
            assert isinstance(tablet, dict)
            assert isinstance(mobile, dict)
            assert isinstance(onboarding, dict)

            assert int(desktop.get("modelGridTrackCount", 0)) >= 2
            assert int(tablet.get("modelGridTrackCount", 0)) == 1
            assert int(tablet.get("formGridTrackCount", 0)) == 1
            assert int(tablet.get("sourceRowTrackCount", 0)) == 0
            assert str(tablet.get("sourceActionsJustify", "")).strip() in {"", "flex-start"}
            assert int(onboarding.get("onboardingTrackCount", 0)) == 1

            mobile_padding_left = str(mobile.get("wrapPaddingLeft", "")).strip()
            mobile_padding_right = str(mobile.get("wrapPaddingRight", "")).strip()
            assert mobile_padding_left == "10px"
            assert mobile_padding_right == "10px"

            mobile_button_rect = mobile.get("buttonRect")
            mobile_button_parent_rect = mobile.get("buttonParentRect")
            assert isinstance(mobile_button_rect, dict)
            assert isinstance(mobile_button_parent_rect, dict)
            button_width = float(mobile_button_rect.get("width", 0))
            parent_width = float(mobile_button_parent_rect.get("width", 0))
            assert button_width > 0
            assert parent_width > 0
            assert parent_width - button_width <= 2.0

            for layout in (desktop, tablet, mobile, onboarding):
                assert bool(layout.get("pageHasHorizontalOverflow")) is False
                assert int(layout.get("overflowNodeCount", 0)) == 0
                page_text = str(layout.get("pageTextLower", ""))
                for forbidden in ("hero", "marketing"):
                    assert forbidden not in page_text

            no_pref = motion.get("no_preference")
            reduce = motion.get("reduce")
            assert isinstance(no_pref, dict)
            assert isinstance(reduce, dict)

            for key in ("button", "input", "status"):
                no_pref_node = no_pref.get(key)
                reduce_node = reduce.get(key)
                assert isinstance(no_pref_node, dict), f"missing no-preference motion node: {key}"
                assert isinstance(reduce_node, dict), f"missing reduce motion node: {key}"
                no_pref_duration = str(no_pref_node.get("transitionDuration", ""))
                reduce_duration = str(reduce_node.get("transitionDuration", ""))
                assert "0.12s" in no_pref_duration
                assert reduce_duration == "0s"

            screenshot_sizes: dict[str, dict[str, int]] = {}
            for name, png_path in screenshots.items():
                path = Path(str(png_path))
                assert path.exists(), f"missing screenshot: {path}"
                width, height = _png_size(path)
                assert width > 0 and height > 0
                screenshot_sizes[name] = {"width": width, "height": height}

            (artifact_dir / "settings-responsive-home.html").write_text(home_html, encoding="utf-8")
            (artifact_dir / "settings-responsive-page.html").write_text(settings_html, encoding="utf-8")
            (artifact_dir / "settings-responsive-rendered-evidence.json").write_text(
                json.dumps(
                    {
                        "screenshots": screenshots,
                        "screenshotSizes": screenshot_sizes,
                        "layouts": layouts,
                        "motion": motion,
                        "styleForbiddenChecks": {
                            "contains_keyframes": "@keyframes" in css_text,
                            "contains_animation": "animation:" in css_text,
                            "contains_linear_gradient": "linear-gradient" in css_text,
                            "contains_radial_gradient": "radial-gradient" in css_text,
                            "contains_hero": "hero" in css_text.lower(),
                            "contains_marketing": "marketing" in css_text.lower(),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
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
