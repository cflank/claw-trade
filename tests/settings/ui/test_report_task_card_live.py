from __future__ import annotations

import contextlib
import json
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


def test_report_task_card_live_text_surface() -> None:
    project_root = Path(__file__).resolve().parents[3]
    frontend_dist = project_root / "web" / "research-ui" / "dist"
    artifact_dir = project_root / ".runtime" / "test-artifacts" / "settings-s06"
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
        _wait_http_ok(f"http://127.0.0.1:{port}/healthz")
        home_html = _read_text(f"http://127.0.0.1:{port}/")
        assert "id=\"root\"" in home_html
        script_match = re.search(r"<script[^>]+src=(['\"])([^'\"]*assets/[^'\"]+\.js)\1", home_html)
        assert script_match, "unable to find built entry script"
        entry_script_path = script_match.group(2)
        entry_script_text = _read_text(f"http://127.0.0.1:{port}{entry_script_path}")

        assert "修改标的" in entry_script_text
        assert "更新标的" in entry_script_text
        assert "市场切换后正在重新校验，请稍候。" in entry_script_text
        assert "请先点击“更新标的”完成重新识别。" in entry_script_text
        for forbidden in ("报告方案", "计价单位", "默认币种"):
            assert forbidden not in entry_script_text

        (artifact_dir / "report-task-card-live-home.html").write_text(home_html, encoding="utf-8")
        (artifact_dir / "report-task-card-live-script.txt").write_text(
            "\n".join(
                [
                    f"entry_script_path={entry_script_path}",
                    f"has_edit_symbol={'修改标的' in entry_script_text}",
                    f"has_refresh_symbol={'更新标的' in entry_script_text}",
                    f"has_market_revalidate={'市场切换后正在重新校验，请稍候。' in entry_script_text}",
                    f"has_symbol_reidentify_prompt={'请先点击“更新标的”完成重新识别。' in entry_script_text}",
                    f"contains_forbidden_report_plan={'报告方案' in entry_script_text}",
                    f"contains_forbidden_currency={'计价单位' in entry_script_text}",
                    f"contains_forbidden_default_currency={'默认币种' in entry_script_text}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "report-task-card-live-script-excerpt.json").write_text(
            json.dumps(
                {
                    "entryScriptPath": entry_script_path,
                    "containsEditSymbol": "修改标的" in entry_script_text,
                    "containsRefreshSymbol": "更新标的" in entry_script_text,
                    "containsRevalidatePrompt": "市场切换后正在重新校验，请稍候。" in entry_script_text,
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
