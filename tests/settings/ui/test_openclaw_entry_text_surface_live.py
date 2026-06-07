from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
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


def _http_get_status(host: str, port: int, path: str) -> tuple[int, dict[str, str], str]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, headers, body
    finally:
        conn.close()


def test_openclaw_entry_text_surface_live() -> None:
    project_root = Path(__file__).resolve().parents[3]
    frontend_dist = project_root / "web" / "research-ui" / "dist"
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s07"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    assert frontend_dist.exists(), "frontend dist missing, run `pnpm --dir web/research-ui build` first"

    port = _find_free_port()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
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
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_http_ok(f"http://127.0.0.1:{port}/healthz")
        settings_html = _read_text(f"http://127.0.0.1:{port}/settings")
        home_html = _read_text(f"http://127.0.0.1:{port}/")
        assert "id=\"root\"" in settings_html
        assert "id=\"root\"" in home_html

        script_match = re.search(r"<script[^>]+src=(['\"])([^'\"]*assets/[^'\"]+\.js)\1", settings_html)
        assert script_match, "unable to find built entry script"
        entry_script_path = script_match.group(2)
        entry_script_text = _read_text(f"http://127.0.0.1:{port}{entry_script_path}")

        assert "打开设备界面" in entry_script_text
        assert "打开 OpenClaw 主界面" not in entry_script_text
        assert "/api/ui/open-device-interface" in entry_script_text

        device_status, device_headers, device_body = _http_get_status("127.0.0.1", port, "/api/ui/open-device-interface")
        assert device_status in {302, 307, 308}
        assert device_headers.get("location")

        (artifact_dir / "settings.html").write_text(settings_html, encoding="utf-8")
        (artifact_dir / "home.html").write_text(home_html, encoding="utf-8")
        (artifact_dir / "entry-script-surface.txt").write_text(
            "\n".join(
                [
                    f"entry_script_path={entry_script_path}",
                    f"contains_open_device_interface={'/api/ui/open-device-interface' in entry_script_text}",
                    f"contains_openclaw_main_entry={'打开 OpenClaw 主界面' in entry_script_text}",
                    f"contains_chat_tool_entry={'打开设备界面' in entry_script_text}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "open-device-interface-status.json").write_text(
            json.dumps(
                {
                    "status": device_status,
                    "location": device_headers.get("location", ""),
                    "body_excerpt": device_body[:300],
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
