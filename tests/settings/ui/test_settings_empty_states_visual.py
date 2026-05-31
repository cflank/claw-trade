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


def _capture_empty_state(*, base_url: str, screenshot_path: Path) -> dict[str, object]:
    capture_script = """
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

payload = json.loads(sys.argv[1])
base_url = payload["base_url"]
screenshot_path = Path(payload["screenshot_path"])
screenshot_path.parent.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
    page = context.new_page()
    page.goto(f"{base_url}/settings", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('[data-testid="settings-section-data-sources"]', timeout=30000)
    page.wait_for_timeout(600)
    source_section = page.locator('[data-testid="settings-section-data-sources"]').first
    source_section.screenshot(path=str(screenshot_path))
    evidence = page.evaluate(
        '''() => {
            const sourceSection = document.querySelector('[data-testid="settings-section-data-sources"]');
            const sourceRows = sourceSection?.querySelectorAll('.ct-source-row').length ?? 0;
            const sourcePills = Array.from(sourceSection?.querySelectorAll('.ct-status-pill') ?? []);
            const disabledPills = sourcePills.filter((item) => (item.textContent ?? "").trim() === "已停用").length;
            const sectionRect = sourceSection ? sourceSection.getBoundingClientRect() : null;
            return {
                sourceRows,
                disabledPills,
                hasLegacyEmptyState: Boolean(sourceSection?.querySelector('.ct-empty')),
                sourceSectionText: (sourceSection?.textContent ?? "").toLowerCase(),
                sectionRect: sectionRect
                    ? {
                          width: Math.round(sectionRect.width),
                          height: Math.round(sectionRect.height),
                      }
                    : null,
            };
        }'''
    )
    context.close()
    browser.close()
print(json.dumps(evidence, ensure_ascii=False))
"""
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            capture_script,
            json.dumps({"base_url": base_url, "screenshot_path": str(screenshot_path)}, ensure_ascii=False),
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
    assert isinstance(evidence, dict), "invalid empty-state evidence payload"
    return evidence


def test_settings_empty_states_visual_surface_live() -> None:
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
        assert "id=\"root\"" in settings_html

        png_path = artifact_dir / "settings-empty-state.png"
        evidence = _capture_empty_state(base_url=base_url, screenshot_path=png_path)

        assert int(evidence.get("sourceRows", -1)) == 12
        assert bool(evidence.get("hasLegacyEmptyState")) is False
        assert int(evidence.get("disabledPills", 0)) > 0
        section_rect = evidence.get("sectionRect")
        assert isinstance(section_rect, dict), "missing data-source section rect"
        assert int(section_rect.get("width", 0)) > 0
        assert int(section_rect.get("height", 0)) > 0

        source_text = str(evidence.get("sourceSectionText", ""))
        for forbidden in (
            "provider attempt",
            "runtime",
            "openclaw",
            "diagnostics",
            "gateway",
            "channel id",
            "plugin",
            "promise",
        ):
            assert forbidden not in source_text

        width, height = _png_size(png_path)
        assert width > 0 and height > 0
        (artifact_dir / "settings-empty-state-page.html").write_text(settings_html, encoding="utf-8")
        (artifact_dir / "settings-empty-state-evidence.json").write_text(
            json.dumps(
                {
                    "emptyState": evidence,
                    "screenshot": {"path": str(png_path), "width": width, "height": height},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "settings-empty-state-evidence.txt").write_text(
            "\n".join(
                [
                    f"source_rows={evidence.get('sourceRows')}",
                    f"disabled_pills={evidence.get('disabledPills')}",
                    f"has_legacy_empty_state={evidence.get('hasLegacyEmptyState')}",
                    f"empty_png={width}x{height}",
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
